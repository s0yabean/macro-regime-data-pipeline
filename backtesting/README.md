# Backtesting

Small, dependency-free backtesters that read local SQLite. Research tooling, not
investment advice.

| Path | What it does |
|---|---|
| `src/ingest/fetch_top_coins.py` | Snapshot the top N coins by market cap from CoinGecko into `top_coins_snapshot` |
| `src/ingest/fetch_coin_history.py` | Pull hourly/daily OHLC and market metrics per coin, chunked to the API's range limits (`--dry-run` prints the call plan and cost estimate) |
| `src/ingest/curl_examples.sh` | The same endpoints as plain curl, for checking a key before writing any data |
| `src/backtest/strategies/sma_cross.py` | Long-only SMA crossover; fills on the bar after the signal |
| `src/backtest/strategies/median_long_short.py` | Cross-sectional median long/short, equal weight, equal leg count |
| `src/backtest/run_strategy.py` | CLI for both strategies; reads `ohlc_bars` or the macro pipeline's `raw_observations` |
| `src/sample/make_synthetic_bars.py` | SYNTHETIC coin universe so everything runs offline |
| `sql/schema.sql` | Tables: `coins`, `top_coins_snapshot`, `ohlc_bars`, `market_metrics`, `ingest_runs` |

## Run offline (synthetic data)

```bash
python backtesting/src/sample/make_synthetic_bars.py
python backtesting/src/backtest/run_strategy.py --strategy sma_cross --coin-id synth-1 \
  --start-date 2024-01-01 --end-date 2025-12-31
python backtesting/src/backtest/run_strategy.py --strategy median_long_short \
  --start-date 2024-01-01 --end-date 2025-12-31 --lookback 1 --hold 1
```

## Run on the macro pipeline's prices

```bash
python backtesting/src/backtest/run_strategy.py --source macro --series-id YF:BTC-USD \
  --db-path data/macro.db --start-date 2024-01-01 --end-date 2025-12-31
```

## Real crypto data

Set `COINGECKO_API_KEY` (and `COINGECKO_API_BASE=https://api.coingecko.com/api/v3` for a
free demo key; the range endpoints may need a paid plan), then:

```bash
python backtesting/src/ingest/fetch_top_coins.py --top-n 50 --no-csv
python backtesting/src/ingest/fetch_coin_history.py --coin-id bitcoin --coin-id ethereum \
  --start-date 2025-01-01 --end-date 2025-06-30 --dry-run
```

## Caveats

- A universe built from today's top-N market cap has survivorship bias for past
  periods. Snapshot the universe on each rebalance date instead.
- Returns are gross: no fees, slippage, funding or borrow costs.
- `median_long_short` reports returns on gross exposure. It has no position sizing.
