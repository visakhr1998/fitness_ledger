"""Double progression state and stall detection."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from fitness_ledger.models import SetEntry
from fitness_ledger.progression import (
    RepRange,
    increment_for,
    main_lifts,
    progression_state,
    stalled,
)

DAY1 = date(2026, 7, 1)
DAY2 = date(2026, 7, 8)
DAY3 = date(2026, 7, 15)


def sets_on(day: date, weight: float, reps: list[int], set_type: str = "normal", tid: str = "BENCH"):
    return [
        SetEntry("w-" + day.isoformat(), day, tid, "Bench Press", set_type, weight, rep)
        for rep in reps
    ]


def test_all_sets_at_top_of_range_is_ready_to_progress():
    sets = sets_on(DAY1, 100, [10, 10, 10])
    state = progression_state(sets, "BENCH", "Bench Press", RepRange(6, 10), "barbell")

    assert state.ready_to_progress is True
    assert state.working_weight_kg == 100
    assert state.suggested_weight_kg == 102.5
    assert "add 2.5 kg" in state.verdict


def test_one_set_short_of_the_top_is_not_ready():
    sets = sets_on(DAY1, 100, [10, 10, 9])
    state = progression_state(sets, "BENCH", "Bench Press", RepRange(6, 10), "barbell")

    assert state.ready_to_progress is False
    assert "add reps" in state.verdict


def test_below_the_bottom_of_the_range_suggests_holding():
    sets = sets_on(DAY1, 100, [5, 4])
    state = progression_state(sets, "BENCH", "Bench Press", RepRange(6, 10))

    assert state.ready_to_progress is False
    assert "below range" in state.verdict


def test_back_off_sets_do_not_block_progression():
    # A heavy top set plus a lighter back-off is the user's actual pattern; the
    # back-off says nothing about whether the working weight should go up.
    sets = sets_on(DAY1, 100, [10, 10]) + sets_on(DAY1, 80, [12])
    state = progression_state(sets, "BENCH", "Bench Press", RepRange(6, 10), "barbell")

    assert state.working_weight_kg == 100
    assert state.reps_at_working_weight == [10, 10]
    assert state.ready_to_progress is True


def test_warmups_are_ignored():
    sets = sets_on(DAY1, 120, [12], set_type="warmup") + sets_on(DAY1, 100, [8, 8])
    state = progression_state(sets, "BENCH", "Bench Press", RepRange(6, 10))

    assert state.working_weight_kg == 100
    assert state.ready_to_progress is False


def test_sessions_at_weight_counts_back_until_the_weight_changes():
    sets = sets_on(DAY1, 95, [8]) + sets_on(DAY2, 100, [8]) + sets_on(DAY3, 100, [9])
    state = progression_state(sets, "BENCH", "Bench Press", RepRange(6, 10))

    assert state.sessions_at_weight == 2
    assert state.last_session == DAY3


def test_no_data_is_reported_not_guessed():
    state = progression_state([], "BENCH", "Bench Press")
    assert state.verdict == "no data"
    assert state.working_weight_kg is None


def test_increment_depends_on_equipment():
    assert increment_for("barbell") == 2.5
    assert increment_for("dumbbell") == 2.0
    assert increment_for("machine") == 5.0
    assert increment_for(None) == 2.5
    assert increment_for("bodyweight-ish unknown") == 2.5


def test_bodyweight_exercise_suggests_reps_not_load():
    sets = sets_on(DAY1, 0, [10, 10])
    state = progression_state(sets, "BENCH", "Pull Up", RepRange(6, 10), "none")

    assert state.ready_to_progress is True
    assert state.suggested_weight_kg is None
    assert "add reps or a set" in state.verdict


def test_inverted_rep_range_is_rejected():
    with pytest.raises(ValueError):
        RepRange(10, 6)


# --- stalls ----------------------------------------------------------------


def test_three_identical_sessions_is_a_stall():
    sets = sets_on(DAY1, 100, [8, 8]) + sets_on(DAY2, 100, [8, 8]) + sets_on(DAY3, 100, [8, 8])
    assert stalled(sets, "BENCH") is True


def test_added_weight_is_not_a_stall():
    sets = sets_on(DAY1, 100, [8, 8]) + sets_on(DAY2, 100, [8, 8]) + sets_on(DAY3, 102.5, [8, 8])
    assert stalled(sets, "BENCH") is False


def test_added_reps_at_the_same_weight_is_not_a_stall():
    sets = sets_on(DAY1, 100, [8, 8]) + sets_on(DAY2, 100, [8, 8]) + sets_on(DAY3, 100, [9, 8])
    assert stalled(sets, "BENCH") is False


def test_too_few_sessions_is_not_a_stall():
    sets = sets_on(DAY1, 100, [8]) + sets_on(DAY2, 100, [8])
    assert stalled(sets, "BENCH") is False


# --- main lifts ------------------------------------------------------------


def test_main_lifts_ranked_by_working_sets_and_require_regularity():
    sets = []
    for day in (DAY1, DAY2, DAY3):
        sets += sets_on(day, 100, [8, 8, 8], tid="BENCH")
        sets += sets_on(day, 60, [10], tid="ROW")
    # A one-off accessory should not qualify, however many sets it had.
    sets += sets_on(DAY1, 20, [12, 12, 12, 12, 12], tid="FLY")

    ranked = main_lifts(sets)
    assert [tid for tid, _ in ranked] == ["BENCH", "ROW"]


def test_main_lifts_respects_limit():
    sets = []
    for index in range(5):
        for day in (DAY1, DAY2, DAY3):
            sets += sets_on(day, 50, [8], tid=f"EX{index}")
    assert len(main_lifts(sets, limit=3)) == 3


# --- load increments per equipment ------------------------------------------
# increment_for was always tested and always correct. What was untested was the
# *seam*: sync stored equipment_category as NULL for all 461 templates, so this
# function was only ever reached with None. Both halves right, the join between
# them wrong -- see test_sync_entry_points for the other side.


def test_bodyweight_and_band_work_add_no_load():
    # The failure this fix exists for: with equipment_category NULL, the app
    # suggested adding 2.5 kg to a Pull Up.
    assert increment_for("none") == 0.0
    assert increment_for("resistance_band") == 0.0
    assert increment_for("suspension") == 0.0
