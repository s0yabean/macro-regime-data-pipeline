#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   export COINGECKO_API_KEY="your_key_here"
#   bash src/ingest/curl_examples.sh

: "${COINGECKO_API_KEY:?Please export COINGECKO_API_KEY before running this script.}"

API_BASE="${COINGECKO_API_BASE:-https://pro-api.coingecko.com/api/v3}"
COIN_ID="${COIN_ID:-bitcoin}"

NOW_TS="$(python - <<'PY'
import time
print(int(time.time()))
PY
)"
FROM_TS="$((NOW_TS - 7*24*60*60))"

echo "Coin: ${COIN_ID}"
echo "Range: last 7 days (unix ${FROM_TS} -> ${NOW_TS})"
echo

echo "1) Top coins preview (first 1 by market cap)"
curl -sS -G "${API_BASE}/coins/markets" \
  -H "accept: application/json" \
  -H "x-cg-pro-api-key: ${COINGECKO_API_KEY}" \
  --data-urlencode "vs_currency=usd" \
  --data-urlencode "order=market_cap_desc" \
  --data-urlencode "per_page=1" \
  --data-urlencode "page=1" \
  --data-urlencode "sparkline=false"
echo

echo "2) OHLC hourly (one coin, last 7 days)"
curl -sS -G "${API_BASE}/coins/${COIN_ID}/ohlc/range" \
  -H "accept: application/json" \
  -H "x-cg-pro-api-key: ${COINGECKO_API_KEY}" \
  --data-urlencode "vs_currency=usd" \
  --data-urlencode "from=${FROM_TS}" \
  --data-urlencode "to=${NOW_TS}" \
  --data-urlencode "interval=hourly"
echo

echo "3) Market chart range (price, market cap, rolling 24h volume)"
curl -sS -G "${API_BASE}/coins/${COIN_ID}/market_chart/range" \
  -H "accept: application/json" \
  -H "x-cg-pro-api-key: ${COINGECKO_API_KEY}" \
  --data-urlencode "vs_currency=usd" \
  --data-urlencode "from=${FROM_TS}" \
  --data-urlencode "to=${NOW_TS}"
echo
