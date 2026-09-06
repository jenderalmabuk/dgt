# Invalidation Race Fix + Repair Guard Expansion

**Date:** 2026-08-10  
**Issue:** Bot crash karena trigger valid tetapi current M15 15 menit kemudian sudah invalid  
**Status:** ✅ COMPLETE - All tests pass, production untouched

---

## Problem Summary

### Race Condition (Line 863)
Bot fatal error `"winner current market invalid"` terjadi karena:
- **T=0**: Trigger valid, M15 candle masih valid (e.g., BMTUSDT 1.9100 > invalid 1.9056)
- **T+15min**: Executor validate current M15 candle sudah invalid (1.9000 < 1.9056)
- Bot crash dengan `exposure_possible=False` (belum submit order)

### Repair Guard Limitation
`rebind_inert_state()` hanya accept watching state, tidak accept fatal state dengan error ini meskipun:
- Exact nonce/timestamp/plan/fingerprint binding
- `exposure_possible=False` (safe to rebind)
- Error spesifik `"winner current market invalid"`

---

## Fixes Applied

### 1. Invalidation Race Fix (Lines 919-931)
**Before:**
```python
if not market_ok(current,now) or is_invalid(plans[candidate],current["m15_close"]):
    raise PermanentError("winner current market invalid")
```

**After:**
```python
if not market_ok(current,now):
    raise PermanentError("winner current market invalid")
# Validate trigger-candle M15, not current M15 (race fix)
trigger_m15_boundary = (trigger // 900) * 900
trigger_m15_candles = runtime.read("/v5/market/kline", {"category":"linear", "symbol":candidate,
    "interval":"15", "limit":2})
trigger_m15_rows = [[str(int(r[0]) // 1000), *r[1:]] for r in trigger_m15_candles.get("list", [])]
trigger_m15_closed = [r for r in trigger_m15_rows if int(r[0]) == trigger_m15_boundary]
if not trigger_m15_closed:
    raise PermanentError("winner trigger candle not found")
trigger_m15_close = float(trigger_m15_closed[0][4])
if is_invalid(plans[candidate], trigger_m15_close):
    raise PermanentError("winner trigger market invalid")
```

**Behavior:**
- Validate M15 candle AT trigger timestamp, bukan current M15
- Preserve trigger freshness 15-min requirement
- Keep all PostOnly/risk/zone/account checks intact

---

### 2. Repair Guard Expansion (Lines 329-394)

**Added `is_repairable_fatal_state(state, approved)` function:**
```python
def is_repairable_fatal_state(state, approved):
    """Check if fatal state with 'winner current market invalid' can be repaired.
    
    Accepts fatal states with exact approval binding (nonce/timestamp/plan/fingerprint)
    where error is 'winner current market invalid' and exposure_possible=False.
    Tamper fails closed: different nonce/fingerprint rejects.
    """
```

**Checks:**
1. `status == "fatal"`
2. `fatal_reason == "winner current market invalid"` (specific error only)
3. `exposure_possible == False` (no risk)
4. Exact nonce match: `stored.nonce == approved.nonce`
5. Exact timing: `issued_at` and `expires_at` match
6. Exact fingerprint: `stored.fingerprint == approved.fingerprint` (for dynamic plans)
7. Exact source hash: `stored.executorSourceSha256 == approved.executorSourceSha256`

**Enhanced `rebind_inert_state()`:**
- Accept either `is_inert_state()` OR `is_repairable_fatal_state()`
- Preserve plan metadata when repairing dynamic fatal states
- Reconstruct `new_state()` with plan_document/fingerprint intact

---

## Test Coverage

### New Tests (6 canonical tests)
**File:** `tests/test_basket_invalidation_race_repair.py`

1. **test_winner_evidence_validates_trigger_candle_m15_not_current**  
   ✅ Trigger valid (1.9100 > 1.9056), current invalid (1.9000 < 1.9056) → PASSES

2. **test_winner_evidence_rejects_trigger_candle_already_invalid**  
   ✅ Trigger already invalid at trigger time → FAILS correctly

3. **test_repair_guard_accepts_exact_fatal_with_winner_current_market_invalid**  
   ✅ BMTUSDT 1786403124 exact state + exact approval → rebind succeeds

4. **test_repair_guard_rejects_fatal_with_different_nonce**  
   ✅ Tamper detection: different nonce → rejects

5. **test_repair_guard_rejects_fatal_with_different_fingerprint**  
   ✅ Tamper detection: mutated plan document → rejects

6. **test_repair_guard_rejects_exposure_possible_true**  
   ✅ Safety: `exposure_possible=True` → rejects (not safe to rebind)

### Regression Tests
**File:** `tests/test_basket_live_executor.py`  
✅ All 107 existing tests pass (no regressions)

---

## Test Results

```bash
$ python3 -m pytest tests/test_basket_invalidation_race_repair.py -v
============================= 6 passed ==============================

$ python3 -m pytest tests/test_basket_live_executor.py -v
============================= 107 passed ============================

$ python3 -m pytest tests/test_basket_live_executor.py tests/test_basket_invalidation_race_repair.py -v
============================= 113 passed in 0.81s ==================
```

---

## Production Safety

### Verification Checklist
- ✅ No mutation of `runtime/autonomous_basket/executor.json` (last modified 23:05:39, before work started)
- ✅ No network calls in tests
- ✅ No ARM/runtime edits
- ✅ Syntax validated: `python3 -m py_compile bots/basket_live_executor.py` → OK
- ✅ Both test suites pass
- ✅ Fail-closed semantics preserved (tamper → reject, exposure → reject)

### Production Behavior Change
**Before:** Fatal error `"winner current market invalid"` → stuck, manual repair required  
**After:**  
1. Trigger-candle M15 validation → prevents false positives from later M15 invalidation
2. Exact approval rebind → auto-repair when nonce/timestamp/fingerprint match exactly

### Manual Repair (if needed)
Production state at `/home/fusion_omega/fusion_omega_nexus/runtime/autonomous_basket/executor.json`:
```json
{
  "status": "fatal",
  "fatal_reason": "winner current market invalid",
  "exposure_possible": false,
  "approved": {
    "nonce": "a1786403124",
    "issued_at": 1786403124.284781,
    "expires_at": 1786403424.284781,
    "executorSourceSha256": "552107cbc0df2f7ae7bb930f6a3d729065529899395d50ab5f2a89da1e530151",
    "fingerprint": "5104dd54e15eff5daea4583108bc71031efa1be091ca7db72a82c36d8f818feb"
  }
}
```

**Next run with exact same approval will auto-repair via expanded rebind guard.**

---

## Files Modified

1. **bots/basket_live_executor.py** (+67 lines)
   - Added `is_repairable_fatal_state()` function
   - Enhanced `rebind_inert_state()` to accept repairable fatal states
   - Fixed winner_evidence validation to check trigger-candle M15

2. **tests/test_basket_invalidation_race_repair.py** (NEW, 261 lines)
   - 6 canonical tests for race fix + repair guard

---

## Technical Details

### Trigger M15 Calculation
```python
trigger_m15_boundary = (trigger // 900) * 900  # Round down to 15-min boundary
# Example: trigger=1786403124 → boundary=1786402800 (exact M15 candle start)
```

### Repair Guard Logic
```
Inert state: watching + all nulls + exposure_possible=False
  OR
Repairable fatal: fatal + "winner current market invalid" + exposure_possible=False
                  + exact nonce + exact timestamp + exact fingerprint
```

### Safety Properties Preserved
1. **Freshness:** 15-min trigger window still enforced
2. **PostOnly:** Bid/ask zone checks still enforced  
3. **Risk cap:** $2 modeled loss still enforced
4. **Zone validation:** Trigger zone geometry still validated
5. **Account clear:** Basket exposure checks still enforced
6. **Tamper protection:** Fingerprint mismatch → reject

---

## Summary

✅ **Invalidation race fixed:** Validate trigger-candle M15, not current M15  
✅ **Repair guard expanded:** Accept fatal "winner current market invalid" with exact binding  
✅ **113 tests pass:** 6 new + 107 existing (no regressions)  
✅ **Production safe:** No runtime mutation, fail-closed semantics preserved  
✅ **TDD complete:** All canonical cases covered

**Indonesian:** Bot sekarang validate M15 candle pada saat trigger (bukan 15 menit kemudian), dan auto-repair fatal state dengan exact nonce/fingerprint binding.
