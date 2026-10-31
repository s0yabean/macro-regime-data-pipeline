"""Cross-sectional median long/short.

Each rebalance period, rank the universe by its trailing return. Go long the names
above the cross-sectional median (plus an optional margin) and short the names below
it (minus the margin), with an equal number of longs and shorts and equal weights.
Hold for one period, then re-rank.

Timing: the ranking uses closes up to and including bar t; the position is held from
the close of bar t to the close of bar t + hold. No future bar is read when ranking.

Returns are reported as fractions of gross exposure (no position sizing, no costs).
"""

from __future__ import annotations

import statistics
from typing import Dict, List, Sequence


def run_median_long_short(
    closes: Dict[str, Sequence[float]],
    lookback: int = 1,
    hold: int = 1,
    margin_pct: float = 0.0,
    exclude: Sequence[str] = (),
) -> Dict[str, float]:
    """closes: {asset_id: [close_0, close_1, ...]} on a shared, aligned calendar."""
    assets = [a for a in closes if a not in set(exclude)]
    if len(assets) < 2:
        raise ValueError("need at least two assets")
    n_bars = min(len(closes[a]) for a in assets)
    period_returns: List[float] = []
    legs_per_period: List[int] = []

    for t in range(lookback, n_bars - hold, hold):
        trailing = {a: closes[a][t] / closes[a][t - lookback] - 1.0 for a in assets}
        med = statistics.median(trailing.values())
        m = margin_pct / 100.0
        longs = sorted((a for a in assets if trailing[a] > med + m), key=lambda a: -trailing[a])
        shorts = sorted((a for a in assets if trailing[a] < med - m), key=lambda a: trailing[a])
        k = min(len(longs), len(shorts))
        if k == 0:
            period_returns.append(0.0)
            legs_per_period.append(0)
            continue
        fwd = {a: closes[a][t + hold] / closes[a][t] - 1.0 for a in longs[:k] + shorts[:k]}
        long_ret = sum(fwd[a] for a in longs[:k]) / k
        short_ret = -sum(fwd[a] for a in shorts[:k]) / k
        period_returns.append((long_ret + short_ret) / 2.0)
        legs_per_period.append(2 * k)

    growth, peak, max_dd = 1.0, 1.0, 0.0
    for r in period_returns:
        growth *= 1.0 + r
        peak = max(peak, growth)
        max_dd = min(max_dd, growth / peak - 1.0)
    traded = [r for r, legs in zip(period_returns, legs_per_period) if legs]
    sd = statistics.pstdev(traded) if len(traded) > 1 else 0.0
    return {
        "periods": float(len(period_returns)),
        "traded_periods": float(len(traded)),
        "avg_legs": round(sum(legs_per_period) / len(legs_per_period), 2) if legs_per_period else 0.0,
        "hit_rate_pct": round(sum(1 for r in traded if r > 0) / len(traded) * 100.0, 2) if traded else 0.0,
        "mean_period_return_pct": round(statistics.mean(traded) * 100.0, 4) if traded else 0.0,
        "period_return_sd_pct": round(sd * 100.0, 4),
        "total_return_pct": round((growth - 1.0) * 100.0, 4),
        "max_drawdown_pct": round(max_dd * 100.0, 4),
    }
