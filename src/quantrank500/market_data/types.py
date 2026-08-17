from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class Bar:
    """One completed 1-minute bar. `ts` is the bar's start time, timezone-aware (ET)."""

    ts: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


@dataclass(frozen=True)
class Trade:
    """One exchange trade print, timezone-aware (ET). Used in tick mode only."""

    ts: datetime
    price: Decimal
    size: int
