# UNDERSTANDING

Handoff document. Written 2026-08-05 against commit `6fad80c`. Everything below
is grounded in files read directly; inferences are marked **[inference]** and
gaps are marked **UNKNOWN**.

---

# Requirements

## What this is, and who it is for

`fitness_ledger` is a **single-user training assistant**. One person, one
deployment, no accounts, no `user_id` anywhere. It is shareable as source — you
clone it and point it at your own credentials — but it is not a service.

The problem it solves: someone training for both strength and running has no
single place that answers *"am I actually training everything enough?"*. Hevy
records lifts, Google Health records runs and sleep, and neither knows about the
other. Volume per muscle group drifts, a muscle group quietly stops being
trained, and you find out months later.

So the core abstraction is a **ledger of effective sets per muscle group against
a weekly target**. Almost every feature is a view over "target vs actual". The
running side has an equivalent single number, the Aerobic Efficiency Index.

## Design principles that govern everything

These are stated in `CLAUDE.md` and are enforced in code and tests:

1. **Advisor, not autopilot.** Nothing changes training without the user. No
   autonomous writes, no silent adaptation.
2. **The volume ledger is the core.** Most features are views over it.
3. **Deterministic where possible, model where necessary.** Volume maths,
   frequency counting, progression and grade adjustment are rules — auditable,
   pure, unit-tested. The model handles judgement, explanation, conversation.
4. **Fixed days, adapted content.** The training week is a template to adjust.
5. **Flag, don't act.** Recovery signals are surfaced, never prescribed.

When the week is constrained, the priority order is **volume per muscle group →
full-body coverage → runs on track → session count**. `CLAUDE.md` calls this the
single most important encoded rule in the system. It is currently written into
the coach's instruction but **not yet enforced by a scorer** — that is planned
work, not done work.

## Current state

**Working and merged (`main`, 290 tests passing):**

- Sync from Hevy and Google Health into local SQLite (477 workouts cached).
- The volume/frequency rules engine, double-progression state, five insight rules.
- CLI with ~17 commands.
- FastAPI backend, 27 endpoints, serving a React dashboard with Run and Gym
  sections.
- Aerobic Efficiency Index with grade adjustment and reliability guards.
- Vitals: Karvonen zones, Tanaka max HR, Mifflin-St Jeor BMR.
- A chat dock that answers questions via tool calls, provider-swappable
  (Gemini free tier by default; Ollama, Anthropic, any OpenAI-compatible server).
- Approval-gated Hevy write-back: propose → diff → confirm → write → log.
- Coach agent (ADK), days 1–4 of a 10-day plan: goals, running target,
  availability, tool layer, deterministic context reader, and a root agent that
  produces a whole week with a rationale and stated trade-offs.

**In progress — the coach, days 5–10 of the plan.** The approved plan lives at
`C:\Users\visak\.claude\plans\woolly-strolling-church.md` (outside the repo).
Remaining: the `Plan` entity and assembler with deterministic set allocation
(day 5), splitting strength/running planners and `previous_plan` continuity
(6–7), fixture weeks and eval harness (8–9), the priority scorer and wiring
approval into Hevy write-back (10).

**Not started:** the redistribution algorithm against the priority ranking, the
`drift` insight rule (blocked on planned sessions existing), and all of v0.4 —
hosting, scheduled runs, storage off local SQLite, secrets in a manager.

**Never happened, deliberately:** nothing has ever been written to the user's
Hevy account. Three write-back proposals exist in `writeback_log`, all with
status `proposed` and `hevy_id: None`. The approve path has never been fired
against live Hevy.

## History, in order, with reasoning

**v0.1 — the deterministic core** (`1063faa`). Sync, the volume calculation, a
CLI. Done when *"how much chest volume did I do last week"* returned a right
answer. Everything since is a view over this.

**v0.2 — the ledger you can look at** (`ff0235e`). FastAPI backend, a single
self-contained HTML dashboard, double-progression state, insight rules.

**A real bug fixed** (`234b8c6`). Dashboard panels used complete-week windows,
which silently hid the newest five days and looked like a failed sync. This
produced the **two window vocabularies** rule that still governs the codebase.
`tests/test_recency.py` pins both halves.

**v0.3 — Run and Gym** (`e5808c6`, `a26a1a4`, PR #2). The v0.2 dashboard was one
undifferentiated scroll; rendering it exposed real faults (four lifts at 28–136 kg
sharing one y-axis, six insight cards burying every chart). Rebuilt as React +
Vite with two sections, added AEI, vitals, a coach strip, a chat dock, and
approval-gated write-back.

**Provider swap** (PR #3). The chat dock required `ANTHROPIC_API_KEY` and shipped
inert because the user had none and did not want to pay. Made the provider
swappable, defaulting to Gemini's free tier.

**PR workflow adopted** (PR #4). The repo had been "commit straight to main, no
remote, no PR flow". That became false once `origin` existed. Now every change —
including one-line docs fixes — goes branch → commits → PR → review → merge.

**README rewritten** (PR #5). It had drifted into self-contradiction: a
`## Dashboard` section still described the v0.2 single HTML page ("no build step,
no npm") directly beneath a `## Frontend` section describing Vite + React.

**Coach agent, days 1–4** (PRs #6–#9). See the ADK decision below.

## Key decisions and rejected alternatives

**Warmups don't count toward effective sets.** A real session logged 27 sets of
which only 16 were working. Configurable via `COUNT_WARMUP_SETS`.

**Rep ranges are configuration, never inferred.** A logged set records what was
done, not what was intended — a heavy top set plus a back-off is
indistinguishable from a failed range attempt. Only sets at the session's top
weight count toward a progression decision.

**AEI grade is binned over 25 m, never per GPS sample.** Raw 1 Hz altitude put
the 95th-percentile grade at 41% on a flat run, and Minetti's curve is
asymmetric — climbing costs more than descending saves — so symmetric noise
biases the result *upward* rather than cancelling. Binning cut inflation from
23% to 10%. Because the choice moves AEI ~10%, `aei.METHOD_VERSION` is part of
the value's identity.

**25 m segments are persisted** so a method change recomputes in ~2.6 s instead
of re-downloading 1.2 MB of GPS per run (~14.3 s).

**One accent, one series per chart.** The Google Health reference palette fails
categorical colour validation outright (cyan→teal ΔE 12.1 against a floor of 15)
but never puts two series in one chart. Adopting the *discipline* rather than the
hues also fixed the broken multi-lift strength chart. Every chart ships a table
twin.

**Gemini free tier over the alternatives.** Rejected, with reasons:

- *Anthropic API* — works, but costs money and the user declined. A Claude
  Pro/Max subscription is **not** an API credential; there is no supported way to
  point the SDK at it. The Claude Agent SDK could technically inherit Claude
  Code's login, but its docs explicitly disallow that for third-party products.
- *`gemma3:4b` locally* — **rejected**: Ollama's `gemma3` carries no `tools`
  capability, and this app's chat loop is entirely tool calls, so it fails
  outright rather than degrading. `qwen3:4b` is the local recommendation
  (smaller download, 256K context, explicit tool support).
- *Self-hosting a model on GCP* — **rejected on cost**. GPUs bill by time, APIs
  bill by use; for one person asking a handful of questions a day that is ~100×
  more expensive than the API being avoided. The honest framing: cost and privacy
  point in opposite directions, and GCP self-hosting is the worst of both.

The user is **outside the EEA/UK/Switzerland**, so Gemini's free-tier terms apply
in full: Google may use prompts for product improvement, and human review is in
scope. This was flagged explicitly and accepted. `ollama` remains the private
alternative, one env var away.

**ADK: rejected, then accepted, on different grounds.** Earlier in the project I
argued against adding an agent framework, and `CLAUDE.md` recorded "Don't add ADK
yet". That argument was about `chat.py` — a 48-line stateless loop that genuinely
does not need a harness, whose built-in file/bash tools are exactly what
"advisor, not autopilot" forbids, and which would put Node back into a runtime
image deliberately made Python-only.

The coach is a **different problem**: multi-agent delegation, session state, and
week-over-week continuity (`previous_plan`) that a stateless call cannot express.
The user's spec scoped it as *a second entry point, not a migration* — `chat.py`
is untouched. ADK is an **optional extra**, so nothing else in the app depends on
its 25 direct dependencies.

**The no-arithmetic boundary, sharpened.** The original spec said the agent may
not "add up sets itself" but also gave the strength planner "set allocation" —
which is arithmetic. Resolved by explicit decision: **the agent never emits a set
count.** It picks exercises and days; the assembler computes sets from the
tool-reported deficit. Enforced structurally — `WeekProposal` has no field for a
set count, rep count or weight. Rejected alternatives: agent proposes and
assembler validates (weakens the guarantee from "cannot be wrong" to "is
checked", and costs turns on a 15 RPM budget); agent allocates and the scorer
catches it (weakest — a plan can score well and still contain an invented
number).

**Running target shape: weekly distance + session count**, mirroring
`VolumeTarget` so existing window-scaling applies unchanged. Rejected: distance
only (one long run satisfies 25 km, which is not the intent) and sessions only
(AEI and any distance goal have nothing to anchor to).

**The context reader is deterministic, not an `LlmAgent`.** It exists to save
requests on a ~15 RPM budget; an `LlmAgent` would spend requests to save
requests. A test asserts it is not one, so a later "upgrade" must argue for
itself.

## Scope

**In:** one person's lifting and running; volume-vs-target as the core; a local
dashboard; a chat dock; approval-gated write-back to Hevy; a weekly planning
coach.

**Out, deliberately:**
- **Not an autopilot.** Write-back stays gated. **Hevy has no delete endpoint**,
  so anything written must be removed by hand in the app — never remove the diff.
- **Not medical advice.** Sleep and resting-HR data are surfaced as the user's
  own history. A test asserts insight output contains no directive language, and
  `tests/guardrails.py` extends that to the coach.
- **Not multi-tenant.**
- **No parallel sub-agents** in the coach (rate-limited, unnecessary).
- **No Agent Engine deployment** (v0.4; runs locally).
- **No solver** unless the scorer proves the agent allocates badly.
- **No multi-week periodisation.** One week at a time.
- **No `drift` insight rule** until planned sessions exist.

## Known issues

**1. The AEI headline is wrong, from bad sensor data.** 🔴 The Run screen's hero
card reads **2.589 m/beat**; every real run sits at 0.91–1.23. Cause: the
2026-08-05 run recorded 5.12 km at 7:00/km pace with an **average heart rate of
61 bpm** — below the user's resting HR of 66, and physiologically impossible
while running. AEI is distance ÷ beats, so too few beats inflates it.
`aei.reliability()` checks *distance* coverage (correctly excluding the
2026-07-05 run whose GPS track held 66 m of a reported 936 m) but never checks
whether heart rate is plausible. Proposed fix: a guard rejecting runs whose
average HR is at or below resting. **Note this will change stored AEI values**,
since that run currently counts toward the mean.

**2. `per ${bucket}` caption on period totals.** 🟠
`frontend/src/screens/RunScreen.tsx:89-101` sets `caption={`per ${data.bucket}`}`
on the Runs and Distance cards. Over a 90-day window this renders *"Runs 12 per
week"* and *"Distance 51.0 km per week"* when those are period totals — off by
~13×. The caption was written to describe the sparkline's bucketing but sits
under the hero number.

**3. Charts do not re-measure when the viewport shrinks.** 🟡 Resizing
desktop→mobile leaves SVGs at their original pixel width (842 px inside a 375 px
viewport) and the page scrolls sideways. A **fresh load at 375 px is clean**, so
this does not affect phones — only desktop window resizing and tablet rotation.
`useWidth` (`frontend/src/charts/primitives.tsx:35`) does use a `ResizeObserver`,
so this is **[inference]** a feedback loop: the SVG's width comes from its
container, the container's width is content-sized by its grid track, so a shrink
cannot propagate. Same family as three layout bugs already fixed with explicit
`minmax(0, 1fr)` tracks and broad `min-width: 0`; some container still lacks it.
Which one is **UNKNOWN**.

**4. `CLAUDE.md` says "178 tests"** (line 150). It is 290.

**5. Windows console mangles non-ASCII** in CLI output (`Zone 1 — recovery`
renders as `Zone 1 ? recovery`). Pre-existing, cosmetic; new CLI code added in
the coach work deliberately uses ASCII to sidestep it.

**6. Four bugs filed against the Google Health MCP server** (private repo
`visakhr1998/google-health-mcp-v1`, issues #3–#6). Ordered by severity: failures
returned as plain text instead of `isError` (#3 — the dangerous one, a failed
request reads as "no data"); truncated responses returned as invalid JSON (#4);
empty pages carrying `nextPageToken` (#5); `daily_rollup` coupling range to page
size (#6). All are worked around in `mcp_client.py` / `sync.py`. A fifth
candidate — filter parameters rejected with
`INVALID_DATA_POINT_FILTER_DATA_TYPE_RESTRICTION` — was **not** filed because it
was never confirmed to be a server bug rather than bad usage.

## Open questions

- Should the HR-plausibility guard exclude the 2026-08-05 run outright, or should
  the user first check Google Health to confirm the watch misread? The user was
  asked and **has not yet answered**.
- Day 8's trajectory eval, as specified in the plan, assumes the agent makes tool
  calls. After the day-4 context injection, a *good* run makes **zero**. The eval
  design needs rethinking so it does not reward the wasteful behaviour that was
  removed.
- Whether the priority-ranking scorer proves the agent allocates well enough, or
  whether a solver is needed. The plan says find out empirically.

## Next steps, as agreed

The immediate agreed next step is **fixing the three UI findings as one PR**,
with the HR-plausibility guard first because it silently distorts the headline
running metric. The coach (day 5 onward) is **parked** at the user's request, not
abandoned.

---

# Tech details

## Architecture

```
                    ┌──────────────┐   ┌──────────────┐
                    │   Hevy MCP   │   │ Google Health│
                    │   (stdio)    │   │  MCP (stdio) │
                    └──────┬───────┘   └──────┬───────┘
                           │                  │
                           └────────┬─────────┘
                                    │  ONLY sync.py talks to MCP
                            ┌───────▼────────┐
                            │  mcp_client.py │  truncation retry,
                            │                │  plain-text error guard
                            └───────┬────────┘
                                    │
                            ┌───────▼────────┐
                            │    sync.py     │  backfill + incremental,
                            │                │  TCX → 25 m bins → AEI
                            └───────┬────────┘
                                    │ writes
                            ┌───────▼────────┐
                            │     db.py      │  Repository protocol
                            │  SQLite, 14    │  → SQLiteRepository
                            │  tables        │
                            └───────┬────────┘
                                    │ reads
              ┌─────────────────────┼─────────────────────┐
              │                     │                     │
      ┌───────▼────────┐   ┌────────▼────────┐   ┌────────▼────────┐
      │   queries.py   │   │   sections.py   │   │  writeback.py   │
      │ (CLI + tools)  │   │ (Run/Gym views) │   │ propose → diff  │
      └───────┬────────┘   └────────┬────────┘   └────────┬────────┘
              │                     │                     │
              └──────────┬──────────┘                     │
                         │ all sit on                     │
         ┌───────────────▼───────────────┐                │
         │        RULES ENGINE           │                │
         │  volume.py · progression.py   │                │
         │  insights.py · aei.py         │                │
         │  vitals.py · tcx.py · icons.py│                │
         │  PURE: dataclasses in/out,    │                │
         │  no DB, no network, no model  │                │
         └───────────────────────────────┘                │
                         │                                │
        ┌────────────────┼────────────────┬───────────────┘
        │                │                │
   ┌────▼─────┐    ┌─────▼─────┐    ┌─────▼──────┐
   │  cli.py  │    │  api.py   │    │  chat.py   │
   │ 19 cmds  │    │ 27 routes │    │ 13 tools   │
   └──────────┘    └─────┬─────┘    └─────┬──────┘
                         │                │
                  ┌──────▼──────┐   ┌─────▼──────┐
                  │ web/dist    │   │   llm.py   │
                  │ (committed  │   │ anthropic  │
                  │  Vite build)│   │ | openai-  │
                  └──────┬──────┘   │ compatible │
                         │          └────────────┘
                  ┌──────▼──────────────┐
                  │ React: App.tsx      │
                  │ Run / Gym sections  │
                  └─────────────────────┘

   coach/  (optional extra, ADK)
   ┌──────────────────────────────────────────────┐
   │ SequentialAgent "coach"                      │
   │   1. ContextReader  ← deterministic, no LLM  │
   │      gather_context() → session state        │
   │   2. week_planner   ← LlmAgent + Gemini      │
   │      tools=build_tools(), output_schema=     │
   │      WeekProposal (no set counts possible)   │
   └──────────────────────────────────────────────┘
```

**The invariants that must not break:**

- The rules engine imports neither `db` nor `mcp_client`. Every number is
  testable without I/O.
- Only `sync.py` talks to the MCP servers.
- `api.py` performs no computation, so the API and CLI cannot disagree.
- The model never computes. `chat.py` and `coach/tools.py` expose *computed
  state*; the prompts forbid arithmetic; there is deliberately **no tool that
  returns individual sets**, because the agent would total them.
- A `Repository` protocol sits between the engine and SQLite so the v0.4 move
  off local storage stays contained. No code outside `db.py` assumes a file path.

## Stack and why

| Component | Version | Why |
|---|---|---|
| Python | 3.12.1 (requires ≥3.11) | |
| `mcp` | ≥1.2 | stdio clients for both data sources — keeps credentials in the servers that already own them |
| `anthropic` | ≥0.40 | original chat provider; still supported |
| `openai` | ≥1.40 | **protocol client only** — one code path reaches Gemini, Groq, Ollama, OpenRouter via their OpenAI-compatible endpoints |
| `fastapi` / `uvicorn` | ≥0.110 / ≥0.27 | backend; uvicorn runs **without** `--reload` |
| `python-dotenv` | ≥1.0 | `.env` loading |
| `google-adk` | ≥2.6 (2.6.2 installed) | **optional extra `[coach]`** — 25 direct deps, nothing else needs them |
| `pytest`, `httpx` | ≥8.0, ≥0.27 | extra `[dev]` |
| React | ^18.3.1 | |
| Vite | ^6.0.7 | **pinned to 6**: `create-vite@latest` needs Node ≥20.12 and this machine has 20.11 |
| TypeScript | ^5.6.3 | |
| Node / npm | v20.11.0 / 10.2.4 | |

No chart library — charts are hand-built inline SVG in
`frontend/src/charts/primitives.tsx`.

## File map

### Python package — `src/fitness_ledger/`

| File | Lines | What it does |
|---|---|---|
| `config.py` | 125 | Frozen `Config` dataclass, loaded from `.env`. Paths, tunables, model provider settings. `_env()` raises for the two required MCP commands; everything else has a default. |
| `models.py` | ~270 | Plain dataclasses the rules engine operates on: `ExerciseTemplate`, `SetEntry`, `Workout`, `Run`, `VolumeTarget`, `MuscleVolume`, `VolumeRollup`, `Insight`, plus coach entities `Goal`, `RunningTarget`, `Availability`. Validation lives here (`__post_init__`) so every entry point gets it. |
| `db.py` | ~860 | `Repository` protocol + `SQLiteRepository`. 14 tables. `_add_missing_columns()` is the idempotent migration hook. |
| `mcp_client.py` | 141 | `MCPClient` over stdio. Handles the truncation marker and plain-text errors. |
| `sync.py` | 561 | Hevy backfill/incremental, Google Health points and rollups, TCX → 25 m bins → AEI, vitals. |
| `volume.py` | 251 | **Rules engine.** `compute_volume`, `coverage`, `weekly_series`, `estimate_1rm`, `best_set_per_session`, `default_targets`. |
| `progression.py` | 230 | Double-progression state, `stalled`, `main_lifts`, equipment load increments. |
| `insights.py` | 284 | Five detection rules → `Insight` records. Pure. |
| `aei.py` | 246 | Minetti cost curve, 25 m binning, `reliability()`, `compute()`, `from_segments()`. `METHOD_VERSION = 1`. |
| `tcx.py` | 101 | Regex TCX parser → `Trackpoint`. Handles the JSON-escaped payload. |
| `vitals.py` | 155 | Tanaka max HR, Karvonen zones, Mifflin-St Jeor BMR, BMI. |
| `icons.py` | 103 | Deterministic exercise-title → icon mapping. `ALL_ICONS` is the contract with the frontend. |
| `queries.py` | 473 | Question-shaped reads for the CLI and chat tools. Owns `parse_window`. |
| `sections.py` | 388 | Run/Gym view queries for the dashboard. |
| `writeback.py` | 199 | Hevy write-back: `build_routine`, `diff`. **The propose step never calls Hevy.** |
| `chat.py` | ~310 | Chat dock: 13 tool definitions (Anthropic schema), `dispatch`, system prompt, the turn loop. |
| `llm.py` | 353 | Provider transports. `AnthropicTransport`, `OpenAICompatibleTransport`, `build()`, `resolve_provider()`, `model_name()`. |
| `api.py` | ~410 | FastAPI, 27 routes, serves `web/dist`. |
| `cli.py` | ~600 | argparse CLI, 19 commands. |
| `coach/__init__.py` | 65 | `require_adk()`, `configure_adk_environment()`, `CoachUnavailable`. |
| `coach/tools.py` | 218 | The 10 tool wrappers the coach may call. No raw-sets tool. |
| `coach/context.py` | ~150 | `gather_context()`, `deficit_summary()`, `training_days()`, `next_monday()`, `build_context_reader()`. |
| `coach/agent.py` | ~210 | `WeekProposal` schemas, `INSTRUCTION`, `build_coach()`, `propose_week()`. |
| `web/dist/` | — | **Committed Vite build.** So a clone runs with Python alone. |

### Frontend — `frontend/src/`

| File | What |
|---|---|
| `main.tsx` | React entry |
| `App.tsx` | Shell: section tabs, one filter row scoping everything below, Run/Gym switch, coach strip, chat dock |
| `api.ts` | Typed client + fetch hooks |
| `theme.css` | Design tokens, dark default, `data-theme` override |
| `app.css` | Layout |
| `charts/primitives.tsx` | `useWidth`, `barPath`, `pillPath`, `Tooltip`, `TableTwin`, `Spark`, `PillBars`, `LineChart`, `Columns`, `TargetBars`, `Radar`, `Card` |
| `components/shell.tsx` | `MetricCard`, `SectionTabs`, `TimeHorizonFilter`, `ThemeControl` (3-state), `SyncControl` (polls status) |
| `components/ExerciseIcon.tsx` | ~27 SVG movement figures + `CoachMascot` owl |
| `components/VitalsCard.tsx` | Age/height/weight/RHR/VO₂max/max HR/BMR + zone table |
| `components/Coach.tsx` | `CoachStrip` (insights grouped by rule) and `ChatDock` (FAB) |
| `components/WriteBack.tsx` | Routine proposal + diff UI |
| `screens/RunScreen.tsx` | AEI hero, AEI-over-time, Runs/Distance cards, avg HR per run, vitals |
| `screens/GymScreen.tsx` | Tonnage, muscle radar, volume-vs-target, tonnage trend, exercise tracker |

### Tests — `tests/` (290 passing)

`test_volume`, `test_windows`, `test_db`, `test_progression`, `test_insights`,
`test_api`, `test_recency`, `test_tcx`, `test_aei`, `test_vitals`, `test_icons`,
`test_writeback`, `test_llm`, `test_goals`, `test_guardrails`, `test_coach_tools`,
`test_coach_setup`, `test_coach_context`, `test_coach_agent`.
`tests/guardrails.py` is a shared helper, not a test module.

## Core modules in depth

### `volume.py` — the thing that must be correct

```
volume[muscle] = Σ(working sets where muscle is primary)
               + secondary_weight × Σ(working sets where muscle is secondary)
frequency[muscle] = count of distinct local dates where volume[muscle] > 0
```

Invariants:
- A muscle listed as both primary and secondary on one exercise **counts once**
  (`volume.py:118`).
- `NON_VOLUME_MUSCLES = {cardio, full_body, other, neck}` are excluded.
- Sets whose template is unknown are **skipped and reported** via
  `unmapped_templates()`, never silently dropped.
- `coverage()` scales targets to the window: four weeks of volume is compared
  against four weeks of target. `window_weeks()` floors at 1.0.

### `queries.parse_window` — two vocabularies, do not mix

- **`last-N-weeks` = N complete weeks**, excluding the part-finished current one.
  Correct for trailing baselines.
- **`last-N-days` includes today.** Correct for recency panels.

Mixing them once hid the newest five days. `tests/test_recency.py` pins both.
Also accepts `this-week`, `last-week`, `today`, `yesterday`, `last-N-months`,
`last-N-hours`, `YYYY-MM`, and `YYYY-MM-DD:YYYY-MM-DD`.

### `aei.py`

`METHOD_VERSION` is part of the value's identity — change any constant and bump
it, so stored runs recompute from `run_segments` without re-downloading.
`reliability()` returns `(reliable, reason, coverage)`; the reason is surfaced in
the UI so an excluded run says why. **Current gap: no heart-rate plausibility
check** (known issue 1).

### `llm.py`

`Transport` ABC with `ask()` / `turn()` / `record()`. Each transport owns its own
message history because the wire formats disagree about where a tool result lives
(Anthropic: a `user` turn with `tool_result` blocks; OpenAI: a `tool` role).
`to_openai_tools()` translates Anthropic tool schemas; argument-less tools get
explicit `type`/`properties` defaults because several providers reject a function
without them. `_loads()` degrades malformed tool arguments to `{}`.
`MAX_TOKENS = 4000`; `DEFAULT_REASONING_EFFORT = {"gemini": "none"}`.

### `coach/`

- `tools.py` — `build_tools(repo, config)` returns closures (not methods) so ADK
  can build a schema from the signature without a `repo` parameter in it.
- `context.py` — `PLANNING_WINDOW = "last-week"` (one complete week);
  `TREND_WINDOW = "last-4-weeks"`. `gather_context` calls the same wrappers the
  agent would, so state and tool output cannot disagree.
- `agent.py` — `WeekProposal` has no field for a set count, rep count or weight.
  `ADK_INTERNAL_CALLS = {"set_model_response"}` is filtered from the trace.

## Data model

SQLite, 14 tables (`db.py` `SCHEMA`):

`exercise_templates`, `workouts`, `workout_sets`, `runs`, `health_daily`,
`volume_targets`, `sync_state`, `exercise_progression`, `run_metrics`,
`run_segments`, `user_settings`, `writeback_log`, `goals`, `availability`.

Notes:
- `run_segments` stores the 25 m bins so a method change needs no re-download.
- `availability` stores **only exceptions** — a day with no row is available.
- The running target lives in `user_settings` under
  `running_distance_km_per_week` / `running_sessions_per_week`, not its own table
  (it is a singleton, unlike per-muscle `volume_targets`).
- `goals` is append-only in spirit: an abandoned goal is kept, never deleted.

## External integrations

**Hevy MCP** — read plus `hevy_create_routine` / `hevy_update_routine` for
write-back. Paginates at **10 items** for workouts and events. **Deletions appear
only in `hevy_list_workout_events`.** No delete endpoint.

**Google Health MCP** — read only. Quirks all handled in `sync.py` /
`mcp_client.py`; four are filed as bugs (see Known issues 6). A TCX export is
~1.2 MB / ~1,950 trackpoints, wrapped as `{"tcxData": "<xml>"}`.

**Gemini** — via the OpenAI-compatible shim at
`https://generativelanguage.googleapis.com/v1beta/openai/`, and via ADK for the
coach.

## Config and secrets

**The repo holds no credentials.** The Hevy API key lives in hevy-mcp's own
dotenv; the Google OAuth token in the health server's token file. `.env` here
holds paths and tunables only. `.env` (`.gitignore:131`), `data/` (`:183`) and
`*.db` (`:184`) are ignored. `src/fitness_ledger/web/dist/` is **un-ignored**
(`:191`) because the Python template's blanket `dist/` would otherwise swallow
the committed build.

| Variable | Default | Notes |
|---|---|---|
| `HEVY_MCP_COMMAND` | — | **required** |
| `HEVY_MCP_ARGS` / `HEVY_MCP_ENV` | — | `\|\|`-separated, so Windows paths with spaces survive |
| `HEALTH_MCP_COMMAND` | — | **required** |
| `HEALTH_MCP_ARGS` / `HEALTH_MCP_ENV` | — | |
| `LEDGER_DB_PATH` | `./data/ledger.db` | |
| `SECONDARY_WEIGHT` | `0.5` | |
| `COUNT_WARMUP_SETS` | `false` | |
| `LOCAL_UTC_OFFSET_MINUTES` | `120` | which day a late session lands on |
| `WEEK_STARTS_ON` | `0` | 0 = Monday |
| `REP_RANGE_LOW` / `HIGH` | `6` / `10` | |
| `LLM_PROVIDER` | auto | `gemini` \| `anthropic` \| `ollama` \| `openai-compatible`; blank auto-selects, preferring the free one |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | — / `gemini-2.5-flash` | currently set to `gemini-3.5-flash` via `LLM_MODEL` |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | — / `claude-opus-5` | |
| `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` | — | |
| `LLM_REASONING_EFFORT` | provider default | `off` sends no parameter |

The coach bridges `GEMINI_API_KEY` → `GOOGLE_API_KEY` (which ADK reads) via
`configure_adk_environment()`, using `setdefault` so an explicitly exported value
wins.

## Commands

```bash
# install
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -e ".[dev]"
./.venv/Scripts/python.exe -m pip install -e ".[coach]"   # only for the planner
cp .env.example .env

# run
./.venv/Scripts/python.exe -m fitness_ledger.cli doctor
./.venv/Scripts/python.exe -m fitness_ledger.cli sync
./.venv/Scripts/python.exe -m fitness_ledger.cli serve    # :8000
./.venv/Scripts/python.exe -m fitness_ledger.cli plan --trace
./.venv/Scripts/python.exe -m fitness_ledger.cli ask "how much chest volume last week?"

# test
./.venv/Scripts/python.exe -m pytest                      # 290

# frontend — REQUIRED after any frontend/src change
cd frontend && npm install && npm run build
npm run dev                                               # :5173, proxies /api to :8000
```

Deploy: **not implemented.** v0.4. `ledger export` dumps every table to JSON so
the move off SQLite is a load, not a rewrite.

## Gotchas

- **`uvicorn` runs without `--reload`.** Restart the server after any Python
  change. A stale server is the most common source of confusion here.
- **Editing `frontend/src` changes nothing** until `npm run build` writes
  `src/fitness_ledger/web/dist`, which FastAPI serves and which **is committed**.
- **Running the test suite migrates your real `data/ledger.db`.**
  `tests/test_icons.py::test_the_whole_catalogue_resolves` opens `config.db_path`
  to check the live catalogue, which triggers schema creation. Additive only.
- **Vite is pinned to 6** because Node here is 20.11 and `create-vite@latest`
  needs ≥20.12.
- **ADK 2.6.2 puts tool schemas in `parameters_json_schema`**, not the legacy
  `parameters` field, which is `None`. Reading the legacy field makes it look
  like every tool takes zero arguments — a silent degradation, not an error.
- **ADK's `SequentialAgent` is deprecated** in favour of `Workflow`, but
  `Workflow` is a graph API and cannot yet be an `LlmAgent` sub-agent. Kept
  deliberately; revisit when the planners split.
- **`google-genai` warns** "Both GOOGLE_API_KEY and GEMINI_API_KEY are set" on
  every coach run. Harmless — the bridge sets the former, `.env` supplies the
  latter.
- **A good coach run makes zero tool calls.** The context reader injects
  everything. Do not treat an empty trace as a failure.
- **Windows/Git Bash path translation**: `/tmp/x` and `$(pwd)` produce MSYS paths
  Python cannot open. Use `Config.load().db_path` or Windows paths in probes.
- **Gemini free tier and thinking**: `gemini-2.5-flash` reasons by default and
  those tokens are charged against `max_tokens` *without appearing in the reply*
  (measured: 554 thinking tokens on an 11-token prompt), truncating answers
  mid-sentence. `LLM_REASONING_EFFORT=none` zeroes it.
- **Non-ASCII in CLI output** renders as `?` on the Windows console.
- **Insight severity ordering** matters: `detect()` sorts warnings before info,
  and coverage gaps are graded so the panel does not fill with identical
  complaints and stop being read.
