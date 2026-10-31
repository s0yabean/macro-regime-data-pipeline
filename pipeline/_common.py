"""Shared helpers for the ingestion scripts.

The one function that matters most is `compute_available_at`: it turns a source's
observation date into the first date a strategy may use the value. See
docs/three-clocks.md for the reasoning.
"""

from __future__ import annotations

import calendar
import datetime as dt
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = str(ROOT / "data" / "macro.db")
DEFAULT_SCHEMA = str(ROOT / "sql" / "schema.sql")
DEFAULT_CONFIG = str(ROOT / "config" / "series_catalog.yaml")

# (series_id, observation_date, available_at, value, frequency, source_url, run_id)
Record = Tuple[str, str, str, float, str, Optional[str], int]


def default_start_date() -> str:
    return (dt.date.today() - dt.timedelta(days=365 * 2)).isoformat()


def period_end(observation_date: str, frequency: str) -> dt.date:
    """Last calendar day of the period an observation describes.

    Sources label periods differently: FRED dates a monthly value on the 1st of the
    month it describes, and a quarterly value on the 1st of the quarter. Daily and
    weekly values (FRED weekly series are labelled by the week-ending date) already
    sit on the last day of their period.
    """
    d = dt.date.fromisoformat(observation_date)
    if frequency == "monthly":
        return d.replace(day=calendar.monthrange(d.year, d.month)[1])
    if frequency == "quarterly":
        q_end_month = ((d.month - 1) // 3 + 1) * 3
        return dt.date(d.year, q_end_month, calendar.monthrange(d.year, q_end_month)[1])
    return d


def compute_available_at(observation_date: str, release_lag_days: int, frequency: str = "daily") -> str:
    """First date a strategy is allowed to know this value.

    available_at = end of the observation period + release lag (days).

    Counting from the period end, not from the label date, is what keeps monthly
    series honest: CPI for September is labelled 2025-09-01 but cannot be known
    until mid-October.
    """
    end = period_end(observation_date, frequency)
    return (end + dt.timedelta(days=max(0, int(release_lag_days)))).isoformat()


def load_config(config_path: str = DEFAULT_CONFIG) -> Dict[str, Any]:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def connect_db(db_path: str = DEFAULT_DB) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def apply_schema(conn: sqlite3.Connection, schema_path: str = DEFAULT_SCHEMA) -> None:
    with open(schema_path, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()


def seed_reference_data(conn: sqlite3.Connection, config: Dict[str, Any]) -> None:
    """Upsert providers, series and indicator definitions from the YAML catalog."""
    for p in config.get("providers", []):
        conn.execute(
            """
            INSERT INTO providers (provider_id, provider_name, base_url, notes)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(provider_id) DO UPDATE SET
              provider_name=excluded.provider_name,
              base_url=excluded.base_url,
              notes=excluded.notes
            """,
            (p.get("provider_id"), p.get("provider_name"), p.get("base_url"), p.get("notes")),
        )

    for s in config.get("series", []):
        conn.execute(
            """
            INSERT INTO series_catalog (
              series_id, provider_id, external_code, display_name,
              regime_code, regime_label, coverage_status, series_domain, native_frequency,
              release_lag_days, units, is_active, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(series_id) DO UPDATE SET
              provider_id=excluded.provider_id,
              external_code=excluded.external_code,
              display_name=excluded.display_name,
              regime_code=excluded.regime_code,
              regime_label=excluded.regime_label,
              coverage_status=excluded.coverage_status,
              series_domain=excluded.series_domain,
              native_frequency=excluded.native_frequency,
              release_lag_days=excluded.release_lag_days,
              units=excluded.units,
              is_active=excluded.is_active,
              notes=excluded.notes,
              updated_at=CURRENT_TIMESTAMP
            """,
            (
                s.get("series_id"),
                s.get("provider_id"),
                s.get("external_code"),
                s.get("display_name"),
                s.get("regime_code"),
                s.get("regime_label"),
                s.get("coverage_status", "exact"),
                s.get("series_domain"),
                s.get("native_frequency"),
                int(s.get("release_lag_days", 0)),
                s.get("units"),
                1 if s.get("is_active", True) else 0,
                s.get("notes"),
            ),
        )

    for ind in config.get("indicators", []):
        conn.execute(
            """
            INSERT INTO indicator_definitions (
              indicator_code, indicator_name, axis, scored, description, scoring_notes
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(indicator_code) DO UPDATE SET
              indicator_name=excluded.indicator_name,
              axis=excluded.axis,
              scored=excluded.scored,
              description=excluded.description,
              scoring_notes=excluded.scoring_notes
            """,
            (
                ind.get("indicator_code"),
                ind.get("indicator_name"),
                ind.get("axis"),
                1 if ind.get("scored", False) else 0,
                ind.get("description"),
                ind.get("scoring_notes"),
            ),
        )
        conn.execute("DELETE FROM indicator_series_map WHERE indicator_code = ?", (ind.get("indicator_code"),))
        for m in ind.get("series_map", []):
            conn.execute(
                "INSERT INTO indicator_series_map (indicator_code, series_id, role) VALUES (?, ?, ?)",
                (ind.get("indicator_code"), m.get("series_id"), m.get("role", "primary")),
            )
    conn.commit()


def select_series(
    config: Dict[str, Any],
    provider_id: str,
    only: Sequence[str] = (),
) -> List[Dict[str, Any]]:
    """Active series for one provider, optionally filtered by series_id or external_code."""
    rows = [r for r in config.get("series", []) if r.get("provider_id") == provider_id and r.get("is_active", True)]
    wanted = {s.strip() for s in only if s.strip()}
    if wanted:
        rows = [r for r in rows if r["series_id"] in wanted or str(r["external_code"]) in wanted]
    return rows


def parse_csv_list(text: str) -> List[str]:
    return [s.strip() for s in (text or "").split(",") if s.strip()]


def begin_ingestion_run(conn: sqlite3.Connection, script_name: str, provider_id: str) -> int:
    cur = conn.execute(
        "INSERT INTO ingestion_runs (script_name, provider_id, status) VALUES (?, ?, 'started')",
        (script_name, provider_id),
    )
    conn.commit()
    return int(cur.lastrowid)


def finish_ingestion_run(
    conn: sqlite3.Connection,
    run_id: int,
    status: str,
    row_count: int,
    error_message: Optional[str] = None,
) -> None:
    conn.execute(
        """
        UPDATE ingestion_runs
        SET status = ?, row_count = ?, error_message = ?, run_finished_at = CURRENT_TIMESTAMP
        WHERE run_id = ?
        """,
        (status, row_count, error_message, run_id),
    )
    conn.commit()


def upsert_observations(conn: sqlite3.Connection, records: Iterable[Record]) -> int:
    records = list(records)
    if not records:
        return 0
    conn.executemany(
        """
        INSERT INTO raw_observations (
          series_id, observation_date, available_at, value, frequency_observed, source_url, ingestion_run_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(series_id, observation_date) DO UPDATE SET
          available_at=excluded.available_at,
          value=excluded.value,
          frequency_observed=excluded.frequency_observed,
          source_url=excluded.source_url,
          ingestion_run_id=excluded.ingestion_run_id
        """,
        records,
    )
    conn.commit()
    return len(records)


def build_records(
    series: Dict[str, Any],
    points: Iterable[Tuple[str, float]],
    source_url: Optional[str],
    run_id: int,
    start_date: str = "",
    max_rows: int = 0,
) -> List[Record]:
    """Turn (observation_date, value) pairs into raw_observations rows with available_at."""
    freq = str(series.get("native_frequency", "daily"))
    lag = int(series.get("release_lag_days", 0))
    rows = sorted((d, v) for d, v in points if not start_date or d >= start_date)
    if max_rows > 0:
        rows = rows[-max_rows:]
    return [
        (str(series["series_id"]), d, compute_available_at(d, lag, freq), float(v), freq, source_url, run_id)
        for d, v in rows
    ]


def ingest_provider(
    conn: sqlite3.Connection,
    config: Dict[str, Any],
    provider_id: str,
    script_name: str,
    series_list: List[Dict[str, Any]],
    fetch,  # Callable[[Dict[str, Any]], Tuple[List[Tuple[str, float]], str]]
    start_date: str,
    max_rows: int,
) -> int:
    """Shared run loop: fetch each series, build records, upsert, log the run.

    Returns a process exit code (0 when every series succeeded).
    """
    seed_reference_data(conn, config)
    run_id = begin_ingestion_run(conn, script_name, provider_id)
    total = 0
    failures: List[str] = []
    for s in series_list:
        print(f"Fetching {provider_id} {s['external_code']} -> {s['series_id']}")
        try:
            points, source_url = fetch(s)
            written = upsert_observations(conn, build_records(s, points, source_url, run_id, start_date, max_rows))
            total += written
            print(f"  rows_written={written}")
        except Exception as exc:  # noqa: BLE001 - one bad series must not stop the run
            failures.append(f"{s['external_code']}: {exc}")
            print(f"  ERROR {failures[-1]}")
    finish_ingestion_run(
        conn, run_id, "success" if not failures else "failed", total, " | ".join(failures) or None
    )
    print(f"{provider_id} ingestion complete: series={len(series_list)} rows_written={total} failures={len(failures)}")
    return 0 if not failures else 1
