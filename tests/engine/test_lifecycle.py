"""Full prediction lifecycle over a MarketDataSource: fill day 1, settle days 1-3."""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from quantrank500.engine import Result, SettledBy, replay_prediction
from quantrank500.market_data import Bar, FakeSource

ET = ZoneInfo("America/New_York")
DAY1, DAY2, DAY3 = date(2025, 6, 2), date(2025, 6, 3), date(2025, 6, 4)


def bar(day: date, hour: int, minute: int, o: str, h: str, lo: str, c: str) -> Bar:
    return Bar(
        ts=datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(lo),
        close=Decimal(c),
        volume=1000,
    )


def quiet_day(day: date) -> list[Bar]:
    return [bar(day, 9, 30, "10.20", "10.30", "10.15", "10.25")]


def test_never_touched_means_unfilled():
    source = FakeSource(
        bars={("ACME", d): quiet_day(d) for d in (DAY1, DAY2, DAY3)},
        opens={("ACME", DAY1): Decimal("10.20")},
    )

    outcome = replay_prediction(
        source, "ACME", entry=Decimal("10.00"), stop=Decimal("9.50"),
        target=Decimal("10.60"), sessions=[DAY1, DAY2, DAY3],
    )

    assert outcome.fill is None
    assert outcome.settlement is None
    assert outcome.pnl is None


def test_fill_day_one_target_day_two():
    source = FakeSource(
        bars={
            ("ACME", DAY1): [
                bar(DAY1, 9, 30, "10.20", "10.30", "10.15", "10.25"),
                bar(DAY1, 9, 47, "10.10", "10.12", "10.00", "10.05"),  # touch fill
            ],
            ("ACME", DAY2): [
                bar(DAY2, 11, 3, "10.50", "10.65", "10.45", "10.60"),  # target
            ],
        },
        opens={("ACME", DAY1): Decimal("10.20")},
    )

    outcome = replay_prediction(
        source, "ACME", entry=Decimal("10.00"), stop=Decimal("9.50"),
        target=Decimal("10.60"), sessions=[DAY1, DAY2, DAY3],
    )

    assert outcome.fill.price == Decimal("10.00")
    assert outcome.fill.ts == datetime(2025, 6, 2, 9, 47, tzinfo=ET)
    assert outcome.settlement.result == Result.WIN
    assert outcome.settlement.ts == datetime(2025, 6, 3, 11, 3, tzinfo=ET)
    assert outcome.pnl == Decimal("30")  # 500 x 6%


def test_same_day_fill_and_stop():
    source = FakeSource(
        bars={
            ("ACME", DAY1): [
                bar(DAY1, 9, 47, "10.10", "10.12", "10.00", "10.05"),  # touch fill
                bar(DAY1, 11, 3, "9.60", "9.65", "9.45", "9.50"),      # stop
            ],
        },
        opens={("ACME", DAY1): Decimal("10.10")},
    )

    outcome = replay_prediction(
        source, "ACME", entry=Decimal("10.00"), stop=Decimal("9.50"),
        target=Decimal("10.60"), sessions=[DAY1, DAY2, DAY3],
    )

    assert outcome.settlement.result == Result.LOSS
    assert outcome.settlement.settled_by == SettledBy.STOP
    assert outcome.pnl == Decimal("-25")  # 500 x -5%


def test_nothing_hit_expires_at_day_three_close():
    source = FakeSource(
        bars={
            ("ACME", DAY1): [bar(DAY1, 9, 47, "10.10", "10.12", "10.00", "10.05")],
            ("ACME", DAY2): quiet_day(DAY2),
            ("ACME", DAY3): [bar(DAY3, 15, 59, "10.05", "10.08", "10.02", "10.05")],
        },
        opens={("ACME", DAY1): Decimal("10.10")},
    )

    outcome = replay_prediction(
        source, "ACME", entry=Decimal("10.00"), stop=Decimal("9.50"),
        target=Decimal("10.60"), sessions=[DAY1, DAY2, DAY3],
    )

    assert outcome.settlement.settled_by == SettledBy.EXPIRY
    assert outcome.settlement.result == Result.BREAKEVEN
    assert outcome.settlement.exit_price == Decimal("10.05")


def test_gap_open_below_entry_fills_at_the_open():
    source = FakeSource(
        bars={
            ("ACME", DAY1): [bar(DAY1, 9, 30, "9.80", "9.90", "9.75", "9.85")],
            ("ACME", DAY2): quiet_day(DAY2),
            ("ACME", DAY3): quiet_day(DAY3),
        },
        opens={("ACME", DAY1): Decimal("9.80")},
    )

    outcome = replay_prediction(
        source, "ACME", entry=Decimal("10.00"), stop=Decimal("9.50"),
        target=Decimal("10.60"), sessions=[DAY1, DAY2, DAY3],
    )

    assert outcome.fill.price == Decimal("9.80")
    assert outcome.fill.at_open is True


def test_missing_data_on_expiry_day_leaves_settlement_unresolved():
    source = FakeSource(
        bars={
            ("ACME", DAY1): [bar(DAY1, 9, 47, "10.10", "10.12", "10.00", "10.05")],
            ("ACME", DAY2): quiet_day(DAY2),
            # DAY3: no bars at all (halt / missing data)
        },
        opens={("ACME", DAY1): Decimal("10.10")},
    )

    outcome = replay_prediction(
        source, "ACME", entry=Decimal("10.00"), stop=Decimal("9.50"),
        target=Decimal("10.60"), sessions=[DAY1, DAY2, DAY3],
    )

    assert outcome.fill is not None
    assert outcome.settlement is None  # the caller decides halt handling (spec §4.3)
    assert outcome.pnl is None
