"""The full public ledger export (spec §8): static files, produced nightly.

Revealed predictions carry payload + nonce so anyone can recompute their
commitment hashes. Unrevealed predictions stay commitment-only. The root hash
is the tip of the hash-chained event log at export time; it also lands in the
latest completed settlement_run.
"""

import csv
import json
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row

ET = ZoneInfo("America/New_York")
MARKET_OPEN = time(9, 30)

COLUMNS = [
    "id", "identity_id", "session_date", "status", "commitment", "created_at",
    "ticker", "entry_price", "stop_loss", "target_price", "commit_nonce",
    "fill_price", "fill_time", "exit_price", "pnl", "result", "settled_by", "settled_at",
]


@dataclass(frozen=True)
class ExportResult:
    json_path: Path
    csv_path: Path
    root_hash: str | None


def export_ledger(conn: psycopg.Connection, out_dir: Path, as_of: datetime) -> ExportResult:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = [_row_for(record, as_of) for record in _ledger_records(conn)]

    json_path = out_dir / "ledger.json"
    json_path.write_text(json.dumps(rows, indent=1))
    csv_path = out_dir / "ledger.csv"
    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    root_hash = _chain_tip(conn)
    if root_hash is not None:
        conn.execute(
            "UPDATE settlement_runs SET daily_root_hash = %s WHERE session_date ="
            " (SELECT MAX(session_date) FROM settlement_runs WHERE status = 'completed')",
            (root_hash,),
        )
        conn.commit()
        (out_dir / "root_hash.txt").write_text(root_hash + "\n")
    return ExportResult(json_path=json_path, csv_path=csv_path, root_hash=root_hash)


def _ledger_records(conn: psycopg.Connection) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            "SELECT p.*, s.exit_price, s.pnl, s.result, s.settled_by, s.settled_at"
            " FROM predictions p LEFT JOIN settlements s ON s.prediction_id = p.id"
            " ORDER BY p.id"
        ).fetchall()


def ledger_row(record: dict, as_of: datetime) -> dict:
    """One export row with the reveal rule applied — shared by the nightly ledger
    export and the per-identity CSV endpoint so the two can never disagree."""
    return _row_for(record, as_of)


def _row_for(record: dict, as_of: datetime) -> dict:
    from quantrank500.api.app import _recompute_commitment  # single hashing definition

    opens_at = datetime.combine(record["session_date"], MARKET_OPEN, tzinfo=ET)
    revealed = as_of.astimezone(ET) >= opens_at
    row = dict.fromkeys(COLUMNS)
    row |= {
        "id": record["id"],
        "identity_id": str(record["identity_id"]),
        "session_date": str(record["session_date"]),
        "status": record["status"],
        "commitment": _recompute_commitment(record),
        "created_at": record["created_at"].isoformat(),
    }
    if not revealed:
        return row
    return row | {
        "ticker": record["ticker"],
        "entry_price": str(record["entry_price"]),
        "stop_loss": str(record["stop_loss"]),
        "target_price": str(record["target_price"]),
        "commit_nonce": record["commit_nonce"],
        "fill_price": _opt(record["fill_price"]),
        "fill_time": _opt_iso(record["fill_time"]),
        "exit_price": _opt(record["exit_price"]),
        "pnl": _opt(record["pnl"]),
        "result": record["result"],
        "settled_by": record["settled_by"],
        "settled_at": _opt_iso(record["settled_at"]),
    }


def _chain_tip(conn: psycopg.Connection) -> str | None:
    row = conn.execute(
        "SELECT event_hash FROM prediction_events ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return row[0] if not isinstance(row, dict) else row["event_hash"]


def _opt(value) -> str | None:
    return None if value is None else str(value)


def _opt_iso(value) -> str | None:
    return None if value is None else value.isoformat()
