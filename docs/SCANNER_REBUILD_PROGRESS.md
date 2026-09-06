# Nexus Scanner Rebuild — PAUSED at Phase 3

**Status:** ⚠️ **BLOCKED** — Need user decision  
**Issue:** Binance rate limiting (691 pairs too many)  
**Time:** 2026-08-06 07:43 UTC

---

## ✅ COMPLETED

### Phase 1: Universe Fetched (3 min)
- 691 total pairs (Binance 530 + Bybit 463)
- Exchange mapping created
- QTUMUSDT, SOLUSDT verified ✅

### Phase 2: Data Requirements Audited (2 min)
- 5 data categories identified
- Coverage gap: 347→691 (49.8% missing)
- Requirements documented

### Phase 3: Collectors Fix (BLOCKED)
- ✅ KLINE_LIMIT set to 50
- ✅ Universe updated to 691 pairs
- ✅ Collectors restarted
- ❌ **Binance still rate-limited (152 x 429 errors in 2 min)**
- ✅ Bybit healthy (90s cycles, 691 symbols)

---

## ⚠️ BLOCKING ISSUE

**Problem:** 691 pairs × 7 TFs = 4,837 API calls/60s = **OVER Binance limit**

**Evidence:**
```
KLINE_LIMIT: 50 ✅
429 errors: 152/2min ❌
Binance cycle: INCOMPLETE (rate limited)
Bybit cycle: 90.3s ✅ (WORKING for all 691!)
```

---

## 💡 SOLUTION PROPOSED

**Option A: Dual-Exchange Strategy (RECOMMENDED)**

**Concept:**
- Binance: 530 native pairs only (avoid rate limit)
- Bybit: ALL 691 pairs (already working @ 90s)
- Bot: Try Binance first, fallback Bybit (already implemented!)

**Benefits:**
- ✅ Binance load reduced (-161 pairs)
- ✅ ALL 691 pairs covered (via Bybit)
- ✅ Zero rate limits
- ✅ Dual redundancy for 530 common pairs

**Implementation:** 10 minutes
- Create filtered binance_universe.txt (530 pairs)
- Restart collectors
- Verify

---

## 📋 ALTERNATIVES

**Option B:** Slow down Binance (120s cycles) — degrades freshness  
**Option C:** Reduce universe back to 347 — defeats rebuild purpose

---

## 🎯 USER DECISION NEEDED

**Question:** Proceed with Option A (dual-exchange)?

**If YES:**
- Implement filtered universe
- 10 min to complete Phase 3
- Then Phase 4 verification

**If NO:**
- Specify alternative approach
- Or accept 347-pair limit

---

**Waiting for user confirmation...**

See: `docs/PHASE3_RATE_LIMIT_ANALYSIS.md` for technical details
