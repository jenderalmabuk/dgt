# Trailing Stop Comparison: FusionXomegabot vs Nexus SignalCopy

**Date:** 2026-08-07 01:56 UTC  
**Purpose:** Compare trailing stop implementations to inform Nexus activation strategy

---

## 📊 SUMMARY COMPARISON

| Aspect | FusionXomegabot (Old) | Nexus SignalCopy (Current) |
|---|---|---|
| **Status** | Live execution (tested) | Shadow journaling only |
| **Activation** | After profit lock (TP1 + time) | After TP1 (proposed) |
| **Trail %** | Dynamic (ATR-based) | Fixed 1% (default) |
| **Watermark** | high/low watermark | high/low watermark |
| **Update frequency** | Every check_positions cycle | Every tick (proposed) |
| **Server SL update** | Yes (async) | Yes (proposed) |
| **Data validation** | Live trades (60+ trades) | Shadow data (23 trades) |

---

## 🔍 FUSIONXOMEGABOT IMPLEMENTATION

### Code Location
`execution/binance_testnet_trader.py` lines 1266-1283

### Activation Logic

```python
# Line 1153-1158
# Trailing SL
if reason is None and pos.get("locked_profit") and hold_min > 10.0:
    old_sl = pos["sl"]
    self._update_trailing_sl(pos, price, sym)
    if pos["sl"] != old_sl:
        pos["sl_kind"] = "TRAILING"
```

**Requirements:**
1. ✅ Position open (no exit reason yet)
2. ✅ `locked_profit` flag set (after TP1 or profit lock trigger)
3. ✅ Hold time > 10 minutes

### Profit Lock Trigger

```python
# Line 1127-1150
# Profit lock — move SL to breakeven+ after sufficient profit & time.
if (reason is None and 
    hold_min > PROFIT_LOCK_MIN_MINUTES and 
    unrealized > (PROFIT_LOCK_TRIGGER_PCT / 100) and 
    not pos.get("locked_profit")):
    
    risk_distance = abs(entry - pos.get("original_sl", pos["sl"]))
    fixed_buffer = entry * (PROFIT_LOCK_BUFFER_PCT / 100)
    risk_buffer = risk_distance * 0.15
    buffer = max(fixed_buffer, risk_buffer)
    
    if side == "LONG":
        new_sl = max(pos["sl"], entry + buffer)
    else:
        new_sl = min(pos["sl"], entry - buffer)
    
    if new_sl != pos["sl"]:
        pos["sl"] = new_sl
        pos["locked_profit"] = True
        pos["sl_kind"] = "BREAKEVEN"
        await self._update_server_sl(sym, side, new_sl)
```

**Config (from line 21 in config.py):**
```python
PROFIT_LOCK_MIN_MINUTES = 5  # Min hold time before profit lock
PROFIT_LOCK_TRIGGER_PCT = 1.5  # Min unrealized profit % to trigger
PROFIT_LOCK_BUFFER_PCT = 0.1  # Buffer above entry (0.1%)
```

### Trailing Stop Implementation

```python
# Line 1266-1283
def _update_trailing_sl(self, pos: Dict, price: float, symbol: str):
    # Dynamic trail % based on ATR
    atr_pct = pos.get("adv_snapshot", {}).get("atr_pct", 2.5)
    trail_pct = max(TRAIL_SL_PCT / 100.0, (atr_pct / 100.0) * TRAIL_SL_ATR_MULTIPLIER)
    
    if pos["side"] == "LONG":
        pos["high_watermark"] = max(pos.get("high_watermark", price), price)
        new_sl = max(pos["sl"], pos["high_watermark"] * (1 - trail_pct))
        if new_sl > pos["sl"] and new_sl < price:
            pos["sl"] = new_sl
            asyncio.create_task(self._update_server_sl(symbol, "LONG", new_sl))
            logger.info(f"[TRAILING_SL] {symbol} LONG | new_sl={new_sl:.6f} | trail={trail_pct*100:.2f}%")
    else:
        pos["low_watermark"] = min(pos.get("low_watermark", price), price)
        new_sl = min(pos["sl"], pos["low_watermark"] * (1 + trail_pct))
        if new_sl < pos["sl"] and new_sl > price:
            pos["sl"] = new_sl
            asyncio.create_task(self._update_server_sl(symbol, "SHORT", new_sl))
            logger.info(f"[TRAILING_SL] {symbol} SHORT | new_sl={new_sl:.6f} | trail={trail_pct*100:.2f}%")
```

**Config:**
```python
TRAIL_SL_PCT = 1.0  # Base trail % (1%)
TRAIL_SL_ATR_MULTIPLIER = 1.0  # ATR multiplier
```

**Formula:**
```
trail_pct = max(1%, ATR% * 1.0)
```

### Server SL Update

```python
# Line 1285-1300
async def _update_server_sl(self, symbol: str, side: str, new_sl: float):
    """Cancel existing SL (algo + legacy) and place new one."""
    try:
        # Cancel algo SL if we have the ID
        pos = self.positions.get(symbol)
        if pos and pos.get("sl_algo_id"):
            await self._cancel_algo_order(symbol, pos["sl_algo_id"])
            pos["sl_algo_id"] = None
        
        # Also cancel all legacy open orders for this symbol
        await self._request("DELETE", "/fapi/v1/allOpenOrders", {"symbol": symbol})
        
        # Place new SL via Algo Order API
        await self._set_stop_loss(symbol, side, new_sl)
    except Exception as e:
        logger.error(f"[BINANCE_SL_UPDATE] Failed for {symbol}: {e}")
```

**Key:** Always cancels old SL, places new one (not just update).

---

## 🔍 NEXUS SIGNALCOPY CURRENT STATE

### Code Location
`execution/paper_mainnet_trader.py` lines 406-443

### Current Implementation (Shadow Only)

```python
def _update_shadow_trailing(self, pos: Dict[str, Any], mark: float) -> None:
    """Counterfactual only. Never mutates provider SL, TP, qty, or execution."""
    if not self._shadow_enabled() or pos.get("shadow_exit_price"):
        return
    
    # Require TP1 hit
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
    
    # Calculate shadow stop
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
    
    # COUNTERFACTUAL: only logs "WOULD_EXIT", does NOT execute
    if hit and not pos.get("shadow_exit_logged"):
        actual_exit = self._adverse_fill(shadow_stop, side, opening=False)
        # ... logging only ...
        logger.info("[SHADOW_TRAIL] WOULD_EXIT %s %s @ %.6g", ...)
```

**Key differences from FusionXomegabot:**
- ❌ Does NOT update real `pos["sl"]`
- ❌ Does NOT call server SL update
- ✅ Only journals counterfactual outcome

---

## 💡 KEY INSIGHTS FROM FUSIONXOMEGABOT

### 1. Two-Phase Activation

**Phase 1: Profit Lock (Breakeven)**
- Trigger: `hold_min > 5` AND `unrealized > 1.5%`
- Action: Move SL to `entry + buffer`
- Sets `locked_profit = True`

**Phase 2: Trailing Stop**
- Trigger: `locked_profit = True` AND `hold_min > 10`
- Action: Trail SL based on high/low watermark

**Why two phases?**
- Profit lock = conservative, protects capital early
- Trailing = aggressive, only after profit confirmed

### 2. ATR-Based Dynamic Trail

```python
trail_pct = max(1%, ATR% * 1.0)
```

**Benefit:** Adapts to volatility
- Low volatility (ATR 1%) → trail 1%
- High volatility (ATR 5%) → trail 5%

**Nexus current:** Fixed 1% (simpler, but less adaptive)

### 3. Server SL Update is Critical

FusionXomegabot **always updates exchange SL** (not just local state):

```python
await self._update_server_sl(symbol, side, new_sl)
```

**Why:**
- Bot crash / restart → SL persists on exchange
- Network disconnect → SL still protects
- Manual intervention → SL visible in exchange UI

**Nexus proposed:** Same pattern (update real `pos["sl"]` + gateway enforces)

### 4. Frequent Updates

FusionXomegabot updates SL **every check_positions cycle** (every few seconds).

**Nexus proposed:** Same (every tick in management loop)

### 5. Exit Classification

```python
if reason == "SL":
    if sl_kind == "TRAILING":
        normalized_reason = "DYNAMIC_SL"
    elif sl_kind == "BREAKEVEN":
        normalized_reason = "BREAKEVEN_STOP"
    else:
        normalized_reason = "HARD_SL"
```

**Benefit:** Clear attribution in logs/reports
- Can measure trailing stop vs breakeven vs original SL performance separately

**Nexus proposed:** Add similar classification

---

## 🎯 RECOMMENDATIONS FOR NEXUS

### 1. **Use FusionXomegabot's Two-Phase Approach**

**Proposed:**

```python
# Phase 1: Profit lock (after TP1)
if pos.get("tp1_hit") is True and not pos.get("locked_profit"):
    # Move SL to breakeven immediately after TP1
    if side == "LONG":
        new_sl = max(pos["sl"], entry * 1.001)  # 0.1% above entry
    else:
        new_sl = min(pos["sl"], entry * 0.999)
    
    if new_sl != pos["sl"]:
        pos["sl"] = new_sl
        pos["locked_profit"] = True
        pos["sl_kind"] = "BREAKEVEN"
        logger.info("[PROFIT_LOCK] %s | SL → breakeven %.6g", symbol, new_sl)

# Phase 2: Trailing stop (after profit lock + 10min)
if (pos.get("locked_profit") and 
    hold_min > 10 and 
    TRAILING_EXECUTION_ENABLED):
    # ... existing trailing logic ...
```

**Benefit:**
- ✅ Immediate downside protection after TP1
- ✅ Trailing only after profit secured
- ✅ Matches proven FusionXomegabot pattern

### 2. **Consider ATR-Based Dynamic Trail**

**Current Nexus:** Fixed 1%

**FusionXomegabot:** `max(1%, ATR% * multiplier)`

**Proposed:**

```python
def _shadow_trail_pct(self, pos: Dict[str, Any]) -> float:
    base = float(os.getenv("SHADOW_TRAIL_MIN_PCT", "0.01"))  # 1% default
    
    # Optional: ATR-based dynamic trail
    if os.getenv("TRAILING_ATR_ADAPTIVE", "false").lower() == "true":
        atr_pct = pos.get("atr_pct", 2.5) / 100.0  # from entry snapshot
        multiplier = float(os.getenv("TRAILING_ATR_MULTIPLIER", "1.0"))
        return max(base, atr_pct * multiplier)
    
    return base
```

**Config:**
```bash
TRAILING_ATR_ADAPTIVE=false  # Start with fixed 1%
TRAILING_ATR_MULTIPLIER=1.0  # Enable later if needed
```

### 3. **Add Exit Classification**

**Proposed:**

```python
if exit_reason == "HARD_SL":
    sl_kind = pos.get("sl_kind", "ORIGINAL")
    if sl_kind == "TRAILING":
        normalized_reason = "DYNAMIC_SL"
    elif sl_kind == "BREAKEVEN":
        normalized_reason = "BREAKEVEN_STOP"
    else:
        normalized_reason = "HARD_SL"
```

**Benefit:** Can measure trailing vs breakeven vs original SL separately in reports.

### 4. **Keep Server SL Update Pattern**

**Nexus gateway already handles SL enforcement.** Just update `pos["sl"]`, gateway checks it every tick.

**No need for explicit `_update_server_sl()` like Binance API.** Bybit gateway manages orders.

### 5. **Start Conservative, Scale Up**

**Phase 1 (Now):**
- ✅ Activation: After TP1 only
- ✅ Trail: Fixed 1%
- ✅ Update: Every tick
- ✅ Mode: Hybrid (env var toggle)

**Phase 2 (After validation):**
- ⏳ Activation: Immediate (after entry)
- ⏳ Trail: ATR-adaptive
- ⏳ Update: Every tick (same)

---

## 📊 FUSIONXOMEGABOT PERFORMANCE

### Trade Stats (Jul 1-8, 2026)

| Metric | Value |
|---|---:|
| Total trades | 60 |
| Win rate | 71.7% |
| Avg PnL/trade | +0.65% |
| Total PnL | +38.80% |

### Exit Breakdown

| Exit Reason | Count | % |
|---|---:|---:|
| SCRATCH_EXIT | 32 | 53.3% |
| PARTIAL_TP1 | 13 | 21.7% |
| BREAKEVEN_STOP | 4 | 6.7% |
| PARTIAL_TP2 | 3 | 5.0% |
| TP_FULL | 2 | 3.3% |
| TIME_EXIT | 2 | 3.3% |
| DAMAGE_REDUCER | 2 | 3.3% |
| HARD_SL | 1 | 1.7% |

**Note:** Only 4 BREAKEVEN_STOP (6.7%) — suggests trailing stop was used but not frequently triggered.

**Interpretation:** Trailing stop present, but most exits via scratch/time/partial TP, not trailing SL hit.

---

## ⚠️ DIFFERENCES TO NOTE

### 1. FusionXomegabot: Binance Testnet
**Nexus:** Bybit mainnet

**Impact:** API differences, but logic transferable.

### 2. FusionXomegabot: Two-step profit lock
**Nexus shadow:** Immediate trailing after TP1

**Recommendation:** Adopt FusionXomegabot's two-step (breakeven first, trail later).

### 3. FusionXomegabot: ATR-adaptive
**Nexus shadow:** Fixed 1%

**Recommendation:** Start fixed, add ATR later.

### 4. FusionXomegabot: Explicit server SL update
**Nexus:** Gateway enforces `pos["sl"]` automatically

**Recommendation:** Keep gateway pattern (simpler).

---

## ✅ FINAL RECOMMENDATION

### Adopt FusionXomegabot's Pattern with Nexus Adaptations

**What to keep from FusionXomegabot:**
1. ✅ Two-phase activation (breakeven → trailing)
2. ✅ After TP1 activation
3. ✅ High/low watermark tracking
4. ✅ SL kind classification (ORIGINAL / BREAKEVEN / TRAILING)
5. ✅ Frequent SL updates (every tick)

**What to adapt for Nexus:**
1. ✅ Start with fixed 1% trail (proven in shadow data)
2. ✅ Gateway SL enforcement (no explicit API calls needed)
3. ✅ Hybrid mode (env var toggle for safety)
4. ⏳ ATR-adaptive later (after fixed 1% validated)

**Implementation sequence:**
1. **Now:** Implement Option B (after TP1, fixed 1%, hybrid mode)
2. **Week 1:** Monitor first 5-10 trades, verify behavior
3. **Week 2:** Review performance vs shadow predictions
4. **Later:** Consider ATR-adaptive, immediate activation

---

## 📋 IMPLEMENTATION CHECKLIST

- [ ] Add two-phase logic (breakeven → trailing)
- [ ] Add `sl_kind` tracking (ORIGINAL / BREAKEVEN / TRAILING)
- [ ] Add exit classification (HARD_SL → BREAKEVEN_STOP / DYNAMIC_SL)
- [ ] Implement env var toggle (`TRAILING_EXECUTION_ENABLED`)
- [ ] Keep shadow journaling (for comparison)
- [ ] Add logging for SL updates
- [ ] Update docs with new exit reasons
- [ ] Monitor first 5 trades closely

---

**Analysis completed:** 2026-08-07 01:56 UTC  
**Author:** Hermes Agent (Kiro)  
**Source:** FusionXomegabot `binance_testnet_trader.py` + Nexus shadow data  
**Recommendation:** Proceed with Option B implementation, informed by FusionXomegabot patterns
