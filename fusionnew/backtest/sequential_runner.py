#!/usr/bin/env python3
"""Sequential backtest runner with aggressive rate limit protection."""
import sys
import time
import subprocess
from pathlib import Path

pairs_file = Path("runtime/imbalance/fixed_universe_180d_turn20m.txt")
pairs = pairs_file.read_text().strip().split()

mode = sys.argv[1] if len(sys.argv) > 1 else "fixed"  # fixed or dynamic
out = f"runtime/imbalance/backtests/h1_fvg_causal_tp_{mode}_30d_sequential.json"

print(f"Sequential backtest: {len(pairs)} pairs, tp_mode={mode}")
print(f"Output: {out}")
print(f"ETA: ~{len(pairs) * 3 / 60:.1f} minutes (3s per pair)")

for i, sym in enumerate(pairs, 1):
    print(f"\n[{i}/{len(pairs)}] {sym}", flush=True)
    cmd = [
        "docker", "run", "--rm", "-u", "0",
        "-v", f"{Path.cwd()}:/app", "-w", "/app",
        "nexus-bot:latest",
        "python", "fusionnew/backtest/h1_fvg_m5_ob_replay.py",
        "--pairs-file", f"/tmp/{sym}.txt",
        "--days", "30",
        "--mode", "engulf-volume",
        "--out", f"/tmp/{sym}_{mode}.json",
        "--tp-mode", mode
    ]
    
    # Write single-pair file
    (Path("/tmp") / f"{sym}.txt").write_text(sym)
    
    try:
        subprocess.run(cmd, check=True, timeout=180, capture_output=True, text=True)
        print(f"  ✓ {sym}")
    except subprocess.CalledProcessError as e:
        print(f"  ✗ {sym}: {e.stderr[-200:]}")
    except subprocess.TimeoutExpired:
        print(f"  ✗ {sym}: timeout")
    
    # Aggressive rate limit protection
    if i < len(pairs):
        time.sleep(3)

print(f"\n✓ Sequential backtest complete: {out}")
