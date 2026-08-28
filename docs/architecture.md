# Architecture

```
CLI ─┐
     ├─► queries.py ──► volume.py · progression.py · insights.py · aei.py
API ─┘        │              (rules engine: plain Python, no I/O)
              ▼
          db.py (repository interface ─► SQLite)
              ▲
          sync.py ──► mcp_client.py ──► Hevy MCP · Google Health MCP

          chat.py ──► queries.py as tools ──► llm.py (provider transports)
          coach/  ──► planning.py (set allocation) ──► assembler ──► Plan
```

Four rules keep this shape:

- **The rules engine takes and returns plain data.** No database, no network, no
  model. Every number can be tested on its own.
- **A repository interface sits between the engine and SQLite,** so swapping the
  storage later is contained. `ledger export` dumps everything to JSON.
- **The model never does maths.** It picks a function to call and describes what
  comes back. That's why the provider is swappable. The coach is the tricky
  case: `planning.py` works out every set count and has no I/O, so it lives with
  the rules engine rather than in `coach/`, which touches the database.
- **`sync.py` is the only thing that *reads* from the MCP servers.** Everything
  else reads the local cache. Two places do call out directly: `doctor` pings
  both servers to check they answer, and approving a Hevy write-back sends the
  routine.

## The coach

An agent that turns your goals, training history and free days into a proposed
week. It's an optional install (`pip install -e ".[coach]"`) because it roughly
triples the dependency tree — ADK declares 25 direct dependencies, which pull
in about 120 packages in total.

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
`planning.py` calculates the numbers. This is enforced by the data structure —
the agent's output type has no field for a set count, rep count or weight.

Set counts come from your **weekly target**, not from what you missed last week.
Using the shortfall punished consistency: hitting your target exactly produced a
week with nothing in it. The shortfall is used instead to decide what survives
when a session won't fit.

When the week is too tight, things are given up in this order: **volume per
muscle group → hitting every muscle → runs on track → number of sessions.**

Plans are append-only. A revision is a new row pointing at the old one, so you
can always see why a week looked the way it did.

## Writing to Hevy

The only part of this app that changes anything outside it. It works as
**propose → diff → confirm → write → log**, and the propose step never contacts
Hevy.

**Hevy has no delete endpoint.** Anything written can only be removed by hand in
the app, so the diff is what makes the write deliberate. It is never optional.

Two places use that one surface: the routine builder and the Week tab's per-day
send. Accepting a *plan* only records it here and writes nothing outside the
app; only the per-day step writes. A test checks that accepting a plan never
reaches `build_routine`.

## API

Every endpoint that reports a *number* is a thin wrapper over `queries.py`, so
the API and CLI can't disagree about a figure. Endpoints that manage state —
goals, plans, availability, write-back — go to the repository directly.

| Endpoint | |
|---|---|
| `GET /api/dashboard` | Everything the front page needs in one request |
| `GET /api/run` · `/api/gym` · `/api/vitals` | The two sections, and vitals |
| `GET /api/volume` · `/api/muscle/{group}` · `/api/trend` | Volume views |
| `GET /api/strength` · `/api/progression` · `/api/exercise/{name}` | Strength state |
| `GET /api/exercises` · `/api/exercises/{id}` | Catalog and per-exercise detail |
| `GET /api/insights` | Warning rules |
| `GET /api/runs` · `/api/health-metrics` | Google Health views |
| `GET/PUT /api/targets` · `PUT /api/rep-ranges` · `GET/PUT /api/settings` | Settings |
| `POST /api/sync` · `GET /api/sync/status` | Refresh, with progress |
| `GET /api/plan` | The saved week |
| `POST /api/plan` · `GET /api/plan/status` | Generate a week, with progress |
| `PUT /api/plan/{id}` | Accept or reject (writes nothing to Hevy) |
| `POST /api/plan/{id}/routine` | Draft a routine from one planned day |
| `GET/POST /api/goals` · `PUT /api/goals/{id}` · `PUT /api/running-target` | Goals and targets |
| `GET/PUT /api/availability` · `DELETE /api/availability/{day}` | Days you can't train |
| `POST /api/writeback/propose` · `/{id}/approve` · `GET /api/writeback` | Hevy write-back |
| `POST /api/chat` | The chat box |
| `GET /health` | Liveness, plus cache counts |
| `GET /` | The built dashboard |

FastAPI also serves interactive docs at `/docs`.

Generating a plan takes about 3 model requests and tens of seconds, so it runs
in the background and you poll for progress — the same pattern as sync. Asking
again while one is running is refused rather than queued, since a duplicate run
costs quota.

## Frontend

`frontend/` is a Vite + React app that builds into `src/fitness_ledger/web/dist`.
Those built files **are committed**, so a clone runs with Python alone.

```bash
cd frontend && npm install && npm run build   # needed after any frontend change
npm run dev                                   # :5173, proxies /api to :8000
```

Three sections: **Run** (efficiency, distance, heart rate, heart-rate zones),
**Gym** (muscle coverage, tonnage, per-exercise strength) and **Week** (the
planned week and the buttons that drive it). Run and Gym have a time filter;
Week doesn't, because a plan is one specific week and filtering it means
nothing.

Charts follow one rule: **one colour, one series per chart.** If you need to
compare, use separate small charts rather than overlaying lines. Every chart
also has a table version. Light and dark are both checked.

## Data source quirks

Each of these is handled in `sync.py` or `mcp_client.py`.

- **Hevy returns 10 items per page.** Downloading ~474 workouts takes ~48 calls.
- **Deleted workouts only show up in the events feed.** They disappear from the
  workout list without trace, so incremental sync reads events instead —
  otherwise the cache keeps sessions you deleted.
- **Google Health cuts responses off at ~25k characters** and appends a notice
  that breaks the JSON. The paginator retries with half the page size.
- **`daily_rollup` rejects date ranges longer than its page size,** and page
  sizes near 100 fail anyway, so rollups are done 40 days at a time.
- **An empty page can still have a next-page token.** Treating empty as "the
  end" once cut sleep history from 85 nights to 14.
- **Some failures come back as ordinary text, not errors.** `mcp_client` treats
  a leading `Error:` as a failure so a bad request can't look like "no data".
- **A GPS export is ~1.2 MB and ~1,950 points.** It's fetched once per run; only
  the 25 m chunks are kept.

The Google Health problems are filed as issues on that server's repo. Filing an
issue doesn't remove the need for the workaround here.
