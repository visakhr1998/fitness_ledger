# CLAUDE.md

Instructions for working in this repo. Keep it current — see
[Updating this file](#updating-this-file).

## What this is

A single-user training assistant that keeps strength and running progressing in
parallel by tracking **volume per muscle group against targets**. Data comes from
Hevy (lifts) and Google Health (runs, sleep, resting HR) via local stdio MCP
servers.

**Current state: v0.3 complete.** v0.1 was the deterministic core (sync, volume
math, CLI); v0.2 added the FastAPI backend, a dashboard, double-progression state
and insight rules; v0.3 split the UI into Run and Gym on React + Vite, added the
Aerobic Efficiency Index, vitals, a coach and chat dock, and approval-gated Hevy
write-back. v0.4 is hosting and scheduled runs. Full plan lives in the roadmap the user supplied — ask for it if
a decision seems to depend on it.

## Design principles — these govern every decision

1. **Advisor, not autopilot.** Never change training without the user. No
   autonomous writes, no silent adaptation, no confidence-threshold auto-actions.
2. **The volume ledger is the core.** Almost every feature is a view over "target
   volume per muscle group vs. actual".
3. **Deterministic where possible, model where necessary.** Volume math,
   frequency counting and redistribution are rules — auditable and testable. The
   model handles judgment, explanation and conversation.
4. **Fixed days, adapted content.** The training week is a template to adjust,
   not rebuild.
5. **Flag, don't act.** Recovery signals and plan drift are surfaced for the user
   to interpret.

When the week is constrained, the priority order is: **volume per muscle group →
full-body coverage → runs on track → session count.** This ranking is the
objective function for redistribution and is the single most important
encoded rule in the system.

## Architecture rules

```
CLI ─┐
     ├─► queries.py ──► volume.py · progression.py · insights.py
API ─┘        │              (rules engine: pure, tested, no I/O)
              ▼
          db.py (Repository interface ─► SQLite)
              ▲
          sync.py ──► mcp_client.py ──► Hevy MCP · Google Health MCP
          chat.py ──► queries.py as tools
             └──► llm.py (transports: anthropic · openai-compatible)
```

- **The rules engine is pure.** `volume.py`, `progression.py` and `insights.py`
  take dataclasses and return dataclasses. No DB, no network, no model. Every
  number must be testable without I/O. Do not import `db` or `mcp_client` there.
- **The model never computes.** `chat.py` exposes query functions as tools and
  the system prompt forbids arithmetic. Never let an LLM do the maths. The coach
  is the hardest case: `planning.py` allocates its set counts and is pure, so it
  sits with the rules engine rather than in `coach/`, which imports the database.
- **Only `sync.py` talks to the MCP servers.** Everything else reads the cache.
- **The API is a thin wrapper over `queries.py`.** No computation in `api.py`, so
  the API and CLI can never disagree about a number.
- **A repository interface sits between the engine and SQLite** so the v0.4 move
  to a persistent backend stays contained. Don't write code that assumes a local
  file path.

## Conventions that are values, not laws

All configurable in `.env`; documented in the README.

| Setting | Default | Meaning |
|---|---|---|
| `SECONDARY_WEIGHT` | `0.5` | Credit when a muscle is a secondary mover |
| `COUNT_WARMUP_SETS` | `false` | Warmups excluded from effective sets |
| `LOCAL_UTC_OFFSET_MINUTES` | `120` | Which day a late-evening session lands on |
| `WEEK_STARTS_ON` | `0` | 0 = Monday |
| `REP_RANGE_LOW` / `HIGH` | `6` / `10` | Default double-progression range |

Resolved decisions worth not re-litigating:

- Warmups don't count toward effective sets (a recent session was 27 logged sets
  but only 16 working).
- A muscle listed as both primary and secondary on one exercise counts once.
- Targets scale to the window: four weeks of volume is compared against four
  weeks of target.
- **Rep ranges are configuration, never inferred.** A logged set records what was
  done, not what was intended — a heavy top set plus a back-off is
  indistinguishable from a failed range attempt. Only sets at the session's top
  weight count toward a progression decision.
- **AEI grade is binned over 25 m, never per sample.** Raw 1 Hz GPS altitude put
  the 95th-percentile grade at 41% on a flat run, and Minetti's curve is
  asymmetric, so symmetric noise biases the result *upward* rather than
  cancelling. Binning cut the inflation from 23% to 10%.
- **`aei.METHOD_VERSION` is part of the value's identity.** The binning choice
  moves AEI ~10%, so a figure computed under a different method is not
  comparable. Change a constant, bump the version; stored runs recompute from
  `run_segments` without re-downloading.
- **One accent, one series per chart.** The reference palette fails the
  categorical validator outright (cyan-to-teal ΔE 12.1, floor 15) but never puts
  two series in one chart. Facet instead of overlaying.
- **The model is swappable because it never computes.** Picking a tool and
  phrasing the dict it returns is an easy job, so a free Gemini Flash or a local
  `qwen3:4b` answers about as well as a frontier model. Tools are declared once
  in Anthropic's schema and translated in `llm.py`; a new provider must never
  mean restating thirteen tool definitions.
- **A provider must support tool calling.** The loop is entirely tool calls, so a
  model without it fails outright rather than degrading. `gemma3` has no tool
  support in Ollama — that is why the local default is `qwen3:4b`.

## Two window vocabularies — do not mix them

This caused a real bug. Keep them separate.

- **`last-N-weeks` = N complete weeks**, excluding the part-finished current one.
  Correct for trailing baselines: an insight rule comparing against a half-done
  week would fire on nothing.
- **`last-N-days` includes today.** Correct for dashboard recency panels.

A dashboard panel on a week window silently hides the newest days and looks like
a failed sync. `tests/test_recency.py` pins both halves — if you change window
semantics, that file should fail.

## Data source quirks

All handled in `sync.py` / `mcp_client.py`; don't rediscover them.

- Hevy paginates at **10 items** for workouts and events. A full backfill of ~474
  workouts is ~48 calls.
- **Deletions only appear in the workout-event feed.** A deleted workout vanishes
  from `list_workouts` without trace, so incremental sync must use
  `hevy_list_workout_events` or the cache keeps phantom sessions.
- Google Health **truncates responses at ~25k characters** and appends a notice
  that breaks the JSON. The paginator retries at half the page size.
- `daily_rollup` **rejects any range longer than its page size**, and page sizes
  near 100 fail regardless — rollups are chunked to 40 days.
- **Pages can be empty and still carry a next-page token.** Treating an empty page
  as end-of-data once cut sleep history from 85 nights to 14.
- **Some failures come back as plain text, not errors.** `mcp_client` raises on a
  leading `Error:` so a bad request can't read as "no data".
- Not every data type supports every action: sleep and resting HR are
  list-only; steps supports `daily_rollup`.
- **A TCX export is ~1.2 MB and ~1,950 trackpoints per run**, returned as
  `{"tcxData": "<xml>"}` -- not raw text. Fetch once per run, store the 25 m
  bins, and recompute from those.
- **Device summaries and GPS tracks disagree.** One session Google Health
  reported as 936 m had a 66 m track; another was a 2-second mis-tap. AEI has
  reliability guards for both, and excluded runs carry a stated reason.
- **A TCX export carries heart rate on roughly 40% of its trackpoints**, at a
  steady ~2.5 s cadence against 1 Hz GPS. That is the watch's sampling rate, not
  a truncated export — do not "fix" it upstream. Any code that sums over time
  must measure the gap since the last HR-bearing sample, not the previous sample.

## Report upstream bugs upstream

Both MCP servers are ours:

- `github.com/visakhr1998/google-health-mcp-v1`
- `github.com/visakhr1998/hevy-mcp`

**Whenever data one of them returns is wrong, malformed, or contradicts its own
schema, open an issue on that repo** — as well as working around it here. Four
are already filed against the health server (issues #3–#6): plain-text failures
instead of `isError`, truncated responses returned as invalid JSON, empty pages
carrying a `nextPageToken`, and `daily_rollup` coupling range to page size.

Two rules, both learned the hard way:

- **Confirm it is the server, not our usage, and not the device.** A candidate
  was dropped because `INVALID_DATA_POINT_FILTER_DATA_TYPE_RESTRICTION` was never
  proven to be a server fault rather than a bad request. Sparse heart rate in a
  TCX looks like a truncated payload and is really the watch's sampling cadence.
  An unconfirmed report costs more than a gap.
- **Record the workaround here too**, under *Data source quirks*, so the next
  session does not rediscover the quirk while waiting for a fix. Every quirk in
  that list is handled in `sync.py` or `mcp_client.py`; a filed issue does not
  remove the need for the guard.

## Working here

```bash
./.venv/Scripts/python.exe -m pytest              # 466 tests, keep them green
cd frontend && npm run build                      # required after any frontend change
./.venv/Scripts/python.exe -m fitness_ledger.cli doctor
./.venv/Scripts/python.exe -m fitness_ledger.cli sync
./.venv/Scripts/python.exe -m fitness_ledger.cli serve   # dashboard on :8000
```

Use the repo venv, not the system Python. The package is installed editable.
`uvicorn` runs without reload, so **restart the server after Python changes**.
The frontend is a **Vite build** -- editing `frontend/src` changes nothing until
`npm run build` writes `src/fitness_ledger/web/dist`, which FastAPI serves and
which **is committed** so a clone runs with Python alone. Vite is pinned to 6
because `create-vite@latest` needs Node 20.12+ and this machine has 20.11.

Expectations for changes:

- **Any rules-engine change needs unit tests.** Hand-computable fixtures, no I/O.
  When touching the volume maths, verify against a real session you can read off
  Hevy and check by hand — that is how the original implementation was validated.
- **Never fabricate a result.** Don't claim a commit, a test run, or a passing
  check that didn't happen. Run it, then report what it actually said.
- **Before writing any chart code**, load the `dataviz` skill and run its palette
  validator. Never eyeball colourblind safety. Every chart needs a table twin.

## The coach's model

`GEMINI_MODEL=gemini-3.6-flash`, decided 2026-08-08 after testing every free
option against the same eval fixture -- three weeks with no back work.

- `gemini-3.5-flash-lite` planned **two squats, 4 sets**, no back, twice. Too
  weak, whatever its rate limit.
- `gemini-3.5-flash` plans well on **20 requests a day**: about two runs.
- `gemini-2.5-flash` returns **404, "no longer available to new users"**.
- `gemini-3.6-flash` planned 4 sessions and 64 sets, opening with a Lat
  Pulldown. Stable across runs.

**Nothing may assume a model id.** One became unavailable mid-project; the
provider indirection in `llm.py` is what made that survivable.

**A fallback provider is supported, and off by default.** Set the three
`COACH_FALLBACK_*` variables and a 429 from the primary re-runs the whole
pipeline on a second, OpenAI-compatible provider -- intended as DeepSeek through
OpenRouter. Only a quota refusal triggers it; a malformed proposal is a real
fault and retrying it elsewhere would hide the cause behind a second bill. The
result carries `planned_by`, so a week produced by the backstop is never silent.

**Coach evals run about monthly.** No free model has both the quality to plan
and the quota to sustain a full pass (~54 requests). If they need to run more
often, the answer is a paid provider -- DeepSeek is costed at roughly $0.0015
per plan in `UNDERSTANDING.md` -- and never a looser assertion.

## Repo conventions

- **Every change goes through a pull request.** Create a branch → make commits →
  open a PR → review → merge. **Never commit straight to `main`**, even for a
  one-line docs fix. `origin` is `github.com/visakhr1998/fitness_ledger`. Only
  commit when asked.

  This replaces an earlier "commit directly to main, no remote, no PR flow" rule
  that predated the remote. If a commit lands on `main` by mistake, recover in
  this order: branch at that commit, verify the branch holds it, confirm a clean
  tree, *then* reset `main` to `origin/main`.
- **No secrets in this repo, ever.** The Hevy API key lives in hevy-mcp's own
  dotenv; the Google OAuth token in the health server's token file. `.env` here
  holds paths and tunables only. `data/` and `*.db` are git-ignored — the
  database holds personal training data.
- Non-goals to preserve: not an autopilot, not medical advice, not multi-tenant.
  Source-shareable, not a shared service.

## Don't do these yet

- **Write-back must stay approval-gated.** propose → diff → confirm → write →
  log. The propose step never calls Hevy. **Hevy has no delete endpoint**, so an
  accidental write can only be undone by hand in the app; never remove the diff.
  Two callers now share that surface — `WriteBack.tsx`'s routine builder and the
  Week tab's per-day send — through `RoutineDiff.tsx`. Approving a *plan* is a
  ledger state change and must never write; only the per-day step does, and
  `tests/test_plan_api.py` asserts the plan decision never reaches
  `build_routine`.
- **The `drift` insight rule is unblocked but not written.** It compares logged
  sessions against *planned* ones, which needed `Plan` and `Availability` to
  exist. Both now do, so the blocker is gone — the rule itself is still to do.
- **ADK is in, for the coach only.** The earlier "don't add ADK yet" line was
  written about `chat.py`, whose 48-line stateless loop genuinely does not need
  a harness, and that is still true — `chat.py` is untouched and stays that way.
  The coach is a different problem: multi-agent delegation, session state and
  week-over-week continuity a stateless call cannot express. It is an **optional
  extra** (`pip install -e ".[coach]"`), so nothing else depends on its 25
  dependencies, and the dashboard must keep working without it.
- **The agent never emits a set count.** It chooses exercises and days;
  `planning.py` computes every number from the tool-reported deficit. This is
  enforced by shape, not by instruction — `WeekProposal` has nowhere to put a
  set count, rep count or weight. Do not add one, and do not let the assembler
  accept a number the agent supplied.
- **The chat dock needs *a* model provider, not a specific one.** Gemini's free
  tier is the default; `ollama` runs it locally. Without any provider the dock
  says so; the rest of the dashboard must never depend on one.

## Keeping UNDERSTANDING.md current

`UNDERSTANDING.md` at the repo root is the handoff document: it exists so a
session with no prior context can pick this project up. Much of what it holds —
why ADK was rejected and then accepted, which alternatives were priced and
dropped, what the numbers in a bug actually were — lives nowhere else, and is
lost the moment a conversation ends.

**It is git-ignored.** This repo is public and the document carries personal
figures and absolute paths from the author's machine. Consequences to keep in
mind: a fresh clone does not have it, so it is a handoff document for *this*
machine; it has no version history, so a bad edit is unrecoverable; and it is
not backed up by pushing. Do not re-add it to git, and do not move its content
into a tracked file to work around that.

**Update it in the same PR as the change**, not afterwards, whenever you:

- finish a milestone or a numbered day of a multi-day plan;
- make or reverse an architectural decision, including the alternatives you
  rejected and why;
- find a bug worth remembering, or fix one whose cause was non-obvious;
- change the stack, a dependency, a command, or an environment variable;
- discover a gotcha, a data-source quirk, or a deliberate workaround;
- agree on a next step, or park work that is half-finished.

Rules for editing it:

- **Ground every statement in code you have read.** Mark inferences as
  **[inference]** and unknowns as **UNKNOWN**. A confident wrong line there
  misleads every future session, which is worse than a gap.
- **Keep the two top-level sections**: `# Requirements` (product prose) and
  `# Tech details`.
- **Correct stale claims rather than appending to them.** The README once
  contradicted itself for two versions because nobody deleted the old paragraph.
- Update the test count and the commit reference in the header when they move.

## Updating this file

Run `/update-claude-md` (defined in `.claude/commands/`). It re-derives this file
from the current state of the repo. Run it after finishing a version milestone,
changing a convention, or discovering a new data-source quirk.
