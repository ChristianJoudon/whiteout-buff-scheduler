#!/usr/bin/env python3
"""Exact, local optimizer for declared Whiteout appointment plans.

This module does not turn inventories into incremental game benefit.  A caller
supplies mutually exclusive, feasible plans and one consistent benefit unit.
OR-Tools CP-SAT is the production engine.  The explicit exhaustive engine is a
small-instance reference implementation and never silently replaces CP-SAT.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import itertools
import json
from pathlib import Path
import sys
import time
from typing import Any, Iterable


class InputError(ValueError):
    """An input assumption is missing, inconsistent, or unsupported."""


class DependencyError(RuntimeError):
    """The explicitly selected solver is not available."""


EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
POLICIES = {
    "balanced": ("people", "benefit", "requested_days", "preferences", "history", "stability"),
    "benefit-first": ("benefit", "people", "requested_days", "preferences", "history", "stability"),
    "team-first": ("people", "requested_days", "preferences", "history", "benefit", "stability"),
}
SAFE_COEFFICIENT_SUM = 2**60


def identifier(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{where} must be a nonempty string.")
    return value.strip()


def integer(value: Any, where: str, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InputError(f"{where} must be an integer.")
    if minimum is not None and value < minimum:
        raise InputError(f"{where} must be at least {minimum}.")
    return value


def decimal_number(value: Any, where: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise InputError(f"{where} must be a finite, nonnegative number.")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise InputError(f"{where} must be a finite, nonnegative number.") from exc
    if not number.is_finite() or number < 0:
        raise InputError(f"{where} must be a finite, nonnegative number.")
    return number


def minutes_us(value: Any, where: str) -> int:
    microseconds = decimal_number(value, where) * Decimal(60_000_000)
    if microseconds != microseconds.to_integral_value():
        raise InputError(f"{where} has finer precision than one microsecond.")
    return int(microseconds)


def utc_us(value: Any, where: str) -> int:
    if not isinstance(value, str):
        raise InputError(f"{where} must be an absolute UTC ISO timestamp ending in Z or +00:00.")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InputError(f"{where} is not a valid ISO timestamp.") from exc
    if dt.tzinfo is None or dt.utcoffset() is None or dt.utcoffset().total_seconds() != 0:
        raise InputError(f"{where} must explicitly use UTC (Z or +00:00).")
    delta = dt.astimezone(timezone.utc) - EPOCH
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds


def list_of(value: Any, where: str) -> list:
    if not isinstance(value, list):
        raise InputError(f"{where} must be a list.")
    return value


def object_of(value: Any, where: str) -> dict:
    if not isinstance(value, dict):
        raise InputError(f"{where} must be an object.")
    return value


def unique_ids(values: Any, where: str) -> tuple[str, ...]:
    result = tuple(identifier(v, where) for v in list_of(values, where))
    if len(set(result)) != len(result):
        raise InputError(f"{where} contains a duplicate identifier.")
    return result


@dataclass(frozen=True)
class Slot:
    id: str
    day: str
    office: str
    start: int
    end: int
    raw_start: str
    raw_end: str


@dataclass(frozen=True)
class Plan:
    id: str
    baseline: bool
    benefit: int
    slot_ids: tuple[str, ...]
    consumption: dict[str, Decimal]
    preference: int
    raw: dict


@dataclass(frozen=True)
class Member:
    id: str
    requested_days: frozenset[str]
    plans: tuple[Plan, ...]
    previous: frozenset[str]
    history_count: int
    history_known: bool


@dataclass(frozen=True)
class Problem:
    objective_unit: str
    policy: str
    deadline_seconds: float
    slots: dict[str, Slot]
    members: tuple[Member, ...]
    locks: tuple[tuple[str, str], ...]
    blocked: frozenset[str]
    cooldown_us: int
    notes: tuple[str, ...]


def overlap(a: Slot, b: Slot) -> bool:
    """Intervals are half-open: adjacent appointments do not overlap."""
    return a.start < b.end and b.start < a.end


def office_conflict(a: Slot, b: Slot) -> bool:
    return a.office == b.office and overlap(a, b)


def member_conflict(a: Slot, b: Slot, cooldown_us: int) -> bool:
    if overlap(a, b):
        return True
    if a.office == b.office:
        return False
    earlier, later = (a, b) if a.start <= b.start else (b, a)
    return later.start - earlier.end < cooldown_us


def validate_tasks(raw: Any, completed: frozenset[str], where: str) -> None:
    """Check explicitly ordered task dependencies; no game/timer simulation."""
    available = set(completed)
    declared: set[str] = set()
    for index, raw_task in enumerate(list_of(raw, where)):
        task = object_of(raw_task, f"{where}[{index}]")
        task_id = identifier(task.get("id"), f"{where}[{index}].id")
        if task_id in declared or task_id in completed:
            raise InputError(f"{where}: task {task_id!r} is duplicated or already completed.")
        required = unique_ids(task.get("prerequisites", []), f"{where}[{index}].prerequisites")
        missing = set(required) - available
        if missing:
            raise InputError(f"{where}: task {task_id!r} has unmet prerequisites {sorted(missing)}. "
                             "Tasks must be listed in dependency order or already completed.")
        available.add(task_id)
        declared.add(task_id)


def normalize(document: dict) -> Problem:
    root = object_of(document, "input")
    unit = identifier(root.get("objective_unit"), "objective_unit")
    policy = root.get("policy", "balanced")
    if policy not in POLICIES:
        raise InputError(f"policy must be one of {list(POLICIES)}.")
    seconds = decimal_number(root.get("deadline_seconds", 30), "deadline_seconds")
    if seconds <= 0 or seconds > 86_400:
        raise InputError("deadline_seconds must be greater than 0 and at most 86400.")
    settings = object_of(root.get("settings", {}), "settings")
    minimum = minutes_us(settings.get("min_slot_minutes", 30), "settings.min_slot_minutes")
    if minimum < 30 * 60_000_000:
        raise InputError("Every slot must be at least 30 minutes; min_slot_minutes cannot be less than 30.")
    event_cap = integer(settings.get("max_event_appointments_per_player", 3),
                        "settings.max_event_appointments_per_player", 1)
    day_cap = integer(settings.get("max_day_appointments_per_player", 1),
                      "settings.max_day_appointments_per_player", 1)
    cooldown = minutes_us(settings.get("cross_position_cooldown_minutes", 0),
                          "settings.cross_position_cooldown_minutes")
    slots: dict[str, Slot] = {}
    for index, raw in enumerate(list_of(root.get("slots"), "slots")):
        item = object_of(raw, f"slots[{index}]")
        sid = identifier(item.get("id"), f"slots[{index}].id")
        if sid in slots:
            raise InputError(f"Duplicate slot id {sid!r}.")
        start = utc_us(item.get("start"), f"slot {sid}.start")
        end = utc_us(item.get("end"), f"slot {sid}.end")
        if end - start < minimum:
            raise InputError(f"Slot {sid!r} must last at least {minimum / 60_000_000:g} minutes.")
        slots[sid] = Slot(sid, identifier(item.get("day"), f"slot {sid}.day"),
                         identifier(item.get("office"), f"slot {sid}.office"),
                         start, end, item["start"], item["end"])
    if not slots:
        raise InputError("At least one slot is required.")
    blocked = frozenset(unique_ids(root.get("blocked_slot_ids", []), "blocked_slot_ids"))
    unknown = blocked - slots.keys()
    if unknown:
        raise InputError(f"Blocked slots are unknown: {sorted(unknown)}.")
    locks: list[tuple[str, str]] = []
    for index, raw in enumerate(list_of(root.get("locks", []), "locks")):
        item = object_of(raw, f"locks[{index}]")
        sid = identifier(item.get("slot_id"), f"locks[{index}].slot_id")
        pid = identifier(item.get("member_id"), f"locks[{index}].member_id")
        if sid not in slots:
            raise InputError(f"Locked slot {sid!r} is unknown.")
        if sid in blocked:
            raise InputError(f"Slot {sid!r} is both blocked and locked; resolve the conflicting instructions.")
        for blocked_id in blocked:
            if office_conflict(slots[sid], slots[blocked_id]):
                raise InputError(f"Locked slot {sid!r} conflicts with blocked office interval {blocked_id!r}; "
                                 "resolve the conflicting instructions. No lock was moved or removed.")
        if any(previous_sid == sid for previous_sid, _ in locks):
            raise InputError(f"Slot {sid!r} is locked more than once.")
        locks.append((sid, pid))
    for (sid_a, pid_a), (sid_b, pid_b) in itertools.combinations(locks, 2):
        a, b = slots[sid_a], slots[sid_b]
        if office_conflict(a, b) or (pid_a == pid_b and member_conflict(a, b, cooldown)):
            raise InputError(f"Fixed locks {sid_a!r} and {sid_b!r} conflict; no lock was moved or removed.")
    members: list[Member] = []
    seen_members: set[str] = set()
    notes: list[str] = []
    for index, raw in enumerate(list_of(root.get("members"), "members")):
        item = object_of(raw, f"members[{index}]")
        pid = identifier(item.get("id"), f"members[{index}].id")
        if pid in seen_members:
            raise InputError(f"Duplicate member id {pid!r}.")
        seen_members.add(pid)
        requested = frozenset(unique_ids(item.get("requested_days", []), f"member {pid}.requested_days"))
        own_event_cap = integer(item.get("max_event_appointments", event_cap), f"member {pid}.max_event_appointments", 0)
        own_day_cap = integer(item.get("max_day_appointments", day_cap), f"member {pid}.max_day_appointments", 0)
        budgets = {identifier(key, f"member {pid}.budgets key"): decimal_number(value, f"member {pid}.budgets.{key}")
                   for key, value in object_of(item.get("budgets", {}), f"member {pid}.budgets").items()}
        previous = frozenset(unique_ids(item.get("previous_slot_ids", []), f"member {pid}.previous_slot_ids"))
        if previous - slots.keys():
            raise InputError(f"Member {pid!r} has unknown previous_slot_ids {sorted(previous - slots.keys())}.")
        history = integer(item.get("history_count", 0), f"member {pid}.history_count", 0)
        completed = frozenset(unique_ids(item.get("completed_tasks", []), f"member {pid}.completed_tasks"))
        windows = None
        if "availability" in item:
            windows = []
            for wi, raw_window in enumerate(list_of(item["availability"], f"member {pid}.availability")):
                window = object_of(raw_window, f"member {pid}.availability[{wi}]")
                start = utc_us(window.get("start"), f"member {pid}.availability[{wi}].start")
                end = utc_us(window.get("end"), f"member {pid}.availability[{wi}].end")
                if end <= start:
                    raise InputError(f"Member {pid!r} has an empty or reversed availability range.")
                windows.append((start, end))
            # Adjacent/overlapping declarations describe one continuous window.
            merged: list[tuple[int, int]] = []
            for start, end in sorted(windows):
                if merged and start <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
                else:
                    merged.append((start, end))
            windows = merged
        else:
            notes.append(f"Member {pid}: availability was not supplied; declared plan feasibility is assumed, with no penalty.")
        own_locks = frozenset(sid for sid, owner in locks if owner == pid)
        plans: list[Plan] = []
        seen_plans: set[str] = set()
        baseline_count = 0
        for pi, raw_plan in enumerate(list_of(item.get("plans"), f"member {pid}.plans")):
            plan = object_of(raw_plan, f"member {pid}.plans[{pi}]")
            plan_id = identifier(plan.get("id"), f"member {pid}.plans[{pi}].id")
            label = f"member {pid}, plan {plan_id}"
            if plan_id in seen_plans:
                raise InputError(f"{label}: duplicate plan id.")
            seen_plans.add(plan_id)
            baseline = plan.get("baseline", False)
            if not isinstance(baseline, bool):
                raise InputError(f"{label}.baseline must be true or false.")
            benefit = integer(plan.get("incremental_benefit"), f"{label}.incremental_benefit")
            if plan.get("objective_unit", unit) != unit:
                raise InputError(f"{label}: objective units differ; convert to {unit!r} explicitly before solving.")
            appts = unique_ids(plan.get("appointments", []), f"{label}.appointments")
            if set(appts) - slots.keys():
                raise InputError(f"{label}: unknown slots {sorted(set(appts) - slots.keys())}.")
            appts = tuple(sorted(appts, key=lambda sid: (slots[sid].start, slots[sid].office, sid)))
            preference = integer(plan.get("preference_score", 0), f"{label}.preference_score", 0)
            if baseline:
                baseline_count += 1
                if appts or benefit != 0 or preference != 0:
                    raise InputError(f"{label}: the no-appointment baseline must have no appointments, zero incremental benefit and zero preference score.")
            elif not appts:
                raise InputError(f"{label}: a nonbaseline plan must request at least one appointment.")
            consumption = {identifier(key, f"{label}.consumption key"): decimal_number(value, f"{label}.consumption.{key}")
                           for key, value in object_of(plan.get("consumption", {}), f"{label}.consumption").items()}
            for resource, cost in consumption.items():
                if resource not in budgets:
                    raise InputError(f"{label}: cannot validate {resource!r} consumption because its budget is missing. "
                                     "Supply the missing planning information; no member penalty is inferred.")
                if cost > budgets[resource]:
                    raise InputError(f"{label}: declared {resource!r} consumption {cost} exceeds its budget {budgets[resource]}.")
            validate_tasks(plan.get("tasks", []), completed, f"{label}.tasks")
            if len(appts) > min(event_cap, own_event_cap):
                raise InputError(f"{label}: appointments exceed the event cap.")
            per_day: dict[str, int] = {}
            for sid in appts:
                slot = slots[sid]
                if slot.day not in requested:
                    raise InputError(f"{label}: slot {sid!r} is on an unrequested day {slot.day!r}.")
                per_day[slot.day] = per_day.get(slot.day, 0) + 1
                if per_day[slot.day] > min(day_cap, own_day_cap):
                    raise InputError(f"{label}: appointments exceed the per-day cap for {slot.day!r}.")
                if windows is not None and not any(start <= slot.start and slot.end <= end for start, end in windows):
                    raise InputError(f"{label}: slot {sid!r} is outside the supplied availability.")
            for a_id, b_id in itertools.combinations(appts, 2):
                if member_conflict(slots[a_id], slots[b_id], cooldown):
                    raise InputError(f"{label}: slots {a_id!r} and {b_id!r} overlap or violate the cross-position cooldown.")
            # Fixed assignments block intersecting intervals, even when their
            # owner has no survey/member entry. A baseline is retained in the
            # schema, but is not selectable when this member has fixed locks.
            blocked_conflict = any(office_conflict(slots[sid], slots[blocked_id])
                                   for sid in appts for blocked_id in blocked)
            other_lock_conflict = any(
                office_conflict(slots[sid], slots[lock_id])
                for sid in appts for lock_id, owner in locks if owner != pid)
            includes_own_locks = own_locks.issubset(appts)
            if not blocked_conflict and not other_lock_conflict and includes_own_locks:
                plans.append(Plan(plan_id, baseline, benefit, appts, consumption, preference, plan))
            else:
                causes = []
                if blocked_conflict:
                    causes.append("conflicts with a blocked office interval")
                if other_lock_conflict:
                    causes.append("conflicts with a fixed office appointment")
                if not includes_own_locks:
                    causes.append("does not include this member's fixed appointments")
                notes.append(f"Member {pid}, plan {plan_id}: unavailable because it {'; '.join(causes)}.")
        if baseline_count != 1:
            raise InputError(f"Member {pid!r} must declare exactly one no-appointment baseline plan; found {baseline_count}.")
        if not plans:
            raise InputError(f"Member {pid!r} has no selectable plan after preserving fixed appointments and blocked intervals.")
        members.append(Member(pid, requested, tuple(sorted(plans, key=lambda plan: plan.id)), previous, history,
                              "history_count" in item))
    if not members:
        raise InputError("At least one member is required.")
    if not all(member.history_known for member in members):
        members = [replace(member, history_count=0) for member in members]
        notes.append("History ranking is disabled for all members because at least one history_count was not supplied; unknown history is not assumed to be zero.")
    for metric in POLICIES[policy]:
        bound = sum(max(abs(plan_metrics(member, plan, slots)[metric]) for plan in member.plans) for member in members)
        if bound > SAFE_COEFFICIENT_SUM:
            raise InputError(f"Objective {metric!r} is too large for safe CP-SAT integer arithmetic; rescale its unit explicitly.")
    return Problem(unit, policy, float(seconds), slots, tuple(sorted(members, key=lambda member: member.id)),
                   tuple(locks), blocked, cooldown, tuple(notes))


def plan_metrics(member: Member, plan: Plan, slots: dict[str, Slot]) -> dict[str, int]:
    served = int(bool(plan.slot_ids))
    return {
        "benefit": plan.benefit,
        "people": served,
        "requested_days": len({slots[sid].day for sid in plan.slot_ids}),
        "preferences": plan.preference,
        "history": -member.history_count * served,
        "stability": -len(member.previous.symmetric_difference(plan.slot_ids)),
    }


def metrics(problem: Problem, choices: Iterable[Plan]) -> dict[str, int]:
    totals = dict.fromkeys(POLICIES["benefit-first"], 0)
    for member, plan in zip(problem.members, choices):
        for key, value in plan_metrics(member, plan, problem.slots).items():
            totals[key] += value
    return totals


def choices_feasible(problem: Problem, choices: tuple[Plan, ...]) -> bool:
    occupancy: list[Slot] = []
    for plan in choices:
        for sid in plan.slot_ids:
            slot = problem.slots[sid]
            if any(office_conflict(slot, old) for old in occupancy):
                return False
            occupancy.append(slot)
    return True


def result_document(problem: Problem, choices: tuple[Plan, ...] | None, status: str,
                    stages: list[dict], engine: str, started: float, **stats: Any) -> dict:
    locked_ids = {sid: pid for sid, pid in problem.locks}
    selected = []
    assignments: list[dict] = []
    if choices is not None:
        if not choices_feasible(problem, choices):
            raise RuntimeError("Independent output validation found an office conflict.")
        for member, plan in zip(problem.members, choices):
            selected.append({"member_id": member.id, "plan_id": plan.id, "baseline": plan.baseline,
                             "incremental_benefit": plan.benefit,
                             "consumption": {key: str(value) for key, value in plan.consumption.items()}})
            for sid in plan.slot_ids:
                slot = problem.slots[sid]
                assignments.append({"member_id": member.id, "plan_id": plan.id, "slot_id": sid,
                                    "day": slot.day, "office": slot.office, "start": slot.raw_start,
                                    "end": slot.raw_end, "locked": locked_ids.get(sid) == member.id,
                                    "external": False})
    known_members = {member.id for member in problem.members}
    saved_assignments = {(a["slot_id"], a["member_id"]) for a in assignments}
    for sid, pid in problem.locks:
        if (sid, pid) in saved_assignments:
            continue
        slot = problem.slots[sid]
        assignments.append({"member_id": pid, "plan_id": None, "slot_id": sid,
                            "day": slot.day, "office": slot.office, "start": slot.raw_start,
                            "end": slot.raw_end, "locked": True, "external": pid not in known_members})
    assignments.sort(key=lambda item: (problem.slots[item["slot_id"]].start, item["office"], item["member_id"]))
    if choices is not None:
        saved = {(a["slot_id"], a["member_id"]) for a in assignments}
        if not set(problem.locks).issubset(saved):
            raise RuntimeError("Independent output validation found a missing fixed appointment.")
    return {
        "schema_version": 1,
        "status": status,
        "optimal": status == "OPTIMAL",
        "engine": engine,
        "objective_unit": problem.objective_unit,
        "policy": problem.policy,
        "objective_order": list(POLICIES[problem.policy]),
        "metrics": metrics(problem, choices) if choices is not None else None,
        "stages": stages,
        "selected_plans": selected,
        "assignments": assignments,
        "notes": list(problem.notes),
        "model_scope": [
            "Benefit is a caller-declared increment over that member's no-appointment baseline, in the single declared objective unit.",
            "Resource inventories do not generate benefit or reliability penalties. Only declared plan consumption and budgets are checked.",
            "Task prerequisites are checked in declared execution order; game timers, action timing and title effects are not simulated.",
            "Availability, when supplied, means the entire appointment must fit inside its UTC windows.",
            "History is prior event service count and is disabled globally if any member lacks it; stability counts changed member-slot relationships.",
            "External fixed appointments are honored but excluded from member benefit and coverage metrics.",
        ],
        "elapsed_seconds": round(time.monotonic() - started, 6),
        **stats,
    }


def solve_exhaustive(problem: Problem, started: float, max_combinations: int) -> dict:
    size = 1
    for member in problem.members:
        size *= len(member.plans)
    if size > max_combinations:
        raise InputError(f"The explicit exhaustive engine is limited to {max_combinations:,} plan combinations; "
                         f"this input has {size:,}. Use CP-SAT for this instance.")
    deadline = started + problem.deadline_seconds
    best_choices: tuple[Plan, ...] | None = None
    best_key: tuple[int, ...] | None = None
    checked = 0
    finished = True
    for choices in itertools.product(*(member.plans for member in problem.members)):
        if time.monotonic() >= deadline:
            finished = False
            break
        checked += 1
        if not choices_feasible(problem, choices):
            continue
        values = metrics(problem, choices)
        key = tuple(values[metric] for metric in POLICIES[problem.policy])
        if best_key is None or key > best_key:
            best_choices, best_key = choices, key
    status = "OPTIMAL" if finished and best_choices is not None else (
        "INFEASIBLE" if finished else "FEASIBLE" if best_choices is not None else "UNKNOWN")
    stages = []
    values = metrics(problem, best_choices) if best_choices is not None else None
    for metric in POLICIES[problem.policy]:
        stages.append({"name": metric, "status": "OPTIMAL" if status == "OPTIMAL" else status,
                       "value": values[metric] if values is not None else None,
                       "method": "complete lexicographic enumeration" if finished else "partial enumeration; no lexicographic stage is proven"})
    return result_document(problem, best_choices, status, stages, "exhaustive", started,
                           combinations_checked=checked, combinations_total=size)


def solve_cp_sat(problem: Problem, started: float) -> dict:
    try:
        from ortools.sat.python import cp_model
    except ImportError as exc:
        raise DependencyError("CP-SAT was selected but OR-Tools is not installed in this Python environment. "
                              "Use an existing OR-Tools environment or install ortools in a dedicated environment; "
                              "this program does not install packages. --engine exhaustive is available only for small reference cases.") from exc
    model = cp_model.CpModel()
    variables = []
    for mi, member in enumerate(problem.members):
        row = [model.NewBoolVar(f"plan_{mi}_{pi}") for pi in range(len(member.plans))]
        model.AddExactlyOne(row)
        variables.append(row)
    # Interval overlap is checked across distinct slot ids; checking a slot id
    # only would incorrectly permit two intersecting office appointments.
    by_office: dict[str, list[tuple[int, int, Any]]] = {}
    for mi, member in enumerate(problem.members):
        for pi, plan in enumerate(member.plans):
            for sid in plan.slot_ids:
                slot = problem.slots[sid]
                by_office.setdefault(slot.office, []).append((slot.start, slot.end, variables[mi][pi]))
    pairs: set[tuple[int, int]] = set()
    for intervals in by_office.values():
        active: list[tuple[int, Any]] = []
        for start, end, variable in sorted(intervals, key=lambda item: (item[0], item[1])):
            active = [(other_end, other_var) for other_end, other_var in active if other_end > start]
            for _, other_var in active:
                first, second = sorted((variable.Index(), other_var.Index()))
                if first == second:
                    raise RuntimeError("An internally conflicting plan escaped normalization.")
                pair = (first, second)
                if pair not in pairs:
                    model.Add(variable + other_var <= 1)
                    pairs.add(pair)
            active.append((end, variable))
    expressions = {}
    for metric in POLICIES[problem.policy]:
        expressions[metric] = sum(
            plan_metrics(member, plan, problem.slots)[metric] * variables[mi][pi]
            for mi, member in enumerate(problem.members) for pi, plan in enumerate(member.plans))
    choices: tuple[Plan, ...] | None = None
    stages = []
    deadline = started + problem.deadline_seconds
    all_optimal = True
    final_status = "UNKNOWN"
    for si, metric in enumerate(POLICIES[problem.policy]):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            all_optimal = False
            stages.append({"name": metric, "status": "NOT_RUN_TIME_LIMIT", "value": None})
            for later in POLICIES[problem.policy][si + 1:]:
                stages.append({"name": later, "status": "NOT_RUN_TIME_LIMIT", "value": None})
            final_status = "FEASIBLE" if choices is not None else "UNKNOWN"
            break
        model.Maximize(expressions[metric])
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = remaining
        solver.parameters.num_search_workers = 1
        solver.parameters.random_seed = 0
        status = solver.Solve(model)
        name = solver.StatusName(status)
        entry = {"name": metric, "status": name, "value": None,
                 "best_bound": solver.BestObjectiveBound() if status in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None,
                 "wall_seconds": round(solver.WallTime(), 6)}
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            choices = tuple(next(plan for pi, plan in enumerate(member.plans) if solver.Value(variables[mi][pi]))
                            for mi, member in enumerate(problem.members))
            value = metrics(problem, choices)[metric]
            entry["value"] = value
            stages.append(entry)
            if status != cp_model.OPTIMAL:
                all_optimal = False
                final_status = "FEASIBLE"
                for later in POLICIES[problem.policy][si + 1:]:
                    stages.append({"name": later, "status": "NOT_RUN_UNPROVEN_PRIOR_STAGE", "value": None})
                break
            model.Add(expressions[metric] == value)
            model.ClearHints()
            for mi, member in enumerate(problem.members):
                for pi, plan in enumerate(member.plans):
                    model.AddHint(variables[mi][pi], int(plan.id == choices[mi].id))
            final_status = "OPTIMAL"
        else:
            stages.append(entry)
            all_optimal = False
            if status == cp_model.MODEL_INVALID:
                raise InputError(f"CP-SAT rejected the model: {model.Validate()}")
            final_status = "FEASIBLE" if choices is not None else name
            for later in POLICIES[problem.policy][si + 1:]:
                stages.append({"name": later, "status": "NOT_RUN_UNPROVEN_PRIOR_STAGE", "value": None})
            break
    return result_document(problem, choices, "OPTIMAL" if all_optimal else final_status,
                           stages, "cp-sat", started, office_conflict_pairs=len(pairs))


def solve(document: dict, engine: str = "cp-sat", max_combinations: int = 100_000) -> dict:
    started = time.monotonic()
    problem = normalize(document)
    if engine == "exhaustive":
        return solve_exhaustive(problem, started, max_combinations)
    if engine == "cp-sat":
        return solve_cp_sat(problem, started)
    raise InputError("engine must be 'cp-sat' or 'exhaustive'.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="-", help="JSON file; '-' reads stdin")
    parser.add_argument("--output", default="-", help="JSON file; '-' writes stdout")
    parser.add_argument("--engine", choices=("cp-sat", "exhaustive"), default="cp-sat")
    parser.add_argument("--max-combinations", type=int, default=100_000,
                        help="Hard combination cap for the explicit exhaustive reference engine")
    args = parser.parse_args(argv)
    try:
        raw = sys.stdin.read() if args.input == "-" else Path(args.input).read_text(encoding="utf-8")
        output = solve(json.loads(raw), args.engine, args.max_combinations)
        code = 0 if output["status"] in ("OPTIMAL", "FEASIBLE") else 1
    except (InputError, DependencyError, json.JSONDecodeError, OSError) as exc:
        output = {"schema_version": 1, "status": "DEPENDENCY_MISSING" if isinstance(exc, DependencyError) else "INPUT_ERROR",
                  "optimal": False, "error": str(exc)}
        code = 2
    text = json.dumps(output, indent=2, ensure_ascii=False) + "\n"
    if args.output == "-":
        sys.stdout.write(text)
    else:
        Path(args.output).write_text(text, encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
