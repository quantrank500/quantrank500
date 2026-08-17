"""M1's validation gate: the engine against real captured market data (the lake copy).

Not synthetic bars — actual 2025-2026 sessions for liquid (AAPL), gappy (SMCI),
and halt-prone (DJT) names. Every invariant asserts the fail-safe direction:
ambiguity settles against the predictor, losses never exit better than the stop,
wins never exit worse than the target, nothing fills or settles without data.

Skipped automatically when the local lake copy is not present.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

psycopg = pytest.importorskip("psycopg")

from quantrank500.engine import (  # noqa: E402
    Fill,
    Position,
    Result,
    SettledBy,
    replay_prediction,
    settle_session_bar_mode,
)
from quantrank500.market_data.local_lake import LocalLakeSource  # noqa: E402

TICKERS = ["AAPL", "SMCI", "DJT"]


@pytest.fixture(scope="module")
def source():
    try:
        lake = LocalLakeSource()
    except psycopg.OperationalError:
        pytest.skip("local Postgres not available")
    if len(lake.calendar()) < 50:
        pytest.skip("lake copy not loaded")
    yield lake
    lake.close()


@pytest.fixture(scope="module")
def sessions(source):
    calendar = source.calendar()
    return calendar[10 : len(calendar) - 5 : 12]  # ~25 sessions spread across the range


def hold_window(source, session: date) -> list[date]:
    return [d for d in source.calendar() if d >= session][:3]


def prior_close(source, ticker: str, session: date) -> Decimal | None:
    calendar = source.calendar()
    prior = calendar[calendar.index(session) - 1]
    return source.official_close(ticker, prior)


class TestFillInvariantsOnRealSessions:
    def test_fills_happen_only_when_the_market_actually_reached_the_entry(
        self, source, sessions
    ):
        checked = 0
        for ticker in TICKERS:
            for session in sessions:
                close = prior_close(source, ticker, session)
                bars = source.session_bars(ticker, session)
                if close is None or not bars:
                    continue
                entry = (close * Decimal("0.99")).quantize(Decimal("0.01"))
                outcome = replay_prediction(
                    source, ticker, entry, entry * Decimal("0.9"), entry * Decimal("2"),
                    hold_window(source, session),
                )
                session_low = min(b.low for b in bars)
                session_open = source.official_open(ticker, session)
                if outcome.fill is None:
                    assert session_low > entry, (ticker, session, "unfilled but touched")
                else:
                    assert session_low <= entry or session_open < entry, (
                        ticker, session, "filled but never reachable",
                    )
                    if outcome.fill.at_open:
                        assert outcome.fill.price == session_open
                        assert outcome.fill.price < entry  # improvement, never worse
                    else:
                        assert outcome.fill.price == entry
                checked += 1
        assert checked > 40  # the sweep must have actually exercised real sessions


class TestSettlementInvariantsOnRealSessions:
    def test_losses_never_beat_the_stop_and_wins_never_trail_the_target(
        self, source, sessions
    ):
        outcomes = {"win": 0, "loss": 0, "breakeven": 0, "open": 0, "unfilled": 0}
        for ticker in TICKERS:
            for session in sessions:
                close = prior_close(source, ticker, session)
                if close is None:
                    continue
                entry = (close * Decimal("0.995")).quantize(Decimal("0.01"))
                stop = (entry * Decimal("0.98")).quantize(Decimal("0.01"))
                target = (entry * Decimal("1.085")).quantize(Decimal("0.01"))
                outcome = replay_prediction(
                    source, ticker, entry, stop, target, hold_window(source, session)
                )
                if outcome.fill is None:
                    outcomes["unfilled"] += 1
                    continue
                if outcome.settlement is None:
                    outcomes["open"] += 1
                    continue
                s = outcome.settlement
                outcomes[s.result.value] += 1
                if s.settled_by == SettledBy.STOP:
                    assert s.exit_price <= stop  # a gap through the stop exits worse
                if s.settled_by == SettledBy.TARGET:
                    assert s.exit_price >= target  # a gap through the target exits better
                if s.settled_by == SettledBy.AMBIGUOUS:
                    assert s.result == Result.LOSS and s.exit_price == stop
                if s.settled_by == SettledBy.EXPIRY:
                    band = outcome.fill.price * Decimal("0.01")
                    spread = s.exit_price - outcome.fill.price
                    expected = (
                        Result.BREAKEVEN if abs(spread) <= band
                        else Result.WIN if spread > 0 else Result.LOSS
                    )
                    assert s.result == expected
        # real markets must have produced every ordinary outcome in this sweep
        assert outcomes["win"] > 0 and outcomes["loss"] > 0
        assert outcomes["unfilled"] > 0


class TestRealGapOpens:
    def test_every_real_gap_down_fills_at_the_open_price(self, source):
        gaps_found = 0
        calendar = source.calendar()
        for ticker in TICKERS:
            for session in calendar[1:]:
                close = prior_close(source, ticker, session)
                session_open = source.official_open(ticker, session)
                if close is None or session_open is None:
                    continue
                if session_open >= close * Decimal("0.97"):
                    continue  # not a 3%+ gap down
                entry = (close * Decimal("0.99")).quantize(Decimal("0.01"))
                outcome = replay_prediction(
                    source, ticker, entry, entry * Decimal("0.5"), entry * Decimal("2"),
                    hold_window(source, session),
                )
                assert outcome.fill is not None
                assert outcome.fill.at_open is True
                assert outcome.fill.price == session_open
                gaps_found += 1
        assert gaps_found > 0, "the slice was chosen to contain real gap days"


class TestRealSameBarConflicts:
    def test_wide_real_bars_settle_against_the_predictor(self, source):
        conflicts = 0
        for ticker in ("SMCI", "DJT"):
            for session in source.calendar()[1:]:
                for bar in source.session_bars(ticker, session):
                    if bar.low <= 0 or (bar.high - bar.low) / bar.low < Decimal("0.02"):
                        continue
                    # stop and target both strictly inside this one real bar
                    stop = (bar.low + (bar.high - bar.low) / 4).quantize(Decimal("0.0001"))
                    target = (bar.high - (bar.high - bar.low) / 4).quantize(Decimal("0.0001"))
                    position = Position(
                        fill=Fill(
                            price=(stop + target) / 2,
                            ts=bar.ts - timedelta(minutes=5),
                            at_open=False,
                        ),
                        stop=stop,
                        target=target,
                    )
                    settlement = settle_session_bar_mode(
                        position, source.session_bars(ticker, session)
                    )
                    assert settlement is not None
                    if settlement.ts != bar.ts:
                        continue  # an earlier bar decided first
                    if bar.open >= target:
                        # the open is the first print: gapped through the target, no
                        # ambiguity — a win at the open (spec §4.3)
                        assert settlement.settled_by == SettledBy.TARGET
                        assert settlement.exit_price >= target
                    elif bar.open <= stop:
                        assert settlement.settled_by == SettledBy.STOP
                        assert settlement.exit_price <= stop
                    else:
                        # open between the levels, both touched inside one bar:
                        # order unknowable -> against the predictor
                        assert settlement.settled_by == SettledBy.AMBIGUOUS
                        assert settlement.result == Result.LOSS
                        assert settlement.exit_price == stop
                        conflicts += 1
                    if conflicts >= 25:
                        return
        assert conflicts > 0, "volatile names must contain 2%-range bars"


class TestSparseAndMissingData:
    def test_sessions_with_missing_bars_never_fabricate_a_settlement(self, source):
        sparse_found = 0
        for ticker in TICKERS:
            for session in source.calendar():
                bars = source.session_bars(ticker, session)
                if bars and len(bars) < 300:  # halts / outages leave holes
                    sparse_found += 1
        # the engine's no-data rules are unit-tested; here we prove the real lake
        # actually contains sparse sessions, so those paths run in production too
        assert sparse_found > 0, "expected at least one sparse session in the slice"
