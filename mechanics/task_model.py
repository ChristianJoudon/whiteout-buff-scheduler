#!/usr/bin/env python3
"""Bounded Whiteout task arithmetic from supplied stats, not a game simulator.

Decimal inputs are converted to exact rational values for arithmetic, integer
floors and plan compilation. Decimal views use 50 significant digits; repeating
results also carry exact numerator/denominator evidence. No game rates, title
strengths, resource conversions, personal buffs or event point tables are built
in. All appointment timestamps, when supplied, must be absolute UTC.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction
import json
from pathlib import Path
import sys
from typing import Any


class InputError(ValueError):
    """Required input is absent, ambiguous, incompatible or invalid."""


PRECISION = 50
COMMON_FIELDS = {
    "action_state", "started_at", "office", "appointments", "evidence",
    "assumptions", "task_id", "objective_unit",
}
SAME_WORK_FIELDS = COMMON_FIELDS | {
    "mode", "base_work_seconds", "existing_speed_percent", "title_speed_percent",
    "fixed_reduction_seconds", "fixed_reduction_stage",
}
TRAINING_FIELDS = COMMON_FIELDS | {
    "mode", "base_minutes_per_troop", "existing_speed_percent", "title_speed_percent",
    "speedup_minutes", "elapsed_minutes", "trainable_units", "action_kind",
    "target_tier_points", "source_tier_points", "points_unit", "batch_capacity",
    "capacity_enhance_percent",
}


def number(value: Any, where: str, *, nonnegative: bool = True) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise InputError(f"{where} must be a finite number, preferably a decimal string.")
    try:
        result = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise InputError(f"{where} must be a finite number.") from exc
    if not result.is_finite() or (nonnegative and result < 0):
        raise InputError(f"{where} must be finite" + (" and nonnegative." if nonnegative else "."))
    return result


def whole(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InputError(f"{where} must be a nonnegative integer.")
    return value


def identifier(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{where} must be a nonempty string.")
    return value.strip()


def decimal_text(value: Fraction | Decimal | int) -> str:
    if not isinstance(value, Fraction):
        value = Fraction(value)
    with localcontext() as context:
        context.prec = PRECISION
        result = Decimal(value.numerator) / Decimal(value.denominator)
        return str(result)


def exact(value: Fraction) -> dict[str, str]:
    return {"numerator": str(value.numerator), "denominator": str(value.denominator)}


def timestamp(value: Any, where: str) -> datetime:
    if not isinstance(value, str):
        raise InputError(f"{where} must be an absolute UTC ISO timestamp.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InputError(f"{where} must be a valid absolute UTC ISO timestamp.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise InputError(f"{where} must explicitly use UTC, ending in Z or +00:00.")
    return parsed


def eligibility(*, action_state: str = "new", started_at: Any = None,
                office: Any = None, appointments: Any = None) -> dict:
    """Snapshot eligibility at the start; this does not book or validate a seat."""
    if action_state not in ("new", "preexisting"):
        raise InputError("action_state must be 'new' or 'preexisting'.")
    supplied = (started_at is not None, office is not None, appointments is not None)
    matches: list[str] = []
    if any(supplied):
        if not all(supplied):
            raise InputError("Timing checks require started_at, office and appointments together.")
        start = timestamp(started_at, "started_at")
        office = identifier(office, "office")
        if not isinstance(appointments, list):
            raise InputError("appointments must be a list.")
        for index, item in enumerate(appointments):
            if not isinstance(item, dict):
                raise InputError(f"appointments[{index}] must be an object.")
            appt_office = identifier(item.get("office"), f"appointments[{index}].office")
            begin = timestamp(item.get("start"), f"appointments[{index}].start")
            end = timestamp(item.get("end"), f"appointments[{index}].end")
            if end <= begin:
                raise InputError(f"appointments[{index}] has an empty or reversed interval.")
            if appt_office == office and begin <= start < end:
                matches.append(str(item.get("id", index)))
        timing = "checked_supplied_utc_intervals"
        active = bool(matches)
        reason = "New action starts inside a matching office interval." if active else "No matching office appointment covers the action start."
    else:
        timing = "assumed_not_time_verified"
        active = True
        reason = "No timing context supplied: assume the new action starts while the requested title is active."
    if action_state == "preexisting":
        active = False
        reason = "Preexisting queues receive no newly acquired title benefit in this start-snapshot model."
    return {"title_applies": active, "action_state": action_state,
            "timing_evidence": timing, "matching_appointment_ids": matches, "reason": reason}


def _factors(existing_speed_percent: Any, title_speed_percent: Any, applies: bool) -> tuple[Fraction, Fraction, Decimal, Decimal]:
    existing = number(existing_speed_percent, "existing_speed_percent", nonnegative=False)
    title = number(title_speed_percent, "title_speed_percent")
    base_factor = Fraction(1) + Fraction(existing) / 100
    title_factor = base_factor + (Fraction(title) / 100 if applies else 0)
    if base_factor <= 0 or title_factor <= 0:
        raise InputError("The supplied combined speed must produce a strictly positive speed factor.")
    return base_factor, title_factor, existing, title


def _metadata(evidence: Any = None, assumptions: Any = None, task_id: Any = None) -> dict:
    if evidence is not None and not isinstance(evidence, dict):
        raise InputError("evidence must be an object containing caller-supplied provenance.")
    if assumptions is None:
        assumptions = []
    if not isinstance(assumptions, list) or any(not isinstance(item, str) for item in assumptions):
        raise InputError("assumptions must be a list of strings.")
    if task_id is not None:
        task_id = identifier(task_id, "task_id")
    return {"task_id": task_id, "input_evidence": evidence or {},
            "evidence_status": "Caller-supplied inputs and provenance; not independently verified in the game.",
            "caller_assumptions": assumptions,
            "arithmetic": {"decimal_significant_digits": PRECISION,
                           "rounding": "Decimal views only; integer floors and plan compilation use exact rational values."}}


def same_work(base_work_seconds: Any, existing_speed_percent: Any, title_speed_percent: Any, *,
              fixed_reduction_seconds: Any = 0, fixed_reduction_stage: Any = None,
              action_state: str = "new", started_at: Any = None, office: Any = None,
              appointments: Any = None, evidence: Any = None, assumptions: Any = None,
              task_id: Any = None, objective_unit: str = "secondsSaved") -> dict:
    """Compare exactly the same fixed work under the supplied additive speeds.

    A nonzero fixed reduction requires an explicit before_speed/after_speed
    stage. This function never chooses an Agnes or other game-effect order.
    """
    if objective_unit != "secondsSaved":
        raise InputError("same-work produces only secondsSaved; it cannot be labeled as game points.")
    work = Fraction(number(base_work_seconds, "base_work_seconds"))
    reduction = Fraction(number(fixed_reduction_seconds, "fixed_reduction_seconds"))
    if fixed_reduction_stage not in (None, "before_speed", "after_speed"):
        raise InputError("fixed_reduction_stage must be before_speed or after_speed.")
    if reduction and fixed_reduction_stage is None:
        raise InputError("A nonzero fixed reduction requires explicit fixed_reduction_stage; effect order is not inferred.")
    gate = eligibility(action_state=action_state, started_at=started_at, office=office, appointments=appointments)
    base_factor, title_factor, existing, title = _factors(existing_speed_percent, title_speed_percent, gate["title_applies"])

    def timer(factor: Fraction) -> Fraction:
        adjusted_work = max(Fraction(0), work - reduction) if fixed_reduction_stage == "before_speed" else work
        duration = adjusted_work / factor
        return max(Fraction(0), duration - reduction) if fixed_reduction_stage == "after_speed" else duration

    baseline, titled = timer(base_factor), timer(title_factor)
    saved = baseline - titled
    result = {"schema_version": 1, "mode": "same-work", "objective_unit": "secondsSaved",
              "baseline_timer_seconds": decimal_text(baseline), "title_timer_seconds": decimal_text(titled),
              "saved_seconds": decimal_text(saved),
              "exact_seconds": {"baseline": exact(baseline), "title": exact(titled), "saved": exact(saved)},
              "eligibility": gate,
              "inputs": {"base_work_seconds": decimal_text(work), "existing_speed_percent": str(existing),
                         "requested_title_speed_percent": str(title),
                         "applied_title_speed_percent": str(title) if gate["title_applies"] else "0",
                         "fixed_reduction_seconds": decimal_text(reduction), "fixed_reduction_stage": fixed_reduction_stage},
              "model_assumptions": [
                  "Compare the same quantity of new work; existing_speed_percent excludes the requested title and already combines every other supplied additive speed bonus.",
                  "Title speed is snapshotted at action start. The title need not remain active for the entire resulting timer.",
                  "Any fixed reduction is applied to both counterfactuals using only the caller's explicit effect order, clamped at zero.",
                  "For preexisting queues, both outputs are the same supplied-work reference timer; the remaining live queue time is not inferred.",
                  "No multiplicative buffs, alliance helps, spending/reinvestment, prerequisites or resource feasibility are simulated.",
              ]}
    result.update(_metadata(evidence, assumptions, task_id))
    return result


def fixed_budget_training(base_minutes_per_troop: Any, existing_speed_percent: Any,
                          title_speed_percent: Any, speedup_minutes: Any, elapsed_minutes: Any, *,
                          trainable_units: Any = None, action_kind: str = "train",
                          target_tier_points: Any = None, source_tier_points: Any = None,
                          points_unit: Any = None, batch_capacity: Any = None,
                          capacity_enhance_percent: Any = 0, action_state: str = "new",
                          started_at: Any = None, office: Any = None, appointments: Any = None,
                          evidence: Any = None, assumptions: Any = None, task_id: Any = None,
                          objective_unit: str = "troops") -> dict:
    """Integer output for one supplied training/promotion stream and time budget.

    This is a bounded arithmetic scenario, not a prediction that every unit is
    collected during a game event or a thirty-minute appointment.
    """
    if objective_unit != "troops":
        raise InputError("fixed-budget-training uses troops as its output unit; optional event points stay separate.")
    base = Fraction(number(base_minutes_per_troop, "base_minutes_per_troop"))
    if base <= 0:
        raise InputError("base_minutes_per_troop must be greater than zero.")
    speedups = Fraction(number(speedup_minutes, "speedup_minutes"))
    elapsed = Fraction(number(elapsed_minutes, "elapsed_minutes"))
    cap = None if trainable_units is None else whole(trainable_units, "trainable_units")
    if action_kind not in ("train", "promote"):
        raise InputError("action_kind must be train or promote.")
    gate = eligibility(action_state=action_state, started_at=started_at, office=office, appointments=appointments)
    base_factor, title_factor, existing, title = _factors(existing_speed_percent, title_speed_percent, gate["title_applies"])

    def quantity(factor: Fraction) -> int:
        possible = ((speedups + elapsed) * factor) // base
        return int(min(possible, cap)) if cap is not None else int(possible)

    baseline, titled = quantity(base_factor), quantity(title_factor)
    points = None
    if target_tier_points is None:
        if source_tier_points is not None or points_unit is not None:
            raise InputError("Event scoring requires target_tier_points and an explicit points_unit.")
    else:
        unit = identifier(points_unit, "points_unit")
        if unit in ("secondsSaved", "troops", "seconds", "minutes"):
            raise InputError("points_unit must identify an event-points unit, not time or troop count.")
        target = Fraction(number(target_tier_points, "target_tier_points"))
        if action_kind == "promote":
            if source_tier_points is None:
                raise InputError("Promotion scoring requires caller-supplied source_tier_points.")
            source = Fraction(number(source_tier_points, "source_tier_points"))
            if source > target:
                raise InputError("source_tier_points cannot exceed target_tier_points.")
        else:
            source = Fraction(0)
            if source_tier_points is not None and number(source_tier_points, "source_tier_points") != 0:
                raise InputError("New training has no source-tier points; use action_kind promote for a delta.")
        per_unit = target - source
        points = {"unit": unit, "points_per_unit": decimal_text(per_unit),
                  "baseline_points": decimal_text(baseline * per_unit),
                  "title_points": decimal_text(titled * per_unit),
                  "additional_points": decimal_text((titled - baseline) * per_unit),
                  "condition": "These points require collection inside the caller's applicable scoring window; collection timing is not simulated."}
    enhance = Fraction(number(capacity_enhance_percent, "capacity_enhance_percent"))
    capacity = None
    if batch_capacity is None:
        if enhance:
            raise InputError("capacity_enhance_percent requires an explicit batch_capacity.")
    else:
        count = whole(batch_capacity, "batch_capacity")
        enhanced = int(Fraction(count) * (1 + enhance / 100))
        normal_time = Fraction(count) * base / title_factor
        enhanced_time = Fraction(enhanced) * base / title_factor
        capacity = {"normal_batch_units": count, "enhanced_batch_units": enhanced,
                    "capacity_enhance_percent": decimal_text(enhance),
                    "normal_batch_minutes": decimal_text(normal_time),
                    "enhanced_batch_minutes": decimal_text(enhanced_time),
                    "throughput_multiplier": "1",
                    "note": "Capacity changes batch count and its time together. +200% triples both; it does not triple speed or the fixed-budget output above."}
    result = {"schema_version": 1, "mode": "fixed-budget-training", "objective_unit": "troops",
              "baseline_units": baseline, "title_units": titled, "additional_units": titled - baseline,
              "event_points": points, "capacity_effect": capacity, "eligibility": gate,
              "inputs": {"base_minutes_per_troop": decimal_text(base),
                         "existing_speed_percent": str(existing), "requested_title_speed_percent": str(title),
                         "applied_title_speed_percent": str(title) if gate["title_applies"] else "0",
                         "speedup_minutes": decimal_text(speedups), "elapsed_minutes": decimal_text(elapsed),
                         "trainable_units": cap, "action_kind": action_kind},
              "model_assumptions": [
                  "Output is floor((1 + (existing_speed_percent + applied_title_speed_percent)/100) * (speedup_minutes + elapsed_minutes) / base_minutes_per_troop), capped by trainable_units when supplied.",
                  "base_minutes_per_troop is raw time before the supplied speeds. For promotion, supply the raw promotion time, not the full target-tier training time.",
                  "The allocated time budget belongs to this one action stream. No three-camp multiplier or repeated use of a shared general-speedup pool is inferred.",
                  "The cap must already represent available troops/resources/backlog if those constrain the plan; omitted caps assume sufficient trainable units.",
                  "This estimate assumes the time budget can be used on actions with this speed stack. Batch initiation, tapping limits, appointment expiry for later starts, integer speedup items, preexisting residuals and collection deadlines are not simulated.",
                  "Capacity Enhance changes queue size and duration together, not per-troop throughput. It is not applied as a speed multiplier.",
                  "Optional event scoring uses only caller-supplied target/source points; no event table or conversion from saved timer seconds is inferred.",
              ]}
    result.update(_metadata(evidence, assumptions, task_id))
    return result


def evaluate(document: Any) -> dict:
    if not isinstance(document, dict):
        raise InputError("Input must be a JSON object.")
    mode = document.get("mode")
    if mode not in ("same-work", "fixed-budget-training"):
        raise InputError("mode must be same-work or fixed-budget-training.")
    allowed = SAME_WORK_FIELDS if mode == "same-work" else TRAINING_FIELDS
    unknown = set(document) - allowed
    if unknown:
        raise InputError(f"Unsupported fields for {mode}: {sorted(unknown)}.")
    required = {"base_work_seconds", "existing_speed_percent", "title_speed_percent"} if mode == "same-work" else {
        "base_minutes_per_troop", "existing_speed_percent", "title_speed_percent", "speedup_minutes", "elapsed_minutes"}
    missing = required - document.keys()
    if missing:
        raise InputError(f"Missing required fields: {sorted(missing)}.")
    arguments = {key: value for key, value in document.items() if key != "mode"}
    return same_work(**arguments) if mode == "same-work" else fixed_budget_training(**arguments)


def compile_same_work_plan(work_items: Any, *, general_speedup_minutes: Any,
                           objective_unit: str = "secondsSaved") -> dict:
    """Compile an exact, whole-second increment for an advanced plan fragment.

    General speedup consumption is supplied ONCE for the entire plan. Its
    allocation/feasibility is not derived or asserted by this arithmetic helper.
    """
    if objective_unit != "secondsSaved":
        raise InputError("The same-work compiler supports only exact secondsSaved, never mixed units or event points.")
    general = number(general_speedup_minutes, "general_speedup_minutes")
    if not isinstance(work_items, list) or not work_items:
        raise InputError("work_items must be a nonempty list of same-work input objects.")
    results = []
    total = Fraction(0)
    for index, item in enumerate(work_items):
        if not isinstance(item, dict) or item.get("mode", "same-work") != "same-work":
            raise InputError(f"work_items[{index}] must contain same-work inputs only.")
        result = evaluate({"mode": "same-work", **item})
        results.append(result)
        saved = result["exact_seconds"]["saved"]
        total += Fraction(int(saved["numerator"]), int(saved["denominator"]))
    if total.denominator != 1:
        raise InputError("The exact aggregate saved time is not a whole number of seconds; no rounding into secondsSaved is performed.")
    return {"objective_unit": "secondsSaved", "incremental_benefit": total.numerator,
            "consumption": {"general_minutes": str(general)}, "work_items": results,
            "assumptions": [
                "This is an advanced-plan fragment; caller supplies appointments, task provenance and the feasible whole-plan resource allocation.",
                "general_minutes is declared once for the whole plan, not multiplied by work-item count. No reinvestment of saved time or duplicate baseline spending is inferred.",
                "The advanced optimizer checks the declared plan consumption against its budgets; this helper does not derive spending from timers.",
            ]}


def _json_default(value: Any) -> str:
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="-", help="JSON file, or - for stdin")
    parser.add_argument("--output", default="-", help="JSON file, or - for stdout")
    args = parser.parse_args(argv)
    code = 0
    try:
        raw = sys.stdin.read() if args.input == "-" else Path(args.input).read_text(encoding="utf-8")
        document = json.loads(raw, parse_float=Decimal)
        result = {"status": "CALCULATED", **evaluate(document)}
    except (InputError, json.JSONDecodeError, OSError) as exc:
        result = {"schema_version": 1, "status": "INPUT_ERROR", "error": str(exc)}
        code = 2
    output = json.dumps(result, indent=2, default=_json_default, ensure_ascii=False) + "\n"
    try:
        if args.output == "-":
            sys.stdout.write(output)
        else:
            Path(args.output).write_text(output, encoding="utf-8")
    except OSError as exc:
        sys.stderr.write(f"Cannot write output: {exc}\n")
        return 2
    return code


if __name__ == "__main__":
    raise SystemExit(main())
