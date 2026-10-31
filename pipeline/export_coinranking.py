#!/usr/bin/env python3
"""Export long-history crypto indicators from Coinranking to a tidy CSV.

Why a second crypto source: CoinGecko's history did not reach back far enough for
multi-cycle backtests. Coinranking's stats endpoints go back to 2013. The output CSV
is in the shape pipeline/ingest_csv.py loads:

    series_id,observation_date,value,source_url

Notes on the data (see docs/data-sources.md):
- bitcoin-dominance-history with timePeriod=all comes back roughly weekly, not daily.
  The exporter prints the median spacing so you can set the catalog frequency to match.
- market-cap-history has gaps. Stablecoin supply is written only for days where both
  USDT and USDC are present (strict intersection, no imputation).

Needs COINRANKING_API_KEY.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import statistics
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

BASE_URL = "https://api.coinranking.com/v2"
USD_REFERENCE_UUID = "yhjMzLPhuIDl"  # Coinranking's public id for US Dollar


def request_json(path: str, params: Dict[str, Any], api_key: str, timeout: int = 45) -> Dict[str, Any]:
    url = f"{BASE_URL}/{path.lstrip('/')}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "macro-regime-data-pipeline/1.0", "x-access-token": api_key},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace").strip().replace("\n", " ")[:300]
        raise RuntimeError(f"HTTP {exc.code} for {path}: {body}") from exc
    if payload.get("status") != "success":
        raise RuntimeError(f"Coinranking error for {path}: {payload.get('type')} - {payload.get('message')}")
    return payload


def to_date(ts: int) -> str:
    return dt.datetime.fromtimestamp(float(ts), tz=dt.timezone.utc).date().isoformat()


def to_float(v: Any):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def coin_uuid(symbol: str, api_key: str) -> str:
    coins = request_json("coins", {"search": symbol, "limit": 50, "orderBy": "marketCap"}, api_key)["data"]["coins"]
    exact = [c for c in coins if str(c.get("symbol", "")).upper() == symbol.upper()]
    if not (exact or coins):
        raise RuntimeError(f"No coin found for {symbol}")
    return str((exact or coins)[0]["uuid"])


def market_cap_by_date(uuid: str, api_key: str, limit: int) -> Dict[str, float]:
    hist = request_json(f"coin/{uuid}/market-cap-history", {"interval": "day", "limit": limit}, api_key)
    out: Dict[str, float] = {}
    for item in hist.get("data", {}).get("history", []):
        # The endpoint has returned the value under different keys over time.
        val = next((to_float(item.get(k)) for k in ("marketCap", "price") if to_float(item.get(k)) is not None), None)
        if item.get("timestamp") is not None and val is not None:
            out[to_date(int(item["timestamp"]))] = val
    return out


def median_spacing_days(dates: List[str]) -> float:
    ds = sorted(dt.date.fromisoformat(d) for d in dates)
    gaps = [(b - a).days for a, b in zip(ds, ds[1:])]
    return statistics.median(gaps) if gaps else 0.0


def main() -> int:
    p = argparse.ArgumentParser(description="Export Coinranking BTC dominance + stablecoin supply to CSV")
    p.add_argument("--out-csv", default="data/exports/coinranking_crypto.csv")
    p.add_argument("--start-date", default="2013-01-01")
    p.add_argument("--time-period", default="all", help="Coinranking timePeriod, e.g. 1y, 3y, 5y, all")
    p.add_argument("--market-cap-limit", type=int, default=5000)
    args = p.parse_args()

    api_key = os.environ.get("COINRANKING_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("Missing COINRANKING_API_KEY (see .env.example).")

    rows: List[Dict[str, Any]] = []

    dom = request_json(
        "stats/bitcoin-dominance-history",
        {"referenceCurrencyUuid": USD_REFERENCE_UUID, "timePeriod": args.time_period},
        api_key,
    )
    dom_points = {to_date(int(i["timestamp"])): to_float(i.get("percentage")) for i in dom.get("data", [])}
    dom_points = {d: v for d, v in dom_points.items() if v is not None and d >= args.start_date}
    src = f"{BASE_URL}/stats/bitcoin-dominance-history"
    rows += [{"series_id": "CRYPTO:BTC_DOMINANCE_PCT", "observation_date": d, "value": v, "source_url": src}
             for d, v in sorted(dom_points.items())]
    print(f"btc_dominance rows={len(dom_points)} median_spacing_days={median_spacing_days(list(dom_points)):.1f}")

    usdt = market_cap_by_date(coin_uuid("USDT", api_key), api_key, args.market_cap_limit)
    usdc = market_cap_by_date(coin_uuid("USDC", api_key), api_key, args.market_cap_limit)
    shared = sorted(d for d in set(usdt) & set(usdc) if d >= args.start_date)
    rows += [{"series_id": "CRYPTO:STABLECOIN_SUPPLY_USD", "observation_date": d, "value": usdt[d] + usdc[d],
              "source_url": f"{BASE_URL}/coin/{{uuid}}/market-cap-history (USDT+USDC)"} for d in shared]
    if shared:
        span = (dt.date.fromisoformat(shared[-1]) - dt.date.fromisoformat(shared[0])).days + 1
        print(f"stablecoin_supply rows={len(shared)} calendar_days={span} coverage_pct={len(shared) / span * 100:.3f}")

    out = Path(args.out_csv)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["series_id", "observation_date", "value", "source_url"])
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {out} ({len(rows)} rows). Load with: python pipeline/ingest_csv.py {out} --provider-id COINRANKING")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
