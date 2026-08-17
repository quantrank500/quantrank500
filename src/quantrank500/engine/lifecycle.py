"""One prediction's full life over a MarketDataSource: fill on day 1, settle days 1-3.

Composes the pure fill/settle/account functions. The only I/O is through the
MarketDataSource interface, so tests drive it with FakeSource and the nightly
job (M2) drives it with real sources, one session at a time or all at once.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from quantrank500.engine.account import standardized_pnl
from quantrank500.engine.fill import fill_bar_mode
from quantrank500.engine.settle import settle_session_bar_mode
from quantrank500.engine.types import Fill, Position, Settlement
from quantrank500.market_data.source import MarketDataSource

MAX_HOLD_SESSIONS = 3  # structural rule (spec §2)


@dataclass(frozen=True)
class ReplayOutcome:
    """How the prediction ended.

    fill None            -> unfilled (permanent, public, counts in Fill Rate).
    settlement None      -> filled but unsettleable from the given sessions:
                            still open, or missing data at expiry (caller decides).
    """

    fill: Fill | None
    settlement: Settlement | None
    pnl: Decimal | None  # standardized $500-notional PnL


def replay_prediction(
    source: MarketDataSource,
    ticker: str,
    entry: Decimal,
    stop: Decimal,
    target: Decimal,
    sessions: list[date],
) -> ReplayOutcome:
    """Replay a prediction over its target session and up to two more."""
    if not 1 <= len(sessions) <= MAX_HOLD_SESSIONS:
        raise ValueError(f"sessions must be 1..{MAX_HOLD_SESSIONS} trading days")

    target_session = sessions[0]
    fill = fill_bar_mode(
        entry,
        source.session_bars(ticker, target_session),
        source.official_open(ticker, target_session),
    )
    if fill is None:
        return ReplayOutcome(fill=None, settlement=None, pnl=None)

    position = Position(fill=fill, stop=stop, target=target)
    settlement = _settle_over(source, ticker, position, sessions)
    if settlement is None:
        return ReplayOutcome(fill=fill, settlement=None, pnl=None)
    return ReplayOutcome(
        fill=fill,
        settlement=settlement,
        pnl=standardized_pnl(fill.price, settlement.exit_price),
    )


def _settle_over(
    source: MarketDataSource, ticker: str, position: Position, sessions: list[date]
) -> Settlement | None:
    expiry_session = sessions[-1]
    for session in sessions:
        settlement = settle_session_bar_mode(
            position,
            source.session_bars(ticker, session),
            expiry=session == expiry_session,
        )
        if settlement is not None:
            return settlement
    return None
