"""Aerobic Efficiency Index.

AEI is grade-adjusted distance per heart beat: how far you travel for each beat
your heart spends, corrected for hills. Rising AEI at the same effort means
aerobic fitness is improving.

    cost(g)   = 155.4g^5 - 30.4g^4 - 43.3g^3 + 46.3g^2 + 19.5g + 3.6   (Minetti)
    gap       = cost(g) / cost(0)
    adjusted  = SUM(segment_distance * gap)
    beats     = SUM(heart_rate_i * minutes_i)
    AEI       = adjusted_metres / beats

**Grade is computed over 25 m distance bins, not per sample.** Applying the
polynomial to raw 1 Hz GPS altitude is not a smaller version of the same answer,
it is a different and wrong one: on a flat run the 95th-percentile sample grade
came out at 41%, and because Minetti's curve is asymmetric -- climbing costs more
than descending saves -- symmetric altitude noise biases the result *upward*
rather than cancelling. Binning cut the inflation from 23% to 10%.

Because the binning choice moves AEI by roughly 10%, a stored value is only
comparable to another computed the same way. Every record carries METHOD_VERSION,
and changing any constant below requires bumping it so stored runs recompute.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .tcx import Trackpoint

# Bump on any change to the constants or the algorithm below.
#   1 -> 2: beats are totalled from the stored bins in every path. Method 1 had
#   two definitions -- a fresh fetch totalled the raw samples and undercounted
#   whenever heart rate was sparser than position, a recompute totalled the bins
#   and did not -- so a run's AEI depended on which path last wrote it (#11).
METHOD_VERSION = 2

BIN_DISTANCE_M = 25.0
MAX_GRADE = 0.30  # clamp; steeper readings are GPS noise, not terrain
FLAT_COST = 3.6  # cost(0), the denominator that makes flat ground factor 1.0

# Reliability guards. A real recorded run passed both; the ones that failed were
# a 2-second mis-tap, and a session Google Health summarised as 936 m whose GPS
# track held only 66 m. Averaging either into the series would move it more than
# a year of training does.
MIN_DISTANCE_M = 500.0
MIN_TRACK_COVERAGE = 0.80  # tracked distance vs the device's own summary


def reliability(
    tracked_distance_m: float, reported_distance_m: float | None
) -> tuple[bool, str | None, float | None]:
    """Whether an AEI from this track is worth plotting.

    Returns (reliable, reason, coverage). The reason is kept so the UI can say
    why a run is missing from the trend instead of silently dropping it.
    """
    coverage = None
    if reported_distance_m and reported_distance_m > 0:
        coverage = tracked_distance_m / reported_distance_m

    if tracked_distance_m < MIN_DISTANCE_M:
        return False, f"tracked only {tracked_distance_m:.0f} m", coverage
    if coverage is not None and coverage < MIN_TRACK_COVERAGE:
        return (
            False,
            f"GPS track covers {coverage:.0%} of the {reported_distance_m:.0f} m recorded",
            coverage,
        )
    return True, None, coverage


def minetti_cost(grade: float) -> float:
    """Energy cost of running at a given gradient, J/kg/m."""
    g = grade
    return 155.4 * g**5 - 30.4 * g**4 - 43.3 * g**3 + 46.3 * g**2 + 19.5 * g + 3.6


def gap_factor(grade: float) -> float:
    """Grade-adjustment multiplier. 1.0 on the flat, >1 uphill."""
    return minetti_cost(grade) / FLAT_COST


@dataclass(frozen=True)
class Segment:
    """One distance bin: what gets stored so a recompute needs no re-download."""

    index: int
    distance_m: float
    grade: float
    heart_rate: float | None
    seconds: float

    @property
    def adjusted_distance_m(self) -> float:
        return self.distance_m * gap_factor(self.grade)


@dataclass(frozen=True)
class RunMetrics:
    """The historical record: [date, actual, adjusted, avg HR, AEI]."""

    local_date: date
    actual_distance_m: float
    adjusted_distance_m: float
    avg_heart_rate: float | None
    total_beats: float
    aei: float | None
    method_version: int = METHOD_VERSION

    def as_dict(self) -> dict[str, object]:
        return {
            "date": self.local_date.isoformat(),
            "actual_distance_m": round(self.actual_distance_m, 1),
            "actual_distance_km": round(self.actual_distance_m / 1000.0, 3),
            "adjusted_distance_m": round(self.adjusted_distance_m, 1),
            "grade_adjustment_ratio": (
                round(self.adjusted_distance_m / self.actual_distance_m, 4)
                if self.actual_distance_m
                else None
            ),
            "avg_heart_rate": round(self.avg_heart_rate, 1) if self.avg_heart_rate else None,
            "total_beats": round(self.total_beats, 1),
            "aei": round(self.aei, 4) if self.aei is not None else None,
            "method_version": self.method_version,
        }


def segment_run(
    points: list[Trackpoint], bin_distance_m: float = BIN_DISTANCE_M
) -> list[Segment]:
    """Resample trackpoints into fixed-distance bins with a mean heart rate.

    Distance bins rather than time bins: grade is rise over run, so a fixed
    denominator keeps the noise in the numerator bounded. Time bins would make
    grade explode whenever you slow down.
    """
    segments: list[Segment] = []
    if len(points) < 2:
        return segments

    anchor = points[0]
    previous = points[0]
    hr_sum = 0.0
    hr_weight = 0.0

    for current in points[1:]:
        # Time-weight the heart rate across the bin, using the gap to the
        # immediately preceding sample rather than searching for it.
        step = (current.time - previous.time).total_seconds()
        if current.heart_rate is not None and step > 0:
            hr_sum += current.heart_rate * step
            hr_weight += step
        previous = current

        distance = current.distance_m - anchor.distance_m
        if distance < bin_distance_m:
            continue
        delta_seconds = (current.time - anchor.time).total_seconds()

        grade = 0.0
        if anchor.altitude_m is not None and current.altitude_m is not None and distance > 0:
            grade = _clamp((current.altitude_m - anchor.altitude_m) / distance)

        segments.append(
            Segment(
                index=len(segments),
                distance_m=distance,
                grade=grade,
                heart_rate=(hr_sum / hr_weight) if hr_weight else current.heart_rate,
                seconds=delta_seconds,
            )
        )
        anchor = current
        hr_sum = hr_weight = 0.0

    # Trailing partial bin: keep the distance, reuse the last known grade rather
    # than inventing one from a short, noisy denominator.
    tail_distance = points[-1].distance_m - anchor.distance_m
    if tail_distance > 0:
        segments.append(
            Segment(
                index=len(segments),
                distance_m=tail_distance,
                grade=segments[-1].grade if segments else 0.0,
                heart_rate=(hr_sum / hr_weight) if hr_weight else points[-1].heart_rate,
                seconds=(points[-1].time - anchor.time).total_seconds(),
            )
        )
    return segments


def beats_from_segments(segments: list[Segment]) -> float:
    """Heart beats spent, totalled from the stored bins.

    **This is the canonical definition.** The 25 m bins are what persist, so a
    recompute under a new METHOD_VERSION has only these to work from; if a fresh
    computation totalled the raw samples instead, the same run would carry two
    different beat counts depending on which path last wrote it. That is exactly
    what happened before method 2 -- see total_beats below.
    """
    return sum(
        (segment.heart_rate or 0.0) * (segment.seconds / 60.0) for segment in segments
    )


def total_beats(points: list[Trackpoint]) -> float:
    """Heart beats spent, straight from the samples: SUM(bpm * minutes).

    The interval is the time **since the last sample carrying a heart rate**,
    not since the previous sample. Only ~40% of the trackpoints in a Google
    Health export have one -- the watch samples heart rate at ~2.5 s against
    1 Hz GPS -- so measuring back to the previous sample credits ~1 s where
    ~2.5 s elapsed and silently drops the rest. That undercounted beats by ~59%
    and inflated AEI ~2.4x on any run computed by this path (issue #11).

    Kept as the independent cross-check on beats_from_segments, which is what
    production stores; a test pins the two together.
    """
    if not points:
        return 0.0

    beats = 0.0
    since = points[0].time
    for point in points:
        if point.heart_rate is None:
            continue
        minutes = (point.time - since).total_seconds() / 60.0
        if minutes > 0:
            beats += point.heart_rate * minutes
        since = point.time
    return beats


def compute(points: list[Trackpoint], local_date: date) -> RunMetrics:
    """Full AEI record for one run."""
    segments = segment_run(points)
    # Via the bins, not the raw samples, so a later recompute reproduces this
    # number exactly rather than approximately.
    beats = beats_from_segments(segments)

    actual = points[-1].distance_m - points[0].distance_m if len(points) >= 2 else 0.0
    adjusted = sum(segment.adjusted_distance_m for segment in segments)

    heart_rates = [point.heart_rate for point in points if point.heart_rate is not None]
    avg_hr = sum(heart_rates) / len(heart_rates) if heart_rates else None

    return RunMetrics(
        local_date=local_date,
        actual_distance_m=actual,
        adjusted_distance_m=adjusted,
        avg_heart_rate=avg_hr,
        total_beats=beats,
        aei=(adjusted / beats) if beats > 0 else None,
    )


def from_segments(
    segments: list[Segment], local_date: date, actual_distance_m: float
) -> RunMetrics:
    """Recompute from stored bins, so changing the method needs no re-download.

    Beats are derived here rather than passed in. The caller used to supply
    them, which is how a wrong total reached storage unchallenged and how the
    test meant to guard this path came to hand it the answer it was checking.
    """
    adjusted = sum(segment.adjusted_distance_m for segment in segments)
    beats = beats_from_segments(segments)
    weighted = [(s.heart_rate, s.seconds) for s in segments if s.heart_rate is not None]
    total_seconds = sum(seconds for _, seconds in weighted)
    avg_hr = (
        sum(hr * seconds for hr, seconds in weighted) / total_seconds
        if total_seconds
        else None
    )
    return RunMetrics(
        local_date=local_date,
        actual_distance_m=actual_distance_m,
        adjusted_distance_m=adjusted,
        avg_heart_rate=avg_hr,
        total_beats=beats,
        aei=(adjusted / beats) if beats > 0 else None,
    )


def _clamp(grade: float) -> float:
    return max(-MAX_GRADE, min(MAX_GRADE, grade))
