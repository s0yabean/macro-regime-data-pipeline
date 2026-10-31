"""Offline tests: time semantics, health checks, point-in-time queries, backtests.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import csv
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
sys.path.insert(0, str(ROOT / "backtesting"))

from _common import compute_available_at, load_config  # noqa: E402
from check_health import run_checks  # noqa: E402
from ingest_coingecko import strict_sum  # noqa: E402
from ingest_csv import load_rows  # noqa: E402
from query_as_of import history, snapshot  # noqa: E402
from src.backtest.strategies.median_long_short import run_median_long_short  # noqa: E402
from src.backtest.strategies.sma_cross import run_sma_cross  # noqa: E402

AS_OF = "2025-10-10"


class AvailableAtTests(unittest.TestCase):
    def test_monthly_counts_from_period_end(self):
        # CPI for September is labelled 2025-09-01 by FRED. With a 14-day lag it is
        # usable from 2025-10-14, not from 2025-09-15.
        self.assertEqual(compute_available_at("2025-09-01", 14, "monthly"), "2025-10-14")

    def test_daily_and_weekly_count_from_label(self):
        self.assertEqual(compute_available_at("2025-10-08", 0, "daily"), "2025-10-08")
        self.assertEqual(compute_available_at("2025-10-08", 7, "weekly"), "2025-10-15")

    def test_quarterly(self):
        self.assertEqual(compute_available_at("2025-04-01", 30, "quarterly"), "2025-07-30")

    def test_every_catalog_series_has_a_lag_and_frequency(self):
        for s in load_config()["series"]:
            self.assertIn(s["native_frequency"], {"daily", "weekly", "monthly", "quarterly"}, s["series_id"])
            self.assertGreaterEqual(int(s.get("release_lag_days", 0)), 0, s["series_id"])


class DatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = str(Path(cls.tmp.name) / "sample.db")
        subprocess.run(
            [sys.executable, str(ROOT / "pipeline" / "make_synthetic_sample.py"), "--db-path", cls.db,
             "--end-date", "2025-12-31"],
            check=True, capture_output=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def conn(self):
        c = sqlite3.connect(self.db)
        c.row_factory = sqlite3.Row
        return c

    def test_health_checks_pass_on_sample(self):
        report = run_checks(self.db, "2025-12-31")
        failed = [c["name"] for c in report["checks"] if c["status"] == "FAIL"]
        self.assertEqual(failed, [])

    def test_point_in_time_never_returns_future_values(self):
        with self.conn() as c:
            rows = snapshot(c, AS_OF)
        self.assertGreater(len(rows), 40)
        self.assertTrue(all(r["available_at"] <= AS_OF for r in rows))

    def test_cpi_as_of_shows_august_not_september(self):
        with self.conn() as c:
            (row,) = snapshot(c, AS_OF, ["FRED:CPIAUCSL"])
            hist = history(c, AS_OF, "FRED:CPIAUCSL", 3)
        self.assertEqual(row["observation_date"], "2025-08-01")
        self.assertEqual([h["observation_date"] for h in hist], ["2025-08-01", "2025-07-01", "2025-06-01"])

    def test_health_check_catches_lag_violation(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = str(Path(tmp) / "bad.db")
            Path(bad).write_bytes(Path(self.db).read_bytes())
            c = sqlite3.connect(bad)
            # No lag at all: CPI stored as available on its label date.
            c.execute("UPDATE raw_observations SET available_at = observation_date WHERE series_id = 'FRED:CPIAUCSL'")
            c.commit()
            c.close()
            report = run_checks(bad, "2025-12-31")
        status = {ch["name"]: ch["status"] for ch in report["checks"]}
        self.assertEqual(status["lag_respected"], "FAIL")
        self.assertFalse(report["all_checks_passed"])

    def test_health_check_catches_lag_counted_from_label_date(self):
        # The subtler bug: available_at = label date + lag. lag_respected passes this
        # exactly; lag_from_period_end must flag it.
        good = {ch["name"]: ch["status"] for ch in run_checks(self.db, "2025-12-31")["checks"]}
        self.assertEqual(good["lag_from_period_end"], "PASS")
        with tempfile.TemporaryDirectory() as tmp:
            bad = str(Path(tmp) / "label_date.db")
            Path(bad).write_bytes(Path(self.db).read_bytes())
            c = sqlite3.connect(bad)
            c.execute(
                """
                UPDATE raw_observations
                SET available_at = date(observation_date, '+' || (
                  SELECT release_lag_days FROM series_catalog sc
                  WHERE sc.series_id = raw_observations.series_id) || ' days')
                WHERE frequency_observed = 'monthly'
                """
            )
            c.commit()
            c.close()
            report = run_checks(bad, "2025-12-31")
        status = {ch["name"]: ch["status"] for ch in report["checks"]}
        self.assertEqual(status["lag_respected"], "PASS")
        self.assertEqual(status["lag_from_period_end"], "FAIL")
        self.assertFalse(report["all_checks_passed"])

    def test_schema_rejects_available_before_observation(self):
        with self.conn() as c, self.assertRaises(sqlite3.IntegrityError):
            c.execute(
                "INSERT INTO raw_observations (series_id, observation_date, available_at, value, frequency_observed)"
                " VALUES ('YF:SPY', '2030-01-02', '2030-01-01', 1.0, 'daily')"
            )


class CsvLoaderTests(unittest.TestCase):
    def _write(self, rows):
        f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="")
        w = csv.DictWriter(f, fieldnames=["series_id", "observation_date", "value", "available_at"])
        w.writeheader()
        w.writerows(rows)
        f.close()
        return f.name

    def test_csv_applies_catalog_lag(self):
        catalog = {s["series_id"]: s for s in load_config()["series"]}
        path = self._write([{"series_id": "MANUAL:ISM_PMI", "observation_date": "2025-08-01", "value": "48.7",
                             "available_at": ""}])
        (rec,) = load_rows(path, catalog, run_id=1)
        self.assertEqual(rec[2], "2025-09-01")  # Aug 31 + 1 day

    def test_csv_rejects_override_earlier_than_lag(self):
        catalog = {s["series_id"]: s for s in load_config()["series"]}
        path = self._write([{"series_id": "MANUAL:ISM_PMI", "observation_date": "2025-08-01", "value": "48.7",
                             "available_at": "2025-08-15"}])
        with self.assertRaises(SystemExit):
            load_rows(path, catalog, run_id=1)


class DerivedSeriesTests(unittest.TestCase):
    def test_stablecoin_sum_keeps_only_days_with_both_components(self):
        usdt = {"2025-01-01": 10.0, "2025-01-02": 11.0}
        usdc = {"2025-01-02": 5.0, "2025-01-03": 6.0}
        self.assertEqual(strict_sum(usdt, usdc), {"2025-01-02": 16.0})


class BacktestTests(unittest.TestCase):
    def test_sma_fills_on_the_bar_after_the_signal(self):
        closes = [10.0] * 5 + [9.0] * 5 + [12.0, 20.0, 21.0, 22.0]
        bars = list(enumerate(closes))
        result = run_sma_cross(bars, fast_window=2, slow_window=4)
        self.assertEqual(result["trades"], 1.0)
        # Cross up is first seen on the 12.0 bar; the fill is the next close (20.0),
        # marked to the last close (22.0): +10%.
        self.assertAlmostEqual(result["total_return_pct"], 10.0, places=4)

    def test_median_long_short_on_a_known_example(self):
        # Two winners keep winning, two losers keep losing: every period returns +5%.
        closes = {
            "a": [100 * 1.05 ** i for i in range(6)],
            "b": [100 * 1.05 ** i for i in range(6)],
            "c": [100 * 0.95 ** i for i in range(6)],
            "d": [100 * 0.95 ** i for i in range(6)],
        }
        r = run_median_long_short(closes, lookback=1, hold=1)
        self.assertEqual(r["traded_periods"], 4.0)
        self.assertAlmostEqual(r["mean_period_return_pct"], 5.0, places=4)
        self.assertEqual(r["hit_rate_pct"], 100.0)


if __name__ == "__main__":
    unittest.main()
