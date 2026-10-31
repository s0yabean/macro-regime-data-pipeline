from __future__ import annotations

import json
import math
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Tuple


DEFAULT_API_BASE = "https://pro-api.coingecko.com/api/v3"


def key_header(api_base: str) -> str:
    """Paid keys use the pro host and header; free demo keys use the public host."""
    return "x-cg-pro-api-key" if "pro-api" in api_base else "x-cg-demo-api-key"


def build_curl_command(
    path: str,
    params: Dict[str, Any],
    api_base: str = DEFAULT_API_BASE,
    api_key_env_var: str = "COINGECKO_API_KEY",
) -> str:
    command_parts = [
        f'curl -G "{api_base}{path}"',
        '-H "accept: application/json"',
        f'-H "{key_header(api_base)}: ${api_key_env_var}"',
    ]
    for key, value in params.items():
        command_parts.append(f'--data-urlencode "{key}={value}"')
    return " \\\n  ".join(command_parts)


def iter_time_chunks(start_ts: int, end_ts: int, max_days: int) -> Iterator[Tuple[int, int]]:
    if start_ts > end_ts:
        raise ValueError("start_ts must be <= end_ts")
    if max_days <= 0:
        raise ValueError("max_days must be positive")

    chunk_seconds = max_days * 24 * 60 * 60
    cursor = start_ts
    while cursor <= end_ts:
        right = min(cursor + chunk_seconds, end_ts)
        yield cursor, right
        cursor = right + 1


@dataclass
class CoinGeckoClient:
    api_key: str
    api_base: str = DEFAULT_API_BASE
    timeout_seconds: int = 30
    user_agent: str = "macro-regime-backtesting/0.1"

    def _get_json(self, path: str, params: Dict[str, Any]) -> Any:
        query = urllib.parse.urlencode(params, doseq=True)
        url = f"{self.api_base}{path}?{query}"
        request = urllib.request.Request(
            url=url,
            headers={
                "accept": "application/json",
                key_header(self.api_base): self.api_key,
                "user-agent": self.user_agent,
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            snippet = error_body[:600]
            raise RuntimeError(f"CoinGecko HTTP {exc.code} at {path}. Body: {snippet}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"CoinGecko connection error at {path}: {exc.reason}") from exc
        return json.loads(body)

    def get_coins_markets_page(
        self,
        vs_currency: str = "usd",
        order: str = "market_cap_desc",
        per_page: int = 250,
        page: int = 1,
        sparkline: bool = False,
    ) -> List[Dict[str, Any]]:
        params = {
            "vs_currency": vs_currency,
            "order": order,
            "per_page": per_page,
            "page": page,
            "sparkline": str(sparkline).lower(),
        }
        payload = self._get_json("/coins/markets", params)
        if not isinstance(payload, list):
            raise RuntimeError(f"Unexpected /coins/markets payload type: {type(payload)!r}")
        return payload

    def get_top_n_by_market_cap(self, top_n: int, vs_currency: str = "usd") -> List[Dict[str, Any]]:
        if top_n <= 0:
            return []
        page_size = 250
        page_count = int(math.ceil(top_n / page_size))
        output: List[Dict[str, Any]] = []
        for page in range(1, page_count + 1):
            output.extend(
                self.get_coins_markets_page(
                    vs_currency=vs_currency,
                    order="market_cap_desc",
                    per_page=page_size,
                    page=page,
                    sparkline=False,
                )
            )
        return output[:top_n]

    def get_ohlc_range(
        self,
        coin_id: str,
        from_ts: int,
        to_ts: int,
        interval: str,
        vs_currency: str = "usd",
    ) -> List[List[float]]:
        params = {
            "vs_currency": vs_currency,
            "from": from_ts,
            "to": to_ts,
            "interval": interval,
        }
        payload = self._get_json(f"/coins/{coin_id}/ohlc/range", params)
        if not isinstance(payload, list):
            raise RuntimeError(f"Unexpected /coins/{coin_id}/ohlc/range payload type: {type(payload)!r}")
        return payload

    def get_market_chart_range(
        self,
        coin_id: str,
        from_ts: int,
        to_ts: int,
        vs_currency: str = "usd",
        interval: Optional[str] = None,
    ) -> Dict[str, List[List[float]]]:
        params: Dict[str, Any] = {
            "vs_currency": vs_currency,
            "from": from_ts,
            "to": to_ts,
        }
        if interval:
            params["interval"] = interval
        payload = self._get_json(f"/coins/{coin_id}/market_chart/range", params)
        if not isinstance(payload, dict):
            raise RuntimeError(
                f"Unexpected /coins/{coin_id}/market_chart/range payload type: {type(payload)!r}"
            )
        return payload
