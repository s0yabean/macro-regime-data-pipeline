#!/usr/bin/env python3
"""Export observations (with catalog metadata) to a flat CSV for review in a spreadsheet."""

from __future__ import annotations

import argparse
import csv
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    p = argparse.ArgumentParser(description="Export raw_observations to CSV")
    p.add_argument("--db-path", default=str(ROOT / "data" / "macro.db"))
    p.add_argument("--csv-path", default=str(ROOT / "data" / "exports" / "observations.csv"))
    p.add_argument("--provider", default="", help="Optional provider_id filter, e.g. FRED")
    p.add_argument("--start-date", default="")
    p.add_argument("--series", default="", help="Optional comma list of series_id")
    args = p.parse_args()

    sql = """
        SELECT ro.series_id, sc.provider_id, sc.external_code, sc.display_name, sc.native_frequency,
               sc.coverage_status, sc.release_lag_days, ro.observation_date, ro.available_at, ro.value,
               ro.source_url, ro.ingestion_run_id
        FROM raw_observations ro
        JOIN series_catalog sc ON sc.series_id = ro.series_id
        WHERE 1 = 1
    """
    params = []
    if args.provider:
        sql += " AND sc.provider_id = ?"
        params.append(args.provider.upper())
    if args.start_date:
        sql += " AND ro.observation_date >= ?"
        params.append(args.start_date)
    ids = [s.strip() for s in args.series.split(",") if s.strip()]
    if ids:
        sql += f" AND ro.series_id IN ({','.join('?' * len(ids))})"
        params.extend(ids)
    sql += " ORDER BY ro.series_id, ro.observation_date"

    conn = sqlite3.connect(f"file:{args.db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    if not rows:
        print("No rows matched")
        return 0
    out = Path(args.csv_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(rows[0].keys())
        w.writerows(tuple(r) for r in rows)
    print(f"wrote {out} rows={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
