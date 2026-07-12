# How the buff scheduler works

This document explains, in plain language, how the SvS ministry scheduler decides who
gets which appointment. It starts with an intuition anyone can follow, then gives the
precise mechanics for anyone reviewing or extending the code.

The one-line version: **it's a plumbing puzzle. One drop of water equals one booked
appointment, the shape of the pipes encodes all the rules, the value stamped on each
pipe encodes our priorities, and the water automatically finds the highest-scoring way
through.**

---

## The problem we're solving

Every SvS cycle we run three buff days — Construction, Research, and Troop Training —
and each day is divided into 30-minute appointment slots. Players fill out a form
saying which days they want, when they're free, and how many speedups and crystals
they'll bring. Each appointment is worth a number of "prep points" based on what that
player brings.

We want the arrangement that scores the most points overall, subject to rules that can
never be broken:

- Nobody is placed outside a time window they said they were free.
- Nobody is placed on a day they didn't pick.
- Each slot holds exactly one player.
- Each player gets at most a set number of appointments for the whole event (default: one per day, three total).

The tempting approach — give the biggest spender the best slot, then the next biggest,
and so on — looks reasonable but gets stuck. Suppose two whales are *both* only free at
exactly 08:00 on Construction day. One slot, two players. Hand 08:00 to the slightly
bigger whale without thinking, and the other whale, who had no other free time, now
scores nothing — when a smarter swap (slide whale A to a different slot he's also free
for, give 08:00 to whale B) would have scored both. Simple sorting can't see trades
like that. You need something that weighs the whole board at once.

---

## The intuition: a network of pipes

Picture the whole problem as pipes that water flows through, from one starting tap on
the left to one drain on the right.

```
                     pipe carries the
                     appointment's points
                            │
   ┌─────┐   ┌──────────────▼──────────┐   ┌───────────┐   ┌───────┐
   │ TAP │──►│         PLAYER           │──►│ TIME SLOT │──►│ DRAIN │
   └─────┘   │   (at most 3 drops)      │   │ (1 drop)  │   └───────┘
             └──────────────────────────┘   └───────────┘

        one drop that reaches the drain  =  one booked appointment
```

Reading it left to right:

The **tap** feeds a pipe into each player, and that pipe is only wide enough for three
drops. That's the "at most three appointments per player" cap — it's built into the
pipe itself, so it can't be violated.

From each player, pipes branch out **only** to the slots they are actually free for, on
the days they picked. There is simply no pipe to a slot they can't make, so scheduling
someone outside their availability is physically impossible in the model. The illegal
options have no plumbing.

Each slot has one narrow pipe to the drain, wide enough for exactly one drop. That's
"one player per slot."

Every player-to-slot pipe is stamped with the points that appointment would earn — a
thicker pipe is worth more. The whole job is to route the water so the total points
stamped on the pipes it flows through is as high as possible. **That routing is the
schedule.**

The three clever parts are how we rank competing goals, how we count shared speedups
fairly, and why the result is provably the best possible.

---

## Ranking competing goals: the place-value trick

We don't have just one goal, we have three, in strict priority order:

1. Most total points.
2. As a tiebreaker, fill the most slots.
3. As a final tiebreaker, be fair — favor players with barely any free time, and among
   otherwise-equal players, whoever signed up earlier.

Rather than run three separate passes, we bake all three into the single number stamped
on each pipe, built like a multi-digit number with separate columns. Points go in the
"millions" column, slot count in the "thousands," fairness in the "ones." Because one
unit in the millions column always outweighs anything happening in the ones column, the
water naturally chases points first, breaks ties by filling more slots, and only then
leans on fairness — all in one solve.

The web version adds one optional nicety inside the fairness column: when it costs
nothing at the points and fill levels, it nudges players toward specific times they
requested in the form comments. It never overrides submitted availability, which always
stays a hard rule.

---

## Counting shared speedups only once

A player's *general* speedups help whichever appointment they land in, but they
shouldn't be counted three times if that player gets three appointments. So in the
model each player actually has two side-by-side feed pipes from the tap: a first one,
one drop wide, that carries the general-speedup bonus, and a second, wider one that
carries zero bonus. However the water splits after that, the bonus rides along on
exactly the first appointment and never repeats. Simple plumbing, exact accounting.

---

## Why the answer is provably the best, not just good

Two things make this an exact optimizer rather than a clever guess.

First, the algorithm never commits blindly. Every pipe has a hidden reverse pipe, so
when routing a new drop reveals that an earlier choice was a mistake, water can flow
backward and re-route. That backtracking is exactly what separates it from the naive
"sort by points" approach — it can discover the whale-swap on its own. It keeps grabbing
the single most profitable route still open, undoing earlier choices as needed, until no
profitable route remains.

Second, networks shaped like this come with a mathematical guarantee: the best possible
flow always lands on whole drops. You never end up with "half a player" in a slot that
you'd have to round off and lose the optimum. So the best water routing is exactly the
best real schedule.

---

## Under the hood (for engineers)

The intuition above is faithful to the implementation. Here is the precise model, as
built in `solve_global_schedule_network` in `SortingAlgorithm_v3.py` (Python) and
`solveSchedule` in the web app.

### The graph

A directed graph with a single source and sink:

- `SOURCE → player`, as two parallel arcs. The first has capacity 1 and profit
  `general_points × SCALE` — this is the "first appointment unlocks the general pool"
  bonus. The second has capacity `max_total − 1` and profit 0. Together they cap the
  player at `max_total` appointments while crediting general speedups exactly once.
- `player → (player, day)`. Days on which the player has zero base points but nonzero
  general points are routed through a per-player capacity-1 "zero-collector," which
  enforces at most one general-only appointment.
- `(player, day) → (day, slot)`, one arc per slot the player is available for, carrying
  the appointment's profit.
- `(day, slot) → SINK`, capacity 1, which enforces one player per slot.

Because availability is expressed as *which arcs exist*, every hard constraint
(availability, selected days, one-per-slot, per-player cap) is structural — no side
constraints needed.

### Lexicographic objective via integer weights

Let `S` be the total number of slots across all days and `maxOrder` the largest sign-up
index. Priorities are layered so a higher layer dominates any possible swing in a lower
one:

```
tie(p, day, slot) = scarcity(p, day) * 10_000 + (maxOrder − order(p))
TIE_BOUND         = 47 * 10_000 + maxOrder + 1        # > any single tie value
FILL              = S * TIE_BOUND + 1                 # one fill > all ties combined
SCALE             = S * (FILL + TIE_BOUND) + 1        # one point > all fills + ties
```

The profit on a candidate arc `(p, day, slot)` is
`base_points[p, day] × SCALE + FILL + tie(p, day, slot)`, and the source arc adds
`general_points[p] × SCALE`. Maximizing total profit therefore maximizes points first,
then filled slots, then the scarcity/order tiebreak — in a single solve. These weights
grow large, so the implementation uses arbitrary-precision integers (Python `int`,
JavaScript `BigInt`) to keep every layer exact.

### Solver and optimality

Profits are negated into costs and solved as a minimum-cost flow using
successive-shortest-path augmentation (SPFA/Bellman-Ford, since costs are negative). The
graph is a DAG and augmentation stops when no profit-positive path remains, yielding the
maximum-profit flow.

Optimality is guaranteed by total unimodularity: the node-arc incidence matrix of a flow
network is totally unimodular, so with integer capacities the min-cost-flow linear
program has an integral optimal vertex. The flow optimum therefore equals the integer
(ILP) optimum exactly — no rounding, no approximation. If OR-Tools is installed the
CP-SAT solver can be used instead (`--solver cpsat`); it reaches the same optimum by a
different route.

---

## How this was verified

The built-in solver was checked against exhaustive brute-force enumeration on 400
randomized instances — including locked template slots and per-player caps — and matched
the true lexicographic optimum on all three layers (points, then fills, then tiebreak)
in every case. The web app's JavaScript engine passed the same brute-force check on 150
instances and, on the real survey export, produced byte-identical column mapping,
identical per-player records, and the identical optimum as the Python pipeline. Every
generated workbook is re-opened and each assignment re-verified before a run reports
success.

---

## Where to look in the code

- `SortingAlgorithm_v3.py` — `build_input_mapping` (reads the form), `build_player_records`
  (parses availability, speedups, crystals, dedupes submissions),
  `solve_global_schedule_network` and `_MinCostFlow` (the solver described here), and the
  output writer and verifier.
- `Whiteout Buff Scheduler.html` — the same pipeline ported to run in a browser, with the
  solver in `solveSchedule`. The core logic sits between the `CORE-BEGIN` and `CORE-END`
  markers and is deliberately kept free of any page code so it can be tested in isolation.
