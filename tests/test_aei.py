"""Aerobic Efficiency Index.

The regression at the bottom runs on a real slice of a recorded run. It exists
because the grade-smoothing choice moves AEI by ~10%: a silent change to the
binning would otherwise produce plausible-looking numbers that are not
comparable with anything already stored.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from fitness_ledger import aei
from fitness_ledger.tcx import Trackpoint, parse_tcx

FIXTURE = Path(__file__).parent / "fixtures" / "run_2026-07-29_slice.tcx"
START = datetime(2026, 7, 29, 5, 15, 0, tzinfo=timezone.utc)


def track(*, seconds: list[int], distance: list[float], altitude=None, hr=None):
    """Build trackpoints from parallel lists."""
    points = []
    for i, (sec, dist) in enumerate(zip(seconds, distance)):
        points.append(
            Trackpoint(
                time=START + timedelta(seconds=sec),
                distance_m=dist,
                altitude_m=altitude[i] if altitude else None,
                heart_rate=hr[i] if hr else None,
            )
        )
    return points


# --- the polynomial --------------------------------------------------------


def test_flat_cost_is_the_published_constant():
    assert aei.minetti_cost(0.0) == pytest.approx(3.6)


def test_gap_factor_is_exactly_one_on_the_flat():
    # Anything else silently rescales every run.
    assert aei.gap_factor(0.0) == 1.0


def test_uphill_costs_more_and_downhill_less():
    assert aei.gap_factor(0.10) > 1.0
    assert aei.gap_factor(-0.10) < 1.0


def test_climbing_costs_more_than_descending_saves():
    # The asymmetry is why raw GPS altitude noise biases AEI upward instead of
    # cancelling out, and therefore why grade is binned over distance.
    uphill_penalty = aei.gap_factor(0.10) - 1.0
    downhill_saving = 1.0 - aei.gap_factor(-0.10)
    assert uphill_penalty > downhill_saving


# --- segmentation ----------------------------------------------------------


def test_segments_close_at_the_bin_distance():
    points = track(seconds=list(range(0, 101, 10)), distance=[i * 10.0 for i in range(11)])
    segments = aei.segment_run(points, bin_distance_m=25.0)

    assert len(segments) >= 3
    assert all(s.distance_m >= 25.0 for s in segments[:-1])


def test_grade_is_measured_over_the_bin_not_the_sample():
    # 1 m rise over 50 m is a 2% grade. Sampled per-point it would read as a
    # series of violent spikes; binned it reads as the gentle slope it is.
    points = track(
        seconds=[0, 10, 20, 30, 40, 50],
        distance=[0, 10, 20, 30, 40, 50],
        altitude=[100.0, 100.0, 100.6, 100.2, 100.8, 101.0],
    )
    segments = aei.segment_run(points, bin_distance_m=50.0)

    assert len(segments) == 1
    assert segments[0].grade == pytest.approx(0.02, abs=1e-6)


def test_grade_is_clamped():
    points = track(seconds=[0, 60], distance=[0, 30], altitude=[100.0, 200.0])
    segments = aei.segment_run(points, bin_distance_m=25.0)

    assert segments[0].grade == aei.MAX_GRADE


def test_missing_altitude_gives_zero_grade_not_a_crash():
    points = track(seconds=[0, 30], distance=[0, 40], hr=[140, 140])
    segments = aei.segment_run(points, bin_distance_m=25.0)

    assert segments[0].grade == 0.0
    assert segments[0].adjusted_distance_m == pytest.approx(segments[0].distance_m)


def test_trailing_partial_bin_keeps_its_distance():
    points = track(seconds=[0, 10, 20], distance=[0, 30, 38])
    segments = aei.segment_run(points, bin_distance_m=25.0)

    assert sum(s.distance_m for s in segments) == pytest.approx(38.0)


def test_too_few_points_yields_no_segments():
    assert aei.segment_run([]) == []
    assert aei.segment_run(track(seconds=[0], distance=[0])) == []


# --- beats -----------------------------------------------------------------


def test_total_beats_is_rate_times_minutes():
    # 120 bpm held for 2 minutes = 240 beats.
    points = track(seconds=[0, 120], distance=[0, 400], hr=[120, 120])
    assert aei.total_beats(points) == pytest.approx(240.0)


def test_points_without_heart_rate_contribute_no_beats():
    points = track(seconds=[0, 60], distance=[0, 200])
    assert aei.total_beats(points) == 0.0


# --- end to end ------------------------------------------------------------


def test_flat_run_adjusts_to_its_actual_distance():
    points = track(
        seconds=list(range(0, 301, 10)),
        distance=[i * 20.0 for i in range(31)],
        altitude=[100.0] * 31,
        hr=[150] * 31,
    )
    metrics = aei.compute(points, date(2026, 7, 29))

    assert metrics.adjusted_distance_m == pytest.approx(metrics.actual_distance_m)
    assert metrics.aei == pytest.approx(metrics.actual_distance_m / metrics.total_beats)


def test_uphill_run_adjusts_upward():
    flat = track(
        seconds=list(range(0, 301, 10)),
        distance=[i * 20.0 for i in range(31)],
        altitude=[100.0] * 31,
        hr=[150] * 31,
    )
    uphill = track(
        seconds=list(range(0, 301, 10)),
        distance=[i * 20.0 for i in range(31)],
        altitude=[100.0 + i * 1.0 for i in range(31)],
        hr=[150] * 31,
    )
    assert aei.compute(uphill, date(2026, 7, 29)).aei > aei.compute(flat, date(2026, 7, 29)).aei


def test_no_heart_rate_means_no_aei_rather_than_a_divide_by_zero():
    points = track(seconds=[0, 60], distance=[0, 200], altitude=[10.0, 10.0])
    metrics = aei.compute(points, date(2026, 7, 29))

    assert metrics.aei is None
    assert metrics.as_dict()["aei"] is None


def test_record_carries_the_method_version():
    points = track(seconds=[0, 60], distance=[0, 200], hr=[140, 140])
    assert aei.compute(points, date(2026, 7, 29)).method_version == aei.METHOD_VERSION


# --- reliability -----------------------------------------------------------
# Both guards come from real recorded sessions: a 2-second mis-tap, and one that
# Google Health summarised as 936 m whose GPS track held only 66 m. Either would
# move the AEI series further than a year of training.


def test_a_full_track_is_reliable():
    ok, reason, coverage = aei.reliability(4128.0, 4123.5)
    assert ok is True
    assert reason is None
    assert coverage == pytest.approx(1.001, abs=0.01)


def test_a_very_short_effort_is_rejected():
    ok, reason, _ = aei.reliability(66.0, 936.6)
    assert ok is False
    assert "66 m" in reason


def test_a_truncated_gps_track_is_rejected():
    # Long enough to pass the distance floor, but only 60% of what the watch
    # recorded -- the pace and therefore the AEI would be nonsense.
    ok, reason, coverage = aei.reliability(600.0, 1000.0)
    assert ok is False
    assert coverage == pytest.approx(0.6)
    assert "60%" in reason


def test_coverage_slightly_over_one_is_still_reliable():
    # TCX distance routinely runs a few percent above the device summary.
    ok, _, _ = aei.reliability(5100.0, 5000.0)
    assert ok is True


def test_missing_reported_distance_falls_back_to_the_length_check():
    ok, _, coverage = aei.reliability(4000.0, None)
    assert ok is True
    assert coverage is None


# --- regression on real data ----------------------------------------------


def test_real_run_slice_pins_the_metric():
    """First 300 trackpoints of the 29 Jul 2026 run.

    Values come from the 25 m binned method with grade clamped to +/-30%. The
    full 1,947-point run scores 1.0248 m/beat by the same method; the raw 1 Hz
    method scored 1.1462 and was rejected for GPS-noise bias.
    """
    points = parse_tcx(FIXTURE.read_text(encoding="utf-8"))
    metrics = aei.compute(points, date(2026, 7, 29))

    assert metrics.actual_distance_m == pytest.approx(674.0, abs=0.1)
    assert metrics.adjusted_distance_m == pytest.approx(869.3, abs=0.5)
    assert metrics.total_beats == pytest.approx(656.0, abs=0.5)
    assert metrics.aei == pytest.approx(1.3251, abs=0.001)


def test_recompute_from_stored_segments_matches_the_direct_path():
    # This is what lets a method change re-run without re-downloading 1.2 MB.
    #
    # It used to pass direct.total_beats into the replay, so the one number that
    # actually diverged between the two paths was handed over rather than
    # checked -- which is how #11 shipped. from_segments now derives beats
    # itself and there is nothing left to hand it.
    points = parse_tcx(FIXTURE.read_text(encoding="utf-8"))
    direct = aei.compute(points, date(2026, 7, 29))
    segments = aei.segment_run(points)

    replayed = aei.from_segments(segments, date(2026, 7, 29), direct.actual_distance_m)
    assert replayed.total_beats == pytest.approx(direct.total_beats, abs=1e-9)
    assert replayed.aei == pytest.approx(direct.aei, abs=1e-9)


# --- sparse heart rate (issue #11) -----------------------------------------


def sparse_hr_track(*, minutes: int, bpm: int, hr_every: int):
    """A run at a steady pace where only every Nth sample carries a heart rate.

    Mirrors a real export: position at 1 Hz, heart rate at ~2.5 s.
    """
    samples = minutes * 60
    return track(
        seconds=list(range(samples + 1)),
        distance=[i * 3.0 for i in range(samples + 1)],  # 3 m/s
        hr=[bpm if i % hr_every == 0 else None for i in range(samples + 1)],
    )


def test_beats_span_the_whole_run_when_heart_rate_is_sparser_than_position():
    """The #11 regression: 150 bpm for 10 minutes is 1500 beats, however
    often the watch reported it."""
    dense = aei.total_beats(sparse_hr_track(minutes=10, bpm=150, hr_every=1))
    sparse = aei.total_beats(sparse_hr_track(minutes=10, bpm=150, hr_every=3))

    assert dense == pytest.approx(1500.0, abs=1.0)
    # Method 1 returned a third of this, because two of every three seconds
    # elapsed between readings and were credited to nobody.
    assert sparse == pytest.approx(1500.0, abs=5.0)


def test_sparse_heart_rate_does_not_move_aei():
    """AEI is the thing that broke: same run, same effort, same number."""
    dense = aei.compute(sparse_hr_track(minutes=10, bpm=150, hr_every=1), date(2026, 8, 7))
    sparse = aei.compute(sparse_hr_track(minutes=10, bpm=150, hr_every=3), date(2026, 8, 7))

    assert sparse.aei == pytest.approx(dense.aei, rel=0.01)
    assert sparse.avg_heart_rate == pytest.approx(150.0, abs=0.5)


def test_the_two_beat_definitions_agree():
    """beats_from_segments is what production stores; total_beats is the
    independent measure it must track. Method 1 had them 2.4x apart."""
    points = parse_tcx(FIXTURE.read_text(encoding="utf-8"))
    segments = aei.segment_run(points)

    assert aei.beats_from_segments(segments) == pytest.approx(
        aei.total_beats(points), rel=0.02
    )
