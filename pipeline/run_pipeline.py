#!/usr/bin/env python3
"""One command: create the schema, seed the catalog, ingest providers, run health checks.

    python pipeline/run_pipeline.py --providers yfinance,fred --start-date 2024-01-01

yfinance needs no key. FRED needs a free FRED_API_KEY (skipped with a message when it
is not set). CoinGecko needs COINGECKO_API_KEY.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from _common import DEFAULT_CONFIG, DEFAULT_DB, apply_schema, connect_db, default_start_date, load_config, seed_reference_data

HERE = Path(__file__).resolve().parent
SCRIPTS = {"yfinance": "ingest_yfinance.py", "fred": "ingest_fred.py", "coingecko": "ingest_coingecko.py"}


def main() -> int:
    p = argparse.ArgumentParser(description="Run the ingestion pipeline end to end")
    p.add_argument("--db-path", default=DEFAULT_DB)
    p.add_argument("--config-path", default=DEFAULT_CONFIG)
    p.add_argument("--start-date", default=default_start_date())
    p.add_argument("--max-rows-per-series", type=int, default=0)
    p.add_argument("--providers", default="yfinance,fred", help="Comma list: yfinance,fred,coingecko")
    p.add_argument("--series", default="", help="Optional comma list of series_id to limit the run")
    p.add_argument("--skip-health", action="store_true")
    args = p.parse_args()

    conn = connect_db(args.db_path)
    apply_schema(conn)
    seed_reference_data(conn, load_config(args.config_path))
    conn.close()

    worst = 0
    for name in [x.strip().lower() for x in args.providers.split(",") if x.strip()]:
        if name not in SCRIPTS:
            raise SystemExit(f"Unsupported provider: {name}")
        cmd = [sys.executable, str(HERE / SCRIPTS[name]), "--db-path", args.db_path, "--config-path",
               args.config_path, "--start-date", args.start_date, "--max-rows-per-series",
               str(args.max_rows_per_series)]
        if args.series:
            cmd += ["--series", args.series]
        print("$ python pipeline/" + SCRIPTS[name], " ".join(cmd[2:]).replace(str(HERE.parent) + "/", ""), flush=True)
        worst = max(worst, subprocess.run(cmd, check=False).returncode)

    if not args.skip_health:
        cmd = [sys.executable, str(HERE / "check_health.py"), "--db-path", args.db_path]
        if args.series:
            cmd += ["--series", args.series]
        worst = max(worst, subprocess.run(cmd, check=False).returncode)
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
