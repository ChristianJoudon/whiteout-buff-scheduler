# Supplied-task mechanics calculator

This local, standard-library calculator evaluates supplied work and speed statistics. It does not infer title strengths, state skills, pet effects, resources, tiers, event rules or feasible task chains. Synthetic samples are not current game defaults.

Run either JSON mode:

```sh
python3 task_model.py --input sample-same-work.json
python3 task_model.py --input sample-training.json
python3 -m unittest discover -s . -p 'test_*.py' -v
```

The CLI also accepts JSON on stdin and `--output result.json`. Invalid input returns `INPUT_ERROR` and exit code 2. Calculated results include input provenance, assumptions, units and arithmetic limits.

`same-work` compares the same fixed amount of work using `T = base_work_seconds / (1 + speed_percent / 100)`. Speeds add as percentage points. A nonzero fixed time reduction requires an explicit `before_speed` or `after_speed` stage; no Agnes order is assumed. Inputs must describe the same work in both counterfactuals. The output is saved timer seconds, never inferred SvS points or additional spending.

`fixed-budget-training` calculates the integer floor of `(1 + combined_speed_percent / 100) × (allocated_speedup_minutes + elapsed_minutes) / base_minutes_per_troop`. An optional `trainable_units` cap constrains both counterfactuals. For promotion, supply the raw per-unit promotion time and available source troops. Optional scoring requires an explicit event-points unit and caller-supplied target points; promotion also requires source points and scores their difference. Time savings, troop output and event points remain separate quantities.

Capacity Enhance is a separate batch-size calculation. Supplying `batch_capacity` and `capacity_enhance_percent: 200` triples batch units and batch time, leaving per-troop throughput unchanged. Capacity is not a multiplier on fixed-budget output. No three-camp multiplier or repeated use of a shared general-speedup pool is inferred.

Set `action_state` to `preexisting` for an already-running queue; a newly acquired title produces no incremental benefit. To check a new start, supply all three of `started_at`, `office` and `appointments`. Matching intervals are half-open absolute UTC ranges: start is included, end is excluded. Office identifiers must match exactly. Omitting timing context explicitly assumes the new action starts under the requested title; the output labels this assumption. Passing timing checks does not prove an in-game reservation exists or validate seat rotation/cooldowns.

Decimal inputs are converted into exact fractions for arithmetic and flooring. Decimal views have 50 significant digits, and same-work results also expose exact rational seconds. Repeating decimal displays are not exact measurements.

The optional Python helper `compile_same_work_plan(work_items, general_speedup_minutes=...)` returns an advanced-plan fragment with objective unit `secondsSaved`. It sums exact same-work savings and rejects a noninteger aggregate instead of silently rounding. The whole-plan general-speedup allocation is required once, independent of work-item count. Caller supplies actual appointment IDs, mutually exclusive plans, provenance and feasible resource consumption; this helper does not derive consumption from timers or invent a reinvestment plan.

The training formula is a bounded allocation scenario. It does not simulate item denominations, camp residuals, requeue/tapping limits, changing buffs on later starts, prerequisites, resource costs or collection windows. Optional event points are conditional on collecting within the applicable scoring window. Whole-plan simulations or guaranteed state-point forecasts require additional validated inputs and constraints.
