#!/usr/bin/env python3
"""Write SYNTHETIC daily OHLC bars for a small made-up coin universe.

Lets the backtests run offline with no API key. Coin ids are synth-1 ... synth-N and
the prices are seeded random walks; none of it is market data.

    python backtesting/src/sample/make_synthetic_bars.py
"""

from __future__ import annotations

import argparse
import math
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.common.sqlite_store import apply_schema, connect, upsert_coins, upsert_ohlc_rows  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description="Create synthetic OHLC bars")
    p.add_argument("--db-path", default=str(ROOT_DIR / "data" / "marketdata.sqlite"))
    p.add_argument("--schema-path", default=str(ROOT_DIR / "sql" / "schema.sql"))
    p.add_argument("--coins", type=int, default=12)
    p.add_argument("--start-date", default="2024-01-01")
    p.add_argument("--days", type=int, default=730)
    p.add_argument("--seed", type=int, default=7)
    args = p.parse_args()

    rng = random.Random(args.seed)
    start = datetime.strptime(args.start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    conn = connect(args.db_path)
    apply_schema(conn, args.schema_path)
    coins = [{"id": f"synth-{i}", "symbol": f"SYN{i}", "name": f"Synthetic {i}"} for i in range(1, args.coins + 1)]
    upsert_coins(conn, coins)

    for c in coins:
        drift, vol, price = rng.uniform(-0.0005, 0.0015), rng.uniform(0.02, 0.06), rng.uniform(1, 500)
        rows = []
        for d in range(args.days):
            ts = int((start + timedelta(days=d)).timestamp() * 1000)
            o = price
            price = o * math.exp(rng.gauss(drift, vol))
            hi, lo = max(o, price) * (1 + abs(rng.gauss(0, vol / 3))), min(o, price) * (1 - abs(rng.gauss(0, vol / 3)))
            rows.append([ts, o, hi, lo, price])
        upsert_ohlc_rows(conn, c["id"], "1d", rows)
    conn.close()
    print(f"SYNTHETIC bars written: {args.db_path} coins={args.coins} days={args.days} from={args.start_date}")


if __name__ == "__main__":
    main()
