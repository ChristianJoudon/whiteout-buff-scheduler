# How the buff scheduler works

## Walkthrough and project explanation

The illustrated operating guide and project case study now have matching, updated editions. This document stays at its original URL so existing links continue to work.

[Read the illustrated walkthrough](https://christianjoudon.github.io/whiteout-buff-scheduler/walkthrough.html) · [Read the project case study](https://christianjoudon.github.io/whiteout-buff-scheduler/) · [Launch the scheduler](https://christianjoudon.github.io/whiteout-buff-scheduler/Whiteout%20Buff%20Scheduler.html)

[Download the walkthrough PDF](docs/Whiteout%20Buff%20Scheduler%20Walkthrough.pdf) · [Download the case study PDF](docs/Whiteout%20Buff%20Scheduler%20Case%20Study.pdf)

Edition: 26 October 2026. Screenshots use synthetic players.

<a id="the-problem-were-solving"></a>

## Turn survey responses into a schedule

Use the browser tool to prepare appointments, review exceptions and download an Excel workbook.

Open the project website and select Launch the scheduler. Start with the response file exported from Microsoft Forms, then follow the sequence below. The tool reads the answers and shows a schedule for review. You arrange the appointments in the game separately.

1. Prepare clear survey answers
2. Set the event rules
3. Choose the response file
4. Read the result
5. Review attention messages and comments
6. Inspect each buff day
7. Download and share the right sheets
8. Resolve common input problems

Before you begin: have a current response export, an agreed appointment length and a confirmed schedule window in UTC. The examples use invented players and a window from 08:00 to 13:00 UTC. These are demonstration settings, not event defaults.

## Prepare clear survey answers

Keep the question headers and make units, player identity and availability unambiguous.

### Use a supported response file

Export the responses as .xlsx or .csv. The file chooser also accepts .xlsm. Keep the survey header row. The tool needs a recognizable Player ID field, one speedups field for each buff day, and either shared availability or availability for each day.

### Write explicit units and UTC ranges

Use a value such as 12h, or put hours in the resource header. A bare number otherwise defaults to days unless the header identifies hours, minutes or points. Write availability such as 08:00 to 10:00 UTC. The tool does not convert local time zones.

### Keep optional answers useful

Alliance, player name, completion time, selected buffs, general speedups, crystals and comments add context. If a selected buffs column is present, each player needs recognizable day choices. A blank choice is not treated as permission to schedule every day.

### Resolve ambiguous resource answers

Use one clear amount instead of a range or a value such as 300/250. Missing or unreadable resource values become zero. The optimizer can only work with the values the parser recognizes.

| Field | Clear example | Meaning |
| --- | --- | --- |
| Player ID | 900000001 | Synthetic identifier |
| Construction speedups (hours) | 12 | Twelve hours |
| Selected buffs | Construction; Research | Requested days |
| General availability | 08:00 to 10:00 | UTC time window |

For duplicate IDs, a recognized completion time takes priority and the latest recognized time wins. When timestamps tie or both are unavailable, the later source row wins. Review the duplicate notice after loading.

## Set the event rules

Open Options when the default appointment rules do not match your event.

### Cap and appointment length

The default allows one appointment per buff day, three total. Reduce the event cap to two or one when needed. Appointment lengths are 30, 20, 15 or 60 minutes.

### UTC schedule window

Set Schedule day runs from and …until (UTC). Defaults cover 00:00 to 24:00. Every whole appointment must fit both this window and the player’s availability.

### Names and time wishes

The names checkbox affects day sheets and Summary, but does not anonymize Scheduler notes. The wishes option recognizes comment time ranges within tied results. It cannot override availability or reduce points or appointment count.

Changing an option after selecting a file recalculates that same file. Download again after making the final change.

## Choose the response file

Selecting a file starts scheduling automatically.

### Choose the current export

Select Choose file… and pick the current response export, or drag the file into the drop area. Keep the original export available if you need to correct an answer and run it again.

### Wait for the result

The tool reads the file and creates the schedule. There is no separate Generate button. Continue when the Done status and result cards appear.

### If a required column cannot be found

Read the message identifying the missing field. Correct the corresponding survey header or export, then choose the corrected file. Renaming an unrelated field to pass the check would give the scheduler the wrong data.

The browser processes the selected file locally. An internet connection is needed initially to load the page and its spreadsheet library.

## Read the result before exporting

The four cards describe different parts of the calculation.

### Players placed is not appointments made

One player can receive appointments on several requested days. In this example, six players receive twelve appointments. The event cap limits the total for each player; it does not promise an appointment to everyone.

### Responses read can include duplicates

Nine source responses become eight unique players because one ID appears twice. Compare the response count with the export and read any duplicate notice before accepting the result.

### Points are an estimate

The score uses the recognized resource amounts and the implemented points model. It assumes those resources are used. The schedule maximizes that model within the configured rules; it does not guarantee the eventual game score.

A successful calculation can still contain entries that need your attention. Continue to the messages and day tabs before downloading.

## Review attention messages and comments

Resolve the input issues that matter before sharing a schedule.

### Read the reason, then check the source

An unplaced player may have no selected day, no usable resources, no full slot inside the window, or stronger competing requests. A short availability range can produce no valid slot even though the player supplied a time. Correct the source only when the answer is wrong, then load it again.

### Keep the on page messages

Read What people wrote in the comments separately from the attention warnings. Scheduler notes contains unplaced reasons and comments, but does not copy every warning. Review duplicate IDs and other on page issues before download. Retain any organizer follow up you still need to resolve.

## Inspect each buff day

Use the three tabs to review who is placed, when and on which day.

### Use UTC and event dates

Select all three day tabs in turn. Times are UTC. The displayed weekday is a label, not a calendar date. Use the agreed event dates when arranging appointments in the game.

### Understand an open row

Open means nobody was assigned to that slot. Empty slots are allowed. The tool cannot override availability, day selection or the appointment cap simply to fill a row.

### Recalculate rule changes

Update Options if the event window or cap changes, then review the new result. A manual workbook swap is not checked by the browser solver and can break the original constraints.

## Download and share the right sheets

Export only after the result and attention messages have been reviewed.

### Open the downloaded workbook

Select Download the schedule (.xlsx). Open the workbook in Excel or Google Sheets. Check that the three day sheets, Summary and Scheduler notes are present and that you are using the version downloaded after your final options change.

### Use each sheet for its purpose

The day sheets contain UTC slots, player IDs, alliances and optional names. Summary lists each player's assignments and estimated points. Scheduler notes holds unplaced reasons and the original comments for organizer review.

### Review the notes before sharing

The names checkbox does not remove names or comments from Scheduler notes. Free text may contain personal information. Share the appropriate day sheets with players and keep organizer notes restricted to the people who need them.

### Arrange the appointments separately

The workbook is a schedule to use in the game. Downloading it does not create or change any in game appointment. If you edit the workbook manually, review the affected availability and appointment limits yourself.

## Resolve common input problems

Use the reported issue to correct the file or the event settings.

### A required column is missing

Keep recognizable headers for Player ID, all three resource fields and shared or day specific availability. Check that the file is the response export rather than a previously generated schedule.

### A player has no valid appointment

Check selected buffs, usable resources, the full appointment length and the UTC window. An aligned single start or a clear time range can be recognized. A ten minute window cannot hold a thirty minute appointment.

### A resource total looks wrong

Use explicit units such as 12h or a header that says hours. Avoid ranges and mixed answers. Blank or unreadable values become zero. A resource field labeled points is interpreted as points rather than a duration.

### A player appears twice

The tool keeps one entry per normalized ID. It prefers a recognized completion time and retains the latest one. When timestamps tie or both are unavailable, the later source row wins. Confirm that the retained answers are the intended ones.

### The spreadsheet library did not load

Reload the page with an internet connection so its external spreadsheet library can load, then choose the file again. Keep the source export available because reloading clears the selected file.

### A comment was not honored

Time wishes are preferences within tied objectives. They do not override submitted availability, day selection, the event cap or higher priority scoring. General prose in a comment may not be recognized as a time preference.

Reference edition: public source reviewed on 26 October 2026, commit 457e36e1fd5633fde36bb1ab7a529c73b91abaf8. The walkthrough covers the browser route. The repository README documents the separate Python route.

## Read the input format examples

Use these synthetic examples to make survey answers clear.

### Keep durations and points separate

A header that identifies points uses the amount as a final score. A speedups field uses a duration. In the implemented model, each speedup minute contributes 30 points; shared general speedups count once for a player across the event.

### A whole slot must fit

With 30 minute appointments, 08:00 to 08:30 can contain one slot. A range from 08:00 to 08:10 cannot. The configured schedule window can narrow the set further. Comments do not create new availability.

| Field | Clear example | Meaning |
| --- | --- | --- |
| Player ID | 900000001 | Synthetic identifier |
| Construction speedups (hours) | 12 | Twelve hours |
| Selected buffs | Construction; Research | Requested days |
| General availability | 08:00 to 10:00 | UTC time window |

Input examples accompany the nine page walkthrough. They are reference material, not additional operating steps.

<a id="the-intuition-a-network-of-pipes"></a>
<a id="ranking-competing-goals-the-place-value-trick"></a>
<a id="counting-shared-speedups-only-once"></a>
<a id="why-the-answer-is-provably-the-best-not-just-good"></a>
<a id="under-the-hood-for-engineers"></a>
<a id="the-graph"></a>
<a id="lexicographic-objective-via-integer-weights"></a>
<a id="solver-and-optimality"></a>

## The model behind the result

The implementation represents appointments as flow through a network. A route passes through a player, a selected day and an available slot. Capacity limits control how many appointments can pass through each part. Missing connections rule out unavailable times. The minimum cost flow solver uses reverse connections to revise earlier choices as it finds a better arrangement. Integer capacities keep assignments whole, so the result does not depend on rounding a fraction of an appointment.
Integer weights preserve the priority order: maximize estimated points, then fill more slots, then apply the tie rules. The lower layer considers limited availability and signup order; the browser can also consider time requests in comments when that option is enabled. None of those preferences overrides availability or the first two objectives. Python integers and JavaScript BigInt retain the large weights exactly. A separate capacity limited connection credits each player's general speedup pool once across the event, rather than adding the same shared resources to every appointment.

[See the complete design explanation](https://christianjoudon.github.io/whiteout-buff-scheduler/#how)

<a id="how-this-was-verified"></a>

## Verification and limits

### Demonstrated

The synthetic walkthrough used nine responses representing eight unique players. The browser placed six players in twelve appointments and displayed 209k estimated points. Those figures describe this example. They do not measure community adoption, time saved, real event scores or improvement over a previous schedule.

### Previously reported

The repository documentation reports comparisons with exhaustive enumeration on 400 Python cases and 150 JavaScript cases. Those are historical verification claims, not tests rerun for this publication. The reviewed public snapshot does not include the test files needed to reproduce those counts directly.

### Still limited

The optimum applies to the parsed records, configured constraints and implemented point model. It cannot establish what a player will actually spend or whether an appointment will be kept. Unclear survey answers still need organizer judgment, and current game scoring rules need to agree with the constants used by the tool.

<a id="where-to-look-in-the-code"></a>

## Implementation references

The browser implementation is in [Whiteout Buff Scheduler.html](Whiteout%20Buff%20Scheduler.html). The Python pipeline and workbook verifier are in [SortingAlgorithm_v3.py](SortingAlgorithm_v3.py). Installation and command line examples remain in [README.md](README.md).
