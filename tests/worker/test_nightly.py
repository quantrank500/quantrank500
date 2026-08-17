"""The nightly settlement replay (spec §4.3) as a time machine: --session-date.

Integration: real Postgres schema, FakeSource bars. Deterministic and idempotent —
a re-run of the same session must change nothing.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

psycopg = pytest.importorskip("psycopg")

from quantrank500.config import APP_DSN  # noqa: E402
from quantrank500.db import apply_app_schema  # noqa: E402
from quantrank500.market_data import Bar, FakeSource  # noqa: E402
from quantrank500.worker.nightly import run_settlement  # noqa: E402

ET = ZoneInfo("America/New_York")
DAY1, DAY2, DAY3 = date(2025, 6, 3), date(2025, 6, 4), date(2025, 6, 5)
CALENDAR = [date(2025, 6, 2), DAY1, DAY2, DAY3, date(2025, 6, 6)]


def bar(day: date, hour: int, minute: int, o: str, h: str, lo: str, c: str) -> Bar:
    return Bar(
        ts=datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET),
        open=Decimal(o), high=Decimal(h), low=Decimal(lo), close=Decimal(c), volume=1000,
    )


def quiet(day: date) -> list[Bar]:
    return [
        bar(day, 9, 30, "10.20", "10.30", "10.15", "10.25"),
        bar(day, 15, 59, "10.20", "10.25", "10.15", "10.22"),
    ]


@pytest.fixture
def db():
    try:
        conn = psycopg.connect(APP_DSN, connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("local Postgres not available")
    apply_app_schema(conn)
    yield conn
    conn.execute(
        "DELETE FROM settlements WHERE prediction_id IN"
        " (SELECT id FROM predictions WHERE ticker = 'NIGHT')"
    )
    conn.execute(
        "DELETE FROM prediction_events WHERE prediction_id IN"
        " (SELECT id FROM predictions WHERE ticker = 'NIGHT')"
    )
    conn.execute("DELETE FROM predictions WHERE ticker = 'NIGHT'")
    conn.execute("DELETE FROM identities WHERE display_name = 'nightly-test'")
    conn.execute("DELETE FROM settlement_runs WHERE session_date BETWEEN %s AND %s",
                 (CALENDAR[0], CALENDAR[-1]))
    conn.commit()
    conn.close()


@pytest.fixture
def prediction_id(db):
    identity = uuid.uuid4()
    db.execute(
        "INSERT INTO identities (public_id, api_token_hash, display_name)"
        " VALUES (%s, %s, 'nightly-test')",
        (identity, "f" * 64),
    )
    row = db.execute(
        "INSERT INTO predictions"
        " (identity_id, ticker, session_date, entry_price, stop_loss, target_price,"
        "  commit_nonce)"
        " VALUES (%s, 'NIGHT', %s, 10.00, 9.50, 10.90, %s) RETURNING id",
        (identity, DAY1, "b" * 32),
    ).fetchone()[0]
    db.commit()
    return row


def status_of(db, prediction_id) -> str:
    return db.execute(
        "SELECT status FROM predictions WHERE id = %s", (prediction_id,)
    ).fetchone()[0]


def touch_fill_day1() -> dict:
    return {("NIGHT", DAY1): [
        bar(DAY1, 9, 30, "10.20", "10.30", "10.15", "10.25"),
        bar(DAY1, 9, 47, "10.10", "10.12", "10.00", "10.05"),
    ]}


def test_a_touching_session_fills_the_prediction(db, prediction_id):
    source = FakeSource(sessions=CALENDAR, bars=touch_fill_day1(),
                        opens={("NIGHT", DAY1): Decimal("10.20")})

    run = run_settlement(db, source, DAY1)

    assert status_of(db, prediction_id) == "active"
    fill = db.execute(
        "SELECT fill_price FROM predictions WHERE id = %s", (prediction_id,)
    ).fetchone()[0]
    assert fill == Decimal("10.0000")
    assert run.data_mode == "bars"


def test_a_session_that_never_touches_marks_unfilled(db, prediction_id):
    source = FakeSource(sessions=CALENDAR, bars={("NIGHT", DAY1): quiet(DAY1)},
                        opens={("NIGHT", DAY1): Decimal("10.20")})

    run = run_settlement(db, source, DAY1)

    assert status_of(db, prediction_id) == "unfilled"
    assert run.unfilled == 1


def test_same_day_stop_settles_the_night_it_happens(db, prediction_id):
    bars = {("NIGHT", DAY1): [
        bar(DAY1, 9, 47, "10.10", "10.12", "10.00", "10.05"),   # touch fill
        bar(DAY1, 11, 3, "9.60", "9.65", "9.45", "9.50"),       # stop
    ]}
    source = FakeSource(sessions=CALENDAR, bars=bars, opens={("NIGHT", DAY1): Decimal("10.10")})

    run = run_settlement(db, source, DAY1)

    assert status_of(db, prediction_id) == "closed"
    result, pnl = db.execute(
        "SELECT result, pnl FROM settlements WHERE prediction_id = %s", (prediction_id,)
    ).fetchone()
    assert result == "loss"
    assert pnl == Decimal("-25.0000")  # 500 x (9.50-10.00)/10.00
    assert run.settled == 1


def test_untouched_position_expires_at_the_end_of_day_three(db, prediction_id):
    source = FakeSource(
        sessions=CALENDAR,
        bars=touch_fill_day1() | {("NIGHT", DAY2): quiet(DAY2), ("NIGHT", DAY3): quiet(DAY3)},
        opens={("NIGHT", DAY1): Decimal("10.20")},
    )

    run_settlement(db, source, DAY1)
    run_settlement(db, source, DAY2)
    assert status_of(db, prediction_id) == "active"  # still open after day 2

    run_settlement(db, source, DAY3)

    assert status_of(db, prediction_id) == "closed"
    settled_by, exit_price = db.execute(
        "SELECT settled_by, exit_price FROM settlements WHERE prediction_id = %s",
        (prediction_id,),
    ).fetchone()
    assert settled_by == "expiry"
    assert exit_price == Decimal("10.2200")  # day 3 final close

def test_rerunning_a_session_changes_nothing(db, prediction_id):
    bars = {("NIGHT", DAY1): [
        bar(DAY1, 9, 47, "10.10", "10.12", "10.00", "10.05"),
        bar(DAY1, 11, 3, "9.60", "9.65", "9.45", "9.50"),
    ]}
    source = FakeSource(sessions=CALENDAR, bars=bars, opens={("NIGHT", DAY1): Decimal("10.10")})

    run_settlement(db, source, DAY1)
    again = run_settlement(db, source, DAY1)

    settlements = db.execute(
        "SELECT COUNT(*) FROM settlements WHERE prediction_id = %s", (prediction_id,)
    ).fetchone()[0]
    events = db.execute(
        "SELECT COUNT(*) FROM prediction_events WHERE prediction_id = %s", (prediction_id,)
    ).fetchone()[0]
    assert settlements == 1
    assert events == 3  # working, filled, settled — once each
    assert again.settled == 0  # nothing left to do


def test_the_worker_is_immune_to_the_callers_row_factory(prediction_id):
    # A dict_row connection (as the API and demo scripts use) must work identically.
    from psycopg.rows import dict_row
    conn = psycopg.connect(APP_DSN, row_factory=dict_row)
    source = FakeSource(sessions=CALENDAR, bars=touch_fill_day1(),
                        opens={("NIGHT", DAY1): Decimal("10.20")})

    run = run_settlement(conn, source, DAY1)

    status = conn.execute(
        "SELECT status FROM predictions WHERE id = %s", (prediction_id,)
    ).fetchone()["status"]
    conn.close()
    assert status == "active"
    assert run.data_mode == "bars"


def test_the_run_is_recorded(db, prediction_id):
    source = FakeSource(sessions=CALENDAR, bars={("NIGHT", DAY1): quiet(DAY1)},
                        opens={("NIGHT", DAY1): Decimal("10.20")})

    run_settlement(db, source, DAY1)

    status, data_mode = db.execute(
        "SELECT status, data_mode FROM settlement_runs WHERE session_date = %s", (DAY1,)
    ).fetchone()
    assert status == "completed"
    assert data_mode == "bars"
