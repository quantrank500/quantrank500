"""Run the API on localhost against the local Postgres + lake copy.

    python scripts/run_api.py            # http://localhost:8000
Frontend:
    python -m http.server 8080 -d frontend
"""

import os

import psycopg
import uvicorn

from quantrank500.api import create_app
from quantrank500.config import APP_DSN, ensure_database
from quantrank500.db import apply_app_schema
from quantrank500.market_data.local_lake import LocalLakeSource


def main() -> None:
    ensure_database(APP_DSN)
    with psycopg.connect(APP_DSN) as conn:
        apply_app_schema(conn)
    app = create_app(connect=lambda: psycopg.connect(APP_DSN), source=LocalLakeSource())
    uvicorn.run(app, host=os.environ.get("QR500_API_HOST", "127.0.0.1"), port=8000)


if __name__ == "__main__":
    main()
