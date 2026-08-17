"""Wipe the current environment's APP database and recreate an empty schema.

    QR500_PG_DSN=...quantrank500_demo python scripts/reset_env.py

Drops app tables only — the lake (market data) lives in its own schema/database
and is never touched. Refuses to run when QR500_ENV=prod: the real ledger is
INSERT-only and must never be reset.
"""

import psycopg

from quantrank500.config import APP_DSN, ENV, ensure_database, forbid_in_prod
from quantrank500.db import apply_app_schema

APP_TABLES = [
    "settlement_runs", "prediction_events", "settlements",
    "predictions", "webauthn_credentials", "identities",
]


def main() -> None:
    forbid_in_prod("reset the app database")
    ensure_database(APP_DSN)
    with psycopg.connect(APP_DSN) as conn:
        for table in APP_TABLES:
            conn.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE')
        conn.commit()
        apply_app_schema(conn)
    print(f"[{ENV}] app database reset: {APP_DSN.rsplit('/', 1)[-1]}")


if __name__ == "__main__":
    main()
