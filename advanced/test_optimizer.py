"""Semantic counterexamples and an independent small-instance oracle."""
from __future__ import annotations
import copy
from datetime import datetime
import importlib.util
import itertools
import json
from pathlib import Path
import random
import subprocess
import sys
import unittest
from unittest.mock import patch

import fixtures
import optimizer


HERE = Path(__file__).resolve().parent
HAS_ORTOOLS = importlib.util.find_spec("ortools") is not None


def independent_oracle(raw):
    """Tiny raw-JSON enumeration: no optimizer helper or normalized model used."""
    slots = {s["id"]: s for s in raw["slots"]}
    intervals = {sid: (datetime.fromisoformat(s["start"].replace("Z", "+00:00")),
                       datetime.fromisoformat(s["end"].replace("Z", "+00:00"))) for sid, s in slots.items()}
    locks = {entry["slot_id"]: entry["member_id"] for entry in raw.get("locks", [])}
    blocked = set(raw.get("blocked_slot_ids", []))
    best = None
    history_known_for_all = all("history_count" in member for member in raw["members"])
    order = {
        "balanced": ("people", "benefit", "requested_days", "preferences", "history", "stability"),
        "benefit-first": ("benefit", "people", "requested_days", "preferences", "history", "stability"),
        "team-first": ("people", "requested_days", "preferences", "history", "benefit", "stability"),
    }[raw.get("policy", "balanced")]
    for selected in itertools.product(*(m["plans"] for m in raw["members"])):
        occupied = [(sid, owner) for sid, owner in locks.items()]
        totals = dict.fromkeys(order, 0)
        legal = True
        for member, plan in zip(raw["members"], selected):
            appts = set(plan["appointments"])
            own_locks = {sid for sid, owner in locks.items() if owner == member["id"]}
            if not own_locks.issubset(appts):
                legal = False
                break
            if any(cost > member.get("budgets", {}).get(resource, -1)
                   for resource, cost in plan.get("consumption", {}).items()):
                legal = False
                break
            for sid in appts:
                a, b = intervals[sid]
                if any(slots[sid]["office"] == slots[other]["office"] and a < intervals[other][1] and intervals[other][0] < b
                       for other in blocked):
                    legal = False
                    break
                for other, owner in occupied:
                    if sid == other and owner == member["id"]:
                        continue
                    if slots[sid]["office"] == slots[other]["office"] and a < intervals[other][1] and intervals[other][0] < b:
                        legal = False
                        break
                occupied.append((sid, member["id"]))
            if not legal:
                break
            totals["benefit"] += plan["incremental_benefit"]
            totals["people"] += int(bool(appts))
            totals["requested_days"] += len({slots[sid]["day"] for sid in appts})
            totals["preferences"] += plan.get("preference_score", 0)
            if history_known_for_all:
                totals["history"] -= member["history_count"] * int(bool(appts))
            totals["stability"] -= len(appts.symmetric_difference(member.get("previous_slot_ids", [])))
        if legal:
            key = tuple(totals[name] for name in order)
            if best is None or key > best:
                best = key
    return best


class SemanticTests(unittest.TestCase):
    def setUp(self):
        self.cases = fixtures.cases()

    def run_case(self, name, engine="exhaustive"):
        result = optimizer.solve(self.cases[name], engine=engine)
        self.assertEqual(result["status"], "OPTIMAL")
        self.assertTrue(result["optimal"])
        self.assertEqual(tuple(result["metrics"][key] for key in result["objective_order"]),
                         independent_oracle(self.cases[name]))
        return result

    def test_stock_is_not_marginal_benefit(self):
        result = self.run_case("increment_over_stock")
        self.assertEqual(result["assignments"][0]["member_id"], "smaller-stock-larger-title-increment")
        self.assertEqual(result["metrics"]["benefit"], 20)

    def test_signed_delta_is_reported_under_explicit_policy(self):
        for engine in (["exhaustive", "cp-sat"] if HAS_ORTOOLS else ["exhaustive"]):
            raw = fixtures.document(members=[fixtures.member(plans=[fixtures.baseline(), fixtures.plan(benefit=-7)])])
            raw["objective_unit"] = "incremental_event_points"
            raw["policy"] = "benefit-first"
            raw["deadline_seconds"] = 60
            result = optimizer.solve(raw, engine)
            self.assertEqual(result["metrics"]["benefit"], 0)
            self.assertEqual(result["metrics"]["people"], 0)
            raw["policy"] = "balanced"
            result = optimizer.solve(raw, engine)
            self.assertEqual(result["metrics"]["benefit"], -7)
            self.assertEqual(result["metrics"]["people"], 1)
            self.assertEqual(tuple(result["metrics"][key] for key in result["objective_order"]), independent_oracle(raw))

    def test_fixed_negative_delta_is_honored(self):
        raw = fixtures.document(members=[fixtures.member(plans=[fixtures.baseline(), fixtures.plan(benefit=-7)])],
                                locks=[{"slot_id": "c1", "member_id": "alpha"}])
        raw["policy"] = "benefit-first"
        raw["deadline_seconds"] = 60
        for engine in (["exhaustive", "cp-sat"] if HAS_ORTOOLS else ["exhaustive"]):
            result = optimizer.solve(raw, engine)
            self.assertEqual(result["metrics"]["benefit"], -7)
            self.assertTrue(result["assignments"][0]["locked"])

    def test_baseline_can_spend_without_a_title(self):
        result = self.run_case("no_title_baseline")
        self.assertEqual(result["metrics"]["benefit"], 5)
        self.assertEqual(result["selected_plans"][0]["consumption"]["general_minutes"], "100")

    def test_shared_general_pool_is_budgeted_inside_whole_plan(self):
        result = self.run_case("general_pool_split")
        self.assertEqual(result["selected_plans"][0]["plan_id"], "split")
        self.assertEqual(len(result["assignments"]), 2)
        self.assertEqual(result["metrics"]["benefit"], 9)

    def test_overbudget_plan_is_explicit_input_gap(self):
        raw = self.cases["general_pool_split"]
        raw["members"][0]["plans"][-1]["consumption"]["general_minutes"] = 101
        with self.assertRaisesRegex(optimizer.InputError, "exceeds its budget"):
            optimizer.solve(raw, "exhaustive")

    def test_missing_budget_does_not_become_reliability_penalty(self):
        raw = self.cases["no_title_baseline"]
        del raw["members"][0]["budgets"]
        with self.assertRaisesRegex(optimizer.InputError, "no member penalty is inferred"):
            optimizer.solve(raw, "exhaustive")

    def test_different_slot_ids_do_not_avoid_office_collision(self):
        result = self.run_case("same_office_overlap")
        self.assertEqual(len(result["assignments"]), 1)
        self.assertEqual(result["assignments"][0]["member_id"], "bravo")

    def test_external_lock_is_kept_and_never_earns_invented_points(self):
        result = self.run_case("external_fixed_appointment")
        self.assertEqual(len(result["assignments"]), 1)
        self.assertTrue(result["assignments"][0]["external"])
        self.assertTrue(result["assignments"][0]["locked"])
        self.assertEqual(result["metrics"]["benefit"], 0)
        self.assertEqual(result["metrics"]["people"], 0)

    def test_fixed_member_plan_must_contain_lock(self):
        result = self.run_case("known_lock")
        self.assertEqual(result["selected_plans"][0]["plan_id"], "locked")
        self.assertEqual(result["metrics"]["benefit"], 1)
        self.assertTrue(result["assignments"][0]["locked"])

    def test_fractional_cooldown_crosses_utc_midnight(self):
        result = self.run_case("fractional_cooldown_midnight")
        self.assertEqual(len(result["assignments"]), 2)
        raw = self.cases["fractional_cooldown_midnight"]
        raw["slots"][1]["start"] = "2026-10-27T00:07:29.999999Z"
        with self.assertRaisesRegex(optimizer.InputError, "cooldown"):
            optimizer.solve(raw, "exhaustive")

    def test_same_player_cannot_hold_overlapping_offices(self):
        raw = self.cases["fractional_cooldown_midnight"]
        raw["settings"]["cross_position_cooldown_minutes"] = 0
        raw["slots"][1]["start"] = "2026-10-26T23:45:00Z"
        with self.assertRaisesRegex(optimizer.InputError, "overlap"):
            optimizer.solve(raw, "exhaustive")

    def test_unrequested_day_is_not_silently_added(self):
        raw = self.cases["general_pool_split"]
        raw["members"][0]["requested_days"] = ["construction-day"]
        with self.assertRaisesRegex(optimizer.InputError, "unrequested day"):
            optimizer.solve(raw, "exhaustive")

    def test_event_and_day_caps(self):
        raw = self.cases["general_pool_split"]
        raw["settings"] = {"max_event_appointments_per_player": 1}
        with self.assertRaisesRegex(optimizer.InputError, "event cap"):
            optimizer.solve(raw, "exhaustive")
        raw = self.cases["general_pool_split"]
        raw["settings"] = {}
        raw["slots"][1]["day"] = "construction-day"
        with self.assertRaisesRegex(optimizer.InputError, "per-day cap"):
            optimizer.solve(raw, "exhaustive")

    def test_availability_is_validated_when_supplied(self):
        raw = self.cases["explicit_preference"]
        raw["members"][0]["availability"] = [{"start": "2026-10-26T08:00:00Z", "end": "2026-10-26T08:15:00Z"}]
        with self.assertRaisesRegex(optimizer.InputError, "outside the supplied availability"):
            optimizer.solve(raw, "exhaustive")

    def test_missing_availability_defaults_neutrally_and_is_disclosed(self):
        result = self.run_case("explicit_preference")
        self.assertTrue(any("no penalty" in note for note in result["notes"]))
        self.assertEqual(result["assignments"][0]["slot_id"], "c2")

    def test_preferences_are_explicit_not_guessed_from_free_text(self):
        raw = self.cases["explicit_preference"]
        raw["members"][0]["comments"] = "Cannot use 08:30–09:00; contact me about this."
        # Free text is deliberately not parsed into availability or a preference.
        result = self.run_case("explicit_preference")
        self.assertEqual(result["metrics"]["preferences"], 2)

    def test_policies_expose_the_actual_team_tradeoff(self):
        result = self.run_case("policy_tradeoff")
        self.assertEqual((result["metrics"]["benefit"], result["metrics"]["people"]), (101, 1))
        self.cases["policy_tradeoff"]["policy"] = "team-first"
        result = self.run_case("policy_tradeoff")
        self.assertEqual((result["metrics"]["benefit"], result["metrics"]["people"]), (100, 2))

    def test_previous_schedule_is_softly_preserved(self):
        result = self.run_case("previous_schedule_stability")
        self.assertEqual(result["assignments"][0]["slot_id"], "c2")
        self.assertEqual(result["metrics"]["stability"], 0)

    def test_cross_event_history_tie_rotation(self):
        result = self.run_case("history_rotation")
        self.assertEqual(result["assignments"][0]["member_id"], "less-served")

    def test_missing_history_disables_comparison_for_everyone(self):
        raw = self.cases["history_rotation"]
        del raw["members"][1]["history_count"]
        result = optimizer.solve(raw, "exhaustive")
        self.assertEqual(result["metrics"]["history"], 0)
        self.assertTrue(any("History ranking is disabled for all" in note for note in result["notes"]))

    def test_task_prerequisites_are_explicit_and_ordered(self):
        self.run_case("completed_prerequisite")
        raw = self.cases["completed_prerequisite"]
        raw["members"][0]["completed_tasks"] = []
        with self.assertRaisesRegex(optimizer.InputError, "unmet prerequisites"):
            optimizer.solve(raw, "exhaustive")

    def test_mixed_benefit_units_are_rejected(self):
        raw = self.cases["increment_over_stock"]
        raw["members"][0]["plans"][1]["objective_unit"] = "gross_event_points"
        with self.assertRaisesRegex(optimizer.InputError, "objective units differ"):
            optimizer.solve(raw, "exhaustive")

    def test_no_baseline_is_not_accepted(self):
        raw = self.cases["no_title_baseline"]
        raw["members"][0]["plans"] = raw["members"][0]["plans"][1:]
        with self.assertRaisesRegex(optimizer.InputError, "exactly one no-appointment baseline"):
            optimizer.solve(raw, "exhaustive")

    def test_conflicting_locks_are_a_blocker_and_never_auto_moved(self):
        raw = self.cases["same_office_overlap"]
        raw["locks"] = [{"slot_id": "c1", "member_id": "outside-one"}, {"slot_id": "c2", "member_id": "outside-two"}]
        with self.assertRaisesRegex(optimizer.InputError, "no lock was moved or removed"):
            optimizer.solve(raw, "exhaustive")

    def test_external_lock_cannot_overlap_differently_named_blocked_interval(self):
        raw = self.cases["same_office_overlap"]
        raw["blocked_slot_ids"] = ["c1"]
        raw["locks"] = [{"slot_id": "c2", "member_id": "outside-member"}]
        with self.assertRaisesRegex(optimizer.InputError, "conflicts with blocked office interval"):
            optimizer.solve(raw, "exhaustive")

    def test_balanced_is_default_and_preserves_broad_access_then_benefit(self):
        raw = self.cases["policy_tradeoff"]
        del raw["policy"]
        result = optimizer.solve(raw, "exhaustive")
        self.assertEqual(result["policy"], "balanced")
        self.assertEqual(result["objective_order"][:2], ["people", "benefit"])
        self.assertEqual((result["metrics"]["people"], result["metrics"]["benefit"]), (2, 100))

    def test_explicit_same_day_combined_office_plan(self):
        raw = self.cases["fractional_cooldown_midnight"]
        raw["slots"][1]["day"] = "construction-day"
        raw["settings"]["max_day_appointments_per_player"] = 2
        result = optimizer.solve(raw, "exhaustive")
        self.assertEqual(len(result["assignments"]), 2)
        self.assertEqual(result["metrics"]["requested_days"], 1)

    def test_blocked_interval_filters_overlapping_slot_ids(self):
        raw = self.cases["same_office_overlap"]
        raw["blocked_slot_ids"] = ["c1"]
        result = self.run_case("same_office_overlap")
        self.assertEqual(result["assignments"], [])

    def test_minimum_slot_length_and_absolute_utc_are_required(self):
        raw = self.cases["no_title_baseline"]
        raw["slots"][0]["end"] = "2026-10-26T08:29:59Z"
        with self.assertRaisesRegex(optimizer.InputError, "at least 30"):
            optimizer.solve(raw, "exhaustive")
        raw["slots"][0]["end"] = "2026-10-26T08:30:00"
        with self.assertRaisesRegex(optimizer.InputError, "explicitly use UTC"):
            optimizer.solve(raw, "exhaustive")

    def test_exhaustive_limit_is_explicit(self):
        with self.assertRaisesRegex(optimizer.InputError, "limited to 1 plan combinations"):
            optimizer.solve(self.cases["no_title_baseline"], "exhaustive", max_combinations=1)

    def test_partial_enumeration_is_not_reported_optimal(self):
        raw = self.cases["no_title_baseline"]
        with patch.object(optimizer.time, "monotonic", side_effect=[0, 0, 100, 100]):
            result = optimizer.solve(raw, "exhaustive")
        self.assertEqual(result["status"], "FEASIBLE")
        self.assertFalse(result["optimal"])
        self.assertTrue(all(stage["status"] != "OPTIMAL" for stage in result["stages"]))

    def test_unknown_result_still_preserves_known_locks(self):
        raw = self.cases["known_lock"]
        with patch.object(optimizer.time, "monotonic", side_effect=[0, 100, 100]):
            result = optimizer.solve(raw, "exhaustive")
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertFalse(result["optimal"])
        self.assertEqual(result["assignments"][0]["slot_id"], "c1")
        self.assertTrue(result["assignments"][0]["locked"])

    def test_input_reordering_does_not_change_equal_tie_selection(self):
        raw = self.cases["same_office_overlap"]
        expected = optimizer.solve(raw, "exhaustive")["assignments"]
        raw["members"].reverse()
        raw["slots"].reverse()
        for member in raw["members"]:
            member["plans"].reverse()
        self.assertEqual(expected, optimizer.solve(raw, "exhaustive")["assignments"])

    @unittest.skipIf(HAS_ORTOOLS, "This assertion applies to environments without OR-Tools")
    def test_missing_ortools_is_not_a_silent_fallback(self):
        with self.assertRaisesRegex(optimizer.DependencyError, "does not install packages"):
            optimizer.solve(self.cases["no_title_baseline"])

    def test_json_cli_roundtrip(self):
        completed = subprocess.run([sys.executable, str(HERE / "optimizer.py"), "--engine", "exhaustive"],
                                   input=json.dumps(self.cases["no_title_baseline"]), text=True,
                                   capture_output=True, check=True)
        self.assertEqual(json.loads(completed.stdout)["metrics"]["benefit"], 5)


class ReferenceComparisonTests(unittest.TestCase):
    def random_cases(self):
        rng = random.Random(48162026)
        slots = [fixtures.slot(), fixtures.slot("c2", "2026-10-26T08:30:00Z", "2026-10-26T09:00:00Z"),
                 fixtures.slot("r1", "2026-10-27T08:00:00Z", "2026-10-27T08:30:00Z", "vice-president", "research-day"),
                 fixtures.slot("r2", "2026-10-27T08:30:00Z", "2026-10-27T09:00:00Z", "vice-president", "research-day")]
        for case in range(60):
            members = []
            for mi in range(3):
                plans = [fixtures.baseline()]
                for pi in range(3):
                    if rng.randrange(2):
                        appts = [rng.choice(["c1", "c2"]), rng.choice(["r1", "r2"])]
                    else:
                        appts = [rng.choice(["c1", "c2", "r1", "r2"])]
                    plans.append(fixtures.plan(f"plan-{pi}", rng.randrange(15), appts,
                                               preference_score=rng.randrange(3)))
                members.append(fixtures.member(f"member-{mi}", plans=plans,
                    requested_days=["construction-day", "research-day"], history_count=rng.randrange(5),
                    previous_slot_ids=[rng.choice(["c1", "c2", "r1", "r2"])]))
            yield fixtures.document(slots=slots, members=members,
                                     policy=("balanced", "team-first", "benefit-first")[case % 3])

    def test_sixty_random_raw_instances_against_independent_oracle(self):
        for raw in self.random_cases():
            result = optimizer.solve(raw, "exhaustive")
            self.assertEqual(result["status"], "OPTIMAL")
            self.assertEqual(tuple(result["metrics"][name] for name in result["objective_order"]), independent_oracle(raw))

    @unittest.skipUnless(HAS_ORTOOLS, "OR-Tools is not installed; production CP-SAT engine was not exercised")
    def test_cp_sat_stages_against_oracle_for_fixtures_and_random_cases(self):
        for raw in list(fixtures.cases().values()) + list(self.random_cases()):
            raw["deadline_seconds"] = 60
            result = optimizer.solve(raw, "cp-sat")
            self.assertEqual(result["status"], "OPTIMAL")
            self.assertTrue(all(stage["status"] == "OPTIMAL" for stage in result["stages"]))
            self.assertEqual(tuple(result["metrics"][name] for name in result["objective_order"]), independent_oracle(raw))


if __name__ == "__main__":
    unittest.main(verbosity=2)
