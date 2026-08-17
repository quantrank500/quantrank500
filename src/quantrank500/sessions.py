"""The Next Session Rule (spec §2, §4.1).

Submissions for a session close at 8:00 PM ET on the prior trading day; later
submissions roll forward to the next session whose cutoff is still open.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
CUTOFF = time(20, 0)  # 8:00 PM ET — structural rule (spec §2)

# NYSE full-closure holidays (observed dates). Known years ahead; extend the
# list each year — projected_sessions raises past its coverage.
NYSE_HOLIDAYS = {
    # 2025
    date(2025, 1, 1), date(2025, 1, 20), date(2025, 2, 17), date(2025, 4, 18),
    date(2025, 5, 26), date(2025, 6, 19), date(2025, 7, 4), date(2025, 9, 1),
    date(2025, 11, 27), date(2025, 12, 25),
    # 2026
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
    date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
    date(2026, 11, 26), date(2026, 12, 25),
    # 2027
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26),
    date(2027, 5, 31), date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6),
    date(2027, 11, 25), date(2027, 12, 24),
}
HOLIDAYS_COVER_THROUGH = date(2027, 12, 31)


def cutoff_for(session: date, calendar: list[date]) -> datetime | None:
    """8:00 PM ET on the trading day before `session`; None if the calendar
    holds no prior trading day to anchor the cutoff to."""
    prior_days = [d for d in calendar if d < session]
    if not prior_days:
        return None
    return datetime.combine(prior_days[-1], CUTOFF, tzinfo=ET)


def target_session(submitted_at: datetime, calendar: list[date]) -> date:
    """The earliest session whose cutoff is still open at `submitted_at`."""
    submitted_et = submitted_at.astimezone(ET)
    for session in sorted(calendar):
        cutoff = cutoff_for(session, calendar)
        if cutoff is not None and submitted_et < cutoff:
            return session
    raise ValueError(f"no open session in the calendar after {submitted_et}")


def projected_sessions(after: date, count: int) -> list[date]:
    """The next `count` trading days strictly after `after` — weekdays minus
    NYSE holidays. The future is projectable; only the past needs evidence."""
    sessions: list[date] = []
    day = after
    while len(sessions) < count:
        day += timedelta(days=1)
        if day > HOLIDAYS_COVER_THROUGH:
            raise ValueError(f"NYSE_HOLIDAYS ends {HOLIDAYS_COVER_THROUGH}; extend the list")
        if day.weekday() < 5 and day not in NYSE_HOLIDAYS:
            sessions.append(day)
    return sessions


def posting_calendar(lake_calendar: list[date], now: datetime, horizon: int = 10) -> list[date]:
    """The lake's sessions (evidence) extended with projected trading days
    around `now`, so the Next Session Rule can always target tomorrow even
    when the lake lags. A few recent days are included purely to anchor the
    next session's cutoff; their own cutoffs have passed, so they are never
    targeted. Settlement never sees this calendar — it settles only sessions
    with data."""
    today = now.astimezone(ET).date()
    projected = projected_sessions(today - timedelta(days=8), count=horizon + 6)
    return sorted(set(lake_calendar) | set(projected))
