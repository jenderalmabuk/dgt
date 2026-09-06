#!/bin/bash
# SignalCopy Weekly Performance Report
# Track: max age closes, bad signal blocks, shadow exit progress

cd /home/fusion_omega/fusion_omega_nexus

python3 - <<'PY'
import json
from datetime import datetime, timezone, timedelta

now = datetime.now(timezone.utc)
week_ago = now - timedelta(days=7)

hist = json.load(open('journal/trade_history.json'))

# Filter last week
week_trades = [t for t in hist 
               if datetime.fromisoformat(t['timestamp_close'].replace('Z', '+00:00')) >= week_ago]

if not week_trades:
    print("No trades this week")
    exit(0)

print("📊 SIGNALCOPY WEEKLY REPORT")
print(f"Period: {week_ago.strftime('%Y-%m-%d')} to {now.strftime('%Y-%m-%d')}")
print()

# Overall stats
total = len(week_trades)
total_pnl = sum(t['pnl_usd'] for t in week_trades)
winners = sum(1 for t in week_trades if t['pnl_usd'] > 0)
wr = winners / total * 100 if total > 0 else 0

print(f"Total closes: {total}")
print(f"Net PnL: ${total_pnl:+.2f}")
print(f"Win rate: {wr:.1f}%")
print()

# Max age closes
max_age = [t for t in week_trades if 'MAX_AGE' in t.get('reason', '')]
if max_age:
    max_age_pnl = sum(t['pnl_usd'] for t in max_age)
    pre = sum(1 for t in max_age if 'PRE_TP1' in t['reason'])
    post = sum(1 for t in max_age if 'POST_TP1' in t['reason'])
    
    print(f"🔒 MAX AGE CLOSES: {len(max_age)}")
    print(f"   Pre-TP1: {pre}")
    print(f"   Post-TP1: {post}")
    print(f"   Total PnL: ${max_age_pnl:+.2f}")
    print()

# Shadow exit progress
shadow_trades = [t for t in week_trades 
                 if t.get('joined_shadow_outcomes')]

if shadow_trades:
    print(f"📈 SHADOW EXIT TRACKING: {len(shadow_trades)} post-TP1 closes")
    
    baseline_total = sum(t['pnl_usd'] for t in shadow_trades)
    
    # Calculate best shadow performance
    best_shadow_total = 0
    for t in shadow_trades:
        joined = t.get('joined_shadow_outcomes', {})
        if joined:
            best = max(v.get('net_pnl', baseline_total) 
                      for v in joined.values() 
                      if v.get('net_pnl') is not None)
            best_shadow_total += best
    
    improvement = best_shadow_total - baseline_total
    pct = improvement / abs(baseline_total) * 100 if baseline_total != 0 else 0
    
    print(f"   Baseline: ${baseline_total:+.2f}")
    print(f"   Best shadow: ${best_shadow_total:+.2f}")
    print(f"   Delta: ${improvement:+.2f} ({pct:+.1f}%)")
    print()
    
    # Check if ready for promotion
    total_shadow_sample = sum(1 for t in hist if t.get('joined_shadow_outcomes'))
    print(f"   Total shadow sample: {total_shadow_sample}")
    
    if total_shadow_sample >= 20:
        print(f"   ✅ READY for promotion decision (≥20 samples)")
    else:
        needed = 20 - total_shadow_sample
        print(f"   ⏳ Need {needed} more samples for promotion")
    print()

# Bad signal stats (for future when blocking is active)
bad_signal_trades = [t for t in week_trades 
                     if t.get('bad_signal_shadow', {}).get('categories')]

if bad_signal_trades:
    print(f"⚠️ BAD SIGNAL TRADES: {len(bad_signal_trades)}")
    
    categories = {}
    for t in bad_signal_trades:
        for cat in t.get('bad_signal_shadow', {}).get('categories', []):
            if cat not in categories:
                categories[cat] = {'count': 0, 'pnl': 0}
            categories[cat]['count'] += 1
            categories[cat]['pnl'] += t['pnl_usd']
    
    for cat, data in sorted(categories.items(), key=lambda x: x[1]['pnl']):
        print(f"   {cat}: {data['count']} trades, ${data['pnl']:+.2f}")
    print()

# Position health
pos = json.load(open('journal/open_positions.json'))
active = len(pos)
pre_tp1 = sum(1 for p in pos.values() if not p.get('tp1_hit'))
post_tp1 = sum(1 for p in pos.values() if p.get('tp1_hit'))

print(f"📦 CURRENT POSITIONS: {active}/30")
print(f"   Pre-TP1: {pre_tp1}")
print(f"   Post-TP1: {post_tp1}")
print(f"   Free slots: {30 - active}")
print()

print("✅ Weekly report complete")
PY
