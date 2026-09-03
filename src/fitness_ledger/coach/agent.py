"""The coach itself.

Two planners behind the context reader, run in order:

    context_reader  ->  strength_planner  ->  running_planner

**Sequential, not parallel.** Running placement depends on strength placement --
you cannot decide where an easy run fits without knowing which day squats landed
on -- and parallel sub-agents multiply requests per turn. Measured, the free tier
allows 5 requests a minute and 20 a day on `gemini-3.5-flash`, so that is the
difference between working and 429s.

The split is not just tidiness. One agent holding the whole week had to weigh
lifting deficits and running targets in a single pass, and running lost every
time: it is a smaller part of the prompt and the deficit numbers shout louder.
Giving running its own turn, with the lifting week already fixed, makes it a
placement problem it can actually solve.

Two properties stay enforced structurally rather than by asking nicely:

- **No output shape carries a set count.** The agents choose exercises and days;
  `planning.py` computes how many sets each gets from the tool-reported deficit.
  A rule the model could break is a rule that eventually gets broken, so it is
  expressed as a schema with nowhere to put a violation.

- **Numbers come from state or tools, never from the model.** Everything the
  proposals carry is an identifier, a date, a distance the running target
  implies, or prose about them.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field, model_validator

from ..config import Config
from ..db import SQLiteRepository
from . import configure_adk_environment, fallback_model, is_quota_error, require_adk
from .context import build_context_reader, next_monday
from .tools import build_tools


# ADK's own plumbing, which shows up alongside real tool calls in the stream.
ADK_INTERNAL_CALLS = frozenset({"set_model_response"})


# --- what each planner may return -------------------------------------------


class PlannedExercise(BaseModel):
    """One exercise in a session. Deliberately carries no set count."""

    @model_validator(mode="before")
    @classmethod
    def _accept_id(cls, data: Any) -> Any:
        """Take `id` for `exercise_template_id`.

        Belt and braces behind naming the pool field to match. A whole week is
        thrown away when one exercise names the field wrong, and the model has
        already done it once -- the cost of tolerating a synonym is far lower
        than the cost of a failed plan.
        """
        if isinstance(data, dict) and "exercise_template_id" not in data and "id" in data:
            data = {**data, "exercise_template_id": data["id"]}
        return data

    exercise_template_id: str = Field(
        description="exercise_template_id from the exercise pool, copied exactly; "
        "anything else cannot be written to Hevy"
    )
    title: str = Field(description="the exercise name, as it appears in the pool")
    targets: list[str] = Field(
        default_factory=list,
        description="muscle groups this is here to serve, from the deficit",
    )


class PlannedSession(BaseModel):
    """A lifting day."""

    session_date: str = Field(description="YYYY-MM-DD, must be one of the training days")
    focus: str = Field(default="", description="short label, e.g. upper, lower, push")
    exercises: list[PlannedExercise] = Field(default_factory=list)


class StrengthProposal(BaseModel):
    """What the strength planner is allowed to return."""

    sessions: list[PlannedSession]
    rationale: str = Field(
        description="why this shape, in two or three sentences, citing the numbers you were given"
    )
    trade_offs: str = Field(
        default="",
        description="which muscle groups you could not get to target, and why; empty only if none",
    )


class RunSession(BaseModel):
    """A running day. Distance comes from the weekly target, never invented."""

    session_date: str = Field(description="YYYY-MM-DD, must be one of the training days")
    focus: str = Field(default="", description="short label, e.g. easy, long, intervals")
    distance_km: float = Field(
        description="kilometres, from the weekly running target split across the runs"
    )


class RunningProposal(BaseModel):
    """What the running planner is allowed to return."""

    sessions: list[RunSession]
    rationale: str = Field(
        description="why these days and distances, citing the running target you were given"
    )
    trade_offs: str = Field(
        default="",
        description="what the running week could not do, and why; empty only if nothing was given up",
    )


# --- instructions ------------------------------------------------------------

STRENGTH_INSTRUCTION = """You are a strength coach planning the lifting sessions of one week
for a single person. Another planner will add runs after you, so plan lifting only.

The week you are planning starts on {week_start}.
Days that can be trained: {training_days}
Active goals and targets: {goals}

Last complete week, against the weekly target:
{deficit_summary}

Continuity -- what you said last time, and what became of it:
{continuity_summary}

That shortfall is what this week has to close. It is already measured over one
complete week, and there is deliberately no tool to recompute it: picking your
own window gives a part-finished week that reads as a full target short, and a
plan built on that shortfall is built on nothing.

Exercises you may use, with the id to copy and the muscles each trains:
{pool_summary}

That is the whole pool. It already includes something for every muscle group
that is short, so there is nothing to widen it to.

You have one tool: get_progression_state, which says whether a lift is due to
go up. Everything else you need is above.

Rules you must not break:

1. Never do arithmetic. Every number you mention must have come from a tool
   result or from the values above. Do not total sets, estimate a one-rep max,
   or work out a deficit yourself. If you need a number you were not given,
   call a tool for it; if no tool provides it, say so instead of deriving it.

2. Never say how many sets an exercise should have. That is decided after you,
   from the deficit. Choose the exercises and the days; the sets follow.

3. Only propose exercises from the pool above, and copy
   exercise_template_id exactly as written -- it is an opaque code like
   79D0BB3A, not a name. An invented id such as "bench_press" cannot be written
   to Hevy, and a week of them is a week that does nothing.

4. Only use the training days listed above. The others are unavailable.

5. Leave at least one listed training day free where you can. The running
   planner comes after you and can only use days you have not filled.

6. If a shortfall also appears in the continuity note above, say so in the
   rationale -- a muscle short for a third week is a different situation from
   one short for the first time, and the plan should read like it knows that.
   Do not reproach anyone for a missed session; report what happened and plan
   around it.

7. You may direct training. You may not direct health. Reporting that sleep
   averaged five hours is fine. Telling someone to rest, skip a session, or
   train differently because of a health signal is not something this app
   does, however reasonable it sounds.

When the week cannot satisfy everything, sacrifice in this order -- protect the
earlier items and give up the later ones:

   1. volume per muscle group
   2. full-body coverage
   3. runs on track
   4. session count

Say in trade_offs what you gave up and why. If a muscle is short and you could
not fix it, name it. An empty trade_offs means nothing was sacrificed, so use
it only when that is true.

Keep the rationale to two or three sentences, and cite the actual numbers you
were given rather than describing them vaguely."""


RUNNING_INSTRUCTION = """You are a running coach placing this week's runs for a single person.
The lifting week is already fixed and you cannot change it.

The week starts on {week_start}.
Days that can be trained: {training_days}
Active goals and targets: {goals}

Lifting is already placed on these days:
{strength_days}

Recent running, for context:
{running_summary}

Your job is to place the runs the weekly running target asks for, on days that
work around the lifting above.

Rules you must not break:

1. Never do arithmetic beyond splitting the weekly target distance across the
   runs. Every other number you mention must have come from a tool result or
   from the values above.

2. If there is no running target, return no sessions at all and say so in
   rationale. Do not invent a distance -- without a target there is nothing to
   protect and nothing to plan toward.

3. Only use the training days listed above.

4. Prefer not to put a hard run the day after a heavy leg session, and prefer
   not to stack a run on a day that already has a long lifting session. These
   are preferences, not hard rules; if the week is tight, say so in trade_offs.

5. You may direct training. You may not direct health. Reporting that sleep
   averaged five hours is fine. Telling someone to rest, skip a run, or train
   differently because of a health signal is not something this app does,
   however reasonable it sounds.

Runs come third in the priority order -- after volume per muscle group and
full-body coverage, before session count. If lifting has taken the days the runs
needed, say that in trade_offs rather than displacing lifting.

Keep the rationale to two or three sentences, citing the target you were given."""


def strength_days(state: dict[str, Any]) -> str:
    """Which days lifting took, rendered for the running planner.

    Deterministic on purpose. Templating the raw proposal dict into the prompt
    would work, but it hands the model a wall of exercise ids to re-read when
    all it needs is the shape of the week.
    """
    proposal = state.get("strength_proposal") or {}
    sessions = proposal.get("sessions") or []
    if not sessions:
        return "  (no lifting sessions planned)"
    return "\n".join(
        f"  {session.get('session_date')}: {session.get('focus') or 'lifting'}"
        f" ({len(session.get('exercises') or [])} exercises)"
        for session in sessions
    )


def running_summary(state: dict[str, Any]) -> str:
    """Recent runs, from the picture the context reader already gathered."""
    runs = ((state.get("ledger_state") or {}).get("runs") or {}).get("runs") or []
    if not runs:
        return "  (no runs recorded recently)"
    return "\n".join(
        f"  {run.get('date')}: {run.get('distance_km')} km"
        for run in runs[-5:]
    )


def _running_instruction(context) -> str:  # noqa: ANN001 - ADK's ReadonlyContext
    """Render the running instruction against session state.

    A callable rather than a template string because `strength_proposal` is a
    nested dict: `{strength_proposal}` would substitute its repr, and the model
    would spend its attention parsing that instead of placing runs.
    """
    state = dict(context.state)
    return RUNNING_INSTRUCTION.format(
        week_start=state.get("week_start", ""),
        training_days=state.get("training_days", []),
        goals=state.get("goals", {}),
        strength_days=strength_days(state),
        running_summary=running_summary(state),
    )


# --- merging -----------------------------------------------------------------


def merge_proposals(
    strength: dict[str, Any] | None, running: dict[str, Any] | None
) -> dict[str, Any]:
    """One week from the two planners' halves.

    Structural only -- concatenate, tag each session with its kind, order by
    date. No number is touched here; the sets are still the assembler's to
    compute and the distances are the running planner's own.

    Rationales are kept apart rather than blended, because when the two
    disagree about what the week is for, that is worth being able to read.
    """
    strength = strength or {}
    running = running or {}

    sessions = [
        {
            "session_date": session.get("session_date"),
            "kind": "lift",
            "focus": session.get("focus", ""),
            "exercises": session.get("exercises") or [],
        }
        for session in strength.get("sessions") or []
    ]
    sessions += [
        {
            "session_date": session.get("session_date"),
            "kind": "run",
            "focus": session.get("focus", ""),
            "distance_km": session.get("distance_km"),
        }
        for session in running.get("sessions") or []
    ]
    sessions.sort(key=lambda session: (session.get("session_date") or "", session["kind"]))

    return {
        "sessions": sessions,
        "rationale": _joined(
            ("Lifting", strength.get("rationale")), ("Running", running.get("rationale"))
        ),
        "trade_offs": _joined(
            ("Lifting", strength.get("trade_offs")), ("Running", running.get("trade_offs"))
        ),
    }


def _joined(*labelled: tuple[str, str | None]) -> str:
    parts = [f"{label}: {text.strip()}" for label, text in labelled if text and text.strip()]
    return " ".join(parts)


# --- wiring ------------------------------------------------------------------


# Planning is a judgement call, but an *evaluable* one. At default temperature
# the same ledger yields a different week each run, which is fine to read and
# useless to grade: a failing assertion could mean the prompt regressed or the
# sampler wandered, and there is no way to tell those apart. Zero does not make
# the model correct, only repeatable -- and a flaky eval is worse than none,
# because it teaches you to ignore failures.
PLANNER_TEMPERATURE = 0.0


def build_coach(
    repo: SQLiteRepository,
    config: Config,
    week_start: date | None = None,
    model: Any = None,
) -> Any:
    """The whole pipeline: read the picture, plan lifting, then place runs.

    `model` overrides the configured provider, which is how the fallback in
    `propose_week` re-runs the identical pipeline somewhere else. Everything
    but the model stays the same, so a plan produced on the backstop is
    comparable with one produced on the primary.
    """
    require_adk()
    model = model or configure_adk_environment(config)

    from google.adk.agents import LlmAgent, SequentialAgent
    from google.genai import types

    week = week_start or next_monday()

    # One tool. Everything else the planner needs -- the deficit, goals,
    # availability, recovery, insights, the previous plan, and now the exercise
    # pool -- is injected, so any further tool is a way to re-fetch something it
    # was handed. Three of ten runs took that route before the other tools were
    # removed, calling get_volume_vs_target despite an instruction not to.
    #
    # get_exercise_pool went the same way on 2026-09-02. Leaving it available
    # meant the pool reached the planner only if the model chose to ask:
    # Gemini asked and DeepSeek did not, so the same code produced a usable
    # week on one provider and 26 invented ids on the other. Handing the pool
    # over makes that independent of the model.
    #
    # Removing a tool beats repeating an instruction. A rule the model *can*
    # break is one that eventually gets broken.
    allowed = {"get_progression_state"}
    tools = [tool for tool in build_tools(repo, config) if tool.__name__ in allowed]
    sampling = types.GenerateContentConfig(temperature=PLANNER_TEMPERATURE)

    strength = LlmAgent(
        name="strength_planner",
        model=model,
        instruction=STRENGTH_INSTRUCTION,
        tools=tools,
        generate_content_config=sampling,
        output_schema=StrengthProposal,
        output_key="strength_proposal",
        # Sequential delegation is the whole design; an agent that can transfer
        # will occasionally decide to run the other planner itself.
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )

    running = LlmAgent(
        name="running_planner",
        model=model,
        instruction=_running_instruction,
        # Deliberately no tools. Everything it needs -- the target, the
        # training days, recent runs, where lifting landed -- is already in
        # session state, and every tool it could call would re-fetch something
        # it has been given. That is the same argument that made the context
        # reader deterministic, applied one level down.
        #
        # It is also what keeps the split affordable. The free tier allows
        # **5 requests per minute** for this model, not the ~15 the plan
        # assumed, and each tool round trip is another request. A tool-less
        # planner costs exactly one.
        tools=[],
        generate_content_config=sampling,
        output_schema=RunningProposal,
        output_key="running_proposal",
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )

    # SequentialAgent is deprecated in ADK 2.6 in favour of Workflow, but
    # Workflow is a graph API (nodes, edges, routes) rather than a drop-in, and
    # ADK's own note says it cannot yet be an LlmAgent sub-agent. This is a
    # straight line of three steps; the graph would buy nothing.
    return SequentialAgent(
        name="coach",
        sub_agents=[build_context_reader(repo, config, week), strength, running],
    )


async def propose_week(
    repo: SQLiteRepository,
    config: Config,
    week_start: date | None = None,
    model: Any = None,
) -> dict[str, Any]:
    """Run the coach, falling back to a second provider if the first refuses.

    The models that plan well on a free tier are exactly the ones with tight
    caps, and a quota error here is not the inconvenience it is in the dock:
    the dock can say "ask again in a minute", but a week that will not generate
    is simply absent. So when the primary returns 429 and a fallback is
    configured, the same pipeline runs again elsewhere.

    Only a quota refusal triggers it. A malformed proposal or a missing tool is
    a real fault, and retrying it on another provider would hide the cause
    behind a second bill.
    """
    require_adk()

    if model is not None:
        # Pinned: no fallback. Used to grade one provider at a time, where a
        # silent switch mid-suite would blend two models into one score that
        # describes neither.
        return await _run_coach(repo, config, week_start, model=model)

    try:
        return await _run_coach(repo, config, week_start)
    except Exception as exc:  # noqa: BLE001 - re-raised unless quota + fallback
        backstop = fallback_model(config) if is_quota_error(exc) else None
        if backstop is None:
            raise
        return await _run_coach(repo, config, week_start, model=backstop, on_fallback=exc)


async def _run_coach(
    repo: SQLiteRepository,
    config: Config,
    week_start: date | None = None,
    model: Any = None,
    on_fallback: BaseException | None = None,
) -> dict[str, Any]:
    """One pass of the pipeline.

    The trace is captured from day one because trajectory evaluation depends on
    it and it cannot be reconstructed afterwards.
    """
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    coach = build_coach(repo, config, week_start, model=model)
    runner = InMemoryRunner(agent=coach, app_name="fitness-ledger-coach")

    session = await runner.session_service.create_session(
        app_name="fitness-ledger-coach", user_id="local"
    )

    trace: list[dict[str, Any]] = []
    async for event in runner.run_async(
        user_id="local",
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text="Plan next week.")]),
    ):
        for call in event.get_function_calls() or []:
            # ADK delivers the structured output as an internal call. It is not
            # a tool the coach chose, and counting it as one would make every
            # trajectory look like it made an extra fetch.
            if call.name in ADK_INTERNAL_CALLS:
                continue
            trace.append({"agent": event.author, "tool": call.name, "args": dict(call.args or {})})

    final = await runner.session_service.get_session(
        app_name="fitness-ledger-coach", user_id="local", session_id=session.id
    )
    state = final.state

    return {
        "week_start": state.get("week_start"),
        "training_days": state.get("training_days", []),
        "proposal": merge_proposals(
            state.get("strength_proposal"), state.get("running_proposal")
        ),
        # Both halves kept as returned, so a merge bug cannot be mistaken for a
        # planner bug when reading a stored plan back.
        "strength_proposal": state.get("strength_proposal"),
        "running_proposal": state.get("running_proposal"),
        "agent_trace": trace,
        # Carried out of session state rather than re-read, so the assembler
        # allocates against the same deficit the agent was shown.
        "ledger_state": state.get("ledger_state", {}),
        "exercise_pool": state.get("exercise_pool", []),
        # Named, not silent. A week planned by the backstop is still a valid
        # week, but you should be able to tell -- if every plan is arriving
        # this way the primary is not really the primary any more.
        "planned_by": getattr(model, "model", None) or configure_adk_environment(config),
        "fell_back_from": str(on_fallback)[:200] if on_fallback else None,
    }
