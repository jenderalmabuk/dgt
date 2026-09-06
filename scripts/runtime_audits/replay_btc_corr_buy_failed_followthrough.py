#!/usr/bin/env python3
"""Causal BUY replay: current smart exit vs failed-follow-through exit."""
import argparse
import csv
import io
import json
import subprocess
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_JOURNAL = ROOT / "runtime/fusionnew/forward_trades_M30_both_fusionnew_m30_btc_corr.jsonl"


def dt(value):
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def sql_ts(value):
    return value.astimezone(timezone.utc).isoformat()


def load_trades(path):
    fills = defaultdict(deque)
    closes = []
    for line in path.open():
        row = json.loads(line)
        if row.get("side") != "BUY":
            continue
        key = (row.get("symbol"), str(row.get("t_complete")), float(row.get("entry", 0)))
        if row.get("event") == "FILL":
            fills[key].append(row)
        elif row.get("event") == "CLOSE":
            closes.append(row)
    joined = []
    for close in closes:
        key = (close["symbol"], str(close["t_complete"]), float(close["entry"]))
        candidates = fills[key]
        eligible = [x for x in candidates if dt(x["ts"]) <= dt(close["ts"])]
        if not eligible:
            raise RuntimeError(f"FILL tidak ditemukan: {key} CLOSE={close['ts']}")
        fill = eligible[-1]
        candidates.remove(fill)
        joined.append((fill, close))
    return joined


def fetch_candles(symbol, start, end):
    query = f"""SELECT extract(epoch from open_time),extract(epoch from close_time),high,low,close
FROM klines WHERE exchange='binance' AND symbol='{symbol}' AND timeframe='5m'
AND open_time >= '{sql_ts(start)}' AND close_time <= '{sql_ts(end)}'
ORDER BY open_time"""
    proc = subprocess.run(
        ["docker", "exec", "-i", "nexus_timescaledb", "psql", "-U", "nexus", "-d", "nexus", "-At", "-F", "\t", "-c", query],
        text=True, capture_output=True, check=True,
    )
    return [[float(v) for v in row] for row in csv.reader(io.StringIO(proc.stdout), delimiter="\t")]


def ema(values, period=20):
    alpha = 2 / (period + 1)
    out = []
    current = None
    for value in values:
        current = value if current is None else alpha * value + (1 - alpha) * current
        out.append(current)
    return out


def simulate(fill, close, enable_failed_followthrough):
    entry, sl, tp = map(float, (close["entry"], close["sl"], close["tp"]))
    risk = entry - sl
    if risk <= 0:
        raise RuntimeError(f"risk BUY invalid: {close['symbol']} entry={entry} sl={sl}")
    fill_bar = dt(fill.get("fill_bar_ts", fill["ts"]))
    end = dt(close["ts"])
    bars = fetch_candles(close["symbol"], fill_bar - timedelta(hours=4), end)
    lifecycle = [i for i, bar in enumerate(bars) if bar[0] >= fill_bar.timestamp()]
    if not lifecycle:
        raise RuntimeError(f"candle lifecycle kosong: {close['symbol']} {fill_bar}..{end}")
    emas = ema([bar[4] for bar in bars])
    floor = None
    peak_closed_r = float("-inf")
    for post_index, i in enumerate(lifecycle, 1):
        _, close_epoch, high, low, price = bars[i]
        # Conservative intrabar ordering, identical in both arms.
        if low <= sl:
            return result(close, fill, "SL", -1.0, close_epoch, post_index, peak_closed_r)
        if high >= tp:
            return result(close, fill, "TP", (tp - entry) / risk, close_epoch, post_index, peak_closed_r)
        # Current smart-exit checks previously armed floor before updating this bar's peak.
        if floor is not None and price <= entry + floor * risk:
            return result(close, fill, "SMART_EXIT", floor, close_epoch, post_index, peak_closed_r)
        current_r = (price - entry) / risk
        peak_closed_r = max(peak_closed_r, current_r)
        if peak_closed_r >= 2:
            floor = 1.0
        elif peak_closed_r >= 1:
            floor = 0.25
        slope_negative = i > 0 and emas[i] < emas[i - 1]
        return3_negative = i >= 3 and price < bars[i - 3][4]
        if (enable_failed_followthrough and post_index >= 6 and peak_closed_r < 0.5
                and price < entry and price < emas[i]
                and (slope_negative or return3_negative)):
            return result(close, fill, "FAILED_FOLLOW_THROUGH", current_r, close_epoch,
                          post_index, peak_closed_r, emas[i], slope_negative, return3_negative)
    # Historical CLOSE boundary: preserve journal close when candle replay has no earlier exit.
    exit_price = float(close["exit"])
    return result(close, fill, "JOURNAL_" + str(close.get("reason", "CLOSE")),
                  (exit_price - entry) / risk, dt(close["ts"]).timestamp(), len(lifecycle), peak_closed_r)


def result(close, fill, reason, r_value, epoch, bars, peak, ema20=None, slope=None, ret3=None):
    out = {
        "symbol": close["symbol"], "t_complete": close["t_complete"],
        "fill_ts": fill["ts"], "fill_bar_ts": fill.get("fill_bar_ts"),
        "historical_close_ts": close["ts"], "exit_ts": datetime.fromtimestamp(epoch, timezone.utc).isoformat(),
        "reason": reason, "r": r_value, "bars_post_fill": bars, "peak_closed_r": peak,
    }
    if ema20 is not None:
        out.update(ema20=ema20, ema20_slope_negative=slope, return_3c_negative=ret3)
    return out


def metrics(rows):
    rs = [x["r"] for x in rows]
    gross_win = sum(x for x in rs if x > 0)
    gross_loss = -sum(x for x in rs if x < 0)
    return {
        "n": len(rs), "net_r": sum(rs), "gross_win_r": gross_win,
        "gross_loss_r": gross_loss, "pf_proxy": gross_win / gross_loss if gross_loss else None,
        "wins": sum(x > 0 for x in rs), "losses": sum(x < 0 for x in rs), "flat": sum(x == 0 for x in rs),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--journal", type=Path, default=DEFAULT_JOURNAL)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    trades = load_trades(args.journal)
    control = [simulate(fill, close, False) for fill, close in trades]
    proposed = [simulate(fill, close, True) for fill, close in trades]
    pairs = []
    for base, test in zip(control, proposed):
        pairs.append({"symbol": base["symbol"], "t_complete": base["t_complete"],
                      "control_reason": base["reason"], "control_r": base["r"],
                      "proposed_reason": test["reason"], "proposed_r": test["r"],
                      "delta_r": test["r"] - base["r"],
                      "winner_cut": base["r"] > 0 and test["r"] < base["r"],
                      "loss_saved": base["r"] < 0 and test["r"] > base["r"],
                      "proposed_detail": test})
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "journal": str(args.journal),
        "data": "TimescaleDB nexus_timescaledb, klines exchange=binance timeframe=5m, close_time<=decision time",
        "semantics": "actual FILL/fill_bar_ts start; closed M5 only; each bar SL-first, TP-second, current smart-exit third, proposed exit fourth at close; EMA20 causal recursive with 4h prefill",
        "control": metrics(control), "proposed": metrics(proposed),
        "comparison": {
            "delta_net_r": sum(x["delta_r"] for x in pairs),
            "winners_cut": sum(x["winner_cut"] for x in pairs),
            "winners_cut_r": sum(-x["delta_r"] for x in pairs if x["winner_cut"]),
            "losses_saved": sum(x["loss_saved"] for x in pairs),
            "losses_saved_r": sum(x["delta_r"] for x in pairs if x["loss_saved"]),
            "failed_followthrough_exits": sum(x["proposed_reason"] == "FAILED_FOLLOW_THROUGH" for x in pairs),
        },
        "trades": pairs,
    }
    text = json.dumps(report, indent=2, allow_nan=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
