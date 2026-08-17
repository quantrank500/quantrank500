"""Derived statistics (spec §5). Never stored; computed from settlements at query time."""

from decimal import Decimal

from quantrank500.stats import (
    breakeven_rate,
    breakeven_win_rate,
    fill_rate,
    mean,
    pf_lower_bound,
    profit_factor,
    qualifies,
    ticker_concentration,
    win_rate,
)


class TestWinRate:
    def test_unresolved_settlements_are_excluded_from_all_statistics(self):
        results = ["win", "win", "loss", "breakeven", "unresolved"]

        assert win_rate(results) == Decimal("0.5")  # 2 of 4 decided

    def test_no_decided_settlements_is_none(self):
        assert win_rate([]) is None
        assert win_rate(["unresolved"]) is None


class TestProfitFactor:
    def test_gross_wins_over_gross_losses(self):
        pnls = [Decimal("30"), Decimal("-10"), Decimal("-5")]

        assert profit_factor(pnls) == Decimal("2")

    def test_no_losses_is_none_never_infinity(self):
        assert profit_factor([Decimal("30")]) is None


class TestBreakevenRate:
    def test_breakevens_over_decided(self):
        results = ["win", "loss", "breakeven", "breakeven", "unresolved"]

        assert breakeven_rate(results) == Decimal("0.5")  # 2 of 4 decided

    def test_no_decided_settlements_is_none(self):
        assert breakeven_rate(["unresolved"]) is None


class TestBreakevenWinRate:
    def test_avg_loss_over_avg_win_plus_avg_loss(self):
        # avg win 30, avg loss 10 -> needs 25% to break even
        pnls = [Decimal("30"), Decimal("30"), Decimal("-10"), Decimal("-10")]

        assert breakeven_win_rate(pnls) == Decimal("0.25")

    def test_big_wins_small_losses_need_a_tiny_win_rate(self):
        # avg win 95, avg loss 5 -> 5% needed: how a 6% win rate stays profitable
        pnls = [Decimal("95"), Decimal("-5")]

        assert breakeven_win_rate(pnls) == Decimal("0.05")

    def test_no_losses_needs_nothing(self):
        assert breakeven_win_rate([Decimal("30")]) == Decimal("0")

    def test_no_wins_needs_everything(self):
        assert breakeven_win_rate([Decimal("-30")]) == Decimal("1")

    def test_no_decided_trades_is_none(self):
        assert breakeven_win_rate([]) is None


class TestMean:
    def test_plain_average(self):
        assert mean([Decimal("0.01"), Decimal("0.03")]) == Decimal("0.02")

    def test_empty_is_none(self):
        assert mean([]) is None


class TestFillRate:
    def test_filled_over_filled_plus_unfilled(self):
        assert fill_rate(filled=3, unfilled=1) == Decimal("0.75")

    def test_no_history_is_none(self):
        assert fill_rate(filled=0, unfilled=0) is None


class TestTickerConcentration:
    def test_largest_single_ticker_share(self):
        assert ticker_concentration(["AAPL", "AAPL", "NVDA", "TSLA"]) == Decimal("0.5")


class TestQualification:
    """Verified + >= 33 settled + fill rate >= 50% + no ticker > 30% + PF >= 1.25."""

    def boundary_kwargs(self):
        return dict(
            verified=True,
            settled=33,
            fill_rate=Decimal("0.50"),
            profit_factor=Decimal("1.25"),
            max_ticker_share=Decimal("0.30"),
        )

    def test_exactly_at_every_boundary_qualifies(self):
        assert qualifies(**self.boundary_kwargs()) is True

    def test_each_failed_criterion_disqualifies(self):
        for failing in (
            {"verified": False},
            {"settled": 32},
            {"fill_rate": Decimal("0.49")},
            {"profit_factor": Decimal("1.24")},
            {"max_ticker_share": Decimal("0.31")},
        ):
            assert qualifies(**self.boundary_kwargs() | failing) is False

    def test_a_perfect_record_with_no_losses_qualifies(self):
        # profit factor None means "no losses yet" -> displays ">X", qualifies
        assert qualifies(**self.boundary_kwargs() | {"profit_factor": None}) is True


class TestPfLowerBound:
    MIXED = [Decimal(p) for p in ("30", "30", "-10", "25", "-15", "20", "-5", "30", "-10", "15")]

    def test_deterministic_for_a_given_seed(self):
        assert pf_lower_bound(self.MIXED) == pf_lower_bound(self.MIXED)

    def test_bound_is_at_or_below_the_point_estimate(self):
        assert pf_lower_bound(self.MIXED) <= profit_factor(self.MIXED)

    def test_no_losses_is_none(self):
        assert pf_lower_bound([Decimal("30"), Decimal("20")]) is None
