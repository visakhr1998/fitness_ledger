# Fitness ledger

A single-user training assistant that keeps lifting and running progressing in
parallel, by tracking **effective sets per muscle group against weekly targets**.

Data comes from Hevy (lifts) and Google Health (runs, sleep, resting heart rate)
through local stdio MCP servers. Everything is cached in SQLite and served by
FastAPI to a React dashboard.

**Current state: v0.3.** v0.4 is hosting and scheduled runs.

## Quickstart

```bash
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -e ".[dev]"
cp .env.example .env          # fill in the two MCP server paths

./.venv/Scripts/python.exe -m fitness_ledger.cli doctor   # are both sources reachable?
./.venv/Scripts/python.exe -m fitness_ledger.cli sync     # backfill, then incremental
./.venv/Scripts/python.exe -m fitness_ledger.cli serve    # dashboard on :8000
```

This repo holds **no credentials**. It shells out to two MCP servers that already
own them — the Hevy API key stays in hevy-mcp's dotenv, the Google OAuth token in
the health server's token file. `.env` here holds paths and tunables only, and
`data/` is git-ignored.

## The volume model

```
volume[muscle] = Σ(working sets where muscle is primary)
               + secondary_weight × Σ(working sets where muscle is secondary)

frequency[muscle] = distinct days in the window with volume[muscle] > 0
```

Conventions, all in `.env` because they are conventions rather than laws:

| Setting | Default | Meaning |
|---|---|---|
| `SECONDARY_WEIGHT` | `0.5` | Credit when the muscle is a secondary mover |
| `COUNT_WARMUP_SETS` | `false` | Warmups excluded from effective sets |
| `LOCAL_UTC_OFFSET_MINUTES` | `120` | Which day a late-evening session lands on |
| `WEEK_STARTS_ON` | `0` | 0 = Monday |
| `REP_RANGE_LOW` / `HIGH` | `6` / `10` | Default double-progression range |

Resolved decisions: warmups don't count (one session logged 27 sets, only 16
working); a muscle listed as both primary and secondary on one exercise counts
once; targets scale to the window, so four weeks of volume is compared against
four weeks of target. Sets whose template isn't in the local catalog are
**skipped and reported** under "unmapped exercises", never silently dropped.

**Two window vocabularies, deliberately distinct.** `last-N-weeks` means N
*complete* weeks, excluding the part-finished current one — correct for trailing
baselines. `last-N-days` includes today — correct for recency panels. Mixing
them once hid the newest five days of data and looked like a failed sync.

## Aerobic Efficiency Index

One number for whether running is improving: **grade-adjusted metres per heart
beat**. Higher is better.

```
cost(g)  = 155.4g⁵ − 30.4g⁴ − 43.3g³ + 46.3g² + 19.5g + 3.6   (Minetti)
adjusted = Σ segment_distance × cost(g)/cost(0)
beats    = Σ heart_rate × minutes
AEI      = adjusted_metres / beats
```

**Grade is binned over 25 m, never per GPS sample.** Raw 1 Hz altitude put the
95th-percentile grade at 41% on a flat run, and Minetti's curve is asymmetric —
climbing costs more than descending saves — so symmetric noise biases the result
*upward* instead of cancelling. Binning cut the inflation from 23% to 10%.

Because that choice moves AEI by ~10%, every stored value carries a
`method_version`; changing the method recomputes from saved 25 m bins without
re-downloading 1.2 MB of GPS per run. Runs the data can't support are excluded
with a stated reason — a 2-second mis-tap, or a session summarised as 936 m whose
GPS track held 66 m.

AEI is only comparable between runs of similar length.

## Model provider

`ask` and the chat dock need a model; **nothing else does**. Because the model
never computes — it picks a tool and phrases the dict that comes back — a small
free model does this job about as well as a frontier one.

| `LLM_PROVIDER` | Also set | Cost | Notes |
|---|---|---|---|
| `gemini` | `GEMINI_API_KEY` | free tier | [AI Studio](https://aistudio.google.com/apikey) key, no card. Default. |
| `ollama` | *(nothing)* | free | Local; `ollama pull qwen3:4b`. Nothing leaves the machine. |
| `anthropic` | `ANTHROPIC_API_KEY` | paid | Also picks up an `ant auth login` profile. |
| `openai-compatible` | `LLM_BASE_URL`, `LLM_MODEL` | varies | Groq, OpenRouter, self-hosted vLLM. |

Leave `LLM_PROVIDER` blank to auto-select whichever key exists, preferring the
free one. `LLM_MODEL` overrides the model for any provider.

- **The model must support tool calling.** The loop is entirely tool calls, so a
  model without it fails outright rather than degrading. `gemma3` has no tool
  support in Ollama — use `qwen3` locally.
- **Thinking is off on Gemini by default** (`LLM_REASONING_EFFORT=none`).
  Reasoning tokens are charged against the output ceiling without appearing in
  the reply — 554 measured on an 11-token prompt — which truncated answers
  mid-sentence.
- **Free tiers are usually paid for with your data.** Outside the EEA, UK and
  Switzerland, Gemini's free tier lets Google use prompts for product
  improvement, with human review in scope. The dock sends derived metrics *and
  your questions*. Use `ollama` if that matters.

## Commands

| Command | What it answers |
|---|---|
| `doctor` | Are both sources reachable, what's cached, which model provider? |
| `sync [--full] [--weeks N]` | Pull Hevy + Google Health into SQLite |
| `volume [--window]` | Every muscle group vs target, with a coverage bar |
| `muscle <name> [--window]` | "How much chest volume did I do last week?" |
| `neglected [--window]` | "What have I been neglecting?" |
| `trend [--weeks] [--muscle]` | Weekly volume over time |
| `progress <exercise>` | Estimated 1RM per session |
| `progression` | Double-progression state per main lift |
| `runs [--window]` · `health [--window]` | Run log; sleep, resting HR, steps |
| `insights` | Run the detection rules |
| `targets [--set chest=16]` · `exercises <query>` | Configuration and catalog |
| `serve [--port]` | Run the dashboard |
| `export [--out]` | Dump every table to JSON, for the move off SQLite |
| `ask "<question>"` | Natural-language Q&A |

Windows accept `this-week`, `last-week`, `last-4-weeks`, `last-30-days`,
`last-3-months`, `2026-07`, or `2026-07-01:2026-07-31`.

## Frontend

`frontend/` is a Vite + React app built into `src/fitness_ledger/web/dist`, which
**is committed** so a clone runs with Python alone and a container image needs no
Node stage.

```bash
cd frontend && npm install && npm run build   # required after any frontend change
npm run dev                                   # :5173, proxies /api to :8000
```

Two sections — **Run** (AEI, distance, heart rate, vitals with Karvonen zones)
and **Gym** (muscle radar, tonnage, per-exercise 1RM and progression) — each with
one time-horizon filter scoping everything below it.

Styled after Google Health, whose important property is what it avoids: every
card is one metric in one colour. Measured as a categorical palette its hues fail
outright (cyan to teal is ΔE 12.1 against a floor of 15), but it never puts two
series in one chart. So: **one accent, one series per chart, facet instead of
overlay.** Every chart ships a table twin. Light and dark are both validated, and
the toggle beats the OS setting in both directions.

## Insight rules

Run on demand (`insights`, `/api/insights`, or the dashboard). All are
**surfaced, never acted on**.

| Rule | Fires when |
|---|---|
| `volume_drop` | A muscle group >25% below its trailing 4-week average |
| `coverage_gap` | Below the frequency target two weeks running |
| `stall` | No load *or* rep increase on a main lift across 3 sessions |
| `progression_ready` | Every working set at the top of the rep range |
| `recovery_flag` | 3-night sleep mean below personal baseline |

Coverage gaps are graded — a muscle that was trained and stopped is a warning,
one that never appears is info — otherwise the panel fills with identical
complaints and stops being read. The recovery rule never prescribes: it reports
the correlation from the user's own history and stops, and a test asserts the
output contains no directive language.

`drift` is **deliberately not implemented**. It compares logged sessions against
*planned* ones, and Plan/Availability still don't exist.

## Double progression

State per exercise: working weight, reps achieved at it, and whether every set
reached the top of the rep range. Load steps come from the equipment (barbell
2.5 kg, dumbbell 2 kg, machine 5 kg).

**Rep ranges are configuration, not inference.** A logged set records what was
done, never what was intended, so a heavy top set plus a back-off is
indistinguishable from a failed range attempt. Only sets at the session's **top
weight** count toward the decision, so back-off sets never block progression.
Override per exercise via `PUT /api/rep-ranges`.

## API

| Endpoint | |
|---|---|
| `GET /api/dashboard` | Everything the front page needs, one round trip |
| `GET /api/run` · `/api/gym` · `/api/vitals` | The two v0.3 sections and vitals |
| `GET /api/volume` · `/api/muscle/{group}` · `/api/trend` | Volume views |
| `GET /api/strength` · `/api/progression` · `/api/exercise/{name}` | Strength state |
| `GET /api/exercises` · `/api/exercises/{id}` | Catalog and per-exercise detail |
| `GET /api/insights` | Detection rules |
| `GET /api/runs` · `/api/health-metrics` | Google Health views |
| `GET/PUT /api/targets` · `/api/rep-ranges` · `/api/settings` | Configuration |
| `POST /api/sync` · `GET /api/sync/status` | Background refresh with progress |
| `POST /api/writeback/propose` · `/{id}/approve` · `GET /api/writeback` | Gated Hevy write-back |
| `POST /api/chat` | The dock |

Endpoints are thin wrappers over `queries.py`, so the API and the CLI can never
disagree about a number.

## Architecture

```
CLI ─┐
     ├─► queries.py ──► volume.py · progression.py · insights.py · aei.py
API ─┘        │              (rules engine: pure, tested, no I/O)
              ▼
          db.py (Repository interface ─► SQLite)
              ▲
          sync.py ──► mcp_client.py ──► Hevy MCP · Google Health MCP

          chat.py ──► queries.py as tools ──► llm.py (provider transports)
```

- **The rules engine has no dependencies.** It takes dataclasses and returns
  dataclasses, so every number is testable without a DB or a network.
- **A repository interface sits between the engine and SQLite**, so the v0.4 move
  to a hosted store is contained rather than a rewrite. `ledger export` makes it
  a load, not a migration.
- **The model never computes.** `chat.py` exposes query functions as tools and
  the prompt forbids arithmetic, so wrong-LLM-maths isn't a failure mode here.
  That is also what makes the provider swappable.
- **Only `sync.py` talks to the MCP servers.** Everything else reads the cache.

## Data source quirks

Found the hard way; each is handled in `sync.py` / `mcp_client.py`:

- **Hevy paginates at 10 items.** A full backfill of ~474 workouts is ~48 calls.
- **Deletions appear only in the workout-event feed.** A deleted workout vanishes
  from `list_workouts` without trace, so incremental sync uses
  `hevy_list_workout_events` or the cache keeps phantom sessions.
- **Google Health truncates at ~25k characters** and appends a notice that breaks
  the JSON. The paginator retries at half the page size.
- **`daily_rollup` rejects any range longer than its page size**, and page sizes
  near 100 fail regardless, so rollups are chunked to 40 days.
- **Pages can be empty and still carry a next-page token.** Treating an empty page
  as end-of-data cut sleep history from 85 nights to 14.
- **Some failures come back as plain text, not errors.** `mcp_client` raises on a
  leading `Error:` so a bad request can't read as "no data".
- **A TCX export is ~1.2 MB and ~1,950 trackpoints**, wrapped as
  `{"tcxData": "<xml>"}`. Fetched once per run; the 25 m bins are what persist.

The Google Health items are filed as issues on that server's own repo.

## Verification

```bash
./.venv/Scripts/python.exe -m pytest      # 202 tests
```

The rules engine is verified against a session small enough to check by hand —
27 logged sets, 11 warmups, 16 working:

| Muscle | Reported | By hand |
|---|---|---|
| chest | 2.0 | 2 primary (chest press) |
| shoulders | 5.0 | 4 primary (press + raises) + 0.5×2 secondary |
| triceps | 4.0 | 2 primary + 0.5×2 + 0.5×2 secondary |
| lats | 3.0 | 2 primary + 0.5×2 secondary |
| upper_back | 3.0 | 2 primary + 0.5×2 secondary |
| biceps | 6.0 | 4 primary + 0.5×2 + 0.5×2 secondary |
| forearms | 2.0 | 0.5×4 secondary |

AEI has a regression test pinning a real run, so a method change can't pass
silently.

## Non-goals

- **Not an autopilot.** Write-back exists but is gated: propose → diff → confirm
  → write → log, and the propose step never contacts Hevy. **Hevy has no delete
  endpoint**, so anything written must be removed by hand in the app.
- **Not medical advice.** Sleep and resting-HR data are surfaced as the user's
  own history, never as a prescription.
- **Not multi-tenant.** One deployment, one person, no accounts, no `user_id`.
  Shareable as source: clone it and point it at your own credentials.

## Next

v0.4 — scheduled plan generation and insight passes, a notification path, storage
off local SQLite, credentials in Secret Manager, deploy behind IAP.

Still open from v0.3: the redistribution algorithm against the priority ranking
(volume per muscle → full-body coverage → runs on track → session count),
availability declaration, and append-only plan history. `drift` waits on those.
