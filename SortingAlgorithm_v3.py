#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Whiteout Survival - Three-Day SvS Ministry Appointment Optimizer
================================================================

Purpose
-------
This program reads a raw application workbook and a three-day buff-schedule
workbook, then creates a privacy-safe schedule that maximizes the total
resource value assigned across:

    1. Construction Day
    2. Research Day
    3. Troop Training Day

The program uses one GLOBAL Google OR-Tools CP-SAT model.  It does not run a
simple greedy sort, and it does not optimize each day in isolation when a
shared General Speedup pool is present.

Hard constraints
----------------
* A schedule slot can contain at most one player.
* A player can receive at most one appointment on each boost day.
* A player is assigned only to a day they selected or are eligible for.
* A player is assigned only to a full 30-minute slot inside their submitted
  availability window.
* Existing assignments in the template can be preserved as locked slots.
* General Speedups are a shared pool and are not counted in full on all three
  days.

Lexicographic optimization
--------------------------
The solver optimizes in three exact stages:

    1. Maximize total estimated SvS/resource points.
    2. Without reducing stage 1, maximize the number of filled slots.
    3. Without reducing stages 1 or 2, prefer applicants with narrower
       availability, then use source-row order as a deterministic tiebreaker.

Privacy
-------
The published workbook contains only the schedule template's time slots,
Player/Chief ID, and Alliance/Tag.  Player names and email addresses are never
written to the output workbook.

Python compatibility
--------------------
Python 3.9+

Required packages
-----------------
    python -m pip install --upgrade pandas openpyxl ortools

No installation is needed for os, pathlib, datetime, re, logging, or shutil;
those modules are part of Python's standard library.
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import re
import shutil
import sys
import traceback
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

import pandas as pd
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
try:
    from ortools.sat.python import cp_model  # type: ignore

    HAS_ORTOOLS = True
except ImportError:  # pragma: no cover - depends on the local environment
    cp_model = None  # type: ignore
    HAS_ORTOOLS = False


# =============================================================================
# 1. DEFAULT FILE PATHS
# =============================================================================
# These defaults match the paths supplied in the conversation.  You can either
# edit them here or override them from the terminal with --applications,
# --template, and --output.

DEFAULT_APPLICATIONS_FILE = Path(
    "/Users/christianjoudon/Downloads/"
    "Copy of State 2564 - SVS Ministry Appointment Schedule  July 2025 Appointments.xlsx"
)
DEFAULT_TEMPLATE_FILE = Path(
    "/Users/christianjoudon/Downloads/State 2564 - Buff Schedule - July 2025.xlsx"
)
DEFAULT_OUTPUT_FILE = Path(
    "/Users/christianjoudon/Downloads/"
    "State 2564 - Optimised Buff Schedule - July 2025.xlsx"
)


# =============================================================================
# 2. SCHEDULING AND SCORING CONFIGURATION
# =============================================================================

SLOT_MINUTES = 30
TIMEZONE_LABEL = "UTC"

# Set to 3 for pure resource maximization: a player may receive one appointment
# on each of the three different boost days.  Change to 1 only if your state has
# a fairness rule limiting each player to one appointment for the entire event.
DEFAULT_MAX_TOTAL_APPOINTMENTS_PER_PLAYER = 3

# The point conversions below are configurable.  They reflect a commonly used
# Whiteout Survival SvS scoring model documented by public community schedulers.
POINTS_PER_SPEEDUP_MINUTE = 30
POINTS_PER_FIRE_CRYSTAL = 2_000
POINTS_PER_REFINED_FIRE_CRYSTAL = 30_000
POINTS_PER_FIRE_CRYSTAL_SHARD = 1_000

# When an ambiguous numeric column is named only "Construction", "Research",
# "Troop", or "General", this tells the program how to interpret the number.
# Valid values: "days", "hours", "minutes", or "points".
AMBIGUOUS_NUMERIC_RESOURCE_UNIT = "days"

# If a day column explicitly contains "score" or "points" in its header, it is
# treated as a complete final score.  Special resources are not added again.
ADD_SPECIAL_RESOURCES_TO_FINAL_SCORE_COLUMNS = False

# An empty availability response is treated as unavailable, never as all day.
# This prevents accidental assignments outside a player's stated availability.
EMPTY_AVAILABILITY_MEANS_ALL_DAY = False

# Existing nonblank player-ID cells in the schedule template are locked and are
# never overwritten when this is True.
PRESERVE_EXISTING_ASSIGNMENTS = True

# If the template has no separate Tag/Alliance column, write "ID - TAG" in its
# ID column so the public schedule still contains only the requested fields.
COMBINE_ID_AND_TAG_IF_TAG_COLUMN_MISSING = True

# If the original template cannot be mapped safely, create a clean three-sheet
# schedule instead of silently writing into uncertain cells.
ALLOW_CLEAN_WORKBOOK_FALLBACK = False

# Only a proven optimal solution is accepted by default.  For this problem size,
# CP-SAT should ordinarily prove optimality quickly.
ACCEPT_FEASIBLE_BUT_NOT_PROVEN_OPTIMAL = False
DEFAULT_SOLVER_TIME_LIMIT_SECONDS = 120.0
MIN_TEMPLATE_SLOTS_PER_BLOCK = 2

# The script never writes names or email addresses to the output workbook.
PRIVACY_FORBIDDEN_OUTPUT_TERMS = {
    "name",
    "email",
    "e-mail",
    "discord",
    "phone",
}


# =============================================================================
# 3. DAY DEFINITIONS
# =============================================================================


@dataclass(frozen=True)
class DaySpec:
    key: str
    label: str
    event_day_number: int
    aliases: Tuple[str, ...]
    day_number_aliases: Tuple[str, ...]


DAY_SPECS: Tuple[DaySpec, ...] = (
    DaySpec(
        key="construction",
        label="Construction Day",
        event_day_number=1,
        aliases=("construction", "build", "building"),
        day_number_aliases=("day 1", "day1"),
    ),
    DaySpec(
        key="research",
        label="Research Day",
        event_day_number=2,
        aliases=("research", "tech", "technology"),
        day_number_aliases=("day 2", "day2"),
    ),
    DaySpec(
        key="training",
        label="Troop Training Day",
        event_day_number=4,
        aliases=("training", "troop", "troops", "education"),
        # Some templates call this the third scheduled buff day even though it
        # occurs on SvS preparation Day 4, so both labels are accepted.
        day_number_aliases=("day 4", "day4", "third day", "day 3", "day3"),
    ),
)
DAY_BY_KEY: Dict[str, DaySpec] = {day.key: day for day in DAY_SPECS}


# =============================================================================
# 4. DATA CLASSES
# =============================================================================


@dataclass
class InputMapping:
    sheet_name: str
    header_row_zero_based: int
    id_column: str
    tag_column: Optional[str]
    timestamp_column: Optional[str]
    selected_days_column: Optional[str]
    general_availability_column: Optional[str]
    day_availability_columns: Dict[str, Optional[str]]
    day_resource_columns: Dict[str, str]
    general_resource_column: Optional[str]
    fire_crystal_column: Optional[str]
    refined_fire_crystal_column: Optional[str]
    fire_crystal_shard_column: Optional[str]
    warnings: List[str] = field(default_factory=list)


@dataclass
class PlayerRecord:
    player_id: str
    tag: str
    source_order: int
    selected_days: Set[str]
    available_slots: Dict[str, Set[int]]
    base_points: Dict[str, int]
    general_points: int
    source_excel_row: int


@dataclass
class TemplateSlot:
    day_key: str
    sheet_name: str
    row: int
    time_column: int
    id_column: int
    tag_column: Optional[int]
    start_minute: int
    end_minute: int
    display_label: str
    locked: bool = False
    locked_player_id: str = ""


@dataclass
class TemplateBlock:
    day_key: str
    sheet_name: str
    time_column: int
    id_column: int
    tag_column: Optional[int]
    slots: List[TemplateSlot]
    mapping_method: str


@dataclass
class Assignment:
    day_key: str
    slot_start: int
    slot_label: str
    player_id: str
    tag: str
    base_points: int
    general_points_allocated: int
    is_locked: bool = False

    @property
    def total_points(self) -> int:
        return self.base_points + self.general_points_allocated


@dataclass
class SolveResult:
    assignments: List[Assignment]
    total_points: int
    filled_slots: int
    solver_status: str
    stage_statuses: Tuple[str, str, str]


# =============================================================================
# 5. EXCEPTIONS AND LOGGING
# =============================================================================


class SchedulerError(RuntimeError):
    """A controlled error with a message suitable for the user."""


LOGGER = logging.getLogger("whiteout_scheduler")


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)-8s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )


# =============================================================================
# 6. TEXT, HEADER, AND NUMBER NORMALIZATION
# =============================================================================

_SPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def normalize_text(value: Any) -> str:
    """Return a stable lowercase representation for matching headers/text."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = text.replace("\u00a0", " ").replace("&", " and ")
    text = text.lower().strip()
    text = _NON_ALNUM_RE.sub(" ", text)
    return _SPACE_RE.sub(" ", text).strip()


def compact_text(value: Any) -> str:
    return normalize_text(value).replace(" ", "")


def is_blank(value: Any) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    return str(value).strip() == ""


def canonical_player_id(value: Any) -> str:
    """Preserve player IDs as text and remove Excel's trailing '.0'."""
    if is_blank(value):
        return ""
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return ""
        if value.is_integer():
            return str(int(value))
        return format(value, "f").rstrip("0").rstrip(".")
    text = str(value).strip()
    if re.fullmatch(r"\d+\.0", text):
        return text[:-2]
    return text


def canonical_tag(value: Any) -> str:
    if is_blank(value):
        return ""
    return _SPACE_RE.sub(" ", str(value).strip())


def parse_number(value: Any) -> float:
    """Parse counts such as 1,250, 1.2k, 3.5M, and ordinary numbers."""
    if is_blank(value):
        return 0.0
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        result = float(value)
        return result if math.isfinite(result) else 0.0

    text = str(value).strip().lower().replace(",", "")
    text = text.replace("$", "").replace("approximately", "")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        return 0.0
    number = float(match.group(0))
    suffix_text = text[match.end() :].strip()
    if suffix_text.startswith("k"):
        number *= 1_000
    elif suffix_text.startswith("m"):
        number *= 1_000_000
    elif suffix_text.startswith("b"):
        number *= 1_000_000_000
    return max(0.0, number)


def parse_form_timestamp(value: Any) -> Any:
    """Parse response timestamps, including Excel serial dates from MS Forms."""
    if is_blank(value):
        return pd.NaT
    if isinstance(value, datetime):
        return pd.Timestamp(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric = float(value)
        if not math.isfinite(numeric):
            return pd.NaT
        # Microsoft Forms exports timestamps as Excel serial day numbers.
        if 20_000.0 <= numeric <= 80_000.0:
            return pd.Timestamp("1899-12-30") + pd.to_timedelta(numeric, unit="D")
        return pd.NaT
    try:
        return pd.to_datetime(str(value), errors="coerce")
    except Exception:
        return pd.NaT


def header_match_score(header: str, aliases: Sequence[str]) -> int:
    """Score a header against aliases; higher is a more reliable match."""
    normalized = normalize_text(header)
    compact = normalized.replace(" ", "")
    tokens = set(normalized.split())
    best = 0
    for alias in aliases:
        alias_norm = normalize_text(alias)
        alias_compact = alias_norm.replace(" ", "")
        alias_tokens = set(alias_norm.split())
        if not alias_norm:
            continue
        if normalized == alias_norm or compact == alias_compact:
            best = max(best, 1_000)
        elif alias_norm in normalized or alias_compact in compact:
            best = max(best, 700 + min(200, len(alias_compact)))
        elif alias_tokens and alias_tokens.issubset(tokens):
            best = max(best, 500 + 20 * len(alias_tokens))
    return best


def resolve_best_column(
    columns: Sequence[str],
    aliases: Sequence[str],
    *,
    required: bool,
    label: str,
    excluded_terms: Sequence[str] = (),
    minimum_score: int = 500,
) -> Optional[str]:
    scored: List[Tuple[int, int, str]] = []
    for position, column in enumerate(columns):
        normalized = normalize_text(column)
        if any(normalize_text(term) in normalized for term in excluded_terms):
            continue
        score = header_match_score(column, aliases)
        if score >= minimum_score:
            scored.append((score, -position, column))

    if not scored:
        if required:
            raise SchedulerError(
                "Required column not found for {}. Looked for aliases: {}.\n"
                "Available columns:\n  - {}".format(
                    label,
                    ", ".join(aliases),
                    "\n  - ".join(str(c) for c in columns),
                )
            )
        return None

    scored.sort(reverse=True)
    top = scored[0]
    if len(scored) > 1 and scored[1][0] == top[0] and scored[1][2] != top[2]:
        raise SchedulerError(
            "Ambiguous columns for {}: '{}' and '{}'. Rename one column or "
            "add a more specific alias in the configuration.".format(
                label, top[2], scored[1][2]
            )
        )
    return top[2]


# =============================================================================
# 7. TIME AND AVAILABILITY PARSING
# =============================================================================

# Accepts 0:00, 00:00, 2 PM, 2:30pm, and 24:00 (as an interval endpoint).
_CLOCK_TOKEN = r"(?:2[0-4]|[01]?\d)(?:\s*:\s*[0-5]\d)?\s*(?:a\.?m\.?|p\.?m\.?)?"
_RANGE_RE = re.compile(
    r"({})\s*(?:-|–|—|to|until|through)\s*({})".format(_CLOCK_TOKEN, _CLOCK_TOKEN),
    flags=re.IGNORECASE,
)
_SINGLE_TIME_RE = re.compile(
    r"^\s*({})\s*(?:utc)?\s*$".format(_CLOCK_TOKEN), re.IGNORECASE
)


def parse_clock_token(token: str, *, allow_24: bool) -> int:
    text = token.lower().replace(".", "").replace(" ", "")
    ampm: Optional[str] = None
    if text.endswith("am"):
        ampm = "am"
        text = text[:-2]
    elif text.endswith("pm"):
        ampm = "pm"
        text = text[:-2]

    if ":" in text:
        hour_text, minute_text = text.split(":", 1)
    else:
        hour_text, minute_text = text, "0"

    hour = int(hour_text)
    minute = int(minute_text)
    if minute < 0 or minute > 59:
        raise ValueError("minute out of range")

    if ampm:
        if hour < 1 or hour > 12:
            raise ValueError("12-hour clock hour out of range")
        if ampm == "am":
            hour = 0 if hour == 12 else hour
        else:
            hour = 12 if hour == 12 else hour + 12
    else:
        if hour == 24 and minute == 0 and allow_24:
            return 1440
        if hour < 0 or hour > 23:
            raise ValueError("24-hour clock hour out of range")

    return hour * 60 + minute


def minutes_to_clock(minutes: int) -> str:
    normalized = minutes % 1440
    return "{:02d}:{:02d}".format(normalized // 60, normalized % 60)


def slot_label(start_minute: int, end_minute: Optional[int] = None) -> str:
    if end_minute is None:
        end_minute = (start_minute + SLOT_MINUTES) % 1440
    return "{}-{} {}".format(
        minutes_to_clock(start_minute), minutes_to_clock(end_minute), TIMEZONE_LABEL
    )


def all_day_slot_starts() -> Set[int]:
    return set(range(0, 1440, SLOT_MINUTES))


def range_to_slot_starts(start: int, end: int) -> Set[int]:
    """
    Return only complete SLOT_MINUTES appointments fully contained in a range.
    Overnight ranges are supported.
    """
    if start == 1440:
        start = 0
    if end == start:
        # A submitted 00:00-00:00 window is interpreted as all day.
        return all_day_slot_starts()

    if end > start:
        intervals = [(start, end)]
    else:
        intervals = [(start, 1440), (0, end)]

    slots: Set[int] = set()
    for interval_start, interval_end in intervals:
        for candidate in range(0, 1440, SLOT_MINUTES):
            candidate_end = candidate + SLOT_MINUTES
            if candidate >= interval_start and candidate_end <= interval_end:
                slots.add(candidate)
    return slots


def parse_availability(value: Any) -> Set[int]:
    """Parse every time range in a response cell into valid slot starts."""
    if is_blank(value):
        return all_day_slot_starts() if EMPTY_AVAILABILITY_MEANS_ALL_DAY else set()

    text = str(value)
    text_for_ranges = re.sub(r"\b(?:UTC|GMT)\b", "", text, flags=re.IGNORECASE)
    normalized = normalize_text(text)
    if any(
        phrase in normalized
        for phrase in ("all day", "any time", "anytime", "open availability")
    ):
        return all_day_slot_starts()

    slots: Set[int] = set()
    matches = list(_RANGE_RE.finditer(text_for_ranges))
    for match in matches:
        try:
            start = parse_clock_token(match.group(1), allow_24=False)
            end = parse_clock_token(match.group(2), allow_24=True)
        except (TypeError, ValueError):
            continue
        if end == 1440:
            if start < end:
                slots.update(range_to_slot_starts(start, end))
            else:
                slots.update(range_to_slot_starts(start, 0))
        else:
            slots.update(range_to_slot_starts(start, end))

    # Some forms contain a single exact appointment time instead of a range.
    if not slots:
        single = _SINGLE_TIME_RE.fullmatch(text_for_ranges.strip())
        if single:
            try:
                start = parse_clock_token(single.group(1), allow_24=False)
                if start % SLOT_MINUTES == 0:
                    slots.add(start)
            except (TypeError, ValueError):
                pass

    return slots


def parse_template_slot_cell(value: Any) -> Optional[Tuple[int, int, str]]:
    """Parse an Excel schedule cell as one 30-minute time slot."""
    if value is None:
        return None

    if isinstance(value, datetime):
        start = value.hour * 60 + value.minute
        end = (start + SLOT_MINUTES) % 1440
        return start, end, slot_label(start, end)

    if isinstance(value, time):
        start = value.hour * 60 + value.minute
        end = (start + SLOT_MINUTES) % 1440
        return start, end, slot_label(start, end)

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric = float(value)
        # Excel stores a time of day as a fraction of one day.
        if 0.0 <= numeric < 1.0:
            start = int(round(numeric * 1440)) % 1440
            if start % SLOT_MINUTES == 0:
                end = (start + SLOT_MINUTES) % 1440
                return start, end, slot_label(start, end)
        return None

    text = str(value).strip()
    text_for_ranges = re.sub(r"\b(?:UTC|GMT)\b", "", text, flags=re.IGNORECASE)
    range_match = _RANGE_RE.search(text_for_ranges)
    if range_match:
        try:
            start = parse_clock_token(range_match.group(1), allow_24=False)
            end_raw = parse_clock_token(range_match.group(2), allow_24=True)
            end = 0 if end_raw == 1440 else end_raw
        except (TypeError, ValueError):
            return None
        duration = (end - start) % 1440
        if duration == SLOT_MINUTES:
            return start, end, slot_label(start, end)
        # A template's time column should identify individual appointments.
        # Longer windows are not treated as slot rows.
        return None

    single_match = _SINGLE_TIME_RE.fullmatch(text_for_ranges)
    if single_match:
        try:
            start = parse_clock_token(single_match.group(1), allow_24=False)
        except (TypeError, ValueError):
            return None
        if start % SLOT_MINUTES == 0:
            end = (start + SLOT_MINUTES) % 1440
            return start, end, slot_label(start, end)
    return None


# =============================================================================
# 8. SPEEDUP AND RESOURCE SCORING
# =============================================================================

_DURATION_PART_RE = re.compile(
    r"(?P<number>\d+(?:\.\d+)?)\s*(?P<unit>days?|d|hours?|hrs?|hr|h|minutes?|mins?|min|m)\b",
    re.IGNORECASE,
)


def numeric_unit_to_minutes(value: float, unit: str) -> float:
    unit_normalized = normalize_text(unit)
    if unit_normalized == "days":
        return value * 1440.0
    if unit_normalized == "hours":
        return value * 60.0
    if unit_normalized == "minutes":
        return value
    raise SchedulerError("Unsupported numeric resource unit: {}".format(unit))


def parse_speedup_minutes(value: Any, *, numeric_unit: str) -> int:
    """Convert numeric or text speedup durations into whole minutes."""
    if is_blank(value):
        return 0

    if isinstance(value, pd.Timedelta):
        return max(0, int(round(value.total_seconds() / 60.0)))
    if isinstance(value, timedelta):
        return max(0, int(round(value.total_seconds() / 60.0)))
    if isinstance(value, time):
        return value.hour * 60 + value.minute
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric = float(value)
        if not math.isfinite(numeric) or numeric < 0:
            return 0
        return int(round(numeric_unit_to_minutes(numeric, numeric_unit)))

    text = str(value).strip().lower().replace(",", "")
    if not text:
        return 0

    # A submitted range such as "350-400 day" resolves to its conservative
    # lower bound so nobody is ranked on speedups they might not have.
    range_match = re.match(
        r"^\s*(\d+(?:\.\d+)?)\s*[-\u2013\u2014~]\s*\d+(?:\.\d+)?\s*", text
    )
    if range_match:
        text = range_match.group(1) + text[range_match.end() :]

    # Explicit duration text such as "12d 6h 30m".
    total_minutes = 0.0
    duration_matches = list(_DURATION_PART_RE.finditer(text))
    if duration_matches:
        for match in duration_matches:
            number = float(match.group("number"))
            unit = match.group("unit").lower()
            if unit.startswith("d"):
                total_minutes += number * 1440.0
            elif unit.startswith("h"):
                total_minutes += number * 60.0
            else:
                total_minutes += number
        return max(0, int(round(total_minutes)))

    # HH:MM duration, interpreted as hours and minutes rather than clock time.
    duration_clock = re.fullmatch(r"\s*(\d{1,4})\s*:\s*([0-5]\d)\s*", text)
    if duration_clock:
        return int(duration_clock.group(1)) * 60 + int(duration_clock.group(2))

    # Plain numeric string uses the configured ambiguous unit.  Messy real
    # answers such as "300/250", "~50", or "78 +" fall back to the first
    # number that appears rather than silently becoming zero.
    try:
        numeric = float(text)
    except ValueError:
        first_number = re.search(r"\d+(?:\.\d+)?", text)
        if not first_number:
            return 0
        numeric = float(first_number.group(0))
    if numeric < 0 or not math.isfinite(numeric):
        return 0
    return int(round(numeric_unit_to_minutes(numeric, numeric_unit)))


def resource_column_is_final_points(header: str) -> bool:
    normalized = normalize_text(header)
    return "score" in normalized or "point" in normalized


def numeric_resource_unit_from_header(header: str) -> str:
    """Use an explicit unit in the header before the global ambiguous default."""
    normalized = normalize_text(header)
    tokens = set(normalized.split())
    if "minute" in tokens or "minutes" in tokens or "mins" in tokens:
        return "minutes"
    if "hour" in tokens or "hours" in tokens or "hrs" in tokens:
        return "hours"
    if "day" in tokens or "days" in tokens:
        return "days"
    return AMBIGUOUS_NUMERIC_RESOURCE_UNIT


def resource_value_to_points(value: Any, header: str) -> int:
    numeric_unit = numeric_resource_unit_from_header(header)
    if resource_column_is_final_points(header) or numeric_unit == "points":
        return max(0, int(round(parse_number(value))))
    minutes = parse_speedup_minutes(value, numeric_unit=numeric_unit)
    return max(0, minutes * POINTS_PER_SPEEDUP_MINUTE)


# =============================================================================
# 9. APPLICATION WORKBOOK DISCOVERY AND COLUMN MAPPING
# =============================================================================

_ID_ALIASES = (
    "chief id",
    "player id",
    "governor id",
    "user id",
    "chief id 9 digit id from chief page",
    "id from chief page",
)
_TAG_ALIASES = (
    "alliance name",
    "alliance tag",
    "player tag",
    "chief tag",
    "alliance",
    "tag",
)
_TIMESTAMP_ALIASES = (
    "completion time",
    "timestamp",
    "submitted at",
    "submission time",
    "response date",
    "date submitted",
)
_SELECTED_DAY_ALIASES = (
    "which ministry appointments",
    "ministry appointments you would like to apply for",
    "appointments you would like to apply for",
    "desired buffs",
    "selected buffs",
    "apply for multiple selections allowed",
)
_GENERAL_AVAILABILITY_ALIASES = (
    "what time range can you use the appointment buffs",
    "what time range can you use the appointment buff",
    "general availability",
    "available time range",
    "preferred time range",
)
_GENERAL_RESOURCE_ALIASES = (
    "general speedups",
    "general speedup",
    "general su",
    "general",
)
_FIRE_CRYSTAL_ALIASES = (
    "refined fire crystal",
    "refined fire crystals",
)
_PLAIN_FIRE_CRYSTAL_ALIASES = (
    "fire crystals",
    "fire crystal",
    "how many fire crystals",
    "fc count",
)
_FIRE_CRYSTAL_SHARD_ALIASES = (
    "fire crystal shards",
    "fire shards",
    "fire shard",
    "fire crystal shard",
    "fc shards",
    "fc shard",
    "shards for research",
)


def day_resource_aliases(day: DaySpec) -> Tuple[str, ...]:
    if day.key == "construction":
        return (
            "construction points",
            "construction score",
            "construction speedups",
            "construction speedup",
            "construction su",
            "construction",
            "build speedups",
        )
    if day.key == "research":
        return (
            "research points",
            "research score",
            "research speedups",
            "research speedup",
            "research su",
            "research",
        )
    return (
        "training points",
        "troop training points",
        "training score",
        "training speedups",
        "troop speedups",
        "troop training speedups",
        "training su",
        "training",
        "troop",
    )


def day_availability_aliases(day: DaySpec) -> Tuple[str, ...]:
    aliases: List[str] = []
    for term in day.aliases:
        aliases.extend(
            (
                "{} availability".format(term),
                "{} time slots".format(term),
                "{} time slot".format(term),
                "{} preferred time".format(term),
                "{} time range".format(term),
            )
        )
    return tuple(aliases)


def locate_application_header(path: Path) -> Tuple[str, int]:
    """Choose the worksheet/header row that most resembles form responses."""
    try:
        excel_file = pd.ExcelFile(path, engine="openpyxl")
    except Exception as exc:
        raise SchedulerError(
            "Could not open application workbook '{}': {}".format(path, exc)
        )

    candidates: List[Tuple[int, int, str, int]] = []
    for sheet_position, sheet_name in enumerate(excel_file.sheet_names):
        try:
            preview = pd.read_excel(
                path,
                sheet_name=sheet_name,
                header=None,
                nrows=40,
                engine="openpyxl",
                dtype=object,
            )
        except Exception:
            continue

        for row_index in range(len(preview.index)):
            cells = [
                normalize_text(v)
                for v in preview.iloc[row_index].tolist()
                if not is_blank(v)
            ]
            if not cells:
                continue
            joined = " | ".join(cells)
            score = 0
            if any(header_match_score(cell, _ID_ALIASES) >= 500 for cell in cells):
                score += 1_000
            if any("time" in cell or "availability" in cell for cell in cells):
                score += 200
            score += 100 * sum(
                1 for day in DAY_SPECS if any(alias in joined for alias in day.aliases)
            )
            if "timestamp" in joined:
                score += 50
            score += min(100, len(cells))
            candidates.append((score, -sheet_position, sheet_name, row_index))

    if not candidates:
        raise SchedulerError(
            "No readable worksheet was found in the application workbook."
        )

    candidates.sort(reverse=True)
    best_score, _sheet_order, best_sheet, best_row = candidates[0]
    if best_score < 1_000:
        raise SchedulerError(
            "The program could not identify the application header row. No row "
            "contained a recognizable Player/Chief ID header."
        )
    return best_sheet, best_row


def read_application_table(path: Path) -> Tuple[pd.DataFrame, str, int]:
    sheet_name, header_row = locate_application_header(path)
    try:
        frame = pd.read_excel(
            path,
            sheet_name=sheet_name,
            header=header_row,
            engine="openpyxl",
            dtype=object,
        )
    except Exception as exc:
        raise SchedulerError(
            "Could not read application data from sheet '{}' row {}: {}".format(
                sheet_name, header_row + 1, exc
            )
        )

    frame = frame.dropna(how="all").copy()
    cleaned_columns: List[str] = []
    seen: Dict[str, int] = {}
    for raw_column in frame.columns:
        base = str(raw_column).replace("\u00a0", " ").strip()
        if not base or base.lower().startswith("unnamed"):
            base = "Unnamed Column"
        count = seen.get(base, 0)
        seen[base] = count + 1
        cleaned_columns.append(
            base if count == 0 else "{} [{}]".format(base, count + 1)
        )
    frame.columns = cleaned_columns
    frame["__source_order"] = range(len(frame))
    frame["__source_excel_row"] = frame["__source_order"] + header_row + 2
    return frame, sheet_name, header_row


def choose_day_resource_column(columns: Sequence[str], day: DaySpec) -> str:
    candidates: List[Tuple[int, int, str]] = []
    for position, column in enumerate(columns):
        normalized = normalize_text(column)
        if any(
            term in normalized
            for term in (
                "time slot",
                "time range",
                "what time",
                "availability",
                "appointment",
                "apply for",
                "selected buff",
                "which ministry",
                "plan to use",
            )
        ):
            continue
        score = header_match_score(column, day_resource_aliases(day))
        if score < 500:
            continue
        if "score" in normalized or "point" in normalized:
            score += 300
        elif "speedup" in normalized or re.search(r"\bsu\b", normalized):
            score += 150
        candidates.append((score, -position, column))

    if not candidates:
        raise SchedulerError(
            "Could not find the {} resource/score column. Available columns:\n  - {}".format(
                day.label, "\n  - ".join(str(c) for c in columns)
            )
        )
    candidates.sort(reverse=True)
    return candidates[0][2]


def map_availability_columns(
    columns: Sequence[str], day_resource_columns: Mapping[str, str]
) -> Tuple[Optional[str], Dict[str, Optional[str]], List[str]]:
    warnings: List[str] = []
    positions = {column: index for index, column in enumerate(columns)}

    general = resolve_best_column(
        columns,
        _GENERAL_AVAILABILITY_ALIASES,
        required=False,
        label="general availability",
        minimum_score=650,
    )

    day_map: Dict[str, Optional[str]] = {day.key: None for day in DAY_SPECS}
    used: Set[str] = set()

    # First use headers that explicitly name a day.
    for day in DAY_SPECS:
        direct = resolve_best_column(
            columns,
            day_availability_aliases(day),
            required=False,
            label="{} availability".format(day.label),
            minimum_score=650,
        )
        if direct:
            day_map[day.key] = direct
            used.add(direct)

    time_candidates: List[str] = []
    for column in columns:
        normalized = normalize_text(column)
        if any(
            term in normalized
            for term in (
                "time slot",
                "timeslot",
                "time range",
                "availability",
                "preferred time",
            )
        ):
            time_candidates.append(column)

    # A single undifferentiated availability column applies to all three days.
    if general and all(day_map[day.key] is None for day in DAY_SPECS):
        for day in DAY_SPECS:
            day_map[day.key] = general
        return general, day_map, warnings

    # Map remaining generic time columns to the nearest resource column.  This
    # handles forms laid out as Construction -> Time slots -> Research ->
    # Time slots 2 -> Training -> Time slots 3.
    for day in DAY_SPECS:
        if day_map[day.key] is not None:
            continue
        resource_position = positions[day_resource_columns[day.key]]
        available_candidates = [
            column
            for column in time_candidates
            if column not in used and column != general
        ]
        if available_candidates:
            after = [
                c for c in available_candidates if positions[c] > resource_position
            ]
            pool = after if after else available_candidates
            nearest = min(pool, key=lambda c: abs(positions[c] - resource_position))
            day_map[day.key] = nearest
            used.add(nearest)
            warnings.append(
                "Mapped '{}' to {} by column proximity.".format(nearest, day.label)
            )

    # If exactly three unresolved generic time columns remain, map left-to-right
    # as Construction, Research, Training.
    unresolved_days = [day for day in DAY_SPECS if day_map[day.key] is None]
    remaining_times = [c for c in time_candidates if c not in used and c != general]
    if unresolved_days and len(unresolved_days) == len(remaining_times):
        remaining_times.sort(key=lambda c: positions[c])
        for day, column in zip(unresolved_days, remaining_times):
            day_map[day.key] = column
            warnings.append(
                "Mapped '{}' to {} by left-to-right order.".format(column, day.label)
            )

    # Final fallback: a recognized general column can fill any still-empty day.
    if general:
        for day in DAY_SPECS:
            if day_map[day.key] is None:
                day_map[day.key] = general

    missing = [
        DAY_BY_KEY[key].label for key, column in day_map.items() if column is None
    ]
    if missing:
        raise SchedulerError(
            "Could not map availability columns for: {}. Time-related columns found: {}".format(
                ", ".join(missing), ", ".join(time_candidates) or "none"
            )
        )

    return general, day_map, warnings


def build_input_mapping(
    frame: pd.DataFrame, sheet_name: str, header_row: int
) -> InputMapping:
    columns = [c for c in frame.columns if not c.startswith("__")]
    id_column = resolve_best_column(
        columns,
        _ID_ALIASES,
        required=True,
        label="Player/Chief ID",
        minimum_score=500,
    )
    assert id_column is not None

    tag_column = resolve_best_column(
        columns,
        _TAG_ALIASES,
        required=False,
        label="Alliance/Tag",
        excluded_terms=("time", "email"),
        minimum_score=500,
    )
    timestamp_column = resolve_best_column(
        columns,
        _TIMESTAMP_ALIASES,
        required=False,
        label="Timestamp",
        minimum_score=500,
    )
    selected_days_column = resolve_best_column(
        columns,
        _SELECTED_DAY_ALIASES,
        required=False,
        label="Selected Ministry Appointments",
        minimum_score=500,
    )

    day_resource_columns = {
        day.key: choose_day_resource_column(columns, day) for day in DAY_SPECS
    }
    general_availability, day_availability_columns, availability_warnings = (
        map_availability_columns(columns, day_resource_columns)
    )

    general_resource_column = resolve_best_column(
        columns,
        _GENERAL_RESOURCE_ALIASES,
        required=False,
        label="General Speedups",
        excluded_terms=(
            "availability",
            "time slot",
            "time range",
            "what time",
            "plan",
        ),
        minimum_score=650,
    )

    refined_fire_crystal_column = resolve_best_column(
        columns,
        _FIRE_CRYSTAL_ALIASES,
        required=False,
        label="Refined Fire Crystals",
        minimum_score=650,
    )
    fire_crystal_shard_column = resolve_best_column(
        columns,
        _FIRE_CRYSTAL_SHARD_ALIASES,
        required=False,
        label="Fire Crystal Shards",
        minimum_score=650,
    )

    # Plain Fire Crystal matching must explicitly exclude refined/shard headers.
    fire_crystal_candidates: List[Tuple[int, int, str]] = []
    for position, column in enumerate(columns):
        normalized = normalize_text(column)
        if "refined" in normalized or "shard" in normalized:
            continue
        score = header_match_score(column, _PLAIN_FIRE_CRYSTAL_ALIASES)
        if score >= 500:
            fire_crystal_candidates.append((score, -position, column))
    fire_crystal_candidates.sort(reverse=True)
    fire_crystal_column = (
        fire_crystal_candidates[0][2] if fire_crystal_candidates else None
    )

    warnings = list(availability_warnings)
    if selected_days_column is None:
        warnings.append(
            "No selected-appointment column was found. Eligibility will be inferred "
            "from positive resources and nonblank day availability."
        )
    if tag_column is None:
        warnings.append(
            "No Alliance/Tag column was found. The output will contain Player ID only."
        )
    if general_resource_column is None:
        warnings.append(
            "No General Speedups column was found; no shared cross-day speedup pool will be modeled."
        )

    return InputMapping(
        sheet_name=sheet_name,
        header_row_zero_based=header_row,
        id_column=id_column,
        tag_column=tag_column,
        timestamp_column=timestamp_column,
        selected_days_column=selected_days_column,
        general_availability_column=general_availability,
        day_availability_columns=day_availability_columns,
        day_resource_columns=day_resource_columns,
        general_resource_column=general_resource_column,
        fire_crystal_column=fire_crystal_column,
        refined_fire_crystal_column=refined_fire_crystal_column,
        fire_crystal_shard_column=fire_crystal_shard_column,
        warnings=warnings,
    )


def parse_selected_days(value: Any) -> Set[str]:
    if is_blank(value):
        return set()
    normalized = normalize_text(value)
    all_phrases = {
        "all",
        "all appointments",
        "all buffs",
        "all three",
        "all three appointments",
    }
    if normalized in all_phrases or any(
        phrase in normalized for phrase in all_phrases if phrase != "all"
    ):
        return {day.key for day in DAY_SPECS}
    selected: Set[str] = set()
    for day in DAY_SPECS:
        if any(alias in normalized for alias in day.aliases):
            selected.add(day.key)
    return selected


def deduplicate_applications(
    frame: pd.DataFrame, mapping: InputMapping
) -> pd.DataFrame:
    working = frame.copy()
    working["__player_id"] = working[mapping.id_column].map(canonical_player_id)
    working = working.loc[working["__player_id"] != ""].copy()
    if working.empty:
        raise SchedulerError(
            "No valid Player/Chief IDs were found in the application data."
        )

    duplicate_count = int(working.duplicated("__player_id", keep=False).sum())
    if mapping.timestamp_column:
        working["__parsed_timestamp"] = working[mapping.timestamp_column].map(
            parse_form_timestamp
        )
        # Stable sorting ensures the last submitted row wins.  Rows with an
        # unreadable timestamp fall back to their original worksheet order.
        working = working.sort_values(
            ["__player_id", "__parsed_timestamp", "__source_order"],
            kind="mergesort",
            na_position="first",
        )
    else:
        working = working.sort_values(
            ["__player_id", "__source_order"], kind="mergesort"
        )

    deduplicated = working.drop_duplicates("__player_id", keep="last").copy()
    if duplicate_count:
        LOGGER.info(
            "Duplicate responses were resolved by Player/Chief ID; latest response retained."
        )
    return deduplicated.sort_values("__source_order", kind="mergesort")


def build_player_records(
    frame: pd.DataFrame, mapping: InputMapping
) -> List[PlayerRecord]:
    deduplicated = deduplicate_applications(frame, mapping)
    players: List[PlayerRecord] = []

    for _, row in deduplicated.iterrows():
        player_id = canonical_player_id(row[mapping.id_column])
        tag = canonical_tag(row[mapping.tag_column]) if mapping.tag_column else ""

        day_points: Dict[str, int] = {}
        final_score_days: Set[str] = set()
        for day in DAY_SPECS:
            column = mapping.day_resource_columns[day.key]
            day_points[day.key] = resource_value_to_points(row[column], column)
            if resource_column_is_final_points(column):
                final_score_days.add(day.key)

        # Add day-specific special resources only when that day column is not
        # already an explicit final score, unless the configuration says otherwise.
        construction_is_final = "construction" in final_score_days
        research_is_final = "research" in final_score_days
        if not construction_is_final or ADD_SPECIAL_RESOURCES_TO_FINAL_SCORE_COLUMNS:
            if mapping.fire_crystal_column:
                day_points["construction"] += int(
                    round(
                        parse_number(row[mapping.fire_crystal_column])
                        * POINTS_PER_FIRE_CRYSTAL
                    )
                )
            if mapping.refined_fire_crystal_column:
                day_points["construction"] += int(
                    round(
                        parse_number(row[mapping.refined_fire_crystal_column])
                        * POINTS_PER_REFINED_FIRE_CRYSTAL
                    )
                )
        if not research_is_final or ADD_SPECIAL_RESOURCES_TO_FINAL_SCORE_COLUMNS:
            if mapping.fire_crystal_shard_column:
                day_points["research"] += int(
                    round(
                        parse_number(row[mapping.fire_crystal_shard_column])
                        * POINTS_PER_FIRE_CRYSTAL_SHARD
                    )
                )

        general_points = 0
        if mapping.general_resource_column:
            general_points = resource_value_to_points(
                row[mapping.general_resource_column], mapping.general_resource_column
            )

        available_slots: Dict[str, Set[int]] = {}
        for day in DAY_SPECS:
            availability_column = mapping.day_availability_columns[day.key]
            available_slots[day.key] = (
                parse_availability(row[availability_column])
                if availability_column
                else set()
            )

        if mapping.selected_days_column:
            selected_days = parse_selected_days(row[mapping.selected_days_column])
        else:
            selected_days = {
                day.key
                for day in DAY_SPECS
                if available_slots[day.key]
                and (day_points[day.key] > 0 or general_points > 0)
            }

        # A selected appointment with no availability remains selected but cannot
        # produce candidate edges; diagnostics will report the mismatch.
        players.append(
            PlayerRecord(
                player_id=player_id,
                tag=tag,
                source_order=int(row["__source_order"]),
                selected_days=selected_days,
                available_slots=available_slots,
                base_points=day_points,
                general_points=general_points,
                source_excel_row=int(row["__source_excel_row"]),
            )
        )

    if not players:
        raise SchedulerError("No usable player records remained after validation.")
    return players


# =============================================================================
# 10. TEMPLATE DISCOVERY
# =============================================================================


@dataclass
class _TemplateCandidate:
    sheet_name: str
    time_column: int
    rows: List[int]
    parsed: Dict[int, Tuple[int, int, str]]
    regularity_score: int


_TEMPLATE_ID_ALIASES = (
    "player id",
    "chief id",
    "governor id",
    "user id",
    "id",
)
_TEMPLATE_TAG_ALIASES = (
    "alliance",
    "alliance tag",
    "tag",
    "player tag",
)


def split_consecutive_rows(
    rows: Sequence[int], maximum_gap: int = 2
) -> List[List[int]]:
    if not rows:
        return []
    groups: List[List[int]] = [[rows[0]]]
    for row in rows[1:]:
        if row - groups[-1][-1] <= maximum_gap:
            groups[-1].append(row)
        else:
            groups.append([row])
    return groups


def find_template_candidates(workbook_path: Path) -> List[_TemplateCandidate]:
    try:
        # The formula workbook preserves the original layout and formulas. The
        # data-only workbook exposes cached formula results when Excel generated
        # the time slots with formulas. Checking both makes template discovery
        # work for literal times and formula-driven times alike.
        workbook = load_workbook(workbook_path, data_only=False)
        values_workbook = load_workbook(workbook_path, data_only=True)
    except Exception as exc:
        raise SchedulerError(
            "Could not open schedule template '{}': {}".format(workbook_path, exc)
        )

    candidates: List[_TemplateCandidate] = []
    for worksheet in workbook.worksheets:
        values_worksheet = values_workbook[worksheet.title]
        max_row = min(worksheet.max_row, 500)
        max_column = min(worksheet.max_column, 100)
        for column in range(1, max_column + 1):
            parsed_by_row: Dict[int, Tuple[int, int, str]] = {}
            for row in range(1, max_row + 1):
                parsed = parse_template_slot_cell(
                    worksheet.cell(row=row, column=column).value
                )
                if parsed is None:
                    parsed = parse_template_slot_cell(
                        values_worksheet.cell(row=row, column=column).value
                    )
                if parsed:
                    parsed_by_row[row] = parsed
            for row_group in split_consecutive_rows(sorted(parsed_by_row)):
                if len(row_group) < MIN_TEMPLATE_SLOTS_PER_BLOCK:
                    continue
                starts = [parsed_by_row[row][0] for row in row_group]
                unique_starts = len(set(starts))
                aligned = sum(1 for start in starts if start % SLOT_MINUTES == 0)
                regularity = unique_starts * 10 + aligned
                candidates.append(
                    _TemplateCandidate(
                        sheet_name=worksheet.title,
                        time_column=column,
                        rows=row_group,
                        parsed={row: parsed_by_row[row] for row in row_group},
                        regularity_score=regularity,
                    )
                )
    return candidates


def template_day_context_score(
    worksheet: Any, candidate: _TemplateCandidate, day: DaySpec
) -> int:
    score = 0
    sheet_normalized = normalize_text(worksheet.title)
    if any(alias in sheet_normalized for alias in day.aliases):
        score += 1_000
    if any(alias in sheet_normalized for alias in day.day_number_aliases):
        score += 500

    first_row = min(candidate.rows)
    row_start = max(1, first_row - 15)
    row_end = min(worksheet.max_row, first_row + 2)
    col_start = max(1, candidate.time_column - 5)
    col_end = min(worksheet.max_column, candidate.time_column + 5)

    for row in range(row_start, row_end + 1):
        for column in range(col_start, col_end + 1):
            value = worksheet.cell(row=row, column=column).value
            if is_blank(value):
                continue
            normalized = normalize_text(value)
            distance = (
                abs(column - candidate.time_column) * 12 + abs(first_row - row) * 2
            )
            local = max(1, 300 - distance)
            if any(alias in normalized for alias in day.aliases):
                score = max(score, local + 300)
            if any(alias in normalized for alias in day.day_number_aliases):
                score = max(score, local + 100)
    return score


def find_nearby_header_column(
    worksheet: Any,
    candidate: _TemplateCandidate,
    aliases: Sequence[str],
    *,
    minimum_score: int,
) -> Optional[int]:
    first_row = min(candidate.rows)
    best: Optional[Tuple[int, int, int]] = None
    for row in range(max(1, first_row - 15), first_row):
        for column in range(
            max(1, candidate.time_column - 5),
            min(worksheet.max_column, candidate.time_column + 6) + 1,
        ):
            value = worksheet.cell(row=row, column=column).value
            if is_blank(value):
                continue
            match_score = header_match_score(str(value), aliases)
            if match_score < minimum_score:
                continue
            distance = abs(candidate.time_column - column) * 20 + (first_row - row)
            item = (match_score * 1_000 - distance, -distance, column)
            if best is None or item > best:
                best = item
    return best[2] if best else None


def find_day_header_anchor(
    worksheet: Any, candidate: _TemplateCandidate, day: DaySpec
) -> Optional[Tuple[int, int]]:
    """Find the worksheet cell that most clearly labels one boost day.

    This is used for templates with one shared time column and separate output
    columns for Construction, Research, and Training.  The return value is
    ``(row, column)`` for the best semantic day heading.
    """
    first_row = min(candidate.rows)
    best: Optional[Tuple[int, int, int]] = None
    for row in range(max(1, first_row - 20), first_row):
        for column in range(1, min(worksheet.max_column, 100) + 1):
            value = worksheet.cell(row=row, column=column).value
            if is_blank(value):
                continue
            normalized = normalize_text(value)
            alias_hits = sum(1 for alias in day.aliases if alias in normalized)
            number_hits = sum(
                1 for alias in day.day_number_aliases if alias in normalized
            )
            if alias_hits == 0 and number_hits == 0:
                continue

            # Prefer explicit day names, then day numbers, then headings close
            # to the actual schedule rows.  A column-distance penalty is not
            # used because a shared time column can sit far to the left.
            semantic_score = alias_hits * 10_000 + number_hits * 3_000
            if normalized == normalize_text(day.label):
                semantic_score += 5_000
            row_distance = first_row - row
            item = (semantic_score - row_distance, -row_distance, column)
            if best is None or item > best:
                best = item
                best_row = row

    if best is None:
        return None
    return best_row, best[2]


def find_output_column_near_day_anchor(
    worksheet: Any,
    candidate: _TemplateCandidate,
    day: DaySpec,
    anchor: Tuple[int, int],
    aliases: Sequence[str],
    *,
    minimum_score: int,
) -> Optional[int]:
    """Find an ID or Tag header associated with a specific day heading."""
    first_row = min(candidate.rows)
    _anchor_row, anchor_column = anchor
    best: Optional[Tuple[int, int, int]] = None

    for row in range(max(1, first_row - 20), first_row):
        for column in range(
            max(1, anchor_column - 6),
            min(worksheet.max_column, anchor_column + 6) + 1,
        ):
            value = worksheet.cell(row=row, column=column).value
            if is_blank(value):
                continue
            header_score = header_match_score(str(value), aliases)
            if header_score < minimum_score:
                continue

            normalized = normalize_text(value)
            day_bonus = 0
            if any(alias in normalized for alias in day.aliases):
                day_bonus += 500
            if any(alias in normalized for alias in day.day_number_aliases):
                day_bonus += 200

            horizontal_distance = abs(column - anchor_column)
            vertical_distance = first_row - row
            score = (
                header_score * 10_000
                + day_bonus * 1_000
                - horizontal_distance * 100
                - vertical_distance
            )
            item = (score, -horizontal_distance, column)
            if best is None or item > best:
                best = item

    return best[2] if best else None


def candidate_slots_for_columns(
    worksheet: Any,
    candidate: _TemplateCandidate,
    day: DaySpec,
    id_column: int,
    tag_column: Optional[int],
    warnings: List[str],
) -> List[TemplateSlot]:
    """Build and validate TemplateSlot records for one mapped output column."""
    slots: List[TemplateSlot] = []
    seen_starts: Set[int] = set()

    for row in sorted(candidate.rows):
        start, end, display = candidate.parsed[row]
        if start in seen_starts:
            warnings.append(
                "Duplicate {} slot {} in '{}', row {} was ignored.".format(
                    day.label, display, candidate.sheet_name, row
                )
            )
            continue
        seen_starts.add(start)

        current_id = worksheet.cell(row=row, column=id_column).value
        locked = PRESERVE_EXISTING_ASSIGNMENTS and not value_is_open_placeholder(
            current_id
        )
        locked_player_id = (
            extract_player_id_from_schedule_cell(current_id) if locked else ""
        )
        slots.append(
            TemplateSlot(
                day_key=day.key,
                sheet_name=candidate.sheet_name,
                row=row,
                time_column=candidate.time_column,
                id_column=id_column,
                tag_column=tag_column,
                start_minute=start,
                end_minute=end,
                display_label=display,
                locked=locked,
                locked_player_id=locked_player_id,
            )
        )

    return slots


def try_shared_time_column_layout(
    workbook: Any,
    candidates: Sequence[_TemplateCandidate],
    candidate_time_columns_by_sheet: Mapping[str, Set[int]],
) -> Tuple[Optional[Dict[str, TemplateBlock]], List[str]]:
    """Map templates that reuse one time column for all three boost days.

    Supported examples include::

        Time | Construction ID | Research ID | Training ID

    and two-row headers such as::

        Time | Construction       | Research           | Training
             | Player ID | Tag    | Player ID | Tag    | Player ID | Tag

    The function returns ``(None, [])`` when no safe shared-column mapping can
    be proven; it never silently guesses among ambiguous output columns.
    """
    local_warnings: List[str] = []
    ordered_candidates = sorted(
        candidates,
        key=lambda item: (
            -len(item.rows),
            -item.regularity_score,
            workbook.sheetnames.index(item.sheet_name),
            item.time_column,
            min(item.rows),
        ),
    )

    for candidate in ordered_candidates:
        worksheet = workbook[candidate.sheet_name]
        anchors: Dict[str, Tuple[int, int]] = {}
        for day in DAY_SPECS:
            anchor = find_day_header_anchor(worksheet, candidate, day)
            if anchor is not None:
                anchors[day.key] = anchor

        id_columns: Dict[str, int] = {}
        tag_columns: Dict[str, Optional[int]] = {}
        mapping_method = "shared-time-column semantic headers"

        if len(anchors) == len(DAY_SPECS):
            for day in DAY_SPECS:
                anchor = anchors[day.key]
                id_column = find_output_column_near_day_anchor(
                    worksheet,
                    candidate,
                    day,
                    anchor,
                    _TEMPLATE_ID_ALIASES,
                    minimum_score=500,
                )
                tag_column = find_output_column_near_day_anchor(
                    worksheet,
                    candidate,
                    day,
                    anchor,
                    _TEMPLATE_TAG_ALIASES,
                    minimum_score=500,
                )

                # A day heading such as "Construction" can itself be the output
                # column when the template has no secondary "Player ID" row.
                if id_column is None and anchor[1] != candidate.time_column:
                    id_column = anchor[1]
                    local_warnings.append(
                        "Used the '{}' day-heading column as the Player ID column.".format(
                            day.label
                        )
                    )

                if id_column is None:
                    id_columns = {}
                    break
                if tag_column in {candidate.time_column, id_column}:
                    tag_column = None
                id_columns[day.key] = id_column
                tag_columns[day.key] = tag_column

        # Positional fallback for a common, unambiguous structure with exactly
        # three repeated Player ID headers but no semantic day names.
        if len(id_columns) != len(DAY_SPECS):
            first_row = min(candidate.rows)
            found_id_columns: Set[int] = set()
            for row in range(max(1, first_row - 20), first_row):
                for column in range(1, min(worksheet.max_column, 100) + 1):
                    if column == candidate.time_column:
                        continue
                    value = worksheet.cell(row=row, column=column).value
                    if is_blank(value):
                        continue
                    if header_match_score(str(value), _TEMPLATE_ID_ALIASES) >= 500:
                        found_id_columns.add(column)

            sorted_ids = sorted(found_id_columns)
            if len(sorted_ids) == len(DAY_SPECS):
                id_columns = {
                    day.key: column for day, column in zip(DAY_SPECS, sorted_ids)
                }
                tag_columns = {day.key: None for day in DAY_SPECS}
                mapping_method = "shared-time-column positional ID headers"
                local_warnings.append(
                    "Mapped shared-time output columns left-to-right as Construction, "
                    "Research, and Training because exactly three Player ID headers were found."
                )

        if len(id_columns) != len(DAY_SPECS):
            continue
        if len(set(id_columns.values())) != len(DAY_SPECS):
            continue
        if any(
            column in candidate_time_columns_by_sheet.get(candidate.sheet_name, set())
            for column in id_columns.values()
        ):
            continue

        # Prevent a Tag column from colliding with another day's ID column.
        all_id_columns = set(id_columns.values())
        for day in DAY_SPECS:
            if tag_columns.get(day.key) in all_id_columns:
                tag_columns[day.key] = None

        blocks: Dict[str, TemplateBlock] = {}
        for day in DAY_SPECS:
            tag_column = tag_columns.get(day.key)
            if tag_column is None:
                local_warnings.append(
                    "No separate Tag/Alliance column was found for {}; ID and Tag "
                    "will be combined in its ID cell.".format(day.label)
                )
            slots = candidate_slots_for_columns(
                worksheet,
                candidate,
                day,
                id_columns[day.key],
                tag_column,
                local_warnings,
            )
            if not slots:
                blocks = {}
                break
            blocks[day.key] = TemplateBlock(
                day_key=day.key,
                sheet_name=candidate.sheet_name,
                time_column=candidate.time_column,
                id_column=id_columns[day.key],
                tag_column=tag_column,
                slots=slots,
                mapping_method=mapping_method,
            )

        if len(blocks) == len(DAY_SPECS):
            return blocks, local_warnings

    return None, []


def extract_player_id_from_schedule_cell(value: Any) -> str:
    """Extract a numeric Whiteout player ID from an existing schedule cell."""
    if is_blank(value):
        return ""
    canonical = canonical_player_id(value)
    digit_match = re.search(r"(?<!\d)(\d{6,12})(?!\d)", canonical)
    if digit_match:
        return digit_match.group(1)
    # Fallback for an unusual alphanumeric ID written as "ID - TAG".
    first_piece = re.split(r"\s[-|/]\s", canonical, maxsplit=1)[0].strip()
    return first_piece


def value_is_open_placeholder(value: Any) -> bool:
    if is_blank(value):
        return True
    normalized = normalize_text(value)
    return normalized in {"open", "available", "tbd", "none", "n a", "na", "-"}


def discover_template_blocks(path: Path) -> Tuple[Dict[str, TemplateBlock], List[str]]:
    warnings: List[str] = []
    candidates = find_template_candidates(path)
    if not candidates:
        raise SchedulerError(
            "No runs of 30-minute time slots were found in the schedule template."
        )

    workbook = load_workbook(path, data_only=False)
    # Prefer longer, more regular runs and remove duplicate candidates that point
    # to the same sheet/column/row range.
    unique: Dict[Tuple[str, int, int, int], _TemplateCandidate] = {}
    for candidate in candidates:
        key = (
            candidate.sheet_name,
            candidate.time_column,
            min(candidate.rows),
            max(candidate.rows),
        )
        current = unique.get(key)
        if current is None or candidate.regularity_score > current.regularity_score:
            unique[key] = candidate
    candidates = list(unique.values())

    candidate_time_columns_by_sheet: Dict[str, Set[int]] = {}
    for candidate in candidates:
        candidate_time_columns_by_sheet.setdefault(candidate.sheet_name, set()).add(
            candidate.time_column
        )

    day_candidate_scores: Dict[str, List[Tuple[int, int, _TemplateCandidate]]] = {
        day.key: [] for day in DAY_SPECS
    }
    for candidate in candidates:
        worksheet = workbook[candidate.sheet_name]
        for day in DAY_SPECS:
            context_score = template_day_context_score(worksheet, candidate, day)
            total = (
                context_score * 10_000
                + len(candidate.rows) * 100
                + candidate.regularity_score
            )
            day_candidate_scores[day.key].append((total, context_score, candidate))

    assigned_candidates: Dict[str, _TemplateCandidate] = {}
    used_signatures: Set[Tuple[str, int, int, int]] = set()

    # Assign only candidates with positive semantic context first.
    for day in DAY_SPECS:
        options = sorted(
            day_candidate_scores[day.key], key=lambda item: item[0], reverse=True
        )
        for _total, context_score, candidate in options:
            signature = (
                candidate.sheet_name,
                candidate.time_column,
                min(candidate.rows),
                max(candidate.rows),
            )
            if context_score > 0 and signature not in used_signatures:
                assigned_candidates[day.key] = candidate
                used_signatures.add(signature)
                break

    # Safe positional fallback for exactly three principal blocks.
    missing_days = [day for day in DAY_SPECS if day.key not in assigned_candidates]
    remaining_candidates = [
        candidate
        for candidate in candidates
        if (
            candidate.sheet_name,
            candidate.time_column,
            min(candidate.rows),
            max(candidate.rows),
        )
        not in used_signatures
    ]
    if missing_days:
        principal = sorted(
            remaining_candidates,
            key=lambda c: (-len(c.rows), c.sheet_name, c.time_column, min(c.rows)),
        )[: len(missing_days)]
        if len(principal) == len(missing_days):
            principal.sort(
                key=lambda c: (
                    workbook.sheetnames.index(c.sheet_name),
                    c.time_column,
                    min(c.rows),
                )
            )
            for day, candidate in zip(missing_days, principal):
                assigned_candidates[day.key] = candidate
                warnings.append(
                    "Template block for {} was mapped by workbook position because no clear day heading was found.".format(
                        day.label
                    )
                )

    if any(day.key not in assigned_candidates for day in DAY_SPECS):
        shared_blocks, shared_warnings = try_shared_time_column_layout(
            workbook, candidates, candidate_time_columns_by_sheet
        )
        if shared_blocks is not None:
            warnings.extend(shared_warnings)
            return shared_blocks, warnings

        missing = [day.label for day in DAY_SPECS if day.key not in assigned_candidates]
        raise SchedulerError(
            "The schedule template could not be mapped to all three boost days. Missing: {}. "
            "The program also checked for a one-time-column/three-output-column layout.".format(
                ", ".join(missing)
            )
        )

    blocks: Dict[str, TemplateBlock] = {}

    for day in DAY_SPECS:
        candidate = assigned_candidates[day.key]
        worksheet = workbook[candidate.sheet_name]
        id_column = find_nearby_header_column(
            worksheet, candidate, _TEMPLATE_ID_ALIASES, minimum_score=500
        )
        tag_column = find_nearby_header_column(
            worksheet, candidate, _TEMPLATE_TAG_ALIASES, minimum_score=500
        )
        method = "header/context"

        if id_column is None:
            proposed = candidate.time_column + 1
            if proposed in candidate_time_columns_by_sheet.get(
                candidate.sheet_name, set()
            ):
                raise SchedulerError(
                    "Could not find an ID column beside the {} time block in sheet '{}'.".format(
                        day.label, candidate.sheet_name
                    )
                )
            id_column = proposed
            method = "adjacent-column fallback"
            warnings.append(
                "No explicit ID header was found for {}; column {} in '{}' will be used.".format(
                    day.label, get_column_letter(id_column), candidate.sheet_name
                )
            )

        if tag_column == candidate.time_column or tag_column == id_column:
            tag_column = None

        if tag_column is None:
            warnings.append(
                "No explicit Tag/Alliance header was found for {}; ID and Tag will be combined in the ID column.".format(
                    day.label
                )
            )

        slots: List[TemplateSlot] = []
        for row in sorted(candidate.rows):
            start, end, display = candidate.parsed[row]
            current_id = worksheet.cell(row=row, column=id_column).value
            locked = PRESERVE_EXISTING_ASSIGNMENTS and not value_is_open_placeholder(
                current_id
            )
            locked_player_id = (
                extract_player_id_from_schedule_cell(current_id) if locked else ""
            )
            slots.append(
                TemplateSlot(
                    day_key=day.key,
                    sheet_name=candidate.sheet_name,
                    row=row,
                    time_column=candidate.time_column,
                    id_column=id_column,
                    tag_column=tag_column,
                    start_minute=start,
                    end_minute=end,
                    display_label=display,
                    locked=locked,
                    locked_player_id=locked_player_id,
                )
            )

        # Remove duplicate start times, retaining the first row. Duplicate slots
        # would otherwise create two appointments at the same time on one day.
        deduplicated_slots: List[TemplateSlot] = []
        seen_starts: Set[int] = set()
        for slot in slots:
            if slot.start_minute in seen_starts:
                warnings.append(
                    "Duplicate {} slot {} in '{}', row {} was ignored.".format(
                        day.label, slot.display_label, slot.sheet_name, slot.row
                    )
                )
                continue
            seen_starts.add(slot.start_minute)
            deduplicated_slots.append(slot)

        if not deduplicated_slots:
            raise SchedulerError("No usable slots remained for {}.".format(day.label))

        blocks[day.key] = TemplateBlock(
            day_key=day.key,
            sheet_name=candidate.sheet_name,
            time_column=candidate.time_column,
            id_column=id_column,
            tag_column=tag_column,
            slots=deduplicated_slots,
            mapping_method=method,
        )

    return blocks, warnings


def standard_template_blocks() -> Dict[str, TemplateBlock]:
    blocks: Dict[str, TemplateBlock] = {}
    for day in DAY_SPECS:
        slots = [
            TemplateSlot(
                day_key=day.key,
                sheet_name=day.label,
                row=index + 2,
                time_column=1,
                id_column=2,
                tag_column=3,
                start_minute=start,
                end_minute=(start + SLOT_MINUTES) % 1440,
                display_label=slot_label(start),
                locked=False,
                locked_player_id="",
            )
            for index, start in enumerate(range(0, 1440, SLOT_MINUTES))
        ]
        blocks[day.key] = TemplateBlock(
            day_key=day.key,
            sheet_name=day.label,
            time_column=1,
            id_column=2,
            tag_column=3,
            slots=slots,
            mapping_method="clean-workbook fallback",
        )
    return blocks


# =============================================================================
# 11. GLOBAL CP-SAT OPTIMIZATION
# =============================================================================


def cp_status_name(status: int) -> str:
    if cp_model is None:
        return str(status)
    status_map = {
        cp_model.UNKNOWN: "UNKNOWN",
        cp_model.MODEL_INVALID: "MODEL_INVALID",
        cp_model.FEASIBLE: "FEASIBLE",
        cp_model.INFEASIBLE: "INFEASIBLE",
        cp_model.OPTIMAL: "OPTIMAL",
    }
    return status_map.get(status, str(status))


def ensure_acceptable_status(status: int, stage_name: str) -> None:
    if status == cp_model.OPTIMAL:
        return
    if status == cp_model.FEASIBLE and ACCEPT_FEASIBLE_BUT_NOT_PROVEN_OPTIMAL:
        LOGGER.warning("%s returned FEASIBLE rather than proven OPTIMAL.", stage_name)
        return
    raise SchedulerError(
        "{} did not return a proven optimal solution. Solver status: {}. "
        "Increase --time-limit or inspect the diagnostics.".format(
            stage_name, cp_status_name(status)
        )
    )


def solve_global_schedule_cpsat(
    players: Sequence[PlayerRecord],
    blocks: Mapping[str, TemplateBlock],
    *,
    max_total_per_player: int,
    time_limit_seconds: float,
) -> SolveResult:
    if not HAS_ORTOOLS:
        raise SchedulerError(
            "Google OR-Tools is not installed, so the CP-SAT solver is "
            "unavailable. Install it with 'python -m pip install ortools' or "
            "rerun with --solver network (the built-in exact solver)."
        )
    if max_total_per_player < 1 or max_total_per_player > len(DAY_SPECS):
        raise SchedulerError(
            "max_total_per_player must be between 1 and {}.".format(len(DAY_SPECS))
        )

    open_slots_by_day: Dict[str, Dict[int, TemplateSlot]] = {}
    for day in DAY_SPECS:
        open_slots_by_day[day.key] = {
            slot.start_minute: slot for slot in blocks[day.key].slots if not slot.locked
        }

    model = cp_model.CpModel()
    player_index = {player.player_id: index for index, player in enumerate(players)}
    player_lookup = {player.player_id: player for player in players}

    # Recognized existing assignments remain fixed and count toward the same
    # one-per-day and event-wide limits as newly created assignments. Existing
    # rows whose ID cannot be matched to the application file remain blocked but
    # cannot be included in the objective because their resources are unknown.
    locked_by_player_day: Dict[Tuple[str, str], TemplateSlot] = {}
    unknown_locked_slots = 0
    for day in DAY_SPECS:
        for slot in blocks[day.key].slots:
            if not slot.locked:
                continue
            if slot.locked_player_id in player_lookup:
                key = (slot.locked_player_id, day.key)
                if key in locked_by_player_day:
                    raise SchedulerError(
                        "Existing template assigns Player ID {} more than once on {}.".format(
                            slot.locked_player_id, day.label
                        )
                    )
                locked_by_player_day[key] = slot
            else:
                unknown_locked_slots += 1
    if unknown_locked_slots:
        LOGGER.warning(
            "%d existing template assignment(s) could not be matched to application IDs; "
            "their slots are preserved and blocked, but their resource values are unknown.",
            unknown_locked_slots,
        )

    # x[player, day, slot] = 1 only for legal candidate assignments.
    x: Dict[Tuple[str, str, int], cp_model.IntVar] = {}
    candidates_by_slot: Dict[Tuple[str, int], List[cp_model.IntVar]] = {}
    candidates_by_player_day: Dict[Tuple[str, str], List[cp_model.IntVar]] = {}

    for player in players:
        for day in DAY_SPECS:
            if (player.player_id, day.key) in locked_by_player_day:
                continue
            if day.key not in player.selected_days:
                continue
            legal_starts = player.available_slots[day.key].intersection(
                open_slots_by_day[day.key].keys()
            )
            if not legal_starts:
                continue
            if player.base_points[day.key] <= 0 and player.general_points <= 0:
                continue
            for start in sorted(legal_starts):
                key = (player.player_id, day.key, start)
                variable = model.NewBoolVar(
                    "x_{}_{}_{}".format(player_index[player.player_id], day.key, start)
                )
                x[key] = variable
                candidates_by_slot.setdefault((day.key, start), []).append(variable)
                candidates_by_player_day.setdefault(
                    (player.player_id, day.key), []
                ).append(variable)

    if not x and not locked_by_player_day:
        raise SchedulerError(
            "No legal player/day/time combinations were found. Check availability, "
            "selected boost days, and template time slots."
        )

    # Slot capacity: at most one person in each appointment.
    for variables in candidates_by_slot.values():
        model.Add(sum(variables) <= 1)

    # One appointment per player per boost day.
    y: Dict[Tuple[str, str], cp_model.IntVar] = {}
    for (player_id, day_key), variables in candidates_by_player_day.items():
        assignment_flag = model.NewBoolVar(
            "y_{}_{}".format(player_index[player_id], day_key)
        )
        model.Add(sum(variables) == assignment_flag)
        y[(player_id, day_key)] = assignment_flag

    for player_day in locked_by_player_day:
        player_id, day_key = player_day
        fixed_flag = model.NewBoolVar(
            "y_locked_{}_{}".format(player_index[player_id], day_key)
        )
        model.Add(fixed_flag == 1)
        y[player_day] = fixed_flag

    # Optional event-wide appointment cap.  The default 3 still permits one on
    # each of the three unique days, but never two on the same day.
    for player in players:
        flags = [
            y[(player.player_id, day.key)]
            for day in DAY_SPECS
            if (player.player_id, day.key) in y
        ]
        if flags:
            model.Add(sum(flags) <= max_total_per_player)

    # Shared General Speedups: allocate each player's finite pool across the
    # three boost days. General Speedups are deliberately modeled once for the
    # entire event, not copied into each day.
    #
    # A subtle but important edge case is a player who has only General
    # Speedups and zero day-specific resources. Without the constraints below,
    # the secondary "fill more slots" objective could assign that player on
    # several days even though only one appointment carries any resource value.
    # We therefore allow at most one general-only appointment and require that
    # appointment to carry the player's full remaining General pool.
    general_allocation: Dict[Tuple[str, str], cp_model.IntVar] = {}
    for player in players:
        if player.general_points <= 0:
            continue

        allocation_vars: List[cp_model.IntVar] = []
        general_only_flags: List[cp_model.IntVar] = []

        for day in DAY_SPECS:
            key = (player.player_id, day.key)
            if key not in y:
                continue

            allocated = model.NewIntVar(
                0,
                player.general_points,
                "general_{}_{}".format(player_index[player.player_id], day.key),
            )
            model.Add(allocated <= player.general_points * y[key])

            if player.base_points[day.key] <= 0:
                # A day with no Construction/Research/Training resources is
                # useful only when this appointment is the one consuming the
                # General pool. This prevents zero-value filler appointments.
                model.Add(allocated == player.general_points * y[key])
                general_only_flags.append(y[key])

            general_allocation[key] = allocated
            allocation_vars.append(allocated)

        if allocation_vars:
            model.Add(sum(allocation_vars) <= player.general_points)
        if general_only_flags:
            model.Add(sum(general_only_flags) <= 1)

    base_terms: List[Any] = []
    for (player_id, day_key), flag in y.items():
        base_terms.append(player_lookup[player_id].base_points[day_key] * flag)
    general_terms = list(general_allocation.values())
    total_points_expression = sum(base_terms) + sum(general_terms)
    filled_expression = sum(y.values())

    def new_solver() -> cp_model.CpSolver:
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = float(time_limit_seconds)
        solver.parameters.num_search_workers = max(1, min(8, os.cpu_count() or 1))
        solver.parameters.random_seed = 2564
        solver.parameters.log_search_progress = False
        return solver

    # Stage 1: exact maximum resource value.
    model.Maximize(total_points_expression)
    solver1 = new_solver()
    status1 = solver1.Solve(model)
    ensure_acceptable_status(status1, "Optimization stage 1 (total resources)")
    best_points = int(solver1.Value(total_points_expression))

    # Stage 2: among all resource-optimal schedules, fill as many appointments
    # as possible.  This cannot sacrifice one resource point from stage 1.
    model.Add(total_points_expression == best_points)
    model.Maximize(filled_expression)
    solver2 = new_solver()
    status2 = solver2.Solve(model)
    ensure_acceptable_status(status2, "Optimization stage 2 (filled slots)")
    best_filled = int(solver2.Value(filled_expression))

    # Stage 3: among schedules identical on resource value and fill count,
    # prefer players with fewer legal times. This protects difficult-to-place
    # high-value applicants and then makes ties deterministic by source order.
    model.Add(filled_expression == best_filled)
    tie_terms: List[Any] = []
    max_order = max((player.source_order for player in players), default=0)
    for (player_id, day_key), flag in y.items():
        player = player_lookup[player_id]
        available_count = max(1, len(player.available_slots[day_key]))
        scarcity_bonus = max(0, 48 - available_count)
        order_bonus = max_order - player.source_order
        tie_weight = scarcity_bonus * 10_000 + order_bonus
        tie_terms.append(tie_weight * flag)
    model.Maximize(sum(tie_terms))
    solver3 = new_solver()
    status3 = solver3.Solve(model)
    ensure_acceptable_status(status3, "Optimization stage 3 (tie-breaking)")

    assignments: List[Assignment] = []
    for (player_id, day_key), slot in locked_by_player_day.items():
        player = player_lookup[player_id]
        allocated_general = 0
        allocation_key = (player_id, day_key)
        if allocation_key in general_allocation:
            allocated_general = int(solver3.Value(general_allocation[allocation_key]))
        assignments.append(
            Assignment(
                day_key=day_key,
                slot_start=slot.start_minute,
                slot_label=slot.display_label,
                player_id=player_id,
                tag=player.tag,
                base_points=player.base_points[day_key],
                general_points_allocated=allocated_general,
                is_locked=True,
            )
        )

    for (player_id, day_key, start), variable in x.items():
        if solver3.Value(variable) != 1:
            continue
        player = player_lookup[player_id]
        slot = open_slots_by_day[day_key][start]
        allocated_general = 0
        allocation_key = (player_id, day_key)
        if allocation_key in general_allocation:
            allocated_general = int(solver3.Value(general_allocation[allocation_key]))
        assignments.append(
            Assignment(
                day_key=day_key,
                slot_start=start,
                slot_label=slot.display_label,
                player_id=player_id,
                tag=player.tag,
                base_points=player.base_points[day_key],
                general_points_allocated=allocated_general,
            )
        )

    assignments.sort(
        key=lambda item: (
            [day.key for day in DAY_SPECS].index(item.day_key),
            item.slot_start,
            item.player_id,
        )
    )

    result = SolveResult(
        assignments=assignments,
        total_points=sum(item.total_points for item in assignments),
        filled_slots=len(assignments),
        solver_status=cp_status_name(status3),
        stage_statuses=(
            cp_status_name(status1),
            cp_status_name(status2),
            cp_status_name(status3),
        ),
    )
    if result.total_points != best_points:
        raise SchedulerError(
            "Internal validation failed: extracted schedule value {:,} does not match "
            "the solver optimum {:,}.".format(result.total_points, best_points)
        )
    validate_solution(result, players, blocks, max_total_per_player)
    return result


def validate_solution(
    result: SolveResult,
    players: Sequence[PlayerRecord],
    blocks: Mapping[str, TemplateBlock],
    max_total_per_player: int,
) -> None:
    player_lookup = {player.player_id: player for player in players}
    seen_slots: Set[Tuple[str, int]] = set()
    seen_player_days: Set[Tuple[str, str]] = set()
    player_total_counts: Dict[str, int] = {}
    general_used: Dict[str, int] = {}

    valid_template_slots = {
        (day.key, slot.start_minute)
        for day in DAY_SPECS
        for slot in blocks[day.key].slots
    }
    locked_template_slots = {
        (day.key, slot.start_minute)
        for day in DAY_SPECS
        for slot in blocks[day.key].slots
        if slot.locked
    }

    for assignment in result.assignments:
        player = player_lookup[assignment.player_id]
        slot_key = (assignment.day_key, assignment.slot_start)
        player_day_key = (assignment.player_id, assignment.day_key)
        if slot_key in seen_slots:
            raise SchedulerError(
                "Internal validation failed: duplicate slot assignment {}.".format(
                    slot_key
                )
            )
        if player_day_key in seen_player_days:
            raise SchedulerError(
                "Internal validation failed: player {} assigned twice on {}.".format(
                    assignment.player_id, assignment.day_key
                )
            )
        if slot_key not in valid_template_slots:
            raise SchedulerError(
                "Internal validation failed: assignment is not a template slot."
            )
        if assignment.is_locked:
            if slot_key not in locked_template_slots:
                raise SchedulerError(
                    "Internal validation failed: fixed assignment is not a locked template slot."
                )
        else:
            if slot_key in locked_template_slots:
                raise SchedulerError(
                    "Internal validation failed: new assignment overwrote a locked slot."
                )
            if assignment.day_key not in player.selected_days:
                raise SchedulerError(
                    "Internal validation failed: assignment to unselected day."
                )
            if assignment.slot_start not in player.available_slots[assignment.day_key]:
                raise SchedulerError(
                    "Internal validation failed: assignment outside availability."
                )

        seen_slots.add(slot_key)
        seen_player_days.add(player_day_key)
        player_total_counts[assignment.player_id] = (
            player_total_counts.get(assignment.player_id, 0) + 1
        )
        general_used[assignment.player_id] = (
            general_used.get(assignment.player_id, 0)
            + assignment.general_points_allocated
        )

    for player_id, count in player_total_counts.items():
        if count > max_total_per_player:
            raise SchedulerError(
                "Internal validation failed: event-wide player cap exceeded."
            )
    for player_id, points in general_used.items():
        if points > player_lookup[player_id].general_points:
            raise SchedulerError(
                "Internal validation failed: General Speedups were double-counted."
            )

    calculated_points = sum(
        assignment.total_points for assignment in result.assignments
    )
    if calculated_points != result.total_points:
        raise SchedulerError("Internal validation failed: objective total mismatch.")


# =============================================================================
# 12. OUTPUT WORKBOOK
# =============================================================================


def create_clean_schedule_workbook(
    path: Path, blocks: Mapping[str, TemplateBlock]
) -> None:
    workbook = Workbook()
    default_sheet = workbook.active
    workbook.remove(default_sheet)

    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(color="FFFFFF", bold=True)

    for day in DAY_SPECS:
        worksheet = workbook.create_sheet(day.label)
        worksheet.append(["Time Slot (UTC)", "Player ID", "Alliance / Tag"])
        for cell in worksheet[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center")
        for slot in blocks[day.key].slots:
            worksheet.append([slot.display_label, "", ""])
        worksheet.freeze_panes = "A2"
        worksheet.column_dimensions["A"].width = 22
        worksheet.column_dimensions["B"].width = 18
        worksheet.column_dimensions["C"].width = 18
        for row in range(2, worksheet.max_row + 1):
            worksheet.cell(row=row, column=2).number_format = "@"
            worksheet.cell(row=row, column=3).number_format = "@"

    workbook.save(path)


def write_schedule_output(
    template_path: Path,
    output_path: Path,
    blocks: Mapping[str, TemplateBlock],
    result: SolveResult,
    *,
    using_fallback: bool,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.resolve() == template_path.resolve():
        raise SchedulerError("Output path must not overwrite the schedule template.")

    if using_fallback:
        create_clean_schedule_workbook(output_path, blocks)
    else:
        try:
            shutil.copy2(template_path, output_path)
        except Exception as exc:
            raise SchedulerError(
                "Could not copy schedule template to output: {}".format(exc)
            )

    workbook = load_workbook(output_path)
    slot_lookup: Dict[Tuple[str, int], TemplateSlot] = {}
    for day in DAY_SPECS:
        for slot in blocks[day.key].slots:
            slot_lookup[(day.key, slot.start_minute)] = slot

    for assignment in result.assignments:
        if assignment.is_locked:
            continue
        slot = slot_lookup[(assignment.day_key, assignment.slot_start)]
        worksheet = workbook[slot.sheet_name]
        id_cell = worksheet.cell(row=slot.row, column=slot.id_column)
        id_cell.number_format = "@"

        if slot.tag_column is None and COMBINE_ID_AND_TAG_IF_TAG_COLUMN_MISSING:
            value = assignment.player_id
            if assignment.tag:
                value = "{} - {}".format(assignment.player_id, assignment.tag)
            id_cell.value = value
        else:
            id_cell.value = assignment.player_id
            if slot.tag_column is not None:
                tag_cell = worksheet.cell(row=slot.row, column=slot.tag_column)
                tag_cell.number_format = "@"
                tag_cell.value = assignment.tag

    # Privacy check: the script deliberately never writes name/email columns.
    workbook.save(output_path)


def verify_output_workbook(
    output_path: Path,
    blocks: Mapping[str, TemplateBlock],
    result: SolveResult,
) -> None:
    try:
        workbook = load_workbook(output_path, data_only=False, read_only=True)
    except Exception as exc:
        raise SchedulerError(
            "Output workbook could not be reopened for verification: {}".format(exc)
        )

    assignment_lookup = {
        (assignment.day_key, assignment.slot_start): assignment
        for assignment in result.assignments
    }
    verified = 0
    for day in DAY_SPECS:
        for slot in blocks[day.key].slots:
            assignment = assignment_lookup.get((day.key, slot.start_minute))
            if not assignment:
                continue
            worksheet = workbook[slot.sheet_name]
            actual = canonical_player_id(
                worksheet.cell(row=slot.row, column=slot.id_column).value
            )
            # If ID and Tag were combined, the ID is at the beginning of the cell.
            if not actual.startswith(assignment.player_id):
                raise SchedulerError(
                    "Output verification failed at {} {}: expected Player ID {}, found '{}'.".format(
                        day.label, slot.display_label, assignment.player_id, actual
                    )
                )
            verified += 1

    if verified != result.filled_slots:
        raise SchedulerError(
            "Output verification failed: expected {} assignments, verified {}.".format(
                result.filled_slots, verified
            )
        )


# =============================================================================
# 13. DIAGNOSTICS
# =============================================================================


def mapping_lines(mapping: InputMapping) -> List[str]:
    lines = [
        "Application worksheet: {}".format(mapping.sheet_name),
        "Header row: {}".format(mapping.header_row_zero_based + 1),
        "Player/Chief ID: {}".format(mapping.id_column),
        "Alliance/Tag: {}".format(mapping.tag_column or "NOT FOUND"),
        "Timestamp: {}".format(mapping.timestamp_column or "NOT FOUND"),
        "Selected appointments: {}".format(mapping.selected_days_column or "INFERRED"),
        "General Speedups: {}".format(mapping.general_resource_column or "NOT FOUND"),
        "Fire Crystals: {}".format(mapping.fire_crystal_column or "NOT FOUND"),
        "Refined Fire Crystals: {}".format(
            mapping.refined_fire_crystal_column or "NOT FOUND"
        ),
        "Fire Crystal Shards: {}".format(
            mapping.fire_crystal_shard_column or "NOT FOUND"
        ),
    ]
    for day in DAY_SPECS:
        lines.append(
            "{} resource/score: {}".format(
                day.label, mapping.day_resource_columns[day.key]
            )
        )
        lines.append(
            "{} availability: {}".format(
                day.label, mapping.day_availability_columns[day.key]
            )
        )
    return lines


def template_lines(blocks: Mapping[str, TemplateBlock]) -> List[str]:
    lines: List[str] = []
    for day in DAY_SPECS:
        block = blocks[day.key]
        locked = sum(1 for slot in block.slots if slot.locked)
        lines.append(
            "{}: sheet='{}', time_col={}, id_col={}, tag_col={}, slots={}, locked={}, method={}".format(
                day.label,
                block.sheet_name,
                get_column_letter(block.time_column),
                get_column_letter(block.id_column),
                get_column_letter(block.tag_column) if block.tag_column else "NONE",
                len(block.slots),
                locked,
                block.mapping_method,
            )
        )
    return lines


def write_diagnostics(
    diagnostic_path: Path,
    *,
    applications_path: Path,
    template_path: Path,
    output_path: Path,
    mapping: Optional[InputMapping],
    blocks: Optional[Mapping[str, TemplateBlock]],
    players: Optional[Sequence[PlayerRecord]],
    result: Optional[SolveResult],
    warnings: Sequence[str],
    error: Optional[BaseException] = None,
) -> None:
    lines: List[str] = [
        "Whiteout Survival Scheduler Diagnostics",
        "=" * 42,
        "Applications: {}".format(applications_path),
        "Template: {}".format(template_path),
        "Output: {}".format(output_path),
        "Ambiguous numeric resource unit: {}".format(AMBIGUOUS_NUMERIC_RESOURCE_UNIT),
        "",
    ]
    if mapping:
        lines.extend(["INPUT MAPPING", "-------------"])
        lines.extend(mapping_lines(mapping))
        lines.append("")
    if blocks:
        lines.extend(["TEMPLATE MAPPING", "----------------"])
        lines.extend(template_lines(blocks))
        lines.append("")
    if players is not None:
        lines.extend(["PLAYER COUNTS", "-------------"])
        lines.append("Unique valid players: {}".format(len(players)))
        for day in DAY_SPECS:
            selected = sum(1 for player in players if day.key in player.selected_days)
            with_availability = sum(
                1
                for player in players
                if day.key in player.selected_days
                and bool(player.available_slots[day.key])
            )
            lines.append(
                "{}: selected={}, selected_with_availability={}".format(
                    day.label, selected, with_availability
                )
            )
        lines.append("")
    if result:
        lines.extend(["OPTIMIZATION RESULT", "-------------------"])
        lines.append("Stage statuses: {}".format(", ".join(result.stage_statuses)))
        lines.append("Filled slots: {}".format(result.filled_slots))
        lines.append("Total estimated points: {:,}".format(result.total_points))
        for day in DAY_SPECS:
            count = sum(1 for item in result.assignments if item.day_key == day.key)
            points = sum(
                item.total_points
                for item in result.assignments
                if item.day_key == day.key
            )
            lines.append(
                "{}: {} assignments, {:,} points".format(day.label, count, points)
            )
        lines.append("")
    if warnings:
        lines.extend(["WARNINGS", "--------"])
        lines.extend("- {}".format(warning) for warning in warnings)
        lines.append("")
    if error is not None:
        lines.extend(["ERROR", "-----", str(error), "", "TRACEBACK", "---------"])
        lines.append(traceback.format_exc())

    diagnostic_path.parent.mkdir(parents=True, exist_ok=True)
    diagnostic_path.write_text("\n".join(lines), encoding="utf-8")


# =============================================================================
# 14. COMMAND-LINE INTERFACE AND MAIN PROGRAM
# =============================================================================



# =============================================================================
# 11B. BUILT-IN EXACT NETWORK-FLOW OPTIMIZATION (no OR-Tools required)
# =============================================================================
#
# The three-day problem has a pure network structure once the shared General
# Speedup pool is written as a "first appointment unlocks the pool" bonus:
#
#     total value =  sum( base_points[p, d] * assigned[p, d] )
#                  + sum( general_points[p] * has_any_appointment[p] )
#
# The concave per-player bonus is modeled exactly with two parallel
# source->player arcs whose profits decrease (the classic convex-arc trick),
# so an integral minimum-cost / maximum-profit flow solves the very same
# integer program that CP-SAT solves.  Network matrices are totally
# unimodular, therefore the flow optimum IS the integer optimum - this is an
# exact solver, not a heuristic.  The three lexicographic stages are combined
# into one exact objective using layered integer weights; Python integers are
# arbitrary precision, so the layering cannot overflow.
#
# The graph:   SOURCE -> player -> (player, day) -> (day, slot) -> SINK
#   * SOURCE->player arc 1: capacity 1, profit = general pool  (pool bonus)
#   * SOURCE->player arc 2: capacity max_total-1, profit 0
#   * players with zero day-specific resources on a day route that day
#     through a per-player capacity-1 collector node, which enforces the
#     "at most one general-only appointment" rule from the CP-SAT model.
#   * (player, day)->(day, slot) arcs exist only for legal candidates and
#     carry profit = base_points * POINT_SCALE + FILL_WEIGHT + tie_weight.


class _MinCostFlow:
    """Successive-shortest-path min-cost flow on a DAG-built network."""

    def __init__(self, node_count: int) -> None:
        self.node_count = node_count
        self.graph: List[List[int]] = [[] for _ in range(node_count)]
        self.to: List[int] = []
        self.cap: List[int] = []
        self.cost: List[int] = []

    def add_edge(self, source: int, target: int, capacity: int, cost: int) -> int:
        edge_id = len(self.to)
        self.graph[source].append(edge_id)
        self.to.append(target)
        self.cap.append(capacity)
        self.cost.append(cost)
        self.graph[target].append(edge_id + 1)
        self.to.append(source)
        self.cap.append(0)
        self.cost.append(-cost)
        return edge_id

    def max_profit_flow(self, source: int, sink: int) -> None:
        """Augment along most-profitable paths while profit remains positive.

        The base network is a DAG, so it contains no negative cycle and the
        successive-shortest-path method is exact.  SPFA handles the negative
        residual costs.
        """
        from collections import deque

        infinity = None
        while True:
            distance: List[Optional[int]] = [infinity] * self.node_count
            in_queue = [False] * self.node_count
            previous_edge: List[int] = [-1] * self.node_count
            distance[source] = 0
            queue = deque([source])
            while queue:
                node = queue.popleft()
                in_queue[node] = False
                node_distance = distance[node]
                for edge_id in self.graph[node]:
                    if self.cap[edge_id] <= 0:
                        continue
                    candidate = node_distance + self.cost[edge_id]
                    target = self.to[edge_id]
                    if distance[target] is None or candidate < distance[target]:
                        distance[target] = candidate
                        previous_edge[target] = edge_id
                        if not in_queue[target]:
                            in_queue[target] = True
                            queue.append(target)
            if distance[sink] is None or distance[sink] >= 0:
                return
            bottleneck = None
            node = sink
            while node != source:
                edge_id = previous_edge[node]
                residual = self.cap[edge_id]
                bottleneck = residual if bottleneck is None else min(bottleneck, residual)
                node = self.to[edge_id ^ 1]
            node = sink
            while node != source:
                edge_id = previous_edge[node]
                self.cap[edge_id] -= bottleneck
                self.cap[edge_id ^ 1] += bottleneck
                node = self.to[edge_id ^ 1]

    def flow_on(self, edge_id: int) -> int:
        return self.cap[edge_id ^ 1]


def solve_global_schedule_network(
    players: Sequence[PlayerRecord],
    blocks: Mapping[str, TemplateBlock],
    *,
    max_total_per_player: int,
) -> SolveResult:
    if max_total_per_player < 1 or max_total_per_player > len(DAY_SPECS):
        raise SchedulerError(
            "max_total_per_player must be between 1 and {}.".format(len(DAY_SPECS))
        )

    open_slots_by_day: Dict[str, Dict[int, TemplateSlot]] = {}
    for day in DAY_SPECS:
        open_slots_by_day[day.key] = {
            slot.start_minute: slot for slot in blocks[day.key].slots if not slot.locked
        }

    player_lookup = {player.player_id: player for player in players}

    locked_by_player_day: Dict[Tuple[str, str], TemplateSlot] = {}
    locked_counts: Dict[str, int] = {}
    unknown_locked_slots = 0
    for day in DAY_SPECS:
        for slot in blocks[day.key].slots:
            if not slot.locked:
                continue
            if slot.locked_player_id in player_lookup:
                key = (slot.locked_player_id, day.key)
                if key in locked_by_player_day:
                    raise SchedulerError(
                        "Existing template assigns Player ID {} more than once on {}.".format(
                            slot.locked_player_id, day.label
                        )
                    )
                locked_by_player_day[key] = slot
                locked_counts[slot.locked_player_id] = (
                    locked_counts.get(slot.locked_player_id, 0) + 1
                )
            else:
                unknown_locked_slots += 1
    if unknown_locked_slots:
        LOGGER.warning(
            "%d existing template assignment(s) could not be matched to application IDs; "
            "their slots are preserved and blocked, but their resource values are unknown.",
            unknown_locked_slots,
        )

    for player_id, count in locked_counts.items():
        if count > max_total_per_player:
            raise SchedulerError(
                "The template already assigns Player ID {} to {} appointments, "
                "which exceeds the event-wide limit of {}. Raise "
                "--max-total-per-player or clear the extra template entries.".format(
                    player_id, count, max_total_per_player
                )
            )

    # Candidate edges, mirroring the CP-SAT candidate rules exactly.
    candidates: List[Tuple[str, str, int]] = []
    for player in players:
        for day in DAY_SPECS:
            if (player.player_id, day.key) in locked_by_player_day:
                continue
            if day.key not in player.selected_days:
                continue
            if player.base_points[day.key] <= 0 and player.general_points <= 0:
                continue
            legal_starts = player.available_slots[day.key].intersection(
                open_slots_by_day[day.key].keys()
            )
            for start in sorted(legal_starts):
                candidates.append((player.player_id, day.key, start))

    if not candidates and not locked_by_player_day:
        raise SchedulerError(
            "No legal player/day/time combinations were found. Check availability, "
            "selected boost days, and template time slots."
        )

    # Exact lexicographic layering, computed from the actual problem size.
    total_slot_count = sum(len(blocks[day.key].slots) for day in DAY_SPECS)
    max_order = max((player.source_order for player in players), default=0)
    tie_bound = 47 * 10_000 + max_order + 1
    fill_weight = total_slot_count * tie_bound + 1
    point_scale = total_slot_count * (fill_weight + tie_bound) + 1

    def tie_weight(player: PlayerRecord, day_key: str) -> int:
        available_count = max(1, len(player.available_slots[day_key]))
        scarcity_bonus = max(0, 48 - available_count)
        order_bonus = max_order - player.source_order
        return scarcity_bonus * 10_000 + order_bonus

    # Node numbering.
    source = 0
    sink = 1
    next_node = 2
    player_nodes: Dict[str, int] = {}
    zero_collector_nodes: Dict[str, int] = {}
    day_player_nodes: Dict[Tuple[str, str], int] = {}
    slot_nodes: Dict[Tuple[str, int], int] = {}

    involved_players = {player_id for player_id, _day, _start in candidates}
    for player_id in sorted(involved_players):
        player_nodes[player_id] = next_node
        next_node += 1
    for player_id, day_key, _start in candidates:
        key = (player_id, day_key)
        if key not in day_player_nodes:
            day_player_nodes[key] = next_node
            next_node += 1
        player = player_lookup[player_id]
        if (
            player.base_points[day_key] <= 0
            and player_id not in zero_collector_nodes
        ):
            zero_collector_nodes[player_id] = next_node
            next_node += 1
    for _player_id, day_key, start in candidates:
        key = (day_key, start)
        if key not in slot_nodes:
            slot_nodes[key] = next_node
            next_node += 1

    network = _MinCostFlow(next_node)

    for player_id, player_node in player_nodes.items():
        player = player_lookup[player_id]
        locked = locked_counts.get(player_id, 0)
        remaining = max(0, max_total_per_player - locked)
        if remaining <= 0:
            continue
        if player.general_points > 0 and locked == 0:
            network.add_edge(
                source, player_node, 1, -(player.general_points * point_scale)
            )
            if remaining > 1:
                network.add_edge(source, player_node, remaining - 1, 0)
        else:
            # Pool already unlocked by a locked appointment (or no pool).
            network.add_edge(source, player_node, remaining, 0)

    for (player_id, day_key), node in day_player_nodes.items():
        player = player_lookup[player_id]
        if player.base_points[day_key] <= 0:
            collector = zero_collector_nodes[player_id]
            network.add_edge(collector, node, 1, 0)
        else:
            network.add_edge(player_nodes[player_id], node, 1, 0)
    for player_id, collector in zero_collector_nodes.items():
        network.add_edge(player_nodes[player_id], collector, 1, 0)

    candidate_edge_ids: Dict[Tuple[str, str, int], int] = {}
    for player_id, day_key, start in candidates:
        player = player_lookup[player_id]
        profit = (
            player.base_points[day_key] * point_scale
            + fill_weight
            + tie_weight(player, day_key)
        )
        edge_id = network.add_edge(
            day_player_nodes[(player_id, day_key)],
            slot_nodes[(day_key, start)],
            1,
            -profit,
        )
        candidate_edge_ids[(player_id, day_key, start)] = edge_id

    for slot_key, node in slot_nodes.items():
        network.add_edge(node, sink, 1, 0)

    network.max_profit_flow(source, sink)

    # Extract assignments, then attach each player's General pool to exactly
    # one of their appointments for reporting: a general-only appointment if
    # one exists (matching the CP-SAT constraint that such an appointment
    # carries the whole pool), otherwise the first appointment in day order.
    chosen: List[Tuple[str, str, int]] = [
        key
        for key, edge_id in candidate_edge_ids.items()
        if network.flow_on(edge_id) == 1
    ]

    day_order = {day.key: index for index, day in enumerate(DAY_SPECS)}
    pool_target: Dict[str, Tuple[str, int]] = {}
    per_player_days: Dict[str, List[Tuple[str, int]]] = {}
    for player_id, day_key, start in chosen:
        per_player_days.setdefault(player_id, []).append((day_key, start))
    for (player_id, day_key), slot in locked_by_player_day.items():
        per_player_days.setdefault(player_id, []).append((day_key, slot.start_minute))
    for player_id, entries in per_player_days.items():
        player = player_lookup[player_id]
        if player.general_points <= 0:
            continue
        entries.sort(key=lambda item: (day_order[item[0]], item[1]))
        zero_days = [
            entry for entry in entries if player.base_points[entry[0]] <= 0
        ]
        pool_target[player_id] = zero_days[0] if zero_days else entries[0]

    assignments: List[Assignment] = []
    for (player_id, day_key), slot in locked_by_player_day.items():
        player = player_lookup[player_id]
        allocated = (
            player.general_points
            if pool_target.get(player_id) == (day_key, slot.start_minute)
            else 0
        )
        assignments.append(
            Assignment(
                day_key=day_key,
                slot_start=slot.start_minute,
                slot_label=slot.display_label,
                player_id=player_id,
                tag=player.tag,
                base_points=player.base_points[day_key],
                general_points_allocated=allocated,
                is_locked=True,
            )
        )
    for player_id, day_key, start in chosen:
        player = player_lookup[player_id]
        slot = open_slots_by_day[day_key][start]
        allocated = (
            player.general_points
            if pool_target.get(player_id) == (day_key, start)
            else 0
        )
        assignments.append(
            Assignment(
                day_key=day_key,
                slot_start=start,
                slot_label=slot.display_label,
                player_id=player_id,
                tag=player.tag,
                base_points=player.base_points[day_key],
                general_points_allocated=allocated,
            )
        )

    assignments.sort(
        key=lambda item: (
            day_order[item.day_key],
            item.slot_start,
            item.player_id,
        )
    )

    # Independent objective cross-check: the reported totals must equal the
    # value implied directly by the chosen assignment set.
    expected_total = sum(
        player_lookup[a.player_id].base_points[a.day_key] for a in assignments
    ) + sum(
        player_lookup[player_id].general_points for player_id in per_player_days
        if player_lookup[player_id].general_points > 0
    )
    result = SolveResult(
        assignments=assignments,
        total_points=sum(item.total_points for item in assignments),
        filled_slots=len(assignments),
        solver_status="OPTIMAL (exact network flow)",
        stage_statuses=("OPTIMAL", "OPTIMAL", "OPTIMAL"),
    )
    if result.total_points != expected_total:
        raise SchedulerError(
            "Internal validation failed: network-flow schedule value {:,} does "
            "not match the assignment-implied value {:,}.".format(
                result.total_points, expected_total
            )
        )
    validate_solution(result, players, blocks, max_total_per_player)
    return result


def solve_global_schedule(
    players: Sequence[PlayerRecord],
    blocks: Mapping[str, TemplateBlock],
    *,
    max_total_per_player: int,
    time_limit_seconds: float,
    solver_choice: str = "auto",
) -> SolveResult:
    if solver_choice == "cpsat" or (solver_choice == "auto" and HAS_ORTOOLS):
        LOGGER.info("Building one global three-day CP-SAT optimization model.")
        return solve_global_schedule_cpsat(
            players,
            blocks,
            max_total_per_player=max_total_per_player,
            time_limit_seconds=time_limit_seconds,
        )
    if solver_choice == "auto":
        LOGGER.info(
            "OR-Tools is not installed; using the built-in exact network-flow solver."
        )
    else:
        LOGGER.info("Using the built-in exact network-flow solver.")
    return solve_global_schedule_network(
        players,
        blocks,
        max_total_per_player=max_total_per_player,
    )


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Optimize Whiteout Survival Construction, Research, and Training buff appointments."
    )
    parser.add_argument(
        "--applications",
        type=Path,
        default=DEFAULT_APPLICATIONS_FILE,
        help="Path to the raw application XLSX workbook.",
    )
    parser.add_argument(
        "--template",
        type=Path,
        default=DEFAULT_TEMPLATE_FILE,
        help="Path to the three-day blank/partially-filled schedule XLSX workbook.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_FILE,
        help="Path for the optimized output XLSX workbook.",
    )
    parser.add_argument(
        "--max-total-per-player",
        type=int,
        default=DEFAULT_MAX_TOTAL_APPOINTMENTS_PER_PLAYER,
        help="Maximum appointments per player across all three days (default: 3).",
    )
    parser.add_argument(
        "--resource-unit",
        choices=("days", "hours", "minutes", "points"),
        default=AMBIGUOUS_NUMERIC_RESOURCE_UNIT,
        help=(
            "Meaning of plain numeric General/Construction/Research/Troop columns "
            "when their headers do not state a unit (default: days)."
        ),
    )
    parser.add_argument(
        "--solver",
        choices=("auto", "cpsat", "network"),
        default="auto",
        help=(
            "Optimizer to use. 'cpsat' requires Google OR-Tools; 'network' is "
            "the built-in exact solver with no extra installation; 'auto' "
            "prefers CP-SAT when OR-Tools is installed (default: auto)."
        ),
    )
    parser.add_argument(
        "--time-limit",
        type=float,
        default=DEFAULT_SOLVER_TIME_LIMIT_SECONDS,
        help="Per-stage CP-SAT time limit in seconds (default: 120).",
    )
    parser.add_argument(
        "--inspect",
        action="store_true",
        help="Inspect and map both workbooks, write diagnostics, and exit without scheduling.",
    )
    parser.add_argument(
        "--allow-clean-fallback",
        action="store_true",
        help=(
            "If the supplied template cannot be mapped, create a clean three-sheet "
            "48-slot workbook. This is off by default to prevent a silent layout change."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print detailed diagnostic logging.",
    )
    return parser.parse_args()


def require_file(path: Path, label: str) -> None:
    if not path.exists():
        raise SchedulerError(
            "{} file not found:\n{}\n\n"
            "Check spelling and spaces, or pass the exact path in quotes with --{}.".format(
                label,
                path,
                "applications" if label == "Applications" else "template",
            )
        )
    if path.suffix.lower() not in {".xlsx", ".xlsm"}:
        raise SchedulerError(
            "{} must be an .xlsx or .xlsm file: {}".format(label, path)
        )


def run(args: argparse.Namespace) -> Path:
    global AMBIGUOUS_NUMERIC_RESOURCE_UNIT
    AMBIGUOUS_NUMERIC_RESOURCE_UNIT = args.resource_unit

    applications_path = args.applications.expanduser().resolve()
    template_path = args.template.expanduser().resolve()
    output_path = args.output.expanduser().resolve()
    diagnostic_path = output_path.with_name(output_path.stem + " - Diagnostics.txt")

    mapping: Optional[InputMapping] = None
    blocks: Optional[Dict[str, TemplateBlock]] = None
    players: Optional[List[PlayerRecord]] = None
    result: Optional[SolveResult] = None
    warnings: List[str] = []
    using_fallback = False

    try:
        require_file(applications_path, "Applications")
        if template_path.exists():
            require_file(template_path, "Template")
        else:
            using_fallback = True
            warnings.append(
                "No schedule template was found at '{}'. A clean three-sheet "
                "48-slot schedule layout will be created instead.".format(
                    template_path
                )
            )
            LOGGER.warning(
                "Template not found; creating the clean three-sheet schedule layout."
            )
        if output_path == applications_path or output_path == template_path:
            raise SchedulerError(
                "The output path must be different from both input paths."
            )

        LOGGER.info("Reading application workbook: %s", applications_path)
        frame, sheet_name, header_row = read_application_table(applications_path)
        mapping = build_input_mapping(frame, sheet_name, header_row)
        warnings.extend(mapping.warnings)
        for line in mapping_lines(mapping):
            LOGGER.info("%s", line)

        players = build_player_records(frame, mapping)
        LOGGER.info("Validated %d unique player records.", len(players))

        if using_fallback:
            blocks = standard_template_blocks()
        else:
            LOGGER.info("Mapping three-day schedule template: %s", template_path)
            try:
                blocks, template_warnings = discover_template_blocks(template_path)
                warnings.extend(template_warnings)
            except SchedulerError as template_error:
                if not (ALLOW_CLEAN_WORKBOOK_FALLBACK or args.allow_clean_fallback):
                    raise
                using_fallback = True
                warnings.append(
                    "Template mapping failed; a clean three-sheet output will be created. Reason: {}".format(
                        template_error
                    )
                )
                LOGGER.warning("Template mapping failed: %s", template_error)
                LOGGER.warning("Using clean three-sheet schedule fallback.")
                blocks = standard_template_blocks()

        for line in template_lines(blocks):
            LOGGER.info("%s", line)

        write_diagnostics(
            diagnostic_path,
            applications_path=applications_path,
            template_path=template_path,
            output_path=output_path,
            mapping=mapping,
            blocks=blocks,
            players=players,
            result=None,
            warnings=warnings,
        )

        if args.inspect:
            LOGGER.info("Inspection complete. Diagnostics: %s", diagnostic_path)
            return diagnostic_path

        result = solve_global_schedule(
            players,
            blocks,
            max_total_per_player=args.max_total_per_player,
            time_limit_seconds=args.time_limit,
            solver_choice=args.solver,
        )
        LOGGER.info(
            "Optimal schedule: %d appointments; estimated total value: %s points.",
            result.filled_slots,
            format(result.total_points, ","),
        )

        write_schedule_output(
            template_path,
            output_path,
            blocks,
            result,
            using_fallback=using_fallback,
        )
        verify_output_workbook(output_path, blocks, result)

        write_diagnostics(
            diagnostic_path,
            applications_path=applications_path,
            template_path=template_path,
            output_path=output_path,
            mapping=mapping,
            blocks=blocks,
            players=players,
            result=result,
            warnings=warnings,
        )

        LOGGER.info("Output verified successfully: %s", output_path)
        LOGGER.info("Diagnostics: %s", diagnostic_path)
        return output_path

    except BaseException as exc:
        # KeyboardInterrupt remains visible but still receives a diagnostic file.
        try:
            write_diagnostics(
                diagnostic_path,
                applications_path=applications_path,
                template_path=template_path,
                output_path=output_path,
                mapping=mapping,
                blocks=blocks,
                players=players,
                result=result,
                warnings=warnings,
                error=exc,
            )
        except Exception:
            pass
        if isinstance(exc, KeyboardInterrupt):
            raise
        if isinstance(exc, SchedulerError):
            raise
        raise SchedulerError("Unexpected error: {}".format(exc)) from exc


def main() -> int:
    args = parse_arguments()
    configure_logging(args.verbose)
    try:
        run(args)
        return 0
    except SchedulerError as exc:
        LOGGER.critical("%s", exc)
        return 1
    except KeyboardInterrupt:
        LOGGER.warning("Interrupted by user.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
