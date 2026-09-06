# Complete Shadow Systems Audit — 2026-08-19

## Overview: 5 Shadow Systems Across 3 Bots

| File | Bot | Type | Entries | Purpose |
|---|---|---|---|---|
| `journal/shadow_trailing.jsonl` | Paper Mainnet | **Trailing Exit** | 216 | Post-TP1 exit variant comparison |
| `runtime/fusion_quantum/ws_lifecycle_shadow_v2.jsonl` | Fusion Quantum | **Lifecycle Scan** | 465 | Zone confirmation tracking (v2) |
| `runtime/fusion_quantum/ws_lifecycle_shadow.jsonl` | Fusion Quantum | **Lifecycle Scan** | 465 | Zone confirmation tracking (v1) |
| `runtime/fusion_quantum/ws_shadow.jsonl` | Fusion Quantum | **Scanner** | 930 | Simpler scan shadow |
| `runtime/autonomous_basket/shadow_double_gate.jsonl` | Autonomous Basket | **Double-Gate** | 16 | Entry validation (ALL REJECTED) |

---

## 1. Shadow Trailing (Paper Mainnet) — ✅ ALREADY ANALYZED

**Key Finding:** Trailing helps post-TP1 (+$123.40 delta) but **79 pre-TP1 HARD_SL losses (-$526) not addressed**.

| Pattern | Count | Baseline | Shadow | Delta |
|---|---|---|---|---|
| Trailing saves losses (post-TP1) | 28 | -$125.93 | +$29.66 | **+$155.60** |
| Full SL, no trail (pre-TP1) | 79 | -$525.89 | -$525.89 | $0 |
| Trailing cuts winners | 47 | +$213.96 | +$181.77 | -$32.20 |

**Best additional exit variant:** `economic_be` — saves +$31.34 from 12 triggered loss trades.

---

## 2. Fusion Quantum Zone Lifecycle (ws_lifecycle_shadow_v2)

**465 scans, 465 unique symbols, 2,198 zones consumed**

| Metric | Value |
|---|---|
| Zone confirmation rate | **18.5%** (406/2,198) |
| Failed confirmation | 81.5% (1,792) |
| Avg scan time | 5.76s |
| Max scan time | 142.75s |
| Stale scans | 0% |

**Interpretation:** Only ~1 in 5 zones get confirmed. High failure rate suggests either:
- Zone criteria too strict
- Market conditions not matching zone expectations
- Timing issues (candle not ready, data stale)

---

## 3. Fusion Quantum Zone Lifecycle v1 (ws_lifecycle_shadow)

**465 scans, 1,863 zones consumed**

| Metric | Value |
|---|---|
| Zone confirmation rate | **19.5%** (364/1,863) |
| Failed confirmation | 80.5% (1,499) |
| Avg scan time | ~similar |

**Note:** v1 and v2 are parallel runs on same symbols. v2 has more consumed zones (2,198 vs 1,863).

---

## 4. Fusion Quantum Simple Scanner (ws_shadow.jsonl)

**930 scans, 465 symbols (2 scans per symbol avg)**

| Metric | Value |
|---|---|
| Unique symbols | 465 |
| Avg scan time | 10.47s |
| Max scan time | 185.16s |

**Purpose:** Lighter weight scan shadow, likely for quick validation before full lifecycle.

---

## 5. Autonomous Basket Double-Gate (shadow_double_gate.jsonl)

**16 entries — ALL REJECTED**

| Live Decision | Count |
|---|---|
| REJECT | 16 |

| Shadow Reason | Count |
|---|---|
| INVALID_INSTRUMENT_METADATA | 16 |

**Critical Issue:** 100% rejection rate. The metadata validation (instrument info, precision, filters) is failing for all candidates. This bot cannot trade until fixed.

---

## Cross-System Summary

| System | Status | Key Issue | Opportunity |
|---|---|---|---|
| **Paper Mainnet Trailing** | ✅ Working | Pre-TP1 losses untouched | Add pre-TP1 early exit logic |
| **FQ Lifecycle v2** | ⚠️ Low confirm | 81.5% zones fail | Tune zone criteria / timing |
| **FQ Lifecycle v1** | ⚠️ Low confirm | 80.5% zones fail | Same as v2 |
| **FQ Simple Scan** | 📊 Monitoring | High scan time (10s avg) | Optimize scan latency |
| **Auto Basket Double-Gate** | 🔴 BROKEN | 100% INVALID_METADATA | Fix instrument metadata |

---

## Recommendations by Priority

### 🔴 CRITICAL — Fix Autonomous Basket
```
Root cause: INVALID_INSTRUMENT_METADATA for all 16 candidates
Action: Check symbol info endpoint, precision, min_qty, filters
Impact: Bot currently cannot execute any trade
```

### 🔴 CRITICAL — Pre-TP1 Loss Reduction (Paper Mainnet)
```
79 HARD_SL losses = -$526 (70% of total losses)
Trailing only works post-TP1
Action: Implement pre-TP1 exit gates:
  - Conflict-based early exit (conflict >= 3 + profit_r <= -0.5)
  - Age-based exit (pre-TP1 > 72h)
  - Bad signal classification early exit
  - economic_be variant deployment (+$31 from 12 losses)
```

### ⚠️ HIGH — Fusion Quantum Zone Confirmation Rate
```
Only 18-19% zone confirmation rate
Action: 
  - Audit zone criteria (entry zone class, structure quality)
  - Check timing: consumed zones show first_touch_at often hours before confirmation
  - Reduce false positives in zone detection
```

### 📊 MEDIUM — Scan Latency Optimization
```
ws_shadow: 10.47s avg, 185s max
ws_lifecycle_v2: 5.76s avg, 142s max
Action: Profile scan bottlenecks, consider async parallel scan
```

### 📊 LOW — Shadow System Consolidation
```
5 shadow systems, 3 different formats
Action: Unify shadow schema for cross-bot comparison
```