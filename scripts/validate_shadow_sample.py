#!/usr/bin/env python3
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "runtime/state"


def stamp(value):
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def main():
    errors = []
    manifest = json.loads((STATE / "shadow_clean_epoch_manifest.json").read_text())
    epoch = stamp(manifest["epoch"])
    history = json.loads((ROOT / "journal/trade_history.json").read_text())
    canonical_by_symbol = {}
    for row in history:
        sym = row.get("symbol")
        ts = row.get("timestamp_close")
        if sym and ts:
            canonical_by_symbol.setdefault(sym, []).append(stamp(ts))

    closes = []
    for line in (ROOT / "journal/shadow_trailing.jsonl").read_text().splitlines():
        row = json.loads(line)
        if row.get("timestamp_close") and stamp(row["timestamp_close"]) >= epoch:
            closes.append(row)

    for row in closes:
        sym = row.get("symbol")
        shadow_ts = stamp(row["timestamp_close"])
        canonical_times = canonical_by_symbol.get(sym, [])
        matched = any(abs((shadow_ts - ct).total_seconds()) <= 2.0 for ct in canonical_times)
        if not matched:
            errors.append(f"missing canonical close: {sym} {row['timestamp_close']}")
            continue
        if row.get("tp1_hit") is True and not row.get("joined_outcomes"):
            errors.append(f"missing joined shadow outcomes: {sym} {row['timestamp_close']}")
        if not row.get("bad_signal_outcome"):
            errors.append(f"missing bad-signal outcome: {sym} {row['timestamp_close']}")

    if errors:
        print("INVALID SHADOW SAMPLE")
        print("\n".join(errors))
        return 1
    print(f"VALID SHADOW SAMPLE epoch={manifest['epoch']} closes={len(closes)} canonical={len(history)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
