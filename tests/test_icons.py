"""Exercise icon mapping.

Hevy provides no imagery, so every template is mapped to a movement-pattern
figure. The contract this file defends: nothing in the 461-template catalogue
may resolve to an icon the frontend cannot draw, and nothing may silently fall
through to the generic dumbbell when a real pattern applies.
"""

from __future__ import annotations

import pytest

from fitness_ledger import icons


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Bench Press (Barbell)", "bench-press"),
        ("Chest Press (Machine)", "bench-press"),
        ("Lat Pulldown (Machine)", "pulldown"),
        ("Seated Cable Row - V Grip (Cable)", "row"),
        ("Iso-Lateral Row (Machine)", "row"),
        ("Squat (Barbell)", "squat"),
        ("Leg Press Horizontal (Machine)", "squat"),
        ("Romanian Deadlift (Barbell)", "deadlift"),
        ("Seated Shoulder Press (Machine)", "overhead-press"),
        ("Lateral Raise (Dumbbell)", "lateral-raise"),
        ("Triceps Pushdown", "triceps"),
        ("Hammer Curl (Dumbbell)", "biceps-curl"),
        ("Preacher Curl (Machine)", "biceps-curl"),
        ("Crunch (Weighted)", "crunch"),
        ("Standing Calf Raise", "calf-raise"),
        ("Hip Thrust (Barbell)", "hip-thrust"),
        ("Pull Up", "pullup"),
        ("Running", "run"),
    ],
)
def test_known_exercises_map_to_their_movement(title, expected):
    assert icons.icon_for(title) == expected


def test_specific_patterns_beat_generic_ones():
    # "Chest Press" must not fall into the generic press bucket, and a pulldown
    # is not a row even though both are pulls.
    assert icons.icon_for("Chest Press (Machine)") == "bench-press"
    assert icons.icon_for("Lat Pulldown") == "pulldown"
    # "Preacher Curl" hits the curl rule, not the leg-curl rule above it.
    assert icons.icon_for("Preacher Curl") == "biceps-curl"
    assert icons.icon_for("Lying Leg Curl") == "leg-curl"


def test_unknown_title_falls_back_to_the_primary_muscle():
    choice = icons.choose("Some Novel Contraption", "quadriceps")
    assert choice.icon == "squat"
    assert choice.source == "muscle"


def test_unknown_everything_still_returns_a_named_icon():
    choice = icons.choose("???", None)
    assert choice.icon == icons.DEFAULT_ICON
    assert choice.source == "default"


def test_every_reachable_icon_is_declared():
    # ALL_ICONS is the contract with the frontend's figure set.
    for icon, _ in icons.TITLE_RULES:
        assert icon in icons.ALL_ICONS
    for icon in icons.MUSCLE_ICONS.values():
        assert icon in icons.ALL_ICONS


def test_the_whole_catalogue_resolves(tmp_path):
    """Every template in the real Hevy catalogue gets a drawable icon."""
    from fitness_ledger.config import Config
    from fitness_ledger.db import SQLiteRepository

    config = Config.load()
    if not config.db_path.exists():
        pytest.skip("no local cache to check the catalogue against")

    with SQLiteRepository(config.db_path, config.local_utc_offset_minutes) as repo:
        templates = repo.get_templates()
    if not templates:
        pytest.skip("catalogue not synced")

    unmapped = []
    for template in templates.values():
        icon = icons.icon_for(template.title, template.primary_muscle_group)
        assert icon in icons.ALL_ICONS, f"{template.title} -> unknown icon {icon}"
        if icon == icons.DEFAULT_ICON:
            unmapped.append(template.title)

    # A handful of oddities may legitimately land on the generic figure, but if
    # most of the catalogue does, the rules have stopped working.
    assert len(unmapped) < len(templates) * 0.25, f"too many unmapped: {unmapped[:10]}"
