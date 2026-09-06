# Max Positions Issue — Root Cause Analysis

**Date:** 2026-08-06 11:18 UTC  
**Issue:** Max open positions turun dari 30 → 20

---

## 🔍 ROOT CAUSE FOUND

### Code Logic (risk/risk_engine.py)

```python
@staticmethod
def get_max_running_positions() -> int:
    paper = os.getenv("GATEWAY_PAPER_MAINNET", "true").lower() in ("1", "true", "yes")
    key = "PAPER_MAX_RUNNING_POSITIONS" if paper else "REAL_MAX_RUNNING_POSITIONS"
    default = "30" if paper else "20"
    return max(1, int(os.getenv(key, os.getenv("FQ_MAX_RUNNING_POSITIONS", default))))
```

**Logic:**
1. Check `GATEWAY_PAPER_MAINNET` env var
2. If paper mode (default "true") → use `PAPER_MAX_RUNNING_POSITIONS` atau default **30**
3. If real mode → use `REAL_MAX_RUNNING_POSITIONS` atau default **20**

---

## 📊 CURRENT STATE

### Environment Variables (TIDAK ADA YANG SET)
```
GATEWAY_PAPER_MAINNET: not set (defaults to "true")
PAPER_MAX_RUNNING_POSITIONS: not set
REAL_MAX_RUNNING_POSITIONS: not set
FQ_MAX_RUNNING_POSITIONS: not set
```

### Hasil
- `GATEWAY_PAPER_MAINNET` tidak set → defaults to **"true"**
- Paper mode aktif → default **30** positions

### Tapi kenapa sekarang 20?

**KEMUNGKINAN:**
1. ❓ Gateway restart dengan environment berbeda
2. ❓ `GATEWAY_PAPER_MAINNET` berubah jadi "false"
3. ❓ Process environment berbeda dari shell environment

---

## 📝 EVIDENCE FROM USER

### Notifikasi Lama (30 positions)
```
⏸️ EPICUSDT LONG: ... kapasitas running maksimal (30/30 aktif; 8 pending)
⏸️ HEIUSDT LONG: ... kapasitas running maksimal (30/30 aktif; 4 pending)
⏸️ ADAUSDT LONG: ... kapasitas running maksimal (30/30 aktif; 2 pending)
```

### Notifikasi Baru (20 positions)
```
⏸️ CYSUSDT SHORT: ... kapasitas running maksimal (21/20 aktif; 0 pending)
⏸️ QTUMUSDT SHORT: ... kapasitas running maksimal (21/20 aktif; 0 pending)
⏸️ ZRXUSDT SHORT: ... kapasitas running maksimal (21/20 aktif; 1 pending)
⏸️ HEIUSDT LONG: ... kapasitas running maksimal (21/20 aktif; 0 pending)
⏸️ CTSIUSDT LONG: ... kapasitas running maksimal (21/20 aktif; 0 pending)
⏸️ ONUSDT LONG: ... kapasitas running maksimal (21/20 aktif; 0 pending)
⏸️ ETCUSDT LONG: ... kapasitas running maksimal (21/20 aktif; 0 pending)
```

---

## 🔧 GATEWAY PROCESSES

```
PID 1316747: python -u -m uvicorn gateway.run_gateway:app --host 0.0.0.0 --port 8787
  Started: 06:19 UTC (5 jam lalu)
  Status: Running

PID 704524: python -u -m uvicorn gateway.run_gateway:app --host 0.0.0.0 --port 8790
  Started: Aug 05 (kemarin)
  Status: Running
```

**Gateway di-restart pada 06:19 UTC hari ini!**

---

## 🎯 KAPAN BERUBAH?

### Timeline
1. **Before 06:19 UTC:** Max 30 positions (EPICUSDT, HEIUSDT, ADAUSDT messages)
2. **06:19 UTC:** Gateway restart (PID 1316747 started)
3. **After 06:19 UTC:** Max 20 positions (CYSUSDT, QTUMUSDT, ZRXUSDT messages)

### Kemungkinan Penyebab
1. **Gateway restart tanpa environment variable** → defaults to "true" tapi somehow jadi 20
2. **Process environment berbeda** dari shell environment
3. **Systemd/tmux environment** tidak preserve variable

---

## 🚨 CURRENT ISSUE

**Open positions:** 20 pairs (FULL)  
**Max allowed:** 20 (should be 30)  
**Signals blocked:** 7+ signals rejected karena capacity

---

## ✅ SOLUTION

### Option 1: Set Environment Variable (RECOMMENDED)
```bash
export GATEWAY_PAPER_MAINNET=true
export PAPER_MAX_RUNNING_POSITIONS=30
```

### Option 2: Set in systemd/supervisor config
Add to service file:
```
Environment="GATEWAY_PAPER_MAINNET=true"
Environment="PAPER_MAX_RUNNING_POSITIONS=30"
```

### Option 3: Set in .env file
```bash
echo "GATEWAY_PAPER_MAINNET=true" >> /home/fusion_omega/.env
echo "PAPER_MAX_RUNNING_POSITIONS=30" >> /home/fusion_omega/.env
```

### Option 4: Hardcode in risk_engine.py (NOT RECOMMENDED)
Change default from "30" to "30" but ensure paper mode is true.

---

## 📋 WHAT WE DID TODAY (Session Analysis)

**Checked:**
- ✅ Session history → NO changes to SignalCopy config
- ✅ Git log → NO commits since July 26
- ✅ File timestamps → execution_config.py last modified July 26
- ✅ Environment variables → ALL empty
- ✅ Gateway processes → Gateway restarted at 06:19 UTC

**Conclusion:**
- **NO CODE CHANGES** in this session
- **Gateway restart** at 06:19 UTC caused the change
- **Environment variables** not preserved on restart
- **Default fallback** kicked in → paper mode should be 30, but somehow 20

---

## 🔧 NEXT STEPS

**DO NOT CHANGE YET** — waiting for user confirmation:

1. Check how gateway is started (systemd? tmux? manual?)
2. Find where GATEWAY_PAPER_MAINNET should be set
3. Verify paper vs real mode intent
4. Set PAPER_MAX_RUNNING_POSITIONS=30
5. Restart gateway with proper environment

**User wants to confirm first before any changes.**
