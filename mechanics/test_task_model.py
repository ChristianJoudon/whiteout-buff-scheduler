"""Stdlib regression tests for the bounded mechanics arithmetic."""
from decimal import Decimal
from fractions import Fraction
import json
from pathlib import Path
import subprocess
import sys
import unittest

from task_model import InputError, compile_same_work_plan, evaluate, fixed_budget_training, same_work


APPOINTMENTS = [{"id": "vp1", "office": "VP", "start": "2026-10-26T08:00:00Z", "end": "2026-10-26T08:30:00Z"}]


def saved_exact(result):
    pair = result["exact_seconds"]["saved"]
    return Fraction(int(pair["numerator"]), int(pair["denominator"]))


class SameWorkTests(unittest.TestCase):
    def test_additive_title_is_percentage_points(self):
        result = same_work("46200", "100", "10")
        self.assertEqual(result["baseline_timer_seconds"], "23100")
        self.assertEqual(result["title_timer_seconds"], "22000")
        self.assertEqual(result["saved_seconds"], "1100")
        self.assertEqual(result["eligibility"]["timing_evidence"], "assumed_not_time_verified")

    def test_fifty_percent_title_is_not_half_timer(self):
        result = same_work("3000", "150", "50")
        self.assertEqual(result["baseline_timer_seconds"], "1200")
        self.assertEqual(result["title_timer_seconds"], "1000")
        self.assertEqual(saved_exact(result), 200)

    def test_fixed_reduction_order_is_required(self):
        with self.assertRaisesRegex(InputError, "explicit fixed_reduction_stage"):
            same_work(3600, 100, 100, fixed_reduction_seconds=600)

    def test_fixed_orders_are_distinct(self):
        before = same_work(3600, 100, 100, fixed_reduction_seconds=600, fixed_reduction_stage="before_speed")
        after = same_work(3600, 100, 100, fixed_reduction_seconds=600, fixed_reduction_stage="after_speed")
        self.assertEqual(saved_exact(before), 500)
        self.assertEqual(saved_exact(after), 600)

    def test_fixed_reduction_clamps_at_zero(self):
        for stage in ("before_speed", "after_speed"):
            result = same_work(10, 0, 10, fixed_reduction_seconds=20, fixed_reduction_stage=stage)
            self.assertEqual(result["baseline_timer_seconds"], "0")
            self.assertEqual(result["title_timer_seconds"], "0")

    def test_start_boundary_and_snapshot_persistence(self):
        result = same_work(46200, 100, 10, started_at="2026-10-26T08:00:00Z", office="VP", appointments=APPOINTMENTS)
        self.assertEqual(saved_exact(result), 1100)
        self.assertGreater(Decimal(result["title_timer_seconds"]), 30 * 60)
        result = same_work(46200, 100, 10, started_at="2026-10-26T08:30:00Z", office="VP", appointments=APPOINTMENTS)
        self.assertEqual(saved_exact(result), 0)

    def test_wrong_office_has_no_title_benefit(self):
        result = same_work(46200, 100, 10, started_at="2026-10-26T08:05:00Z", office="Education", appointments=APPOINTMENTS)
        self.assertFalse(result["eligibility"]["title_applies"])
        self.assertEqual(saved_exact(result), 0)

    def test_preexisting_queue_has_no_new_title_benefit(self):
        result = same_work(46200, 100, 10, action_state="preexisting", started_at="2026-10-26T08:05:00Z", office="VP", appointments=APPOINTMENTS)
        self.assertEqual(saved_exact(result), 0)

    def test_gating_context_must_be_complete_and_utc(self):
        with self.assertRaisesRegex(InputError, "together"):
            same_work(100, 0, 10, started_at="2026-10-26T08:05:00Z")
        with self.assertRaisesRegex(InputError, "UTC"):
            same_work(100, 0, 10, started_at="2026-10-26T08:05:00-10:00", office="VP", appointments=APPOINTMENTS)

    def test_midnight_absolute_utc(self):
        appts = [{"office": "VP", "start": "2026-10-26T23:45:00Z", "end": "2026-10-27T00:15:00Z"}]
        result = same_work(46200, 100, 10, started_at="2026-10-27T00:00:00+00:00", office="VP", appointments=appts)
        self.assertTrue(result["eligibility"]["title_applies"])

    def test_invalid_numbers_and_unit_mixing_rejected(self):
        for value in (True, "NaN", "Infinity", -1):
            with self.subTest(value=value), self.assertRaises(InputError):
                same_work(value, 0, 10)
        with self.assertRaises(InputError):
            same_work(100, -100, 10)
        with self.assertRaisesRegex(InputError, "game points"):
            same_work(100, 0, 10, objective_unit="svsPoints")


class FixedBudgetTests(unittest.TestCase):
    def test_supplied_time_and_additive_speeds(self):
        result = fixed_budget_training("2.5", "150", "50", 60, 30)
        self.assertEqual((result["baseline_units"], result["title_units"], result["additional_units"]), (90, 108, 18))
        self.assertIsNone(result["event_points"])

    def test_integer_floor_does_not_round_near_boundary(self):
        result = fixed_budget_training(1, 0, 0, "0.9999999999999999999999999999999999999999999999999999999", 0)
        self.assertEqual(result["baseline_units"], 0)
        result = fixed_budget_training("0.3", 0, 0, 1, 0)
        self.assertEqual(result["baseline_units"], 3)

    def test_trainable_cap_limits_both_counterfactuals(self):
        result = fixed_budget_training("2.5", 150, 50, 60, 30, trainable_units=100)
        self.assertEqual((result["baseline_units"], result["title_units"], result["additional_units"]), (90, 100, 10))

    def test_no_speedups_can_still_gain_output_from_elapsed_time(self):
        result = fixed_budget_training(2, 0, 100, 0, 10)
        self.assertEqual((result["baseline_units"], result["title_units"]), (5, 10))

    def test_preexisting_queue_and_wrong_office_have_no_gain(self):
        result = fixed_budget_training(2, 0, 100, 10, 0, action_state="preexisting")
        self.assertEqual(result["additional_units"], 0)
        result = fixed_budget_training(2, 0, 100, 10, 0, started_at="2026-10-26T08:05:00Z", office="Education", appointments=APPOINTMENTS)
        self.assertEqual(result["additional_units"], 0)

    def test_capacity_enhance_triples_count_and_time_not_throughput(self):
        plain = fixed_budget_training(2, 100, 50, 100, 20)
        enhanced = fixed_budget_training(2, 100, 50, 100, 20, batch_capacity=100, capacity_enhance_percent=200)
        effect = enhanced["capacity_effect"]
        self.assertEqual((effect["normal_batch_units"], effect["enhanced_batch_units"]), (100, 300))
        self.assertEqual(Decimal(effect["enhanced_batch_minutes"]), 3 * Decimal(effect["normal_batch_minutes"]))
        self.assertEqual(effect["throughput_multiplier"], "1")
        self.assertEqual(plain["title_units"], enhanced["title_units"])

    def test_promotion_points_use_only_supplied_delta(self):
        result = fixed_budget_training(2, 0, 100, 10, 0, action_kind="promote", target_tier_points=60, source_tier_points=45, points_unit="declaredSvsPoints")
        self.assertEqual(result["event_points"]["points_per_unit"], "15")
        self.assertEqual(result["event_points"]["additional_points"], "75")

    def test_new_training_uses_supplied_target_points(self):
        result = fixed_budget_training(2, 0, 100, 10, 0, target_tier_points="1.25", points_unit="customEventPoints")
        self.assertEqual(result["event_points"]["additional_points"], "6.25")

    def test_invalid_scoring_and_missing_capacity_rejected(self):
        with self.assertRaisesRegex(InputError, "source_tier_points"):
            fixed_budget_training(2, 0, 100, 10, 0, action_kind="promote", target_tier_points=60, points_unit="svs")
        with self.assertRaisesRegex(InputError, "exceed"):
            fixed_budget_training(2, 0, 100, 10, 0, action_kind="promote", target_tier_points=60, source_tier_points=61, points_unit="svs")
        with self.assertRaisesRegex(InputError, "batch_capacity"):
            fixed_budget_training(2, 0, 100, 10, 0, capacity_enhance_percent=200)
        with self.assertRaises(InputError):
            fixed_budget_training(0, 0, 100, 10, 0)


class PlanCompilerTests(unittest.TestCase):
    def test_general_spend_declared_once_and_exact_fractional_sum(self):
        item = {"base_work_seconds": "0.5", "existing_speed_percent": 0, "title_speed_percent": 200}
        result = compile_same_work_plan([item, item, item], general_speedup_minutes="30")
        self.assertEqual(result["incremental_benefit"], 1)
        self.assertEqual(result["consumption"], {"general_minutes": "30"})
        self.assertEqual(result["objective_unit"], "secondsSaved")

    def test_noninteger_seconds_are_not_silently_rounded(self):
        item = {"base_work_seconds": "0.5", "existing_speed_percent": 0, "title_speed_percent": 200}
        with self.assertRaisesRegex(InputError, "no rounding"):
            compile_same_work_plan([item], general_speedup_minutes=0)

    def test_mixed_modes_and_objective_units_rejected(self):
        with self.assertRaises(InputError):
            compile_same_work_plan([{"mode": "fixed-budget-training"}], general_speedup_minutes=0)
        with self.assertRaises(InputError):
            compile_same_work_plan([], general_speedup_minutes=0, objective_unit="svs")


class InterfaceTests(unittest.TestCase):
    def test_unknown_fields_and_missing_inputs_rejected(self):
        with self.assertRaisesRegex(InputError, "Unsupported fields"):
            evaluate({"mode": "same-work", "base_work_seconds": 1, "existing_speed_percent": 0, "title_speed_percent": 0, "gross_points": 100})
        with self.assertRaisesRegex(InputError, "Missing required"):
            evaluate({"mode": "same-work"})

    def test_cli_json_in_out_and_error_exit(self):
        program = Path(__file__).with_name("task_model.py")
        payload = {"mode": "same-work", "base_work_seconds": 46200, "existing_speed_percent": 100, "title_speed_percent": 10, "evidence": {"source": "synthetic test"}}
        run = subprocess.run([sys.executable, str(program)], input=json.dumps(payload), text=True, capture_output=True, check=False)
        self.assertEqual(run.returncode, 0, run.stderr)
        result = json.loads(run.stdout)
        self.assertEqual(result["saved_seconds"], "1100")
        self.assertEqual(result["input_evidence"]["source"], "synthetic test")
        run = subprocess.run([sys.executable, str(program)], input="{}", text=True, capture_output=True, check=False)
        self.assertEqual(run.returncode, 2)
        self.assertEqual(json.loads(run.stdout)["status"], "INPUT_ERROR")


if __name__ == "__main__":
    unittest.main()
