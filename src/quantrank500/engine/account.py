"""The $500 account (spec §4.4). Derived, never stored.

Pure math over (fill_price, exit_price) pairs in settlement order.
"""

from collections.abc import Iterable
from decimal import Decimal

AUDIT_NOTIONAL = Decimal(500)  # every stored PnL is standardized at $500
STARTING_BALANCE = Decimal(500)
POSITION_DIVISOR = Decimal(3)  # each position = one third of the balance at fill


def trade_return(fill_price: Decimal, exit_price: Decimal) -> Decimal:
    return (exit_price - fill_price) / fill_price


def standardized_pnl(fill_price: Decimal, exit_price: Decimal) -> Decimal:
    return AUDIT_NOTIONAL * trade_return(fill_price, exit_price)


def replay_balance(settled_prices: Iterable[tuple[Decimal, Decimal]]) -> Decimal:
    """Replay (fill_price, exit_price) pairs chronologically into a balance."""
    balance = STARTING_BALANCE
    for fill_price, exit_price in settled_prices:
        balance += balance / POSITION_DIVISOR * trade_return(fill_price, exit_price)
    return balance
