"""Detection rules.

Each rule is tested for both firing and, just as importantly, staying quiet --
an insight engine that cries wolf gets ignored, which is the same as not having
one.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

from fitness_ledger.insights import (
    coverage_gap,
    detect,
    progression_insights,
    recovery_flag,
    volume_drop,
)
from fitness_ledger.models import ExerciseTemplate, SetEntry, VolumeTarget
from fitness_ledger.progression import RepRange

# A Wednesday, so "last complete week" is unambiguous.
TODAY = date(2026, 7, 29)
LAST_WEEK = date(2026, 7, 20)  # Monday of the last complete week

TEMPLATES = {
    "BENCH": ExerciseTemplate("BENCH", "Bench Press", "weight_reps", "chest", ("triceps",), "barbell"),
    "SQUAT": ExerciseTemplate("SQUAT", "Squat", "weight_reps", "quadriceps", (), "barbell"),
}
TARGETS = {
    "chest": VolumeTarget("chest", 14, 2),
    "quadriceps": VolumeTarget("quadriceps", 14, 2),
}


def make_sets(day: date, count: int, tid: str = "BENCH", weight: float = 100.0, reps: int = 8):
    return [
        SetEntry(f"w-{day}-{i}", day, tid, TEMPLATES[tid].title, "normal", weight, reps)
        for i in range(count)
    ]


def weeks_of(counts: list[int], tid: str = "BENCH", start_week_offset: int = 5):
    """Build sets across consecutive weeks, oldest first."""
    sets = []
    for index, count in enumerate(counts):
        day = LAST_WEEK - timedelta(days=7 * (len(counts) - 1 - index))
        sets += make_sets(day, count, tid)
    return sets


# --- volume drop -----------------------------------------------------------


def test_volume_drop_fires_when_last_week_is_well_below_baseline():
    # Four weeks of 10 sets, then 2.
    sets = weeks_of([10, 10, 10, 10, 2])
    found = volume_drop(sets, TEMPLATES, TODAY)

    chest = [i for i in found if i.subject == "chest"]
    assert len(chest) == 1
    assert chest[0].severity == "warn"
    assert chest[0].data["drop_pct"] == 80


def test_volume_drop_stays_quiet_within_tolerance():
    # 10, 10, 10, 10 then 8 is a 20% dip -- inside the 25% threshold.
    sets = weeks_of([10, 10, 10, 10, 8])
    assert [i for i in volume_drop(sets, TEMPLATES, TODAY) if i.subject == "chest"] == []


def test_volume_drop_ignores_trivially_small_baselines():
    # Going from 1 set a week to 0 is a 100% drop and completely uninteresting.
    sets = weeks_of([1, 1, 1, 1, 0])
    assert [i for i in volume_drop(sets, TEMPLATES, TODAY) if i.subject == "chest"] == []


def test_volume_drop_needs_a_full_baseline():
    sets = weeks_of([10, 2])
    assert volume_drop(sets, TEMPLATES, TODAY) == []


# --- coverage gap ----------------------------------------------------------


def test_coverage_gap_fires_after_two_weeks_below_frequency():
    # One session a week for two weeks, against a 2x target.
    sets = weeks_of([5, 5])
    found = {i.subject: i for i in coverage_gap(sets, TEMPLATES, TARGETS, TODAY)}

    assert found["chest"].severity == "warn"
    assert found["chest"].data["frequencies"] == [1, 1]
    assert found["chest"].data["in_programme"] is True


def test_coverage_gap_quiet_when_frequency_met_in_either_week():
    sets = make_sets(LAST_WEEK, 3) + make_sets(LAST_WEEK + timedelta(days=2), 3)
    sets += make_sets(LAST_WEEK - timedelta(days=7), 3)
    found = {i.subject for i in coverage_gap(sets, TEMPLATES, TARGETS, TODAY)}

    assert "chest" not in found


def test_untrained_muscle_is_info_not_warn():
    found = {i.subject: i for i in coverage_gap([], TEMPLATES, TARGETS, TODAY)}

    assert found["quadriceps"].severity == "info"
    assert found["quadriceps"].data["in_programme"] is False
    assert "not trained at all" in found["quadriceps"].message


# --- progression -----------------------------------------------------------


def test_progression_ready_is_surfaced():
    sets = []
    for offset in (21, 14, 7):
        day = TODAY - timedelta(days=offset)
        sets += make_sets(day, 3, "BENCH", weight=100, reps=10)
    found = progression_insights(sets, TEMPLATES, TODAY, {})

    ready = [i for i in found if i.rule == "progression_ready"]
    assert len(ready) == 1
    assert "2.5 kg" in ready[0].message


def test_stall_is_surfaced_when_nothing_moves():
    sets = []
    for offset in (21, 14, 7):
        day = TODAY - timedelta(days=offset)
        sets += make_sets(day, 3, "BENCH", weight=100, reps=8)
    found = progression_insights(sets, TEMPLATES, TODAY, {})

    stalls = [i for i in found if i.rule == "stall"]
    assert len(stalls) == 1
    assert stalls[0].severity == "warn"


def test_an_exercise_cannot_be_both_stalled_and_ready():
    sets = []
    for offset in (21, 14, 7):
        sets += make_sets(TODAY - timedelta(days=offset), 3, "BENCH", weight=100, reps=10)
    rules = {i.rule for i in progression_insights(sets, TEMPLATES, TODAY, {})}

    assert rules == {"progression_ready"}


# --- recovery --------------------------------------------------------------


def full_sleep(value: float, days: int = 28) -> dict[date, float]:
    return {TODAY - timedelta(days=offset): value for offset in range(1, days + 1)}


def test_recovery_flag_fires_on_short_sleep_against_baseline():
    sleep = full_sleep(450)
    for offset in (1, 2, 3):
        sleep[TODAY - timedelta(days=offset)] = 330  # 5.5h
    found = recovery_flag([], sleep, TODAY)

    assert len(found) == 1
    assert found[0].severity == "info", "recovery signals are informational only"
    assert found[0].rule == "recovery_flag"


def test_recovery_flag_quiet_when_sleep_is_normal():
    assert recovery_flag([], full_sleep(450), TODAY) == []


def test_recovery_flag_needs_a_baseline():
    sleep = {TODAY - timedelta(days=offset): 300 for offset in range(1, 4)}
    assert recovery_flag([], sleep, TODAY) == []


def test_recovery_flag_reports_the_users_own_pattern_not_advice():
    sleep = full_sleep(450)
    short_days = [TODAY - timedelta(days=offset) for offset in (1, 2, 3, 10, 11)]
    for day in short_days:
        sleep[day] = 330

    sets = []
    for day in short_days:
        sets += make_sets(day, 4)
    for offset in (5, 6, 7):
        sets += make_sets(TODAY - timedelta(days=offset), 12)

    found = recovery_flag(sets, sleep, TODAY)
    assert "averaged 4 working sets" in found[0].message
    assert found[0].data["sets_after_short_nights"] == 4.0
    # No prescription anywhere in the output.
    assert not any(word in found[0].message.lower() for word in ("should", "rest", "skip", "reduce"))


# --- assembly --------------------------------------------------------------


def test_detect_sorts_warnings_before_info():
    sets = weeks_of([10, 10, 10, 10, 2])
    found = detect(sets, TEMPLATES, TARGETS, full_sleep(450), TODAY)

    severities = [i.severity for i in found]
    assert severities == sorted(severities, key=lambda s: {"warn": 0, "info": 1}[s])
    assert any(i.rule == "volume_drop" for i in found)


def test_detect_on_empty_history_does_not_crash():
    found = detect([], TEMPLATES, TARGETS, {}, TODAY)
    assert all(i.rule == "coverage_gap" for i in found)


# --- a lift with no load ----------------------------------------------------
# `progression_state` stores `top_weight or None`, and Hevy records no weight
# for a Pull Up, so the ready-to-progress and stall messages were formatting
# None with `:g`. That raised TypeError and took the whole insight pass with it,
# which meant /api/insights, /api/dashboard and the coach's context all failed
# on one unloadable exercise.


def _bodyweight_sessions(reps, weeks=6, template="PULLUP"):
    from datetime import timedelta

    sets = []
    for week in range(weeks):
        day = TODAY - timedelta(weeks=weeks - week)
        sets += [
            SetEntry("w" + str(week), day, template, "Pull Up", "normal", None, rep)
            for rep in reps
        ]
    return sets


def test_a_bodyweight_lift_ready_to_progress_does_not_crash():
    sets = _bodyweight_sessions([10, 10, 10])
    templates = {
        "PULLUP": ExerciseTemplate(
            "PULLUP", "Pull Up", "reps_only", "lats", (), "none", False
        )
    }

    findings = progression_insights(sets, templates, TODAY, {}, RepRange(6, 10))

    ready = [f for f in findings if f.rule == "progression_ready"]
    assert ready, "a bodyweight lift at the top of its range should still be reported"
    # The load clause is dropped rather than rendered as None, and an
    # unloadable exercise is told to add reps rather than kilograms.
    assert "None" not in ready[0].message
    assert "kg" not in ready[0].message
    assert "add reps" in ready[0].message


def test_a_loaded_lift_still_names_its_weight():
    sets = _bodyweight_sessions([10, 10, 10])
    loaded = [replace(entry, weight_kg=60.0) for entry in sets]

    findings = progression_insights(loaded, {}, TODAY, {}, RepRange(6, 10))

    ready = [f for f in findings if f.rule == "progression_ready"]
    assert ready and "60 kg" in ready[0].message


def test_the_configured_rep_range_reaches_the_rules():
    """A configured 8-12 was honoured by the exercise screen and ignored here.

    `insight_report` passed `{**{k: default for k in overrides}, **overrides}`,
    which is just `overrides`, so every non-overridden lift fell through to
    progression_state's own hard-coded 6-10 and the two views disagreed about
    the same lift.
    """
    sets = _bodyweight_sessions([10, 10, 10])

    # Ten reps tops out a 6-10 range but sits mid-way through 8-12.
    at_default = progression_insights(sets, {}, TODAY, {}, RepRange(6, 10))
    at_configured = progression_insights(sets, {}, TODAY, {}, RepRange(8, 12))

    assert [f.rule for f in at_default] == ["progression_ready"]
    assert "progression_ready" not in [f.rule for f in at_configured]
