"""Bybit REST candle verification for exact closed-candle parity."""
import pandas as pd
import urllib.request
import urllib.parse
import json
import time
from typing import Any


def verify_candle_ohlc(symbol: str, timeframe: str, open_time: pd.Timestamp, 
                       expected: dict[str, float], tolerance: float = 1e-6) -> dict[str, Any]:
    """
    Verify candle OHLC against Bybit REST API.
    
    Returns:
        {"ok": True} if match, or {"ok": False, "error": str, "rest": dict, "cache": dict}
    """
    if open_time.tzinfo is None:
        open_time = open_time.tz_localize("UTC")
    else:
        open_time = open_time.tz_convert("UTC")
    
    start_ms = int(open_time.timestamp() * 1000)
    interval_map = {"15m": "15", "4h": "240"}
    interval = interval_map.get(timeframe)
    if not interval:
        return {"ok": False, "error": f"unsupported timeframe: {timeframe}"}
    
    # Bybit REST v5 kline endpoint
    params = {
        "category": "linear",
        "symbol": symbol.upper(),
        "interval": interval,
        "start": str(start_ms),
        "limit": "1"
    }
    url = f"https://api.bybit.com/v5/market/kline?{urllib.parse.urlencode(params)}"
    
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "FusionQuantum/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        
        if data.get("retCode") != 0:
            return {"ok": False, "error": f"API error: {data.get('retMsg', 'unknown')}"}
        
        rows = data.get("result", {}).get("list", [])
        if not rows:
            return {"ok": False, "error": "no candle returned"}
        
        # Bybit returns: [startTime, open, high, low, close, volume, turnover]
        row = rows[0]
        rest_candle = {
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5])
        }
        
        # Compare OHLC
        for k in ("open", "high", "low", "close"):
            if abs(rest_candle[k] - expected[k]) > tolerance:
                return {
                    "ok": False,
                    "error": f"mismatch {k}: rest={rest_candle[k]:.6f} cache={expected[k]:.6f} delta={abs(rest_candle[k]-expected[k]):.6f}",
                    "rest": rest_candle,
                    "cache": expected,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "open_time": open_time.isoformat()
                }
        
        return {"ok": True, "rest": rest_candle, "cache": expected}
    
    except Exception as exc:
        return {"ok": False, "error": f"REST fetch failed: {repr(exc)}"}


def verify_setup_candles(setup: dict[str, Any]) -> dict[str, Any]:
    """
    Verify MSS candle OHLC from setup evidence against Bybit REST.
    
    Returns:
        {"ok": True} or {"ok": False, "error": str, "details": dict}
    """
    evidence = setup.get("evidence", {})
    mss = evidence.get("mss_candle")
    if not mss:
        return {"ok": False, "error": "no mss_candle in evidence"}
    
    symbol = setup.get("symbol")
    if not symbol:
        return {"ok": False, "error": "no symbol in setup"}
    
    open_time_str = mss.get("open_time")
    if not open_time_str:
        return {"ok": False, "error": "no open_time in mss_candle"}
    
    try:
        open_time = pd.Timestamp(open_time_str)
    except Exception as exc:
        return {"ok": False, "error": f"invalid open_time: {repr(exc)}"}
    
    expected = {
        "open": float(mss["open"]),
        "high": float(mss["high"]),
        "low": float(mss["low"]),
        "close": float(mss["close"]),
        "volume": float(mss.get("volume", 0))
    }
    
    result = verify_candle_ohlc(symbol, "15m", open_time, expected)
    if not result["ok"]:
        result["setup_id"] = setup.get("setup_id")
        result["setup_symbol"] = symbol
    
    return result
