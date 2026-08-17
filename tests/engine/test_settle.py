"""Bar-mode settlement (spec §4.3): stop/target/expiry, gaps, same-bar ambiguity.

Every ambiguous case settles against the predictor. That is the design's spine.
"""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from quantrank500.engine import (
    Fill,
    Position,
    Result,
    SettledBy,
    settle_session_bar_mode,
)
from quantrank500.market_data import Bar

ET = ZoneInfo("America/New_York")


def bar(hour: int, minute: int, o: str, h: str, lo: str, c: str, volume: int = 1000) -> Bar:
    return Bar(
        ts=datetime(2025, 6, 3, hour, minute, tzinfo=ET),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(lo),
        close=Decimal(c),
        volume=volume,
    )


@pytest.fixture
def position():
    """Filled yesterday at 10.00; stop 9.50, target 10.60. Today's bars are all post-fill."""
    return Position(
        fill=Fill(price=Decimal("10.00"), ts=datetime(2025, 6, 2, 9, 47, tzinfo=ET), at_open=False),
        stop=Decimal("9.50"),
        target=Decimal("10.60"),
    )


class TestOrdinarySettlement:
    def test_stop_touch_is_a_loss_exiting_at_stop(self, position):
        bars = [bar(9, 30, "9.80", "9.85", "9.45", "9.60")]

        settlement = settle_session_bar_mode(position, bars)

        assert settlement.exit_price == Decimal("9.50")
        assert settlement.result == Result.LOSS
        assert settlement.settled_by == SettledBy.STOP
        assert settlement.ts == bars[0].ts

    def test_target_touch_is_a_win_exiting_at_target(self, position):
        bars = [bar(10, 0, "10.40", "10.65", "10.35", "10.55")]

        settlement = settle_session_bar_mode(position, bars)

        assert settlement.exit_price == Decimal("10.60")
        assert settlement.result == Result.WIN
        assert settlement.settled_by == SettledBy.TARGET

    def test_both_levels_inside_one_bar_settles_against_predictor(self, position):
        bars = [bar(9, 30, "10.00", "10.70", "9.40", "10.10")]

        settlement = settle_session_bar_mode(position, bars)

        assert settlement.exit_price == Decimal("9.50")
        assert settlement.result == Result.LOSS
        assert settlement.settled_by == SettledBy.AMBIGUOUS

    def test_bar_opening_below_stop_exits_at_that_bars_open(self, position):
        bars = [bar(9, 30, "9.20", "9.60", "9.10", "9.40")]  # gapped through the stop

        settlement = settle_session_bar_mode(position, bars)

        assert settlement.exit_price == Decimal("9.20")  # worse than stop; real gap
        assert settlement.result == Result.LOSS
        assert settlement.settled_by == SettledBy.STOP

    def test_bar_opening_below_stop_exits_at_open_even_if_high_reaches_target(self, position):
        # The open is the bar's first price: the position was gone before any rally.
        bars = [bar(9, 30, "9.20", "10.70", "9.10", "10.65")]

        settlement = settle_session_bar_mode(position, bars)

        assert settlement.exit_price == Decimal("9.20")
        assert settlement.result == Result.LOSS
        assert settlement.settled_by == SettledBy.STOP

    def test_bar_opening_above_target_exits_at_that_bars_open(self, position):
        bars = [bar(9, 30, "10.80", "10.90", "10.70", "10.85")]  # gapped through the target

        settlement = settle_session_bar_mode(position, bars)

        assert settlement.exit_price == Decimal("10.80")  # better than target; real gap
        assert settlement.result == Result.WIN
        assert settlement.settled_by == SettledBy.TARGET

    def test_earliest_deciding_bar_settles(self, position):
        bars = [
            bar(9, 30, "9.80", "9.90", "9.45", "9.60"),   # stop here
            bar(9, 31, "9.60", "10.70", "9.55", "10.65"),  # target later
        ]

        settlement = settle_session_bar_mode(position, bars)

        assert settlement.result == Result.LOSS
        assert settlement.ts == bars[0].ts

    def test_nothing_hit_and_not_expiry_stays_open(self, position):
        bars = [bar(9, 30, "10.00", "10.20", "9.90", "10.10")]

        assert settle_session_bar_mode(position, bars) is None

    def test_no_bars_stays_open(self, position):
        assert settle_session_bar_mode(position, []) is None


class TestExpiry:
    """End of day 3: exit at final price; within ±1% of fill = breakeven."""

    def test_final_close_above_the_band_is_a_win(self, position):
        bars = [bar(15, 59, "10.10", "10.15", "10.05", "10.15")]

        settlement = settle_session_bar_mode(position, bars, expiry=True)

        assert settlement.exit_price == Decimal("10.15")
        assert settlement.result == Result.WIN
        assert settlement.settled_by == SettledBy.EXPIRY
        assert settlement.ts == bars[0].ts

    def test_final_close_below_the_band_is_a_loss(self, position):
        bars = [bar(15, 59, "9.85", "9.88", "9.80", "9.85")]

        settlement = settle_session_bar_mode(position, bars, expiry=True)

        assert settlement.result == Result.LOSS

    def test_final_close_inside_the_band_is_breakeven(self, position):
        bars = [bar(15, 59, "10.05", "10.08", "10.02", "10.05")]

        settlement = settle_session_bar_mode(position, bars, expiry=True)

        assert settlement.result == Result.BREAKEVEN

    def test_exactly_one_percent_above_fill_is_still_breakeven(self, position):
        bars = [bar(15, 59, "10.10", "10.10", "10.05", "10.10")]

        settlement = settle_session_bar_mode(position, bars, expiry=True)

        assert settlement.exit_price == Decimal("10.10")
        assert settlement.result == Result.BREAKEVEN

    def test_expiry_with_no_bars_stays_unsettled_for_the_caller_to_resolve(self, position):
        # Halt / missing data: the engine cannot price an exit it cannot see.
        assert settle_session_bar_mode(position, [], expiry=True) is None


class TestFillBarOrderingAmbiguity:
    """The bar that filled the position: bar mode cannot order prints inside it.

    Conservative rule: on a touch-fill bar, a target touch does NOT count (it may
    have printed before the fill) but a stop touch DOES. Gap-open fills are exempt:
    the open is the session's first print, so everything in that bar is post-fill.
    """

    def touch_filled(self, fill_ts: datetime) -> Position:
        return Position(
            fill=Fill(price=Decimal("10.00"), ts=fill_ts, at_open=False),
            stop=Decimal("9.50"),
            target=Decimal("10.60"),
        )

    def test_bars_before_the_fill_are_ignored(self):
        fill_ts = datetime(2025, 6, 3, 10, 0, tzinfo=ET)
        bars = [
            bar(9, 30, "9.40", "9.45", "9.35", "9.40"),    # stop territory, pre-fill
            bar(10, 0, "10.05", "10.10", "10.00", "10.05"),  # the fill bar
        ]

        assert settle_session_bar_mode(self.touch_filled(fill_ts), bars) is None

    def test_target_touch_on_the_touch_fill_bar_does_not_win(self):
        fill_ts = datetime(2025, 6, 3, 10, 0, tzinfo=ET)
        bars = [bar(10, 0, "10.30", "10.70", "10.00", "10.50")]

        assert settle_session_bar_mode(self.touch_filled(fill_ts), bars) is None

    def test_stop_touch_on_the_touch_fill_bar_is_a_loss(self):
        fill_ts = datetime(2025, 6, 3, 10, 0, tzinfo=ET)
        bars = [bar(10, 0, "10.05", "10.10", "9.45", "9.60")]

        settlement = settle_session_bar_mode(self.touch_filled(fill_ts), bars)

        assert settlement.result == Result.LOSS
        assert settlement.settled_by == SettledBy.STOP

    def test_target_touch_after_a_gap_open_fill_does_win(self):
        fill_ts = datetime(2025, 6, 3, 9, 30, tzinfo=ET)
        position = Position(
            fill=Fill(price=Decimal("9.80"), ts=fill_ts, at_open=True),
            stop=Decimal("9.50"),
            target=Decimal("10.60"),
        )
        bars = [bar(9, 30, "9.80", "10.65", "9.75", "10.60")]

        settlement = settle_session_bar_mode(position, bars)

        assert settlement.result == Result.WIN
        assert settlement.settled_by == SettledBy.TARGET
