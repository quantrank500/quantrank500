from datetime import date
from decimal import Decimal

from quantrank500.market_data.types import Bar, Trade

Key = tuple[str, date]  # (ticker, session)


class FakeSource:
    """In-memory MarketDataSource for tests. Bar mode by default (no trades)."""

    def __init__(
        self,
        bars: dict[Key, list[Bar]] | None = None,
        trades: dict[Key, list[Trade]] | None = None,
        opens: dict[Key, Decimal] | None = None,
        closes: dict[Key, Decimal] | None = None,
        sessions: list[date] | None = None,
    ):
        self._bars = bars or {}
        self._trades = trades or {}
        self._opens = opens or {}
        self._closes = closes or {}
        self._sessions = sessions or []

    def session_bars(self, ticker: str, session: date) -> list[Bar]:
        return sorted(self._bars.get((ticker, session), []), key=lambda b: b.ts)

    def session_trades(self, ticker: str, session: date) -> list[Trade] | None:
        recorded = self._trades.get((ticker, session))
        if recorded is None:
            return None
        return sorted(recorded, key=lambda t: t.ts)

    def official_open(self, ticker: str, session: date) -> Decimal | None:
        return self._opens.get((ticker, session))

    def official_close(self, ticker: str, session: date) -> Decimal | None:
        return self._closes.get((ticker, session))

    def calendar(self) -> list[date]:
        return sorted(set(self._sessions))
