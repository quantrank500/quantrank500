from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from quantrank500.market_data import Bar, FakeSource, Trade

ET = ZoneInfo("America/New_York")


def bar(hour: int, minute: int, o="10.00", h="10.10", lo="9.90", c="10.05", vol=1000) -> Bar:
    return Bar(
        ts=datetime(2025, 6, 2, hour, minute, tzinfo=ET),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(lo),
        close=Decimal(c),
        volume=vol,
    )


def test_session_bars_are_chronological_even_if_added_out_of_order():
    late, early = bar(10, 0), bar(9, 30)
    source = FakeSource(bars={("ACME", date(2025, 6, 2)): [late, early]})

    assert source.session_bars("ACME", date(2025, 6, 2)) == [early, late]


def test_session_bars_for_unknown_ticker_or_session_is_empty():
    source = FakeSource()

    assert source.session_bars("ACME", date(2025, 6, 2)) == []


def test_official_close_returns_none_when_unknown():
    source = FakeSource(closes={("ACME", date(2025, 6, 2)): Decimal("10.05")})

    assert source.official_close("ACME", date(2025, 6, 2)) == Decimal("10.05")
    assert source.official_close("ACME", date(2025, 6, 3)) is None


def test_official_open_returns_none_when_unknown():
    source = FakeSource(opens={("ACME", date(2025, 6, 2)): Decimal("9.98")})

    assert source.official_open("ACME", date(2025, 6, 2)) == Decimal("9.98")
    assert source.official_open("ZZZZ", date(2025, 6, 2)) is None


def test_session_trades_is_none_for_a_bar_mode_source():
    source = FakeSource()

    assert source.session_trades("ACME", date(2025, 6, 2)) is None


def test_session_trades_returned_chronologically_when_provided():
    late = Trade(ts=datetime(2025, 6, 2, 9, 31, tzinfo=ET), price=Decimal("10.01"), size=100)
    early = Trade(ts=datetime(2025, 6, 2, 9, 30, tzinfo=ET), price=Decimal("10.00"), size=200)
    source = FakeSource(trades={("ACME", date(2025, 6, 2)): [late, early]})

    assert source.session_trades("ACME", date(2025, 6, 2)) == [early, late]


def test_calendar_is_sorted_unique_session_dates():
    source = FakeSource(sessions=[date(2025, 6, 3), date(2025, 6, 2), date(2025, 6, 3)])

    assert source.calendar() == [date(2025, 6, 2), date(2025, 6, 3)]
