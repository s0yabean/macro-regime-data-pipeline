-- Data-health checks. Each block after a "-- check:" header returns the rows that
-- FAIL the check, so an empty result means the check passed. Blocks marked
-- "-- info:" are summaries and never fail.
--
-- pipeline/check_health.py parses this file, runs every block and prints a
-- PASS/FAIL report (JSON with --json). To run by hand:
--   sqlite3 -header -column data/macro.db
--   sqlite> .parameter set :as_of '2025-10-10'
--   sqlite> .read sql/health_checks.sql

-- info: coverage_by_provider | Series and row counts per provider
SELECT
  sc.provider_id,
  COUNT(DISTINCT sc.series_id) AS active_series,
  COUNT(ro.observation_date) AS row_count,
  MIN(ro.observation_date) AS first_date,
  MAX(ro.observation_date) AS last_date
FROM series_catalog sc
LEFT JOIN raw_observations ro ON ro.series_id = sc.series_id
WHERE sc.is_active = 1
GROUP BY sc.provider_id
ORDER BY sc.provider_id;

-- check: date_format | observation_date and available_at must be ISO YYYY-MM-DD
SELECT series_id, observation_date, available_at
FROM raw_observations
WHERE observation_date IS NULL
   OR available_at IS NULL
   OR observation_date NOT GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]'
   OR available_at NOT GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]'
LIMIT 20;

-- check: no_lookahead | A value can never be available before the period it describes
SELECT series_id, observation_date, available_at
FROM raw_observations
WHERE available_at < observation_date
LIMIT 20;

-- check: lag_respected | available_at must be at least observation_date + catalog release lag
SELECT ro.series_id, ro.observation_date, ro.available_at, sc.release_lag_days
FROM raw_observations ro
JOIN series_catalog sc ON sc.series_id = ro.series_id
WHERE julianday(ro.available_at) - julianday(ro.observation_date) < sc.release_lag_days
LIMIT 20;

-- check: lag_from_period_end | Monthly/quarterly rows must not be available before period end + catalog release lag
-- lag_respected measures from observation_date, which for monthly and quarterly series is
-- the label at the START of the period. A rule that adds the lag to the label date passes
-- it exactly while exposing each value about a month early. This check measures from the
-- end of the period instead (FRED labels a month on its 1st and a quarter on its first day).
SELECT ro.series_id, ro.observation_date, ro.available_at, sc.release_lag_days,
  date(ro.observation_date, 'start of month',
       CASE ro.frequency_observed WHEN 'quarterly' THEN '+3 months' ELSE '+1 month' END,
       '-1 day', '+' || sc.release_lag_days || ' days') AS earliest_allowed
FROM raw_observations ro
JOIN series_catalog sc ON sc.series_id = ro.series_id
WHERE ro.frequency_observed IN ('monthly', 'quarterly')
  AND ro.available_at < date(ro.observation_date, 'start of month',
       CASE ro.frequency_observed WHEN 'quarterly' THEN '+3 months' ELSE '+1 month' END,
       '-1 day', '+' || sc.release_lag_days || ' days')
LIMIT 20;

-- check: frequency_matches_catalog | Observed frequency must match the catalog's native frequency
SELECT
  sc.series_id,
  sc.native_frequency AS catalog_frequency,
  GROUP_CONCAT(DISTINCT ro.frequency_observed) AS observed_frequencies
FROM series_catalog sc
JOIN raw_observations ro ON ro.series_id = sc.series_id
GROUP BY sc.series_id, sc.native_frequency
HAVING COUNT(DISTINCT ro.frequency_observed) > 1
    OR MAX(ro.frequency_observed) <> sc.native_frequency;

-- check: active_series_have_rows | Every active catalog series must have at least one row
SELECT sc.provider_id, sc.series_id
FROM series_catalog sc
LEFT JOIN raw_observations ro ON ro.series_id = sc.series_id
WHERE sc.is_active = 1
GROUP BY sc.series_id
HAVING COUNT(ro.observation_date) = 0
ORDER BY sc.provider_id, sc.series_id;

-- check: not_stale | Latest available value must be recent enough for its frequency (relative to :as_of)
SELECT
  sc.series_id,
  sc.native_frequency,
  MAX(ro.available_at) AS last_available_at,
  CAST(julianday(:as_of) - julianday(MAX(ro.available_at)) AS INTEGER) AS days_since_available
FROM series_catalog sc
JOIN raw_observations ro ON ro.series_id = sc.series_id AND ro.available_at <= :as_of
WHERE sc.is_active = 1
GROUP BY sc.series_id, sc.native_frequency
HAVING days_since_available > CASE sc.native_frequency
  WHEN 'daily' THEN 7
  WHEN 'weekly' THEN 16
  WHEN 'monthly' THEN 75
  ELSE 120
END
ORDER BY days_since_available DESC;

-- check: no_negative_where_impossible | Prices, market caps and supply values must be positive
SELECT ro.series_id, ro.observation_date, ro.value
FROM raw_observations ro
JOIN series_catalog sc ON sc.series_id = ro.series_id
WHERE ro.value <= 0
  AND sc.series_domain IN ('asset', 'benchmark')
LIMIT 20;

-- check: last_run_succeeded | The most recent ingestion run per provider must have succeeded
SELECT r.provider_id, r.run_id, r.status, r.error_message
FROM ingestion_runs r
JOIN (
  SELECT provider_id, MAX(run_id) AS max_run
  FROM ingestion_runs
  GROUP BY provider_id
) latest ON latest.provider_id = r.provider_id AND latest.max_run = r.run_id
WHERE r.status <> 'success';
