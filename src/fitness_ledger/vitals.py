"""Derived physiological figures.

Everything here is an estimate from a published formula, not a measurement. The
UI labels them as such, and any value the user supplies directly overrides the
estimate -- a measured max heart rate beats an age formula every time.

Pure functions: numbers in, numbers out.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Karvonen zone boundaries as fractions of heart rate reserve.
ZONE_BOUNDS: list[tuple[str, float, float]] = [
    ("Zone 1 — recovery", 0.50, 0.60),
    ("Zone 2 — aerobic", 0.60, 0.70),
    ("Zone 3 — tempo", 0.70, 0.80),
    ("Zone 4 — threshold", 0.80, 0.90),
    ("Zone 5 — max", 0.90, 1.00),
]


def tanaka_max_heart_rate(age: int) -> float:
    """208 - 0.7 x age. Fits observed data better than the old 220 - age."""
    return 208 - 0.7 * age


def heart_rate_reserve(max_hr: float, resting_hr: float) -> float:
    return max(max_hr - resting_hr, 0.0)


def karvonen_zones(max_hr: float, resting_hr: float) -> list[dict[str, object]]:
    """Heart rate zones from reserve, which is why resting HR is needed.

    Percent-of-max zones ignore resting heart rate and so drift as fitness
    changes; Karvonen anchors to the reserve between rest and max.
    """
    reserve = heart_rate_reserve(max_hr, resting_hr)
    zones: list[dict[str, object]] = []
    for name, low, high in ZONE_BOUNDS:
        zones.append(
            {
                "name": name,
                "low_pct": round(low * 100),
                "high_pct": round(high * 100),
                "low_bpm": round(resting_hr + reserve * low),
                "high_bpm": round(resting_hr + reserve * high),
            }
        )
    return zones


def mifflin_st_jeor_bmr(
    weight_kg: float, height_cm: float, age: int, sex: str
) -> float | None:
    """Resting energy expenditure, kcal/day.

    The sex term is a flat +5 / -161, so the figure cannot be produced without
    it. Returns None rather than silently assuming one.
    """
    base = 10 * weight_kg + 6.25 * height_cm - 5 * age
    normalised = (sex or "").strip().lower()
    if normalised in {"male", "m"}:
        return base + 5
    if normalised in {"female", "f"}:
        return base - 161
    return None


def bmi(weight_kg: float, height_cm: float) -> float | None:
    if not height_cm:
        return None
    metres = height_cm / 100.0
    return weight_kg / (metres * metres)


@dataclass
class Vitals:
    """Everything the vitals card shows, with its provenance."""

    age: int | None = None
    height_cm: float | None = None
    weight_kg: float | None = None
    resting_heart_rate: float | None = None
    vo2_max: float | None = None
    cardio_fitness_level: str | None = None
    max_heart_rate: float | None = None
    max_hr_source: str = "estimated"  # "measured" | "user" | "estimated"
    sex: str | None = None
    zones: list[dict[str, object]] = field(default_factory=list)
    bmr_kcal: float | None = None
    bmi: float | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "age": self.age,
            "height_cm": self.height_cm,
            "weight_kg": round(self.weight_kg, 1) if self.weight_kg else None,
            "resting_heart_rate": round(self.resting_heart_rate) if self.resting_heart_rate else None,
            "vo2_max": round(self.vo2_max, 1) if self.vo2_max else None,
            "cardio_fitness_level": self.cardio_fitness_level,
            "max_heart_rate": round(self.max_heart_rate) if self.max_heart_rate else None,
            "max_hr_source": self.max_hr_source,
            "sex": self.sex,
            "zones": self.zones,
            "bmr_kcal": round(self.bmr_kcal) if self.bmr_kcal else None,
            "bmi": round(self.bmi, 1) if self.bmi else None,
        }


def build(
    *,
    age: int | None,
    height_cm: float | None,
    weight_kg: float | None,
    resting_hr: float | None,
    vo2_max: float | None = None,
    cardio_fitness_level: str | None = None,
    sex: str | None = None,
    max_hr_override: float | None = None,
    observed_max_hr: float | None = None,
) -> Vitals:
    """Assemble the vitals card, preferring real data over formulas.

    Max heart rate order of preference: what the user set, then the highest
    value actually recorded during a run, then the Tanaka estimate.
    """
    vitals = Vitals(
        age=age,
        height_cm=height_cm,
        weight_kg=weight_kg,
        resting_heart_rate=resting_hr,
        vo2_max=vo2_max,
        cardio_fitness_level=cardio_fitness_level,
        sex=sex,
    )

    if max_hr_override:
        vitals.max_heart_rate, vitals.max_hr_source = max_hr_override, "user"
    elif observed_max_hr and age and observed_max_hr > tanaka_max_heart_rate(age):
        # Only trust an observed peak that exceeds the estimate; a lower one just
        # means you have not gone that hard recently.
        vitals.max_heart_rate, vitals.max_hr_source = observed_max_hr, "measured"
    elif age:
        vitals.max_heart_rate, vitals.max_hr_source = tanaka_max_heart_rate(age), "estimated"

    if vitals.max_heart_rate and resting_hr:
        vitals.zones = karvonen_zones(vitals.max_heart_rate, resting_hr)

    if weight_kg and height_cm and age:
        vitals.bmr_kcal = mifflin_st_jeor_bmr(weight_kg, height_cm, age, sex or "")
        vitals.bmi = bmi(weight_kg, height_cm)

    return vitals
