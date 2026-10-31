#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.common.coingecko import CoinGeckoClient, build_curl_command
from src.common.sqlite_store import apply_schema, connect, insert_top_snapshot, upsert_coins


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch top N coins by market cap from CoinGecko Pro and store snapshot in CSV/SQLite."
    )
    parser.add_argument("--api-key", default=os.getenv("COINGECKO_API_KEY"), help="CoinGecko Pro API key.")
    parser.add_argument(
        "--api-base",
        default=os.getenv("COINGECKO_API_BASE", "https://pro-api.coingecko.com/api/v3"),
        help="CoinGecko API base URL.",
    )
    parser.add_argument("--top-n", type=int, default=300, help="How many coins to fetch (default: 300).")
    parser.add_argument("--vs-currency", default="usd", help="Quote currency (default: usd).")
    parser.add_argument(
        "--snapshot-ts",
        default=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        help="Snapshot timestamp label in ISO format, e.g. 2026-03-25T09:30:00Z.",
    )
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
        "--csv-path",
        default="",
        help="CSV output path. If omitted, auto-generated under data/exports/.",
    )
    parser.add_argument("--print-curl", action="store_true", help="Print equivalent curl requests.")
    parser.add_argument("--no-db", action="store_true", help="Skip writing to SQLite.")
    parser.add_argument("--no-csv", action="store_true", help="Skip writing CSV.")
    return parser.parse_args()


def ensure_csv_path(csv_path_arg: str, snapshot_ts: str) -> Path:
    if csv_path_arg:
        path = Path(csv_path_arg)
    else:
        safe_stamp = snapshot_ts.replace(":", "").replace("-", "").replace("T", "_").replace("Z", "Z")
        path = ROOT_DIR / "data" / "exports" / f"top_coins_snapshot_{safe_stamp}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return

    fieldnames: List[str] = []
    seen = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    args = parse_args()
    if not args.api_key:
        raise SystemExit("Missing API key. Pass --api-key or set COINGECKO_API_KEY in environment.")

    if args.print_curl:
        print("Curl (top 250 page 1):")
        print(
            build_curl_command(
                "/coins/markets",
                {
                    "vs_currency": args.vs_currency,
                    "order": "market_cap_desc",
                    "per_page": 250,
                    "page": 1,
                    "sparkline": "false",
                },
                api_base=args.api_base,
            )
        )
        print("\nCurl (page 2, keep first 50 for top 300):")
        print(
            build_curl_command(
                "/coins/markets",
                {
                    "vs_currency": args.vs_currency,
                    "order": "market_cap_desc",
                    "per_page": 250,
                    "page": 2,
                    "sparkline": "false",
                },
                api_base=args.api_base,
            )
        )
        print()

    client = CoinGeckoClient(api_key=args.api_key, api_base=args.api_base)
    coins = client.get_top_n_by_market_cap(top_n=args.top_n, vs_currency=args.vs_currency)

    coin_ids = [coin["id"] for coin in coins if "id" in coin]
    unique_count = len(set(coin_ids))
    print(f"Fetched {len(coins)} rows, {unique_count} unique coin IDs.")
    print("First 10 IDs:", ", ".join(coin_ids[:10]))

    if not args.no_csv:
        csv_path = ensure_csv_path(args.csv_path, args.snapshot_ts)
        write_csv(csv_path, coins)
        print(f"Wrote CSV: {csv_path}")

    if not args.no_db:
        conn = connect(args.db_path)
        try:
            apply_schema(conn, args.schema_path)
            upsert_coins(conn, coins)
            insert_top_snapshot(conn, args.snapshot_ts, coins)
        finally:
            conn.close()
        print(f"Wrote SQLite snapshot: {args.db_path} (snapshot_ts={args.snapshot_ts})")


if __name__ == "__main__":
    main()

