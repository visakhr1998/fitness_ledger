"""Unit tests for the rules engine.

No database, no network, no model: fixtures are hand-built so every expected
number here can be checked by hand.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from fitness_ledger.models import ExerciseTemplate, SetEntry
from fitness_ledger.volume import (
    best_set_per_session,
    compute_volume,
    coverage,
    default_targets,
    estimate_1rm,
    is_working_set,
    unmapped_templates,
    week_start,
    week_window,
    weekly_series,
)

MONDAY = date(2026, 7, 27)
TUESDAY = date(2026, 7, 28)
WEDNESDAY = date(2026, 7, 29)
NEXT_MONDAY = date(2026, 8, 3)

TEMPLATES = {
    # chest primary, triceps + shoulders secondary
    "BENCH": ExerciseTemplate("BENCH", "Bench Press (Barbell)", "weight_reps", "chest", ("triceps", "shoulders")),
    # back primary, biceps secondary
    "ROW": ExerciseTemplate("ROW", "Barbell Row", "weight_reps", "upper_back", ("biceps", "lats")),
    # isolation, no secondaries
    "CURL": ExerciseTemplate("CURL", "Biceps Curl", "weight_reps", "biceps", ()),
    # a template whose secondary repeats its primary -- must not double count
    "SQUAT": ExerciseTemplate("SQUAT", "Squat", "weight_reps", "quadriceps", ("quadriceps", "glutes")),
    # cardio is not a strength volume target
    "RUN": ExerciseTemplate("RUN", "Treadmill", "distance_duration", "cardio", ()),
}


def make_set(template_id: str, day: date, set_type: str = "normal", weight=100.0, reps=10):
    return SetEntry(
        workout_id=f"w-{day.isoformat()}",
        local_date=day,
        exercise_template_id=template_id,
        exercise_title=TEMPLATES[template_id].title if template_id in TEMPLATES else "?",
        set_type=set_type,
        weight_kg=weight,
        reps=reps,
    )


# --- effective set counting ------------------------------------------------


def test_primary_counts_one_secondary_counts_half():
    sets = [make_set("BENCH", MONDAY)]
    rollup = compute_volume(sets, TEMPLATES, MONDAY, NEXT_MONDAY)

    assert rollup.get("chest").effective_sets == 1.0
    assert rollup.get("triceps").effective_sets == 0.5
    assert rollup.get("shoulders").effective_sets == 0.5
    assert rollup.working_sets == 1


def test_secondary_weight_is_configurable():
    sets = [make_set("BENCH", MONDAY)]
    rollup = compute_volume(sets, TEMPLATES, MONDAY, NEXT_MONDAY, secondary_weight=0.25)

    assert rollup.get("chest").effective_sets == 1.0
    assert rollup.get("triceps").effective_sets == 0.25


def test_warmups_excluded_by_default_and_included_when_enabled():
    sets = [
        make_set("BENCH", MONDAY, "warmup"),
        make_set("BENCH", MONDAY, "warmup"),
        make_set("BENCH", MONDAY, "normal"),
    ]

    default = compute_volume(sets, TEMPLATES, MONDAY, NEXT_MONDAY)
    assert default.get("chest").effective_sets == 1.0
    assert default.working_sets == 1

    with_warmups = compute_volume(sets, TEMPLATES, MONDAY, NEXT_MONDAY, count_warmup_sets=True)
    assert with_warmups.get("chest").effective_sets == 3.0


@pytest.mark.parametrize(
    "set_type,expected", [("normal", True), ("failure", True), ("dropset", True), ("warmup", False)]
)
def test_working_set_types(set_type, expected):
    assert is_working_set(make_set("BENCH", MONDAY, set_type)) is expected


def test_secondary_matching_primary_is_not_double_counted():
    sets = [make_set("SQUAT", MONDAY)]
    rollup = compute_volume(sets, TEMPLATES, MONDAY, NEXT_MONDAY)

    assert rollup.get("quadriceps").effective_sets == 1.0
    assert rollup.get("quadriceps").secondary_sets == 0
    assert rollup.get("glutes").effective_sets == 0.5


def test_cardio_is_not_a_volume_target():
    rollup = compute_volume([make_set("RUN", MONDAY)], TEMPLATES, MONDAY, NEXT_MONDAY)
    assert "cardio" not in rollup.muscles


# --- frequency -------------------------------------------------------------


def test_frequency_counts_distinct_days_not_sets():
    sets = [
        make_set("BENCH", MONDAY),
        make_set("BENCH", MONDAY),
        make_set("BENCH", MONDAY),
        make_set("BENCH", WEDNESDAY),
    ]
    rollup = compute_volume(sets, TEMPLATES, MONDAY, NEXT_MONDAY)

    assert rollup.get("chest").effective_sets == 4.0
    assert rollup.get("chest").frequency == 2


def test_secondary_involvement_counts_toward_frequency():
    sets = [make_set("BENCH", MONDAY), make_set("CURL", WEDNESDAY)]
    rollup = compute_volume(sets, TEMPLATES, MONDAY, NEXT_MONDAY)

    # triceps only ever a secondary, so it is trained on one day
    assert rollup.get("triceps").frequency == 1
    assert rollup.get("biceps").frequency == 1


def test_workout_count_is_distinct_workouts():
    sets = [make_set("BENCH", MONDAY), make_set("ROW", MONDAY), make_set("CURL", WEDNESDAY)]
    rollup = compute_volume(sets, TEMPLATES, MONDAY, NEXT_MONDAY)
    assert rollup.workouts == 2


# --- windows ---------------------------------------------------------------


def test_window_is_closed_open():
    sets = [make_set("BENCH", MONDAY), make_set("BENCH", NEXT_MONDAY)]
    rollup = compute_volume(sets, TEMPLATES, MONDAY, NEXT_MONDAY)

    assert rollup.get("chest").effective_sets == 1.0, "end date must be exclusive"


def test_sets_before_window_are_excluded():
    sets = [make_set("BENCH", date(2026, 7, 26))]
    rollup = compute_volume(sets, TEMPLATES, MONDAY, NEXT_MONDAY)
    assert rollup.get("chest").effective_sets == 0.0


def test_week_start_and_window():
    assert week_start(WEDNESDAY) == MONDAY
    assert week_start(MONDAY) == MONDAY
    assert week_window(WEDNESDAY) == (MONDAY, NEXT_MONDAY)
    # Sunday-start weeks
    assert week_start(WEDNESDAY, week_starts_on=6) == date(2026, 7, 26)


def test_weekly_series_produces_one_rollup_per_week():
    sets = [make_set("BENCH", MONDAY), make_set("BENCH", date(2026, 8, 5))]
    series = weekly_series(sets, TEMPLATES, MONDAY, date(2026, 8, 10))

    assert len(series) == 2
    assert series[0].get("chest").effective_sets == 1.0
    assert series[1].get("chest").effective_sets == 1.0


# --- unmapped templates ----------------------------------------------------


def test_unknown_template_is_skipped_not_guessed():
    sets = [make_set("BENCH", MONDAY), SetEntry("w1", MONDAY, "MYSTERY", "Some Machine", "normal", 50, 10)]
    rollup = compute_volume(sets, TEMPLATES, MONDAY, NEXT_MONDAY)

    assert rollup.working_sets == 1
    assert unmapped_templates(sets, TEMPLATES) == {"MYSTERY": "Some Machine"}


# --- tonnage ---------------------------------------------------------------


def test_tonnage_follows_the_same_weighting():
    sets = [make_set("BENCH", MONDAY, weight=100, reps=5)]
    rollup = compute_volume(sets, TEMPLATES, MONDAY, NEXT_MONDAY)

    assert rollup.get("chest").tonnage_kg == 500.0
    assert rollup.get("triceps").tonnage_kg == 250.0


def test_bodyweight_sets_contribute_volume_but_no_tonnage():
    entry = SetEntry("w1", MONDAY, "CURL", "Curl", "normal", weight_kg=None, reps=12)
    rollup = compute_volume([entry], TEMPLATES, MONDAY, NEXT_MONDAY)

    assert rollup.get("biceps").effective_sets == 1.0
    assert rollup.get("biceps").tonnage_kg == 0.0


# --- coverage --------------------------------------------------------------


def test_coverage_reports_deficit_against_target():
    sets = [make_set("BENCH", MONDAY) for _ in range(4)]
    rollup = compute_volume(sets, TEMPLATES, MONDAY, NEXT_MONDAY)
    rows = {row["muscle_group"]: row for row in coverage(rollup, default_targets())}

    assert rows["chest"]["effective_sets"] == 4.0
    assert rows["chest"]["target_sets"] == 14
    assert rows["chest"]["sets_deficit"] == 10.0
    assert rows["chest"]["pct_of_target"] == 29
    # untouched muscle groups still appear, at zero
    assert rows["hamstrings"]["effective_sets"] == 0.0
    assert rows["hamstrings"]["frequency"] == 0


def test_target_is_scaled_to_the_window_length():
    # Four weeks of 9 chest sets a week is 36 sets against a 56-set target (64%),
    # not 36 against 14 (257%).
    sets = [make_set("BENCH", MONDAY) for _ in range(36)]
    rollup = compute_volume(sets, TEMPLATES, MONDAY, MONDAY + timedelta(days=28))
    row = {r["muscle_group"]: r for r in coverage(rollup, default_targets())}["chest"]

    assert row["weeks"] == 4.0
    assert row["effective_sets"] == 36.0
    assert row["sets_per_week"] == 9.0
    assert row["target_sets"] == 56.0
    assert row["target_sets_per_week"] == 14
    assert row["pct_of_target"] == 64
    assert row["sets_deficit"] == 20.0


def test_single_week_window_leaves_targets_unscaled():
    sets = [make_set("BENCH", MONDAY) for _ in range(7)]
    rollup = compute_volume(sets, TEMPLATES, MONDAY, NEXT_MONDAY)
    row = {r["muscle_group"]: r for r in coverage(rollup, default_targets())}["chest"]

    assert row["weeks"] == 1.0
    assert row["target_sets"] == 14
    assert row["sets_per_week"] == 7.0


def test_sub_week_window_is_not_scaled_below_one_week():
    # A three-day window should not make the target three sevenths of itself and
    # flatter the numbers.
    sets = [make_set("BENCH", MONDAY) for _ in range(7)]
    rollup = compute_volume(sets, TEMPLATES, MONDAY, MONDAY + timedelta(days=3))
    row = {r["muscle_group"]: r for r in coverage(rollup, default_targets())}["chest"]

    assert row["weeks"] == 1.0
    assert row["target_sets"] == 14


def test_coverage_is_sorted_worst_first():
    sets = [make_set("BENCH", MONDAY) for _ in range(14)]
    rollup = compute_volume(sets, TEMPLATES, MONDAY, NEXT_MONDAY)
    rows = coverage(rollup, default_targets())

    assert rows[0]["pct_of_target"] == 0
    assert rows[-1]["muscle_group"] == "chest"


# --- progression -----------------------------------------------------------


def test_epley_and_brzycki_1rm():
    assert estimate_1rm(100, 1) == 100
    assert estimate_1rm(100, 10) == pytest.approx(133.33, abs=0.01)
    assert estimate_1rm(100, 10, "brzycki") == pytest.approx(133.33, abs=0.01)
    assert estimate_1rm(0, 5) == 0.0
    assert estimate_1rm(100, 0) == 0.0


def test_best_set_per_session_picks_top_e1rm_and_ignores_warmups():
    sets = [
        make_set("BENCH", MONDAY, "warmup", weight=200, reps=10),  # must be ignored
        make_set("BENCH", MONDAY, "normal", weight=100, reps=5),  # e1RM 116.7
        make_set("BENCH", MONDAY, "normal", weight=95, reps=8),  # e1RM 120.3, the best
        make_set("BENCH", WEDNESDAY, "normal", weight=105, reps=5),
    ]
    sessions = best_set_per_session(sets, "BENCH")

    assert len(sessions) == 2
    assert sessions[0]["weight_kg"] == 95 and sessions[0]["reps"] == 8
    assert sessions[0]["e1rm"] == pytest.approx(120.3, abs=0.1)
    assert sessions[1]["date"] == WEDNESDAY
    assert sessions[0]["date"] < sessions[1]["date"], "oldest first"
