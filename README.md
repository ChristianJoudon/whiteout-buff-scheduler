# ❄ Whiteout Buff Scheduler

A free, browser-based **schedule optimizer for Whiteout Survival SvS buff days** — Construction,
Research, and Troop Training. Drop in the sign-ups your members submit through Microsoft Forms and get
the arrangement that earns the **most total prep points**, while never placing anyone outside a time they
said they were free.

**▶ Use it now: https://christianjoudon.github.io/whiteout-buff-scheduler/**

- **[Launch the scheduler](https://christianjoudon.github.io/whiteout-buff-scheduler/Whiteout%20Buff%20Scheduler.html)** — runs entirely in your browser
- **[Walkthrough and explainer](ALGORITHM.md)** with an [illustrated browser edition](https://christianjoudon.github.io/whiteout-buff-scheduler/walkthrough.html)
- **[Written project case study](https://christianjoudon.github.io/whiteout-buff-scheduler/)** and [case study PDF](docs/Whiteout%20Buff%20Scheduler%20Case%20Study.pdf).
- **[Walkthrough PDF](docs/Whiteout%20Buff%20Scheduler%20Walkthrough.pdf)** for offline reference.
- Survey data is processed locally. The initial page loads external libraries and fonts.

---

## The easy way (no Python, no terminal)

Open the **[hosted scheduler](https://christianjoudon.github.io/whiteout-buff-scheduler/Whiteout%20Buff%20Scheduler.html)**,
or download **Whiteout Buff Scheduler.html** and double-click it in any browser. Drag the responses file
your members download from Microsoft Forms onto the big panel, glance at the schedule and the
"needs a quick look" notes, and press **Download the schedule (.xlsx)**. That's the whole workflow.

The browser processes the selected file locally. Selecting a file schedules it automatically; changing Options recalculates that same file. Review the attention messages and all three day tabs before downloading. The workbook contains three day sheets, Summary and Scheduler notes. The notes contain unplaced reasons and original comments, but do not copy every on page warning.

Defaults are 30 minute appointments, up to one per day and three across the event, with a full UTC day window. Names can be hidden in the day sheets and Summary, but Scheduler notes can still contain names and comments. Review that sheet before sharing.

## What the optimizer guarantees

Within the rules that can never be broken — nobody placed outside a time they're free, nobody placed on a
day they didn't pick, at most one player per slot, and a per-player appointment cap — it finds the arrangement
with the **highest total estimated prep points**, then fills as many remaining slots as possible, then
breaks ties fairly (favoring players with the fewest free times, and earlier sign-ups among equals).

It is an **exact optimizer, not a rough sort**: the three-day problem is modeled as a minimum-cost flow
network whose optimum is provably the true optimum (total unimodularity guarantees integer solutions). A
player's shared *general* speedups are credited exactly once across the whole event. Read the full
plain-language walkthrough in **[ALGORITHM.md](ALGORITHM.md)**.

The implemented point model uses: speedups **30 pts/minute**, Fire Crystals **2,000**,
Refined Fire Crystals **30,000**, Fire Crystal Shards **1,000**.

## Verification

Project documentation records comparisons against exhaustive enumeration on 400 Python cases and 150 JavaScript cases. These are historical verification reports. The public snapshot used for the October 2026 documentation review does not include those test files, and the counts were not rerun for that review.

The browser checks assignment constraints before displaying results. The Python pipeline has a separate verifier that reopens the written workbook. The browser does not reopen the downloaded workbook. The illustrated synthetic example was run through the actual browser tool, and its downloaded workbook was inspected for the documentation.

Use explicit resource amounts and units. Ambiguous values such as `300/250` or a range still need review; do not assume the parser always selects a conservative amount. A recognized completion time takes priority when resolving duplicate player IDs, with the latest recognized timestamp winning. When timestamps tie or both are unavailable, the later source row wins.

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
contributing. Survey data is processed locally. The browser initially loads external libraries and fonts. Free text in comments may contain personal information, and the names option does not anonymize Scheduler notes. Review exported sheets before sharing.

## License

[MIT](LICENSE) — a community tool for Whiteout Survival players. Not affiliated with or endorsed by the
game's developers.
