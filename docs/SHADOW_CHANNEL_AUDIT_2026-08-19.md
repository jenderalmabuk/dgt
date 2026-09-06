# Shadow Trailing & Channel Audit — 2026-08-19

## Fixes Applied
1. **trade_journal.py** `_normalize_trade_record()`: surfaces `adv_snapshot` fields to root
2. **paper_mainnet_trader.py** `_append_shadow_result()`: includes `source_chat_id`
3. **Backfill**: 240/248 trades patched with root `source_chat_id`
4. **Documentation**: `docs/SIGNAL_COPY_FIELD_MAPPING.md`

## Channel Audit (248 trades)
| Channel ID | Trades | W | L | Win% | Net PnL |
|---|---|---|---|---|---|
| -1003934526446 | 3 | 2 | 1 | 66.7% | +5.04 |
| -1002458566069 | 2 | 1 | 1 | 50.0% | +4.89 |
| -1002264738008 | 8 | 2 | 6 | 25.0% | -16.71 |
| -1002224539786 | 2 | 1 | 1 | 50.0% | -4.97 |
| -1002097370390 | 3 | 2 | 1 | 66.7% | -3.15 |
| -1002030275921 | 7 | 3 | 4 | 42.9% | -20.29 |
| -1001959071837 | 2 | 0 | 2 | 0.0% | -14.82 |
| -1001754095061 | 20 | 12 | 8 | 60.0% | -16.82 |
| **-1001652601224** | 45 | 14 | 31 | **31.1%** | **-155.18** ⚠️ |
| -1001594522150 | 1 | 0 | 1 | 0.0% | -2.14 |
| **-1001553551852** | 29 | 14 | 15 | 48.3% | -32.75 ⚠️ |
| -1001334826070 | 1 | 1 | 0 | 100% | +6.06 |
| -1001297561296 | 2 | 2 | 0 | 100% | +8.66 |
| -1001078354109 | 1 | 0 | 1 | 0.0% | -1.98 |
| **-1001700314832** | 8 | 0 | 8 | **0.0%** | **-61.34** ⚠️ |
| + 10 more channels with <5 trades |

**Silent channels (0 trades):** `-1002817127218`, `-1001783301467`, `-1001552004524`, `-1001164308989`, `-1003988458515`  
**8 trades** have no channel ID (pre-field era)

## Shadow Trailing Analysis (212 entries)
| Metric | Value |
|---|---|
| Overall Baseline | -$239.39 |
| Overall Shadow | -$116.00 |
| **Delta (trailing benefit)** | **+$123.40** |
| Trades where trailing triggered | 75/212 (35%) |

**Loss Breakdown:**
| Pattern | Count | Baseline | Shadow | Impact |
|---|---|---|---|---|
| Trailing saves losses (post-TP1) | 28 | -$125.93 | +$29.66 | +$155.60 ✅ |
| Full SL, no trail (pre-TP1 HARD_SL) | 79 | -$525.89 | -$525.89 | $0 — unavoidable |
| Trailing cuts winners | 47 | +$213.96 | +$181.77 | -$32.20 |

**Best Post-TP1 Exit Variants for Loss Reduction:**
| Variant | Loss Trades Triggered | Total Saved | Avg/Trade |
|---|---|---|---|
| current | 11 | +$43.17 | +$3.92 |
| mfe_giveback | 14 | +$36.41 | +$2.60 |
| economic_be | 12 | +$31.34 | +$2.61 |
| wide_continuation | 11 | +$27.10 | +$2.46 |

## Realizable Loss Reduction
| Priority | Action | Est. Impact |
|---|---|---|
| 1 | Keep trailing (already live) | +$123/mo |
| 2 | Deploy `economic_be` variant | +$31 from 12 loss trades |
| 3 | Pre-TP1 early exit for bad signals (79 losses = -$526) | Potential +$200-300 |
| 4 | Remove worst channels | Immediate improvement |

**Biggest untapped:** 79 pre-TP1 HARD_SL losses (-$526) — need pre-TP1 exit logic