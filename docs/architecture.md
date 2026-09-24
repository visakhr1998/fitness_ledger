# Architecture

This page describes how the code is organised. It is intended for contributors.

## Overview

```
CLI ─┐
     ├─► queries.py ──► volume.py · progression.py · insights.py · aei.py
API ─┘        │              (rules engine: plain Python, no I/O)
              ▼
          db.py (SQLiteRepository ─► SQLite)
              ▲
          sync.py ──► mcp_client.py ──► Hevy MCP · Google Health MCP

          chat.py   ──► queries.py as tools ──► llm.py (provider transports)
          intake.py ──► llm.py            (plain English ─► proposed records)
          coach/    ──► planning.py (set allocation) ──► assembler ──► Plan
```

## Design rules

The following rules keep this structure intact:

- **The rules engine has no I/O.** `volume.py`, `progression.py`, `insights.py`,
  `aei.py` and `planning.py` take plain data and return plain data. They do not
  use the database, the network or a model, so every number can be tested in
  isolation.
- **All storage goes through `db.py`.** No other module opens a connection or
  builds a database path, so the storage backend can be replaced in one place.
- **The model never calculates.** It chooses which function to call and
  describes the result. This is also why the provider can be swapped freely.
- **Only `sync.py` reads from the MCP servers.** Everything else reads the local
  cache. The two exceptions are `doctor`, which checks that both servers
  respond, and approving a Hevy write-back, which sends the routine.

For anything that reports a number, the API is a thin wrapper over `queries.py`,
so the API and the CLI always agree. Endpoints that manage state (goals, plans,
availability and write-back) use `SQLiteRepository` directly. The full endpoint
list is available at `/docs` while the server is running.

## Goal intake

`intake.py` turns a sentence into a proposal: goals, standing weekly rules, a
weekly running target and one-off days off. Nothing is saved until the user
confirms it in the Goals screen.

- A deterministic check for red-flag symptoms (for example "numbness" or "gives
  way") runs before the model is called. If it matches, no model request is made
  and a fixed referral message is returned.
- The model returns its result through a single tool call, and every proposed
  record is validated by the same model classes the CLI and API use.
- Relative dates such as "this Friday" are resolved by copying from a 14-day
  calendar included in the prompt. Any date outside that calendar is rejected.

## The planner

The planner turns goals, training history and free days into a proposed week.

```
context reader (no model; reads the database)
        │  goals · weekly rules · training history · free days · exercise pool
        │  · last week's plan · whether this week follows a break
        ▼
strength planner ──► running planner        (run in sequence)
        │
        ▼
planning.py calculates sets and checks the rules; the assembler stores the plan
```

### Division of work

The planner chooses exercises and days. `planning.py` calculates every number.
This is enforced by the output schema: the planner's output types have no field
for sets, reps, weight or distance.

Set counts come from the weekly target rather than from last week's shortfall.
Allocating the shortfall would give someone who hit their target a near-empty
week. The shortfall is used instead to decide which sets to keep when a session
exceeds its limit. When a week is too tight, volume is given up in this order:

1. Volume per muscle group
2. Coverage of every muscle group
3. Runs on target
4. Number of sessions

### Validation and retries

`planning.validate` checks each draft against the hard rules: training days
only, the set limits, the exercise pool, rest between sessions for the same
muscle, no run the day after legs (unless the setting allows it), and the
standing weekly rules.

A draft that breaks a rule, or contains no training, is sent back to the planner
with the list of problems. After `COACH_MAX_PLAN_ATTEMPTS` attempts, the draft
with the fewest problems is kept and shown with them. A week with no training is
never stored; the plan status reports the failure instead.

### Returning from a break

`planning.ramp` scales the weekly targets after a break of two or more weeks
without lifting. The context reader calculates the factor once and the assembler
reads it back, so the stored plan is scaled by exactly the factor the planner
was told about.

### Continuity

The planner is shown last week's plan and asked to reuse its exercises, since
progress on a lift can only be tracked if the lift appears again. The plan's
trade-offs report how many exercises were kept.

### Storage and timing

Plans are append-only: a revision is stored as a new row that points to the
previous one.

Each attempt takes two or three model requests. A plan can therefore take from
tens of seconds to several minutes, so generation runs in the background and the
client polls for status, as it does for sync. A second request while one is
running is refused rather than queued.

## Writing to Hevy

Write-back is the only feature that changes data outside the app. It follows a
fixed sequence: **propose, diff, confirm, write, log**. The propose step never
contacts Hevy.

Hevy has no delete endpoint, so anything written must be removed by hand in the
Hevy app. For that reason the diff step cannot be skipped.

Two features use this flow: the routine builder on the Gym screen and the
per-day send on the Week screen. Accepting a plan only records it locally; only
the per-day send writes to Hevy.

Two safeguards protect the write:

- A proposal is claimed in the database before the Hevy call is made, so two
  simultaneous approvals cannot both write a routine.
- Write requests carrying an `Origin` header from another site are refused, so
  a web page open in another tab cannot trigger an approval.
