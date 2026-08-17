"""The floors-based universe (spec v13.41): reference lookup and floor checks.
Fetch is duck-typed; the DB tests use the test database's own lake schema."""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import psycopg
import pytest
from tests.test_databento_ingest import record

from quantrank500.config import APP_DSN
from quantrank500.market_data.local_lake import LAKE_SCHEMA
from quantrank500.market_data.reference import (
    Reference,
    databento_latest,
    floors_reason,
    lake_latest,
    make_reference,
)

ET = ZoneInfo("America/New_York")
MON, TUE = date(2026, 8, 10), date(2026, 8, 11)


def ref(close="10.00", volume=500_000, session=MON) -> Reference:
    return Reference(session=session, close=Decimal(close), volume=volume)


def test_floors_pass_for_a_liquid_stock():
    assert floors_reason(ref(close="25.00", volume=1_000_000)) is None


def test_price_floor_rejects_sub_3_dollar_stocks():
    assert "price below" in floors_reason(ref(close="2.99", volume=10_000_000))


def test_dollar_volume_floor_rejects_illiquid_stocks():
    # $10 x 50k shares = $500k/day — half the floor
    assert "dollar volume" in floors_reason(ref(close="10.00", volume=50_000))


@pytest.fixture
def lake(request):
    try:
        conn = psycopg.connect(APP_DSN)
    except psycopg.OperationalError:
        pytest.skip("Postgres not available")
    conn.execute(LAKE_SCHEMA)
    conn.execute("DELETE FROM lake.daily_prices WHERE ticker = 'REFT'")
    conn.commit()
    yield conn
    conn.rollback()
    conn.execute("DELETE FROM lake.daily_prices WHERE ticker = 'REFT'")
    conn.commit()
    conn.close()


def seed_daily(conn, session, close="10.00", volume=100):
    conn.execute(
        "INSERT INTO lake.daily_prices"
        " (ticker, session_date, open_price, high, low, close_price, volume)"
        " VALUES ('REFT', %s, 1, 1, 1, %s, %s)",
        (session, Decimal(close), volume),
    )


def test_lake_latest_returns_most_recent_session(lake):
    seed_daily(lake, MON, close="10.00")
    seed_daily(lake, TUE, close="11.00")
    result = lake_latest(lake, "REFT")
    assert (result.session, result.close) == (TUE, Decimal("11.00"))


def test_databento_latest_walks_back_and_teaches_the_lake(lake):
    calls = []

    def fetch(ticker, schema, session):
        calls.append(session)
        # data exists only for Monday; later sessions are empty
        return [record(datetime(2026, 8, 10, 0, 0))] if session == MON else []

    result = databento_latest(fetch, lake, "REFT", today=date(2026, 8, 12))
    assert result.session == MON and result.close == Decimal("10.2000")
    assert calls[0] > calls[-1]  # newest sessions tried first
    assert lake_latest(lake, "REFT").session == MON  # the pull was upserted


def test_lookup_prefers_fresh_lake_and_falls_back_to_stale_on_dry_fetch(lake):
    seed_daily(lake, date(2026, 3, 27), close="9.99")  # stale by months
    lake.commit()  # the lookup opens its own connection

    def dry_fetch(ticker, schema, session):
        return []

    lookup = make_reference(lambda: psycopg.connect(APP_DSN), dry_fetch,
                            today=lambda: date(2026, 8, 17))
    result = lookup("REFT")
    assert result.close == Decimal("9.99")  # stale beats nothing


def test_lookup_unknown_ticker_without_fetch_is_none(lake):
    lookup = make_reference(lambda: psycopg.connect(APP_DSN), None,
                            today=lambda: date(2026, 8, 17))
    assert lookup("NEVERSEEN") is None
