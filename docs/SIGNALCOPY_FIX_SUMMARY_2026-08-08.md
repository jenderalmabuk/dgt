# SIGNALCOPY FIX SUMMARY — 2026-08-08

**Time:** 2026-08-08 08:30 - 11:20 UTC (2h 50m)

---

## 🚨 BUGS FIXED

### 1. ✅ Stale Price Data — Bybit Fallback Missing

**Reported:** TIAUSDT SHORT rejected `SL_ALREADY_PASSED` padahal market valid

**Root cause:**
- Binance cache 32h stale (TIAUSDT $0.3349 vs real $0.3271)
- Original fix: fallback Binance REST API
- Problem: GWEIUSDT, BLUAIUSDT not on Binance → REST return 400 → fallback gagal → pakai stale cache

**Impact:**
- GWEIUSDT: parser $0.0177 vs real $0.0263 = -33% error → false `SL_ALREADY_PASSED`
- BLUAIUSDT: parser $0.0124 vs real $0.0200 = -38% error → false `SL_ALREADY_PASSED`

**Fix deployed:** `nexus/data_bridge.py` line 424-456
```python
if age_sec > 1800:  # >30min stale
    # Try Binance REST first
    price = await fetch_binance_rest(symbol)
    
    # NEW: Fallback to Bybit if Binance fails
    if price is None:
        df_bybit = await self._fetch_klines_from_api(symbol, "5m", limit=1)
        price = float(df_bybit['close'].iloc[-1])
    
    # Last resort: stale cache
    if price is None:
        price = float(df_5m['close'].iloc[-1])
```

**Container:** `nexus_signal_copy` rebuilt 08:40 UTC

**Verification:**
```
GWEIUSDT   binance 2905m ❌  bybit 10m ✅
BLUAIUSDT  binance 2945m ❌  bybit 5m ✅
IOTAUSDT   binance 2945m ❌  bybit 10m ✅
ALGOUSDT   binance 1450m ❌  bybit 10m ✅
TIAUSDT    binance 2905m ❌  bybit 10m ✅
```

**Status:** ✅ Fixed — Bybit fresh data now used for symbols not on Binance

---

### 2. ✅ MTF Alignment Dict AttributeError

**Reported:** ONUSDT LONG rejected with `'dict' object has no attribute 'upper'`

**Root cause:**
- `execution/paper_mainnet_trader.py` line 526
- Code: `mtf = snapshot.get("mtf_alignment", "").upper()`
- `mtf_alignment` is **dict** (e.g. `{"label": "BULLISH"}`), not string
- `.upper()` called on dict → AttributeError

**Impact:**
- Signal VALID → executor crash → reject dengan error message tidak jelas
- Smart exemption logic broken (cannot check MTF alignment)

**Fix deployed:** `execution/paper_mainnet_trader.py` line 523-530
```python
mtf_raw = snapshot.get("mtf_alignment", "")
# Handle dict or string: extract label if dict, use as-is if string
mtf = mtf_raw.get("label", "") if isinstance(mtf_raw, dict) else str(mtf_raw)
mtf = mtf.upper() if mtf else ""
```

**Container:** `nexus_gateway` rebuilt 11:07 UTC

**Status:** ✅ Fixed — MTF dict safely extracted

---

### 3. ✅ Trailing SL = 0.0000

**Reported:** TRXUSDT, ADAUSDT: TP1 hit, `sl_kind=TRAILING`, but `stop_loss=0.0000`

**Root cause:**
- Trailing manager updated `pos["sl"]` but NOT `pos["stop_loss"]` / `pos["sl_price"]`
- Journal `open_positions.json` not synced → `stop_loss=0.0` → no stop protection

**Impact:**
- Unlimited downside risk after TP1
- Trailing active but journal shows SL=0

**Fix deployed:** `execution/paper_mainnet_trader.py` line 451, 510
```python
# Before:
pos["sl"] = new_sl
pos["sl_kind"] = "TRAILING"

# After:
pos["sl"] = new_sl
pos["stop_loss"] = new_sl  # Sync journal field
pos["sl_price"] = new_sl   # Sync management field
pos["sl_kind"] = "TRAILING"
```

**Container:** `nexus_gateway` rebuilt 11:17 UTC

**Verification:**
- ZECUSDT trailing update 11:17, 11:19 UTC → synced correctly ✅
- Code fix live, will apply on next price movement for TRXUSDT/ADAUSDT

**Status:** ✅ Fixed — Next trailing update will sync all 3 fields

---

### 4. ✅ Cronjob Script Path Error

**Reported:** `SignalCopy bad signal rejection monitor` failed: script not found

**Root cause:**
- Script path: `.hermes/profiles/signal-copy/scripts/signalcopy_rejection_monitor.sh`
- Actual location: `fusion_omega_nexus/scripts/signalcopy_rejection_monitor.sh`
- Workdir correct, path relative wrong

**Fix deployed:**
```bash
cronjob update job_id=04c37eb8db06 script=scripts/signalcopy_rejection_monitor.sh
```

**Status:** ✅ Fixed — cron path corrected

---

## 🐛 BUGS IDENTIFIED (Not Fixed Yet)

### 5. ❌ Shadow Eligible = 0

**Symptoms:**
- 99 shadow entries, 0 eligible
- Before (Aug 7 10:02): 94 entries, 23 eligible
- After (Aug 8 08:40): 99 entries, 0 eligible

**Impact:**
- Shadow analysis invalid
- Cannot measure trailing effectiveness
- Field regression after container rebuild

**Root cause:**
- `eligible` field logic broken or TP1 detection not working
- Possible: `tp1_hit` field not set correctly in shadow journal write

**Status:** ⚠️ **NOT FIXED** — needs shadow journal logic audit

---

## 📊 PERFORMANCE BASELINE (All-Time)

| Metric | Value |
|---|---:|
| Total trades | 135 |
| Win Rate | 40.7% (55W / 80L) |
| Total PnL | **-$332.09** ❌ |
| Avg/trade | -$2.46 |
| Hard SL exits | 66 (48.9%) ❌ |

**Problem:** 49% hard SL → signal quality atau entry timing buruk

---

## 📋 CURRENT STATE

### Containers

| Container | Status | Age |
|---|---|---|
| `nexus_signal_copy` | ✅ Up | 2h 40m (rebuilt 08:40) |
| `nexus_gateway` | ✅ Up | 3m (rebuilt 11:17) |
| `nexus_scanner` | ✅ Up | 25h |
| `nexus_binance_collector` | ✅ Up | 26h (102 symbols) |
| `nexus_bybit_collector` | ✅ Up | 2d (691 symbols) |

### Open Positions

| Item | Count |
|---|---:|
| Total open | 15 |
| TP1 hit | 11 (73%) |
| Trailing SL active | 3 (TRXUSDT, ADAUSDT, ZECUSDT) |
| Age >72h | 6 (oldest: LINKUSDT 155.8h) |

### Data Health

- Fresh pairlist: 632 pairs (stale/missing excluded)
- Binance collector: 102 symbols, ~44s cycle, 0 rate-limit
- Bybit collector: 691 symbols, ~60s cycle, 0 rate-limit
- Problem symbols now fresh via Bybit: ✅

---

## 🎯 NEXT ACTIONS

### Priority 1: Fix Shadow Eligible Field (Pending)
- Audit `shadow_trailing.jsonl` write logic
- Ensure `eligible=True` when `tp1_hit=True`

### Priority 2: Review Signal Quality (Pending)
- 49% hard SL too high
- Analyze validation score distribution: hard_sl vs winners
- Consider raising threshold or tighter entry filters

### Priority 3: Close Stale Winners (Pending)
- 6 positions >72h post-TP1
- Lower `MAX_AGE_POST_TP1` or force close

---

## 📝 FILES MODIFIED

1. `nexus/data_bridge.py` — Bybit fallback logic (line 424-456)
2. `execution/paper_mainnet_trader.py` — MTF dict handling (line 523-530)
3. `execution/paper_mainnet_trader.py` — Trailing SL sync (line 451, 510)
4. Cronjob `04c37eb8db06` — script path corrected

---

## ✅ VERIFICATION CHECKLIST

- [x] Bybit fallback deployed
- [x] MTF dict bug fixed
- [x] Trailing SL bug fixed
- [x] Cronjob path fixed
- [x] Containers rebuilt and running
- [x] Data freshness verified (Bybit fresh for problem symbols)
- [x] Trailing update verified (ZECUSDT synced correctly)
- [ ] Shadow eligible field (pending)
- [ ] Signal quality review (pending)

---

## 📊 BUGS FIXED SUMMARY

| # | Bug | Impact | Status | Time |
|---|---|---|---|---|
| 1️⃣ | Stale price Bybit fallback | False reject GWEIUSDT, BLUAIUSDT | ✅ Fixed | 08:40 |
| 2️⃣ | MTF dict AttributeError | ONUSDT crash | ✅ Fixed | 11:07 |
| 3️⃣ | Trailing SL = 0 | No stop protection | ✅ Fixed | 11:17 |
| 4️⃣ | Cronjob path | Monitor fail | ✅ Fixed | 08:50 |

**Total: 4 bugs fixed in 2h 50m** 🚀

Signal baru sekarang akan:
- ✅ Pakai Bybit fresh kalau Binance stale/unavailable
- ✅ Tidak crash saat MTF alignment dict
- ✅ Sync trailing SL ke journal (stop_loss, sl_price)
- ✅ Execute tanpa `'dict' object no attribute 'upper'` error

**Ready untuk production.** 🎯

