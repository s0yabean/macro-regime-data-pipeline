-- Macro regime data pipeline: SQLite schema.
--
-- Two dates on every observation (see docs/three-clocks.md):
--   observation_date  what period the value describes (as labelled by the source)
--   available_at      first date a strategy is allowed to know the value
-- Every downstream query filters on available_at, never on observation_date alone.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS providers (
  provider_id TEXT PRIMARY KEY,
  provider_name TEXT NOT NULL,
  base_url TEXT,
  notes TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS series_catalog (
  series_id TEXT PRIMARY KEY,
  provider_id TEXT NOT NULL,
  external_code TEXT NOT NULL,
  display_name TEXT NOT NULL,
  regime_code TEXT CHECK (regime_code IN ('R1', 'R2', 'R3', 'R4') OR regime_code IS NULL),
  regime_label TEXT CHECK (regime_label IN ('Reflation', 'Stagflation', 'Goldilocks', 'Recession') OR regime_label IS NULL),
  coverage_status TEXT NOT NULL DEFAULT 'exact' CHECK (coverage_status IN ('exact', 'proxy', 'not_available')),
  series_domain TEXT NOT NULL CHECK (series_domain IN (
    'indicator',
    'transition',
    'asset',
    'benchmark',
    'risk_free',
    'derived'
  )),
  native_frequency TEXT NOT NULL CHECK (native_frequency IN ('daily', 'weekly', 'monthly', 'quarterly')),
  release_lag_days INTEGER NOT NULL DEFAULT 0 CHECK (release_lag_days >= 0),
  units TEXT,
  is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
  notes TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (provider_id) REFERENCES providers(provider_id)
);

CREATE INDEX IF NOT EXISTS idx_series_catalog_provider ON series_catalog(provider_id);
CREATE INDEX IF NOT EXISTS idx_series_catalog_domain ON series_catalog(series_domain);

CREATE TABLE IF NOT EXISTS indicator_definitions (
  indicator_code TEXT PRIMARY KEY,
  indicator_name TEXT NOT NULL,
  axis TEXT NOT NULL CHECK (axis IN ('liquidity', 'inflation', 'transition', 'crypto', 'precious_metals')),
  scored INTEGER NOT NULL CHECK (scored IN (0, 1)),
  description TEXT,
  scoring_notes TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS indicator_series_map (
  indicator_code TEXT NOT NULL,
  series_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('primary', 'secondary', 'supporting')),
  PRIMARY KEY (indicator_code, series_id),
  FOREIGN KEY (indicator_code) REFERENCES indicator_definitions(indicator_code),
  FOREIGN KEY (series_id) REFERENCES series_catalog(series_id)
);

CREATE TABLE IF NOT EXISTS ingestion_runs (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  run_finished_at TEXT,
  script_name TEXT NOT NULL,
  provider_id TEXT,
  status TEXT NOT NULL CHECK (status IN ('started', 'success', 'failed')),
  row_count INTEGER,
  error_message TEXT,
  FOREIGN KEY (provider_id) REFERENCES providers(provider_id)
);

CREATE TABLE IF NOT EXISTS raw_observations (
  series_id TEXT NOT NULL,
  observation_date TEXT NOT NULL,
  available_at TEXT NOT NULL,
  value REAL NOT NULL,
  frequency_observed TEXT NOT NULL CHECK (frequency_observed IN ('daily', 'weekly', 'monthly', 'quarterly')),
  source_url TEXT,
  ingestion_run_id INTEGER,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (series_id, observation_date),
  FOREIGN KEY (series_id) REFERENCES series_catalog(series_id),
  FOREIGN KEY (ingestion_run_id) REFERENCES ingestion_runs(run_id),
  -- A value can never be known before the period it describes.
  CHECK (available_at >= observation_date)
);

CREATE INDEX IF NOT EXISTS idx_raw_observations_date ON raw_observations(observation_date);
CREATE INDEX IF NOT EXISTS idx_raw_observations_available_at ON raw_observations(series_id, available_at);

-- Latest value per series as stored. Fine for dashboards. NOT safe for backtests:
-- use sql/point_in_time.sql, which filters on available_at.
CREATE VIEW IF NOT EXISTS vw_latest_series_values AS
SELECT
  ro.series_id,
  sc.display_name,
  sc.provider_id,
  ro.observation_date,
  ro.available_at,
  ro.value
FROM raw_observations ro
JOIN series_catalog sc ON sc.series_id = ro.series_id
JOIN (
  SELECT series_id, MAX(observation_date) AS max_date
  FROM raw_observations
  GROUP BY series_id
) latest
  ON latest.series_id = ro.series_id
 AND latest.max_date = ro.observation_date;
