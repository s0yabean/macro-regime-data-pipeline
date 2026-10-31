#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.common.coingecko import CoinGeckoClient, iter_time_chunks
from src.common.sqlite_store import (
    apply_schema,
    connect,
    upsert_market_metrics,
    upsert_ohlc_rows,
)


def parse_date_to_unix(date_text: str, end_of_day: bool = False) -> int:
    dt = datetime.strptime(date_text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    return int(dt.timestamp())


def read_coin_ids_from_csv(path: str) -> List[str]:
    ids: List[str] = []
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            coin_id = row.get("coin_id") or row.get("id")
            if coin_id:
                ids.append(coin_id.strip())
    return ids


def unique_keep_order(items: Sequence[str]) -> List[str]:
    seen = set()
    output: List[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            output.append(item)
    return output


def write_ohlc_csv(path: Path, coin_id: str, timeframe: str, rows: Sequence[Sequence[float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["coin_id", "timeframe", "ts", "open", "high", "low", "close"])
        for row in rows:
            if len(row) < 5:
                continue
            writer.writerow([coin_id, timeframe, int(row[0]), row[1], row[2], row[3], row[4]])


def write_market_csv(path: Path, coin_id: str, timeframe: str, payload: Dict[str, List[List[float]]]) -> None:
    prices = {int(ts): value for ts, value in payload.get("prices", [])}
    market_caps = {int(ts): value for ts, value in payload.get("market_caps", [])}
    volumes = {int(ts): value for ts, value in payload.get("total_volumes", [])}
    all_ts = sorted(set(prices.keys()) | set(market_caps.keys()) | set(volumes.keys()))

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["coin_id", "timeframe", "ts", "price", "market_cap", "volume_24h"])
        for ts in all_ts:
            writer.writerow([coin_id, timeframe, ts, prices.get(ts), market_caps.get(ts), volumes.get(ts)])


def estimate_chunk_count(start_ts: int, end_ts: int, max_days: int) -> int:
    total_days = (end_ts - start_ts + 1) / (24 * 60 * 60)
    return int(math.ceil(total_days / max_days))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch per-coin historical OHLC + market metrics and store into SQLite/CSV."
    )
    parser.add_argument("--api-key", default=os.getenv("COINGECKO_API_KEY"), help="CoinGecko Pro API key.")
    parser.add_argument(
        "--api-base",
        default=os.getenv("COINGECKO_API_BASE", "https://pro-api.coingecko.com/api/v3"),
        help="CoinGecko API base URL.",
    )
    parser.add_argument("--coin-id", action="append", default=[], help="Repeatable. Example: --coin-id bitcoin")
    parser.add_argument("--coins-csv", default="", help="CSV path containing coin_id or id column.")
    parser.add_argument("--max-coins", type=int, default=0, help="Optional limit for testing.")
    parser.add_argument("--start-date", required=True, help="Start date in YYYY-MM-DD (UTC).")
    parser.add_argument("--end-date", required=True, help="End date in YYYY-MM-DD (UTC).")
    parser.add_argument(
        "--db-path",
        default=str(ROOT_DIR / "data" / "marketdata.sqlite"),
        help="SQLite database path.",
    )
    parser.add_argument(
        "--schema-path",
        default=str(ROOT_DIR / "sql" / "schema.sql"),
        help="SQLite schema SQL path.",
    )
    parser.add_argument(
        "--csv-dir",
        default=str(ROOT_DIR / "data" / "exports" / "history"),
        help="Directory for per-coin CSV exports.",
    )
    parser.add_argument("--no-csv-export", action="store_true", help="Skip CSV exports.")
    parser.add_argument("--skip-ohlc-hourly", action="store_true", help="Skip /ohlc/range interval=hourly.")
    parser.add_argument("--skip-ohlc-daily", action="store_true", help="Skip /ohlc/range interval=daily.")
    parser.add_argument(
        "--skip-market-hourly",
        action="store_true",
        help="Skip /market_chart/range hourly pass (<=90 day chunks).",
    )
    parser.add_argument(
        "--skip-market-daily",
        action="store_true",
        help="Skip /market_chart/range daily pass (>90 day window).",
    )
    parser.add_argument("--sleep-seconds", type=float, default=0.2, help="Pause between API calls.")
    parser.add_argument("--dry-run", action="store_true", help="Print plan only (no API calls).")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.api_key and not args.dry_run:
        raise SystemExit("Missing API key. Pass --api-key or set COINGECKO_API_KEY in environment.")

    start_ts = parse_date_to_unix(args.start_date, end_of_day=False)
    end_ts = parse_date_to_unix(args.end_date, end_of_day=True)
    if start_ts > end_ts:
        raise SystemExit("start-date must be <= end-date")

    coin_ids = list(args.coin_id)
    if args.coins_csv:
        coin_ids.extend(read_coin_ids_from_csv(args.coins_csv))
    coin_ids = unique_keep_order(coin_ids)
    if args.max_coins > 0:
        coin_ids = coin_ids[: args.max_coins]
    if not coin_ids:
        raise SystemExit("No coin IDs provided. Use --coin-id and/or --coins-csv.")

    hourly_ohlc_chunks = estimate_chunk_count(start_ts, end_ts, 31)
    daily_ohlc_chunks = estimate_chunk_count(start_ts, end_ts, 180)
    hourly_market_chunks = estimate_chunk_count(start_ts, end_ts, 90)
    daily_market_chunks = 1

    calls_per_coin = 0
    if not args.skip_ohlc_hourly:
        calls_per_coin += hourly_ohlc_chunks
    if not args.skip_ohlc_daily:
        calls_per_coin += daily_ohlc_chunks
    if not args.skip_market_hourly:
        calls_per_coin += hourly_market_chunks
    if not args.skip_market_daily:
        calls_per_coin += daily_market_chunks

    print(f"Coins selected: {len(coin_ids)}")
    print(f"Date range UTC: {args.start_date} to {args.end_date}")
    print(f"Estimated API calls per coin: {calls_per_coin}")
    print(f"Estimated total API calls: {calls_per_coin * len(coin_ids)}")

    if args.dry_run:
        return

    client = CoinGeckoClient(api_key=args.api_key, api_base=args.api_base)
    conn = connect(args.db_path)
    apply_schema(conn, args.schema_path)
    csv_dir = Path(args.csv_dir)

    try:
        for coin_id in coin_ids:
            print(f"\nProcessing coin: {coin_id}")

            if not args.skip_ohlc_hourly:
                all_rows: List[List[float]] = []
                for from_ts, to_ts in iter_time_chunks(start_ts, end_ts, max_days=31):
                    payload = client.get_ohlc_range(coin_id, from_ts, to_ts, interval="hourly")
                    upsert_ohlc_rows(conn, coin_id, "1h", payload)
                    all_rows.extend(payload)
                    print(
                        f"  OHLC hourly chunk {from_ts}->{to_ts}: {len(payload)} rows"
                    )
                    time.sleep(args.sleep_seconds)
                if not args.no_csv_export:
                    write_ohlc_csv(csv_dir / f"{coin_id}_ohlc_1h.csv", coin_id, "1h", all_rows)

            if not args.skip_ohlc_daily:
                all_rows = []
                for from_ts, to_ts in iter_time_chunks(start_ts, end_ts, max_days=180):
                    payload = client.get_ohlc_range(coin_id, from_ts, to_ts, interval="daily")
                    upsert_ohlc_rows(conn, coin_id, "1d", payload)
                    all_rows.extend(payload)
                    print(
                        f"  OHLC daily chunk {from_ts}->{to_ts}: {len(payload)} rows"
                    )
                    time.sleep(args.sleep_seconds)
                if not args.no_csv_export:
                    write_ohlc_csv(csv_dir / f"{coin_id}_ohlc_1d.csv", coin_id, "1d", all_rows)

            if not args.skip_market_hourly:
                hourly_payload_merged = {"prices": [], "market_caps": [], "total_volumes": []}
                for from_ts, to_ts in iter_time_chunks(start_ts, end_ts, max_days=90):
                    payload = client.get_market_chart_range(coin_id, from_ts, to_ts)
                    upsert_market_metrics(
                        conn,
                        coin_id,
                        "1h",
                        payload.get("prices", []),
                        payload.get("market_caps", []),
                        payload.get("total_volumes", []),
                    )
                    hourly_payload_merged["prices"].extend(payload.get("prices", []))
                    hourly_payload_merged["market_caps"].extend(payload.get("market_caps", []))
                    hourly_payload_merged["total_volumes"].extend(payload.get("total_volumes", []))
                    print(
                        "  Market hourly chunk "
                        f"{from_ts}->{to_ts}: prices={len(payload.get('prices', []))}, "
                        f"market_caps={len(payload.get('market_caps', []))}, "
                        f"total_volumes={len(payload.get('total_volumes', []))}"
                    )
                    time.sleep(args.sleep_seconds)
                if not args.no_csv_export:
                    write_market_csv(
                        csv_dir / f"{coin_id}_market_1h.csv",
                        coin_id,
                        "1h",
                        hourly_payload_merged,
                    )

            if not args.skip_market_daily:
                payload = client.get_market_chart_range(coin_id, start_ts, end_ts)
                upsert_market_metrics(
                    conn,
                    coin_id,
                    "1d",
                    payload.get("prices", []),
                    payload.get("market_caps", []),
                    payload.get("total_volumes", []),
                )
                print(
                    f"  Market daily full-range: prices={len(payload.get('prices', []))}, "
                    f"market_caps={len(payload.get('market_caps', []))}, "
                    f"total_volumes={len(payload.get('total_volumes', []))}"
                )
                if not args.no_csv_export:
                    write_market_csv(csv_dir / f"{coin_id}_market_1d.csv", coin_id, "1d", payload)
                time.sleep(args.sleep_seconds)
    finally:
        conn.close()

    print(f"\nDone. SQLite updated: {args.db_path}")


if __name__ == "__main__":
    main()

