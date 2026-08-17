"""DatabentoIngest (spec §6, M5.7): record conversion, session filtering, the
ingest window, and the first-write-wins lake upsert.

No network and no databento package — records are duck-typed exactly as DBN
OHLCV messages present them: prices as ints scaled by 1e9, timestamps as
nanoseconds UTC. The DB tests use the test app database with its own lake
schema; the shared read-only lake is never written.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import psycopg
import pytest

from quantrank500.config import APP_DSN
from quantrank500.market_data.databento import (
    UNDEF_PRICE,
    bar_from_record,
    daily_row,
    ingest_plan,
    regular_session_bars,
    trading_days,
    upsert_bars,
    upsert_daily,
)
from quantrank500.market_data.local_lake import LAKE_SCHEMA

ET = ZoneInfo("America/New_York")
SESSION = date(2026, 8, 14)  # a Friday


def scaled(price: str) -> int:
    return int(Decimal(price) * 10**9)


def record(wall_et: datetime, o="10.0", h="10.5", lo="9.5", c="10.2", vol=100):
    """A DBN-shaped ohlcv record for the given ET wall-clock time."""
    ts_utc = wall_et.replace(tzinfo=ET).astimezone(UTC)
    return SimpleNamespace(
        ts_event=int(ts_utc.timestamp() * 1_000_000_000),
        open=scaled(o), high=scaled(h), low=scaled(lo), close=scaled(c),
        volume=vol,
    )


def test_bar_from_record_converts_scaled_prices_and_utc_nanoseconds():
    bar = bar_from_record(record(datetime(2026, 8, 14, 9, 31), o="123.4567"))
    assert bar.open == Decimal("123.4567")
    assert bar.ts == datetime(2026, 8, 14, 9, 31, tzinfo=ET)
    assert bar.volume == 100


def test_bar_from_record_quantizes_to_four_decimals():
    r = record(datetime(2026, 8, 14, 9, 31))
    r.close = int(Decimal("10.123456789") * 10**9)
    assert bar_from_record(r).close == Decimal("10.1235")


def test_bar_from_record_rejects_undefined_price_sentinel():
    r = record(datetime(2026, 8, 14, 9, 31))
    r.high = UNDEF_PRICE
    assert bar_from_record(r) is None


def test_regular_session_bars_filters_extended_hours_and_sorts():
    records = [
        record(datetime(2026, 8, 14, 15, 59)),  # last session minute — kept
        record(datetime(2026, 8, 14, 9, 29)),   # pre-market — dropped
        record(datetime(2026, 8, 14, 9, 30)),   # first session minute — kept
        record(datetime(2026, 8, 14, 16, 0)),   # after-hours — dropped
    ]
    bars = regular_session_bars(records)
    assert [b.ts.time().isoformat("minutes") for b in bars] == ["09:30", "15:59"]


def test_daily_row_takes_first_defined_record():
    row = daily_row([record(datetime(2026, 8, 14, 0, 0), o="10.0", c="10.2", vol=999)])
    assert row == (Decimal("10.0000"), Decimal("10.5000"),
                   Decimal("9.5000"), Decimal("10.2000"), 999)


def test_daily_row_none_when_no_data():
    assert daily_row([]) is None
    r = record(datetime(2026, 8, 14, 0, 0))
    r.open = UNDEF_PRICE
    assert daily_row([r]) is None


def test_trading_days_skip_weekends_and_holidays():
    # Wed 2026-11-25 .. Mon 2026-11-30: Thanksgiving Thu 26 and the weekend drop
    assert trading_days(date(2026, 11, 25), date(2026, 11, 30)) == \
        [date(2026, 11, 25), date(2026, 11, 27), date(2026, 11, 30)]


def test_ingest_plan_dailies_cover_universe_minutes_only_pending():
    mon = date(2026, 8, 17)
    dailies, minutes = ingest_plan(
        universe=["AAPL", "MSFT"], pending=["GME"],
        daily_window=[mon], minute_window=[mon],
        have_daily=set(), have_minutes=set(),
    )
    assert dailies == [("AAPL", mon), ("GME", mon), ("MSFT", mon)]
    assert minutes == [("GME", mon)]  # minutes never pulled for idle symbols


def test_ingest_plan_skips_what_the_lake_already_holds():
    mon = date(2026, 8, 17)
    dailies, minutes = ingest_plan(
        universe=["AAPL"], pending=["AAPL"],
        daily_window=[mon], minute_window=[mon],
        have_daily={("AAPL", mon)}, have_minutes={("AAPL", mon)},
    )
    assert dailies == [] and minutes == []


@pytest.fixture
def lake_conn():
    try:
        conn = psycopg.connect(APP_DSN)
    except psycopg.OperationalError:
        pytest.skip("Postgres not available")
    conn.execute(LAKE_SCHEMA)
    conn.execute("DELETE FROM lake.minute_bars WHERE ticker = 'TEST'")
    conn.execute("DELETE FROM lake.daily_prices WHERE ticker = 'TEST'")
    yield conn
    conn.rollback()
    conn.close()


def test_upsert_bars_first_write_wins(lake_conn):
    first = regular_session_bars([record(datetime(2026, 8, 14, 9, 30), c="10.2")])
    upsert_bars(lake_conn, "TEST", first)
    rewrite = regular_session_bars([record(datetime(2026, 8, 14, 9, 30), c="99.9")])
    upsert_bars(lake_conn, "TEST", rewrite)
    (close,) = lake_conn.execute(
        "SELECT close FROM lake.minute_bars WHERE ticker = 'TEST'"
    ).fetchone()
    assert close == Decimal("10.2000")  # the lake is evidence: never rewritten


def test_upsert_daily_first_write_wins(lake_conn):
    upsert_daily(lake_conn, "TEST", SESSION, daily_row([record(datetime(2026, 8, 14, 0, 0))]))
    changed = record(datetime(2026, 8, 14, 0, 0), c="99.9")
    upsert_daily(lake_conn, "TEST", SESSION, daily_row([changed]))
    (close,) = lake_conn.execute(
        "SELECT close_price FROM lake.daily_prices"
        " WHERE ticker = 'TEST' AND session_date = %s", (SESSION,)
    ).fetchone()
    assert close == Decimal("10.2000")
