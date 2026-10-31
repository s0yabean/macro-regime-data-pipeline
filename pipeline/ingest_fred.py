#!/usr/bin/env python3
"""Ingest FRED series into raw_observations.

Needs FRED_API_KEY. Keys are free: https://fred.stlouisfed.org/docs/api/api_key.html
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Tuple

from _common import (
    DEFAULT_CONFIG,
    DEFAULT_DB,
    DEFAULT_SCHEMA,
    apply_schema,
    connect_db,
    default_start_date,
    ingest_provider,
    load_config,
    parse_csv_list,
    select_series,
)

FRED_API_URL = "https://api.stlouisfed.org/fred/series/observations"


def normalize_fred_value(external_code: str, value: float) -> float:
    """Store Fed net-liquidity components in one unit (millions USD).

    WALCL and WTREGEN are reported in millions; RRPONTSYD is reported in billions.
    """
    if external_code == "RRPONTSYD":
        return value * 1000.0
    return value


def _http_get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "macro-regime-data-pipeline/1.0"})
    with urllib.request.urlopen(req, timeout=45) as resp:
        return resp.read().decode("utf-8")


def fetch_via_api(api_key: str, code: str, start_date: str) -> Tuple[List[Tuple[str, str]], str]:
    params = {
        "series_id": code,
        "api_key": api_key,
        "file_type": "json",
        "observation_start": start_date,
        "sort_order": "asc",
    }
    data = json.loads(_http_get(FRED_API_URL + "?" + urllib.parse.urlencode(params)))
    if "error_code" in data:
        raise RuntimeError(f"FRED error: {data.get('error_message')}")
    return [(o.get("date"), o.get("value")) for o in data.get("observations", [])], FRED_API_URL


def clean(code: str, raw: List[Tuple[str, str]]) -> List[Tuple[str, float]]:
    out: List[Tuple[str, float]] = []
    for date, value in raw:
        if value in (None, "", "."):  # FRED marks missing values with "."
            continue
        try:
            out.append((str(date), normalize_fred_value(code, float(value))))
        except ValueError:
            continue
    return out


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ingest FRED data into SQLite")
    p.add_argument("--db-path", default=DEFAULT_DB)
    p.add_argument("--schema-path", default=DEFAULT_SCHEMA)
    p.add_argument("--config-path", default=DEFAULT_CONFIG)
    p.add_argument("--start-date", default=default_start_date())
    p.add_argument("--max-rows-per-series", type=int, default=0, help="0 = keep all rows")
    p.add_argument("--series", default="", help="Comma list of series_id or FRED codes, e.g. CPIAUCSL,T10Y2Y")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    api_key = os.environ.get("FRED_API_KEY", "").strip()
    if not api_key:
        print("FRED_API_KEY not set; skipping FRED (free key: https://fred.stlouisfed.org/docs/api/api_key.html)")
        return 0

    config = load_config(args.config_path)
    series = select_series(config, "FRED", parse_csv_list(args.series))

    def fetch(s: Dict[str, Any]):
        code = str(s["external_code"])
        raw, source = fetch_via_api(api_key, code, args.start_date)
        return clean(code, raw), source

    conn = connect_db(args.db_path)
    try:
        apply_schema(conn, args.schema_path)
        return ingest_provider(
            conn, config, "FRED", "ingest_fred.py", series, fetch, args.start_date, args.max_rows_per_series
        )
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
