"""Small synthetic scenarios; none contain real player information."""
from __future__ import annotations
import copy


def slot(sid="c1", start="2026-10-26T08:00:00Z", end="2026-10-26T08:30:00Z",
         office="vice-president", day="construction-day"):
    return {"id": sid, "day": day, "office": office, "start": start, "end": end}


def plan(pid="with-title", benefit=10, appointments=None, **kwargs):
    return {"id": pid, "incremental_benefit": benefit,
            "appointments": ["c1"] if appointments is None else appointments, **kwargs}


def baseline(**kwargs):
    return plan("no-title", 0, [], baseline=True, **kwargs)


def member(pid="alpha", plans=None, **kwargs):
    return {"id": pid, "requested_days": ["construction-day"],
            "plans": [baseline(), plan()] if plans is None else plans, **kwargs}


def document(slots=None, members=None, **kwargs):
    return {"objective_unit": "saved_timer_minutes", "policy": "benefit-first", "deadline_seconds": 10,
            "slots": [slot()] if slots is None else slots,
            "members": [member()] if members is None else members, **kwargs}


def cases():
    result = {}
    result["increment_over_stock"] = document(members=[
        member("large-stock-small-title-increment", plans=[baseline(), plan(benefit=10)],
               reported_inventory={"general_minutes": 1000000}),
        member("smaller-stock-larger-title-increment", plans=[baseline(), plan(benefit=20)],
               reported_inventory={"general_minutes": 1000}),
    ])
    result["no_title_baseline"] = document(members=[member(
        budgets={"general_minutes": 100},
        plans=[baseline(consumption={"general_minutes": 100}),
               plan(benefit=5, consumption={"general_minutes": 100})])])
    shared_slots = [slot(), slot("r1", "2026-10-27T08:00:00Z", "2026-10-27T08:30:00Z",
                                "vice-president", "research-day")]
    result["general_pool_split"] = document(slots=shared_slots, members=[member(
        requested_days=["construction-day", "research-day"], budgets={"general_minutes": 100},
        plans=[baseline(), plan("construction", 4, ["c1"], consumption={"general_minutes": 60}),
               plan("research", 6, ["r1"], consumption={"general_minutes": 70}),
               plan("split", 9, ["c1", "r1"], consumption={"general_minutes": 100})])])
    # Different ids, intersecting office intervals: one may be assigned.
    overlap_slots = [slot(), slot("c2", "2026-10-26T08:15:00Z", "2026-10-26T08:45:00Z")]
    result["same_office_overlap"] = document(slots=overlap_slots, members=[
        member("alpha", plans=[baseline(), plan(benefit=10)]),
        member("bravo", plans=[baseline(), plan(benefit=20, appointments=["c2"])])])
    result["external_fixed_appointment"] = document(slots=overlap_slots, members=[
        member("alpha", plans=[baseline(), plan(benefit=999, appointments=["c2"])])],
        locks=[{"slot_id": "c1", "member_id": "external-existing-member"}])
    midnight_slots = [slot("c1", "2026-10-26T23:30:00Z", "2026-10-27T00:00:00Z"),
                      slot("r1", "2026-10-27T00:07:30Z", "2026-10-27T00:37:30Z", "education-minister", "research-day")]
    result["fractional_cooldown_midnight"] = document(slots=midnight_slots, settings={"cross_position_cooldown_minutes": 7.5},
        members=[member(requested_days=["construction-day", "research-day"],
                        plans=[baseline(), plan("both", 30, ["c1", "r1"])],
                        availability=[{"start": "2026-10-26T23:00:00Z", "end": "2026-10-27T01:00:00Z"}])])
    pref_slots = [slot(), slot("c2", "2026-10-26T08:30:00Z", "2026-10-26T09:00:00Z")]
    result["explicit_preference"] = document(slots=pref_slots, members=[member(plans=[
        baseline(), plan("earlier", 10, ["c1"], preference_score=0),
        plan("preferred", 10, ["c2"], preference_score=2)])])
    result["known_lock"] = document(slots=pref_slots, members=[member(plans=[
        baseline(), plan("locked", 1, ["c1"]), plan("otherwise-better", 100, ["c2"])])],
        locks=[{"slot_id": "c1", "member_id": "alpha"}])
    result["policy_tradeoff"] = document(slots=shared_slots, members=[
        member("alpha", requested_days=["construction-day", "research-day"],
               plans=[baseline(), plan("both", 101, ["c1", "r1"]), plan("construction", 50, ["c1"])]),
        member("bravo", requested_days=["research-day"], plans=[baseline(), plan("research", 50, ["r1"])])])
    result["previous_schedule_stability"] = document(slots=pref_slots, members=[member(
        previous_slot_ids=["c2"], plans=[baseline(), plan("first", 10, ["c1"]), plan("second", 10, ["c2"])])])
    result["history_rotation"] = document(members=[
        member("long-served", history_count=5), member("less-served", history_count=0)])
    result["completed_prerequisite"] = document(members=[member(
        completed_tasks=["embassy-ready"], plans=[baseline(), plan(tasks=[
            {"id": "furnace-upgrade", "prerequisites": ["embassy-ready"]},
            {"id": "next-building", "prerequisites": ["furnace-upgrade"]}])])])
    return copy.deepcopy(result)
