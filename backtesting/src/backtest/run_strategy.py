#!/usr/bin/env python3
"""Run a strategy on local SQLite data.

Two data sources:
- --source bars  (default): ohlc_bars in the backtesting DB (CoinGecko ingest or the
  synthetic sample from src/sample/make_synthetic_bars.py)
- --source macro: daily closes from the macro pipeline DB (raw_observations), e.g.
  YF:BTC-USD, YF:SPY. Rows are read by available_at, never past --end-date.

Examples:
  python backtesting/src/backtest/run_strategy.py --strategy sma_cross --coin-id synth-1 \
      --start-date 2024-01-01 --end-date 2025-12-31
  python backtesting/src/backtest/run_strategy.py --strategy median_long_short \
      --start-date 2024-01-01 --end-date 2025-12-31 --lookback 1 --hold 1
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.backtest.strategies.median_long_short import run_median_long_short  # noqa: E402
from src.backtest.strategies.sma_cross import run_sma_cross  # noqa: E402


def to_unix_ms(date_text: str, end_of_day: bool = False) -> int:
    dt = datetime.strptime(date_text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    return int(dt.timestamp() * 1000)


def bars_from_ohlc(conn: sqlite3.Connection, coin_id: str, timeframe: str, start: str, end: str) -> List[Tuple[int, float]]:
    rows = conn.execute(
        "SELECT ts, close FROM ohlc_bars WHERE coin_id = ? AND timeframe = ? AND ts BETWEEN ? AND ? ORDER BY ts",
        (coin_id, timeframe, to_unix_ms(start), to_unix_ms(end, True)),
    ).fetchall()
    return [(int(ts), float(c)) for ts, c in rows]


def bars_from_macro(conn: sqlite3.Connection, series_id: str, start: str, end: str) -> List[Tuple[int, float]]:
    rows = conn.execute(
        """
        SELECT observation_date, value FROM raw_observations
        WHERE series_id = ? AND observation_date >= ? AND available_at <= ?
        ORDER BY observation_date
        """,
        (series_id, start, end),
    ).fetchall()
    return [(to_unix_ms(d), float(v)) for d, v in rows]


def aligned_universe(conn: sqlite3.Connection, timeframe: str, start: str, end: str) -> Dict[str, List[float]]:
    """Close series for every coin, restricted to timestamps all coins share."""
    rows = conn.execute(
        "SELECT coin_id, ts, close FROM ohlc_bars WHERE timeframe = ? AND ts BETWEEN ? AND ? ORDER BY ts",
        (timeframe, to_unix_ms(start), to_unix_ms(end, True)),
    ).fetchall()
    by_coin: Dict[str, Dict[int, float]] = {}
    for coin, ts, close in rows:
        by_coin.setdefault(coin, {})[int(ts)] = float(close)
    if not by_coin:
        return {}
    shared = sorted(set.intersection(*(set(v) for v in by_coin.values())))
    return {coin: [series[t] for t in shared] for coin, series in by_coin.items()}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run a backtest on local SQLite data")
    p.add_argument("--strategy", default="sma_cross", choices=["sma_cross", "median_long_short"])
    p.add_argument("--source", default="bars", choices=["bars", "macro"])
    p.add_argument("--coin-id", default="", help="ohlc_bars coin_id (sma_cross, --source bars)")
    p.add_argument("--series-id", default="", help="raw_observations series_id (sma_cross, --source macro)")
    p.add_argument("--timeframe", default="1d", choices=["1h", "1d"])
    p.add_argument("--start-date", required=True)
    p.add_argument("--end-date", required=True)
    p.add_argument("--db-path", default=str(ROOT_DIR / "data" / "marketdata.sqlite"))
    p.add_argument("--fast-window", type=int, default=20)
    p.add_argument("--slow-window", type=int, default=50)
    p.add_argument("--lookback", type=int, default=1, help="median_long_short: ranking lookback in bars")
    p.add_argument("--hold", type=int, default=1, help="median_long_short: holding period in bars")
    p.add_argument("--margin-pct", type=float, default=0.0, help="median_long_short: band around the median")
    p.add_argument("--exclude", default="", help="median_long_short: comma list of coin_ids left out of the universe")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if not Path(args.db_path).exists():
        raise SystemExit(f"Database not found: {args.db_path}")
    conn = sqlite3.connect(f"file:{args.db_path}?mode=ro", uri=True)
    params: Dict[str, object]
    try:
        if args.strategy == "sma_cross":
            if args.fast_window >= args.slow_window:
                raise SystemExit("fast-window must be smaller than slow-window")
            if args.source == "macro":
                bars = bars_from_macro(conn, args.series_id, args.start_date, args.end_date)
            else:
                bars = bars_from_ohlc(conn, args.coin_id, args.timeframe, args.start_date, args.end_date)
            if not bars:
                raise SystemExit("No bars found for the requested asset and range")
            result = run_sma_cross(bars, args.fast_window, args.slow_window)
            params = {"fast_window": args.fast_window, "slow_window": args.slow_window, "bar_count": len(bars)}
        else:
            universe = aligned_universe(conn, args.timeframe, args.start_date, args.end_date)
            if len(universe) < 2:
                raise SystemExit("median_long_short needs at least two coins in ohlc_bars")
            exclude = [x.strip() for x in args.exclude.split(",") if x.strip()]
            result = run_median_long_short(universe, args.lookback, args.hold, args.margin_pct, exclude)
            params = {"lookback": args.lookback, "hold": args.hold, "margin_pct": args.margin_pct,
                      "universe_size": len(universe) - len(set(exclude) & set(universe)),
                      "bar_count": len(next(iter(universe.values())))}
    finally:
        conn.close()

    print(json.dumps({
        "strategy": args.strategy,
        "source": args.source,
        "asset": args.coin_id or args.series_id or "universe",
        "timeframe": args.timeframe,
        "start_date": args.start_date,
        "end_date": args.end_date,
        "params": params,
        "result": result,
    }, indent=2))


if __name__ == "__main__":
    main()
