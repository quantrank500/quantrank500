"""Commit-reveal primitives (spec §4.1).

The commitment is public at submission; the payload stays hidden until 9:30 AM ET.
The nonce is essential: without it, the small prediction space (known tickers,
±3% price band) is brute-forceable. After reveal anyone can recompute the hash.
"""

import hashlib
import json
import secrets
from decimal import Decimal


def canonical_price(value: Decimal | str) -> str:
    """Prices in commitment payloads always carry 4 decimals ("10.00" -> "10.0000"),
    matching DECIMAL(12,4) storage, so client-sent and database-read values hash alike."""
    return str(Decimal(value).quantize(Decimal("0.0001")))


def new_nonce() -> str:
    """Random 128-bit nonce as 32 hex chars."""
    return secrets.token_hex(16)


def canonical_payload(fields: dict) -> str:
    """Deterministic JSON: sorted keys, no whitespace. The reveal publishes this
    exact string so anyone can re-hash it."""
    return json.dumps(fields, sort_keys=True, separators=(",", ":"))


def commitment_hash(payload: str, nonce: str) -> str:
    """SHA-256(canonical payload || nonce), hex."""
    return hashlib.sha256((payload + nonce).encode()).hexdigest()
