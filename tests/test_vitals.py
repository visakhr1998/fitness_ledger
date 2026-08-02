"""Derived physiological figures."""

from __future__ import annotations

import pytest

from fitness_ledger import vitals


def test_tanaka_max_heart_rate():
    assert vitals.tanaka_max_heart_rate(28) == pytest.approx(188.4)


def test_karvonen_zones_span_rest_to_max():
    zones = vitals.karvonen_zones(max_hr=188, resting_hr=62)

    assert len(zones) == 5
    # Zone 1 starts at 50% of the 126 bpm reserve above rest.
    assert zones[0]["low_bpm"] == round(62 + 126 * 0.50)
    assert zones[-1]["high_bpm"] == 188


def test_zones_are_contiguous():
    zones = vitals.karvonen_zones(max_hr=188, resting_hr=62)
    for lower, upper in zip(zones, zones[1:]):
        assert lower["high_bpm"] == upper["low_bpm"]


def test_zones_use_reserve_not_percent_of_max():
    # A higher resting heart rate lifts every zone boundary; percent-of-max
    # would leave them unchanged.
    low_rhr = vitals.karvonen_zones(max_hr=188, resting_hr=50)
    high_rhr = vitals.karvonen_zones(max_hr=188, resting_hr=70)

    assert high_rhr[1]["low_bpm"] > low_rhr[1]["low_bpm"]


def test_bmr_differs_by_sex():
    male = vitals.mifflin_st_jeor_bmr(69, 179, 28, "male")
    female = vitals.mifflin_st_jeor_bmr(69, 179, 28, "female")

    assert male - female == pytest.approx(166)
    assert male == pytest.approx(10 * 69 + 6.25 * 179 - 5 * 28 + 5)


def test_bmr_returns_none_without_sex_rather_than_guessing():
    assert vitals.mifflin_st_jeor_bmr(69, 179, 28, "") is None
    assert vitals.mifflin_st_jeor_bmr(69, 179, 28, "unspecified") is None


def test_bmi():
    assert vitals.bmi(69, 179) == pytest.approx(21.5, abs=0.05)
    assert vitals.bmi(69, 0) is None


# --- assembly --------------------------------------------------------------


def build(**overrides):
    base = dict(age=28, height_cm=179.0, weight_kg=69.0, resting_hr=62.0)
    return vitals.build(**{**base, **overrides})


def test_max_hr_falls_back_to_the_estimate():
    result = build()
    assert result.max_hr_source == "estimated"
    assert result.max_heart_rate == pytest.approx(188.4)


def test_user_override_beats_everything():
    result = build(max_hr_override=195, observed_max_hr=192)
    assert result.max_heart_rate == 195
    assert result.max_hr_source == "user"


def test_an_observed_peak_above_the_estimate_wins():
    result = build(observed_max_hr=194)
    assert result.max_heart_rate == 194
    assert result.max_hr_source == "measured"


def test_an_observed_peak_below_the_estimate_is_ignored():
    # Never having gone that hard is not evidence of a lower ceiling.
    result = build(observed_max_hr=170)
    assert result.max_hr_source == "estimated"


def test_bmr_absent_until_sex_is_known():
    assert build().bmr_kcal is None
    # 10(69) + 6.25(179) - 5(28) + 5 = 690 + 1118.75 - 140 + 5
    assert build(sex="male").bmr_kcal == pytest.approx(1673.75)


def test_vitals_survive_missing_inputs():
    result = vitals.build(
        age=None, height_cm=None, weight_kg=None, resting_hr=None
    )
    payload = result.as_dict()

    assert payload["max_heart_rate"] is None
    assert payload["bmr_kcal"] is None
    assert payload["zones"] == []


def test_as_dict_rounds_for_display():
    payload = build(sex="male", vo2_max=51.149976, cardio_fitness_level="GOOD").as_dict()

    assert payload["vo2_max"] == 51.1
    assert payload["cardio_fitness_level"] == "GOOD"
    assert payload["max_heart_rate"] == 188
    assert isinstance(payload["bmr_kcal"], int)
