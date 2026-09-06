"""Audit latest common 24h funnel; no strategy changes."""
import argparse, json
from pathlib import Path
import pandas as pd
from backtest.data import fetch_klines, fetch_oi_days
from fusionnew.backtest.h1_fvg_m5_ob_replay import causal_setups, engulfing_setups, oi_spike_setups, replay


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs-file", required=True)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--oi", action="store_true")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(); rows = []; errors = []
    for symbol in Path(a.pairs_file).read_text().split():
        try:
            h1 = fetch_klines(symbol, "1h", a.days); m5 = fetch_klines(symbol, "5m", a.days)
            if h1.empty or m5.empty:
                raise ValueError("empty candles")
            end = pd.Timestamp(m5.open_time.max()) + pd.Timedelta(minutes=5)
            start = end - pd.Timedelta(days=1)
            raw = causal_setups(h1, m5, rr=3)
            spike = engulfing_setups(raw, m5, rr=3)
            in_window = lambda s: start <= pd.Timestamp(s["decision_time"]) < end
            recent_spikes = [s for s in spike if in_window(s)]
            oi_pass = oi_spike_setups(recent_spikes, fetch_oi_days(symbol, "5m", a.days)) if a.oi and recent_spikes else []
            trades = replay(symbol, a.days, "engulf-volume", 3)
            fills = [t for t in trades if start <= pd.Timestamp(t["time"]) < end]
            rows.append({"symbol": symbol, "start": str(start), "end": str(end),
                         "spikes": sum(map(in_window, spike)),
                         "oi_pass": sum(map(in_window, oi_pass)), "fills": len(fills)})
        except Exception as exc:
            errors.append(f"{symbol}:{type(exc).__name__}:{exc}")
        print(symbol, len(rows), flush=True)
    totals = {k: sum(x[k] for x in rows) for k in ("spikes", "oi_pass", "fills")}
    out = {"pairs_requested": len(Path(a.pairs_file).read_text().split()),
           "pairs_ok": len(rows), "totals": totals, "errors": errors, "pairs": rows}
    Path(a.out).write_text(json.dumps(out, indent=2)); print(json.dumps({k: out[k] for k in ("pairs_requested", "pairs_ok", "totals", "errors")}, indent=2))
    if errors: raise SystemExit(2)


if __name__ == "__main__": main()
