#!/usr/bin/env python3
"""Point-in-time queries: what could a strategy have known on a given date?

    # Snapshot of every series as of a date
    python pipeline/query_as_of.py --as-of 2025-10-10

    # One series, as of a date, with the last N known values
    python pipeline/query_as_of.py --as-of 2025-10-10 --series FRED:CPIAUCSL --history 3

    # Indicator view: all series mapped to an indicator code
    python pipeline/query_as_of.py --as-of 2025-10-10 --indicator I1

Every query filters on available_at <= as_of. observation_date is returned so the
reader can see how old the newest known value is.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
PIT_SQL = (ROOT / "sql" / "point_in_time.sql").read_text(encoding="utf-8")


def snapshot(conn: sqlite3.Connection, as_of: str, series_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    rows = [dict(r) for r in conn.execute(PIT_SQL, {"as_of": as_of}).fetchall()]
    if series_ids:
        rows = [r for r in rows if r["series_id"] in series_ids]
    return rows


def history(conn: sqlite3.Connection, as_of: str, series_id: str, n: int) -> List[Dict[str, Any]]:
    sql = """
        SELECT observation_date, available_at, value
        FROM raw_observations
        WHERE series_id = ? AND available_at <= ?
        ORDER BY observation_date DESC
        LIMIT ?
    """
    return [dict(r) for r in conn.execute(sql, (series_id, as_of, n)).fetchall()]


def indicator_series(conn: sqlite3.Connection, code: str) -> List[str]:
    rows = conn.execute("SELECT series_id FROM indicator_series_map WHERE indicator_code = ?", (code,)).fetchall()
    return [r["series_id"] for r in rows]


def main() -> int:
    p = argparse.ArgumentParser(description="Point-in-time query of the macro dataset")
    p.add_argument("--db-path", default=str(ROOT / "data" / "macro.db"))
    p.add_argument("--as-of", required=True, help="YYYY-MM-DD")
    p.add_argument("--series", default="", help="Comma list of series_id")
    p.add_argument("--indicator", default="", help="Indicator code from the catalog, e.g. L2, I1, C2")
    p.add_argument("--history", type=int, default=0, help="Also return the last N known values per series")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    if not Path(args.db_path).exists():
        raise SystemExit(f"Database not found: {args.db_path}")
    conn = sqlite3.connect(f"file:{args.db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        ids = [s.strip() for s in args.series.split(",") if s.strip()]
        if args.indicator:
            ids += indicator_series(conn, args.indicator)
        rows = snapshot(conn, args.as_of, ids or None)
        if args.history:
            for r in rows:
                r["history"] = history(conn, args.as_of, r["series_id"], args.history)
    finally:
        conn.close()

    if args.json:
        print(json.dumps({"as_of": args.as_of, "rows": rows}, indent=2))
        return 0
    print(f"as_of={args.as_of}  series={len(rows)}")
    print(f"{'series_id':<32}{'observation_date':<18}{'available_at':<14}{'value':>16}  coverage")
    for r in rows:
        print(f"{r['series_id']:<32}{r['observation_date']:<18}{r['available_at']:<14}{r['value']:>16.4f}  {r['coverage_status']}")
        for h in r.get("history", [])[1:]:
            print(f"{'':<32}{h['observation_date']:<18}{h['available_at']:<14}{h['value']:>16.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
