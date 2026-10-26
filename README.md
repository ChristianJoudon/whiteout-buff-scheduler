# Whiteout Buff Scheduler v4

Create a reviewable appointment draft from a Whiteout Survival survey. Version 4 prioritizes broad first access, then the additional benefit a title creates. Resource inventory is context. It is not automatically converted into a claim about the gain from an appointment.

[Launch the scheduler](https://christianjoudon.github.io/whiteout-buff-scheduler/Whiteout%20Buff%20Scheduler.html) · [Illustrated walkthrough](https://christianjoudon.github.io/whiteout-buff-scheduler/walkthrough.html) · [Project case study](https://christianjoudon.github.io/whiteout-buff-scheduler/)

Edition: 26 October 2026. The existing project, app, walkthrough, explainer and PDF addresses remain in use.

## The easy way (no Python, no terminal)

Read the [illustrated guide](ALGORITHM.md) or follow its [website edition](https://christianjoudon.github.io/whiteout-buff-scheduler/walkthrough.html). Set Options, choose the survey, optionally import accepted appointments, review the results and comments, inspect the three days, and download the workbook. Choosing a file schedules it automatically. Options changes recalculate it and reset comment review.

Try [sample-survey.csv](sample-survey.csv) and [sample-accepted.csv](sample-accepted.csv). They contain invented members only. The guide uses 08:00 to 10:00 UTC; the app initially offers a full UTC day. Each seat lasts 30 minutes. The default event cap permits up to one appointment on each of the three days.

Accepted appointments stay fixed, including members absent from the survey. Conflicting commitments stop the calculation. A generated or reviewed draft is not an appointment booked in the game.

## What the optimizer guarantees

The browser solves its additive three day appointment model exactly, under the selected objective order and parsed constraints. Available times, requested days, seat occupancy and event caps remain hard rules. Broad first access, team access and benefit first are different policies; the guide explains their order and tradeoffs.

Benefit estimates are additional whole minutes saved against the same plan without the title. Leave unknown estimates blank. If eligible estimates are incomplete, benefit ranking is disabled for everyone; selecting benefit first instead produces a concrete blocker. Incomplete history disables rotation for everyone. Free text comments never become automatic preferences or invented gains.

The browser does not allocate coupled resources across tasks, simulate the whole game or guarantee a real event score. Use the separate advanced model for supplied whole event alternatives, shared budgets, absolute dates, prerequisites and physical offices.

## Verification

The [current release checks](release-checks.json) ran on 26 October 2026:

* Browser: 23 targeted regressions and 300 comparisons with exhaustive enumeration, plus a 120 member scale example.
* Advanced optimizer: 38 tests run, with 37 passing and one dependency specific test skipped.
* Mechanics calculator: 25 tests passed.
* Integrated task example: `OPTIMAL`, two members, 1,600 additional timer seconds saved, zero general speedups consumed.

The illustrated guide was exercised in the actual browser with synthetic inputs. Its downloaded workbook was reopened to confirm all six sheets and the displayed example. These checks establish specific behavior, not reader usability, actual community adoption or live game outcomes. [verification.json](verification.json) retains the earlier local development record; its local publication status describes that earlier run.

## Advanced plan optimizer

Clone or download this repository, then run these commands from its folder on macOS or Linux:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python advanced/optimizer.py --input advanced/example.json --output advanced/result.json
```

Read [advanced/schema.txt](advanced/schema.txt) before changing the input. Supply exactly one no title baseline per member and complete alternative plans. A baseline may still contribute and consume resources. Benefits are signed integer differences in one declared unit. The production solver selects supplied alternatives and validates declared constraints; it does not invent game feasible plans.

`OPTIMAL` requires proof at all six policy stages. `FEASIBLE` and `UNKNOWN` preserve uncertainty. The exhaustive reference engine is limited to small instances and is not an automatic production fallback.

## Mechanics calculator

[mechanics/README.md](mechanics/README.md) explains the separate `same-work` and `fixed-budget-training` routes. They evaluate explicitly supplied task statistics with exact fractions, title start gating and bounded training arithmetic. They do not infer live buff values, task readiness, collection windows, residual camps or resource reinvestment.

Run the complete synthetic calculator to optimizer example:

```bash
.venv/bin/python examples/from_tasks.py
```

## Reproduce the checks

```bash
node tests/browser-core.test.cjs
.venv/bin/python -m unittest discover -s advanced -p 'test_*.py' -q
python3 -m unittest discover -s mechanics -p 'test_*.py' -q
```

## Files

| File or folder | Purpose |
| --- | --- |
| `index.html` | Current written project case study |
| `walkthrough.html`, `ALGORITHM.md` | Illustrated v4 instructions at the existing addresses |
| `Whiteout Buff Scheduler.html` | Browser survey planner |
| `advanced/` | Complete alternative plan optimizer, schema, examples and tests |
| `mechanics/` | Supplied task comparison calculator and tests |
| `examples/` | Integration and clearly labeled documentation variations |
| `tests/` | Browser solver regressions and exhaustive comparisons |
| `research.html`, `research.json` | Development research, assumptions and source record |
| `documentation-source.json` | Shared editable source for the guide and case study |
| `docs/` | [Walkthrough PDF](docs/Whiteout%20Buff%20Scheduler%20Walkthrough.pdf) and [case study PDF](docs/Whiteout%20Buff%20Scheduler%20Case%20Study.pdf) |

<a id="power-user-commands-python-script"></a>

## Earlier Python pipeline

[SortingAlgorithm_v3.py](SortingAlgorithm_v3.py) and [requirements-whiteout.txt](requirements-whiteout.txt) remain available for historical links. That pipeline uses the earlier inventory based model. It is separate from v4 and does not implement v4's incremental benefit policies. Current instructions use the browser planner or the advanced and mechanics routes above.

## Privacy note

Survey processing stays local. Initial loading uses external fonts and SheetJS. The six exported sheets are the three buff days, Summary, Declared resources and Scheduler notes. Excluding names from day sheets and Summary does not remove names and comments from private notes. Review the whole workbook before sharing.

Keep real surveys, member details and generated private schedules out of Git. The committed CSV and workbook examples are synthetic teaching fixtures. The existing spreadsheet ignore rules remain in place.

## License

[MIT](LICENSE). A community project for Whiteout Survival players. Not affiliated with or endorsed by the game's developers.
