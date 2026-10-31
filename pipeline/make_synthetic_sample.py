#!/usr/bin/env python3
"""Build a SYNTHETIC sample database offline, for the quickstart and the tests.

Every active catalog series gets a seeded random walk on its native calendar
(weekdays for daily series, Wednesdays for weekly, 1st of month for monthly). The
values are made up and are labelled synthetic in source_url. What is real is the
time logic: each row's available_at comes from the same compute_available_at the
ingesters use, and rows whose release date falls after --end-date are left out,
just as a live database would not have them yet.

    python pipeline/make_synthetic_sample.py --db-path data/sample.db
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import random
import zlib
from pathlib import Path
from typing import Dict, Iterator, List, Tuple

from _common import (
    DEFAULT_CONFIG,
    apply_schema,
    begin_ingestion_run,
    build_records,
    connect_db,
    finish_ingestion_run,
    load_config,
    seed_reference_data,
    upsert_observations,
)

ROOT = Path(__file__).resolve().parents[1]


def calendar_dates(start: dt.date, end: dt.date, freq: str) -> Iterator[dt.date]:
    d = start
    if freq == "monthly":
        d = start.replace(day=1)
        while d <= end:
            yield d
            d = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        return
    while d <= end:
        if (freq == "daily" and d.weekday() < 5) or (freq == "weekly" and d.weekday() == 2):
            yield d
        d += dt.timedelta(days=1)


def random_walk(series_id: str, n: int, start_level: float, vol: float) -> List[float]:
    rng = random.Random(zlib.crc32(series_id.encode()))  # stable across runs and machines
    level, out = start_level, []
    for _ in range(n):
        level *= math.exp(rng.gauss(0.0002, vol))
        out.append(round(level, 6))
    return out


def start_level(series: Dict) -> Tuple[float, float]:
    units = str(series.get("units", "")).lower()
    if "percent" in units:
        return 3.0, 0.01
    if units.startswith("index points"):
        return 50.0, 0.02
    if units == "usd" and series["series_domain"] == "indicator":
        return 1e11, 0.005
    return 100.0, 0.012


def main() -> int:
    p = argparse.ArgumentParser(description="Create a synthetic sample database")
    p.add_argument("--db-path", default=str(ROOT / "data" / "sample.db"))
    p.add_argument("--config-path", default=DEFAULT_CONFIG)
    p.add_argument("--end-date", default=(dt.date.today() - dt.timedelta(days=1)).isoformat())
    p.add_argument("--years", type=int, default=2)
    args = p.parse_args()

    end = dt.date.fromisoformat(args.end_date)
    start = end - dt.timedelta(days=365 * args.years)
    Path(args.db_path).unlink(missing_ok=True)

    config = load_config(args.config_path)
    conn = connect_db(args.db_path)
    apply_schema(conn)
    seed_reference_data(conn, config)

    total = 0
    by_provider: Dict[str, List[Dict]] = {}
    for s in config["series"]:
        if s.get("is_active", True):
            by_provider.setdefault(s["provider_id"], []).append(s)

    for provider, series_list in sorted(by_provider.items()):
        run_id = begin_ingestion_run(conn, "make_synthetic_sample.py", provider)
        rows = 0
        for s in series_list:
            dates = [d.isoformat() for d in calendar_dates(start, end, s["native_frequency"])]
            level, vol = start_level(s)
            values = random_walk(s["series_id"], len(dates), level, vol)
            recs = build_records(s, zip(dates, values), f"synthetic://{s['series_id']}", run_id)
            recs = [r for r in recs if r[2] <= end.isoformat()]  # not yet released -> not in the DB
            rows += upsert_observations(conn, recs)
        finish_ingestion_run(conn, run_id, "success", rows)
        total += rows

    conn.close()
    print(f"SYNTHETIC sample written: {args.db_path}")
    print(f"window={start}..{end} series={sum(len(v) for v in by_provider.values())} rows={total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
