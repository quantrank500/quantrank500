"""Databento ingest — nightly pull of EQUS.MINI bars into the lake (spec §6, M5.7).

The engine keeps reading through LocalLakeSource; this module only WRITES.
For each session that pending predictions need, it fetches 1-minute bars and
the daily bar for each pending ticker and upserts them into lake.minute_bars /
lake.daily_prices. First write wins — the lake is evidence, never rewritten.

Cost control is structural: only symbols with open predictions are pulled, so
cost scales with predictions, not the catalog (~$0.08/night measured for 13
symbols). Bar mode, regular session (09:30–16:00 ET) only; daily open/close
come from EQUS.MINI's ohlcv-1d — a venue blend, not the official auction
prints (the documented H.4 envelope).

EQUS.MINI history for a session becomes available after ~midnight ET, so the
evening run usually finds nothing for that day and the next morning run picks
it up; catch_up self-heals either way. Holidays return no data and are simply
never added to the calendar.
"""

import logging
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from quantrank500.market_data.types import Bar
from quantrank500.sessions import NYSE_HOLIDAYS

log = logging.getLogger("ingest")

ET = ZoneInfo("America/New_York")
DATASET = "EQUS.MINI"
PRICE_SCALE = 10**9  # DBN prices are ints scaled by 1e9
UNDEF_PRICE = (1 << 63) - 1  # DBN sentinel for "no price"
FOUR_DP = Decimal("0.0001")
SESSION_START = time(9, 30)
SESSION_END = time(16, 0)

# fetch(ticker, schema, session) -> DBN-shaped records; tests inject a fake
Fetch = Callable[[str, str, date], Iterable]


def bar_from_record(record) -> Bar | None:
    """One DBN ohlcv record -> Bar (ET-aware), or None if any price is undefined."""
    prices = (record.open, record.high, record.low, record.close)
    if any(p == UNDEF_PRICE for p in prices):
        return None
    ts_utc = datetime.fromtimestamp(record.ts_event / 1_000_000_000, tz=UTC)
    o, h, lo, c = ((Decimal(p) / PRICE_SCALE).quantize(FOUR_DP) for p in prices)
    return Bar(ts=ts_utc.astimezone(ET), open=o, high=h, low=lo, close=c,
               volume=int(record.volume))


def regular_session_bars(records) -> list[Bar]:
    bars = (bar_from_record(r) for r in records)
    session_only = [b for b in bars if b and SESSION_START <= b.ts.time() < SESSION_END]
    return sorted(session_only, key=lambda b: b.ts)


def daily_row(records) -> tuple[Decimal, Decimal, Decimal, Decimal, int] | None:
    """The session's daily OHLC + volume from the first defined ohlcv-1d record."""
    for record in records:
        bar = bar_from_record(record)
        if bar:
            return (bar.open, bar.high, bar.low, bar.close, bar.volume)
    return None


def trading_days(start: date, end: date) -> list[date]:
    """Weekdays minus NYSE holidays, start..end inclusive."""
    days = ((start + timedelta(days=n)) for n in range((end - start).days + 1))
    return [d for d in days if d.weekday() < 5 and d not in NYSE_HOLIDAYS]


def ingest_plan(
    universe: Iterable[str],
    pending: Iterable[str],
    daily_window: list[date],
    minute_window: list[date],
    have_daily: set[tuple[str, date]],
    have_minutes: set[tuple[str, date]],
) -> tuple[list[tuple[str, date]], list[tuple[str, date]]]:
    """(ticker, session) pulls still needed. Dailies cover the whole posting
    universe — pennies, and they keep every member's entry band honest.
    Minutes cover only pending predictions' tickers — the real cost, scaling
    with predictions, not the catalog."""
    tickers = sorted(set(universe) | set(pending))
    dailies = [(t, d) for d in daily_window for t in tickers if (t, d) not in have_daily]
    minutes = [(t, d) for d in minute_window for t in pending if (t, d) not in have_minutes]
    return dailies, minutes


def upsert_bars(conn, ticker: str, bars: list[Bar]) -> None:
    conn.cursor().executemany(
        "INSERT INTO lake.minute_bars (ticker, ts_et, open, high, low, close, volume)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
        [(ticker, b.ts.replace(tzinfo=None), b.open, b.high, b.low, b.close, b.volume)
         for b in bars],
    )


def upsert_daily(conn, ticker: str, session: date, row) -> None:
    o, h, lo, c, volume = row
    conn.execute(
        "INSERT INTO lake.daily_prices"
        " (ticker, session_date, open_price, high, low, close_price, volume)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
        (ticker, session, o, h, lo, c, volume),
    )


DAILY_LOOKBACK_DAYS = 7


def ingest_pending(app_conn, lake_conn, fetch: Fetch, today: date) -> tuple[int, int]:
    """Pull what the platform still needs, as of last night (a session's
    history publishes after ~midnight ET, so today itself is never asked for).
    Dailies refresh every symbol ever predicted (keeps entry bands honest);
    minutes cover only pending predictions. Cost scales with usage — the
    floors-based universe (spec v13.41) has no list to refresh.
    Returns (daily_pulls, minute_pulls) that produced data."""
    last = today - timedelta(days=1)
    predicted = [t for (t,) in app_conn.execute(
        "SELECT DISTINCT ticker FROM predictions"
        " WHERE ticker IS NOT NULL ORDER BY ticker"
    ).fetchall()]
    pending = [t for (t,) in app_conn.execute(
        "SELECT DISTINCT ticker FROM predictions"
        " WHERE status IN ('queued', 'active') AND ticker IS NOT NULL ORDER BY ticker"
    ).fetchall()]
    (min_needed,) = app_conn.execute(
        "SELECT MIN(session_date) FROM predictions WHERE status IN ('queued', 'active')"
    ).fetchone()

    daily_window = trading_days(last - timedelta(days=DAILY_LOOKBACK_DAYS - 1), last)
    minute_window = trading_days(min_needed, last) if min_needed else []

    have_daily = set()
    if daily_window:
        have_daily = {(t, d) for t, d in lake_conn.execute(
            "SELECT ticker, session_date FROM lake.daily_prices WHERE session_date >= %s",
            (daily_window[0],),
        ).fetchall()}
    have_minutes = set()
    if minute_window and pending:
        have_minutes = {(t, d) for t, d in lake_conn.execute(
            "SELECT DISTINCT ticker, ts_et::date FROM lake.minute_bars"
            " WHERE ticker = ANY(%s) AND ts_et >= %s",
            (pending, minute_window[0]),
        ).fetchall()}

    dailies, minutes = ingest_plan(
        predicted, pending, daily_window, minute_window, have_daily, have_minutes
    )
    # commit per pull: first-write-wins makes partial progress safe, so a
    # crash or restart resumes where it left off instead of starting over
    minutes_pulled = 0
    for ticker, session in minutes:
        bars = regular_session_bars(fetch(ticker, "ohlcv-1m", session))
        if bars:
            upsert_bars(lake_conn, ticker, bars)
            minutes_pulled += 1
        lake_conn.commit()
    dailies_pulled = 0
    for i, (ticker, session) in enumerate(dailies, start=1):
        row = daily_row(fetch(ticker, "ohlcv-1d", session))
        if row:
            upsert_daily(lake_conn, ticker, session, row)
            dailies_pulled += 1
        lake_conn.commit()
        if i % 100 == 0:
            log.info("dailies %s/%s (%s with data)", i, len(dailies), dailies_pulled)
    return dailies_pulled, minutes_pulled


def databento_fetch(api_key: str) -> Fetch:
    """The real Fetch, backed by the databento package (optional dependency —
    imported lazily so dev/test environments never need it). Transient server
    errors (504s) retry twice with backoff; persistent errors still raise."""
    import socket
    import time

    import databento as db

    socket.setdefaulttimeout(60)  # a dead pull must fail loudly, not stall the night
    client = db.Historical(api_key)

    def fetch(ticker: str, schema: str, session: date):
        last_error = None
        for attempt in range(3):
            try:
                return list(client.timeseries.get_range(
                    dataset=DATASET, symbols=[ticker], schema=schema,
                    start=session.isoformat(),
                    end=(session + timedelta(days=1)).isoformat(),
                ))
            except Exception as error:  # noqa: BLE001 — gateway hiccups take any shape
                last_error = error
                time.sleep(2 * 2**attempt)
        raise last_error

    return fetch
