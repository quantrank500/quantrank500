"""Environment configuration.

Environments differ only in the APP database (identities, predictions,
settlements, events, runs). The LAKE (read-only market data) is shared by
dev/test/demo and is never mounted in prod (spec §6: dev/test only).

    QR500_ENV       dev (default) | test | demo | prod
    QR500_PG_DSN    the app database
    QR500_LAKE_DSN  the market-data lake (defaults to the dev database)
"""

import os

import psycopg

ENV = os.environ.get("QR500_ENV", "dev")

APP_DSN = os.environ.get(
    "QR500_PG_DSN", "postgresql://quantrank:quantrank@localhost:5432/quantrank500"
)
LAKE_DSN = os.environ.get(
    "QR500_LAKE_DSN", "postgresql://quantrank:quantrank@localhost:5432/quantrank500"
)


def forbid_in_prod(action: str) -> None:
    """The ledger is INSERT-only; simulated or destructive operations must never
    touch production. Every seeding/reset script calls this first."""
    if ENV == "prod":
        raise SystemExit(f"refusing to {action}: QR500_ENV=prod")


def ensure_database(dsn: str) -> None:
    """Create the database named in `dsn` if it does not exist yet."""
    try:
        psycopg.connect(dsn, connect_timeout=3).close()
        return
    except psycopg.OperationalError as error:
        if "does not exist" not in str(error):
            raise
    parts = psycopg.conninfo.conninfo_to_dict(dsn)
    dbname = parts.pop("dbname")
    with psycopg.connect(**parts, dbname="postgres", autocommit=True) as maintenance:
        maintenance.execute(f'CREATE DATABASE "{dbname}"')
