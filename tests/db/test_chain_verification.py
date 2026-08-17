"""The hash chain must prove itself: verification walks every event and recomputes.

A tampered payload anywhere in the chain breaks every hash after it.
"""

import uuid
from datetime import date

import pytest

psycopg = pytest.importorskip("psycopg")

from quantrank500.config import APP_DSN  # noqa: E402
from quantrank500.db import apply_app_schema  # noqa: E402
from quantrank500.db.chain import verify_chain  # noqa: E402
from quantrank500.db.events import append_event  # noqa: E402


@pytest.fixture
def db():
    try:
        conn = psycopg.connect(APP_DSN, connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("local Postgres not available")
    apply_app_schema(conn)
    yield conn
    conn.execute(
        "DELETE FROM prediction_events WHERE prediction_id IN"
        " (SELECT id FROM predictions WHERE ticker = 'CHAIN')"
    )
    conn.execute("DELETE FROM predictions WHERE ticker = 'CHAIN'")
    conn.execute("DELETE FROM identities WHERE display_name = 'chain-test'")
    conn.commit()
    conn.close()


@pytest.fixture
def chained(db):
    identity = uuid.uuid4()
    db.execute(
        "INSERT INTO identities (public_id, api_token_hash, display_name)"
        " VALUES (%s, %s, 'chain-test')",
        (identity, "b" * 64),
    )
    prediction_id = db.execute(
        "INSERT INTO predictions"
        " (identity_id, ticker, session_date, entry_price, stop_loss, target_price,"
        "  commit_nonce)"
        " VALUES (%s, 'CHAIN', %s, 10.00, 9.50, 10.90, %s) RETURNING id",
        (identity, date(2025, 6, 2), "c" * 32),
    ).fetchone()[0]
    for event_type, payload in (
        ("posted", {"step": 1}), ("working", {"step": 2}), ("filled", {"step": 3})
    ):
        append_event(db, prediction_id, event_type, payload)
    db.commit()
    return db, prediction_id


def test_an_untampered_chain_verifies(chained):
    db, _ = chained

    report = verify_chain(db)

    assert report.intact is True
    assert report.events_checked >= 3
    assert report.first_broken_event_id is None


def test_a_tampered_payload_is_detected(chained):
    db, prediction_id = chained
    # An attacker with database access edits one payload in place.
    tampered_id = db.execute(
        "UPDATE prediction_events SET payload = '{\"step\":99}'::jsonb"
        " WHERE prediction_id = %s AND event_type = 'working' RETURNING id",
        (prediction_id,),
    ).fetchone()[0]
    db.commit()

    report = verify_chain(db)

    assert report.intact is False
    assert report.first_broken_event_id == tampered_id
