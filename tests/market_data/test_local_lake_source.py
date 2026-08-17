"""Integration tests: LocalLakeSource against the real lake schema in local Postgres.

Seeds synthetic tickers (TESTA/TESTB) and cleans them up; never touches copied
real data. Skipped entirely when the local database is unreachable.
"""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

psycopg = pytest.importorskip("psycopg")

from quantrank500.config import LAKE_DSN  # noqa: E402
from quantrank500.market_data.local_lake import (  # noqa: E402
    LAKE_SCHEMA,
    LocalLakeSource,
)

ET = ZoneInfo("America/New_York")
JUN2 = date(2025, 6, 2)
JUN3 = date(2025, 6, 3)


@pytest.fixture(scope="module")
def seeded_db():
    try:
        conn = psycopg.connect(LAKE_DSN, connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("local Postgres not available")
    conn.execute(LAKE_SCHEMA)
    conn.execute("DELETE FROM lake.minute_bars WHERE ticker LIKE 'TEST%'")
    conn.execute("DELETE FROM lake.daily_prices WHERE ticker LIKE 'TEST%'")
    conn.cursor().executemany(
        "INSERT INTO lake.minute_bars (ticker, ts_et, open, high, low, close, volume)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s)",
        [
            # deliberately inserted out of order; both sessions present
            ("TESTA", "2025-06-02 09:31:00", "10.05", "10.15", "10.00", "10.10", 500),
            ("TESTA", "2025-06-02 09:30:00", "10.00", "10.10", "9.95", "10.05", 1200),
            ("TESTA", "2025-06-03 09:30:00", "11.00", "11.10", "10.95", "11.05", 800),
        ],
    )
    conn.cursor().executemany(
        "INSERT INTO lake.daily_prices"
        " (ticker, session_date, open_price, high, low, close_price, volume)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s)",
        [
            ("TESTA", JUN2, "10.00", "10.50", "9.90", "10.25", 100000),
            ("TESTA", JUN3, "11.00", "11.20", "10.80", "11.10", 90000),
            ("TESTB", JUN2, "50.00", "51.00", "49.50", "50.75", 20000),
        ],
    )
    conn.commit()
    yield conn
    conn.execute("DELETE FROM lake.minute_bars WHERE ticker LIKE 'TEST%'")
    conn.execute("DELETE FROM lake.daily_prices WHERE ticker LIKE 'TEST%'")
    conn.commit()
    conn.close()


@pytest.fixture(scope="module")
def source(seeded_db):
    src = LocalLakeSource()
    yield src
    src.close()


def test_session_bars_are_chronological_decimal_and_et_aware(source):
    bars = source.session_bars("TESTA", JUN2)

    assert [b.ts for b in bars] == [
        datetime(2025, 6, 2, 9, 30, tzinfo=ET),
        datetime(2025, 6, 2, 9, 31, tzinfo=ET),
    ]
    assert bars[0].open == Decimal("10.00")
    assert bars[0].low == Decimal("9.95")
    assert bars[0].volume == 1200


def test_session_bars_returns_only_the_requested_session(source):
    bars = source.session_bars("TESTA", JUN3)

    assert len(bars) == 1
    assert bars[0].ts.date() == JUN3


def test_session_bars_for_unknown_ticker_is_empty(source):
    assert source.session_bars("TESTZ", JUN2) == []


def test_official_open_and_close_come_from_daily_prices(source):
    assert source.official_open("TESTA", JUN2) == Decimal("10.00")
    assert source.official_close("TESTA", JUN2) == Decimal("10.25")
    assert source.official_close("TESTA", date(2025, 6, 4)) is None


def test_session_trades_is_none_because_the_lake_is_minute_bars(source):
    assert source.session_trades("TESTA", JUN2) is None


def test_calendar_lists_distinct_session_dates_ascending(source):
    sessions = source.calendar()

    assert JUN2 in sessions and JUN3 in sessions
    assert sessions == sorted(set(sessions))
