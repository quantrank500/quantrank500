"""Run the nightly settlement replay for one session — the time machine (spec §11 M2).

    python scripts/nightly_job.py --session-date 2025-06-03

Any past session in the lake copy can be replayed; re-runs are idempotent.
"""

import argparse
from datetime import date

import psycopg

from quantrank500.config import APP_DSN
from quantrank500.db import apply_app_schema
from quantrank500.market_data.local_lake import LocalLakeSource
from quantrank500.worker import run_settlement


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-date", type=date.fromisoformat, required=True)
    parser.add_argument("--pg-dsn", default=APP_DSN)
    args = parser.parse_args()

    source = LocalLakeSource(args.pg_dsn)
    with psycopg.connect(args.pg_dsn) as conn:
        apply_app_schema(conn)
        run = run_settlement(conn, source, args.session_date)
    source.close()
    print(
        f"settlement run {run.session_date} [{run.data_mode}]: "
        f"{run.settled} settled, {run.unfilled} unfilled"
    )


if __name__ == "__main__":
    main()
