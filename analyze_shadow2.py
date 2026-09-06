import json
from collections import defaultdict

path = '/home/fusion_omega/fusion_omega_nexus/journal/shadow_trailing.jsonl'
shadow = []
with open(path) as f:
    for line in f:
        line = line.strip()
        if line:
            shadow.append(json.loads(line))

# Check what smart_sl_tp_shadow pre_tp1_conflict_exit looks like
for s in shadow:
    sltp = s.get('smart_sl_tp_shadow', {})
    p = sltp.get('pre_tp1_conflict')
    if p:
        print(json.dumps(p, indent=2, default=str))
        break

# Check hybrid
for s in shadow:
    sltp = s.get('smart_sl_tp_shadow', {})
    h = sltp.get('hybrid')
    if h:
        print("\n=== hybrid ===")
        print(json.dumps(h, indent=2, default=str))
        break

# Count pre_tp1_conflict would_exit
pre_exit = sum(1 for s in shadow if (s.get('smart_sl_tp_shadow', {}).get('pre_tp1_conflict') or {}).get('would_exit'))
print(f"\nPre-TP1 conflict exit would trigger: {pre_exit}/{len(shadow)}")

# Check for loss trades only: what would pre_tp1_conflict do?
losses_pre = 0
losses_pre_exit = 0
for s in shadow:
    bl = float(s.get('baseline_net_pnl', 0) or 0)
    if bl < 0:
        losses_pre += 1
        p = (s.get('smart_sl_tp_shadow', {}) or {}).get('pre_tp1_conflict', {})
        if p and p.get('would_exit'):
            losses_pre_exit += 1

print(f"Losses where pre_tp1_conflict would exit: {losses_pre_exit}/{losses_pre}")