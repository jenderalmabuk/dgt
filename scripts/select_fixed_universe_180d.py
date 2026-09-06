#!/usr/bin/env python3
"""Select fixed universe by 180d Binance Futures data quality; no outcome/PnL leakage."""
import csv
import json
import math
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "bots/universe.txt"
OUT = ROOT / "runtime/imbalance"
DAYS = 180
LIMIT = 181
MIN_CLOSED = 180
MIN_COVERAGE = 0.99
MAX_GAP_DAYS = 2.0
MIN_MEDIAN_TURNOVER = 5_000_000.0


def fetch(symbol):
    q = urllib.parse.urlencode({"symbol": symbol, "interval": "1d", "limit": LIMIT})
    req = urllib.request.Request("https://fapi.binance.com/fapi/v1/klines?" + q, headers={"User-Agent": "nexus-history-audit/1"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        except Exception:
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)


def median(xs):
    xs = sorted(xs)
    n = len(xs)
    return (xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2) if n else 0.0


def audit(symbol, now_ms):
    rows = fetch(symbol)
    closed = [r for r in rows if int(r[6]) < now_ms][-MIN_CLOSED:]
    opens = [int(r[0]) for r in closed]
    gaps = [(b - a) / 86_400_000 for a, b in zip(opens, opens[1:])]
    turnover = [float(r[7]) for r in closed]
    n = len(closed)
    coverage = n / MIN_CLOSED
    latest_age = (now_ms - int(closed[-1][6])) / 86_400_000 if closed else math.inf
    result = {
        "symbol": symbol,
        "closed_days": n,
        "coverage": coverage,
        "max_gap_days": max(gaps, default=math.inf),
        "zero_volume_days": sum(float(r[5]) <= 0 for r in closed),
        "median_turnover_usdt": median(turnover),
        "latest_age_days": latest_age,
        "first_open": datetime.fromtimestamp(opens[0] / 1000, timezone.utc).date().isoformat() if opens else "",
        "last_open": datetime.fromtimestamp(opens[-1] / 1000, timezone.utc).date().isoformat() if opens else "",
    }
    result["selected"] = (
        n >= MIN_CLOSED
        and coverage >= MIN_COVERAGE
        and result["max_gap_days"] <= MAX_GAP_DAYS
        and result["zero_volume_days"] == 0
        and result["median_turnover_usdt"] >= MIN_MEDIAN_TURNOVER
        and latest_age <= 2.0
    )
    return result


def main():
    symbols = sorted({x.strip() for x in SOURCE.read_text().splitlines() if x.strip()})
    now_ms = int(time.time() * 1000)
    results = []
    for i, symbol in enumerate(symbols, 1):
        try:
            results.append(audit(symbol, now_ms))
        except Exception as e:
            results.append({"symbol": symbol, "selected": False, "error": f"{type(e).__name__}: {e}"})
        if i % 50 == 0:
            print(f"progress {i}/{len(symbols)} selected={sum(r['selected'] for r in results)}", flush=True)
        time.sleep(0.04)
    selected = sorted(r["symbol"] for r in results if r["selected"])
    OUT.mkdir(parents=True, exist_ok=True)
    fields = ["symbol", "selected", "closed_days", "coverage", "max_gap_days", "zero_volume_days", "median_turnover_usdt", "latest_age_days", "first_open", "last_open", "error"]
    with (OUT / "history_quality_180d.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fields, extrasaction="ignore"); w.writeheader(); w.writerows(results)
    (OUT / "fixed_universe_180d.txt").write_text("\n".join(selected) + "\n")
    summary = {"generated_at": datetime.now(timezone.utc).isoformat(), "source_count": len(symbols), "selected_count": len(selected), "rules": {"closed_days": MIN_CLOSED, "coverage": MIN_COVERAGE, "max_gap_days": MAX_GAP_DAYS, "zero_volume_days": 0, "median_turnover_usdt": MIN_MEDIAN_TURNOVER, "latest_age_days": 2.0}, "selected": selected}
    (OUT / "fixed_universe_180d.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: summary[k] for k in ("source_count", "selected_count", "rules")}, indent=2))


if __name__ == "__main__":
    main()
