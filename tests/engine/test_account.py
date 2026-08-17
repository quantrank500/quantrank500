"""The $500 account (spec §4.4): derived by replay, never stored.

Stored per-prediction PnL is standardized at $500 notional (the audit constant).
The account compounds: each position risks one third of the balance at fill time.
"""

from decimal import Decimal

from quantrank500.engine import replay_balance, standardized_pnl


class TestStandardizedPnl:
    def test_win_is_500_notional_times_the_return(self):
        # (10.30 - 10.00) / 10.00 = 3% -> 500 x 0.03 = 15
        assert standardized_pnl(Decimal("10.00"), Decimal("10.30")) == Decimal("15")

    def test_loss_is_negative(self):
        assert standardized_pnl(Decimal("10.00"), Decimal("9.40")) == Decimal("-30")


class TestReplayBalance:
    def test_no_settlements_is_the_starting_500(self):
        assert replay_balance([]) == Decimal("500")

    def test_single_win_adds_a_third_of_balance_times_return(self):
        # 500 + (500/3) x 0.03 = 505
        balance = replay_balance([(Decimal("10.00"), Decimal("10.30"))])

        assert balance == Decimal("505")

    def test_single_loss_subtracts(self):
        # 500 + (500/3) x (-0.06) = 490
        balance = replay_balance([(Decimal("10.00"), Decimal("9.40"))])

        assert balance == Decimal("490")

    def test_wins_compound_in_order(self):
        # 500 -> 505 -> 505 + (505/3) x 0.03 = 510.05
        balance = replay_balance(
            [
                (Decimal("10.00"), Decimal("10.30")),
                (Decimal("20.00"), Decimal("20.60")),
            ]
        )

        assert balance == Decimal("510.05")

    def test_a_breakeven_labeled_exit_still_moves_the_balance_by_actual_prices(self):
        # Expiry at +0.6% is labeled breakeven, but the account uses actual prices:
        # 500 + (500/3) x 0.006 = 501
        balance = replay_balance([(Decimal("10.00"), Decimal("10.06"))])

        assert balance == Decimal("501")
