"""Verify the hash-chained event log end to end (spec §11 M5).

    python scripts/verify_ledger.py
"""

import psycopg

from quantrank500.config import APP_DSN
from quantrank500.db.chain import verify_chain


def main() -> None:
    with psycopg.connect(APP_DSN) as conn:
        report = verify_chain(conn)
    if report.intact:
        print(f"chain intact: {report.events_checked} events verified")
    else:
        raise SystemExit(
            f"CHAIN BROKEN at event id {report.first_broken_event_id}"
            f" ({report.events_checked} events walked)"
        )


if __name__ == "__main__":
    main()
