"""Window parsing. 'Last week' meaning the wrong seven days would quietly wrong
every number the app reports, so the semantics are pinned here."""

from __future__ import annotations

from datetime import date

import pytest

from fitness_ledger.queries import WindowError, describe_window, parse_window

# A Wednesday.
TODAY = date(2026, 7, 29)


def test_this_week_is_the_current_monday_to_next_monday():
    assert parse_window("this-week", TODAY) == (date(2026, 7, 27), date(2026, 8, 3))


def test_last_week_is_the_previous_complete_week():
    assert parse_window("last-week", TODAY) == (date(2026, 7, 20), date(2026, 7, 27))


def test_last_n_weeks_excludes_the_part_finished_current_week():
    # Otherwise a trailing average is diluted by however far into today we are.
    assert parse_window("last-4-weeks", TODAY) == (date(2026, 6, 29), date(2026, 7, 27))


def test_last_n_days_is_inclusive_of_today():
    assert parse_window("last-7-days", TODAY) == (date(2026, 7, 22), date(2026, 7, 30))


def test_today_and_yesterday():
    assert parse_window("today", TODAY) == (date(2026, 7, 29), date(2026, 7, 30))
    assert parse_window("yesterday", TODAY) == (date(2026, 7, 28), date(2026, 7, 29))


def test_month_window():
    assert parse_window("2026-07", TODAY) == (date(2026, 7, 1), date(2026, 8, 1))


def test_december_month_rolls_into_next_year():
    assert parse_window("2026-12", TODAY) == (date(2026, 12, 1), date(2027, 1, 1))


def test_explicit_range_is_inclusive_of_both_ends():
    assert parse_window("2026-07-01:2026-07-31", TODAY) == (date(2026, 7, 1), date(2026, 8, 1))


def test_sunday_start_weeks():
    assert parse_window("this-week", TODAY, week_starts_on=6) == (
        date(2026, 7, 26),
        date(2026, 8, 2),
    )


def test_underscores_and_spaces_are_accepted():
    assert parse_window("last week", TODAY) == parse_window("last-week", TODAY)
    assert parse_window("last_4_weeks", TODAY) == parse_window("last-4-weeks", TODAY)


def test_unknown_window_raises():
    with pytest.raises(WindowError):
        parse_window("since the dawn of time", TODAY)


def test_describe_window_renders_inclusive_dates():
    assert describe_window(date(2026, 7, 20), date(2026, 7, 27)) == "2026-07-20 to 2026-07-26"
    assert describe_window(date(2026, 7, 20), date(2026, 7, 21)) == "2026-07-20"
