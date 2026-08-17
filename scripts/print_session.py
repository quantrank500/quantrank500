"""M0 deliverable: print one real session's minute bars + official prices from the lake copy.

Usage:
    python scripts/print_session.py --session 2025-06-02 --tickers AAPL,NVDA,SMCI
"""

import argparse
from datetime import date

from quantrank500.market_data.local_lake import LocalLakeSource


def print_ticker(source: LocalLakeSource, ticker: str, session: date) -> None:
    bars = source.session_bars(ticker, session)
    official_open = source.official_open(ticker, session)
    official_close = source.official_close(ticker, session)

    print(f"\n{ticker} - {session}")
    print(f"  official open {official_open}   official close {official_close}")
    if not bars:
        print("  no minute bars recorded")
        return

    session_low = min(b.low for b in bars)
    session_high = max(b.high for b in bars)
    print(f"  {len(bars)} minute bars   session low {session_low}   high {session_high}")
    for bar in (*bars[:3], None, *bars[-2:]):
        if bar is None:
            print("    ...")
        else:
            print(
                f"    {bar.ts:%H:%M}  O {bar.open}  H {bar.high}"
                f"  L {bar.low}  C {bar.close}  V {bar.volume:,}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=date.fromisoformat, required=True)
    parser.add_argument("--tickers", default="AAPL,NVDA,SMCI")
    args = parser.parse_args()

    source = LocalLakeSource()
    for ticker in [t.strip().upper() for t in args.tickers.split(",") if t.strip()]:
        print_ticker(source, ticker, args.session)
    source.close()


if __name__ == "__main__":
    main()
