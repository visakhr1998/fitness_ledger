"""The running rules, and which screen each rule belongs to.

Before these, every rule was strength or sleep, so a Run-section coach strip had
nothing to say (#14). Both rules here are pure: dataclasses in, Insight records
out, no repo and no clock beyond the `today` they are handed.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from fitness_ledger.insights import (
    AEI_TREND_RUNS,
    RULE_SECTIONS,
    aei_trend,
    detect,
    for_section,
    running_shortfall,
)
from fitness_ledger.models import Insight, Run, RunningTarget
from guardrails import assert_no_directive_health_language

# A Wednesday, so "last complete week" is unambiguous.
TODAY = date(2026, 7, 29)
LAST_WEEK_MONDAY = date(2026, 7, 20)
TARGET = RunningTarget(distance_km_per_week=25.0, sessions_per_week=3)


def run(day: date, km: float, kind: str = "RUNNING") -> Run:
    return Run(
        id=f"r-{day}-{km}",
        start_time=None,
        local_date=day,
        exercise_type=kind,
        distance_m=km * 1000.0,
        active_duration_s=km * 300.0,
    )


def week(*distances: float, kind: str = "RUNNING") -> list[Run]:
    return [run(LAST_WEEK_MONDAY + timedelta(days=i), km, kind) for i, km in enumerate(distances)]


# --- running_shortfall -----------------------------------------------------


def test_quiet_when_the_week_met_its_target():
    assert running_shortfall(week(9.0, 8.0, 8.0), TARGET, TODAY) == []


def test_quiet_within_the_tolerance():
    """23 km against 25 is on track; a rule that fires at 99% gets ignored."""
    assert running_shortfall(week(8.0, 8.0, 7.0), TARGET, TODAY) == []


def test_flags_a_distance_shortfall():
    [found] = running_shortfall(week(6.0, 6.0, 6.0), TARGET, TODAY)

    assert found.rule == "running_shortfall"
    assert found.severity == "warn"
    assert found.data["distance_km"] == 18.0
    assert found.data["sessions"] == 3
    assert "18.0 km" in found.message and "25 km" in found.message


def test_too_few_sessions_is_softer_than_too_few_kilometres():
    """One long run can satisfy the distance and still miss the intent, but it
    is a smaller miss than not covering the distance at all."""
    [found] = running_shortfall(week(25.0), TARGET, TODAY)

    assert found.severity == "info"
    assert found.data["sessions"] == 1


def test_quiet_without_a_target():
    """An unset target is not a shortfall."""
    assert running_shortfall(week(1.0), None, TODAY) == []


def test_only_the_last_complete_week_counts():
    """This week is short by definition; counting it would fire every Monday."""
    this_week = [run(TODAY, 3.0)]
    assert running_shortfall(week(9.0, 8.0, 8.0) + this_week, TARGET, TODAY) == []


def test_walks_are_not_runs():
    assert running_shortfall(week(30.0, kind="WALKING"), TARGET, TODAY)[0].data["sessions"] == 0


# --- aei_trend -------------------------------------------------------------


def series(*values: float) -> list[tuple[date, float]]:
    return [(TODAY - timedelta(days=3 * (len(values) - i)), v) for i, v in enumerate(values)]


def test_quiet_until_there_are_enough_runs():
    assert aei_trend(series(*[1.0] * (AEI_TREND_RUNS * 2 - 1)), TODAY) == []
    assert aei_trend([], TODAY) == []


def test_quiet_when_the_change_is_noise():
    assert aei_trend(series(1.00, 1.01, 0.99, 1.00, 1.01, 1.00), TODAY) == []


def test_reports_an_improvement():
    [found] = aei_trend(series(1.00, 1.00, 1.00, 1.10, 1.10, 1.10), TODAY)

    assert found.rule == "aei_trend"
    assert found.severity == "info"
    assert found.data["change_pct"] == pytest.approx(10.0, abs=0.1)
    assert "up 10%" in found.message


def test_reports_a_decline_without_raising_an_alarm():
    """AEI moves ~10% on a method change and a hot day moves it too, so a
    decline is reported, not warned about."""
    [found] = aei_trend(series(1.10, 1.10, 1.10, 1.00, 1.00, 1.00), TODAY)

    assert found.severity == "info"
    assert found.data["change_pct"] < 0
    assert "down" in found.message


def test_order_comes_from_the_dates_not_the_list():
    shuffled = list(reversed(series(1.00, 1.00, 1.00, 1.10, 1.10, 1.10)))
    [found] = aei_trend(shuffled, TODAY)
    assert found.data["change_pct"] == pytest.approx(10.0, abs=0.1)


# --- sections --------------------------------------------------------------


def test_every_rule_declares_a_section():
    """detect() asserts this too, so a new rule cannot quietly land on neither
    screen -- but this says so directly."""
    assert set(RULE_SECTIONS) == {
        "volume_drop", "coverage_gap", "stall", "progression_ready",
        "running_shortfall", "aei_trend", "recovery_flag",
    }


def test_recovery_shows_on_both_screens():
    """Sleep is neither lifting nor running; hiding it from one screen would
    mean seeing it depended on which tab was open."""
    assert RULE_SECTIONS["recovery_flag"] == frozenset({"gym", "run"})


def test_for_section_splits_without_losing_anything():
    findings = [
        Insight("volume_drop", "warn", "chest", "m", TODAY, {}),
        Insight("aei_trend", "info", "aerobic_efficiency", "m", TODAY, {}),
        Insight("recovery_flag", "info", "sleep", "m", TODAY, {}),
    ]

    assert [i.rule for i in for_section(findings, "gym")] == ["volume_drop", "recovery_flag"]
    assert [i.rule for i in for_section(findings, "run")] == ["aei_trend", "recovery_flag"]
    assert for_section(findings, None) == findings


def test_detect_returns_running_findings_for_the_run_section():
    found = detect(
        [], {}, {}, {}, TODAY,
        runs=week(6.0, 6.0, 6.0),
        running_target=TARGET,
        aei_series=series(1.00, 1.00, 1.00, 1.10, 1.10, 1.10),
        section="run",
    )

    assert {i.rule for i in found} == {"running_shortfall", "aei_trend"}
    # Warnings first, as everywhere else.
    assert found[0].severity == "warn"


def test_detect_without_running_data_still_runs_the_strength_rules():
    """A caller with no runs gets silence from these, not an error."""
    assert detect([], {}, {}, {}, TODAY, section="run") == []


# --- the guardrail ---------------------------------------------------------


def test_running_findings_do_not_instruct():
    """Same line the recovery rule holds: observe, never prescribe."""
    found = detect(
        [], {}, {}, {}, TODAY,
        runs=week(6.0),
        running_target=TARGET,
        aei_series=series(1.10, 1.10, 1.10, 1.00, 1.00, 1.00),
    )
    assert found

    for insight in found:
        assert_no_directive_health_language(insight.message, insight.rule)
