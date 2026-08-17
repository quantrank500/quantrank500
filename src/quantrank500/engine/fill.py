"""Bar-mode fill rules (spec §4.2).

A queued prediction becomes a working limit order for its target session only:
- Touch rule: filled when a completed 1-minute bar's Low <= entry. Fill price = entry.
- Gap improvement: if the session opens below entry, fill price = the opening price.
- No touching bar by the close -> unfilled.

Pure functions: bars in, decision out. No I/O, no clock, no database.
"""

from decimal import Decimal

from quantrank500.engine.types import Fill
from quantrank500.market_data.types import Bar

VOLATILE_OPEN_THRESHOLD = Decimal("0.03")  # working default (spec §2)


def fill_bar_mode(entry: Decimal, bars: list[Bar], official_open: Decimal | None) -> Fill | None:
    """Return the session's Fill, or None if the order never filled.

    `bars` are the session's regular-session minute bars, chronological.
    No recorded trading means nothing verifiable to fill against: fail safe, unfilled.
    """
    if not bars:
        return None

    session_open = official_open if official_open is not None else bars[0].open
    if session_open < entry:
        return Fill(price=session_open, ts=bars[0].ts, at_open=True)  # gap improvement

    for bar in bars:
        if bar.low <= entry:
            return Fill(price=entry, ts=bar.ts, at_open=False)
    return None


def volatile_open_flag(bars: list[Bar], prior_close: Decimal) -> bool:
    """Informational only: first-bar range > 3% of the prior session's close."""
    if not bars:
        return False
    first = bars[0]
    return first.high - first.low > prior_close * VOLATILE_OPEN_THRESHOLD
