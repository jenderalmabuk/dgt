import json
from collections import defaultdict

path = '/home/fusion_omega/fusion_omega_nexus/journal/shadow_trailing.jsonl'
shadow = []
with open(path) as f:
    for line in f:
        line = line.strip()
        if line:
            shadow.append(json.loads(line))

lines = []
lines.append(f"Total shadow entries: {len(shadow)}")

# Check first entry fields
if shadow:
    lines.append(f"Sample fields: {sorted(shadow[0].keys())}")
    has_variants = sum(1 for s in shadow if s.get('shadow_variants'))
    lines.append(f"Has shadow_variants: {has_variants}/{len(shadow)}")

# Aggregate baseline vs shadow_net_pnl
total_baseline = sum(float(s.get('baseline_net_pnl', 0) or 0) for s in shadow)
total_shadow = sum(float(s.get('shadow_net_pnl', 0) or 0) for s in shadow)
total_delta = sum(float(s.get('shadow_delta_usd', 0) or 0) for s in shadow)
lines.append(f"Overall: baseline={total_baseline:+.2f} shadow={total_shadow:+.2f} delta={total_delta:+.2f}")

# Check shadow_variants structure
for s in shadow:
    sv = s.get('shadow_variants', {})
    if sv:
        lines.append(f"Sample shadow_variants keys: {list(sv.keys())}")
        # Print first variant
        for k, v in sv.items():
            lines.append(f"  {k}: {v}")
        break

# Aggregate by shadow_triggered
triggered = sum(1 for s in shadow if s.get('shadow_triggered'))
lines.append(f"\nShadow triggered: {triggered}/{len(shadow)}")

# For shadow_triggered trades: compare baseline vs shadow
trig_baseline = sum(float(s.get('baseline_net_pnl', 0) or 0) for s in shadow if s.get('shadow_triggered'))
trig_shadow = sum(float(s.get('shadow_net_pnl', 0) or 0) for s in shadow if s.get('shadow_triggered'))
trig_delta = sum(float(s.get('shadow_delta_usd', 0) or 0) for s in shadow if s.get('shadow_triggered'))
lines.append(f"Triggered trades: baseline={trig_baseline:+.2f} shadow={trig_shadow:+.2f} delta={trig_delta:+.2f}")

# Non-triggered
non_trig_baseline = sum(float(s.get('baseline_net_pnl', 0) or 0) for s in shadow if not s.get('shadow_triggered'))
non_trig_shadow = sum(float(s.get('shadow_net_pnl', 0) or 0) for s in shadow if not s.get('shadow_triggered'))
lines.append(f"Non-triggered: baseline={non_trig_baseline:+.2f} shadow={non_trig_shadow:+.2f}")

# Loss analysis: trades where baseline was negative
losses = [s for s in shadow if float(s.get('baseline_net_pnl', 0) or 0) < 0]
lines.append(f"\nLoss trades: {len(losses)}")
if losses:
    loss_baseline = sum(float(s.get('baseline_net_pnl', 0) or 0) for s in losses)
    loss_shadow = sum(float(s.get('shadow_net_pnl', 0) or 0) for s in losses)
    loss_delta = sum(float(s.get('shadow_delta_usd', 0) or 0) for s in losses)
    lines.append(f"Loss trades: baseline={loss_baseline:+.2f} shadow={loss_shadow:+.2f} delta={loss_delta:+.2f}")
    
    # How many losses would have been reduced/won by shadow?
    improved = sum(1 for s in losses if float(s.get('shadow_delta_usd', 0) or 0) > 0)
    worsened = sum(1 for s in losses if float(s.get('shadow_delta_usd', 0) or 0) < 0)
    lines.append(f"Losses improved by shadow: {improved}/{len(losses)}")
    lines.append(f"Losses worsened by shadow: {worsened}/{len(losses)}")

# Per-entry detail for losses
lines.append(f"\n{'Symbol':<15} {'Side':<5} {'Baseline':>9} {'Shadow':>9} {'Delta':>8} {'Triggered':<10}")
lines.append("-" * 60)
for s in sorted(losses, key=lambda x: float(x.get('shadow_delta_usd', 0) or 0), reverse=True):
    sym = s.get('symbol', '?')[:14]
    side = s.get('side', '?')[:4]
    bl = float(s.get('baseline_net_pnl', 0) or 0)
    sh = float(s.get('shadow_net_pnl', 0) or 0)
    d = float(s.get('shadow_delta_usd', 0) or 0)
    trig = 'YES' if s.get('shadow_triggered') else 'no'
    lines.append(f"{sym:<15} {side:<5} {bl:>+8.2f} {sh:>+8.2f} {d:>+7.2f} {trig:<10}")

with open('/tmp/shadow_analysis.txt', 'w') as f:
    f.write('\n'.join(lines))
print('DONE')