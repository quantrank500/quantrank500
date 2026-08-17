"""Posting-time reference data (spec v13.41): the latest daily close and
volume for any US ticker, plus the universe floors.

The universe is no longer a list — it is two objective floors any ticker can
clear: price >= $3 and daily dollar volume >= $1M (working defaults, spec §2).
Both come from the same daily bar, so the check is free.

Lookup order: the lake first; if the lake has never seen the ticker (or its
close is stale), one small on-demand Databento pull — which is upserted, so
the lake learns the symbol and the nightly refresh keeps it fresh from then on.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from quantrank500.market_data.databento import (
    Fetch,
    daily_row,
    trading_days,
    upsert_daily,
)

PRICE_FLOOR = Decimal("3.00")             # working default (spec §2)
DOLLAR_VOLUME_FLOOR = Decimal("1000000")  # working default (spec §2)
STALE_AFTER_DAYS = 7  # a week-old close can't anchor a ±3% entry band
LOOKBACK_DAYS = 7     # how far back the on-demand pull searches for a session


@dataclass(frozen=True)
class Reference:
    session: date
    close: Decimal
    volume: int


def floors_reason(ref: Reference) -> str | None:
    """Why this ticker fails the universe floors — None if it clears them."""
    if ref.close < PRICE_FLOOR:
        return f"price below the ${PRICE_FLOOR} floor"
    if ref.close * ref.volume < DOLLAR_VOLUME_FLOOR:
        return "daily dollar volume below the $1M floor"
    return None


def lake_latest(conn, ticker: str) -> Reference | None:
    row = conn.execute(
        "SELECT session_date, close_price, volume FROM lake.daily_prices"
        " WHERE ticker = %s AND close_price IS NOT NULL"
        " ORDER BY session_date DESC LIMIT 1",
        (ticker,),
    ).fetchone()
    if not row:
        return None
    session, close, volume = row[0], row[1], row[2]
    return Reference(session=session, close=close, volume=int(volume or 0))


def databento_latest(fetch: Fetch, conn, ticker: str, today: date) -> Reference | None:
    """The most recent daily bar Databento has, walking back from yesterday.
    Whatever is found is upserted — the lake learns the symbol."""
    sessions = trading_days(today - timedelta(days=LOOKBACK_DAYS), today - timedelta(days=1))
    for session in reversed(sessions):
        row = daily_row(fetch(ticker, "ohlcv-1d", session))
        if row:
            upsert_daily(conn, ticker, session, row)
            conn.commit()
            _open, _high, _low, close, volume = row
            return Reference(session=session, close=close, volume=volume)
    return None


def make_reference(connect: Callable, fetch: Fetch | None, today: Callable[[], date]):
    """The lookup the posting endpoint uses: lake first, Databento for the
    unseen or stale, lake value as the fallback when the pull finds nothing."""

    def lookup(ticker: str) -> Reference | None:
        with connect() as conn:
            known = lake_latest(conn, ticker)
            fresh_enough = known and known.session >= today() - timedelta(days=STALE_AFTER_DAYS)
            if fresh_enough or fetch is None:
                return known
            return databento_latest(fetch, conn, ticker, today()) or known

    return lookup
