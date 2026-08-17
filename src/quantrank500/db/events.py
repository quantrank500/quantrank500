"""prediction_events: INSERT-only, hash-chained. The source of truth (spec §7).

Every event links to the previous one: event_hash = SHA-256(prev_hash || payload),
where payload is the canonical JSON form (sorted keys, no whitespace). Payloads are
canonicalized here — JSONB storage normalizes text, so verification re-canonicalizes
the parsed value and must land on the same string. Corrections are new events,
never edits.
"""

import hashlib

import psycopg

from quantrank500.commitment import canonical_payload

GENESIS_HASH = "0" * 64


def append_event(
    conn: psycopg.Connection, prediction_id: int, event_type: str, payload: dict
) -> None:
    canonical = canonical_payload(payload)
    prev = conn.execute(
        "SELECT event_hash FROM prediction_events ORDER BY id DESC LIMIT 1"
    ).fetchone()
    prev_hash = _first_column(prev) if prev else GENESIS_HASH
    event_hash = event_hash_of(prev_hash, canonical)
    conn.execute(
        "INSERT INTO prediction_events"
        " (prediction_id, event_type, payload, prev_hash, event_hash)"
        " VALUES (%s, %s, %s::jsonb, %s, %s)",
        (prediction_id, event_type, canonical, prev_hash, event_hash),
    )


def event_hash_of(prev_hash: str, canonical_payload_text: str) -> str:
    return hashlib.sha256((prev_hash + canonical_payload_text).encode()).hexdigest()


def _first_column(row) -> str:
    # rows arrive as tuples or dicts depending on the connection's row_factory
    return row["event_hash"] if isinstance(row, dict) else row[0]
