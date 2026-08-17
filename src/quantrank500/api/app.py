"""The API (spec §8), prototype scope: endpoints 1-5, UUID ledger tier only.

Wiring rules that never bend:
- Timestamps come from the database (NOW()), never the client.
- Parameterized queries only.
- Pre-open, a prediction's payload is withheld (read-path filter, spec §4.1);
  only identity, session, and the commitment hash are visible.

`create_app` takes its collaborators (connection factory, market data source,
clock) so tests drive it with FakeSource and a frozen clock. The default clock
asks Postgres for NOW().
"""

import csv
import hashlib
import io
import os
import re
import secrets
from collections.abc import Callable
from datetime import date, datetime, time
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row
from pydantic import BaseModel

from quantrank500.commitment import (
    canonical_payload,
    canonical_price,
    commitment_hash,
    new_nonce,
)
from quantrank500.config import ENV
from quantrank500.db.events import append_event
from quantrank500.engine import replay_balance
from quantrank500.export import COLUMNS as EXPORT_COLUMNS
from quantrank500.export import ledger_row
from quantrank500.market_data.reference import Reference, floors_reason, make_reference
from quantrank500.market_data.source import MarketDataSource
from quantrank500.sessions import posting_calendar, target_session
from quantrank500.stats import (
    MIN_SETTLED,
    breakeven_rate,
    breakeven_win_rate,
    fill_rate,
    mean,
    pf_lower_bound,
    profit_factor,
    qualifies,
    ticker_concentration,
    win_rate,
)

ET = ZoneInfo("America/New_York")
MARKET_OPEN = time(9, 30)
ENTRY_BAND = Decimal("0.03")  # working default (spec §2)


class PredictionIn(BaseModel):
    ticker: str
    entry_price: Decimal
    stop_loss: Decimal
    target_price: Decimal


class DisplayNameIn(BaseModel):
    display_name: str


# letters, digits, spaces, _ and - only: URLs and markup are impossible by construction
DISPLAY_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _-]{2,31}")


def create_app(
    connect: Callable[[], psycopg.Connection],
    source: MarketDataSource,
    clock: Callable[[], datetime] | None = None,
    cors_origins: list[str] | None = None,
    reference: Callable[[str], Reference | None] | None = None,
) -> FastAPI:
    # the posting universe is two floors any ticker can clear (spec v13.41);
    # tests inject their own reference lookup
    if reference is None:
        reference = _default_reference()
    app = FastAPI(
        title="QuantRank500 API",
        version="0.1.0",
        # deployed behind nginx at /api (proxy strips the prefix); this keeps
        # /docs and openapi.json links correct there. Empty locally.
        root_path=os.environ.get("QR500_ROOT_PATH", ""),
        description=(
            "A record of predictions, not investment advice. "
            "Reads are public; writes require the X-Api-Token issued with an identity. "
            "Payloads of predictions whose session has not opened are withheld "
            "(commit-reveal, spec §4.1)."
        ),
    )
    app.add_middleware(
        CORSMiddleware,
        # production is a strict allow-list of the two frontend origins (spec §10)
        allow_origins=cors_origins or ["http://localhost:8080", "http://127.0.0.1:8080"],
        allow_methods=["GET", "POST"],
        allow_headers=["X-Api-Token", "Content-Type"],
    )

    def db():
        with connect() as conn:
            conn.row_factory = dict_row
            yield conn

    def now(conn: psycopg.Connection) -> datetime:
        if clock is not None:
            return clock()
        return conn.execute("SELECT NOW() AS now").fetchone()["now"]

    def authenticate(
        conn: psycopg.Connection = Depends(db), x_api_token: str = Header(default="")
    ) -> dict:
        token_hash = hashlib.sha256(x_api_token.encode()).hexdigest()
        identity = conn.execute(
            "SELECT public_id FROM identities WHERE api_token_hash = %s", (token_hash,)
        ).fetchone()
        if identity is None:
            raise HTTPException(status_code=401, detail="unknown api token")
        return identity

    @app.get("/", summary="Service card: environment, endpoints, frontend")
    def root():
        return {
            "service": "QuantRank500 API",
            "environment": ENV,
            "frontend": "http://localhost:8080",
            "endpoints": [
                "POST /identities",
                "POST /predictions",
                "GET /predictions/{id}",
                "GET /predictions?identity= | ?session_date=",
                "GET /scoreboard",
                "GET /identities/{public_id}/stats",
            ],
            "note": "A record of predictions, not investment advice.",
        }

    @app.post("/identities", status_code=201,
              summary="Create an anonymous ledger identity (returns the one-time api_token)")
    def create_identity(conn: psycopg.Connection = Depends(db)):
        public_id = uuid4()
        api_token = secrets.token_hex(32)  # 256-bit; shown once, never stored
        conn.execute(
            "INSERT INTO identities (public_id, api_token_hash) VALUES (%s, %s)",
            (public_id, hashlib.sha256(api_token.encode()).hexdigest()),
        )
        conn.commit()
        return {"public_id": str(public_id), "api_token": api_token}

    @app.post("/predictions", status_code=201,
              summary="Submit a prediction; targets the next open session,"
                      " commitment public immediately")
    def post_prediction(
        plan: PredictionIn,
        conn: psycopg.Connection = Depends(db),
        identity: dict = Depends(authenticate),
    ):
        ticker = plan.ticker.strip().upper()
        submitted_at = now(conn)
        try:
            # forward calendar: posting targets tomorrow even when the lake lags
            calendar = posting_calendar(source.calendar(), submitted_at)
            session = target_session(submitted_at, calendar)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        quote = reference(ticker)
        if quote is None:
            raise HTTPException(status_code=422, detail=f"unknown ticker {ticker}")
        floor_problem = floors_reason(quote)
        if floor_problem:
            raise HTTPException(status_code=422, detail=f"{ticker}: {floor_problem}")
        reference_close = quote.close
        if abs(plan.entry_price - reference_close) > reference_close * ENTRY_BAND:
            raise HTTPException(
                status_code=422,
                detail=f"entry must be within 3% of the latest close ({reference_close})",
            )

        nonce = new_nonce()
        try:
            row = conn.execute(
                "INSERT INTO predictions"
                " (identity_id, ticker, session_date, entry_price, stop_loss,"
                "  target_price, commit_nonce)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)"
                " RETURNING id, session_date, created_at",
                (
                    identity["public_id"], ticker, session, plan.entry_price,
                    plan.stop_loss, plan.target_price, nonce,
                ),
            ).fetchone()
        except psycopg.errors.UniqueViolation as exc:
            conn.rollback()
            raise HTTPException(
                status_code=409, detail=f"one prediction per session ({session})"
            ) from exc
        except psycopg.errors.CheckViolation as exc:
            conn.rollback()
            raise HTTPException(status_code=422, detail="invalid plan: " + str(exc)) from exc

        fields = _commitment_fields(
            identity["public_id"], ticker, row["session_date"],
            plan.entry_price, plan.stop_loss, plan.target_price,
        )
        commitment = commitment_hash(canonical_payload(fields), nonce)
        append_event(conn, row["id"], "posted", fields)
        conn.commit()
        return {
            "id": row["id"],
            "session_date": str(row["session_date"]),
            "commitment": commitment,
            "status": "queued",
        }

    @app.post("/identities/display-name",
              summary="Set or change your display name; unverified names always render"
                      " with a UUID suffix")
    def set_display_name(
        body: DisplayNameIn,
        conn: psycopg.Connection = Depends(db),
        identity: dict = Depends(authenticate),
    ):
        name = body.display_name.strip()
        if not DISPLAY_NAME_PATTERN.fullmatch(name):
            raise HTTPException(
                status_code=422,
                detail="3-32 characters: letters, digits, spaces, _ or -",
            )
        conn.execute(
            "UPDATE identities SET display_name = %s WHERE public_id = %s",
            (name, identity["public_id"]),
        )
        conn.commit()
        return {"display_name": name}

    @app.get("/predictions/{prediction_id}",
             summary="One prediction; payload hidden until its session opens at 9:30 ET")
    def get_prediction(prediction_id: int, conn: psycopg.Connection = Depends(db)):
        row = conn.execute(
            "SELECT p.*, i.display_name FROM predictions p"
            " JOIN identities i ON i.public_id = p.identity_id WHERE p.id = %s",
            (prediction_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="no such prediction")
        return _present(row, now(conn))

    @app.get("/predictions",
             summary="List predictions by identity or session_date (same reveal rule)")
    def list_predictions(
        identity: str | None = None,
        session_date: str | None = None,
        conn: psycopg.Connection = Depends(db),
    ):
        current = now(conn)
        if identity is not None:
            rows = conn.execute(
                "SELECT p.*, i.display_name FROM predictions p"
                " JOIN identities i ON i.public_id = p.identity_id"
                " WHERE p.identity_id = %s ORDER BY p.created_at DESC",
                (identity,),
            ).fetchall()
        elif session_date is not None:
            wanted = (
                target_session(current, posting_calendar(source.calendar(), current))
                if session_date == "today"
                else date.fromisoformat(session_date)
            )
            rows = conn.execute(
                "SELECT p.*, i.display_name FROM predictions p"
                " JOIN identities i ON i.public_id = p.identity_id"
                " WHERE p.session_date = %s ORDER BY p.created_at",
                (wanted,),
            ).fetchall()
        else:
            raise HTTPException(status_code=422, detail="identity or session_date required")
        return [_present(row, current) for row in rows]

    @app.get("/identities/{public_id}/export.csv",
             summary="Full record of one identity as CSV (reveal rule applies;"
                     " every revealed row carries commitment + nonce for verification)")
    def export_identity_csv(public_id: str, conn: psycopg.Connection = Depends(db)):
        if conn.execute(
            "SELECT 1 FROM identities WHERE public_id = %s", (public_id,)
        ).fetchone() is None:
            raise HTTPException(status_code=404, detail="no such identity")
        records = conn.execute(
            "SELECT p.*, s.exit_price, s.pnl, s.result, s.settled_by, s.settled_at"
            " FROM predictions p LEFT JOIN settlements s ON s.prediction_id = p.id"
            " WHERE p.identity_id = %s ORDER BY p.session_date, p.id",
            (public_id,),
        ).fetchall()
        current = now(conn)
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=EXPORT_COLUMNS)
        writer.writeheader()
        writer.writerows(ledger_row(record, current) for record in records)
        return Response(
            content=buffer.getvalue(),
            media_type="text/csv",
            headers={
                "Content-Disposition":
                    f'attachment; filename="quantrank500-{public_id[:8]}.csv"'
            },
        )

    @app.get("/identities/{public_id}/stats",
             summary="Public record: lifetime stats, open positions, recent settlements")
    def identity_stats(public_id: str, conn: psycopg.Connection = Depends(db)):
        if conn.execute(
            "SELECT 1 FROM identities WHERE public_id = %s", (public_id,)
        ).fetchone() is None:
            raise HTTPException(status_code=404, detail="no such identity")
        return _identity_stats(conn, public_id)

    @app.get("/scoreboard",
             summary="Lifetime ranking, pending progress, Quarterly 500, data freshness")
    def scoreboard(conn: psycopg.Connection = Depends(db)):
        identity_ids = [
            row["identity_id"]
            for row in conn.execute(
                "SELECT DISTINCT p.identity_id FROM settlements s"
                " JOIN predictions p ON p.id = s.prediction_id"
            ).fetchall()
        ]
        boards = {"ranked": [], "pending": []}
        for identity_id in identity_ids:
            stats = _identity_stats(conn, identity_id)
            board = "ranked" if stats["qualified"] else "pending"
            boards[board].append(stats)
        # rank by lower CI bound of lifetime PF, descending; unproven (no-loss) records last
        boards["ranked"].sort(
            key=lambda s: (s["pf_lower_bound"] is None, -Decimal(s["pf_lower_bound"] or 0))
        )
        return boards | {
            "quarterly": _quarterly_500(conn, now(conn)),
            "freshness": _freshness(conn),
            "survivorship": _survivorship(conn),
        }

    return app


def _survivorship(conn: psycopg.Connection) -> dict:
    """The graveyard, on the record (spec v13.40): how many identities ever
    posted vs how many survived to 33 settlements. The base rate any single
    showcased record should be read against — the Sybil defense is that
    abandoned identities never disappear."""
    row = conn.execute(
        "SELECT"
        " (SELECT COUNT(DISTINCT identity_id) FROM predictions) AS ever_posted,"
        " (SELECT COUNT(*) FROM"
        "   (SELECT p.identity_id FROM settlements s"
        "    JOIN predictions p ON p.id = s.prediction_id"
        "    GROUP BY p.identity_id HAVING COUNT(*) >= %s) survivors) AS reached_33",
        (MIN_SETTLED,),
    ).fetchone()
    return {"ever_posted": row["ever_posted"], "reached_33": row["reached_33"]}


def _identity_stats(conn: psycopg.Connection, identity_id) -> dict:
    settled = conn.execute(
        "SELECT s.result, s.pnl, s.exit_price, p.fill_price, p.ticker,"
        "       p.session_date, s.settled_by,"
        "       p.entry_price, p.stop_loss, p.target_price"
        " FROM settlements s JOIN predictions p ON p.id = s.prediction_id"
        " WHERE p.identity_id = %s ORDER BY s.settled_at",
        (identity_id,),
    ).fetchall()
    open_positions = conn.execute(
        "SELECT ticker, session_date, fill_price FROM predictions"
        " WHERE identity_id = %s AND status = 'active' ORDER BY session_date",
        (identity_id,),
    ).fetchall()
    unfilled = conn.execute(
        "SELECT COUNT(*) AS n FROM predictions"
        " WHERE identity_id = %s AND status = 'unfilled'",
        (identity_id,),
    ).fetchone()["n"]
    display_name = conn.execute(
        "SELECT display_name FROM identities WHERE public_id = %s", (identity_id,)
    ).fetchone()["display_name"]
    decided = [row for row in settled if row["result"] != "unresolved"]
    pnls = [row["pnl"] for row in decided]
    rate = fill_rate(filled=len(settled), unfilled=unfilled)
    pf = profit_factor(pnls)
    balance = replay_balance((row["fill_price"], row["exit_price"]) for row in decided)
    recent = decided[-MIN_SETTLED:]
    return {
        "identity_id": str(identity_id),
        "display_name": display_name,
        "settled": len(settled),
        # progress is a climb toward the 33-count bar; at or past it, an unranked
        # identity is failing a quality bar instead — a fraction would be nonsense
        "progress": f"{len(settled)} of {MIN_SETTLED}" if len(settled) < MIN_SETTLED else None,
        "win_rate": _num(win_rate([row["result"] for row in settled])),
        "profit_factor": _num(pf),
        "pf_lower_bound": _num(pf_lower_bound(pnls)),
        "fill_rate": _num(rate),
        "breakeven_rate": _num(breakeven_rate([row["result"] for row in settled])),
        "breakeven_win_rate": _num(breakeven_win_rate(pnls)),
        # how the record was earned (spec §5): planned stop distance and reward:risk
        "avg_risk_pct": _num(mean(
            [(r["entry_price"] - r["stop_loss"]) / r["entry_price"] for r in decided]
        )),
        "avg_rr": _num(mean(
            [
                (r["target_price"] - r["entry_price"]) / (r["entry_price"] - r["stop_loss"])
                for r in decided
            ]
        )),
        "account_balance": _num(balance.quantize(Decimal("0.01"))),
        "recent_form": {
            "win_rate": _num(win_rate([row["result"] for row in recent])),
            "profit_factor": _num(profit_factor([row["pnl"] for row in recent])),
        },
        "open": [
            {
                "ticker": row["ticker"],
                "session_date": str(row["session_date"]),
                "fill_price": _num(row["fill_price"]),
            }
            for row in open_positions
        ],
        "recent_settlements": [
            {
                "ticker": row["ticker"],
                "session_date": str(row["session_date"]),
                "result": row["result"],
                "settled_by": row["settled_by"],
                "pnl": _num(row["pnl"]),
            }
            for row in reversed(settled[-10:])
        ],
        # Prototype scope (M2-M5): UUID ledger tier only, so the verified criterion
        # is waived until passkeys arrive at M6. All other criteria are enforced.
        "qualified": qualifies(
            verified=True,
            settled=len(settled),
            fill_rate=rate,
            profit_factor=pf,
            max_ticker_share=ticker_concentration([row["ticker"] for row in decided]),
        ),
    }


def _quarterly_500(conn: psycopg.Connection, current: datetime) -> list[dict]:
    """Everyone re-enters at $500 each quarter; min 10 settled in the window."""
    quarter_start = date(current.year, 3 * ((current.month - 1) // 3) + 1, 1)
    rows = conn.execute(
        "SELECT p.identity_id, i.display_name, s.pnl, s.exit_price, p.fill_price, s.result"
        " FROM settlements s JOIN predictions p ON p.id = s.prediction_id"
        " JOIN identities i ON i.public_id = p.identity_id"
        " WHERE s.settled_at >= %s ORDER BY s.settled_at",
        (quarter_start,),
    ).fetchall()
    by_identity: dict = {}
    for row in rows:
        if row["result"] != "unresolved":
            by_identity.setdefault(row["identity_id"], []).append(row)
    table = [
        {
            "identity_id": str(identity_id),
            "display_name": settled[0]["display_name"],
            "settled": len(settled),
            "balance": _num(
                replay_balance(
                    (r["fill_price"], r["exit_price"]) for r in settled
                ).quantize(Decimal("0.01"))
            ),
        }
        for identity_id, settled in by_identity.items()
        if len(settled) >= 10  # working default (spec §5)
    ]
    return sorted(table, key=lambda row: Decimal(row["balance"]), reverse=True)


def _freshness(conn: psycopg.Connection) -> dict | None:
    run = conn.execute(
        "SELECT session_date, completed_at FROM settlement_runs"
        " WHERE status = 'completed' ORDER BY session_date DESC LIMIT 1"
    ).fetchone()
    if run is None:
        return None
    return {
        "data_current_through": str(run["session_date"]),
        "settled_at": run["completed_at"].isoformat(),
    }


def _num(value) -> str | None:
    return None if value is None else str(value)


def _default_reference() -> Callable[[str], Reference | None]:
    """Posting-time reference lookup for real deployments: the lake, plus
    on-demand Databento when a key is configured (unseen or stale tickers)."""
    from quantrank500.config import LAKE_DSN

    fetch = None
    api_key = os.environ.get("DATABENTO_API_KEY")
    if api_key:
        from quantrank500.market_data.databento import databento_fetch

        fetch = databento_fetch(api_key)
    return make_reference(
        lambda: psycopg.connect(LAKE_DSN), fetch,
        today=lambda: datetime.now(ET).date(),
    )


def _commitment_fields(identity_id, ticker, session_date, entry, stop, target) -> dict:
    return {
        "identity_id": str(identity_id),
        "ticker": ticker,
        "session_date": str(session_date),
        "entry_price": canonical_price(entry),
        "stop_loss": canonical_price(stop),
        "target_price": canonical_price(target),
    }


def _recompute_commitment(row: dict) -> str:
    """Derived, never stored: anyone with the revealed payload + nonce gets the same hash."""
    fields = _commitment_fields(
        row["identity_id"], row["ticker"], row["session_date"],
        row["entry_price"], row["stop_loss"], row["target_price"],
    )
    return commitment_hash(canonical_payload(fields), row["commit_nonce"])


def _present(row: dict, current: datetime) -> dict:
    """Read-path filter: before the session opens, the payload stays hidden."""
    opens_at = datetime.combine(row["session_date"], MARKET_OPEN, tzinfo=ET)
    revealed = current.astimezone(ET) >= opens_at
    public = {
        "id": row["id"],
        "identity_id": str(row["identity_id"]),
        "display_name": row.get("display_name"),
        "session_date": str(row["session_date"]),
        "status": row["status"],
        "revealed": revealed,
        "commitment": _recompute_commitment(row),
    }
    if not revealed:
        return public  # content unreadable, provably locked
    return public | {
        "ticker": row["ticker"],
        "entry_price": str(row["entry_price"]),
        "stop_loss": str(row["stop_loss"]),
        "target_price": str(row["target_price"]),
        "fill_price": str(row["fill_price"]) if row["fill_price"] is not None else None,
        "volatile_open_flag": row["volatile_open_flag"],
    }
