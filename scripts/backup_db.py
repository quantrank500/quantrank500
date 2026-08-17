"""Nightly backup: pg_dump inside the db container, keep the last 7 (spec §10).

    python scripts/backup_db.py                 # writes backups/quantrank500-YYYYMMDD.sql
    python scripts/backup_db.py --restore FILE  # restore drill into a scratch database

Schedule on the host (Windows Task Scheduler, 2:00 AM). Weekly off-machine copy
and the monthly restore test stay manual on purpose — you should see them happen.
"""

import argparse
import subprocess
from datetime import date
from pathlib import Path

CONTAINER = "quantrank500-db"
KEEP = 7


def dump(backup_dir: Path) -> Path:
    backup_dir.mkdir(exist_ok=True)
    out = backup_dir / f"quantrank500-{date.today():%Y%m%d}.sql"
    with out.open("wb") as handle:
        subprocess.run(
            ["docker", "exec", CONTAINER, "pg_dump", "-U", "quantrank", "quantrank500"],
            stdout=handle, check=True,
        )
    old = sorted(backup_dir.glob("quantrank500-*.sql"))[:-KEEP]
    for stale in old:
        stale.unlink()
    return out


def restore_drill(backup_file: Path) -> None:
    """Restore into a scratch database inside the container, then drop it."""
    scratch = "quantrank500_restore_test"
    run = lambda *cmd: subprocess.run(  # noqa: E731
        ["docker", "exec", "-i", CONTAINER, *cmd], check=True, stdin=subprocess.DEVNULL
    )
    run("dropdb", "-U", "quantrank", "--if-exists", scratch)
    run("createdb", "-U", "quantrank", scratch)
    with backup_file.open("rb") as handle:
        subprocess.run(
            ["docker", "exec", "-i", CONTAINER, "psql", "-q", "-U", "quantrank", scratch],
            stdin=handle, check=True, stdout=subprocess.DEVNULL,
        )
    result = subprocess.run(
        ["docker", "exec", CONTAINER, "psql", "-U", "quantrank", "-t", "-c",
         "SELECT COUNT(*) FROM predictions", scratch],
        check=True, capture_output=True, text=True,
    )
    run("dropdb", "-U", "quantrank", scratch)
    print(f"restore drill OK: {result.stdout.strip()} predictions restored and verified")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restore", type=Path, help="run a restore drill from this dump")
    parser.add_argument("--backup-dir", type=Path, default=Path("backups"))
    args = parser.parse_args()
    if args.restore:
        restore_drill(args.restore)
    else:
        print(f"backed up to {dump(args.backup_dir)}")


if __name__ == "__main__":
    main()
