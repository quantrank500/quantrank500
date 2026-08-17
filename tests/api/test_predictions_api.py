"""API integration tests: real Postgres, FakeSource market data, frozen clock.

Frozen at Monday 2025-06-02 19:00 ET -> submissions target Tuesday 2025-06-03.
"""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

psycopg = pytest.importorskip("psycopg")
from fastapi.testclient import TestClient  # noqa: E402

from quantrank500.api import create_app  # noqa: E402
from quantrank500.config import APP_DSN  # noqa: E402
from quantrank500.db import apply_app_schema  # noqa: E402
from quantrank500.market_data import FakeSource  # noqa: E402
from quantrank500.market_data.reference import Reference  # noqa: E402

ET = ZoneInfo("America/New_York")
MONDAY, TUESDAY = date(2025, 6, 2), date(2025, 6, 3)
CALENDAR = [date(2025, 5, 30), MONDAY, TUESDAY, date(2025, 6, 4), date(2025, 6, 5)]

frozen_now = datetime(2025, 6, 2, 19, 0, tzinfo=ET)  # Monday evening, before cutoff


def fake_source() -> FakeSource:
    return FakeSource(
        sessions=CALENDAR,
        closes={("ACME", MONDAY): Decimal("10.00"), ("ACME", date(2025, 5, 30)): Decimal("9.90")},
    )


def fake_reference(ticker: str) -> Reference | None:
    """ACME clears the floors; PENNY and THIN exist but fail them."""
    quotes = {
        "ACME": Reference(session=MONDAY, close=Decimal("10.00"), volume=1_000_000),
        "PENNY": Reference(session=MONDAY, close=Decimal("2.50"), volume=9_000_000),
        "THIN": Reference(session=MONDAY, close=Decimal("10.00"), volume=50_000),
    }
    return quotes.get(ticker)


@pytest.fixture(scope="module")
def client():
    try:
        conn = psycopg.connect(APP_DSN, connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("local Postgres not available")
    apply_app_schema(conn)
    conn.close()

    app = create_app(
        connect=lambda: psycopg.connect(APP_DSN),
        source=fake_source(),
        clock=lambda: frozen_now,
        reference=fake_reference,
    )
    yield TestClient(app)

    cleanup = psycopg.connect(APP_DSN)
    cleanup.execute(
        "DELETE FROM prediction_events WHERE prediction_id IN"
        " (SELECT id FROM predictions WHERE ticker = 'ACME')"
    )
    cleanup.execute("DELETE FROM predictions WHERE ticker = 'ACME'")
    cleanup.execute(
        "DELETE FROM identities WHERE display_name IS NULL AND verified_at IS NULL"
        " AND public_id NOT IN (SELECT DISTINCT identity_id FROM predictions)"
    )
    cleanup.commit()
    cleanup.close()


@pytest.fixture
def identity(client):
    created = client.post("/identities").json()
    return created  # {"public_id": ..., "api_token": ...}


def post_plan(client, token: str, **overrides):
    plan = {
        "ticker": "ACME",
        "entry_price": "10.00",
        "stop_loss": "9.50",
        "target_price": "10.90",
    } | overrides
    return client.post("/predictions", json=plan, headers={"X-Api-Token": token})


class TestIdentities:
    def test_creating_an_identity_returns_uuid_and_one_time_token(self, client):
        response = client.post("/identities")

        assert response.status_code == 201
        body = response.json()
        assert len(body["api_token"]) == 64  # 256-bit hex
        assert body["public_id"]


class TestDisplayName:
    def test_a_valid_name_is_saved_and_appears_in_stats(self, client, identity):
        response = client.post(
            "/identities/display-name",
            json={"display_name": "  Sharp Trader_1 "},
            headers={"X-Api-Token": identity["api_token"]},
        )

        assert response.status_code == 200
        assert response.json()["display_name"] == "Sharp Trader_1"  # trimmed
        stats = client.get(f"/identities/{identity['public_id']}/stats").json()
        assert stats["display_name"] == "Sharp Trader_1"

    def test_urls_and_markup_are_impossible_by_charset(self, client, identity):
        for bad in ("http://spam.io", "<b>hi</b>", "a", "x" * 33, "dots.not.allowed"):
            response = client.post(
                "/identities/display-name",
                json={"display_name": bad},
                headers={"X-Api-Token": identity["api_token"]},
            )
            assert response.status_code == 422, bad

    def test_setting_a_name_requires_the_token(self, client):
        response = client.post(
            "/identities/display-name", json={"display_name": "nobody"}
        )

        assert response.status_code == 401


class TestPostPrediction:
    def test_valid_plan_is_queued_for_tuesday_with_a_commitment(self, client, identity):
        response = post_plan(client, identity["api_token"])

        assert response.status_code == 201
        body = response.json()
        assert body["session_date"] == "2025-06-03"  # Monday 7 PM -> Tuesday
        assert body["status"] == "queued"
        assert len(body["commitment"]) == 64

    def test_unknown_token_is_rejected(self, client):
        response = post_plan(client, "not-a-token")

        assert response.status_code == 401

    def test_unknown_ticker_is_rejected(self, client, identity):
        response = post_plan(client, identity["api_token"], ticker="ZZZZ")

        assert response.status_code == 422

    def test_sub_3_dollar_stock_fails_the_price_floor(self, client, identity):
        response = post_plan(client, identity["api_token"], ticker="PENNY",
                             entry_price="2.50", stop_loss="2.30", target_price="2.80")

        assert response.status_code == 422
        assert "price below" in response.json()["detail"]

    def test_illiquid_stock_fails_the_dollar_volume_floor(self, client, identity):
        response = post_plan(client, identity["api_token"], ticker="THIN")

        assert response.status_code == 422
        assert "dollar volume" in response.json()["detail"]

    def test_entry_outside_the_3pct_band_is_rejected(self, client, identity):
        response = post_plan(
            client, identity["api_token"],
            entry_price="10.31", stop_loss="9.80", target_price="11.20",
        )

        assert response.status_code == 422

    def test_target_below_minimum_distance_is_rejected(self, client, identity):
        response = post_plan(client, identity["api_token"], target_price="10.80")

        assert response.status_code == 422

    def test_second_prediction_for_the_same_session_is_rejected(self, client, identity):
        assert post_plan(client, identity["api_token"]).status_code == 201

        response = post_plan(client, identity["api_token"], entry_price="9.90")

        assert response.status_code == 409


class TestReadPathFilter:
    def test_before_the_open_only_the_commitment_is_visible(self, client, identity):
        posted = post_plan(client, identity["api_token"]).json()

        shown = client.get(f"/predictions/{posted['id']}").json()

        assert shown["revealed"] is False
        assert shown["commitment"] == posted["commitment"]
        assert "ticker" not in shown
        assert "entry_price" not in shown

    def test_after_the_open_the_payload_is_revealed_and_commitment_matches(
        self, client, identity
    ):
        posted = post_plan(client, identity["api_token"]).json()  # targets Tuesday

        tuesday_open = datetime(2025, 6, 3, 9, 30, tzinfo=ET)
        after_open = TestClient(
            create_app(
                connect=lambda: psycopg.connect(APP_DSN),
                source=fake_source(),
                clock=lambda: tuesday_open,
                reference=fake_reference,
            )
        )
        shown = after_open.get(f"/predictions/{posted['id']}").json()

        assert shown["revealed"] is True
        assert shown["ticker"] == "ACME"
        assert shown["entry_price"] == "10.0000"
        # recomputed from stored payload + nonce; must equal the pre-open commitment
        assert shown["commitment"] == posted["commitment"]

    def test_listing_by_session_date(self, client, identity):
        posted = post_plan(client, identity["api_token"]).json()

        listed = client.get("/predictions", params={"session_date": "2025-06-03"}).json()

        assert any(r["id"] == posted["id"] for r in listed)
