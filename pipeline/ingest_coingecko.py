#!/usr/bin/env python3
"""Ingest crypto indicator series from CoinGecko into raw_observations.

Series (see config/series_catalog.yaml):
- CRYPTO:BTC_DOMINANCE_PCT     BTC market cap / total crypto market cap * 100
- CRYPTO:STABLECOIN_SUPPLY_USD USDT market cap + USDC market cap
- CRYPTO:USDT_MARKET_CAP_USD, CRYPTO:USDC_MARKET_CAP_USD (components, inactive by default)

Derived series keep a day only when every input is present on that day. Zero-filling
a missing component would silently understate supply, so we skip the day instead.

Needs COINGECKO_API_KEY. The total-market-cap history endpoint is on the paid plan;
set COINGECKO_API_BASE to the public base URL to use a free demo key for the
component series only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List

from _common import (
    DEFAULT_CONFIG,
    DEFAULT_DB,
    DEFAULT_SCHEMA,
    apply_schema,
    begin_ingestion_run,
    build_records,
    connect_db,
    default_start_date,
    finish_ingestion_run,
    load_config,
    parse_csv_list,
    seed_reference_data,
    select_series,
    upsert_observations,
)

DEFAULT_BASE = "https://pro-api.coingecko.com/api/v3"


def coingecko_get_json(base_url: str, path: str, params: Dict[str, str], api_key: str) -> Dict:
    url = base_url.rstrip("/") + "/" + path.lstrip("/") + ("?" + urllib.parse.urlencode(params) if params else "")
    header = "x-cg-pro-api-key" if "pro-api" in base_url else "x-cg-demo-api-key"
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "macro-regime-data-pipeline/1.0", header: api_key}
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace").strip().replace("\n", " ")[:260]
        raise RuntimeError(f"HTTP {exc.code} for {path}: {body}") from exc


def _ts_to_date(ts: float) -> str:
    if ts > 10_000_000_000:  # epoch milliseconds
        ts = ts / 1000.0
    return dt.datetime.fromtimestamp(float(ts), tz=dt.timezone.utc).date().isoformat()


def parse_ts_rows(rows: object) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for r in rows if isinstance(rows, list) else []:
        if isinstance(r, list) and len(r) >= 2 and r[1] is not None:
            out[_ts_to_date(r[0])] = float(r[1])
    return out


def strict_sum(*maps: Dict[str, float]) -> Dict[str, float]:
    """Sum per date, keeping only dates present in every input."""
    shared = set(maps[0]).intersection(*maps[1:])
    return {d: sum(m[d] for m in maps) for d in shared}


def dominance(btc_mcap: Dict[str, float], total_mcap: Dict[str, float]) -> Dict[str, float]:
    return {d: btc_mcap[d] / total_mcap[d] * 100.0 for d in set(btc_mcap) & set(total_mcap) if total_mcap[d] > 0}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ingest CoinGecko indicator series into SQLite")
    p.add_argument("--db-path", default=DEFAULT_DB)
    p.add_argument("--schema-path", default=DEFAULT_SCHEMA)
    p.add_argument("--config-path", default=DEFAULT_CONFIG)
    p.add_argument("--start-date", default=default_start_date())
    p.add_argument("--max-rows-per-series", type=int, default=0)
    p.add_argument("--series", default="", help="Comma list of series_id or external codes")
    p.add_argument("--include-components", action="store_true", help="Also write USDT/USDC component series")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    api_key = os.environ.get("COINGECKO_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("Missing COINGECKO_API_KEY (see .env.example).")
    base = os.environ.get("COINGECKO_API_BASE", DEFAULT_BASE).strip() or DEFAULT_BASE

    config = load_config(args.config_path)
    by_code = {str(s["external_code"]): s for s in config["series"] if s["provider_id"] == "COINGECKO"}
    wanted = {str(s["external_code"]) for s in select_series(config, "COINGECKO", parse_csv_list(args.series))}
    if args.include_components:
        wanted |= {"USDT_MARKET_CAP_USD", "USDC_MARKET_CAP_USD"}

    conn = connect_db(args.db_path)
    apply_schema(conn, args.schema_path)
    seed_reference_data(conn, config)
    run_id = begin_ingestion_run(conn, "ingest_coingecko.py", "COINGECKO")
    failures: List[str] = []
    derived: Dict[str, Dict[str, float]] = {}
    chart = {"vs_currency": "usd", "days": "max", "interval": "daily"}

    try:
        if wanted & {"USDT_MARKET_CAP_USD", "USDC_MARKET_CAP_USD", "STABLECOIN_SUPPLY_USD"}:
            usdt = parse_ts_rows(coingecko_get_json(base, "/coins/tether/market_chart", chart, api_key).get("market_caps"))
            usdc = parse_ts_rows(coingecko_get_json(base, "/coins/usd-coin/market_chart", chart, api_key).get("market_caps"))
            derived["USDT_MARKET_CAP_USD"], derived["USDC_MARKET_CAP_USD"] = usdt, usdc
            derived["STABLECOIN_SUPPLY_USD"] = strict_sum(usdt, usdc)
        if "BTC_DOMINANCE_PCT" in wanted:
            total = coingecko_get_json(base, "/global/market_cap_chart", {"vs_currency": "usd", "days": "max"}, api_key)
            total_map = parse_ts_rows(total.get("market_cap_chart", {}).get("market_cap"))
            btc = parse_ts_rows(coingecko_get_json(base, "/coins/bitcoin/market_chart", chart, api_key).get("market_caps"))
            derived["BTC_DOMINANCE_PCT"] = dominance(btc, total_map)
    except Exception as exc:  # noqa: BLE001
        failures.append(str(exc))

    total_rows = 0
    for code in sorted(wanted):
        points = derived.get(code)
        if not points:
            failures.append(f"{code}: no rows")
            continue
        recs = build_records(
            by_code[code], points.items(), f"{base}#{code}", run_id, args.start_date, args.max_rows_per_series
        )
        total_rows += upsert_observations(conn, recs)
        print(f"{code}: rows_written={len(recs)}")

    finish_ingestion_run(conn, run_id, "success" if not failures else "failed", total_rows, " | ".join(failures) or None)
    conn.close()
    print(f"COINGECKO ingestion complete: rows_written={total_rows} failures={failures or 'none'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
