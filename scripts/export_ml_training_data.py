#!/usr/bin/env python3
"""
Export enriched trade history for ML training.

Extracts all features used in signal validation and execution:
- Entry conditions (RSI, price movement, entry zone)
- Market structure (OI, funding, CVD, regime)
- Signal metadata (scores, source channel, leverage)
- Outcomes (PnL, exit reason, duration)

Output: CSV with one row per completed trade.
"""

import json
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List


def extract_features(trade: Dict[str, Any]) -> Dict[str, Any]:
    """Extract ML training features from a completed trade."""
    snap = trade.get("adv_snapshot") or {}
    bad_signal = trade.get("bad_signal_shadow") or {}
    
    # Parse timestamps
    ts_open = trade.get("timestamp_open", "")
    ts_close = trade.get("timestamp_close", "")
    
    try:
        dt_open = datetime.fromisoformat(ts_open.replace("Z", "+00:00"))
        dt_close = datetime.fromisoformat(ts_close.replace("Z", "+00:00"))
        duration_hours = (dt_close - dt_open).total_seconds() / 3600.0
    except Exception:
        duration_hours = None
    
    # Core trade data
    features = {
        # Identity
        "symbol": trade.get("symbol"),
        "side": trade.get("side"),
        "source_chat_id": snap.get("source_chat_id"),
        "timestamp_open": ts_open,
        "timestamp_close": ts_close,
        "duration_hours": duration_hours,
        
        # Entry
        "entry_price": trade.get("entry_price"),
        "sl_price": trade.get("sl_price"),
        "tp1": snap.get("tp1"),
        "tp2": snap.get("tp2"),
        "tp3": snap.get("tp3"),
        "leverage": snap.get("leverage"),
        
        # Entry zone
        "entry_low": snap.get("entry_low") or snap.get("signal_entry_low"),
        "entry_high": snap.get("entry_high") or snap.get("signal_entry_high"),
        
        # Technical indicators
        "rsi": snap.get("rsi"),
        "price_change_15m_pct": snap.get("price_change_15m_pct"),
        "atr_pct": snap.get("atr_pct"),
        
        # Market structure
        "cvd_zscore": snap.get("cvd_zscore"),
        "funding_rate_pct": snap.get("funding_rate_pct"),
        "oi_change_5m_pct": snap.get("oi_change_5m_pct"),
        "oi_change_15m_pct": snap.get("oi_change_15m_pct"),
        "oi_change_1h_pct": snap.get("oi_change_1h_pct"),
        "oi_source": snap.get("oi_source"),
        
        # Regime
        "regime": snap.get("regime"),
        "btc_regime": snap.get("btc_regime"),
        "flow_direction": snap.get("flow_direction"),
        
        # Signal scores
        "mtf_score": snap.get("mtf_score"),
        "tv_score": snap.get("tv_score"),
        "total_score": snap.get("total_score"),
        
        # Bad signal classification
        "bad_signal_categories": ",".join(bad_signal.get("categories", [])) or None,
        "reversal_risk": bad_signal.get("reversal_risk", False),
        
        # Outcomes
        "pnl_usd": trade.get("pnl_usd"),
        "pnl_pct": trade.get("pnl_pct"),
        "reason": trade.get("reason"),
        "tp1_hit": trade.get("tp1_hit"),
        "tp2_hit": trade.get("tp2_hit"),
        "tp3_hit": trade.get("tp3_hit"),
        
        # Shadow variants (best delta)
        "shadow_best_delta": None,  # TODO: compute from shadow_variants if available
    }
    
    return features


def export_csv(trades: List[Dict[str, Any]], output_path: Path) -> None:
    """Write trades to CSV."""
    import csv
    
    if not trades:
        print("No trades to export", file=sys.stderr)
        return
    
    # Get all possible keys from first trade
    sample = extract_features(trades[0])
    fieldnames = list(sample.keys())
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        
        for trade in trades:
            features = extract_features(trade)
            writer.writerow(features)
    
    print(f"Exported {len(trades)} trades to {output_path}")


def export_json(trades: List[Dict[str, Any]], output_path: Path) -> None:
    """Write trades to JSON Lines format."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, "w", encoding="utf-8") as f:
        for trade in trades:
            features = extract_features(trade)
            f.write(json.dumps(features) + "\n")
    
    print(f"Exported {len(trades)} trades to {output_path}")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Export trade history for ML training")
    parser.add_argument("--input", default="journal/trade_history.json", help="Input trade history JSON")
    parser.add_argument("--output", default="data/ml_training_history.csv", help="Output file")
    parser.add_argument("--format", choices=["csv", "jsonl"], default="csv", help="Output format")
    parser.add_argument("--min-trades", type=int, default=0, help="Minimum trades to export")
    
    args = parser.parse_args()
    
    # Load trade history
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: {input_path} not found", file=sys.stderr)
        sys.exit(1)
    
    trades = json.loads(input_path.read_text())
    print(f"Loaded {len(trades)} trades from {input_path}")
    
    if len(trades) < args.min_trades:
        print(f"Warning: Only {len(trades)} trades available (minimum: {args.min_trades})", file=sys.stderr)
    
    # Export
    output_path = Path(args.output)
    
    if args.format == "csv":
        export_csv(trades, output_path)
    else:
        export_json(trades, output_path)
    
    print("\nFeature summary:")
    print(f"  Symbols: {len(set(t.get('symbol') for t in trades))}")
    print(f"  Channels: {len(set((t.get('adv_snapshot') or {}).get('source_chat_id') for t in trades))}")
    print(f"  Win rate: {len([t for t in trades if float(t.get('pnl_usd', 0)) > 0]) / len(trades) * 100:.1f}%")
    print(f"  Date range: {min(t.get('timestamp_open', '') for t in trades)[:10]} to {max(t.get('timestamp_close', '') for t in trades)[:10]}")


if __name__ == "__main__":
    main()
