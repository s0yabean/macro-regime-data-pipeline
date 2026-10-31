PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS coins (
  coin_id TEXT PRIMARY KEY,
  symbol TEXT NOT NULL,
  name TEXT NOT NULL,
  first_seen_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS top_coins_snapshot (
  snapshot_ts TEXT NOT NULL,
  rank INTEGER NOT NULL,
  coin_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  name TEXT NOT NULL,
  market_cap REAL,
  total_volume REAL,
  source_endpoint TEXT NOT NULL DEFAULT '/coins/markets',
  PRIMARY KEY (snapshot_ts, rank),
  FOREIGN KEY (coin_id) REFERENCES coins (coin_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_top_snapshot_coin
  ON top_coins_snapshot (snapshot_ts, coin_id);

CREATE TABLE IF NOT EXISTS ohlc_bars (
  coin_id TEXT NOT NULL,
  timeframe TEXT NOT NULL CHECK (timeframe IN ('1h', '1d')),
  ts INTEGER NOT NULL, -- Unix timestamp in milliseconds
  open REAL NOT NULL,
  high REAL NOT NULL,
  low REAL NOT NULL,
  close REAL NOT NULL,
  source_endpoint TEXT NOT NULL DEFAULT '/coins/{id}/ohlc/range',
  PRIMARY KEY (coin_id, timeframe, ts),
  FOREIGN KEY (coin_id) REFERENCES coins (coin_id)
);

CREATE INDEX IF NOT EXISTS idx_ohlc_timeframe_ts
  ON ohlc_bars (timeframe, ts);

CREATE TABLE IF NOT EXISTS market_metrics (
  coin_id TEXT NOT NULL,
  timeframe TEXT NOT NULL CHECK (timeframe IN ('1h', '1d')),
  ts INTEGER NOT NULL, -- Unix timestamp in milliseconds
  price REAL,
  market_cap REAL,
  volume_24h REAL,
  source_endpoint TEXT NOT NULL DEFAULT '/coins/{id}/market_chart/range',
  PRIMARY KEY (coin_id, timeframe, ts),
  FOREIGN KEY (coin_id) REFERENCES coins (coin_id)
);

CREATE INDEX IF NOT EXISTS idx_market_metrics_timeframe_ts
  ON market_metrics (timeframe, ts);

CREATE TABLE IF NOT EXISTS ingest_runs (
  run_id TEXT PRIMARY KEY,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  script_name TEXT NOT NULL,
  status TEXT NOT NULL,
  details TEXT
);

