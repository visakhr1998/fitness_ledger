"""Everything the planners need, fetched once.

The plan called this an agent. It is deliberately *not* an LlmAgent, and the
reason is the same one that motivated it: the context reader exists so the
strength and running planners don't each make the same tool calls against a free
tier that allows single-digit requests per minute. An LlmAgent would spend
requests to save requests.

So the work is a plain function, and the ADK piece is a thin adapter that
publishes the result to session state. Three things follow from that:

- it costs no tokens and consumes no rate limit;
- it cannot hallucinate a deficit, because no model is involved;
- it is unit-testable without a model, a network, or ADK installed.

`gather_context` calls the same wrappers in `tools.py` that the agent would
call itself, rather than reaching into `queries.py` separately. If the two
diverged, state and tool output would disagree about the same number, and the
agent would have no way to tell which was right.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ..config import Config
from ..db import SQLiteRepository
from ..models import WEEKDAY_NAMES
from ..queries import planning_preferences
from .tools import build_tools

# One complete week is what a one-week plan is measured against: over
# `last-4-weeks` the target is scaled to four weeks, so its deficit is four
# weeks' worth and planning against it would quadruple every session.
#
# Left to itself the agent picked `this-week` -- a barely-started week, where
# almost nothing is logged yet and every muscle reads as a full target short.
# It then planned against that fabricated shortfall. So the window is chosen
# here, not there.
PLANNING_WINDOW = "last-week"

# Four complete weeks is the baseline the insight rules use, kept alongside so
# the coach can tell a one-off from a pattern without disagreeing with the
# insight cards about which is which.
TREND_WINDOW = "last-4-weeks"
RUN_WINDOW = "last-4-weeks"
RECOVERY_WINDOW = "last-2-weeks"

STATE_KEYS = (
    "goals",
    "ledger_state",
    "availability",
    "constraints",
    "preferences",
    "exercise_pool",
    "previous_plan",
)


def next_monday(today: date | None = None) -> date:
    """The Monday of the week being planned.

    Planning always targets a whole week that hasn't started. Planning the
    current part-finished week would compare a full target against partial
    logged volume and read every muscle as behind.
    """
    today = today or date.today()
    return today - timedelta(days=today.weekday()) + timedelta(days=7)


def gather_context(
    repo: SQLiteRepository, config: Config, week_start: date | None = None
) -> dict[str, Any]:
    """Read the whole planning picture in one pass.

    Returns exactly the keys in STATE_KEYS, so a planner can rely on all of
    them being present -- an absent key and an empty one mean different things
    and only one of them should be possible.
    """
    week = week_start or next_monday()
    tools = {tool.__name__: tool for tool in build_tools(repo, config)}
    volume = tools["get_volume_vs_target"](PLANNING_WINDOW)

    return {
        "week_start": week.isoformat(),
        "goals": tools["get_goals"](),
        "ledger_state": {
            "volume": volume,
            "volume_trend": tools["get_volume_vs_target"](TREND_WINDOW),
            "progression": tools["get_progression_state"](),
            "runs": tools["get_recent_runs"](RUN_WINDOW),
            # The same window the strength deficit uses, for the same reason:
            # a running week is measured against one *complete* week, and the
            # four-week list above is trend context rather than a shortfall.
            # Fetched rather than filtered out of `runs`, so the window
            # boundaries come from `parse_window` and cannot drift from the
            # ones the volume table used.
            "runs_last_week": tools["get_recent_runs"](PLANNING_WINDOW),
            "recovery": tools["get_recovery_signals"](RECOVERY_WINDOW),
            "insights": tools["get_insights"](),
        },
        "availability": tools["get_availability"](week.isoformat()),
        # Standing weekly rules. Saved and shown on the Goals screen for weeks
        # before anything handed them to a planner (#70).
        "constraints": tools["get_constraints"](),
        # The settings `validate` will hold the week to, so the prompts can
        # describe the rules the config actually has. The running prompt used
        # to describe run-after-legs as a rule while the config switched it
        # off, and the model reported violating a rule that did not exist (#61).
        "preferences": {
            "allow_run_after_leg_day": planning_preferences(repo).allow_run_after_leg_day,
        },
        "exercise_pool": covering_pool(tools, volume),
        "previous_plan": tools["get_previous_plan"](),
    }


# How many unlogged exercises to add per uncovered muscle. Two is enough to
# choose between without turning the pool into the whole catalog.
EXTRA_PER_MUSCLE = 2


def covering_pool(tools: dict[str, Any], volume: dict[str, Any]) -> list[dict[str, Any]]:
    """The logged pool, plus something for every muscle that is short.

    The default pool is what the user actually trains, which is the right
    starting point and the wrong finishing one: **a muscle you have never
    trained has no logged exercises, so it cannot appear.** Those are exactly
    the muscles most likely to be neglected, and the coach was being asked to
    fix a back deficit while holding a list containing no back exercises.

    Found by an eval: the plan for a three-week back gap was a squat. The
    stronger model worked around it by querying the tool again with different
    arguments; the smaller one did not, and neither should have had to.
    """
    pool = tools["get_exercise_pool"]()
    covered = {
        muscle
        for row in pool
        for muscle in (row["primary_muscle_group"], *row["secondary_muscle_groups"])
        if muscle
    }
    known = {row["exercise_template_id"] for row in pool}

    short = [
        row["muscle_group"]
        for row in volume.get("muscles", [])
        if (row.get("sets_deficit") or 0) > 0 and row["muscle_group"] not in covered
    ]
    for muscle in short:
        for row in tools["get_exercise_pool"](muscle, False)[:EXTRA_PER_MUSCLE]:
            if row["exercise_template_id"] not in known:
                known.add(row["exercise_template_id"])
                pool.append(row)
    return pool


def continuity_summary(context: dict[str, Any]) -> str:
    """Last week's plan and what became of it, rendered for the instruction.

    This is what makes the coach a coach rather than a week generator: *"back is
    still short, third week"* needs to know what was said last time and whether
    it was followed. Without it every week is argued from scratch, and a
    shortfall that has survived three plans reads exactly like a new one.

    Rendered here, deterministically, for the same reason the deficit is: the
    agent should be handed the comparison rather than fetch two things and work
    out the relationship itself.
    """
    previous = context.get("previous_plan") or {}
    if not previous.get("available"):
        return "No previous plan. This is the first week being planned."

    # `get_previous_plan` returns the latest plan of *any* week, which after a
    # replan is this same week's earlier draft. Saying so matters: the block
    # otherwise reads "that week has not started, so there is nothing to judge"
    # and then hands over that draft's shortfall as though it were a second
    # week of completed history. The planner has no way to tell the two apart,
    # and a muscle short in one draft would read as short for two weeks.
    same_week = previous.get("week_start") == context.get("week_start")
    followed = previous.get("followed") or {}

    if same_week:
        lines = [
            f"An earlier draft of THIS SAME week ({previous.get('week_start')},"
            f" {previous.get('status', 'proposed')}), which you are now replanning."
            " It is a draft, not a week that happened, so it is not history and"
            " nothing in it counts as a week trained."
        ]
    else:
        lines = [
            f"The plan for an earlier week, {previous.get('week_start')}"
            f" ({previous.get('status', 'proposed')})."
        ]
        if followed.get("not_started"):
            # A plan for a week that has not begun says nothing about
            # adherence, and reporting "0 of 6 trained" would have the planner
            # writing around a failure that never happened.
            lines.append("That week has not started, so there is nothing to judge yet.")
        elif followed:
            lines.append(
                f"{followed.get('sessions_completed', 0)} of"
                f" {followed.get('sessions_planned', 0)} of its sessions had logged training."
            )
            missed = followed.get("missed_days") or []
            if missed:
                lines.append(f"Nothing was logged on: {', '.join(missed)}.")

    if previous.get("trade_offs"):
        # Labelled by where the numbers come from. These are what allocation
        # could not cover in that draft -- an arithmetic result, not measured
        # training -- and unlabelled they look exactly like logged shortfall.
        whose = "that draft" if same_week else "that week's plan"
        lines.append(
            f"What allocation could not cover in {whose}"
            f" (figures from the allocator, not from logged training):"
            f" {previous['trade_offs']}"
        )
    return " ".join(lines)


def deficit_summary(context: dict[str, Any]) -> str:
    """The weekly shortfall, rendered for the instruction.

    Rendering, not computing: every figure here came from the volume tool. It
    exists so the agent is *given* the deficit rather than choosing a window
    and deriving one -- which is how it ended up planning against a
    barely-started week.
    """
    muscles = context["ledger_state"]["volume"]["muscles"]
    short = sorted(
        (m for m in muscles if (m["sets_deficit"] or 0) > 0),
        key=lambda m: m["sets_deficit"],
        reverse=True,
    )
    if not short:
        return "No muscle group is below target. Plan for progression, not more volume."

    lines = [
        f"{m['muscle_group']}: {m['effective_sets']:g} of {m['target_sets']:g} sets"
        f" (short {m['sets_deficit']:g}), trained {m['frequency']} of"
        f" {m['target_frequency']} days"
        for m in short
    ]
    return "\n".join(lines)


def pool_summary(context: dict[str, Any]) -> str:
    """The exercise pool, rendered into the instruction.

    This exists because the pool was never reaching the planner. `covering_pool`
    was built, written to session state and used by the assembler to validate --
    but `STRENGTH_INSTRUCTION` had no placeholder for it, and the agent was told
    to call `get_exercise_pool` instead. A model that skips the call has nothing
    to choose from.

    Measured on 2026-09-02 with DeepSeek: it made *no* tool calls and invented
    plausible ids -- `bench_press`, `squat` -- so all 26 exercises failed
    validation and no day could be written to Hevy. Gemini had been making the
    call and papering over the gap. Handing the pool over is what makes the
    behaviour independent of whether a given model chooses to fetch it.

    Grouped by primary muscle because that is how the deficit reads: the agent
    is told "lats: 4 of 14 sets" and needs to find the lat exercises.
    """
    pool = context.get("exercise_pool") or []
    if not pool:
        return "  (no exercises available -- the catalogue has not been synced)"

    by_muscle: dict[str, list[dict[str, Any]]] = {}
    for row in pool:
        by_muscle.setdefault(row.get("primary_muscle_group") or "other", []).append(row)

    lines: list[str] = []
    for muscle in sorted(by_muscle):
        lines.append(f"{muscle}:")
        for row in by_muscle[muscle]:
            also = ", ".join(row.get("secondary_muscle_groups") or ())
            suffix = f" (also {also})" if also else ""
            lines.append(
                f"  {row['exercise_template_id']}  {row['title']}{suffix}"
            )
    return "\n".join(lines)


def progression_summary(context: dict[str, Any]) -> str:
    """Which lifts are due to go up, rendered into the instruction.

    This was the strength planner's last tool. Handing it over instead closes
    a fork nobody chose: ADK only reaches for its prompt-based
    `set_model_response` workaround when an agent has an output schema *and*
    tools, so holding one tool put Gemini on that path while an
    OpenAI-compatible provider took the native one. Same agent, same prompt,
    two different mechanisms for getting structured output back, decided by
    which provider was configured.

    With `tools=[]` both providers take the native path, and the behaviour
    stops depending on a capability check buried in ADK.

    It is also the same move PR #37 made for the exercise pool, for the same
    reason and in the same words: removing a tool beats repeating an
    instruction, because a call the model *may* make is one it will sometimes
    skip. The data was already being fetched -- `gather_context` puts it in
    `ledger_state.progression` -- so nothing new is read here.
    """
    rows = ((context.get("ledger_state") or {}).get("progression")) or []
    if not rows:
        return "  (no lift has enough recent history to judge)"

    lines: list[str] = []
    for row in rows:
        weight = row.get("working_weight_kg")
        load = f"{weight:g} kg" if weight is not None else "bodyweight"
        ready = "READY to go up" if row.get("ready_to_progress") else "hold"
        suggested = row.get("suggested_weight_kg")
        target = f", next {suggested:g} kg" if suggested is not None else ""
        lines.append(
            f"  {row.get('exercise')}: {load}, {ready}{target}"
            f" -- {row.get('verdict', '')}"
        )
    return "\n".join(lines)


def derive_summaries(context: dict[str, Any]) -> dict[str, Any]:
    """Everything the instructions read that is computed from the raw context.

    One definition, because the alternative is two lists kept in agreement --
    the shape `sync_all` exists to avoid, and the shape that let a placeholder
    ship unpublished once already. ADK substitutes `{key}` from session state,
    so a key the reader forgets renders literally and silently: the model reads
    the word "{pool_summary}" and the failure looks like a prompt problem.

    The test that guards this calls the same function rather than restating the
    list, so a new summary cannot be added to the instruction and forgotten
    here.
    """
    return {
        "training_days": training_days(context),
        "deficit_summary": deficit_summary(context),
        "continuity_summary": continuity_summary(context),
        "pool_summary": pool_summary(context),
        "progression_summary": progression_summary(context),
        "running_day_note": running_day_note(context),
        "running_deficit_summary": running_deficit_summary(context),
        "constraints_summary": constraints_summary(context),
        "run_after_legs_rule": run_after_legs_rule(context),
        "replan_note": replan_note(context),
    }


CONSTRAINT_WORDS = {
    "no_high_impact": "no running or jumping",
    "no_lifting": "no lifting",
    "no_intervals": "easy running only, no hard efforts",
}


def constraints_summary(context: dict[str, Any]) -> str:
    """Standing weekly rules, rendered as the dates they fall on this week.

    Dates rather than weekday numbers, because turning "weekday 2" into
    2026-09-23 is arithmetic, and the planners are handed every date they need
    rather than asked to derive one. A day already lost to availability is
    left out: there is nothing to restrict on a day nobody trains.
    """
    rules = context.get("constraints") or []
    if not rules:
        return "  (none)"

    week = date.fromisoformat(context["week_start"])
    trainable = set(training_days(context))
    lines = []
    for rule in sorted(rules, key=lambda r: r["weekday"]):
        day = (week + timedelta(days=rule["weekday"])).isoformat()
        if day not in trainable:
            continue
        reason = f" -- {rule['reason']}" if rule.get("reason") else ""
        lines.append(
            f"  {day} ({WEEKDAY_NAMES[rule['weekday']]}):"
            f" {CONSTRAINT_WORDS.get(rule['kind'], rule['kind'])}{reason}"
        )
    return "\n".join(lines) or "  (none that fall on a training day this week)"


def run_after_legs_rule(context: dict[str, Any]) -> str:
    """The run-after-legs line, stated as the config has it (#61).

    The prompt used to call this a preference while the config called it
    allowed, and the model reported "a preference violation but unavoidable"
    on a week with a clean arrangement free. Now the prompt says whichever is
    true, because `validate` checks exactly that.
    """
    allowed = ((context.get("preferences") or {}).get("allow_run_after_leg_day"))
    if allowed:
        return (
            "A run the day after a leg session is allowed. Prefer not to put a"
            " hard run there, but it is not a rule."
        )
    return (
        "Never put a run on the day after a lifting day with leg work. This is a"
        " hard rule and the week is checked against it: a week that breaks it is"
        " rejected and planned again. If no free day is left that follows it,"
        " place fewer runs and say so in trade_offs."
    )


def replan_note(context: dict[str, Any]) -> str:
    """Why the previous attempt at this week was rejected, if it was (#56).

    A week that breaks a hard rule is planned again rather than stored, and
    re-asking with the same prompt at temperature 0 mostly returns the same
    week. Naming the violations is what gives the next attempt something to
    change.
    """
    problems = context.get("rejected_problems") or []
    if not problems:
        return ""
    listed = "\n".join(f"  - {problem}" for problem in problems)
    return (
        "\nA previous attempt at this week was rejected because it broke these"
        f" rules:\n{listed}\nPlan the week again so that none of them happens.\n"
    )


def running_deficit_summary(context: dict[str, Any]) -> str:
    """The running week against its target, the way the deficit table reads.

    The running planner was being handed two raw run rows and asked to work out
    for itself whether the week was behind -- arithmetic, on the side of the
    app that does not do arithmetic, while the strength planner received a
    finished "X of Y sets, short Z" table.

    Measured over one complete week, matching `deficit_summary` and
    `insights.running_shortfall`, which uses the last complete week precisely
    so it does not fire every Monday against a week that has barely started.

    Silent about a shortfall when no target is set: an unset target is not a
    shortfall, and saying "short 25 km" to someone who never asked for 25 km
    would invent the goal.
    """
    target = ((context.get("goals") or {}).get("running_target")) or None
    last_week = ((context.get("ledger_state") or {}).get("runs_last_week")) or {}
    window = last_week.get("window", "the last complete week")
    done_km = last_week.get("total_km") or 0
    done_runs = last_week.get("count") or 0

    if not target:
        return (
            f"  No running target is set. Last complete week ({window}):"
            f" {done_runs} run(s), {done_km:g} km. Nothing is behind, because"
            " nothing was asked for."
        )

    want_km = target.get("distance_km_per_week") or 0
    want_runs = target.get("sessions_per_week") or 0
    short_km = max(want_km - done_km, 0)
    short_runs = max(want_runs - done_runs, 0)
    return (
        f"  Last complete week ({window}): {done_km:g} of {want_km:g} km"
        f" (short {short_km:g}), {done_runs} of {want_runs} sessions"
        f" (short {short_runs})."
    )


def running_day_note(context: dict[str, Any]) -> str:
    """Rule 5 of the strength instruction, or the reason there isn't one.

    "Leave a day free for the running planner" was unconditional, and with no
    running target the running planner is not built at all -- so the strength
    planner was giving up a training day for an agent that never runs, on a
    week that could have used it.

    Whether a target exists is already in the context; deciding it here keeps
    the instruction from asking the model to work out whether a rule applies
    to it.
    """
    target = ((context.get("goals") or {}).get("running_target")) or None
    if not target:
        return (
            "Use as many of the listed training days as the week needs. No"
            " running target is set, so no runs will be placed and no day needs"
            " to be kept free."
        )
    return (
        "Leave at least one listed training day free where you can. A running"
        " planner comes after you and can only use days you have not filled."
    )


def training_days(context: dict[str, Any]) -> list[str]:
    """Days of the planned week that are actually trainable.

    Availability records only exceptions, so this expands the week and removes
    them. Kept here rather than left to the agent because "which days are
    left" is arithmetic, and the agent does not do arithmetic.
    """
    week = date.fromisoformat(context["week_start"])
    lost = {
        entry["date"]
        for entry in context["availability"]["unavailable"]
        if not entry["available"]
    }
    return [
        day.isoformat()
        for day in (week + timedelta(days=offset) for offset in range(7))
        if day.isoformat() not in lost
    ]


def build_context_reader(
    repo: SQLiteRepository,
    config: Config,
    week_start: date | None = None,
    rejected_problems: list[str] | None = None,
):
    """ADK adapter: publish `gather_context` to session state.

    `rejected_problems` are the hard-rule violations of a previous attempt at
    the same week, rendered into the instructions by `replan_note`.

    Imported lazily so this module stays usable -- and testable -- without the
    optional `coach` extra installed.
    """
    from google.adk.agents import BaseAgent
    from google.adk.events import Event, EventActions

    class ContextReader(BaseAgent):
        """Writes the planning picture to session state. Calls no model."""

        async def _run_async_impl(self, ctx):  # noqa: ANN001 - ADK's signature
            state = gather_context(repo, config, week_start)
            state["rejected_problems"] = list(rejected_problems or [])
            state.update(derive_summaries(state))
            yield Event(
                author=self.name,
                actions=EventActions(state_delta=state),
            )

    return ContextReader(name="context_reader")
