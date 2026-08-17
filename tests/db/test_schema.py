"""The schema's structural constraints must reject bad rows at the database layer."""

import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

from quantrank500.config import APP_DSN  # noqa: E402
from quantrank500.db import apply_app_schema  # noqa: E402


@pytest.fixture(scope="module")
def db():
    try:
        conn = psycopg.connect(APP_DSN, connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("local Postgres not available")
    apply_app_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def identity(db):
    public_id = uuid.uuid4()
    db.execute(
        "INSERT INTO identities (public_id, api_token_hash) VALUES (%s, %s)",
        (public_id, "0" * 64),
    )
    db.commit()
    yield public_id
    db.execute("DELETE FROM predictions WHERE identity_id = %s", (public_id,))
    db.execute("DELETE FROM identities WHERE public_id = %s", (public_id,))
    db.commit()


def insert_prediction(db, identity, entry: str, stop: str, target: str, session="2025-06-02"):
    db.execute(
        "INSERT INTO predictions"
        " (identity_id, ticker, session_date, entry_price, stop_loss, target_price, commit_nonce)"
        " VALUES (%s, 'ACME', %s, %s, %s, %s, %s)",
        (identity, session, entry, stop, target, "a" * 32),
    )
    db.commit()


def test_applying_the_schema_twice_is_idempotent(db):
    apply_app_schema(db)


def test_a_valid_plan_is_accepted(db, identity):
    insert_prediction(db, identity, entry="10.00", stop="9.50", target="10.90")


def test_target_below_minimum_distance_is_rejected(db, identity):
    with pytest.raises(psycopg.errors.CheckViolation):
        insert_prediction(db, identity, entry="10.00", stop="9.50", target="10.80")
    db.rollback()


def test_stop_at_or_above_entry_is_rejected(db, identity):
    with pytest.raises(psycopg.errors.CheckViolation):
        insert_prediction(db, identity, entry="10.00", stop="10.00", target="10.60")
    db.rollback()


def test_second_prediction_for_the_same_session_is_rejected(db, identity):
    insert_prediction(db, identity, entry="10.00", stop="9.50", target="10.90")
    with pytest.raises(psycopg.errors.UniqueViolation):
        insert_prediction(db, identity, entry="11.00", stop="10.50", target="11.95")
    db.rollback()
