"""One-time copy of a slice of the AlphaVantage lake into QuantRank500's own Postgres.

Reads (SELECT only, off-market-hours) from the shared SQL Server:
  - ResearchDB.dbo.MinuteBars        -> lake.minute_bars   (regular session 09:30-16:00 ET)
  - MCP_TestDB.dbo.StockDailyPrices  -> lake.daily_prices  (official open/close)

After this copy, QuantRank500 never touches the shared instance again (spec §6).
Idempotent: re-running replaces each ticker's rows.

Usage:
    python scripts/copy_lake_slice.py            # default tickers and date range
    python scripts/copy_lake_slice.py --tickers AAPL,NVDA --start 2025-06-01 --end 2025-06-30
"""

import argparse
import sys
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import psycopg
import pyodbc

from quantrank500.config import LAKE_DSN as PG_DSN
from quantrank500.market_data.local_lake import LAKE_SCHEMA as PG_SCHEMA

ET = ZoneInfo("America/New_York")

# Chosen so M1's adversarial cases exist in the data: mega-liquid, volatile/gappy,
# halt-prone, and low-priced names.
DEFAULT_TICKERS = [
    "AAPL", "MSFT", "NVDA",          # mega-liquid
    "TSLA", "AMD", "COIN",           # liquid + volatile
    "MSTR", "SMCI",                  # gappy, large daily ranges
    "GME", "DJT",                    # halt-prone
    "SOFI", "PLUG", "RIVN",          # low-priced, high-volume
]

MSSQL_CONN = (
    "DRIVER={ODBC Driver 18 for SQL Server};SERVER=localhost;"
    "Trusted_Connection=yes;TrustServerCertificate=yes"
)

BARS_QUERY = """
SELECT Symbol, BarTimeEt, [Open], High, Low, [Close], Volume
FROM ResearchDB.dbo.MinuteBars
WHERE Symbol = ?
  AND BarTimeUtc >= ? AND BarTimeUtc < ?
  AND CAST(BarTimeEt AS time) >= '09:30:00'
  AND CAST(BarTimeEt AS time) <  '16:00:00'
ORDER BY BarTimeEt
"""

DAILY_QUERY = """
SELECT Symbol, TradeDate, OpenPrice, High, Low, ClosePrice, Volume
FROM MCP_TestDB.dbo.StockDailyPrices
WHERE Symbol = ? AND TradeDate >= ? AND TradeDate <= ?
ORDER BY TradeDate
"""


def refuse_during_market_hours(allow_override: bool) -> None:
    now = datetime.now(ET)
    is_weekday = now.weekday() < 5
    in_session = time(9, 25) <= now.time() < time(16, 5)
    if is_weekday and in_session and not allow_override:
        sys.exit(
            "Refusing to query the shared SQL Server during market hours "
            f"(now {now:%H:%M} ET). It hosts LIVE trading. Re-run after 16:05 ET, "
            "or pass --allow-market-hours if you are certain."
        )


def copy_ticker_bars(mssql, pg, ticker: str, start: date, end: date) -> int:
    # A 16:00 ET bar lands at 20:00/21:00 UTC the same date, so these UTC bounds
    # cover the whole [start, end] ET range; the session-time filter is exact.
    src = mssql.cursor()
    src.execute(BARS_QUERY, ticker, f"{start} 00:00:00", f"{end} 23:59:59")
    with pg.cursor() as cur:
        cur.execute("DELETE FROM lake.minute_bars WHERE ticker = %s", (ticker,))
        copied = 0
        with cur.copy(
            "COPY lake.minute_bars (ticker, ts_et, open, high, low, close, volume) FROM STDIN"
        ) as copy:
            while rows := src.fetchmany(50_000):
                for symbol, ts_et, o, h, lo, c, vol in rows:
                    copy.write_row((symbol, ts_et, o, h, lo, c, vol))
                copied += len(rows)
    pg.commit()
    return copied


def copy_ticker_daily(mssql, pg, ticker: str, start: date, end: date) -> int:
    src = mssql.cursor()
    src.execute(DAILY_QUERY, ticker, start, end)
    rows = src.fetchall()
    with pg.cursor() as cur:
        cur.execute("DELETE FROM lake.daily_prices WHERE ticker = %s", (ticker,))
        cur.executemany(
            "INSERT INTO lake.daily_prices"
            " (ticker, session_date, open_price, high, low, close_price, volume)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [tuple(r) for r in rows],
        )
    pg.commit()
    return len(rows)


def report_size(pg) -> None:
    with pg.cursor() as cur:
        cur.execute(
            "SELECT pg_size_pretty(pg_total_relation_size('lake.minute_bars')),"
            "       pg_size_pretty(pg_total_relation_size('lake.daily_prices')),"
            "       pg_size_pretty(pg_database_size(current_database()))"
        )
        bars_size, daily_size, db_size = cur.fetchone()
    print(f"\nStorage: minute_bars {bars_size}, daily_prices {daily_size}, database {db_size}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tickers", default=",".join(DEFAULT_TICKERS))
    parser.add_argument("--start", type=date.fromisoformat, default=date(2025, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 3, 31))
    parser.add_argument("--allow-market-hours", action="store_true")
    parser.add_argument("--pg-dsn", default=PG_DSN)
    args = parser.parse_args()

    refuse_during_market_hours(args.allow_market_hours)

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    mssql = pyodbc.connect(MSSQL_CONN, readonly=True, autocommit=True)
    with psycopg.connect(args.pg_dsn) as pg:
        pg.execute(PG_SCHEMA)
        pg.commit()
        total = 0
        for ticker in tickers:
            bars = copy_ticker_bars(mssql, pg, ticker, args.start, args.end)
            days = copy_ticker_daily(mssql, pg, ticker, args.start, args.end)
            total += bars
            print(f"{ticker:6s} {bars:>9,} bars  {days:>4} daily rows")
        print(f"\nTotal: {total:,} minute bars across {len(tickers)} tickers")
        report_size(pg)
    mssql.close()


if __name__ == "__main__":
    main()
