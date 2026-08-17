"""M1 deliverable: replay a hypothetical prediction against real history.

    python scripts/replay_prediction.py --ticker SMCI --session 2025-06-02 \\
        --entry 42.00 --stop 40.00 --target 44.50

Prints what the engine decided: "filled 09:47, stopped 11:03 next day, -$14.20".
"""

import argparse
from datetime import date
from decimal import Decimal

from quantrank500.engine import replay_prediction
from quantrank500.engine.lifecycle import MAX_HOLD_SESSIONS
from quantrank500.market_data.local_lake import LocalLakeSource


def hold_window(calendar: list[date], target_session: date) -> list[date]:
    """The target session plus up to two more trading days, from the lake calendar."""
    upcoming = [s for s in calendar if s >= target_session]
    if not upcoming or upcoming[0] != target_session:
        raise SystemExit(f"{target_session} is not a trading session in the lake copy")
    return upcoming[:MAX_HOLD_SESSIONS]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--session", type=date.fromisoformat, required=True)
    parser.add_argument("--entry", type=Decimal, required=True)
    parser.add_argument("--stop", type=Decimal, required=True)
    parser.add_argument("--target", type=Decimal, required=True)
    args = parser.parse_args()

    source = LocalLakeSource()
    sessions = hold_window(source.calendar(), args.session)
    outcome = replay_prediction(
        source, args.ticker.upper(), args.entry, args.stop, args.target, sessions
    )
    source.close()

    plan = (
        f"{args.ticker.upper()} {args.session}:"
        f" entry {args.entry}, stop {args.stop}, target {args.target}"
    )
    if outcome.fill is None:
        print(f"{plan}\nEngine says: never filled -> unfilled (public, counts in Fill Rate)")
        return
    filled = f"filled {outcome.fill.ts:%Y-%m-%d %H:%M} at {outcome.fill.price}" + (
        " (gap improvement at the open)" if outcome.fill.at_open else ""
    )
    if outcome.settlement is None:
        print(f"{plan}\nEngine says: {filled}, but could not settle from available data")
        return
    s = outcome.settlement
    sign = "+" if outcome.pnl >= 0 else "-"
    print(
        f"{plan}\n"
        f"Engine says: {filled}, "
        f"{s.settled_by} {s.ts:%Y-%m-%d %H:%M} at {s.exit_price} "
        f"({s.result}), {sign}${abs(outcome.pnl):.2f} on $500"
    )


if __name__ == "__main__":
    main()
