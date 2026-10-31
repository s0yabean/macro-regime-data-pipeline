#!/usr/bin/env python3
"""Run sql/health_checks.sql against a database and report PASS/FAIL per check.

Exit code 0 only when every check passes, so this can gate a pipeline, a CI job or an
agent ("do not answer from this dataset unless check_health exits 0").

    python pipeline/check_health.py --db-path data/macro.db
    python pipeline/check_health.py --db-path data/macro.db --as-of 2025-10-10 --json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SQL = ROOT / "sql" / "health_checks.sql"
HEADER = re.compile(r"^-- (check|info): (\w+) \| (.+)$", re.M)


def parse_checks(sql_text: str) -> List[Dict[str, str]]:
    matches = list(HEADER.finditer(sql_text))
    blocks = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(sql_text)
        blocks.append({"kind": m.group(1), "name": m.group(2), "description": m.group(3),
                       "sql": sql_text[m.end():end].strip()})
    return blocks


def run_checks(db_path: str, as_of: str, series: Optional[Set[str]] = None,
               sql_path: Path = DEFAULT_SQL) -> Dict[str, Any]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    results = []
    try:
        for block in parse_checks(sql_path.read_text(encoding="utf-8")):
            params = {"as_of": as_of} if ":as_of" in block["sql"] else {}
            rows = [dict(r) for r in conn.execute(block["sql"], params).fetchall()]
            if series:
                rows = [r for r in rows if "series_id" not in r or r["series_id"] in series]
            entry = {"name": block["name"], "kind": block["kind"], "description": block["description"]}
            if block["kind"] == "check":
                entry.update(status="PASS" if not rows else "FAIL", failing_rows=rows[:20])
            else:
                entry.update(status="INFO", rows=rows)
            results.append(entry)
    finally:
        conn.close()
    ok = all(r["status"] != "FAIL" for r in results)
    return {"db_path": db_path, "as_of": as_of, "all_checks_passed": ok, "checks": results}


def main() -> int:
    p = argparse.ArgumentParser(description="Data-health checks for the macro dataset")
    p.add_argument("--db-path", default=str(ROOT / "data" / "macro.db"))
    p.add_argument("--as-of", default=dt.date.today().isoformat(), help="Reference date for staleness")
    p.add_argument("--series", default="", help="Limit series-level checks to this comma list of series_id")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    if not Path(args.db_path).exists():
        raise SystemExit(f"Database not found: {args.db_path}")
    scope = {s.strip() for s in args.series.split(",") if s.strip()} or None
    report = run_checks(args.db_path, args.as_of, scope)

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        shown = Path(args.db_path).resolve()
        shown = shown.relative_to(ROOT) if ROOT in shown.parents else shown.name
        print(f"\n## Data health ({shown}, as_of={args.as_of})")
        for c in report["checks"]:
            if c["kind"] == "info":
                for r in c["rows"]:
                    print("  info  " + "  ".join(f"{k}={v}" for k, v in r.items()))
                continue
            print(f"  {c['status']:<4}  {c['name']:<30} {c['description']}")
            for r in c["failing_rows"][:5]:
                print("          " + "  ".join(f"{k}={v}" for k, v in r.items()))
        print(f"all_checks_passed={'true' if report['all_checks_passed'] else 'false'}")
    return 0 if report["all_checks_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
