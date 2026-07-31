"""Detection rules.

Deterministic and pure, like the rest of the rules engine: data in, Insight
records out. Nothing here changes training or writes anywhere -- these are
observations for the user to interpret, which is why even the recovery rule
reports a correlation from their own history rather than a recommendation.

The drift rule from the plan is deliberately absent: it needs planned sessions
to compare against, and Plan/Availability arrive in v0.3. Approximating it from
habitual training days would invent a signal rather than measure one.
"""

from __future__ import annotations

from datetime import date, timedelta
from statistics import mean

from .models import ExerciseTemplate, Insight, SetEntry, VolumeTarget
from .progression import RepRange, main_lifts, progression_state, stalled
from .volume import compute_volume, week_start

# Thresholds, gathered here so they are tunable in one place rather than buried.
VOLUME_DROP_RATIO = 0.75  # below 75% of the trailing average is a drop
VOLUME_DROP_MIN_BASELINE = 4.0  # ignore muscles whose baseline is trivially small
COVERAGE_GAP_WEEKS = 2
STALL_SESSIONS = 3
SLEEP_SHORT_MINUTES = 30.0  # below baseline by this much counts as short
SLEEP_RECENT_DAYS = 3
SLEEP_BASELINE_DAYS = 28


def detect(
    sets: list[SetEntry],
    templates: dict[str, ExerciseTemplate],
    targets: dict[str, VolumeTarget],
    sleep_by_day: dict[date, float],
    today: date,
    *,
    secondary_weight: float = 0.5,
    count_warmup_sets: bool = False,
    week_starts_on: int = 0,
    rep_ranges: dict[str, RepRange] | None = None,
) -> list[Insight]:
    """Run every rule and return the findings, most severe first."""
    insights: list[Insight] = []
    insights += volume_drop(
        sets, templates, today,
        secondary_weight=secondary_weight,
        count_warmup_sets=count_warmup_sets,
        week_starts_on=week_starts_on,
    )
    insights += coverage_gap(
        sets, templates, targets, today,
        secondary_weight=secondary_weight,
        count_warmup_sets=count_warmup_sets,
        week_starts_on=week_starts_on,
    )
    insights += progression_insights(sets, templates, today, rep_ranges or {})
    insights += recovery_flag(sets, sleep_by_day, today)

    order = {"warn": 0, "info": 1}
    insights.sort(key=lambda i: (order.get(i.severity, 9), i.rule, i.subject))
    return insights


def _week_rollups(
    sets, templates, today, weeks, *, secondary_weight, count_warmup_sets, week_starts_on
):
    """The last ``weeks`` complete weeks, newest first."""
    current = week_start(today, week_starts_on)
    out = []
    for index in range(1, weeks + 1):
        start = current - timedelta(days=7 * index)
        out.append(
            compute_volume(
                sets, templates, start, start + timedelta(days=7),
                secondary_weight=secondary_weight,
                count_warmup_sets=count_warmup_sets,
            )
        )
    return out


def volume_drop(
    sets, templates, today, *, secondary_weight=0.5, count_warmup_sets=False, week_starts_on=0
) -> list[Insight]:
    """Last complete week down more than 25% against the trailing 4-week average."""
    rollups = _week_rollups(
        sets, templates, today, 5,
        secondary_weight=secondary_weight,
        count_warmup_sets=count_warmup_sets,
        week_starts_on=week_starts_on,
    )
    if len(rollups) < 5:
        return []
    latest, baseline_weeks = rollups[0], rollups[1:]

    findings: list[Insight] = []
    muscles = {m for rollup in rollups for m in rollup.muscles}
    for muscle in sorted(muscles):
        baseline = mean(rollup.get(muscle).effective_sets for rollup in baseline_weeks)
        if baseline < VOLUME_DROP_MIN_BASELINE:
            continue
        actual = latest.get(muscle).effective_sets
        if actual >= baseline * VOLUME_DROP_RATIO:
            continue
        drop = round(100 * (1 - actual / baseline))
        findings.append(
            Insight(
                rule="volume_drop",
                severity="warn",
                subject=muscle,
                message=(
                    f"{muscle.replace('_', ' ')} volume was {actual:.1f} sets last week, "
                    f"{drop}% below the {baseline:.1f} average of the four weeks before it"
                ),
                detected_at=today,
                data={
                    "effective_sets": round(actual, 2),
                    "baseline_sets": round(baseline, 2),
                    "drop_pct": drop,
                },
            )
        )
    return findings


def coverage_gap(
    sets, templates, targets, today, *, secondary_weight=0.5, count_warmup_sets=False, week_starts_on=0
) -> list[Insight]:
    """Muscle groups under their frequency target two weeks running."""
    rollups = _week_rollups(
        sets, templates, today, COVERAGE_GAP_WEEKS,
        secondary_weight=secondary_weight,
        count_warmup_sets=count_warmup_sets,
        week_starts_on=week_starts_on,
    )
    if len(rollups) < COVERAGE_GAP_WEEKS:
        return []

    # Whether this muscle is part of the programme at all decides severity: a
    # muscle that was being trained and stopped is a regression; one that never
    # appears is a standing gap, and shouldn't shout as loudly.
    recent = compute_volume(
        sets, templates, today - timedelta(weeks=8), today + timedelta(days=1),
        secondary_weight=secondary_weight,
        count_warmup_sets=count_warmup_sets,
    )

    findings: list[Insight] = []
    for muscle, target in sorted(targets.items()):
        frequencies = [rollup.get(muscle).frequency for rollup in rollups]
        if any(freq >= target.frequency_per_week for freq in frequencies):
            continue
        in_programme = recent.get(muscle).effective_sets > 0
        findings.append(
            Insight(
                rule="coverage_gap",
                severity="warn" if in_programme else "info",
                subject=muscle,
                message=(
                    f"{muscle.replace('_', ' ')} hit {frequencies[0]}x and {frequencies[1]}x "
                    f"in the last two weeks, against a target of {target.frequency_per_week}x"
                    + ("" if in_programme else " (not trained at all in the last 8 weeks)")
                ),
                detected_at=today,
                data={
                    "frequencies": frequencies,
                    "target_frequency": target.frequency_per_week,
                    "in_programme": in_programme,
                },
            )
        )
    return findings


def progression_insights(
    sets, templates, today, rep_ranges: dict[str, RepRange]
) -> list[Insight]:
    """Stalls and ready-to-progress calls on the lifts that are trained often."""
    findings: list[Insight] = []
    for template_id, title in main_lifts(sets):
        template = templates.get(template_id)
        state = progression_state(
            sets,
            template_id,
            title,
            rep_range=rep_ranges.get(template_id),
            equipment_category=template.equipment_category if template else None,
        )

        if state.ready_to_progress:
            findings.append(
                Insight(
                    rule="progression_ready",
                    severity="info",
                    subject=title,
                    message=(
                        f"{title}: all working sets at the top of "
                        f"{state.rep_range.low}-{state.rep_range.high} at "
                        f"{state.working_weight_kg:g} kg -- {state.verdict.split('--')[-1].strip()}"
                    ),
                    detected_at=today,
                    data=state.as_dict(),
                )
            )
        elif stalled(sets, template_id, STALL_SESSIONS):
            findings.append(
                Insight(
                    rule="stall",
                    severity="warn",
                    subject=title,
                    message=(
                        f"{title}: no load or rep increase across the last "
                        f"{STALL_SESSIONS} sessions at {state.working_weight_kg:g} kg"
                    ),
                    detected_at=today,
                    data=state.as_dict(),
                )
            )
    return findings


def recovery_flag(
    sets: list[SetEntry], sleep_by_day: dict[date, float], today: date
) -> list[Insight]:
    """Short sleep against personal baseline, reported with the user's own pattern.

    Informational by design. The output is a correlation drawn from their history
    -- what training actually looked like after previous short-sleep nights --
    rather than any instruction about what to do today.
    """
    if not sleep_by_day:
        return []

    recent_days = [today - timedelta(days=offset) for offset in range(1, SLEEP_RECENT_DAYS + 1)]
    recent = [sleep_by_day[day] for day in recent_days if day in sleep_by_day]
    if len(recent) < SLEEP_RECENT_DAYS:
        return []

    baseline_days = [today - timedelta(days=offset) for offset in range(1, SLEEP_BASELINE_DAYS + 1)]
    baseline_values = [sleep_by_day[day] for day in baseline_days if day in sleep_by_day]
    if len(baseline_values) < 7:
        return []

    recent_mean, baseline_mean = mean(recent), mean(baseline_values)
    if recent_mean >= baseline_mean - SLEEP_SHORT_MINUTES:
        return []

    # What happened on training days that followed a short night, historically.
    short_nights = {day for day, minutes in sleep_by_day.items() if minutes < baseline_mean - SLEEP_SHORT_MINUTES}
    sets_by_day: dict[date, int] = {}
    for entry in sets:
        if entry.set_type in {"normal", "failure", "dropset"}:
            sets_by_day[entry.local_date] = sets_by_day.get(entry.local_date, 0) + 1

    after_short = [count for day, count in sets_by_day.items() if day in short_nights]
    others = [count for day, count in sets_by_day.items() if day not in short_nights]

    correlation = ""
    if len(after_short) >= 3 and others:
        correlation = (
            f" On your {len(after_short)} previous training days after a short night you "
            f"averaged {mean(after_short):.0f} working sets, against {mean(others):.0f} otherwise."
        )

    return [
        Insight(
            rule="recovery_flag",
            severity="info",
            subject="sleep",
            message=(
                f"Sleep averaged {recent_mean / 60:.1f}h over the last {SLEEP_RECENT_DAYS} nights, "
                f"below your {baseline_mean / 60:.1f}h baseline.{correlation}"
            ),
            detected_at=today,
            data={
                "recent_mean_minutes": round(recent_mean),
                "baseline_mean_minutes": round(baseline_mean),
                "sets_after_short_nights": round(mean(after_short), 1) if after_short else None,
                "sets_otherwise": round(mean(others), 1) if others else None,
            },
        )
    ]
