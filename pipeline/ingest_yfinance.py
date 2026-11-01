#!/usr/bin/env python3
"""Ingest daily closes from Yahoo Finance (yfinance, no key) into raw_observations."""

from __future__ import annotations

import argparse
from typing import Any, Dict

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


def _select_price_column(df):
    """Prefer Adj Close, fall back to Close; handles yfinance's MultiIndex columns."""
    for name in ("Adj Close", "Close"):
        if name in df.columns:
            col = df[name]
            return col.iloc[:, 0] if getattr(col, "ndim", 1) > 1 else col
        for c in df.columns:
            if isinstance(c, tuple) and c and c[0] == name:
                return df[c]
    raise RuntimeError("No Close/Adj Close column found")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ingest yfinance data into SQLite")
    p.add_argument("--db-path", default=DEFAULT_DB)
    p.add_argument("--schema-path", default=DEFAULT_SCHEMA)
    p.add_argument("--config-path", default=DEFAULT_CONFIG)
    p.add_argument("--start-date", default=default_start_date())
    p.add_argument("--max-rows-per-series", type=int, default=0, help="0 = keep all rows")
    p.add_argument("--series", default="", help="Comma list of series_id or tickers, e.g. SPY,GLD,BTC-USD")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    try:
        import yfinance as yf
    except ImportError as exc:
        raise SystemExit(f"yfinance is required: pip install -r requirements.txt ({exc})") from exc

    config = load_config(args.config_path)
    series = select_series(config, "YFINANCE", parse_csv_list(args.series))

    def fetch(s: Dict[str, Any]):
        ticker = str(s["external_code"])
        df = yf.download(
            ticker, start=args.start_date, interval="1d", auto_adjust=False, progress=False, threads=False
        )
        if df is None or df.empty:
            raise RuntimeError("empty dataframe")
        prices = _select_price_column(df).dropna()
        points = [(idx.date().isoformat(), float(val)) for idx, val in prices.items()]
        return points, f"https://finance.yahoo.com/quote/{ticker}"

    conn = connect_db(args.db_path)
    try:
        apply_schema(conn, args.schema_path)
        return ingest_provider(
            conn, config, "YFINANCE", "ingest_yfinance.py", series, fetch, args.start_date, args.max_rows_per_series
        )
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
