"""Commit-reveal primitives (spec §4.1): the hash MUST include the random nonce."""

from quantrank500.commitment import (
    canonical_payload,
    canonical_price,
    commitment_hash,
    new_nonce,
)

PLAN = {
    "ticker": "ACME",
    "session_date": "2025-06-02",
    "entry_price": "10.00",
    "stop_loss": "9.50",
    "target_price": "10.60",
}


def test_canonical_payload_is_key_sorted_and_compact():
    scrambled = dict(reversed(list(PLAN.items())))

    assert canonical_payload(scrambled) == canonical_payload(PLAN)
    assert " " not in canonical_payload(PLAN)


def test_commitment_is_reproducible_from_payload_and_nonce():
    nonce = new_nonce()

    first = commitment_hash(canonical_payload(PLAN), nonce)
    second = commitment_hash(canonical_payload(PLAN), nonce)

    assert first == second
    assert len(first) == 64  # SHA-256 hex


def test_different_nonce_means_different_commitment():
    payload = canonical_payload(PLAN)

    assert commitment_hash(payload, new_nonce()) != commitment_hash(payload, new_nonce())


def test_canonical_price_always_carries_four_decimals():
    # client-sent "10.00" and database-read "10.0000" must hash identically
    assert canonical_price("10.00") == "10.0000"
    assert canonical_price("10.0000") == "10.0000"


def test_nonce_is_128_bit_hex():
    nonce = new_nonce()

    assert len(nonce) == 32
    int(nonce, 16)  # parses as hex
