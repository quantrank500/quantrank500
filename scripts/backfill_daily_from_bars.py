"""Backfill lake.daily_prices from the copied minute bars, locally.

StockDailyPrices on the shared server covers only its own watchlist. For tickers
it misses, derive session prices from the bars we already copied: open = first
bar's open, close = last bar's close, high/low/volume aggregated. These are
bar-derived session prices, not official auction prints — a documented dev
approximation until DatabentoSource (M6) brings official opens/closes.
Existing rows are never overwritten. No shared-server contact.
"""

import psycopg

from quantrank500.config import LAKE_DSN

BACKFILL = """
WITH firsts AS (
    SELECT DISTINCT ON (ticker, ts_et::date)
           ticker, ts_et::date AS session_date, open
    FROM lake.minute_bars
    ORDER BY ticker, ts_et::date, ts_et
),
lasts AS (
    SELECT DISTINCT ON (ticker, ts_et::date)
           ticker, ts_et::date AS session_date, close
    FROM lake.minute_bars
    ORDER BY ticker, ts_et::date, ts_et DESC
),
aggregates AS (
    SELECT ticker, ts_et::date AS session_date,
           MAX(high) AS high, MIN(low) AS low, SUM(volume) AS volume
    FROM lake.minute_bars
    GROUP BY ticker, ts_et::date
)
INSERT INTO lake.daily_prices
    (ticker, session_date, open_price, high, low, close_price, volume)
SELECT f.ticker, f.session_date, f.open, a.high, a.low, l.close, a.volume
FROM firsts f
JOIN lasts l USING (ticker, session_date)
JOIN aggregates a USING (ticker, session_date)
ON CONFLICT (ticker, session_date) DO NOTHING
"""


def main() -> None:
    with psycopg.connect(LAKE_DSN) as conn:
        inserted = conn.execute(BACKFILL).rowcount
        conn.commit()
        tickers, days = conn.execute(
            "SELECT COUNT(DISTINCT ticker), COUNT(DISTINCT session_date)"
            " FROM lake.daily_prices"
        ).fetchone()
    print(f"backfilled {inserted:,} rows; daily_prices now covers"
          f" {tickers} tickers x {days} sessions")


if __name__ == "__main__":
    main()
