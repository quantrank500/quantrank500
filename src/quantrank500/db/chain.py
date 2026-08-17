"""Chain verification: walk every event, recompute every hash, report the first break.

Anyone with the export can do the same; this is the reconciliation job's core.
"""

from dataclasses import dataclass

import psycopg

from quantrank500.commitment import canonical_payload
from quantrank500.db.events import GENESIS_HASH, event_hash_of


@dataclass(frozen=True)
class ChainReport:
    intact: bool
    events_checked: int
    first_broken_event_id: int | None


def verify_chain(conn: psycopg.Connection) -> ChainReport:
    events = conn.execute(
        "SELECT id, payload, prev_hash, event_hash FROM prediction_events ORDER BY id"
    ).fetchall()
    expected_prev = GENESIS_HASH
    for event in events:
        event_id, payload, prev_hash, event_hash = _fields(event)
        recomputed = event_hash_of(prev_hash, canonical_payload(payload))
        if prev_hash != expected_prev or recomputed != event_hash:
            return ChainReport(
                intact=False,
                events_checked=len(events),
                first_broken_event_id=event_id,
            )
        expected_prev = event_hash
    return ChainReport(intact=True, events_checked=len(events), first_broken_event_id=None)


def _fields(row) -> tuple:
    if isinstance(row, dict):
        return row["id"], row["payload"], row["prev_hash"], row["event_hash"]
    return row
