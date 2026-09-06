# Max Positions Fix — Summary Report

**Date:** 2026-08-06 13:57 UTC  
**Issue:** Max open positions turun dari 30 → 20  
**Status:** ✅ **FIXED**

---

## 🎯 ROOT CAUSE

**Conflicting configuration values:**

1. **`.env` (host):** `MAX_OPEN_POS_GLOBAL=20` ❌
2. **`docker/.env`:** `MAX_OPEN_POS_GLOBAL=20` ❌ (was 30 before)
3. **`docker-compose.yml`:** `MAX_OPEN_POS_GLOBAL: ${MAX_OPEN_POS_GLOBAL:-20}` (default 20)

**What happened:**
- Gateway restart at 06:19 UTC loaded environment with `MAX_OPEN_POS_GLOBAL=20`
- SignalCopy orchestrator (line 704) checks `MAX_OPEN_POS_GLOBAL` first
- Result: bot blocked at 20 positions instead of 30

**Timeline:**
- **Before 06:19 UTC:** 30/30 positions working
- **06:19 UTC:** Gateway restart (docker container)
- **After 06:19 UTC:** 21/20 rejected signals

---

## ✅ FIX APPLIED

### Files Changed

**1. `/home/fusion_omega/fusion_omega_nexus/.env` line 72:**
```diff
-MAX_OPEN_POS_GLOBAL=20
+MAX_OPEN_POS_GLOBAL=30
```

**2. `/home/fusion_omega/fusion_omega_nexus/docker/.env`:**
```diff
-MAX_OPEN_POS_GLOBAL=20
+MAX_OPEN_POS_GLOBAL=30
```

**3. Gateway restarted:**
```bash
cd /home/fusion_omega/fusion_omega_nexus/docker
export MAX_OPEN_POS_GLOBAL=30
docker compose up -d gateway
```

---

## 📊 VERIFICATION

**Container environment (AFTER FIX):**
```
MAX_OPEN_POS_GLOBAL=30               ✅
PAPER_MAX_RUNNING_POSITIONS=30       ✅
GATEWAY_PAPER_MAINNET=true           ✅
```

**Logic paths:**

1. **SignalCopy orchestrator** (signal_copy/orchestrator.py line 704):
   ```python
   cap = int(os.getenv("SIGNAL_COPY_MAX_RUNNING_POSITIONS", 
                       str(MAX_OPEN_POS_GLOBAL)))
   # Result: 30 ✅
   ```

2. **RiskManager** (risk/risk_engine.py):
   ```python
   paper = os.getenv("GATEWAY_PAPER_MAINNET", "true")  # "true"
   key = "PAPER_MAX_RUNNING_POSITIONS"                 # paper mode
   result = int(os.getenv(key, default))               # 30 ✅
   ```

**Both paths now return 30 positions.**

---

## 🔍 WHY IT CHANGED

**Session analysis:**
- ✅ This session (2026-08-06) rebuilt Nexus scanner (347→691 pairs)
- ✅ NO changes to SignalCopy config in this session
- ✅ NO manual edits to `.env` in this session
- ❌ Gateway restarted at 06:19 UTC (container restart)

**Likely cause:**
- Previous session or manual change set `MAX_OPEN_POS_GLOBAL=20`
- Gateway restart at 06:19 loaded the wrong value
- User expected 30 (from earlier working state)

**Why session search failed:**
- Change happened in a different session
- Or manual edit outside of Hermes
- Session history doesn't go back far enough

---

## 🚀 NEXT STEPS

**Immediate:**
- ✅ Gateway running with `MAX_OPEN_POS_GLOBAL=30`
- ✅ Bot will now accept up to 30 concurrent positions
- ⏳ Wait for next signal to verify acceptance

**Monitoring:**
- Watch for bot notices like:
  - ✅ `BTCUSDT LONG: executed (25/30 aktif)` — OK!
  - ❌ `BTCUSDT LONG: kapasitas running maksimal (31/30)` — still wrong

**Prevention:**
- Keep `.env` and `docker/.env` in sync
- Document any manual changes to max positions
- Consider hardcoding 30 in docker-compose.yml default

---

## 📋 FINAL STATUS

**Max open positions:** 30 ✅  
**Gateway:** Running (nexus_gateway container)  
**Bot:** SignalCopy ready  
**Issue:** RESOLVED  

**Next signal akan diterima hingga 30 concurrent positions!**
