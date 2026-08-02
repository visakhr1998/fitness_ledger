"""TCX parsing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fitness_ledger.tcx import Trackpoint, has_altitude, parse_tcx

FIXTURE = Path(__file__).parent / "fixtures" / "run_2026-07-29_slice.tcx"


def wrap(*trackpoints: str) -> str:
    return (
        "<TrainingCenterDatabase><Activities><Activity><Lap><Track>"
        + "".join(trackpoints)
        + "</Track></Lap></Activity></Activities></TrainingCenterDatabase>"
    )


def point(time: str, distance: str, altitude: str | None = None, hr: str | None = None) -> str:
    parts = [f"<Time>{time}</Time>"]
    if altitude is not None:
        parts.append(f"<AltitudeMeters>{altitude}</AltitudeMeters>")
    parts.append(f"<DistanceMeters>{distance}</DistanceMeters>")
    if hr is not None:
        parts.append(f"<HeartRateBpm><Value>{hr}</Value></HeartRateBpm>")
    return "<Trackpoint>" + "".join(parts) + "</Trackpoint>"


def test_parses_fields_from_a_trackpoint():
    parsed = parse_tcx(wrap(point("2026-07-29T05:15:23.000+02:00", "12.5", "85.8", "134")))

    assert len(parsed) == 1
    entry = parsed[0]
    assert entry.distance_m == 12.5
    assert entry.altitude_m == 85.8
    assert entry.heart_rate == 134
    assert entry.time.utcoffset() == timedelta(hours=2)


def test_accepts_z_suffix_and_offsets():
    parsed = parse_tcx(
        wrap(
            point("2026-07-29T05:15:23Z", "0"),
            point("2026-07-29T07:15:24.000+02:00", "3"),
        )
    )
    # Same instant expressed two ways; both must land in UTC comparably.
    assert parsed[0].time.astimezone(timezone.utc) < parsed[1].time.astimezone(timezone.utc)


def test_points_are_sorted_oldest_first():
    parsed = parse_tcx(
        wrap(
            point("2026-07-29T05:15:30Z", "30"),
            point("2026-07-29T05:15:10Z", "10"),
            point("2026-07-29T05:15:20Z", "20"),
        )
    )
    assert [p.distance_m for p in parsed] == [10, 20, 30]


def test_points_without_time_or_distance_are_skipped():
    parsed = parse_tcx(
        wrap(
            "<Trackpoint><DistanceMeters>5</DistanceMeters></Trackpoint>",
            "<Trackpoint><Time>2026-07-29T05:15:23Z</Time></Trackpoint>",
            point("2026-07-29T05:15:24Z", "6"),
        )
    )
    assert len(parsed) == 1


def test_missing_altitude_or_heart_rate_keeps_the_point():
    # One dropped sensor reading must not discard the sample -- and with it the
    # distance and beats it carries.
    parsed = parse_tcx(wrap(point("2026-07-29T05:15:23Z", "10")))

    assert len(parsed) == 1
    assert parsed[0].altitude_m is None
    assert parsed[0].heart_rate is None


def test_unparseable_numbers_do_not_kill_the_run():
    parsed = parse_tcx(wrap(point("2026-07-29T05:15:23Z", "not-a-number")))
    assert parsed == []


def test_json_escaped_payload_is_handled():
    # The MCP layer can hand back a payload with literal backslash-n.
    escaped = wrap(point("2026-07-29T05:15:23Z", "10", "80", "120")).replace("><", ">\\n<")
    assert len(parse_tcx(escaped)) == 1


def test_has_altitude_thresholds_on_coverage():
    with_alt = [Trackpoint(datetime.now(timezone.utc), 0, altitude_m=10.0)]
    without = [Trackpoint(datetime.now(timezone.utc), 0)]

    assert has_altitude(with_alt) is True
    assert has_altitude(without) is False
    assert has_altitude([]) is False
    assert has_altitude(with_alt + without) is True  # 50% meets the default
    assert has_altitude(with_alt + without * 3) is False


# --- real data -------------------------------------------------------------


def test_real_fixture_parses_completely():
    parsed = parse_tcx(FIXTURE.read_text(encoding="utf-8"))

    assert len(parsed) == 300
    assert all(p.altitude_m is not None for p in parsed)
    assert all(p.heart_rate is not None for p in parsed)
    assert parsed[0].distance_m == 0.0
    assert parsed[-1].distance_m > parsed[0].distance_m
