"""The nightly settlement replay (spec §4.3) — one batch job after the close.

Deterministic and idempotent: `run_settlement(conn, source, session_date)` replays
one session and can re-run any past session with identical results (the time
machine). Bar mode only until M6.

Per session:
1. Queued predictions for the session become working limit orders and either
   fill (active) or expire unfilled. Both are recorded as events.
2. Active positions are settled against the session's bars; expiry is the third
   session after the fill. Settled positions close with an INSERT-only
   settlements row. A halted/dataless expiry stays open for a later run.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import tuple_row

from quantrank500.db.events import append_event
from quantrank500.engine import (
    Fill,
    Position,
    fill_bar_mode,
    settle_session_bar_mode,
    standardized_pnl,
    volatile_open_flag,
)
from quantrank500.market_data.source import MarketDataSource

ET = ZoneInfo("America/New_York")
MAX_HOLD_SESSIONS = 3  # structural rule (spec §2)


@dataclass(frozen=True)
class RunSummary:
    session_date: date
    settled: int
    unfilled: int
    data_mode: str


def run_settlement(
    conn: psycopg.Connection, source: MarketDataSource, session_date: date
) -> RunSummary:
    calendar = source.calendar()
    if session_date not in calendar:
        raise ValueError(f"{session_date} is not a trading session in this source")

    _record_run_started(conn, session_date)
    unfilled = _work_the_queue(conn, source, session_date, calendar)
    settled = _settle_open_positions(conn, source, session_date, calendar)
    _record_run_completed(conn, session_date, settled, unfilled)
    conn.commit()
    return RunSummary(session_date, settled=settled, unfilled=unfilled, data_mode="bars")


def catch_up(
    conn: psycopg.Connection, source: MarketDataSource, today: date
) -> list[RunSummary]:
    """Replay every session that still needs a run, oldest first (spec §10:
    missed nights self-heal). A session needs a run when a queued or active
    prediction exists at or before it and no completed run covers it."""
    rows = conn.cursor(row_factory=tuple_row)  # immune to the caller's row_factory
    pending_from = rows.execute(
        "SELECT MIN(session_date) FROM predictions WHERE status IN ('queued', 'active')"
    ).fetchone()[0]
    if pending_from is None:
        return []
    already_done = {
        row[0]
        for row in rows.execute(
            "SELECT session_date FROM settlement_runs WHERE status = 'completed'"
        ).fetchall()
    }
    needed = [
        session
        for session in source.calendar()
        if pending_from <= session <= today and session not in already_done
    ]
    return [run_settlement(conn, source, session) for session in needed]


def _work_the_queue(conn, source, session_date, calendar) -> int:
    """Queued predictions become working limit orders; fill or go unfilled."""
    queued = conn.cursor(row_factory=tuple_row).execute(
        "SELECT id, ticker, entry_price FROM predictions"
        " WHERE status = 'queued' AND session_date = %s ORDER BY id",
        (session_date,),
    ).fetchall()
    unfilled_count = 0
    for prediction_id, ticker, entry in queued:
        append_event(conn, prediction_id, "working", {})
        bars = source.session_bars(ticker, session_date)
        fill = fill_bar_mode(entry, bars, source.official_open(ticker, session_date))
        if fill is None:
            conn.execute(
                "UPDATE predictions SET status = 'unfilled' WHERE id = %s", (prediction_id,)
            )
            append_event(conn, prediction_id, "unfilled", {})
            unfilled_count += 1
            continue
        flagged = volatile_open_flag(bars, _prior_close(source, ticker, session_date, calendar))
        conn.execute(
            "UPDATE predictions SET status = 'active', fill_price = %s, fill_time = %s,"
            " volatile_open_flag = %s WHERE id = %s",
            (fill.price, fill.ts, flagged, prediction_id),
        )
        append_event(
            conn, prediction_id, "filled",
            {"fill_price": str(fill.price), "fill_time": fill.ts.isoformat()},
        )
    return unfilled_count


def _settle_open_positions(conn, source, session_date, calendar) -> int:
    active = conn.cursor(row_factory=tuple_row).execute(
        "SELECT id, ticker, entry_price, stop_loss, target_price, fill_price, fill_time"
        " FROM predictions WHERE status = 'active' ORDER BY id"
    ).fetchall()
    settled_count = 0
    for prediction_id, ticker, entry, stop, target, fill_price, fill_time in active:
        fill_session = fill_time.astimezone(ET).date()
        sessions_held = calendar.index(session_date) - calendar.index(fill_session)
        if sessions_held < 0:
            continue  # filled after this (replayed) session; not ours to settle
        position = Position(
            # a gap-improvement fill is the only way fill_price differs from entry
            fill=Fill(price=fill_price, ts=fill_time, at_open=fill_price != entry),
            stop=stop,
            target=target,
        )
        bars = source.session_bars(ticker, session_date)
        settlement = settle_session_bar_mode(
            position, bars, expiry=sessions_held >= MAX_HOLD_SESSIONS - 1
        )
        if settlement is None:
            continue  # still open — or an expiry with no data, left for a later run
        _record_settlement(conn, prediction_id, fill_price, settlement, bars)
        settled_count += 1
    return settled_count


def _record_settlement(conn, prediction_id, fill_price, settlement, bars) -> None:
    settling_bar = next((b for b in bars if b.ts == settlement.ts), None)
    pnl = standardized_pnl(fill_price, settlement.exit_price).quantize(Decimal("0.0001"))
    conn.execute(
        "INSERT INTO settlements"
        " (prediction_id, exit_price, pnl, result, settled_by, settled_at,"
        "  settled_bar_open, settled_bar_high, settled_bar_low, settled_bar_close)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            prediction_id, settlement.exit_price, pnl,
            settlement.result.value, settlement.settled_by.value, settlement.ts,
            settling_bar.open if settling_bar else None,
            settling_bar.high if settling_bar else None,
            settling_bar.low if settling_bar else None,
            settling_bar.close if settling_bar else None,
        ),
    )
    conn.execute("UPDATE predictions SET status = 'closed' WHERE id = %s", (prediction_id,))
    append_event(
        conn, prediction_id, "settled",
        {
            "exit_price": str(settlement.exit_price),
            "result": settlement.result.value,
            "settled_by": settlement.settled_by.value,
            "settled_at": settlement.ts.isoformat(),
        },
    )


def _prior_close(source, ticker, session_date, calendar) -> Decimal:
    index = calendar.index(session_date)
    if index == 0:
        return Decimal("Infinity")  # no prior session -> flag can never trip
    return source.official_close(ticker, calendar[index - 1]) or Decimal("Infinity")


def _record_run_started(conn, session_date) -> None:
    conn.execute(
        "INSERT INTO settlement_runs (session_date, started_at, status, data_mode)"
        " VALUES (%s, NOW(), 'running', 'bars')"
        " ON CONFLICT (session_date) DO UPDATE SET started_at = NOW(), status = 'running'",
        (session_date,),
    )


def _record_run_completed(conn, session_date, settled: int, unfilled: int) -> None:
    conn.execute(
        "UPDATE settlement_runs SET status = 'completed', completed_at = NOW(),"
        " predictions_settled = %s, predictions_unfilled = %s WHERE session_date = %s",
        (settled, unfilled, session_date),
    )
