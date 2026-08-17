from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class Result(StrEnum):
    WIN = "win"
    LOSS = "loss"
    BREAKEVEN = "breakeven"
    UNRESOLVED = "unresolved"


class SettledBy(StrEnum):
    STOP = "stop"
    TARGET = "target"
    EXPIRY = "expiry"
    HALT = "halt"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class Fill:
    """A prediction becoming a position.

    `at_open` marks a gap-improvement fill at the session's opening price: the open
    is the session's first print, so everything afterwards is provably post-fill.
    """

    price: Decimal
    ts: datetime
    at_open: bool


@dataclass(frozen=True)
class Position:
    """An open position awaiting settlement."""

    fill: Fill
    stop: Decimal
    target: Decimal


@dataclass(frozen=True)
class Settlement:
    """How a position ended: at what price, why, and at what market time."""

    exit_price: Decimal
    result: Result
    settled_by: SettledBy
    ts: datetime
