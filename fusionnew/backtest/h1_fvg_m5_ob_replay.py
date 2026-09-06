"""Isolated no-lookahead replay: H1 FVG -> retest -> causal M5 OB/FVG."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from backtest.data import fetch_klines, fetch_oi_days


def _atr(df: pd.DataFrame, period: int = 14) -> np.ndarray:
    h, l, c = (df[x].to_numpy(float) for x in ("high", "low", "close"))
    prev = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(abs(h - prev), abs(l - prev)))
    return pd.Series(tr).rolling(period, min_periods=1).mean().to_numpy()


def _h1_fvgs(h1: pd.DataFrame) -> list[dict]:
    out = []
    for i in range(2, len(h1)):
        area = None
        if h1.low.iloc[i] > h1.high.iloc[i - 2]:
            area = {"side": "BULL", "low": float(h1.high.iloc[i - 2]),
                    "high": float(h1.low.iloc[i])}
        elif h1.high.iloc[i] < h1.low.iloc[i - 2]:
            area = {"side": "BEAR", "low": float(h1.high.iloc[i]),
                    "high": float(h1.low.iloc[i - 2])}
        if area is None:
            continue
        area["formed"] = h1.open_time.iloc[i] + pd.Timedelta(hours=1)
        area["invalidated"] = None
        for k in range(i + 1, len(h1)):
            broken = (area["side"] == "BULL" and h1.close.iloc[k] < area["low"]) or (
                area["side"] == "BEAR" and h1.close.iloc[k] > area["high"])
            if broken:
                area["invalidated"] = h1.open_time.iloc[k] + pd.Timedelta(hours=1)
                break
        out.append(area)
    return out


def causal_setups(h1: pd.DataFrame, m5: pd.DataFrame, atr_period: int = 14,
                  displacement_atr: float = 1.0, rr: float = 3.0,
                  atr_buffer: float = 0.25) -> list[dict]:
    """Return setups known only after causal three-candle M5 FVG closes."""
    atr = _atr(m5, atr_period); setups = []
    for area in _h1_fvgs(h1):
        eligible = m5.index[(m5.open_time >= area["formed"]) & (
            True if area["invalidated"] is None else m5.open_time < area["invalidated"])].tolist()
        for ob in eligible:
            if ob + 2 >= len(m5):
                continue
            touched = m5.low.iloc[ob] <= area["high"] and m5.high.iloc[ob] >= area["low"]
            if not touched:
                continue
            j = ob + 2
            if area["invalidated"] is not None and m5.open_time.iloc[j] >= area["invalidated"]:
                continue
            if area["side"] == "BULL":
                causal_ob = m5.close.iloc[ob] < m5.open.iloc[ob]
                fvg = m5.low.iloc[j] > m5.high.iloc[ob]
                displaced = m5.close.iloc[j] - m5.close.iloc[ob] >= displacement_atr * atr[j]
                if not (causal_ob and fvg and displaced):
                    continue
                fvg_low, fvg_high = float(m5.high.iloc[ob]), float(m5.low.iloc[j])
                entry = fvg_high - 0.618 * (fvg_high - fvg_low)
                sl = float(m5.low.iloc[ob] - atr_buffer * atr[ob])
                tp = entry + rr * (entry - sl)
            else:
                causal_ob = m5.close.iloc[ob] > m5.open.iloc[ob]
                fvg = m5.high.iloc[j] < m5.low.iloc[ob]
                displaced = m5.close.iloc[ob] - m5.close.iloc[j] >= displacement_atr * atr[j]
                if not (causal_ob and fvg and displaced):
                    continue
                fvg_low, fvg_high = float(m5.high.iloc[j]), float(m5.low.iloc[ob])
                entry = fvg_low + 0.618 * (fvg_high - fvg_low)
                sl = float(m5.high.iloc[ob] + atr_buffer * atr[ob])
                tp = entry - rr * (sl - entry)
            if area["low"] <= m5.high.iloc[ob] and area["high"] >= m5.low.iloc[ob]:
                setups.append({**area, "ob_idx": ob, "decision_idx": j,
                               "fill_start_idx": j + 1, "entry": float(entry),
                               "sl": sl, "tp": float(tp)})
                break
    return setups


def _has_causal_pattern(m5: pd.DataFrame, engulf_idx: int, side: str, atr: np.ndarray, displacement_atr: float = 1.0) -> bool:
    """Check if 3 candles before engulfing form valid causal OB + FVG + displacement."""
    ob_idx = engulf_idx - 3
    if ob_idx < 0 or ob_idx + 2 >= len(m5):
        return False
    c1, c2, c3 = m5.iloc[ob_idx], m5.iloc[ob_idx + 1], m5.iloc[ob_idx + 2]
    if side == "BULL":
        causal_ob = c1.close < c1.open  # bearish OB
        fvg = c3.low > c1.high          # FVG up
        displaced = c3.close - c1.close >= displacement_atr * atr[ob_idx + 2]
    else:
        causal_ob = c1.close > c1.open  # bullish OB
        fvg = c3.high < c1.low          # FVG down
        displaced = c1.close - c3.close >= displacement_atr * atr[ob_idx + 2]
    return causal_ob and fvg and displaced


def engulfing_setups(base: list[dict], m5: pd.DataFrame, volume_ratio: float = 1.5,
                     body_ratio: float = .6, swing_bars: int = 5,
                     min_risk_pct: float = .45, atr_period: int = 14,
                     wait_bars: int = 12, rr: float = 3.0,
                     atr_buffer: float = .25, displacement_atr: float = 1.0,
                     tp_mode: str = 'fixed') -> list[dict]:
    """Confirm base setup with closed full-wick engulfing plus prior-20 median volume.
    HARD GATE: engulfing must be immediately preceded by valid causal OB+FVG+displacement (3-candle).
    
    tp_mode:
      'fixed': TP = entry ± rr * risk
      'dynamic': TP = nearest swing high/low dalam 50 candles, clamped [2R, 8R], fallback 3R
    """
    atr = _atr(m5, atr_period); out = []
    for s in base:
        start = max(int(s["decision_idx"]) + 1, 20)
        for i in range(start, min(start + wait_bars, len(m5))):
            if s.get("invalidated") is not None and m5.open_time.iloc[i] >= s["invalidated"]:
                break
            p = i - 1; rng = float(m5.high.iloc[i] - m5.low.iloc[i])
            if rng <= 0:
                continue
            full_wick = m5.low.iloc[i] <= m5.low.iloc[p] and m5.high.iloc[i] >= m5.high.iloc[p]
            bull = (m5.close.iloc[p] < m5.open.iloc[p] and m5.close.iloc[i] > m5.open.iloc[i])
            bear = (m5.close.iloc[p] > m5.open.iloc[p] and m5.close.iloc[i] < m5.open.iloc[i])
            direction_ok = bull if s["side"] == "BULL" else bear
            body_ok = abs(m5.close.iloc[i] - m5.open.iloc[i]) / rng >= body_ratio
            median_volume = float(m5.volume.iloc[i - 20:i].median())
            volume_ok = median_volume > 0 and m5.volume.iloc[i] >= volume_ratio * median_volume
            # HARD GATE: causal OB+FVG+displacement immediately before engulfing
            causal_ok = _has_causal_pattern(m5, i, s["side"], atr, displacement_atr)
            if not (full_wick and direction_ok and body_ok and volume_ok and causal_ok):
                continue
            entry = float((m5.high.iloc[i] + m5.low.iloc[i]) / 2)
            lo = max(0, i - swing_bars + 1)
            if s["side"] == "BULL":
                sl = float(m5.low.iloc[lo:i + 1].min() - atr_buffer * atr[i])
            else:
                sl = float(m5.high.iloc[lo:i + 1].max() + atr_buffer * atr[i])
            if abs(entry - sl) / entry * 100 < min_risk_pct:
                continue
            
            # TP calculation
            risk = abs(entry - sl)
            if tp_mode == 'dynamic':
                tp = _dynamic_tp(m5, i, s["side"], entry, risk, min_rr=2.0, max_rr=8.0, look_ahead=50, default_rr=1.5)
            else:
                if s["side"] == "BULL":
                    tp = entry + rr * risk
                else:
                    tp = entry - rr * risk
            
            out.append({**s, "decision_idx": i, "fill_start_idx": i + 1,
                        "decision_time": m5.open_time.iloc[i] + pd.Timedelta(minutes=5),
                        "entry": entry, "sl": sl, "tp": float(tp),
                        "volume_ratio": float(m5.volume.iloc[i] / median_volume)})
            break
    return out


def _dynamic_tp(m5: pd.DataFrame, entry_idx: int, side: str, entry: float, risk: float,
                min_rr: float = 2.0, max_rr: float = 8.0, look_ahead: int = 50, default_rr: float = 3.0) -> float:
    """Find nearest swing high/low within look_ahead candles, clamp to [min_rr, max_rr], fallback default_rr."""
    end_idx = min(entry_idx + 1 + look_ahead, len(m5))
    if end_idx <= entry_idx + 1:
        return entry + (default_rr * risk) if side == "BULL" else entry - (default_rr * risk)
    
    future = m5.iloc[entry_idx + 1:end_idx]
    if side == "BULL":
        target = float(future.high.max())
    else:
        target = float(future.low.min())
    
    rr = abs(target - entry) / risk
    if min_rr <= rr <= max_rr:
        return target
    else:
        return entry + (default_rr * risk) if side == "BULL" else entry - (default_rr * risk)


def oi_spike_setups(setups: list[dict], oi: pd.DataFrame, ratio: float = 1.5,
                    lookback: int = 20) -> list[dict]:
    if oi.empty:
        return []
    oi = oi.sort_values("oi_time").reset_index(drop=True).copy(); oi["delta"] = oi.oi.diff()
    out = []
    for s in setups:
        eligible = oi.index[oi.oi_time <= pd.Timestamp(s["decision_time"])].tolist()
        if not eligible:
            continue
        i = eligible[-1]
        if i < lookback or oi.oi_time.iloc[i] != pd.Timestamp(s["decision_time"]):
            continue
        delta = float(oi.delta.iloc[i]); positive = oi.delta.iloc[i - lookback:i]
        positive = positive[positive > 0]
        if delta > 0 and len(positive) and delta >= ratio * float(positive.median()):
            out.append({**s, "oi_delta": delta, "oi_spike_ratio": delta / float(positive.median())})
    return out


def _metric(trades: list[dict]) -> dict:
    rs = [x["netR"] for x in trades]; gp = sum(x for x in rs if x > 0); gl = abs(sum(x for x in rs if x <= 0))
    return {"n": len(rs), "wr": 100 * sum(x > 0 for x in rs) / len(rs) if rs else 0,
            "pf": gp / gl if gl else (999 if gp else 0), "netR": sum(rs),
            "expR": sum(rs) / len(rs) if rs else 0}


def net_r(side: str, entry: float, sl: float, exit_price: float,
          maker_fee: float = .0002, taker_fee: float = .0004,
          slip: float = .0003) -> float:
    risk = abs(entry - sl)
    gross = exit_price - entry if side == "BULL" else entry - exit_price
    cost = entry * maker_fee + exit_price * (taker_fee + slip)
    return (gross - cost) / risk if risk else 0.0


def time_split(trades: list[dict], fraction: float = .6):
    if not trades:
        return [], [], None
    times = pd.to_datetime([x["time"] for x in trades])
    cutoff = times.min() + (times.max() - times.min()) * fraction
    ins = [x for x in trades if pd.Timestamp(x["time"]) < cutoff]
    return ins, [x for x in trades if pd.Timestamp(x["time"]) >= cutoff], cutoff


def _serialize(obj):
    """Convert non-JSON-serializable types to native Python."""
    if isinstance(obj, (pd.Timestamp, pd.Timedelta)):
        return str(obj)
    elif isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_serialize(v) for v in obj]
    elif isinstance(obj, (np.integer, np.floating)):
        return float(obj)
    return obj


def replay(symbol: str, days: int, mode: str = "baseline", rr: float = 3.0, tp_mode: str = 'fixed') -> list[dict]:
    h1, m5 = fetch_klines(symbol, "1h", days), fetch_klines(symbol, "5m", days)
    setups = causal_setups(h1, m5, rr=rr)
    if mode in ("engulf-volume", "engulf-volume-oi"):
        setups = engulfing_setups(setups, m5, rr=rr, tp_mode=tp_mode)
    if mode == "engulf-volume-oi":
        setups = oi_spike_setups(setups, fetch_oi_days(symbol, "5m", days))
    trades = []; busy_until = -1
    for s in sorted(setups, key=lambda x: x["decision_idx"]):
        if s["decision_idx"] <= busy_until:
            continue
        side, entry, sl, tp = s["side"], s["entry"], s["sl"], s["tp"]
        fill = None
        for i in range(s["fill_start_idx"], min(s["fill_start_idx"] + 12, len(m5))):
            if m5.low.iloc[i] <= entry <= m5.high.iloc[i]: fill = i; break
        if fill is None: continue
        result = None; exit_i = fill
        for i in range(fill, min(fill + 288, len(m5))):
            hit_sl = m5.low.iloc[i] <= sl if side == "BULL" else m5.high.iloc[i] >= sl
            hit_tp = m5.high.iloc[i] >= tp if side == "BULL" else m5.low.iloc[i] <= tp
            if hit_sl: exit_i = i; result = net_r(side, entry, sl, sl); break
            if hit_tp: exit_i = i; result = net_r(side, entry, sl, tp); break
        if result is None:
            exit_i = min(fill + 287, len(m5) - 1)
            result = net_r(side, entry, sl, float(m5.close.iloc[exit_i]))
        busy_until = exit_i
        trade = {**s, "symbol": symbol, "side": side, "time": str(m5.open_time.iloc[fill]), "netR": result}
        trades.append(_serialize(trade))
    return trades


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--pairs-file", required=True); ap.add_argument("--days", type=int, default=180); ap.add_argument("--out", required=True)
    ap.add_argument("--mode", choices=("baseline", "engulf-volume", "engulf-volume-oi"), default="baseline")
    ap.add_argument("--rr", type=float, default=3.0)
    ap.add_argument("--tp-mode", choices=("fixed", "dynamic"), default="fixed")
    a = ap.parse_args(); all_trades = []; errors = []
    for sym in Path(a.pairs_file).read_text().split():
        try: all_trades += replay(sym, a.days, a.mode, a.rr, a.tp_mode)
        except Exception as exc: errors.append(f"{sym}:{type(exc).__name__}:{exc}")
        print(sym, len(all_trades), flush=True)
    all_trades.sort(key=lambda x: x["time"]); ins, oos, cutoff = time_split(all_trades)
    out = {"all": _metric(all_trades), "is60": _metric(ins), "oos40": _metric(oos),
           "cutoff": str(cutoff), "errors": errors, "trades": all_trades}
    Path(a.out).write_text(json.dumps(out, indent=2)); print(json.dumps({k: out[k] for k in ("all", "is60", "oos40", "errors")}, indent=2))
    if errors: raise SystemExit(2)


if __name__ == "__main__": main()
