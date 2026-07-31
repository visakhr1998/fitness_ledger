# Fitness ledger — v0.1

Tracks **effective sets per muscle group against weekly targets**, over Hevy
(lifts) and Google Health (runs, sleep, resting heart rate).

v0.1 is the deterministic core: sync, the volume calculation, and a CLI to query
it. No planning, no write-back, no web app, no ADK — those arrive at v0.2–v0.4.

> **Done when:** "how much chest volume did I do last week" returns a right answer.
> It does, and the number is verified by hand against a known session (see
> [Verification](#verification)).

## The volume model

```
volume[muscle] = Σ(working sets where muscle is primary)
               + secondary_weight × Σ(working sets where muscle is secondary)

frequency[muscle] = count of distinct days in the window with volume[muscle] > 0
```

Conventions, all configurable in `.env` because they are conventions rather than
laws:

| Setting | Default | Meaning |
|---|---|---|
| `SECONDARY_WEIGHT` | `0.5` | Credit for a set where the muscle is a secondary mover |
| `COUNT_WARMUP_SETS` | `false` | Warmups are excluded from effective sets |
| `LOCAL_UTC_OFFSET_MINUTES` | `120` | Decides which day a late-evening session lands on |
| `WEEK_STARTS_ON` | `0` | 0 = Monday |

Three decisions the plan left open, resolved here and worth revisiting with data:

- **Warmups don't count.** Standard practice for effective-set counting, and it
  matters a lot: a recent session logged 27 sets, of which only 16 were working
  sets. Flip `COUNT_WARMUP_SETS` to compare.
- **A muscle listed as both primary and secondary on one exercise counts once.**
- **Targets scale to the window.** A four-week window is compared against four
  weeks of target, not one. Windows shorter than a week are not scaled down.

Muscle groups Hevy reports that aren't strength-volume targets (`cardio`,
`full_body`, `other`, `neck`) are excluded. Sets whose exercise template isn't in
the local catalog are **skipped and reported** under "unmapped exercises" rather
than silently dropped.

## Setup

```bash
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -e ".[dev]"
cp .env.example .env    # then fill in the two MCP server paths
```

This repo holds **no credentials**. It shells out to two stdio MCP servers that
already own them — the Hevy API key stays in hevy-mcp's own dotenv, and the
Google OAuth token stays in the health server's token file. `.env` here contains
paths and tunables only.

```bash
./.venv/Scripts/python.exe -m fitness_ledger.cli doctor   # check both sources
./.venv/Scripts/python.exe -m fitness_ledger.cli sync     # backfill, then incremental
```

## Commands

| Command | What it answers |
|---|---|
| `doctor` | Are both data sources reachable, and what's cached? |
| `sync [--full] [--weeks N]` | Pull Hevy + Google Health into SQLite |
| `volume [--window]` | Every muscle group vs target, with a coverage bar |
| `muscle <name> [--window]` | "How much chest volume did I do last week?" |
| `neglected [--window]` | "What have I been neglecting?" |
| `trend [--weeks] [--muscle]` | Weekly volume over time |
| `progress <exercise>` | "Am I progressing on bench?" — estimated 1RM per session |
| `runs [--window]` | Run log with pace |
| `health [--window]` | Sleep, resting HR, steps per day |
| `targets [--set chest=16]` | Show or adjust weekly targets |
| `exercises <query>` | Search the exercise catalog |
| `ask "<question>"` | Natural-language Q&A (needs `ANTHROPIC_API_KEY`) |

Windows accept `this-week`, `last-week`, `last-4-weeks`, `last-30-days`,
`2026-07`, or `2026-07-01:2026-07-31`. `last-N-weeks` means N **complete** weeks,
excluding the part-finished current one, so trailing averages aren't diluted.

## Architecture

```
CLI  ──►  queries.py  ──►  volume.py   (rules engine: pure, tested, no I/O)
             │                  ▲
             ▼                  │
          db.py (Repository interface ─► SQLite)
             ▲
             │
          sync.py  ──►  mcp_client.py  ──►  Hevy MCP · Google Health MCP

          chat.py  ──►  queries.py as tools (model explains, never computes)
```

- **The rules engine has no dependencies.** `volume.py` takes dataclasses and
  returns dataclasses, so every number can be tested without a DB or a network.
- **A repository interface sits between the engine and SQLite**, so moving to
  Firestore or a mounted volume at v0.4 is contained rather than a rewrite.
- **The model never computes.** `chat.py` exposes the query functions as tools and
  the system prompt forbids arithmetic. Wrong-LLM-maths isn't a failure mode here.
- **Only `sync.py` talks to the MCP servers.** Everything else reads the cache.

## Data source quirks

Both were found the hard way; each is handled in `sync.py`/`mcp_client.py`:

- **Hevy paginates at 10 items** for workouts and events. A full backfill of 474
  workouts is ~48 calls — fine as a one-off, incremental after that.
- **Deletions are only visible in the workout-event feed.** A deleted workout just
  vanishes from `list_workouts`, so incremental sync goes through
  `hevy_list_workout_events` or the cache would keep phantom sessions forever.
- **Google Health truncates responses at ~25k characters** and appends a notice
  that breaks the JSON. The paginator retries at half the page size; sleep points
  carry full stage arrays and settle around 2 per page.
- **`daily_rollup` rejects any range longer than its page size**, and page sizes
  near 100 fail regardless — so rollups are chunked into 40-day windows.
- **Pages can be empty and still carry a next-page token.** Treating an empty page
  as end-of-data silently cut the sleep history from 85 nights to 14.
- **Some failures come back as plain text, not errors.** `mcp_client` raises on a
  leading `Error:` so a bad request can't read as "no data".

## Verification

The 28 July session is small enough to check by hand: 27 logged sets, 11 of them
warmups, 16 working. The ledger reports 16 working sets for that week and:

| Muscle | Reported | By hand |
|---|---|---|
| chest | 2.0 | 2 primary (chest press) |
| shoulders | 5.0 | 4 primary (press + raises) + 0.5×2 secondary |
| triceps | 4.0 | 2 primary + 0.5×2 + 0.5×2 secondary |
| lats | 3.0 | 2 primary + 0.5×2 secondary |
| upper_back | 3.0 | 2 primary + 0.5×2 secondary |
| biceps | 6.0 | 4 primary + 0.5×2 + 0.5×2 secondary |
| forearms | 2.0 | 0.5×4 secondary |

Run the suite with `./.venv/Scripts/python.exe -m pytest`.

## Non-goals

- **Not an autopilot.** v0.1 is read-only; nothing writes to Hevy. When write-back
  arrives at v0.3 it stays gated on explicit approval.
- **Not medical advice.** Sleep and resting-HR data are surfaced as the user's own
  history, never as a prescription, and the app has nothing to say about training
  while ill beyond showing past patterns.
- **Not multi-tenant.** One deployment, one person, no accounts, no `user_id`.
  Shareable as source: clone it and point it at your own credentials.

## Next

v0.2 — FastAPI + web dashboard, double-progression state per exercise, insight
rules surfaced in the UI. The rules engine and query layer are already the shape
the API needs.
