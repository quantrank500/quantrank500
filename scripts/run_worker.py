"""The worker: one settlement run after each close, missed nights self-heal.

Runs catch_up() once at startup (the PC may have been off), then every weekday
at 5:00 PM ET. catch_up is a no-op when there is nothing to settle, so holidays
and duplicate runs cost nothing.

With QR500_INGEST=databento (demo/prod VMs), each run first pulls any sessions
pending predictions need into the lake. EQUS.MINI publishes a session's history
after ~midnight ET, so the 8:00 AM run usually lands it; an ingest failure only
delays settlement — never blocks settling from data already in the lake.
"""

import logging
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from quantrank500.config import APP_DSN, LAKE_DSN, ensure_database
from quantrank500.db import apply_app_schema
from quantrank500.export import export_ledger
from quantrank500.market_data.local_lake import LocalLakeSource
from quantrank500.worker import catch_up

EXPORT_DIR = Path(os.environ.get("QR500_EXPORT_DIR", "exports"))
INGEST = os.environ.get("QR500_INGEST", "off")

ET = ZoneInfo("America/New_York")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("worker")


def pull_market_data() -> None:
    from quantrank500.market_data.databento import databento_fetch, ingest_pending

    fetch = databento_fetch(os.environ["DATABENTO_API_KEY"])
    with psycopg.connect(APP_DSN) as app_conn, psycopg.connect(LAKE_DSN) as lake_conn:
        dailies, minutes = ingest_pending(
            app_conn, lake_conn, fetch, today=datetime.now(ET).date()
        )
    log.info("ingest: %s daily rows, %s minute sessions", dailies, minutes)


def settle_everything_pending() -> None:
    if INGEST == "databento":
        try:
            pull_market_data()
        except Exception:
            log.exception("ingest failed; settling from data already in the lake")
    source = LocalLakeSource()
    with psycopg.connect(APP_DSN) as conn:
        runs = catch_up(conn, source, today=datetime.now(ET).date())
        export = export_ledger(conn, EXPORT_DIR, as_of=datetime.now(ET))
    source.close()
    if not runs:
        log.info("nothing to settle")
    for run in runs:
        log.info(
            "settled %s [%s]: %s settled, %s unfilled",
            run.session_date, run.data_mode, run.settled, run.unfilled,
        )
    log.info("ledger exported to %s (root hash %s)", export.json_path, export.root_hash)


def main() -> None:
    ensure_database(APP_DSN)
    with psycopg.connect(APP_DSN) as conn:
        apply_app_schema(conn)
    # a pristine environment (prod) has no dump to create the lake tables;
    # the DDL is CREATE IF NOT EXISTS, so this is a no-op everywhere else
    from quantrank500.market_data.local_lake import LAKE_SCHEMA
    with psycopg.connect(LAKE_DSN) as lake:
        lake.execute(LAKE_SCHEMA)
        lake.commit()
    settle_everything_pending()  # self-heal on startup
    # timezone as a ZoneInfo object on scheduler AND triggers: a string here
    # silently fell back to the container's UTC (found in prod logs, 2026-08-18)
    scheduler = BlockingScheduler(timezone=ET)
    scheduler.add_job(
        settle_everything_pending,
        CronTrigger(day_of_week="mon-fri", hour=17, minute=0, timezone=ET),
    )
    scheduler.add_job(
        # EQUS.MINI publishes yesterday's session after ~midnight ET;
        # the morning run settles what the evening run couldn't see yet
        settle_everything_pending,
        CronTrigger(day_of_week="mon-fri", hour=8, minute=0, timezone=ET),
    )
    log.info("scheduled settlement runs, weekdays 17:00 + 08:00 ET")
    scheduler.start()


if __name__ == "__main__":
    main()
