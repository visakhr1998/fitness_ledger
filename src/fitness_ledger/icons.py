"""Exercise iconography.

Hevy's API returns no images -- only a title, muscle groups and equipment -- so
each template is mapped to one of a small set of movement-pattern figures drawn
in the frontend. Keying on the movement rather than the exercise means 461
templates need ~20 icons instead of 461 illustrations, and a new template the
user creates still gets a sensible figure.

The mapping is pure and deterministic so it can be tested across the whole
catalogue: no template may fall through without a named icon.
"""

from __future__ import annotations

from dataclasses import dataclass

# Ordered: the first matching rule wins, so put specific patterns before the
# generic ones they would otherwise be swallowed by.
TITLE_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("bench-press", ("bench press", "chest press", "floor press")),
    ("pushup", ("push up", "push-up", "pushup", "dip")),
    ("overhead-press", ("shoulder press", "overhead press", "military press", "arnold")),
    ("lateral-raise", ("lateral raise", "front raise", "rear delt", "reverse fly", "face pull")),
    ("chest-fly", ("fly", "flye", "pec deck", "cable crossover")),
    ("pullup", ("pull up", "pull-up", "pullup", "chin up", "chin-up", "muscle up")),
    ("pulldown", ("pulldown", "pull down", "lat prayer", "pullover")),
    ("row", ("row", "rowing")),
    ("shrug", ("shrug", "upright")),
    ("deadlift", ("deadlift", "rack pull", "good morning", "hyperextension", "back extension")),
    ("squat", ("squat", "leg press", "hack", "sissy")),
    ("lunge", ("lunge", "split squat", "step up", "bulgarian")),
    ("leg-extension", ("leg extension", "knee extension")),
    ("leg-curl", ("leg curl", "hamstring curl", "nordic")),
    ("hip-thrust", ("hip thrust", "glute bridge", "kickback", "abduction", "adduction")),
    ("calf-raise", ("calf", "toe raise")),
    ("biceps-curl", ("curl",)),
    ("triceps", ("triceps", "tricep", "pushdown", "skull", "kickback", "overhead extension")),
    ("wrist", ("wrist", "forearm", "grip", "farmer")),
    ("crunch", ("crunch", "sit up", "sit-up", "ab ", "plank", "russian twist", "leg raise", "hanging knee")),
    ("neck", ("neck",)),
    ("run", ("run", "treadmill", "sprint", "jog")),
    ("bike", ("bike", "cycling", "spin")),
    ("row-machine", ("rowing machine", "ergometer", "erg")),
    ("swim", ("swim",)),
    ("cardio", ("elliptical", "stair", "jump rope", "burpee", "walk", "hiking")),
]

# Fallback by primary muscle when the title says nothing recognisable.
MUSCLE_ICONS: dict[str, str] = {
    "chest": "bench-press",
    "lats": "pulldown",
    "upper_back": "row",
    "traps": "shrug",
    "lower_back": "deadlift",
    "shoulders": "overhead-press",
    "biceps": "biceps-curl",
    "triceps": "triceps",
    "forearms": "wrist",
    "quadriceps": "squat",
    "hamstrings": "leg-curl",
    "glutes": "hip-thrust",
    "calves": "calf-raise",
    "abdominals": "crunch",
    "abductors": "hip-thrust",
    "adductors": "hip-thrust",
    "neck": "neck",
    "cardio": "cardio",
    "full_body": "dumbbell",
    "other": "dumbbell",
}

# Last resort. Every icon named anywhere in this module must exist in the
# frontend's icon set; a test asserts that the catalogue produces nothing else.
DEFAULT_ICON = "dumbbell"

ALL_ICONS: frozenset[str] = frozenset(
    [icon for icon, _ in TITLE_RULES] + list(MUSCLE_ICONS.values()) + [DEFAULT_ICON]
)


@dataclass(frozen=True)
class IconChoice:
    icon: str
    source: str  # "title" | "muscle" | "default"


def choose(title: str, primary_muscle_group: str | None = None) -> IconChoice:
    """Pick a movement figure for one exercise."""
    haystack = f" {(title or '').lower().strip()} "

    for icon, needles in TITLE_RULES:
        if any(needle in haystack for needle in needles):
            return IconChoice(icon, "title")

    muscle = (primary_muscle_group or "").lower().strip()
    if muscle in MUSCLE_ICONS:
        return IconChoice(MUSCLE_ICONS[muscle], "muscle")

    return IconChoice(DEFAULT_ICON, "default")


def icon_for(title: str, primary_muscle_group: str | None = None) -> str:
    return choose(title, primary_muscle_group).icon
