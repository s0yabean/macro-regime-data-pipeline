"""Long-only SMA crossover.

Signal clock vs execution clock: a cross is detected on the close of bar i, and the
trade fills at the close of bar i+1. Filling on the same close that produced the
signal would assume you could see the close and trade at it simultaneously.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple


@dataclass
class Trade:
    entry_ts: int
    exit_ts: int
    entry_price: float
    exit_price: float
    pnl_pct: float


def _moving_average(values: Sequence[float], window: int) -> List[float]:
    if window <= 0:
        raise ValueError("window must be > 0")
    output: List[float] = [float("nan")] * len(values)
    running_sum = 0.0
    for i, value in enumerate(values):
        running_sum += value
        if i >= window:
            running_sum -= values[i - window]
        if i >= window - 1:
            output[i] = running_sum / window
    return output


def _summary(trades: List[Trade], closes: Sequence[float]) -> Dict[str, float]:
    wins = sum(1 for t in trades if t.pnl_pct > 0)
    growth = 1.0
    for t in trades:
        growth *= 1.0 + t.pnl_pct / 100.0
    buy_hold = (closes[-1] / closes[0] - 1.0) * 100.0 if closes else 0.0
    return {
        "trades": float(len(trades)),
        "wins": float(wins),
        "losses": float(len(trades) - wins),
        "win_rate": round(wins / len(trades) * 100.0, 4) if trades else 0.0,
        "total_return_pct": round((growth - 1.0) * 100.0, 4),
        "buy_and_hold_return_pct": round(buy_hold, 4),
    }


def run_sma_cross(
    bars: Sequence[Tuple[int, float]],
    fast_window: int = 20,
    slow_window: int = 50,
) -> Dict[str, float]:
    closes = [float(c) for _, c in bars]
    if len(bars) < max(fast_window, slow_window) + 2:
        return _summary([], closes)

    timestamps = [int(ts) for ts, _ in bars]
    fast_ma = _moving_average(closes, fast_window)
    slow_ma = _moving_average(closes, slow_window)

    in_position = False
    entry_ts, entry_price = 0, 0.0
    trades: List[Trade] = []

    # Signal on bar i, fill on bar i + 1.
    for i in range(1, len(closes) - 1):
        prev_fast, prev_slow, curr_fast, curr_slow = fast_ma[i - 1], slow_ma[i - 1], fast_ma[i], slow_ma[i]
        if any(x != x for x in (prev_fast, prev_slow, curr_fast, curr_slow)):  # NaN warm-up
            continue
        cross_up = prev_fast <= prev_slow and curr_fast > curr_slow
        cross_down = prev_fast >= prev_slow and curr_fast < curr_slow
        fill_ts, fill_px = timestamps[i + 1], closes[i + 1]

        if cross_up and not in_position:
            in_position, entry_ts, entry_price = True, fill_ts, fill_px
        elif cross_down and in_position:
            trades.append(Trade(entry_ts, fill_ts, entry_price, fill_px, (fill_px / entry_price - 1.0) * 100.0))
            in_position = False

    if in_position:  # mark open position to the last close
        trades.append(Trade(entry_ts, timestamps[-1], entry_price, closes[-1], (closes[-1] / entry_price - 1.0) * 100.0))

    return _summary(trades, closes)
