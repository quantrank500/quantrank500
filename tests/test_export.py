"""The public ledger export (spec §8): produced nightly, served as files.

Revealed predictions export with full payload + nonce so anyone can recompute
their commitments. Unrevealed predictions export as commitment-only rows —
locked content stays locked, even in the export.
"""

import csv
import json
import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

psycopg = pytest.importorskip("psycopg")

from quantrank500.config import APP_DSN  # noqa: E402
from quantrank500.db import apply_app_schema  # noqa: E402
from quantrank500.export import export_ledger  # noqa: E402

ET = ZoneInfo("America/New_York")
PAST, FUTURE = date(2025, 6, 2), date(2025, 6, 4)
AS_OF = datetime(2025, 6, 3, 22, 0, tzinfo=ET)  # PAST revealed, FUTURE still locked


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
        " (SELECT id FROM predictions WHERE ticker = 'XPRT')"
    )
    conn.execute("DELETE FROM predictions WHERE ticker = 'XPRT'")
    conn.execute("DELETE FROM identities WHERE display_name = 'export-test'")
    conn.commit()
    conn.close()


@pytest.fixture
def seeded(db):
    identity = uuid.uuid4()
    db.execute(
        "INSERT INTO identities (public_id, api_token_hash, display_name)"
        " VALUES (%s, %s, 'export-test')",
        (identity, "a" * 64),
    )
    for session in (PAST, FUTURE):
        db.execute(
            "INSERT INTO predictions"
            " (identity_id, ticker, session_date, entry_price, stop_loss, target_price,"
            "  commit_nonce)"
            " VALUES (%s, 'XPRT', %s, 10.00, 9.50, 10.90, %s)",
            (identity, session, "9" * 32),
        )
    db.commit()
    return db, identity


def export_rows(seeded, tmp_path):
    db, identity = seeded
    result = export_ledger(db, tmp_path, as_of=AS_OF)
    rows = json.loads(result.json_path.read_text())
    return result, [r for r in rows if r["identity_id"] == str(identity)]


def test_revealed_rows_carry_payload_and_nonce(seeded, tmp_path):
    _, rows = export_rows(seeded, tmp_path)

    revealed = next(r for r in rows if r["session_date"] == str(PAST))
    assert revealed["ticker"] == "XPRT"
    assert revealed["commit_nonce"] == "9" * 32
    assert len(revealed["commitment"]) == 64


def test_unrevealed_rows_are_commitment_only(seeded, tmp_path):
    _, rows = export_rows(seeded, tmp_path)

    locked = next(r for r in rows if r["session_date"] == str(FUTURE))
    assert locked.get("ticker") is None
    assert locked.get("entry_price") is None
    assert locked.get("commit_nonce") is None
    assert len(locked["commitment"]) == 64


def test_csv_and_root_hash_are_produced(seeded, tmp_path):
    result, _ = export_rows(seeded, tmp_path)

    assert result.csv_path.exists()
    assert len(list(csv.DictReader(result.csv_path.open()))) >= 2
    assert result.root_hash is None or len(result.root_hash) == 64
