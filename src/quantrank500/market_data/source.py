from datetime import date
from decimal import Decimal
from typing import Protocol

from quantrank500.market_data.types import Bar, Trade


class MarketDataSource(Protocol):
    """The engine's only view of market data (spec §6).

    The engine never talks to a provider directly; it consumes this interface.
    Implementations: FakeSource (tests), LocalLakeSource (dev), DatabentoSource (M6).
    """

    def session_bars(self, ticker: str, session: date) -> list[Bar]:
        """Regular-session 1-minute bars, chronological. Empty if none recorded.

        A no-trade minute has no bar — gaps in the sequence are normal.
        """
        ...

    def session_trades(self, ticker: str, session: date) -> list[Trade] | None:
        """Trade prints, chronological — or None if this source has no tick data."""
        ...

    def official_open(self, ticker: str, session: date) -> Decimal | None:
        """The session's official opening price, or None if unknown."""
        ...

    def official_close(self, ticker: str, session: date) -> Decimal | None:
        """The session's official closing price, or None if unknown."""
        ...

    def calendar(self) -> list[date]:
        """Trading sessions this source covers, ascending."""
        ...
