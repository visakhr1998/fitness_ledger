# Fitness ledger — v0.3

Tracks **effective sets per muscle group against weekly targets**, over Hevy
(lifts) and Google Health (runs, sleep, resting heart rate).

- **v0.1** — the deterministic core: sync, the volume calculation, a CLI.
  *Done when "how much chest volume did I do last week" returns a right answer.*
- **v0.2** — the ledger you can look at: FastAPI backend, web dashboard, double
  progression state, and the insight rules.
  *Done when opening the app tells you what you're neglecting without asking.*
- **v0.3** — Run and Gym as separate sections on React + Vite, the Aerobic
  Efficiency Index, vitals with heart-rate zones, a coach, a chat dock, and
  approval-gated Hevy write-back.

v0.4 is hosting and scheduled runs.

```bash
python -m fitness_ledger.cli serve      # dashboard on http://127.0.0.1:8000
```

## Aerobic Efficiency Index

One number for whether running is improving: **grade-adjusted metres travelled
per heart beat**. Higher is better.

```
cost(g)  = 155.4g⁵ − 30.4g⁴ − 43.3g³ + 46.3g² + 19.5g + 3.6   (Minetti)
gap      = cost(g) / cost(0)
adjusted = Σ segment_distance × gap
beats    = Σ heart_rate × minutes
AEI      = adjusted_metres / beats
```

**Grade is measured over 25 m distance bins, not per GPS sample.** That is not a
refinement, it is the difference between a number and noise: raw 1 Hz altitude
put the 95th-percentile grade at 41% on a flat run, and because Minetti's curve
is asymmetric — climbing costs more than descending saves — symmetric error
biases the result *upward* instead of cancelling. Binning cut the inflation from
23% to 10%.

Because that choice moves AEI by roughly 10%, every stored value carries a
`method_version`. Change the method and stored runs recompute from the saved
25 m bins, without re-downloading 1.2 MB of GPS per run.

Runs are excluded when the data cannot support the metric — a 2-second mis-tap,
or a session summarised as 936 m whose GPS track held only 66 m. Excluded runs
appear with the reason rather than vanishing.

## Frontend

`frontend/` is a Vite + React app built into `src/fitness_ledger/web/dist`, which
is committed so a clone runs with Python alone.

```bash
cd frontend && npm install && npm run build   # after any frontend change
npm run dev                                    # port 5173, proxies /api to :8000
```

Styled after Google Health. The reference's important property is what it
avoids: every card is one metric in one colour. Measured as a categorical
palette its hues fail outright — cyan and teal separate by only ΔE 12.1 in
normal vision against a floor of 15 — but it never puts two series in one chart.
So the app follows the same rule: **one accent, one series per chart, facet
instead of overlay**. Every chart still ships a table twin.

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
| `insights` | Run the detection rules |
| `progression` | Double-progression state per main lift |
| `serve [--port]` | Run the dashboard |
| `export [--out]` | Dump every table to JSON, for the eventual move off SQLite |
| `ask "<question>"` | Natural-language Q&A (needs a model provider — see below) |

Windows accept `this-week`, `last-week`, `last-4-weeks`, `last-30-days`,
`2026-07`, or `2026-07-01:2026-07-31`. `last-N-weeks` means N **complete** weeks,
excluding the part-finished current one, so trailing averages aren't diluted.

## Choosing a model provider

`ask` and the chat dock need a model; **nothing else does**. Because the model
never computes — it picks a tool and phrases the dict that comes back — a small
free model does this job about as well as a frontier one. Set `LLM_PROVIDER` in
`.env`:

| Provider | Set | Cost | Notes |
|---|---|---|---|
| `gemini` | `GEMINI_API_KEY` | free tier | Key from [AI Studio](https://aistudio.google.com/apikey), no card. Default. |
| `ollama` | *(nothing)* | free | Local. `ollama pull qwen3:4b` first. Nothing leaves the machine. |
| `anthropic` | `ANTHROPIC_API_KEY` | paid | Best prose; also picks up an `ant auth login` profile if no key is set. |
| `openai-compatible` | `LLM_BASE_URL`, `LLM_MODEL` | varies | Groq, OpenRouter, self-hosted vLLM. |

Leave `LLM_PROVIDER` blank to auto-select whichever key exists, preferring the
free one. `LLM_MODEL` overrides the model for any provider.

Two things worth knowing before picking:

- **The model must support tool calling.** The whole loop is 13 tool calls; a
  model without it doesn't degrade, it fails. `gemma3` has no tool support in
  Ollama — use `qwen3` locally.
- **Thinking is disabled on Gemini by default** (`LLM_REASONING_EFFORT=none`).
  Reasoning tokens are charged against the output ceiling without appearing in
  the reply — measured 554 of them on an 11-token prompt — which cut answers off
  mid-sentence. The model isn't allowed to reason about numbers here, so the
  budget is better spent on the answer.
- **Free tiers are usually paid for with your data.** Outside the EEA, UK and
  Switzerland, Gemini's free tier permits Google to use prompts for product
  improvement, with human review in scope. The dock sends derived training
  metrics *and your questions*. Use `ollama` if that matters to you.

## Insight rules

Run on demand (`insights`, `/api/insights`, or the dashboard); scheduled runs and
persistence arrive at v0.4. All are **surfaced, never acted on**.

| Rule | Fires when |
|---|---|
| `volume_drop` | A muscle group is >25% below its trailing 4-week average |
| `coverage_gap` | Below the frequency target two weeks running |
| `stall` | No load *or* rep increase on a main lift across 3 sessions |
| `progression_ready` | Every working set at the top of the rep range |
| `recovery_flag` | 3-night sleep mean below personal baseline |

`drift` from the plan is **deliberately not implemented**: it compares logged
sessions against *planned* ones, and Plan/Availability don't exist until v0.3.
Approximating it from habitual training days would invent a signal rather than
measure one.

Two design points worth keeping:

- **Coverage gaps are graded.** A muscle that was being trained and stopped is a
  warning; one that never appears at all is info. Otherwise the panel fills with
  identical shouts about abductors every week and stops being read.
- **The recovery rule never prescribes.** It reports the correlation from the
  user's own history — "on your 9 previous training days after a short night you
  averaged 14 working sets, against 15 otherwise" — and stops there. A test
  asserts the output contains no directive language.

## Double progression

State per exercise: the working weight, reps achieved at it, and whether every
set reached the top of the rep range. Load steps come from the equipment
(barbell 2.5 kg, dumbbell 2 kg, machine 5 kg).

**Rep ranges are configuration, not inference.** A logged set records what was
done, never what was intended, so a heavy top set followed by a back-off is
indistinguishable from a failed range attempt. The default is `REP_RANGE_LOW`–
`REP_RANGE_HIGH` (6–10), overridable per exercise via `PUT /api/rep-ranges`.
Only sets at the session's **top weight** count toward the decision, so back-off
sets never block progression.

## Dashboard

Single self-contained HTML page served by FastAPI — no build step, no npm, which
for one user beats a React toolchain. Charts are hand-built SVG.

- Estimated 1RM per lift over time (primary), volume vs target per muscle group,
  weekly volume, insight cards, progression, runs and recovery tables.
- Categorical palette **validated** with the dataviz validator in both modes, not
  eyeballed. Light mode has three slots under 3:1 contrast, so direct endpoint
  labels and a table view are mandatory — every chart has a table twin.
- One filter row scoping everything below it; never per-chart filters.
- Light and dark are both selected palettes; the toggle beats the OS setting in
  both directions.

## API

| Endpoint | |
|---|---|
| `GET /api/dashboard` | Everything the front page needs, one round trip |
| `GET /api/volume?window=` · `/api/muscle/{group}` | Volume views |
| `GET /api/trend` · `/api/strength` · `/api/progression` | Series and state |
| `GET /api/insights` | Detection rules |
| `GET /api/runs` · `/api/health-metrics` | Google Health views |
| `GET/PUT /api/targets` · `PUT /api/rep-ranges` | Configuration |
| `POST /api/sync` | Explicitly triggered refresh |

Endpoints are thin wrappers over `queries.py`, so the API and the CLI can never
disagree about a number.

## Architecture

```
CLI ─┐
     ├─► queries.py ──► volume.py · progression.py · insights.py
API ─┘        │              (rules engine: pure, tested, no I/O)
              ▼
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

- **Not an autopilot.** Write-back exists but is gated: propose → diff → confirm
  → write → log, and the propose step never contacts Hevy. Hevy has no delete
  endpoint, so anything written must be removed by hand in the app.
- **Not medical advice.** Sleep and resting-HR data are surfaced as the user's own
  history, never as a prescription, and the app has nothing to say about training
  while ill beyond showing past patterns.
- **Not multi-tenant.** One deployment, one person, no accounts, no `user_id`.
  Shareable as source: clone it and point it at your own credentials.

## Next

v0.4 — scheduled weekly plan generation, a scheduled insight pass, drift
detection, a notification path, storage moved off local SQLite, credentials in
Secret Manager, and a deploy behind IAP. `ledger export` exists so that move is
a load rather than a rewrite.

Still outstanding from v0.3: the redistribution algorithm against the priority
ranking, availability declaration, and append-only plan history. The `drift`
insight rule waits on those, since it compares against *planned* sessions.
