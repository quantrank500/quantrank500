"""M2 deliverable: the full loop on a real past session, zero data cost.

    python scripts/time_machine_demo.py --ticker AAPL --session 2025-06-02 \\
        --entry 200.00 --stop 195.00 --target 212.00

Creates a demo identity, seeds the prediction for that (past) session directly in
the database — the live API cutoff cannot target the past; replaying it is exactly
what the time machine is for — then runs the nightly job for the session and the
two that follow, and prints the identity's resulting record.
"""

import argparse
import uuid
from datetime import date
from decimal import Decimal

import psycopg
from psycopg.rows import dict_row

from quantrank500.commitment import new_nonce
from quantrank500.config import APP_DSN
from quantrank500.db import apply_app_schema
from quantrank500.market_data.local_lake import LocalLakeSource
from quantrank500.worker import run_settlement


def seed_prediction(conn, ticker, session, entry, stop, target) -> uuid.UUID:
    identity = uuid.uuid4()
    conn.execute(
        "INSERT INTO identities (public_id, api_token_hash, display_name)"
        " VALUES (%s, %s, %s)",
        (identity, "0" * 64, f"time-machine-{str(identity)[:8]}"),
    )
    conn.execute(
        "INSERT INTO predictions"
        " (identity_id, ticker, session_date, entry_price, stop_loss, target_price,"
        "  commit_nonce)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (identity, ticker, session, entry, stop, target, new_nonce()),
    )
    conn.commit()
    return identity


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--session", type=date.fromisoformat, required=True)
    parser.add_argument("--entry", type=Decimal, required=True)
    parser.add_argument("--stop", type=Decimal, required=True)
    parser.add_argument("--target", type=Decimal, required=True)
    args = parser.parse_args()

    source = LocalLakeSource()
    calendar = source.calendar()
    if args.session not in calendar:
        raise SystemExit(f"{args.session} is not a trading session in the lake copy")
    hold_window = [d for d in calendar if d >= args.session][:3]

    with psycopg.connect(APP_DSN, row_factory=dict_row) as conn:
        apply_app_schema(conn)
        identity = seed_prediction(
            conn, args.ticker.upper(), args.session, args.entry, args.stop, args.target
        )
        print(f"identity {identity} predicts {args.ticker.upper()} {args.session}:"
              f" entry {args.entry}, stop {args.stop}, target {args.target}\n")

        for night in hold_window:
            run = run_settlement(conn, source, night)
            print(f"  nightly job {night}: {run.settled} settled, {run.unfilled} unfilled")
            status = conn.execute(
                "SELECT status FROM predictions WHERE identity_id = %s", (identity,)
            ).fetchone()["status"]
            print(f"  -> prediction status: {status}")
            if status in ("closed", "unfilled"):
                break

        settlement = conn.execute(
            "SELECT s.* FROM settlements s JOIN predictions p ON p.id = s.prediction_id"
            " WHERE p.identity_id = %s",
            (identity,),
        ).fetchone()
        if settlement:
            print(
                f"\nSettled: {settlement['settled_by']} at {settlement['exit_price']}"
                f" ({settlement['result']}), PnL ${settlement['pnl']} on $500 notional"
            )
    source.close()


if __name__ == "__main__":
    main()
