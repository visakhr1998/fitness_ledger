"""Every fixture must actually be what it says it is.

This is the load-bearing half of the eval harness, and it needs no model. A
fixture that has quietly stopped representing "back neglected" turns every eval
built on it into a test of nothing that still passes — the worst failure mode an
eval suite has, because it reports success.

So each fixture is checked against the rules engine: does the deficit really
show back short, is the stall really detectable, does the recovery rule really
fire. Only once that holds is it worth asking a model anything.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

import fixtures
from fitness_ledger.coach.context import deficit_summary, gather_context, training_days
from fitness_ledger.config import Config
from fitness_ledger.db import SQLiteRepository


@pytest.fixture()
def config(tmp_path):
    return replace(Config.load(), db_path=tmp_path / "fixture.db")


@pytest.fixture()
def repo(config):
    with SQLiteRepository(config.db_path, 120) as repository:
        yield repository


def seeded(repo, config, name: str) -> dict:
    """Build a fixture and read it back the way the coach will."""
    fixtures.build(repo, name)
    return gather_context(repo, config)


def deficits(context: dict) -> dict[str, float]:
    return {
        row["muscle_group"]: row["sets_deficit"]
        for row in context["ledger_state"]["volume"]["muscles"]
        if (row["sets_deficit"] or 0) > 0
    }


# --- the catalog itself -----------------------------------------------------


def test_every_fixture_has_a_premise_and_an_expectation():
    """A fixture whose correct behaviour is not written down cannot be
    evaluated against anything."""
    for fixture in fixtures.FIXTURES:
        assert fixture.premise.strip(), fixture.name
        assert fixture.expect.strip(), fixture.name


def test_fixture_names_are_unique():
    names = [fixture.name for fixture in fixtures.FIXTURES]
    assert len(names) == len(set(names))


def test_every_fixture_builds_without_error(repo, config):
    """Cheap, and it catches a template id typo before the eval does."""
    for fixture in fixtures.FIXTURES:
        with SQLiteRepository(config.db_path.with_name(f"{fixture.name}.db"), 120) as scratch:
            fixtures.build(scratch, fixture.name)


# --- each premise, checked against the rules engine -------------------------


def test_back_neglected_really_is_short_in_the_back(repo, config):
    short = deficits(seeded(repo, config, "back_neglected"))

    assert short.get("lats", 0) > 0
    assert short.get("upper_back", 0) > 0
    # ...and the muscles that *were* trained are not the story.
    assert short.get("chest", 0) < short["lats"]


def test_all_targets_met_leaves_nothing_to_close(repo, config):
    context = seeded(repo, config, "all_targets_met")

    assert deficits(context) == {}
    assert "No muscle group is below target" in deficit_summary(context)


def test_no_history_reads_every_muscle_as_short(repo, config):
    short = deficits(seeded(repo, config, "no_history"))

    # 16 default targets, all untouched.
    assert len(short) >= 14


def test_bench_stalled_is_visible_in_progression_state(repo, config):
    context = seeded(repo, config, "bench_stalled")

    bench = next(
        row for row in context["ledger_state"]["progression"]
        if row["exercise_template_id"] == "BENCH"
    )
    assert bench["stalled"] is True
    assert bench["ready_to_progress"] is False


def test_ready_to_progress_really_is_ready(repo, config):
    context = seeded(repo, config, "ready_to_progress")

    squat = next(
        row for row in context["ledger_state"]["progression"]
        if row["exercise_template_id"] == "SQUAT"
    )
    assert squat["ready_to_progress"] is True


def test_poor_sleep_makes_the_recovery_rule_fire(repo, config):
    context = seeded(repo, config, "poor_sleep")

    rules = {insight["rule"] for insight in context["ledger_state"]["insights"]}
    assert "recovery_flag" in rules


def test_a_normal_week_does_not_fire_the_recovery_rule(repo, config):
    """The control. A rule that fires on every fixture measures nothing."""
    context = seeded(repo, config, "back_neglected")

    rules = {insight["rule"] for insight in context["ledger_state"]["insights"]}
    assert "recovery_flag" not in rules


def test_running_behind_fires_the_running_shortfall(repo, config):
    context = seeded(repo, config, "running_behind")

    rules = {insight["rule"] for insight in context["ledger_state"]["insights"]}
    assert "running_shortfall" in rules
    assert context["goals"]["running_target"]["distance_km_per_week"] == 25.0


def test_running_only_has_no_running_problem(repo, config):
    """20 km across two runs a week, against a 20 km / 2 target."""
    context = seeded(repo, config, "running_only")

    rules = {insight["rule"] for insight in context["ledger_state"]["insights"]}
    assert "running_shortfall" not in rules
    assert len(deficits(context)) >= 14  # ...but nothing has been lifted


def test_two_days_lost_removes_exactly_those_days(repo, config):
    context = seeded(repo, config, "two_days_lost")
    days = training_days(context)

    assert len(days) == 5
    week = context["week_start"]
    assert week in days  # Monday survives


def test_no_gym_early_week_loses_monday_and_tuesday(repo, config):
    context = seeded(repo, config, "no_gym_early_week")
    days = training_days(context)

    assert len(days) == 5
    assert context["week_start"] not in days  # Monday is gone


# --- the shape the coach will actually be handed ----------------------------


def test_the_deficit_summary_names_the_neglected_muscle(repo, config):
    """The planner is handed this text, not the raw rows. If the rendering
    stops naming the muscle, the eval is grading a prompt that never said it."""
    summary = deficit_summary(seeded(repo, config, "back_neglected"))

    assert "lats" in summary
    assert "short" in summary


def test_a_fixture_is_reproducible(repo, config, tmp_path):
    """Built twice, the same premise reads the same. Dates are relative to
    today, so this also guards against a fixture drifting out of its window."""
    first = deficits(seeded(repo, config, "back_neglected"))

    other = replace(config, db_path=tmp_path / "again.db")
    with SQLiteRepository(other.db_path, 120) as scratch:
        fixtures.build(scratch, "back_neglected")
        second = deficits(gather_context(scratch, other))

    assert first == second


def test_the_catalog_can_train_every_muscle_that_has_a_target():
    """A muscle with no exercise in the catalog is a permanent deficit the
    coach cannot close -- it would show in every fixture as the same phantom
    gap, and "all targets met" could never be true."""
    from fitness_ledger.volume import default_targets

    covered = set()
    for template in fixtures.CATALOG:
        covered.add(template.primary_muscle_group)
        covered |= set(template.secondary_muscle_groups)

    assert not set(default_targets()) - covered
