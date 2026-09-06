# Signal Copy Field Mapping — Audit Guide

## Problem History
2026-08-19: Audit salah klasifikasi 15 channel sebagai "silent" (0 trades) karena baca root `source_chat_id` yang selalu `"NONE"`. Data channel sebenarnya tersimpan di `adv_snapshot.source_chat_id` dan `adv_snapshot.signal_source_channel_id`.

## Field Mapping

### Trade History (trade_history.json)

| Field | Location | Value | Catatan |
|---|---|---|---|
| `source_chat_id` | Root | `"NONE"` | **LEGACY — JANGAN DIPAKAI** |
| `source_chat_id` | `adv_snapshot.source_chat_id` | `"-1001161683441"` | ✅ Channel ID sebenarnya |
| `signal_source_channel_id` | `adv_snapshot.signal_source_channel_id` | `"-1001161683441"` | ✅ Sama dengan atas, alias |
| `symbol` | Root | `"ACEUSDT"` | |
| `pnl_usd` | Root | `+12.34` | **BUKAN** `pnl` |
| `reason` | Root | `"HARD_SL"` | **BUKAN** `close_reason` |
| `sl_original` | Root | `0.1720` | **BUKAN** `sl` |
| `sl_kind_at_exit` | Root | `"ORIGINAL"` | |
| `active_sl_at_exit` | Root | `0.1720` | |

### Shadow Trailing (shadow_trailing.jsonl)

| Field | Value | Catatan |
|---|---|---|
| `source_chat_id` | `"-1001161683441"` | ✅ Patched 2026-08-19, sebelumnya tidak ada |
| `symbol` | `"ACEUSDT"` | |
| `baseline_net_pnl` | `+12.34` | |
| `shadow_net_pnl` | `+15.67` | |
| `shadow_delta_usd` | `+3.33` | |

## Patch Applied 2026-08-19

### 1. trade_journal.py `_normalize_trade_record()`
Sebelum `return normalized`, sekarang copy field dari `adv_snapshot` ke root:
- `signal_source`
- `source_chat_id`
- `signal_id`
- `signal_entry_low`, `signal_entry_high`, `signal_active_entry`
- `signal_entry_type`
- `signal_timeframe`, `signal_leverage`
- `signal_tp_ladder`, `signal_raw_text`

### 2. paper_mainnet_trader.py `_append_shadow_result()`
Tambah `"source_chat_id"` field di shadow row, ambil dari `pos.adv_snapshot.source_chat_id`.

## Query Patterns untuk Audit

```bash
# Channel activity (BENAR)
python3 -c "
import json
d = json.load(open('journal/trade_history.json'))
from collections import Counter
c = Counter(t.get('adv_snapshot',{}).get('source_chat_id','NONE') for t in d)
for ch, n in c.most_common(): print(f'{ch}: {n}')
"

# Setelah patch, root-level juga可用:
python3 -c "
import json
d = json.load(open('journal/trade_history.json'))
from collections import Counter
c = Counter(t.get('source_chat_id','NONE') for t in d)
for ch, n in c.most_common(): print(f'{ch}: {n}')
"
```

## Channel Status (2026-08-19)

### Aktif (11 channel)
| Channel ID | Trades | Net PnL |
|---|---|---|
| -1003934526446 | 154 | +$18.56 |
| -1002030275921 | 24 | +$14.87 |
| -1001652601224 | 19 | +$7.12 |
| -1001161683441 | 16 | — |
| -1001622654998 | 15 | +$4.33 |
| -1002264738008 | 8 | -$1.24 |
| -1001164308989 | 5 | +$2.89 |
| -1001594522150 | 5 | +$2.30 |
| -1001347728413 | 3 | +$0.87 |
| -1001078354109 | 2 | -$0.44 |
| -1001317169843 | 1 | +$0.31 |
| -1001700314832 | 1 | -$0.23 |

### Silent (3 channel — 0 trade, 0 parser log)
- `-1001552004524`
- `-1001783301467`
- `-1001590450482`
