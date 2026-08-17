"""Seed the demo environment with simulated participants over real market history.

    QR500_ENV=demo QR500_PG_DSN=...quantrank500_demo python scripts/seed_demo.py

Forty identities with distinct personalities post across ~110 real sessions; the
real settlement engine replays real bars to decide every outcome — nothing about
the results is scripted. Deterministic (seeded RNG): the same demo every time.

All display names start with "demo-" so a screenshot can never be mistaken for a
real record. Refuses to run when QR500_ENV=prod. Run scripts/reset_env.py first
for a clean slate.
"""

import hashlib
import random
import uuid
from decimal import ROUND_UP, Decimal

import psycopg

from quantrank500.commitment import new_nonce
from quantrank500.config import APP_DSN, ENV, ensure_database, forbid_in_prod
from quantrank500.db import apply_app_schema
from quantrank500.engine import Result, replay_prediction
from quantrank500.market_data.local_lake import LocalLakeSource
from quantrank500.worker import catch_up

LIQUID = ["AAPL", "MSFT", "NVDA", "AMD"]
VOLATILE = ["SMCI", "MSTR", "DJT", "COIN", "GME"]
EVERYTHING = LIQUID + VOLATILE + ["TSLA", "SOFI", "PLUG", "RIVN"]

# style, how many, activity rate, entry offset vs prior close, stop %, target %, tickers, edge
# edge: probability of using hindsight — testing candidate plans against the replay
# engine and keeping a winner. Simulates skill in demo data; a real market would
# not qualify random pickers (we checked — it didn't).
PERSONALITIES = [
    ("sharp",   4, 0.75, (-0.012, -0.003), (0.020, 0.035), (0.085, 0.105), EVERYTHING, 0.80),
    ("decent",  4, 0.70, (-0.012, -0.003), (0.020, 0.035), (0.085, 0.105), LIQUID, 0.55),
    ("steady",  4, 0.85, (-0.012, -0.003), (0.018, 0.030), (0.085, 0.100), LIQUID, 0.0),
    ("swing",   6, 0.50, (-0.015, -0.005), (0.020, 0.035), (0.090, 0.115), EVERYTHING, 0.0),
    ("fisher",  6, 0.70, (-0.029, -0.022), (0.010, 0.020), (0.085, 0.095), EVERYTHING, 0.0),
    ("yolo",    6, 0.60, (-0.010,  0.000), (0.004, 0.012), (0.085, 0.120), VOLATILE, 0.0),
    ("single",  4, 0.60, (-0.012, -0.004), (0.015, 0.030), (0.085, 0.105), ["TSLA"], 0.0),
    ("casual",  6, 0.15, (-0.015, -0.003), (0.015, 0.030), (0.085, 0.110), EVERYTHING, 0.0),
    # joined recently: genuinely mid-climb toward 33 ("16 of 33" rows)
    ("newcomer", 4, 0.70, (-0.012, -0.003), (0.018, 0.030), (0.085, 0.100), EVERYTHING, 0.35),
]

NEWCOMER_SESSIONS = 40  # newcomers only post in the last N sessions of the window

HINDSIGHT_TRIES = 8

SESSIONS_TO_SEED = 110
CENT = Decimal("0.01")


def create_identity(conn, style: str, number: int, rng: random.Random) -> uuid.UUID:
    identity = uuid.uuid4()
    fake_token_hash = hashlib.sha256(rng.randbytes(32)).hexdigest()
    conn.execute(
        "INSERT INTO identities (public_id, api_token_hash, display_name)"
        " VALUES (%s, %s, %s)",
        (identity, fake_token_hash, f"demo-{style}-{number:02d}"),
    )
    return identity


def plan_prediction(rng, close, offsets, stops, targets):
    entry = (close * (1 + Decimal(str(rng.uniform(*offsets))))).quantize(CENT)
    stop = (entry * (1 - Decimal(str(rng.uniform(*stops))))).quantize(CENT)
    target_pct = max(rng.uniform(*targets), 0.0852)  # stay above the 8.5% CHECK
    target = (entry * (1 + Decimal(str(target_pct)))).quantize(CENT, rounding=ROUND_UP)
    return entry, stop, target


def main() -> None:
    forbid_in_prod("seed simulated data")
    ensure_database(APP_DSN)
    rng = random.Random(42)
    source = LocalLakeSource()
    calendar = source.calendar()
    window = calendar[-(SESSIONS_TO_SEED + 5) : -5]
    settle_until = calendar[calendar.index(window[-1]) + 3]

    with psycopg.connect(APP_DSN) as conn:
        apply_app_schema(conn)
        planned = 0
        total_identities = sum(count for _, count, *_ in PERSONALITIES)
        for style, count, activity, offsets, stops, targets, tickers, edge in PERSONALITIES:
            style_window = window[-NEWCOMER_SESSIONS:] if style == "newcomer" else window
            for number in range(1, count + 1):
                identity = create_identity(conn, style, number, rng)
                for session in style_window:
                    if rng.random() > activity:
                        continue
                    prior = calendar[calendar.index(session) - 1]
                    use_hindsight = rng.random() < edge
                    tries = HINDSIGHT_TRIES if use_hindsight else 1
                    plan = None
                    for _ in range(tries):
                        ticker = rng.choice(tickers)
                        close = source.official_close(ticker, prior)
                        if close is None:
                            continue
                        candidate = plan_prediction(rng, close, offsets, stops, targets)
                        plan = (ticker, *candidate)
                        if not use_hindsight:
                            break
                        outcome = replay_prediction(
                            source, ticker, *candidate,
                            sessions=[d for d in calendar if d >= session][:3],
                        )
                        if outcome.settlement and outcome.settlement.result == Result.WIN:
                            break  # a winner found in hindsight; keep it
                    if plan is None:
                        continue
                    ticker, entry, stop, target = plan
                    conn.execute(
                        "INSERT INTO predictions"
                        " (identity_id, ticker, session_date, entry_price, stop_loss,"
                        "  target_price, commit_nonce)"
                        " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (identity, ticker, session, entry, stop, target, new_nonce()),
                    )
                    planned += 1
        conn.commit()
        print(f"[{ENV}] seeded {total_identities} identities, {planned} predictions"
              f" over {window[0]} .. {window[-1]}")
        print("replaying nightly settlements (a minute or two)...")
        runs = catch_up(conn, source, today=settle_until)
        settled = sum(run.settled for run in runs)
        unfilled = sum(run.unfilled for run in runs)
        print(f"[{ENV}] {len(runs)} settlement runs: {settled} settled, {unfilled} unfilled")
    source.close()


if __name__ == "__main__":
    main()
