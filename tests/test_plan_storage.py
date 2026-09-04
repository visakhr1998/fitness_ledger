"""Plans are append-only, and the assembler is the only thing that writes them.

A revision is a new row that supersedes the old one, never an edit. The reason a
week looked the way it did is part of the record -- the coach reads the previous
plan to tell a new shortfall from one that has persisted, and a replan that
overwrote its predecessor would erase exactly that.
"""

from __future__ import annotations

from datetime import date

import pytest

from fitness_ledger.coach import assembler
from fitness_ledger.db import SQLiteRepository
from fitness_ledger.models import Plan, PlannedExercise, PlannedSession
from fitness_ledger.planning import Preferences

WEEK = date(2026, 8, 10)


@pytest.fixture()
def repo(tmp_path):
    with SQLiteRepository(tmp_path / "plans.db", 120) as repository:
        yield repository


def a_plan(rationale: str = "first", week: date = WEEK) -> Plan:
    return Plan(
        week_start=week,
        rationale=rationale,
        sessions=(
            PlannedSession(
                local_date=week,
                kind="lift",
                focus="upper",
                exercises=(PlannedExercise("BENCH", "Bench Press", 4, ("chest",)),),
            ),
            PlannedSession(local_date=week, kind="run", distance_km=6.0),
        ),
    )


# --- storage ----------------------------------------------------------------


def test_a_plan_round_trips_with_its_set_counts(repo):
    stored = repo.add_plan(a_plan())
    read = repo.get_plan(stored.id)

    assert read.week_start == WEEK
    assert read.sessions[0].exercises[0].sets == 4
    assert read.sessions[0].exercises[0].targets == ("chest",)
    assert read.sessions[1].distance_km == 6.0
    assert read.total_sets == 4


def test_replanning_supersedes_rather_than_overwrites(repo):
    first = repo.add_plan(a_plan("first"))
    second = repo.add_plan(a_plan("revised"))

    assert second.supersedes == first.id
    assert repo.get_plan(first.id).status == "superseded"
    assert repo.get_plan(first.id).rationale == "first"  # still readable
    assert repo.latest_plan(WEEK).rationale == "revised"


def test_a_plan_for_a_different_week_supersedes_nothing(repo):
    first = repo.add_plan(a_plan("first", WEEK))
    later = repo.add_plan(a_plan("next week", date(2026, 8, 17)))

    assert later.supersedes is None
    assert repo.get_plan(first.id).status == "proposed"


def test_the_agent_trace_survives_storage(repo):
    """It cannot be reconstructed afterwards and the trajectory eval needs it."""
    trace = ({"tool": "get_exercise_pool", "args": {}},)
    stored = repo.add_plan(Plan(week_start=WEEK, agent_trace=trace))

    assert repo.get_plan(stored.id).agent_trace == trace


def test_latest_plan_without_a_week_spans_every_week(repo):
    repo.add_plan(a_plan("older", date(2026, 8, 3)))
    repo.add_plan(a_plan("newer", date(2026, 8, 17)))

    assert repo.latest_plan().rationale == "newer"


def test_no_plans_yet_is_a_None_not_an_error(repo):
    assert repo.latest_plan() is None
    assert repo.get_plan(1) is None


def test_an_unknown_status_is_refused(repo):
    stored = repo.add_plan(a_plan())
    with pytest.raises(ValueError):
        repo.set_plan_status(stored.id, "maybe")


# --- preferences ------------------------------------------------------------


def test_preferences_default_when_nothing_is_set(repo):
    assert assembler.planning_preferences(repo) == Preferences()


def test_preferences_come_from_settings(repo):
    repo.set_setting("max_sets_per_session", "16")
    repo.set_setting("allow_run_after_leg_day", "false")

    prefs = assembler.planning_preferences(repo)

    assert prefs.max_sets_per_session == 16
    assert prefs.allow_run_after_leg_day is False


def test_a_malformed_preference_falls_back_rather_than_crashing(repo):
    """A bad setting should give a safe week, not no week."""
    repo.set_setting("max_sets_per_session", "lots")

    assert assembler.planning_preferences(repo).max_sets_per_session == Preferences().max_sets_per_session


# --- assembly ---------------------------------------------------------------


def a_result() -> dict:
    return {
        "week_start": WEEK.isoformat(),
        "training_days": [WEEK.isoformat(), "2026-08-12"],
        "proposal": {
            "sessions": [
                {
                    "session_date": WEEK.isoformat(),
                    "kind": "lift",
                    "focus": "upper",
                    "exercises": [
                        {"exercise_template_id": "BENCH", "title": "Bench", "targets": ["chest"]}
                    ],
                }
            ],
            "rationale": "chest was six short",
            "trade_offs": "",
        },
        "ledger_state": {
            "volume": {
                "muscles": [
                    {"muscle_group": "chest", "sets_deficit": 6, "target_sets": 6},
                    {"muscle_group": "calves", "sets_deficit": 8, "target_sets": 8},
                    {"muscle_group": "lats", "sets_deficit": 0, "target_sets": 14},
                ]
            }
        },
        "exercise_pool": [{"id": "BENCH"}],
        "agent_trace": [],
    }


def test_assembly_allocates_stores_and_reports(repo, tmp_path):
    from fitness_ledger.config import Config

    out = assembler.assemble(repo, Config.load(), a_result())
    plan = out["plan"]

    assert plan.id is not None
    assert plan.sessions[0].exercises[0].sets == 6
    assert out["problems"] == []
    # calves had a deficit and nothing trains it -- said, not swallowed.
    assert out["unplaced"] == ["calves"]
    assert "calves" in plan.trade_offs


def test_the_agents_own_trade_offs_are_kept_alongside_the_computed_ones(repo):
    from fitness_ledger.config import Config

    result = a_result()
    result["proposal"]["trade_offs"] = "Dropped the second run."

    plan = assembler.assemble(repo, Config.load(), result)["plan"]

    assert "Dropped the second run." in plan.trade_offs
    assert "calves" in plan.trade_offs


def test_dry_run_allocates_without_storing(repo):
    from fitness_ledger.config import Config

    out = assembler.assemble(repo, Config.load(), a_result(), persist=False)

    assert out["plan"].id is None
    assert repo.latest_plan() is None


def test_an_exercise_that_cannot_be_placed_is_reported_not_swallowed(repo):
    """The silent half of an invented template id.

    `validate` walks the *allocated* sessions, and allocation drops an exercise
    that serves no muscle before it ever gets there. So an invented id is
    reported only when the agent also filled `targets`; omit them and the same
    id disappears with nothing said. A week that looks thinly planned is then
    really a week whose choices were discarded in silence.
    """
    from fitness_ledger.config import Config

    result = a_result()
    result["proposal"]["sessions"][0]["exercises"] += [
        # Not in the pool and naming no muscle -- the shape that vanished.
        {"exercise_template_id": "squat", "title": "Squat", "targets": []},
    ]

    out = assembler.assemble(repo, Config.load(), result, persist=False)

    planned = {e.exercise_template_id for s in out["plan"].sessions for e in s.exercises}
    assert planned == {"BENCH"}, "the unknown exercise should still be dropped"
    assert any("squat" in problem for problem in out["problems"]), out["problems"]


def test_an_unplaceable_exercise_is_reported_once_not_twice(repo):
    """An invented id that *does* name a muscle survives allocation, so
    `validate` already reports it against the pool. Reporting it again here
    would make one fault read as two."""
    from fitness_ledger.config import Config

    result = a_result()
    result["proposal"]["sessions"][0]["exercises"] += [
        {"exercise_template_id": "squat", "title": "Squat", "targets": ["quadriceps"]},
    ]
    result["ledger_state"]["volume"]["muscles"].append(
        {"muscle_group": "quadriceps", "sets_deficit": 4, "target_sets": 8}
    )

    problems = assembler.assemble(repo, Config.load(), result, persist=False)["problems"]

    assert sum("squat" in problem for problem in problems) == 1, problems


def test_a_pool_exercise_squeezed_by_the_ceiling_is_not_called_unplannable(repo):
    """Dropping to fit the per-session ceiling is allocation working, and the
    trade-offs already carry it. Only unknown ids are faults."""
    assert assembler.unplannable(
        [{"exercises": [{"exercise_template_id": "BENCH", "title": "Bench"}]}],
        (),
        {"BENCH"},
    ) == []


def test_only_positive_deficits_drive_allocation(repo):
    """A muscle at or above target is not short, and planning against it would
    invent volume."""
    assert "lats" not in assembler.deficits(a_result()["ledger_state"])


# --- the read path ----------------------------------------------------------


def test_the_week_view_says_so_when_nothing_is_planned(repo):
    from fitness_ledger.config import Config
    from fitness_ledger.sections import plan_section

    view = plan_section(repo, Config.load())

    assert view["available"] is False
    assert view["plan"] is None
    assert view["problems"] == []


def test_the_week_view_carries_the_plan_and_its_adherence(repo):
    from fitness_ledger.config import Config
    from fitness_ledger.sections import plan_section

    repo.add_plan(a_plan())
    view = plan_section(repo, Config.load())

    assert view["available"] is True
    assert view["plan"]["week_start"] == WEEK.isoformat()
    assert view["plan"]["total_sets"] == 4
    assert view["adherence"]["sessions_ahead"] >= 0


def test_problems_are_recomputed_not_stored(repo):
    """They are a function of the plan and the *current* preferences, so a
    stored verdict would go stale the moment a preference changed."""
    from fitness_ledger.config import Config
    from fitness_ledger.sections import plan_section

    repo.add_plan(a_plan())
    assert plan_section(repo, Config.load())["problems"] == []

    # Tighten the ceiling under the stored plan; the view must notice.
    repo.set_setting("max_sets_per_session", "2")
    problems = plan_section(repo, Config.load())["problems"]

    assert any("over the 2 allowed in a session" in p for p in problems)


def test_a_week_with_no_plan_of_its_own_is_not_silently_another_weeks(repo):
    from fitness_ledger.config import Config
    from fitness_ledger.sections import plan_section

    repo.add_plan(a_plan())
    view = plan_section(repo, Config.load(), "2026-09-07")

    assert view["available"] is False


# --- exercises the agent named but did not describe -------------------------


def a_pool():
    return [
        {
            "exercise_template_id": "CALF",
            "title": "Calf Raise",
            "primary_muscle_group": "calves",
            "secondary_muscle_groups": [],
        },
        {
            "exercise_template_id": "ROW",
            "title": "Barbell Row",
            "primary_muscle_group": "upper_back",
            "secondary_muscle_groups": ["lats", "biceps"],
        },
    ]


def a_session(exercises):
    return [{"session_date": WEEK.isoformat(), "kind": "lift", "exercises": exercises}]


def test_an_exercise_with_no_targets_is_looked_up_not_discarded():
    """Observed in a real run: the agent proposed `Calf Raise` with an empty
    targets list. Allocation works from targets, so it was worth nothing, got
    zero sets and vanished -- leaving a week of four sets."""
    from fitness_ledger.coach.assembler import with_targets

    filled = with_targets(
        a_session([{"exercise_template_id": "CALF", "title": "Calf Raise", "targets": []}]),
        a_pool(),
    )

    assert filled[0]["exercises"][0]["targets"] == ["calves"]


def test_the_lookup_takes_secondaries_too():
    from fitness_ledger.coach.assembler import with_targets

    filled = with_targets(
        a_session([{"exercise_template_id": "ROW", "title": "Barbell Row", "targets": []}]),
        a_pool(),
    )

    assert filled[0]["exercises"][0]["targets"] == ["upper_back", "lats", "biceps"]


def test_what_the_agent_did_say_is_left_alone():
    """It chose those muscles for a reason -- it may be using a compound for
    one head of it. Overriding a stated intent would be worse than filling a
    silence."""
    from fitness_ledger.coach.assembler import with_targets

    filled = with_targets(
        a_session([{"exercise_template_id": "ROW", "title": "Barbell Row", "targets": ["lats"]}]),
        a_pool(),
    )

    assert filled[0]["exercises"][0]["targets"] == ["lats"]


def test_an_unknown_exercise_is_left_as_it_came():
    """Nothing to look it up with. It will fail pool validation, which is the
    right place to say so."""
    from fitness_ledger.coach.assembler import with_targets

    filled = with_targets(
        a_session([{"exercise_template_id": "GHOST", "title": "?", "targets": []}]),
        a_pool(),
    )

    assert filled[0]["exercises"][0]["targets"] == []
