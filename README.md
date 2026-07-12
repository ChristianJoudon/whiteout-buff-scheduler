# ❄ Whiteout Buff Scheduler

A free, browser-based **schedule optimizer for Whiteout Survival SvS buff days** — Construction,
Research, and Troop Training. Drop in the sign-ups your members submit through Microsoft Forms and get
the arrangement that earns the **most total prep points**, while never placing anyone outside a time they
said they were free.

**▶ Use it now: https://christianjoudon.github.io/whiteout-buff-scheduler/**

- **[Launch the scheduler](https://christianjoudon.github.io/whiteout-buff-scheduler/Whiteout%20Buff%20Scheduler.html)** — runs entirely in your browser
- **[How it works (the explainer)](ALGORITHM.md)** — plain-language walkthrough of the optimizer
- No sign-up, no installs, nothing uploaded. The survey file never leaves your computer.

---

## The easy way (no Python, no terminal)

Open the **[hosted scheduler](https://christianjoudon.github.io/whiteout-buff-scheduler/Whiteout%20Buff%20Scheduler.html)**,
or download **Whiteout Buff Scheduler.html** and double-click it in any browser. Drag the responses file
your members download from Microsoft Forms onto the big panel, glance at the schedule and the
"needs a quick look" notes, and press **Download the schedule (.xlsx)**. That's the whole workflow.

The page runs entirely on the computer; the survey file is never uploaded anywhere. The download contains
one sheet per buff day (Construction / Research / Troop Training) sorted by time, a Summary sheet with
everyone's appointments and estimated points, and a "Scheduler notes" sheet listing anything worth
checking by hand plus every comment people wrote on the form. Options for appointment length, per-player
limits, the daily time window, and whether to include in-game names sit behind an "Options" fold, but the
defaults match how the form is normally run (30-minute slots, up to one appointment per day, full 24-hour
UTC coverage). Emails are never written to the output.

## What the optimizer guarantees

Within the rules that can never be broken — nobody placed outside a time they're free, nobody placed on a
day they didn't pick, one player per slot, and a per-player appointment cap — it finds the arrangement
with the **highest total estimated prep points**, then fills as many remaining slots as possible, then
breaks ties fairly (favoring players with the fewest free times, and earlier sign-ups among equals).

It is an **exact optimizer, not a rough sort**: the three-day problem is modeled as a minimum-cost flow
network whose optimum is provably the true optimum (total unimodularity guarantees integer solutions). A
player's shared *general* speedups are credited exactly once across the whole event. Read the full
plain-language walkthrough in **[ALGORITHM.md](ALGORITHM.md)**.

The point model matches current community references: speedups **30 pts/minute**, Fire Crystals **2,000**,
Refined Fire Crystals **30,000**, Fire Crystal Shards **1,000**.

## Verification

The built-in solver was checked against exhaustive brute-force enumeration on 400 randomized instances
(including locked template slots and event-wide caps) and matched the true lexicographic optimum on all
three layers — total points, then filled slots, then the scarcity/order tie-break — in every case. The web
page's JavaScript engine passed the same brute-force check on 150 instances, and on a real state's July
sign-ups it produces byte-identical column mapping, identical player records for all unique players, and
the identical optimum as the Python pipeline. The written workbook is re-opened and every ID re-verified
before a run reports success.

Messy answers such as `300/250`, `78days +`, and `350-400 day` no longer collapse to zero — ranges resolve
to their conservative lower bound. The Forms "Completion time" column (including Excel serial dates) is
recognized so duplicate submissions are resolved by actual submission time rather than by luck of row order.

## Power-user commands (Python script)

The `SortingAlgorithm_v3.py` pipeline runs the same exact solver from the command line and can fill an
existing shared workbook in place.

```bash
python -m pip install -r requirements-whiteout.txt   # pandas + openpyxl; ortools optional

python SortingAlgorithm_v3.py --inspect --verbose    # map both workbooks, no scheduling

python SortingAlgorithm_v3.py \
  --applications "/path/to/Forms responses.xlsx" \
  --output "/path/to/Optimised Buff Schedule.xlsx" \
  --verbose
```

`--template` may point at your state's shared schedule workbook to fill it in place (existing entries stay
locked); if it's omitted or missing, the clean three-sheet layout is created. `--max-total-per-player 1`
enforces one appointment for the whole event, `--resource-unit days|hours|minutes|points` sets the meaning
of plain numeric columns, and `--solver auto|cpsat|network` chooses the optimizer (the built-in `network`
solver is exact and needs no external dependencies). The Python output writes only time, Player ID, and
Alliance — never emails.

v3 removes the OR-Tools requirement: it ships a built-in exact minimum-cost-flow solver that provably
reaches the same optimum. If OR-Tools is installed, CP-SAT is still available via `--solver cpsat`.

## Files

| File | What it is |
| --- | --- |
| `index.html` | Presentation / landing page (this is what GitHub Pages serves first) |
| `Whiteout Buff Scheduler.html` | The scheduler — a single self-contained web app |
| `SortingAlgorithm_v3.py` | The Python pipeline (same exact solver) |
| `ALGORITHM.md` | Plain-language explainer of how the optimizer works |
| `requirements-whiteout.txt` | Python dependencies (pandas + openpyxl; ortools optional) |

## Privacy note

Real member sign-ups and generated schedules contain players' in-game names and numeric game IDs, so
`.gitignore` excludes all spreadsheet files (`*.xlsx`, `*.xls`, `*.csv`) — please keep it that way when
contributing. The tools themselves never transmit any data off your machine.

## License

[MIT](LICENSE) — a community tool for Whiteout Survival players. Not affiliated with or endorsed by the
game's developers.
