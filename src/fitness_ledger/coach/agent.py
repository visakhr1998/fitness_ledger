"""The coach itself.

A single LlmAgent for now; the split into strength and running planners comes
later. It runs behind the context reader in a SequentialAgent, so the picture
is already in session state before the model is asked anything.

Two properties are enforced structurally rather than by asking nicely:

- **The output shape carries no set counts.** WeekProposal has nowhere to put
  one. The agent chooses exercises and days; the assembler computes how many
  sets each gets from the tool-reported deficit. A rule the model could break
  is a rule that eventually gets broken, so it is expressed as a schema.

- **Numbers come from state or tools, never from the model.** Everything the
  proposal carries is an identifier, a date, or prose about them.

The instruction states the priority ranking, but does not rely on the model to
apply it correctly -- the scorer checks that deterministically once the plan
exists. The instruction exists so the agent tries; the scorer exists so we
find out whether trying was enough.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from ..config import Config
from ..db import SQLiteRepository
from . import configure_adk_environment, require_adk
from .context import build_context_reader, next_monday
from .tools import build_tools


# ADK's own plumbing, which shows up alongside real tool calls in the stream.
ADK_INTERNAL_CALLS = frozenset({"set_model_response"})


class PlannedExercise(BaseModel):
    """One exercise in a session. Deliberately carries no set count."""

    exercise_template_id: str = Field(
        description="id from the exercise pool; anything else cannot be written to Hevy"
    )
    title: str = Field(description="the exercise name, as it appears in the pool")
    targets: list[str] = Field(
        default_factory=list,
        description="muscle groups this is here to serve, from the deficit",
    )


class PlannedSession(BaseModel):
    session_date: str = Field(description="YYYY-MM-DD, must be one of the training days")
    kind: str = Field(description="'lift' or 'run'")
    focus: str = Field(default="", description="short label, e.g. upper, lower, easy run")
    exercises: list[PlannedExercise] = Field(default_factory=list)
    distance_km: float | None = Field(
        default=None, description="runs only; leave null for lifting sessions"
    )


class WeekProposal(BaseModel):
    """What the agent is allowed to return."""

    sessions: list[PlannedSession]
    rationale: str = Field(
        description="why this shape, in two or three sentences, citing the numbers you were given"
    )
    trade_offs: str = Field(
        default="",
        description="what could not be satisfied and why; empty only if nothing was sacrificed",
    )


INSTRUCTION = """You are a strength and running coach planning one week for a single person.

The week you are planning starts on {week_start}.
Days that can be trained: {training_days}
Active goals and targets: {goals}

Last complete week, against the weekly target:
{deficit_summary}

That shortfall is what this week has to close. It is already measured over one
complete week, so do not call get_volume_vs_target to recompute it and do not
pick your own window -- a part-finished week reads as a full target short and
would send you planning against a shortfall that is not real.

Use get_exercise_pool to see what exists, and get_progression_state if you need
to know whether a lift is due to go up. Only call other tools if you actually
need something the above does not give you; each call costs time.

Rules you must not break:

1. Never do arithmetic. Every number you mention must have come from a tool
   result or from the values above. Do not total sets, estimate a one-rep max,
   or work out a deficit yourself. If you need a number you were not given,
   call a tool for it; if no tool provides it, say so instead of deriving it.

2. Never say how many sets an exercise should have. That is decided after you,
   from the deficit. Choose the exercises and the days; the sets follow.

3. Only propose exercises that appear in get_exercise_pool. Anything else does
   not exist in this person's app and cannot be written back.

4. Only use the training days listed above. The others are unavailable.

5. You may direct training. You may not direct health. Reporting that sleep
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


def build_coach(
    repo: SQLiteRepository, config: Config, week_start: date | None = None
) -> Any:
    """The whole pipeline: read the picture, then plan against it."""
    require_adk()
    model = configure_adk_environment(config)

    from google.adk.agents import LlmAgent, SequentialAgent

    week = week_start or next_monday()

    planner = LlmAgent(
        name="week_planner",
        model=model,
        instruction=INSTRUCTION,
        tools=build_tools(repo, config),
        output_schema=WeekProposal,
        output_key="week_proposal",
        # Nothing to hand off to yet, and an agent that can transfer will
        # occasionally try to.
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )

    # SequentialAgent is deprecated in ADK 2.6 in favour of Workflow, but
    # Workflow is a graph API (nodes, edges, routes) rather than a drop-in,
    # and ADK's own note says it cannot yet be an LlmAgent sub-agent. For a
    # two-step sequence the graph buys nothing. Revisit when the planners
    # split and there is actually a graph to express.
    return SequentialAgent(
        name="coach",
        sub_agents=[build_context_reader(repo, config, week), planner],
    )


async def propose_week(
    repo: SQLiteRepository, config: Config, week_start: date | None = None
) -> dict[str, Any]:
    """Run the coach once and return the proposal plus its tool trace.

    The trace is captured from day one because trajectory evaluation depends
    on it and it cannot be reconstructed afterwards.
    """
    require_adk()
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    coach = build_coach(repo, config, week_start)
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
            # ADK delivers the structured output as an internal call. It is
            # not a tool the coach chose, and counting it as one would make
            # every trajectory look like it made an extra fetch.
            if call.name in ADK_INTERNAL_CALLS:
                continue
            trace.append({"tool": call.name, "args": dict(call.args or {})})

    final = await runner.session_service.get_session(
        app_name="fitness-ledger-coach", user_id="local", session_id=session.id
    )

    return {
        "week_start": final.state.get("week_start"),
        "training_days": final.state.get("training_days", []),
        "proposal": final.state.get("week_proposal"),
        "agent_trace": trace,
        # Carried out of session state rather than re-read, so the assembler
        # allocates against the same deficit the agent was shown. Gathering it
        # again would cost nothing in tokens but could differ, and a plan built
        # from a different deficit than it was argued from is worse than a
        # slow one.
        "ledger_state": final.state.get("ledger_state", {}),
        "exercise_pool": final.state.get("exercise_pool", []),
    }
