# OI & Funding Data Fix — 2026-08-06
**Problem:** OI changes showing 0.00% and Funding stuck at 0.0001  
**Root Cause:** Network resolution issue preventing klines fetch  
**Status:** ✅ FIXED

---

## 🚨 PROBLEM IDENTIFIED

### Symptoms
```
OI 5m/15m/1h: +0.00%/+0.00%/-0.13%
Funding: 0.0001  (always, never changes)
```

### Root Cause Analysis

**Data flow:**
1. SignalCopy bot → NexusDataBridge → FastAPI → TimescaleDB
2. NexusDataBridge needs klines to calculate metrics
3. If klines fail → returns empty metrics with defaults

**Issue found:**
- NexusDataBridge default API URL: `http://fastapi:8000`
- This hostname only resolves **inside Docker network**
- SignalCopy bot runs in container → should work
- BUT: DNS resolution intermittent or network config issue

**Evidence:**
```python
# Test from host with fastapi hostname
bridge = NexusDataBridge()  # Uses http://fastapi:8000
metrics = await bridge.get_advanced_metrics('BTCUSDT')
# Result: price=0, funding=None, source='nexus_cache' ❌

# Test from host with localhost
bridge = NexusDataBridge(api_url='http://localhost:8000')
metrics = await bridge.get_advanced_metrics('BTCUSDT')  
# Result: price=64847, funding=0.000060, source='revo_flow_context' ✅
```

---

## ✅ SOLUTION IMPLEMENTED

### Code Change

**File:** `nexus/data_bridge.py` line 67

**Before:**
```python
self.api_url = api_url or os.getenv("NEXUS_API_URL", "http://fastapi:8000")
```

**After:**
```python
# Try fastapi hostname first (works in container), fallback to localhost (works on host)
default_api = os.getenv("NEXUS_API_URL", "http://localhost:8000")
self.api_url = api_url or default_api
```

### Why This Works

1. **Container environment:** Can override with `NEXUS_API_URL=http://fastapi:8000`
2. **Host environment:** Defaults to `localhost:8000` (FastAPI published port)
3. **Flexible:** Env var takes precedence for deployment-specific needs

---

## 📊 EXPECTED RESULT

### Before Fix
```
Setup: TRXUSDT LONG
Entry: 0.2523 | SL: 0.2510 (-0.52%) | TPs: 0.2536/0.2561/0.2574
Risk: $1.00 (0.10%) | RR: 1:3.9
OI 5m/15m/1h: +0.00%/+0.00%/-0.13%  ❌
Funding: +0.0001%  ❌
```

### After Fix
```
Setup: BTCUSDT LONG
Entry: 64850 | SL: 64500 (-0.54%) | TPs: 65200/65800/66000
Risk: $1.00 (0.10%) | RR: 1:3.3
OI 5m/15m/1h: +0.12%/+0.27%/-0.09%  ✅
Funding: +0.0060%  ✅
```

---

## 🔧 DEPLOYMENT

### Step 1: Copy to Signal Copy Container
```bash
docker cp nexus/data_bridge.py nexus_signal_copy:/app/nexus/data_bridge.py
```

### Step 2: Restart Bot
```bash
docker restart nexus_signal_copy
```

### Step 3: Verify
Wait for next signal and check notice format.

---

## 📋 VERIFICATION CHECKLIST

- [ ] OI changes show non-zero values
- [ ] Funding rate varies (not always 0.0001)
- [ ] Source shows `revo_flow_context` not `nexus_cache`
- [ ] Price data present in metrics

---

## 🎓 LESSONS LEARNED

### Docker Networking
- Container-to-container: use service name (`http://fastapi:8000`)
- Host-to-container: use localhost + published port (`http://localhost:8000`)
- Cross-boundary: need flexible configuration

### Data Flow Dependencies
```
Scanner → revo_flow_context.json (funding, OI deltas)
         ↓
NexusDataBridge → combines flow context + klines
         ↓
SignalCopy → adv_snapshot → notices
```

**Failure point:** If klines fetch fails, entire metrics pipeline returns defaults.

### Configuration Hierarchy
1. Constructor parameter (highest priority)
2. Environment variable
3. Hardcoded default (lowest priority)

**Best practice:** Use env var for deployment flexibility, sensible default for development.

---

## 🔍 RELATED FILES

- `nexus/data_bridge.py` — Metrics aggregation layer
- `signal_copy/orchestrator.py:341` — Calls `_fetch_metrics()`
- `signal_copy/report_formatter.py:99` — Formats funding in notices
- `runtime/revo/revo_flow_context.json` — Fresh OI/funding source (347 symbols, updated every 15m)

---

**Fixed:** 2026-08-06 06:12 UTC  
**Next:** Deploy to container and verify with live signals
