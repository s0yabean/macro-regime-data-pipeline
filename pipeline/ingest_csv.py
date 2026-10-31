#!/usr/bin/env python3
"""Load a staged CSV into raw_observations for series that have no usable API.

Some indicators (ISM Manufacturing PMI, central-bank gold purchases) are published
as monthly releases rather than through a free API. They are copied into a tidy CSV
and loaded here. The catalog's release lag is applied in exactly the same way as the
API ingesters, so the CSV only needs the value and the period it describes:

    series_id,observation_date,value
    MANUAL:ISM_PMI,2025-08-01,48.7

Optional columns: available_at (overrides the computed date, but may never be
earlier than it) and source_url.

The same loader takes the long-history crypto CSVs written by export_coinranking.py.
"""

from __future__ import annotations

import argparse
import csv
from typing import Dict, List

from _common import (
    DEFAULT_CONFIG,
    DEFAULT_DB,
    DEFAULT_SCHEMA,
    Record,
    apply_schema,
    begin_ingestion_run,
    compute_available_at,
    connect_db,
    finish_ingestion_run,
    load_config,
    seed_reference_data,
    upsert_observations,
)


def load_rows(csv_path: str, catalog: Dict[str, Dict], run_id: int) -> List[Record]:
    records: List[Record] = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for i, row in enumerate(csv.DictReader(f), start=2):
            sid = (row.get("series_id") or "").strip()
            obs = (row.get("observation_date") or "").strip()
            val = (row.get("value") or "").strip()
            if not (sid and obs and val):
                continue
            if sid not in catalog:
                raise SystemExit(f"line {i}: series_id {sid!r} is not in the catalog; add it to series_catalog.yaml")
            s = catalog[sid]
            freq = s["native_frequency"]
            floor = compute_available_at(obs, int(s.get("release_lag_days", 0)), freq)
            avail = (row.get("available_at") or "").strip() or floor
            if avail < floor:
                raise SystemExit(f"line {i}: available_at {avail} is earlier than the catalog lag allows ({floor})")
            records.append((sid, obs, avail, float(val), freq, (row.get("source_url") or "").strip() or None, run_id))
    return records


def main() -> int:
    p = argparse.ArgumentParser(description="Load a staged CSV into raw_observations")
    p.add_argument("csv_path")
    p.add_argument("--db-path", default=DEFAULT_DB)
    p.add_argument("--schema-path", default=DEFAULT_SCHEMA)
    p.add_argument("--config-path", default=DEFAULT_CONFIG)
    p.add_argument("--provider-id", default="MANUAL", help="Provider recorded on the ingestion run")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    config = load_config(args.config_path)
    catalog = {s["series_id"]: s for s in config["series"]}
    conn = connect_db(args.db_path)
    try:
        apply_schema(conn, args.schema_path)
        seed_reference_data(conn, config)
        run_id = begin_ingestion_run(conn, "ingest_csv.py", args.provider_id)
        records = load_rows(args.csv_path, catalog, run_id)
        written = 0 if args.dry_run else upsert_observations(conn, records)
        finish_ingestion_run(conn, run_id, "success", written)
        print(f"records_ready={len(records)} rows_written={written}{' (dry run)' if args.dry_run else ''}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
