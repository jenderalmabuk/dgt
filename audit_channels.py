import json
from collections import Counter, defaultdict

path = '/home/fusion_omega/fusion_omega_nexus/journal/trade_history.json'
with open(path) as f:
    trades = json.load(f)

# Channel activity from root source_chat_id (post-backfill)
ch_stats = defaultdict(lambda: {'trades': 0, 'wins': 0, 'losses': 0, 'pnl': 0.0})

for t in trades:
    scid = t.get('source_chat_id') or 'NONE'
    pnl = float(t.get('pnl_usd', 0) or 0)
    ch_stats[scid]['trades'] += 1
    ch_stats[scid]['pnl'] += pnl
    if pnl > 0:
        ch_stats[scid]['wins'] += 1
    elif pnl < 0:
        ch_stats[scid]['losses'] += 1

# Config channels
config_channels = [
    -1003934526446, -1002817127218, -1002458566069, -1002264738008, -1002224539786,
    -1002097370390, -1002078531832, -1002030275921, -1001982472141, -1001959071837,
    -1001855648946, -1001783301467, -1001754095061, -1001727857237, -1001652601224,
    -1001622654998, -1001594522150, -1001590450482, -1001552004524, -1001553551852,
    -1001347728413, -1001334826070, -1001317169843, -1001297561296, -1001161683441,
    -1001164308989, -1001078354109, -1003988458515, -1001700314832
]

lines = []
lines.append(f"Total trades: {len(trades)}")
lines.append("")
lines.append(f"{'Channel ID':<20} {'Trades':>6} {'W':>4} {'L':>4} {'Win%':>6} {'Net PnL':>10}")
lines.append("-" * 55)

active = []
silent = []
for ch in config_channels:
    ch_str = str(ch)
    stats = ch_stats.get(ch_str, {'trades': 0, 'wins': 0, 'losses': 0, 'pnl': 0.0})
    if stats['trades'] > 0:
        wr = stats['wins'] / stats['trades'] * 100 if stats['trades'] > 0 else 0
        lines.append(f"{ch_str:<20} {stats['trades']:>6} {stats['wins']:>4} {stats['losses']:>4} {wr:>5.1f}% {stats['pnl']:>+9.2f}")
        active.append(ch_str)
    else:
        silent.append(ch_str)

lines.append("")
lines.append(f"Active: {len(active)} channels")
lines.append(f"Silent: {len(silent)} channels")
lines.append("")
lines.append("Silent channels:")
for s in silent:
    lines.append(f"  {s}")

none_stats = ch_stats.get('NONE', {'trades': 0})
lines.append("")
lines.append(f"NONE (no channel ID): {none_stats['trades']} trades")

with open('/tmp/channel_audit.txt', 'w') as f:
    f.write('\n'.join(lines))
print('DONE')