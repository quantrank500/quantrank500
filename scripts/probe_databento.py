"""Phase B probe: gain confidence in Databento with evidence, not promises.

    python scripts/probe_databento.py            # cost previews only (spends nothing)
    python scripts/probe_databento.py --pull     # + one small real pull for cross-validation

Reads DATABENTO_API_KEY from the environment or the repo-root .env
(never passed on the command line, never printed).

What it measures (spec §6 gate, v13.26):
1. Dataset coverage: does EQUS.MINI cover our date range?
2. Cost, measured: price of one production-shaped nightly pull
   (13 symbols x 1 session, trades + ohlcv-1m) via the cost-preview API — x21
   approximates the monthly bill before spending a cent.
3. Cross-validation (--pull): fetch one ticker-session of 1-minute bars and
   diff them against the dev lake's bars for the same session.
"""

import argparse
import os
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import databento as db

TICKERS = ["AAPL", "MSFT", "NVDA", "TSLA", "AMD", "COIN", "MSTR",
           "SMCI", "GME", "DJT", "SOFI", "PLUG", "RIVN"]
DATASET = "EQUS.MINI"
PROBE_SESSION = date(2025, 6, 2)  # a session the lake also holds -> comparable
CROSS_TICKER = "SMCI"


def api_key() -> str:
    key = os.environ.get("DATABENTO_API_KEY")
    if not key:
        env_file = Path(__file__).resolve().parent.parent / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("DATABENTO_API_KEY="):
                    key = line.split("=", 1)[1].strip()
    if not key:
        sys.exit("DATABENTO_API_KEY not found in environment or .env")
    return key


def cost_preview(client: db.Historical, schema: str, symbols: list[str],
                 start: date, end: date) -> float:
    return client.metadata.get_cost(
        dataset=DATASET, symbols=symbols, schema=schema,
        start=start.isoformat(), end=end.isoformat(),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pull", action="store_true",
                        help="spend a little: pull one ticker-session of bars and cross-validate")
    args = parser.parse_args()

    client = db.Historical(api_key())

    print("=== 1. Coverage ===")
    rng = client.metadata.get_dataset_range(dataset=DATASET)
    print(f"{DATASET} available range: {rng}")

    print("\n=== 2. Cost of one production-shaped night (13 symbols, 1 session) ===")
    day_after = PROBE_SESSION + timedelta(days=1)
    for schema in ("trades", "ohlcv-1m"):
        usd = cost_preview(client, schema, TICKERS, PROBE_SESSION, day_after)
        print(f"  {schema:9s}: ${usd:.4f}/night  ->  ~${usd * 21:.2f}/month (x21 sessions)")

    if not args.pull:
        print("\n(cost previews spend nothing; re-run with --pull for the cross-validation)")
        return

    print(f"\n=== 3. Cross-validation: {CROSS_TICKER} {PROBE_SESSION} ohlcv-1m vs the lake ===")
    store = client.timeseries.get_range(
        dataset=DATASET, symbols=[CROSS_TICKER], schema="ohlcv-1m",
        start=PROBE_SESSION.isoformat(), end=day_after.isoformat(),
    )
    frame = store.to_df()
    print(f"Databento returned {len(frame)} bars")

    import psycopg

    from quantrank500.config import LAKE_DSN
    lake = {}
    with psycopg.connect(LAKE_DSN) as conn:
        for ts, o, h, lo, c in conn.execute(
            "SELECT ts_et, open, high, low, close FROM lake.minute_bars"
            " WHERE ticker = %s AND ts_et >= %s AND ts_et < %s ORDER BY ts_et",
            (CROSS_TICKER, PROBE_SESSION, day_after),
        ).fetchall():
            lake[ts.strftime("%H:%M")] = (o, h, lo, c)
    print(f"Lake has {len(lake)} bars")

    matches = mismatches = db_only = 0
    worst = None
    for ts, row in frame.iterrows():
        minute = ts.tz_convert("America/New_York").strftime("%H:%M")
        if minute not in lake:
            db_only += 1
            continue
        lo_, lh, ll, lc = lake[minute]
        diffs = [abs(Decimal(str(row["open"])) - lo_), abs(Decimal(str(row["high"])) - lh),
                 abs(Decimal(str(row["low"])) - ll), abs(Decimal(str(row["close"])) - lc)]
        if max(diffs) <= Decimal("0.01"):
            matches += 1
        else:
            mismatches += 1
            if worst is None or max(diffs) > worst[1]:
                worst = (minute, max(diffs))
    print(f"Within a cent: {matches}  |  beyond a cent: {mismatches}"
          f"  |  Databento-only minutes: {db_only}")
    if worst:
        print(f"Largest OHLC difference: {worst[1]} at {worst[0]} ET")
    print("\nInterpretation: EQUS.MINI is a venue blend, the lake is a consolidated"
          " feed — small differences are expected and are exactly what H.4 documents."
          " Large systematic differences would be the red flag.")


if __name__ == "__main__":
    main()
