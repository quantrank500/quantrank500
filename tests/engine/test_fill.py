"""Bar-mode fill rules (spec §4.2): touch rule, gap improvement, unfilled sessions."""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from quantrank500.engine import fill_bar_mode, volatile_open_flag
from quantrank500.market_data import Bar

ET = ZoneInfo("America/New_York")


def bar(hour: int, minute: int, o: str, h: str, lo: str, c: str, volume: int = 1000) -> Bar:
    return Bar(
        ts=datetime(2025, 6, 2, hour, minute, tzinfo=ET),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(lo),
        close=Decimal(c),
        volume=volume,
    )


class TestFill:
    def test_no_bar_reaches_entry_means_unfilled(self):
        bars = [bar(9, 30, "10.50", "10.60", "10.40", "10.55")]

        assert fill_bar_mode(Decimal("10.00"), bars, official_open=Decimal("10.50")) is None

    def test_bar_low_touching_entry_fills_at_entry(self):
        bars = [
            bar(9, 30, "10.50", "10.60", "10.40", "10.55"),
            bar(9, 47, "10.20", "10.25", "10.00", "10.10"),
        ]

        fill = fill_bar_mode(Decimal("10.00"), bars, official_open=Decimal("10.50"))

        assert fill.price == Decimal("10.00")
        assert fill.ts == bars[1].ts

    def test_earliest_touching_bar_wins(self):
        bars = [
            bar(9, 40, "10.10", "10.15", "9.99", "10.05"),
            bar(10, 15, "10.05", "10.10", "9.95", "10.00"),
        ]

        fill = fill_bar_mode(Decimal("10.00"), bars, official_open=Decimal("10.10"))

        assert fill.ts == bars[0].ts

    def test_session_opening_below_entry_fills_at_official_open(self):
        bars = [bar(9, 30, "9.80", "9.90", "9.75", "9.85")]

        fill = fill_bar_mode(Decimal("10.00"), bars, official_open=Decimal("9.80"))

        assert fill.price == Decimal("9.80")  # gap improvement
        assert fill.ts == bars[0].ts

    def test_missing_official_open_falls_back_to_first_bar_open(self):
        bars = [bar(9, 30, "9.80", "9.90", "9.75", "9.85")]

        fill = fill_bar_mode(Decimal("10.00"), bars, official_open=None)

        assert fill.price == Decimal("9.80")

    def test_no_bars_at_all_means_unfilled_even_if_open_was_below_entry(self):
        # No recorded trading -> nothing verifiable to fill against. Fail safe.
        assert fill_bar_mode(Decimal("10.00"), [], official_open=Decimal("9.80")) is None


class TestVolatileOpenFlag:
    def test_first_bar_range_above_3pct_of_prior_close_is_flagged(self):
        bars = [bar(9, 30, "10.00", "10.40", "10.00", "10.20")]  # range 0.40

        assert volatile_open_flag(bars, prior_close=Decimal("10.00")) is True

    def test_first_bar_range_at_exactly_3pct_is_not_flagged(self):
        bars = [bar(9, 30, "10.00", "10.30", "10.00", "10.20")]  # range 0.30

        assert volatile_open_flag(bars, prior_close=Decimal("10.00")) is False

    def test_no_bars_is_not_flagged(self):
        assert volatile_open_flag([], prior_close=Decimal("10.00")) is False
