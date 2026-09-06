# QTUM Signal Analysis — 2026-08-06 07:08 UTC

**Signal:** QTUMUSDT SHORT from channel -1001727857237  
**Result:** ❌ NOT EXECUTED  
**Reason:** Capacity limit (21/20 active positions)

---

## ✅ SIGNAL VALIDATION — PASSED

```
Score: 88.0/100
MTF: 30/100 | TV: 43/100
RSI: 75.4 | Regime: TRENDING
CVD z: +0.79
Adversarial: APPROVED
```

**Signal was VALID and APPROVED** ✅

---

## ❌ EXECUTION — BLOCKED

```
[MAX_POS] QTUMUSDT parsed but NOT executed: 
running book full (21 active; 0 pending; cap=20)
```

**Capacity exceeded by 1 position.**

---

## 📊 OI/FUNDING DATA ISSUE

### User Concern:
```
OI 5m/15m/1h: +0.00%/+0.00%/+0.70%
Funding: +0.0001%
```

"masih" (still showing stale data)

### Root Cause:

**QTUMUSDT not in scanner universe:**
- Scanner tracks: 347 symbols
- Binance whitelist: 245 symbols
- Missing: 134 symbols (55%)
- QTUMUSDT: ❌ NOT SCANNED

**Data flow:**
```
Scanner → revo_flow_context.json (347 symbols)
         ↓
Missing QTUM → Fallback to nexus_cache (626h old)
         ↓
Stale OI/Funding data in notice
```

### Impact Analysis:

**On validation:** ✅ NO IMPACT
- Score: 88.0 (valid)
- Adversarial: approved
- Signal processed correctly

**On execution:** ✅ NO IMPACT
- Rejection reason: capacity (21/20)
- NOT rejected due to data quality

**On notices:** ⚠️ COSMETIC IMPACT
- OI shows 0.00%/0.00% (stale)
- Funding shows 0.0001% (default)
- User sees less accurate market context

---

## 🎯 ACTUAL PROBLEM

**Not data quality — CAPACITY LIMIT!**

```
Active: 21 positions
Limit:  20 positions
Result: QTUM blocked (and future signals will be blocked)
```

---

## 💡 SOLUTIONS

### Priority 1: Capacity Issue (Urgent)

**Option A:** Close 1-2 positions manually to free slots  
**Option B:** Increase cap to 25-30 (if risk allows)  
**Option C:** Let max age policy close aging positions (next run 9:00 UTC)

### Priority 2: OI/Funding Data (Non-urgent)

**Current state:**
- 45% pairs: fresh data ✅
- 55% pairs: stale cache ⚠️
- System functional ✅
- Notices less accurate ⚠️

**Options:**
- Accept current (low priority cosmetic issue)
- Expand scanner universe (add 134 missing pairs)
- Add direct API fallback for missing symbols

---

## 📋 SUMMARY

| Aspect | Status | Impact |
|---|---|---|
| Signal parsing | ✅ Working | None |
| Validation | ✅ Working | None |
| Adversarial | ✅ Working | None |
| OI/Funding data | ⚠️ Stale for 55% pairs | Cosmetic (notices) |
| Execution | ❌ Blocked | **HIGH (no new entries!)** |

**Root cause of QTUM rejection:** Capacity (21/20), NOT data  
**Root cause of stale OI/Funding:** Scanner coverage (45% vs 100%)

**Urgent:** Fix capacity  
**Non-urgent:** Expand scanner coverage

---

**Next Steps:**

1. Check which positions can close (age, profit)
2. Free 1-2 slots for new signals
3. (Optional) Expand scanner to cover all Binance pairs

---

**Created:** 2026-08-06 07:08 UTC  
**Signal time:** 2026-08-06 06:58 UTC  
**Capacity:** 21/20 (over by 1)
