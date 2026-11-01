from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, Sequence


def connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def apply_schema(conn: sqlite3.Connection, schema_path: str) -> None:
    schema_sql = Path(schema_path).read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    conn.commit()


def upsert_coins(conn: sqlite3.Connection, coins: Sequence[Dict[str, Any]]) -> None:
    rows = [
        (
            str(coin["id"]),
            str(coin.get("symbol", "")).lower(),
            str(coin.get("name", "")),
        )
        for coin in coins
        if coin.get("id")
    ]
    conn.executemany(
        """
        INSERT INTO coins (coin_id, symbol, name)
        VALUES (?, ?, ?)
        ON CONFLICT(coin_id) DO UPDATE SET
          symbol=excluded.symbol,
          name=excluded.name
        """,
        rows,
    )
    conn.commit()


def insert_top_snapshot(conn: sqlite3.Connection, snapshot_ts: str, coins: Sequence[Dict[str, Any]]) -> None:
    rows = []
    for idx, coin in enumerate(coins, start=1):
        rank = int(coin.get("market_cap_rank") or idx)
        rows.append(
            (
                snapshot_ts,
                rank,
                str(coin["id"]),
                str(coin.get("symbol", "")).lower(),
                str(coin.get("name", "")),
                float(coin["market_cap"]) if coin.get("market_cap") is not None else None,
                float(coin["total_volume"]) if coin.get("total_volume") is not None else None,
            )
        )
    conn.executemany(
        """
        INSERT INTO top_coins_snapshot (
          snapshot_ts, rank, coin_id, symbol, name, market_cap, total_volume
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(snapshot_ts, rank) DO UPDATE SET
          coin_id=excluded.coin_id,
          symbol=excluded.symbol,
          name=excluded.name,
          market_cap=excluded.market_cap,
          total_volume=excluded.total_volume
        """,
        rows,
    )
    conn.commit()


def upsert_ohlc_rows(
    conn: sqlite3.Connection,
    coin_id: str,
    timeframe: str,
    ohlc_rows: Sequence[Sequence[float]],
) -> None:
    rows = []
    for row in ohlc_rows:
        if len(row) < 5:
            continue
        ts, open_, high, low, close = row[:5]
        rows.append((coin_id, timeframe, int(ts), float(open_), float(high), float(low), float(close)))

    if not rows:
        return

    conn.executemany(
        """
        INSERT INTO ohlc_bars (coin_id, timeframe, ts, open, high, low, close)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(coin_id, timeframe, ts) DO UPDATE SET
          open=excluded.open,
          high=excluded.high,
          low=excluded.low,
          close=excluded.close
        """,
        rows,
    )
    conn.commit()


def upsert_market_metrics(
    conn: sqlite3.Connection,
    coin_id: str,
    timeframe: str,
    prices: Sequence[Sequence[float]],
    market_caps: Sequence[Sequence[float]],
    total_volumes: Sequence[Sequence[float]],
) -> None:
    combined: Dict[int, Dict[str, float]] = {}

    for ts, value in prices:
        combined.setdefault(int(ts), {})["price"] = float(value)
    for ts, value in market_caps:
        combined.setdefault(int(ts), {})["market_cap"] = float(value)
    for ts, value in total_volumes:
        combined.setdefault(int(ts), {})["volume_24h"] = float(value)

    rows = [
        (
            coin_id,
            timeframe,
            ts,
            metric.get("price"),
            metric.get("market_cap"),
            metric.get("volume_24h"),
        )
        for ts, metric in combined.items()
    ]
    if not rows:
        return

    conn.executemany(
        """
        INSERT INTO market_metrics (
          coin_id, timeframe, ts, price, market_cap, volume_24h
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(coin_id, timeframe, ts) DO UPDATE SET
          price=excluded.price,
          market_cap=excluded.market_cap,
          volume_24h=excluded.volume_24h
        """,
        rows,
    )
    conn.commit()

