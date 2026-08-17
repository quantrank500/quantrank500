"""The Next Session Rule (spec §2, §4.1): cutoff 8:00 PM ET the prior trading day.

The information void: by 8 PM, after-hours earnings are public to everyone;
futures and pre-market are unknown to everyone. Weekend submissions therefore
target Tuesday — Monday's cutoff (Friday 8 PM) has already passed.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from quantrank500.sessions import target_session

ET = ZoneInfo("America/New_York")

# Mon Jun 2 .. Fri Jun 6, then Mon Jun 9, Tue Jun 10 (a normal fortnight, 2025)
CALENDAR = [
    date(2025, 6, 2), date(2025, 6, 3), date(2025, 6, 4), date(2025, 6, 5),
    date(2025, 6, 6), date(2025, 6, 9), date(2025, 6, 10),
]


def submitted(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2025, 6, day, hour, minute, tzinfo=ET)


def test_monday_evening_before_cutoff_targets_tuesday():
    assert target_session(submitted(2, 19), CALENDAR) == date(2025, 6, 3)


def test_at_the_cutoff_exactly_rolls_to_wednesday():
    assert target_session(submitted(2, 20), CALENDAR) == date(2025, 6, 4)


def test_monday_morning_cannot_target_monday_itself():
    # Monday's cutoff was Friday 8 PM; a 9:29 AM submission targets Tuesday.
    assert target_session(submitted(2, 9, 29), CALENDAR) == date(2025, 6, 3)


def test_friday_before_cutoff_targets_monday():
    assert target_session(submitted(6, 19, 59), CALENDAR) == date(2025, 6, 9)


def test_friday_after_cutoff_rolls_past_monday_to_tuesday():
    assert target_session(submitted(6, 20), CALENDAR) == date(2025, 6, 10)


def test_saturday_targets_tuesday_because_mondays_cutoff_has_passed():
    assert target_session(submitted(7, 12), CALENDAR) == date(2025, 6, 10)


def test_holiday_gap_uses_the_prior_trading_day_for_the_cutoff():
    # Pretend Wed Jun 4 is a holiday: Thursday's cutoff is Tuesday 8 PM.
    calendar = [d for d in CALENDAR if d != date(2025, 6, 4)]

    assert target_session(submitted(3, 19), calendar) == date(2025, 6, 5)
    assert target_session(submitted(3, 21), calendar) == date(2025, 6, 6)


def test_non_et_timestamps_are_converted():
    utc_before_cutoff = datetime(2025, 6, 2, 23, 59, tzinfo=ZoneInfo("UTC"))  # 19:59 ET

    assert target_session(utc_before_cutoff, CALENDAR) == date(2025, 6, 3)


def test_calendar_exhausted_raises():
    with pytest.raises(ValueError):
        target_session(submitted(10, 21), CALENDAR)


# --- The forward calendar (M5.7): live posting must always find tomorrow ---

from quantrank500.sessions import posting_calendar, projected_sessions  # noqa: E402


def test_projected_sessions_skip_weekends():
    # Fri 2026-08-14 -> Mon 17, Tue 18
    assert projected_sessions(date(2026, 8, 14), count=2) == \
        [date(2026, 8, 17), date(2026, 8, 18)]


def test_projected_sessions_skip_nyse_holidays():
    # Wed 2026-11-25 -> Thanksgiving Thu 26 is skipped -> Fri 27, Mon 30
    assert projected_sessions(date(2026, 11, 25), count=2) == \
        [date(2026, 11, 27), date(2026, 11, 30)]


def test_posting_calendar_extends_a_stale_lake_past_today():
    # The demo's lake ends in March; posting on Aug 17 must target Aug 18.
    stale_lake = [date(2026, 3, 26), date(2026, 3, 27)]
    now = datetime(2026, 8, 17, 19, 0, tzinfo=ET)  # Monday evening, before cutoff

    calendar = posting_calendar(stale_lake, now)

    assert target_session(now, calendar) == date(2026, 8, 18)


def test_posting_calendar_never_invents_past_sessions_for_settlement():
    # Projected days are for targeting only; nothing between the lake's end
    # and last week appears (the gap has no data and never will).
    stale_lake = [date(2026, 3, 27)]
    now = datetime(2026, 8, 17, 19, 0, tzinfo=ET)

    calendar = posting_calendar(stale_lake, now)

    gap = [d for d in calendar if date(2026, 4, 1) <= d <= date(2026, 8, 7)]
    assert gap == []


def test_posting_calendar_keeps_historical_behavior_unchanged():
    # A replayed historical submission still targets its historical session.
    now = datetime(2025, 6, 2, 19, 0, tzinfo=ET)
    assert target_session(now, posting_calendar(CALENDAR, now)) == date(2025, 6, 3)
