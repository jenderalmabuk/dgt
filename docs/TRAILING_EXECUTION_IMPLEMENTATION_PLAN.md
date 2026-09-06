# Shadow Trailing — Execution Mode Implementation Plan

**Date:** 2026-08-07 01:35 UTC  
**Status:** DRAFT — Review before implementation  
**Risk Level:** MEDIUM (changes live execution behavior)

---

## 📋 CURRENT STATE

### Code Analysis

**File:** `execution/paper_mainnet_trader.py`

**Current behavior (line 406-443):**
```python
def _update_shadow_trailing(self, pos: Dict[str, Any], mark: float) -> None:
    """Counterfactual only. Never mutates provider SL, TP, qty, or execution."""
    # ... shadow logic calculates trailing stop ...
    if hit:
        # COUNTERFACTUAL: only journals "WOULD_EXIT", does NOT execute
        logger.info("[SHADOW_TRAIL] WOULD_EXIT %s %s @ %.6g", ...)
        pos["shadow_exit_price"] = actual_exit
        # Does NOT update pos["sl"] or trigger real exit
```

**Called from:** Line 684 in management loop (every tick, for every open position).

**Environment:**
- `SHADOW_TRAILING_ENABLED=true` (already set)
- `SHADOW_TRAIL_MIN_PCT=0.01` (1% trail)
- `SHADOW_TRAIL_ATR_MULTIPLIER=1.0`

---

## 🎯 GOAL

**Enable real execution:** Update `pos["sl"]` with trailing stop, trigger real exit when hit.

---

## 🔧 IMPLEMENTATION OPTIONS

### Option A: Full Execution Mode (Aggressive)

**What:**
- Remove "counterfactual only" restriction
- Update real `pos["sl"]` with shadow trailing stop
- Exit position when trailing stop hit

**Changes:**
```python
def _update_shadow_trailing(self, pos: Dict[str, Any], mark: float) -> None:
    """Real trailing stop execution (LIVE mode)."""
    # ... existing shadow logic ...
    
    # NEW: Update real SL
    if not pos.get("shadow_trailing_active"):
        pos["shadow_trailing_active"] = True
        pos["shadow_activated_at"] = datetime.now(UTC)
        # ... watermarks ...
    
    # Calculate trailing stop
    if side == "LONG":
        candidate = pos["shadow_high_watermark"] * (1.0 - trail)
        new_sl = max(float(pos.get("sl", 0) or 0), candidate)
    else:
        candidate = pos["shadow_low_watermark"] * (1.0 + trail)
        old = float(pos.get("sl", 0) or 0)
        new_sl = min(old, candidate) if old > 0 else candidate
    
    # NEW: Update real SL (not just shadow)
    if new_sl != pos.get("sl"):
        logger.info("[TRAILING_SL] %s %s | SL %.6g → %.6g (trail %.2f%%)",
                    pos["symbol"], side, pos.get("sl", 0), new_sl, trail*100)
        pos["sl"] = new_sl
    
    # Check if hit
    hit = (mark <= new_sl) if side == "LONG" else (mark >= new_sl)
    if hit:
        # NEW: Trigger real exit (not counterfactual)
        logger.info("[TRAILING_SL] EXIT %s %s @ %.6g (SL hit)", 
                    pos["symbol"], side, new_sl)
        # Existing exit logic handles the rest
```

**Pros:**
- ✅ Simple, direct
- ✅ Audit data proves benefit
- ✅ No env var confusion

**Cons:**
- ⚠️ Cannot easily revert without code change
- ⚠️ No A/B testing option
- ⚠️ Changes ALL positions immediately

**Risk:** MEDIUM

---

### Option B: Hybrid Mode with Toggle (Safe)

**What:**
- Keep shadow journaling logic
- Add separate execution path controlled by env var
- Can toggle on/off without rebuild

**Changes:**
```python
# Add new env var
TRAILING_EXECUTION_ENABLED = os.getenv("TRAILING_EXECUTION_ENABLED", "false").lower() in ("1", "true", "yes")

def _update_shadow_trailing(self, pos: Dict[str, Any], mark: float) -> None:
    """Shadow trailing: journaling + optional execution."""
    if not self._shadow_enabled() or pos.get("shadow_exit_price"):
        return
    
    # ... existing shadow logic (journaling) ...
    
    # NEW: Optional execution mode
    if TRAILING_EXECUTION_ENABLED and not hit:
        # Update real SL with trailing stop
        if side == "LONG":
            new_sl = max(float(pos.get("sl", 0) or 0), pos["shadow_stop"])
        else:
            old = float(pos.get("sl", 0) or 0)
            new_sl = min(old, pos["shadow_stop"]) if old > 0 else pos["shadow_stop"]
        
        if new_sl != pos.get("sl"):
            logger.info("[TRAILING_SL] %s %s | SL %.6g → %.6g",
                        pos["symbol"], side, pos.get("sl", 0), new_sl)
            pos["sl"] = new_sl
            # Exit logic already handles SL hits in main loop
```

**Env var:**
```bash
TRAILING_EXECUTION_ENABLED=true   # Enable real execution
TRAILING_EXECUTION_ENABLED=false  # Keep journaling only (current)
```

**Pros:**
- ✅ Easy rollback (just env var)
- ✅ Can A/B test
- ✅ Shadow journaling continues (data comparison)
- ✅ Safe incremental rollout

**Cons:**
- ⚠️ More complex code
- ⚠️ Two code paths to maintain

**Risk:** LOW

---

### Option C: Gradual Rollout (Conservative)

**What:**
- Start with partial activation (e.g., only after TP2 hit)
- Monitor closely
- Expand activation criteria gradually

**Changes:**
```python
def _update_shadow_trailing(self, pos: Dict[str, Any], mark: float) -> None:
    """Shadow trailing with gradual activation."""
    # ... existing shadow logic ...
    
    # NEW: Gradual activation criteria
    activate_execution = (
        TRAILING_EXECUTION_ENABLED and
        pos.get("tp1_hit") is True and
        pos.get("tp2_hit") is True  # Only after TP2 (more conservative)
    )
    
    if activate_execution:
        # Update real SL
        # ... same as Option B ...
```

**Pros:**
- ✅ Lowest risk
- ✅ Learn from TP2+ positions first
- ✅ Can expand gradually

**Cons:**
- ⚠️ Slower benefit realization
- ⚠️ Complex activation logic

**Risk:** VERY LOW

---

## 📊 RECOMMENDATION

### ✅ Option B: Hybrid Mode with Toggle

**Reasoning:**
1. ✅ **Safe rollback:** Just change env var, no rebuild
2. ✅ **Data continuity:** Shadow journaling continues for comparison
3. ✅ **Flexible:** Can enable/disable per environment
4. ✅ **Audit trail:** Can compare shadow vs real execution

**Implementation:**
1. Add `TRAILING_EXECUTION_ENABLED` env var (default: false)
2. Modify `_update_shadow_trailing()` to update real SL when enabled
3. Keep shadow journaling active (for audit)
4. Add logging for SL updates
5. Rebuild Docker images
6. Set `TRAILING_EXECUTION_ENABLED=true` in env
7. Restart containers

---

## 🔍 DETAILED IMPLEMENTATION

### Step 1: Code Changes

**File:** `execution/paper_mainnet_trader.py`

**Add at top (near other env vars):**
```python
TRAILING_EXECUTION_ENABLED = os.getenv("TRAILING_EXECUTION_ENABLED", "false").lower() in ("1", "true", "yes")
```

**Modify `_update_shadow_trailing()` (line 406):**

```python
def _update_shadow_trailing(self, pos: Dict[str, Any], mark: float) -> None:
    """Shadow trailing: journaling + optional live execution."""
    if not self._shadow_enabled() or pos.get("shadow_exit_price"):
        return
    
    # Require TP1 hit (same as before)
    if pos.get("tp1_hit") is not True:
        return
    
    side = pos["side"]
    trail = self._shadow_trail_pct(pos)
    
    # Initialize shadow tracking
    if not pos.get("shadow_trailing_active"):
        pos["shadow_trailing_active"] = True
        pos["shadow_activated_at"] = datetime.now(UTC)
        pos["shadow_high_watermark"] = mark
        pos["shadow_low_watermark"] = mark
    
    # Update watermarks
    pos["shadow_high_watermark"] = max(float(pos.get("shadow_high_watermark", mark)), mark)
    pos["shadow_low_watermark"] = min(float(pos.get("shadow_low_watermark", mark)), mark)
    
    # Calculate shadow trailing stop
    if side == "LONG":
        candidate = pos["shadow_high_watermark"] * (1.0 - trail)
        shadow_stop = max(float(pos.get("shadow_stop", 0) or 0), candidate)
        hit = mark <= shadow_stop
    else:
        candidate = pos["shadow_low_watermark"] * (1.0 + trail)
        old = float(pos.get("shadow_stop", 0) or 0)
        shadow_stop = min(old, candidate) if old > 0 else candidate
        hit = mark >= shadow_stop
    
    pos["shadow_stop"] = shadow_stop
    
    # Shadow journaling (always track counterfactual)
    if hit and not pos.get("shadow_exit_logged"):
        actual_exit = self._adverse_fill(shadow_stop, side, opening=False)
        qty = float(pos.get("qty", 0))
        gross = ((actual_exit - pos["entry_price"]) * qty if side == "LONG"
                 else (pos["entry_price"] - actual_exit) * qty)
        fee = self._fee(actual_exit * qty)
        pos["shadow_exit_price"] = actual_exit
        pos["shadow_exit_at"] = datetime.now(UTC)
        pos["shadow_remaining_net_pnl"] = gross - fee
        pos["shadow_exit_logged"] = True
        logger.info("[SHADOW_TRAIL] WOULD_EXIT %s %s @ %.6g stop=%.6g qty=%.8g",
                    pos["symbol"], side, actual_exit, shadow_stop, qty)
    
    # NEW: Optional live execution
    if TRAILING_EXECUTION_ENABLED and not hit:
        # Update real SL with trailing stop
        current_sl = float(pos.get("sl", 0) or 0)
        
        if side == "LONG":
            new_sl = max(current_sl, shadow_stop)
        else:
            new_sl = min(current_sl, shadow_stop) if current_sl > 0 else shadow_stop
        
        # Only update if changed
        if abs(new_sl - current_sl) > 1e-8:  # Avoid floating point noise
            logger.info("[TRAILING_SL] %s %s | SL %.8g → %.8g (trail %.2f%%)",
                        pos["symbol"], side, current_sl, new_sl, trail*100)
            pos["sl"] = new_sl
            # Note: Exit logic in main loop already checks pos["sl"] vs mark
```

---

### Step 2: Environment Configuration

**Add to `.env` and `docker/.env`:**
```bash
# Shadow trailing execution (enable real trailing stop)
TRAILING_EXECUTION_ENABLED=true
```

**Add to `docker-compose.yml` gateway service:**
```yaml
environment:
  # ... existing vars ...
  TRAILING_EXECUTION_ENABLED: ${TRAILING_EXECUTION_ENABLED:-false}
```

---

### Step 3: Rebuild & Deploy

```bash
cd /home/fusion_omega/fusion_omega_nexus/docker

# 1. Update env files
echo "TRAILING_EXECUTION_ENABLED=true" >> ../.env
echo "TRAILING_EXECUTION_ENABLED=true" >> .env

# 2. Rebuild images
docker compose build analyst_bot      # Builds nexus-bot:latest (gateway)
docker compose build signal_copy      # Builds signal_copy:latest

# 3. Restart containers
docker compose up -d --force-recreate gateway signal_copy

# 4. Verify
docker exec nexus_gateway env | grep TRAILING_EXECUTION
docker logs nexus_gateway --tail 20
```

---

### Step 4: Monitoring

**First 5 trades:**
```bash
# Watch for trailing SL updates
docker logs -f nexus_gateway | grep TRAILING_SL

# Check shadow journal
tail -f /home/fusion_omega/fusion_omega_nexus/journal/shadow_trailing.jsonl

# Compare real exits vs shadow predictions
```

**Expected logs:**
```
[TRAILING_SL] BTCUSDT LONG | SL 45000.0 → 45500.0 (trail 1.00%)
[TRAILING_SL] BTCUSDT LONG | SL 45500.0 → 46000.0 (trail 1.00%)
[TRAILING_SL] EXIT BTCUSDT LONG @ 46000.0 (SL hit)
```

---

## ⚠️ RISKS & MITIGATIONS

### Risk 1: Trailing SL updates too frequent

**Symptom:** Log spam, performance impact

**Mitigation:**
- Added float comparison threshold (`abs(new_sl - current_sl) > 1e-8`)
- Only log when SL actually changes

### Risk 2: Conflict with existing SL logic

**Symptom:** SL reverts, unexpected exits

**Mitigation:**
- Trailing SL only RAISES SL for LONG (never lowers)
- Trailing SL only LOWERS SL for SHORT (never raises)
- Uses `max()` / `min()` to preserve provider SL if tighter

### Risk 3: TP1 requirement delays activation

**Symptom:** Position reverses before TP1, no trailing protection

**Mitigation:**
- This is intentional (shadow data shows TP1+ is where benefit comes)
- Can modify to activate immediately if needed

### Risk 4: Code bugs

**Symptom:** Unexpected exits, wrong SL values

**Mitigation:**
- Monitor first 5 trades closely
- Shadow journaling continues (can compare real vs shadow)
- Easy rollback via env var

---

## 🎯 SUCCESS CRITERIA

### Week 1 (Aug 7-14)

✅ **No critical bugs** (unexpected exits, wrong SL calculation)  
✅ **Trailing SL updates logged** (confirms activation)  
✅ **First 5 exits match expected behavior**  
✅ **Net PnL positive vs baseline expectation**

### Week 2 (Aug 14-21)

✅ **Win rate improvement sustained** (target: +20pp minimum)  
✅ **Average PnL/trade positive** (target: +$1.50 minimum)  
✅ **No user complaints** (unexpected behavior)

### Decision Point (Aug 14)

- **Keep:** If net positive (expected)
- **Tune:** If mixed results (adjust trail %)
- **Revert:** If net negative (set `TRAILING_EXECUTION_ENABLED=false`)

---

## 📋 ROLLBACK PLAN

**If problems occur:**

1. **Immediate (no rebuild):**
   ```bash
   # Stop execution, keep journaling
   docker exec nexus_gateway sh -c "export TRAILING_EXECUTION_ENABLED=false"
   docker restart nexus_gateway
   ```

2. **Permanent (with rebuild):**
   ```bash
   # Edit .env
   sed -i 's/TRAILING_EXECUTION_ENABLED=true/TRAILING_EXECUTION_ENABLED=false/' .env
   
   # Rebuild & restart
   docker compose build analyst_bot
   docker compose up -d --force-recreate gateway
   ```

3. **Full revert (emergency):**
   ```bash
   # Remove code changes via git
   cd /home/fusion_omega/fusion_omega_nexus
   git checkout execution/paper_mainnet_trader.py
   
   # Rebuild
   cd docker
   docker compose build analyst_bot signal_copy
   docker compose up -d --force-recreate gateway signal_copy
   ```

---

## 📊 EXPECTED OUTCOME

Based on 23-trade audit:

| Metric | Before | After (Expected) |
|---|---:|---:|
| Win rate | 52.2% | 82.6% (+30pp) |
| Avg PnL/trade | $0.63 | $2.85 (+$2.22) |
| Monthly (60 trades) | $37.80 | $171.00 (+$133.20) |

**Timeline:**
- **Aug 7:** Implement & deploy
- **Aug 7-14:** Monitor (first 10-20 trades)
- **Aug 14:** Review & decide (keep/tune/revert)

---

## ✅ APPROVAL NEEDED

**Question for user:**

1. **Option B (Hybrid mode with toggle)** — agree?
2. **Activation criteria:** TP1 hit only, or immediate?
3. **Trail %:** 1% default, or different?
4. **Deploy now or wait?**

**Proceed with implementation?**

---

**Plan prepared:** 2026-08-07 01:35 UTC  
**Author:** Hermes Agent (Kiro)  
**Status:** AWAITING USER APPROVAL
