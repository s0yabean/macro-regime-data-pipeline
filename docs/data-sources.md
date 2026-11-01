# Data sources and the decisions behind them

The catalog ([`config/series_catalog.yaml`](../config/series_catalog.yaml)) holds 55
series (53 active) mapped to 16 indicators on five axes: liquidity, inflation,
transition, crypto and precious metals.

| Provider | Key | Active series | Script |
|---|---|---|---|
| Yahoo Finance (yfinance) | none | 33 | `pipeline/ingest_yfinance.py` |
| FRED | free `FRED_API_KEY` | 16 | `pipeline/ingest_fred.py` |
| CoinGecko | `COINGECKO_API_KEY` | 2 | `pipeline/ingest_coingecko.py` |
| Coinranking (long history) | `COINRANKING_API_KEY` | loads into the 2 crypto series | `pipeline/export_coinranking.py` then `ingest_csv.py` |
| Staged CSV | none | 2 | `pipeline/ingest_csv.py` |

## Indicator map

| Code | Indicator | Axis | Primary series | Fallback / supporting |
|---|---|---|---|---|
| L1 | US M2 money supply | liquidity | `FRED:M2SL` | |
| L2 | Fed net liquidity | liquidity | `FRED:WALCL` | `FRED:WTREGEN`, `FRED:RRPONTSYD` |
| L3 | US dollar index | liquidity | `YF:DX-Y.NYB` | `FRED:DTWEXBGS` |
| L4 | High-yield credit spreads | liquidity | `FRED:BAMLH0A0HYM2` | |
| L5 | Policy rate (Fed funds proxy) | liquidity | `FRED:FEDFUNDS` | |
| I1 | CPI 3-month annualised | inflation | `FRED:CPIAUCSL` | |
| I2 | 10Y real yield | inflation | `FRED:DFII10` | `FRED:GS10` |
| I3 | Copper and copper/gold ratio | inflation | `FRED:PCOPPUSDM` | `YF:HG=F`, `YF:GC=F` |
| I4 | Oil (WTI / Brent) | inflation | `FRED:DCOILWTICO` | `FRED:DCOILBRENTEU`, `YF:CL=F`, `YF:BZ=F` |
| T1 | Yield curve 10Y-2Y | transition | `FRED:T10Y2Y` | |
| T2 | ISM manufacturing PMI | transition | `MANUAL:ISM_PMI` | |
| T3 | VIX | transition | `FRED:VIXCLS` | `YF:^VIX` |
| C1 | BTC dominance | crypto | `CRYPTO:BTC_DOMINANCE_PCT` | |
| C2 | Stablecoin supply | crypto | `CRYPTO:STABLECOIN_SUPPLY_USD` | USDT / USDC components |
| P1 | Gold/silver ratio | precious metals | `YF:GC=F` | `YF:SI=F` |
| P2 | Central-bank gold purchases | precious metals | `MANUAL:CB_GOLD_NET_TONNES` | |

The rest of the yfinance series are tradable assets and benchmarks (SPY, QQQ, TLT,
GLD, sector and commodity ETFs, BTC and ETH) for the execution clock.

`coverage_status` says how faithful a series is to the indicator it stands for:
`exact` means it is the thing itself; `proxy` means it is a close substitute (FEDFUNDS
for "central bank policy", M2 for "global liquidity") and an agent or analyst should
say so when using it.

## Decisions and gotchas

**Unit normalisation on ingest.** Fed net liquidity is WALCL - WTREGEN - RRPONTSYD.
FRED reports the first two in millions of USD and the reverse repo in billions.
`ingest_fred.py` multiplies RRPONTSYD by 1,000 before writing, so the three
components can be combined without a unit bug.

**Two series share the name "dollar index".** `YF:DX-Y.NYB` is the market ICE index (about
100 in March 2026). `FRED:DTWEXBGS` is the Fed's nominal broad index (about 120 on the same
dates). Both values are correct; they measure different baskets. The market index is primary
because that is the one the framework means, and the broad index stays as the fallback.

**London gold fix is gone from FRED.** FRED removed the ICE Benchmark Administration
series (including `GOLDAMGBD228NLBM`) in 2022
([announcement](https://news.research.stlouisfed.org/2022/01/ice-benchmark-administration-ltd-iba-data-to-be-removed-from-fred/)).
Gold comes from `YF:GC=F` (futures) and the ETFs instead. Futures and a spot fixing
are different instruments; treat GC=F as a proxy for the fix.

**Short history on some FRED series.** The ICE BofA spread series such as
`BAMLH0A0HYM2` are served with a limited history window through the API. The
`active_series_have_rows` and coverage summary in the health report make this visible
before anyone backtests a decade on three years of data.

**Two crypto sources.** CoinGecko is the default for daily BTC dominance and stablecoin
market caps. Its history did not reach back far enough for multi-cycle tests, so
`export_coinranking.py` pulls the same two indicators from Coinranking back to 2013.
Two things to know about that export:

- `bitcoin-dominance-history` with `timePeriod=all` comes back roughly weekly. The
  exporter prints the median spacing; if you load the long history, set
  `CRYPTO:BTC_DOMINANCE_PCT` to `native_frequency: weekly` so the health checks and
  staleness rules match.
- `market-cap-history` has gaps. Stablecoin supply is written only on days where both
  USDT and USDC are present (strict intersection). The exporter prints the resulting
  coverage percentage. Zero-filling a missing component would understate supply on
  those days and create fake drops in the series.

**No free API for two indicators.** ISM manufacturing PMI and central-bank gold
purchases are published as monthly releases. The pipeline does not scrape them. Copy
the monthly print into a CSV (`series_id,observation_date,value[,source_url]`) and load
it with `ingest_csv.py`, which applies the same release-lag rule as the API ingesters
and rejects any `available_at` earlier than the lag allows. See
[`examples/manual_series_SYNTHETIC.csv`](../examples/manual_series_SYNTHETIC.csv) for
the format (the values in it are made up).

**Failures are logged, not hidden.** Every run writes a row to `ingestion_runs` with
status, row count and the error text per failed series. One bad series does not stop
the others, and `last_run_succeeded` in the health report fails until it is fixed.
