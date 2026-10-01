#!/usr/bin/env python3
"""
Nexus data fetcher module.
"""
import os
import time
import json
import datetime as _dt
import pandas as pd
import numpy as np
import httpx
from pathlib import Path

# Bybit kline API has no taker split — real CVD comes from trades stream
# We will enrich kline data with taker buy volume from our trades collector
TRADES_FILE = os.getenv("TRADES_DATA_PATH", "/app/runtime/revo/trades_bybit.jsonl")

# Cache for trades data (read every 30 seconds)
_trades_data_cache = None
_trades_data_cache_time = 0
_trades_data_cache_ttl = 30  # seconds


def _get_trades_data():
    global _trades_data_cache, _trades_data_cache_time
    now = time.time()
    if _trades_data_cache is not None and now - _trades_data_cache_time < _trades_data_cache_ttl:
        return _trades_data_cache
    _trades_data_cache = {}
    try:
        if os.path.exists(TRADES_FILE):
            with open(TRADES_FILE, "r") as f:
                for line in f:
                    record = json.loads(line.strip())
                    key = (record["symbol"], record["tf"], record["timestamp"])
                    _trades_data_cache[key] = {
                        "taker_buy_base": record["taker_buy_base"],
                        "taker_buy_quote": record["taker_buy_quote"],
                    }
    except Exception as e:
        print(f"[nexus_data] Error reading trades data: {e}")
    _trades_data_cache_time = now
    return _trades_data_cache


COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "taker_buy_base",
    "taker_buy_quote",
    "quote_volume",
]

TF_SEC = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}

_NEXUS_URL = os.getenv("NEXUS_API_URL") or os.getenv("NEXUS_URL") or "http://localhost:8000"
_STALE_MULT = int(os.getenv("STALE_MULT", "4"))


def _parse_utc_datetime(val) -> _dt.datetime:
    """Safely parse various datetime/timestamp types into UTC datetime."""
    if isinstance(val, (int, float)):
        sec = val / 1000.0 if val > 1e11 else float(val)
        return _dt.datetime.fromtimestamp(sec, tz=_dt.timezone.utc)
    dt = pd.to_datetime(val, utc=True)
    if hasattr(dt, "to_pydatetime"):
        return dt.to_pydatetime()
    return _dt.datetime.fromtimestamp(pd.Timestamp(dt).timestamp(), tz=_dt.timezone.utc)


def _get_client() -> httpx.Client:
    if not hasattr(_get_client, "client"):
        _get_client.client = httpx.Client(timeout=httpx.Timeout(15))
    return _get_client.client


def _to_df(rows: list[dict], symbol: str = "?", tf: str = "?", exchange: str = "?") -> pd.DataFrame:
    """Convert Nexus API response to same DataFrame format as Binance raw."""
    if not rows:
        return pd.DataFrame(columns=COLUMNS)

    df = pd.DataFrame(rows)

    # Explicit schema mapping BEFORE any default-filling: the Nexus API returns
    # taker_buy_vol / quote_vol, the engine expects taker_buy_base / quote_volume.
    for src, dst in {
        "taker_buy_vol": "taker_buy_base",
        "taker_buy_quote_vol": "taker_buy_quote",
        "quote_vol": "quote_volume",
    }.items():
        if src in df.columns and dst not in df.columns:
            df[dst] = df[src]

    open_times = pd.to_datetime(df["open_time"])

    for col in COLUMNS:
        if col not in df.columns:
            df[col] = 0.0

    for col in ["open", "high", "low", "close", "volume", "taker_buy_base", "taker_buy_quote", "quote_volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    # Enrich with real taker buy volume from trades collector (if available)
    trades_cache = _get_trades_data()
    if trades_cache:
        for idx, row in df.iterrows():
            t_val = row["open_time"]
            if hasattr(t_val, "timestamp"):
                candle_start_sec = int(t_val.timestamp())
            elif isinstance(t_val, (int, float)):
                candle_start_sec = int(t_val // 1000 if t_val > 1e11 else t_val)
            else:
                candle_start_sec = int(pd.to_datetime(t_val).timestamp())
            key = (symbol.upper(), tf, candle_start_sec)
            if key in trades_cache:
                df.at[idx, "taker_buy_base"] = trades_cache[key]["taker_buy_base"]
                df.at[idx, "taker_buy_quote"] = trades_cache[key]["taker_buy_quote"]

    # Schema-mismatch alarm: taker_buy_base all zero while volume is non-zero
    # Warn for Binance or non-Bybit sources since Bybit native REST klines don't provide taker split
    if exchange.lower() != "bybit" and float(df["volume"].sum()) > 0 and float(df["taker_buy_base"].abs().sum()) == 0:
        print(f"[WARN] SCHEMA MISMATCH {symbol} {tf}: taker_buy_base is all-zero "
              f"while volume > 0 — CVD will be 0 (check API column names)")

    # Strip timezone (engine expects naive datetimes like Binance raw)
    if open_times.dt.tz is not None:
        open_times = open_times.dt.tz_localize(None)
    df["open_time"] = open_times

    keep = [c for c in COLUMNS if c in df.columns]
    return df[keep].reset_index(drop=True)


def fetch_recent(symbol: str, interval: str, limit: int = 500) -> pd.DataFrame:
    """Fetch recent klines from Nexus API (which aggregates Binance and Bybit)."""
    tf = interval
    df = pd.DataFrame(columns=COLUMNS)
    now = _dt.datetime.now(_dt.timezone.utc)
    tf_sec = TF_SEC.get(tf)
    if not tf_sec:
        if tf.endswith("m"):
            tf_sec = int(tf[:-1]) * 60
        elif tf.endswith("h"):
            tf_sec = int(tf[:-1]) * 3600
        elif tf.endswith("d"):
            tf_sec = int(tf[:-1]) * 86400
        else:
            tf_sec = 60
    stale_limit = _STALE_MULT * tf_sec
    # Prefer the freshest source. Binance is tried first, but if its newest
    # candle is stale (e.g. symbol delisted from Binance futures -> the row
    # freezes into a zombie), fall back to Bybit instead of trusting a frozen
    # frame. The last source is kept as fallback if every source is stale.
    for exchange in ("binance", "bybit"):
        try:
            r = _get_client().get(
                f"{_NEXUS_URL}/klines/{exchange}/{symbol}",
                params={"tf": tf, "limit": limit},
                timeout=15,
            )
            if r.status_code == 200 and r.json().get("count", 0) > 0:
                cand = _to_df(r.json()["data"], symbol=symbol, tf=tf, exchange=exchange)
                if len(cand) == 0:
                    continue
                df = cand  # keep as fallback even if stale
                last_open = _parse_utc_datetime(cand["open_time"].iloc[-1])
                if (now - last_open).total_seconds() <= stale_limit:
                    break  # fresh enough — stop here
        except Exception:
            continue

    # If a multi-minute tf is empty/stale on every source, synthesize it from
    # 1m (which may be live on a different exchange). Rescues symbols that lost
    # native 3m when delisted from Binance but still trade on Bybit — Bybit does
    # not store 3m for any symbol, so 3m is otherwise Binance-only.
    _needs_synth = len(df) == 0
    if not _needs_synth:
        _lo = _parse_utc_datetime(df["open_time"].iloc[-1])
        _needs_synth = (now - _lo).total_seconds() > stale_limit
    if _needs_synth and tf in ("3m", "5m", "15m", "30m", "1h"):
        step = {"3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60}[tf]
        m1 = fetch_recent(symbol, "1m", min(limit * step, 2000))
        if len(m1) == 0:
            return pd.DataFrame(columns=COLUMNS)
        g = m1.set_index(pd.to_datetime(m1["open_time"]))
        agg = {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
            "taker_buy_base": "sum",
            "taker_buy_quote": "sum",
        }
        if "quote_volume" in m1.columns:
            agg["quote_volume"] = "sum"
        out = (g.resample(f"{step}min", label="left", closed="left")
                 .agg(agg)
                 .dropna(subset=["open"])
                 .reset_index())
        out = out.rename(columns={out.columns[0]: "open_time"})
        keep = [c for c in COLUMNS if c in out.columns]
        out = out[keep].reset_index(drop=True)
        return out

    # ── Stale-data guard: lag = now_utc - last open_time ──
    lag_sec = None
    stale = False
    if len(df) > 0:
        last_open = _parse_utc_datetime(df["open_time"].iloc[-1])
        lag_sec = (now - last_open).total_seconds()
        if lag_sec > stale_limit:
            stale = True
            print(f"[WARN] STALE DATA {symbol} {tf} lag={int(lag_sec)}s "
                  f"(> {int(stale_limit)}s = {_STALE_MULT:g}x timeframe)")
    df.attrs["lag_sec"] = lag_sec
    df.attrs["stale"] = stale
    return df