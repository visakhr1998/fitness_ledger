# Architecture

```
CLI ─┐
     ├─► queries.py ──► volume.py · progression.py · insights.py · aei.py
API ─┘        │              (rules engine: plain Python, no I/O)
              ▼
          db.py (SQLiteRepository ─► SQLite)
              ▲
          sync.py ──► mcp_client.py ──► Hevy MCP · Google Health MCP

          chat.py ──► queries.py as tools ──► llm.py (provider transports)
          coach/  ──► planning.py (set allocation) ──► assembler ──► Plan
```

Four rules keep this shape:

- **The rules engine takes and returns plain data.** No database, no network, no
  model, so every number is testable on its own.
- **All storage lives in `db.py`,** so swapping it later stays contained. No
  code elsewhere opens a connection or builds a database path.
- **The model never does maths.** It picks a function to call and describes what
  comes back — which is why the provider is swappable. `planning.py` works out
  every set count and has no I/O, so it sits with the rules engine rather than in
  `coach/`, which touches the database.
- **`sync.py` is the only thing that reads from the MCP servers.** Everything
  else reads the local cache. Two places call out directly: `doctor` pings both
  to check they answer, and approving a write-back sends the routine.

The API is a thin wrapper over `queries.py` for anything that reports a number,
so the API and CLI can't disagree about a figure. Endpoints that manage state —
goals, plans, availability, write-back — use `SQLiteRepository` directly. The live
endpoint list is at `/docs` while the server is running; there is no copy of it
here, because a hand-maintained one was wrong within a week.

## The planner

Turns goals, training history and free days into a proposed week.

```
context reader (no model — just reads the database)
        │  goals · training history · free days · exercise pool · last week's plan
        ▼
strength planner ──► running planner        (one after the other)
        │
        ▼
planning.py works out the sets, validates, saves the Plan
```

**The agent never says how many sets.** It picks exercises and days;
`planning.py` calculates the numbers. That's enforced by the data structure — the
agent's output type has no field for a set count, rep count or weight — rather
than by asking it nicely in a prompt.

**Set counts come from the weekly target, not from what you missed.** Using the
shortfall punished consistency: hitting your target exactly produced a week with
nothing in it. The shortfall instead decides what survives when a session won't
fit.

When the week is too tight, things are given up in this order: **volume per
muscle group → hitting every muscle → runs on track → number of sessions.**

Plans are append-only — a revision is a new row pointing at the old one.

Generating one takes about 3 model requests and tens of seconds, so it runs in
the background and the client polls, the same pattern as sync. Asking again
while one is running is refused rather than queued, because a duplicate run
costs quota.

## Writing to Hevy

The only part of this app that changes anything outside it: **propose → diff →
confirm → write → log**, and the propose step never contacts Hevy.

**Hevy has no delete endpoint.** Anything written can only be removed by hand in
the app, so the diff is what makes the write deliberate. It is never optional.

Two callers share that one surface: the routine builder and the Week tab's
per-day send. Accepting a *plan* only records it locally; only the per-day step
writes.
