"""Scoreboard and stats endpoints: derived entirely from seeded settlements."""

import itertools
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

psycopg = pytest.importorskip("psycopg")
from fastapi.testclient import TestClient  # noqa: E402

from quantrank500.api import create_app  # noqa: E402
from quantrank500.config import APP_DSN  # noqa: E402
from quantrank500.db import apply_app_schema  # noqa: E402
from quantrank500.market_data import FakeSource  # noqa: E402

ET = ZoneInfo("America/New_York")
FROZEN_NOW = datetime(2025, 6, 30, 18, 0, tzinfo=ET)  # end of Q2
TICKERS = ["SBAA", "SBBB", "SBCC", "SBDD"]  # rotation keeps every share under 30%


def seed_identity(conn, name: str, outcomes: list[tuple[str, str]]):
    """Each outcome is (result, exit_price) for a 10.00 fill; sessions run backward
    from late June so everything lands in Q2 2025."""
    identity = uuid.uuid4()
    conn.execute(
        "INSERT INTO identities (public_id, api_token_hash, display_name)"
        " VALUES (%s, %s, %s)",
        (identity, "e" * 64, name),
    )
    session = date(2025, 6, 27)
    for (result, exit_price), ticker in zip(outcomes, itertools.cycle(TICKERS)):
        prediction_id = conn.execute(
            "INSERT INTO predictions"
            " (identity_id, ticker, session_date, entry_price, stop_loss, target_price,"
            "  commit_nonce, status, fill_price, fill_time)"
            " VALUES (%s, %s, %s, 10.00, 9.50, 10.90, %s, 'closed', 10.00, %s)"
            " RETURNING id",
            (identity, ticker, session, "c" * 32,
             datetime.combine(session, datetime.min.time(), tzinfo=ET)),
        ).fetchone()[0]
        pnl = (Decimal(exit_price) - 10) * 50  # 500 x (exit-10)/10
        conn.execute(
            "INSERT INTO settlements"
            " (prediction_id, exit_price, pnl, result, settled_by, settled_at)"
            " VALUES (%s, %s, %s, %s, 'target', %s)",
            (prediction_id, exit_price, pnl, result,
             datetime.combine(session, datetime.min.time(), tzinfo=ET)),
        )
        session -= timedelta(days=1)
        if session.weekday() >= 5:
            session -= timedelta(days=session.weekday() - 4)
    conn.commit()
    return identity


@pytest.fixture(scope="module")
def seeded():
    try:
        conn = psycopg.connect(APP_DSN, connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("local Postgres not available")
    apply_app_schema(conn)

    # 22 wins at 10.60, 11 losses at 9.50: PF well above 1.25, fill rate 1.0
    strong = [("win", "10.60")] * 22 + [("loss", "9.50")] * 11
    qualified = seed_identity(conn, "scoreboard-strong", strong)
    pending = seed_identity(conn, "scoreboard-new", [("win", "10.60")] * 5)
    # plenty of settled but PF ~0: unranked for quality, not for count
    flat = seed_identity(conn, "scoreboard-flat", [("loss", "9.50")] * 40)

    yield {"qualified": qualified, "pending": pending, "flat": flat, "conn": conn}

    for name in ("scoreboard-strong", "scoreboard-new", "scoreboard-flat"):
        conn.execute(
            "DELETE FROM settlements WHERE prediction_id IN"
            " (SELECT p.id FROM predictions p JOIN identities i"
            "  ON i.public_id = p.identity_id WHERE i.display_name = %s)", (name,))
        conn.execute(
            "DELETE FROM prediction_events WHERE prediction_id IN"
            " (SELECT p.id FROM predictions p JOIN identities i"
            "  ON i.public_id = p.identity_id WHERE i.display_name = %s)", (name,))
        conn.execute(
            "DELETE FROM predictions WHERE identity_id IN"
            " (SELECT public_id FROM identities WHERE display_name = %s)", (name,))
        conn.execute("DELETE FROM identities WHERE display_name = %s", (name,))
    conn.commit()
    conn.close()


@pytest.fixture(scope="module")
def client(seeded):
    app = create_app(
        connect=lambda: psycopg.connect(APP_DSN),
        source=FakeSource(),
        clock=lambda: FROZEN_NOW,
        reference=lambda ticker: None,  # this suite never posts
    )
    return TestClient(app)


def test_a_qualified_record_is_ranked(client, seeded):
    board = client.get("/scoreboard").json()

    ranked_ids = [row["identity_id"] for row in board["ranked"]]
    assert str(seeded["qualified"]) in ranked_ids
    winner = next(r for r in board["ranked"] if r["identity_id"] == str(seeded["qualified"]))
    assert winner["settled"] == 33
    assert Decimal(winner["profit_factor"]) > Decimal("1.25")
    assert winner["pf_lower_bound"] is not None


def test_a_short_record_is_pending_with_progress(client, seeded):
    board = client.get("/scoreboard").json()

    pending = next(
        r for r in board["pending"] if r["identity_id"] == str(seeded["pending"])
    )
    assert pending["progress"] == "5 of 33"
    assert pending["qualified"] is False


def test_survivorship_counts_every_identity_that_ever_posted(client, seeded):
    board = client.get("/scoreboard").json()

    survivorship = board["survivorship"]
    # the graveyard is on the record: posted >= survived, and the seeded
    # 33-settled identity is a survivor
    assert survivorship["ever_posted"] >= 2
    assert 1 <= survivorship["reached_33"] <= survivorship["ever_posted"]


def test_quarterly_500_requires_ten_settled_and_restarts_at_500(client, seeded):
    board = client.get("/scoreboard").json()

    quarterly_ids = [row["identity_id"] for row in board["quarterly"]]
    assert str(seeded["qualified"]) in quarterly_ids   # 33 settled this quarter
    assert str(seeded["pending"]) not in quarterly_ids  # only 5 settled
    leader = next(r for r in board["quarterly"] if r["identity_id"] == str(seeded["qualified"]))
    assert Decimal(leader["balance"]) != Decimal("500")


def test_enough_settled_but_failing_quality_shows_no_progress_fraction(client, seeded):
    # "68 of 33" must never render: past the count bar, progress is meaningless.
    board = client.get("/scoreboard").json()

    flat = next(r for r in board["pending"] if r["identity_id"] == str(seeded["flat"]))
    assert flat["settled"] == 40
    assert flat["progress"] is None
    assert flat["qualified"] is False


def test_identity_stats_reports_the_500_account(client, seeded):
    stats = client.get(f"/identities/{seeded['qualified']}/stats").json()

    assert stats["settled"] == 33
    assert stats["fill_rate"] == "1"
    assert Decimal(stats["account_balance"]) > Decimal("500")  # 2:1 wins at 6% vs 5%
    assert stats["recent_form"]["win_rate"] is not None


def test_unknown_identity_is_404(client):
    assert client.get(f"/identities/{uuid.uuid4()}/stats").status_code == 404


class TestCsvExport:
    def test_full_record_downloads_as_csv_with_verification_columns(self, client, seeded):
        import csv as csvlib
        import io

        response = client.get(f"/identities/{seeded['qualified']}/export.csv")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert "attachment" in response.headers["content-disposition"]
        rows = list(csvlib.DictReader(io.StringIO(response.text)))
        assert len(rows) == 33
        settled = rows[0]
        assert settled["ticker"] in TICKERS
        assert settled["result"] in ("win", "loss")
        assert settled["pnl"] != ""
        assert len(settled["commitment"]) == 64
        assert len(settled["commit_nonce"]) == 32  # revealed: verifiable by anyone

    def test_unrevealed_predictions_export_commitment_only(self, client, seeded):
        import csv as csvlib
        import io

        conn = seeded["conn"]
        conn.execute(
            "INSERT INTO predictions"
            " (identity_id, ticker, session_date, entry_price, stop_loss, target_price,"
            "  commit_nonce)"
            " VALUES (%s, 'SBAA', %s, 10.00, 9.50, 10.90, %s)",
            (seeded["pending"], date(2025, 7, 1), "d" * 32),  # after FROZEN_NOW: locked
        )
        conn.commit()

        response = client.get(f"/identities/{seeded['pending']}/export.csv")

        rows = list(csvlib.DictReader(io.StringIO(response.text)))
        locked = next(r for r in rows if r["session_date"] == "2025-07-01")
        assert locked["ticker"] == ""
        assert locked["entry_price"] == ""
        assert locked["commit_nonce"] == ""  # the nonce would make it brute-forceable
        assert len(locked["commitment"]) == 64

    def test_export_of_unknown_identity_is_404(self, client):
        assert client.get(f"/identities/{uuid.uuid4()}/export.csv").status_code == 404
