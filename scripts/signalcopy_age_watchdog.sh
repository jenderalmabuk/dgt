#!/bin/bash
# SignalCopy Position Age Watchdog
# Alert if any position exceeds max age threshold

cd /home/fusion_omega/fusion_omega_nexus

python3 - <<'PY'
import json
from datetime import datetime, timezone

pos = json.load(open('journal/open_positions.json'))
now = datetime.now(timezone.utc)

total = len(pos)

# Check stuck positions
stuck_pre = []
stuck_post = []
aging_pre = []
aging_post = []

for symbol, p in pos.items():
    opened = datetime.fromisoformat(p['opened_at'].replace('Z', '+00:00'))
    age_h = (now - opened).total_seconds() / 3600
    age_d = age_h / 24
    tp1_hit = p.get('tp1_hit', False)
    
    if not tp1_hit:
        if age_h > 72:
            stuck_pre.append((symbol, age_d))
        elif age_h > 48:
            aging_pre.append((symbol, age_d))
    else:
        if age_h > 168:
            stuck_post.append((symbol, age_d))
        elif age_h > 120:
            aging_post.append((symbol, age_d))

# Build report
if stuck_pre or stuck_post:
    print("🚨 ALERT: STUCK POSITIONS DETECTED")
    print()
    print(f"Total active: {total}")
    print()
    
    if stuck_pre:
        print(f"❌ Pre-TP1 >72h (CRITICAL): {len(stuck_pre)}")
        for sym, age in stuck_pre:
            print(f"   {sym:12} {age:5.1f}d")
        print()
    
    if stuck_post:
        print(f"❌ Post-TP1 >168h (CRITICAL): {len(stuck_post)}")
        for sym, age in stuck_post:
            print(f"   {sym:12} {age:5.1f}d")
        print()
    
    print("⚠️ Max age policy should have closed these!")
    print("Check if gateway is running: docker ps | grep nexus_gateway")

elif aging_pre or aging_post:
    print("⚠️ WARNING: AGING POSITIONS")
    print()
    print(f"Total active: {total}")
    print()
    
    if aging_pre:
        print(f"Pre-TP1 >48h (aging): {len(aging_pre)}")
        for sym, age in aging_pre[:3]:
            print(f"   {sym:12} {age:5.1f}d")
        print()
    
    if aging_post:
        print(f"Post-TP1 >120h (aging): {len(aging_post)}")
        for sym, age in aging_post[:3]:
            print(f"   {sym:12} {age:5.1f}d")
        print()
    
    print("Will be auto-closed if reach threshold (72h / 168h)")

else:
    # Silent on healthy state (no output = no notification)
    exit(0)
PY
