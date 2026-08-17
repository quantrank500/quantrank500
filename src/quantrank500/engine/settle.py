"""Bar-mode settlement (spec §4.3). Pure: position + one session's bars in, verdict out.

Rules, applied per bar in chronological order:
- A bar opening beyond a level exits at that bar's open (a real gap: worse than the
  stop on the way down, better than the target on the way up).
- Stop touch (low <= stop) exits at the stop, a loss.
- Target touch (high >= target) exits at the target, a win.
- Both levels inside one bar: order unknowable from bars -> settles against the
  predictor (loss at stop, settled_by 'ambiguous').
- On the bar that touch-filled the position, a target touch does not count (it may
  have printed before the fill); a stop touch does. Gap-open fills are exempt.
- Expiry session with no level hit: exit at the final bar's close; within ±1% of
  the fill price it is breakeven, above a win, below a loss.
- Nothing hit and not expiry -> None: still open. No bars -> None: the engine
  cannot price an exit it cannot see; the caller decides halt handling.
"""

from decimal import Decimal

from quantrank500.engine.types import Position, Result, SettledBy, Settlement
from quantrank500.market_data.types import Bar

EXPIRY_BREAKEVEN_BAND = Decimal("0.01")  # working default (spec §2)


def settle_session_bar_mode(
    position: Position, bars: list[Bar], expiry: bool = False
) -> Settlement | None:
    post_fill = [b for b in bars if b.ts >= position.fill.ts]
    for bar in post_fill:
        is_touch_fill_bar = bar.ts == position.fill.ts and not position.fill.at_open
        settlement = _settle_bar(position, bar, target_counts=not is_touch_fill_bar)
        if settlement is not None:
            return settlement
    if expiry and post_fill:
        return _expire(position, final_close=post_fill[-1].close, ts=post_fill[-1].ts)
    return None


def _settle_bar(position: Position, bar: Bar, target_counts: bool) -> Settlement | None:
    if bar.open <= position.stop:
        return Settlement(bar.open, Result.LOSS, SettledBy.STOP, bar.ts)
    if target_counts and bar.open >= position.target:
        return Settlement(bar.open, Result.WIN, SettledBy.TARGET, bar.ts)

    stop_hit = bar.low <= position.stop
    target_hit = target_counts and bar.high >= position.target
    if stop_hit and target_hit:
        return Settlement(position.stop, Result.LOSS, SettledBy.AMBIGUOUS, bar.ts)
    if stop_hit:
        return Settlement(position.stop, Result.LOSS, SettledBy.STOP, bar.ts)
    if target_hit:
        return Settlement(position.target, Result.WIN, SettledBy.TARGET, bar.ts)
    return None


def _expire(position: Position, final_close: Decimal, ts) -> Settlement:
    band = position.fill.price * EXPIRY_BREAKEVEN_BAND
    if abs(final_close - position.fill.price) <= band:
        result = Result.BREAKEVEN
    elif final_close > position.fill.price:
        result = Result.WIN
    else:
        result = Result.LOSS
    return Settlement(final_close, result, SettledBy.EXPIRY, ts)
