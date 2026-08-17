"""Derived statistics (spec §5). Never stored; computed from settlements at query time.

Unresolved settlements are excluded from every statistic. A profit factor with no
losses is None — displayed as ">X", never infinity.
"""

import random
from decimal import Decimal

DECIDED = ("win", "loss", "breakeven")

# Qualification thresholds (spec §5). Fill rate / concentration are working defaults.
MIN_SETTLED = 33
MIN_FILL_RATE = Decimal("0.50")
MIN_PROFIT_FACTOR = Decimal("1.25")
MAX_TICKER_SHARE = Decimal("0.30")

BOOTSTRAP_RESAMPLES = 1000
CONFIDENCE_LOWER_TAIL = 0.025  # 95% two-sided


def breakeven_rate(results: list[str]) -> Decimal | None:
    """Breakevens dilute win rate without being losses; showing their share
    makes win rate interpretable."""
    decided = [r for r in results if r in DECIDED]
    if not decided:
        return None
    breakevens = sum(1 for r in decided if r == "breakeven")
    return Decimal(breakevens) / Decimal(len(decided))


def breakeven_win_rate(pnls: list[Decimal]) -> Decimal | None:
    """The win rate these win/loss sizes require just to break even:
    avg_loss / (avg_win + avg_loss). Comparing it with the actual win rate makes
    the edge visible — a 6% win rate can beat a 4% requirement."""
    wins = [p for p in pnls if p > 0]
    losses = [-p for p in pnls if p < 0]
    if not wins and not losses:
        return None
    avg_win = (sum(wins) / len(wins)) if wins else Decimal(0)
    avg_loss = (sum(losses) / len(losses)) if losses else Decimal(0)
    return avg_loss / (avg_win + avg_loss)


def mean(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    return sum(values) / Decimal(len(values))


def win_rate(results: list[str]) -> Decimal | None:
    decided = [r for r in results if r in DECIDED]
    if not decided:
        return None
    wins = sum(1 for r in decided if r == "win")
    return Decimal(wins) / Decimal(len(decided))


def profit_factor(pnls: list[Decimal]) -> Decimal | None:
    gross_wins = sum(p for p in pnls if p > 0)
    gross_losses = -sum(p for p in pnls if p < 0)
    if gross_losses == 0:
        return None
    return Decimal(gross_wins) / Decimal(gross_losses)


def fill_rate(filled: int, unfilled: int) -> Decimal | None:
    if filled + unfilled == 0:
        return None
    return Decimal(filled) / Decimal(filled + unfilled)


def ticker_concentration(tickers: list[str]) -> Decimal:
    if not tickers:
        return Decimal(0)
    heaviest = max(tickers.count(t) for t in set(tickers))
    return Decimal(heaviest) / Decimal(len(tickers))


def qualifies(
    verified: bool,
    settled: int,
    fill_rate: Decimal | None,
    profit_factor: Decimal | None,
    max_ticker_share: Decimal,
) -> bool:
    return (
        verified
        and settled >= MIN_SETTLED
        and fill_rate is not None
        and fill_rate >= MIN_FILL_RATE
        # None means no losses yet: better than any threshold
        and (profit_factor is None or profit_factor >= MIN_PROFIT_FACTOR)
        and max_ticker_share <= MAX_TICKER_SHARE
    )


def pf_lower_bound(pnls: list[Decimal], seed: int = 0) -> Decimal | None:
    """Lower 95% confidence bound of the profit factor, percentile bootstrap.

    Deterministic for a given seed so ranks are reproducible from the export.
    """
    if profit_factor(pnls) is None:
        return None
    rng = random.Random(seed)
    resampled = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        sample = [pnls[rng.randrange(len(pnls))] for _ in pnls]
        resampled.append(profit_factor(sample))
    # a no-loss resample is an infinitely good draw: sort it to the top
    ordered = sorted(resampled, key=lambda pf: (pf is None, pf))
    return ordered[int(BOOTSTRAP_RESAMPLES * CONFIDENCE_LOWER_TAIL)]
