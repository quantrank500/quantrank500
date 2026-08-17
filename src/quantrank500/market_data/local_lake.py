"""LocalLakeSource — dev/test MarketDataSource backed by QuantRank500's own Postgres.

The data is a one-time copy of a slice of the AlphaVantage minute-bar lake
(scripts/copy_lake_slice.py). Bar mode only — the lake is minutes, so
session_trades is always None. Dev/test only; this data is never published.
"""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import psycopg

from quantrank500.config import LAKE_DSN
from quantrank500.market_data.types import Bar, Trade

ET = ZoneInfo("America/New_York")

LAKE_SCHEMA = """
CREATE SCHEMA IF NOT EXISTS lake;
CREATE TABLE IF NOT EXISTS lake.minute_bars (
    ticker  text NOT NULL,
    ts_et   timestamp NOT NULL,   -- naive ET wall time, as recorded in the lake
    open    numeric(18,4),
    high    numeric(18,4),
    low     numeric(18,4),
    close   numeric(18,4),
    volume  bigint,
    PRIMARY KEY (ticker, ts_et)
);
CREATE TABLE IF NOT EXISTS lake.daily_prices (
    ticker       text NOT NULL,
    session_date date NOT NULL,
    open_price   numeric(18,4),
    high         numeric(18,4),
    low          numeric(18,4),
    close_price  numeric(18,4),
    volume       bigint,
    PRIMARY KEY (ticker, session_date)
);
"""


class LocalLakeSource:
    def __init__(self, conninfo: str = LAKE_DSN):
        self._conn = psycopg.connect(conninfo)

    def close(self) -> None:
        self._conn.close()

    def session_bars(self, ticker: str, session: date) -> list[Bar]:
        rows = self._conn.execute(
            "SELECT ts_et, open, high, low, close, volume"
            " FROM lake.minute_bars"
            " WHERE ticker = %s AND ts_et >= %s AND ts_et < %s"
            " ORDER BY ts_et",
            (ticker, datetime.combine(session, time.min),
             datetime.combine(session + timedelta(days=1), time.min)),
        ).fetchall()
        return [
            Bar(ts=ts.replace(tzinfo=ET), open=o, high=h, low=lo, close=c, volume=vol)
            for ts, o, h, lo, c, vol in rows
        ]

    def session_trades(self, ticker: str, session: date) -> list[Trade] | None:
        return None  # the lake is minute bars; tick data arrives with DatabentoSource (M6)

    def official_open(self, ticker: str, session: date) -> Decimal | None:
        return self._daily_price("open_price", ticker, session)

    def official_close(self, ticker: str, session: date) -> Decimal | None:
        return self._daily_price("close_price", ticker, session)

    def calendar(self) -> list[date]:
        rows = self._conn.execute(
            "SELECT DISTINCT session_date FROM lake.daily_prices ORDER BY session_date"
        ).fetchall()
        return [session_date for (session_date,) in rows]

    def _daily_price(self, column: str, ticker: str, session: date) -> Decimal | None:
        assert column in ("open_price", "close_price")
        row = self._conn.execute(
            f"SELECT {column} FROM lake.daily_prices WHERE ticker = %s AND session_date = %s",
            (ticker, session),
        ).fetchone()
        return row[0] if row else None
