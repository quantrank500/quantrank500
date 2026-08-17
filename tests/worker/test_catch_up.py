"""Missed nights self-heal (spec §10): catch-up replays every session that still
needs a run, oldest first, and lands on the same results a live run would have."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

psycopg = pytest.importorskip("psycopg")

from quantrank500.config import APP_DSN  # noqa: E402
from quantrank500.db import apply_app_schema  # noqa: E402
from quantrank500.market_data import Bar, FakeSource  # noqa: E402
from quantrank500.worker.nightly import catch_up  # noqa: E402

ET = ZoneInfo("America/New_York")
DAY1, DAY2, DAY3 = date(2025, 7, 8), date(2025, 7, 9), date(2025, 7, 10)
CALENDAR = [date(2025, 7, 7), DAY1, DAY2, DAY3, date(2025, 7, 11)]


def bar(day: date, hour: int, minute: int, o: str, h: str, lo: str, c: str) -> Bar:
    return Bar(
        ts=datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET),
        open=Decimal(o), high=Decimal(h), low=Decimal(lo), close=Decimal(c), volume=1000,
    )


@pytest.fixture
def db():
    try:
        conn = psycopg.connect(APP_DSN, connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("local Postgres not available")
    apply_app_schema(conn)
    yield conn
    for table, where in (
        ("settlements", "prediction_id IN (SELECT id FROM predictions WHERE ticker = 'CATCH')"),
        ("prediction_events",
         "prediction_id IN (SELECT id FROM predictions WHERE ticker = 'CATCH')"),
        ("predictions", "ticker = 'CATCH'"),
        ("identities", "display_name = 'catch-up-test'"),
        ("settlement_runs", f"session_date BETWEEN '{CALENDAR[0]}' AND '{CALENDAR[-1]}'"),
    ):
        conn.execute(f"DELETE FROM {table} WHERE {where}")  # noqa: S608 — test cleanup
    conn.commit()
    conn.close()


def test_a_pc_left_off_for_three_nights_catches_up_in_one_call(db):
    identity = uuid.uuid4()
    db.execute(
        "INSERT INTO identities (public_id, api_token_hash, display_name)"
        " VALUES (%s, %s, 'catch-up-test')",
        (identity, "d" * 64),
    )
    db.execute(
        "INSERT INTO predictions"
        " (identity_id, ticker, session_date, entry_price, stop_loss, target_price,"
        "  commit_nonce)"
        " VALUES (%s, 'CATCH', %s, 10.00, 9.50, 10.90, %s)",
        (identity, DAY1, "a" * 32),
    )
    db.commit()

    source = FakeSource(
        sessions=CALENDAR,
        bars={
            ("CATCH", DAY1): [bar(DAY1, 9, 47, "10.10", "10.12", "10.00", "10.05")],  # fill
            ("CATCH", DAY2): [bar(DAY2, 10, 0, "10.20", "10.30", "10.15", "10.25")],
            ("CATCH", DAY3): [bar(DAY3, 15, 59, "10.20", "10.25", "10.15", "10.22")],  # expiry
        },
        opens={("CATCH", DAY1): Decimal("10.10")},
    )

    runs = catch_up(db, source, today=DAY3)

    assert [run.session_date for run in runs] == [DAY1, DAY2, DAY3]
    status = db.execute(
        "SELECT status FROM predictions WHERE ticker = 'CATCH'"
    ).fetchone()[0]
    assert status == "closed"


def test_nothing_pending_means_no_runs(db):
    source = FakeSource(sessions=CALENDAR)

    assert catch_up(db, source, today=DAY3) == []
