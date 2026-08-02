"""TCX parsing.

Google Health exports an exercise as Training Center XML: roughly one trackpoint
per second, each carrying time, position, altitude, cumulative distance and heart
rate. A 33-minute run is ~1,950 points and ~1.2 MB, so this is the expensive part
of the AEI pipeline and runs once per run.

Pure parsing: bytes in, dataclasses out. No network, no database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

# The export uses the Garmin TCX namespace, and namespace handling in
# ElementTree makes the queries noisy for a document this regular. A scoped
# regex over each <Trackpoint> block is simpler and measurably faster here.
_TRACKPOINT = re.compile(r"<Trackpoint>(.*?)</Trackpoint>", re.S)
_FIELD_CACHE: dict[str, re.Pattern[str]] = {}


def _field(block: str, tag: str) -> str | None:
    pattern = _FIELD_CACHE.get(tag)
    if pattern is None:
        pattern = re.compile(rf"<{tag}>([^<]+)</{tag}>")
        _FIELD_CACHE[tag] = pattern
    match = pattern.search(block)
    return match.group(1).strip() if match else None


@dataclass(frozen=True)
class Trackpoint:
    """One sample from a recorded activity."""

    time: datetime
    distance_m: float  # cumulative from the start of the activity
    altitude_m: float | None = None
    heart_rate: float | None = None
    latitude: float | None = None
    longitude: float | None = None


def parse_tcx(text: str) -> list[Trackpoint]:
    """Parse TCX into trackpoints, oldest first.

    Points without a time or cumulative distance are skipped -- they cannot
    contribute to either distance or beats. Points missing altitude or heart
    rate are kept and handled downstream, so one dropout does not discard a run.
    """
    # The MCP layer can hand back a JSON-escaped payload; unescape once so the
    # regexes see real markup either way.
    if "\\n" in text and "\n" not in text:
        text = text.replace("\\n", "\n")

    points: list[Trackpoint] = []
    for match in _TRACKPOINT.finditer(text):
        block = match.group(1)

        raw_time = _field(block, "Time")
        raw_distance = _field(block, "DistanceMeters")
        if not raw_time or raw_distance is None:
            continue
        try:
            moment = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
            distance = float(raw_distance)
        except ValueError:
            continue

        points.append(
            Trackpoint(
                time=moment,
                distance_m=distance,
                altitude_m=_as_float(_field(block, "AltitudeMeters")),
                # <HeartRateBpm><Value>142</Value></HeartRateBpm>
                heart_rate=_as_float(_field(block, "Value")),
                latitude=_as_float(_field(block, "LatitudeDegrees")),
                longitude=_as_float(_field(block, "LongitudeDegrees")),
            )
        )

    points.sort(key=lambda point: point.time)
    return points


def _as_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def has_altitude(points: list[Trackpoint], minimum_fraction: float = 0.5) -> bool:
    """Whether enough points carry altitude for grade adjustment to mean anything."""
    if not points:
        return False
    with_altitude = sum(1 for point in points if point.altitude_m is not None)
    return with_altitude / len(points) >= minimum_fraction
