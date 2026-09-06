# SignalCopy Bot — 2 Instance Comparison

**Date:** 2026-08-06 15:34 UTC  
**Discovery:** 2 SignalCopy bots running simultaneously  
**Status:** ⚠️ NEED TO CHOOSE ONE

---

## 📊 BOT #1 — MANUAL PYTHON PROCESS

### Basic Info
```
Process ID: 1338421
Started: 2026-08-06 07:07:40 UTC
Uptime: 8.4 hours (30,375 seconds)
Command: python3 -u signal_copy/run.py
Location: /home/fusion_omega/fusion_omega_nexus
Type: MANUAL PYTHON (host machine, not container)
```

### Characteristics
```
✅ Running directly on host machine
✅ Direct access to file system
✅ Not isolated (shares environment with host)
✅ Log to stdout (background process)
✅ Started: 07:07 UTC (morning)
```

### How It Was Started
```bash
# Started manually with background process:
cd /home/fusion_omega/fusion_omega_nexus
PYTHONPATH=/home/fusion_omega/fusion_omega_nexus python3 -u signal_copy/run.py 2>&1 &

# Or via Hermes background process:
proc_fa252ca14ad1
```

### When To Use
- Development/testing
- Debug directly
- Quick iteration
- Manual control
- Fast deployment without docker

### Code Version
```
File: signal_copy/validation_engine.py
Modified: 2026-07-24 22:15:04 (13 days ago)
Status: OLD CODE (before smart exemptions)
```

---

## 📊 BOT #2 — DOCKER CONTAINER

### Basic Info
```
Container: nexus_signal_copy
Container ID: f40e094a69a7
Started: 2026-08-06 14:44:56 UTC
Uptime: 49 minutes
Command: docker compose up signal_copy
Location: Inside container
Type: DOCKER CONTAINER (isolated environment)
```

### Characteristics
```
✅ Isolated environment (container)
✅ Reproducible (Dockerfile definition)
✅ Easy management (docker ps/logs/restart)
✅ Production-ready
✅ Defined in docker-compose.yml
✅ Started: 14:44 UTC (afternoon)
```

### How It Was Started
```bash
# Started by Hermes Agent:
cd /home/fusion_omega/fusion_omega_nexus/docker
docker compose up signal_copy

# Process:
proc_aaf0a6615bc1 (PID 1516025)
```

### When To Use
- Production deployment
- Consistent environment
- Multiple services coordination
- Standard operation
- Scalable architecture

### Code Version
```
File: Container has COPY of code at build time
Modified: Unknown (depends on when image was built)
Status: Unknown (need to check)
```

---

## 🔍 SMART EXEMPTIONS STATUS

### Deployment Report
```
File: docs/SMART_EXEMPTIONS_DEPLOYED.md
Created: 2026-08-06 05:42 UTC
Status: ✅ LIVE in Production (claimed)
Code location: execution/paper_mainnet_trader.py
```

### Code Files Modified
```
File: execution/paper_mainnet_trader.py
Modified: Need to check timestamp
Expected changes:
  - Lines 453-469
  - Function: _classify_bad_signal()
  - Smart exemptions logic:
    • Large move (>10%)
    • Volume spike (>1.5σ)
    • MTF aligned
```

### validation_engine.py
```
File: signal_copy/validation_engine.py
Modified: 2026-07-24 22:15:04 (13 days ago)
Status: No smart exemptions code found
Note: Smart exemptions in paper_mainnet_trader.py, not validation_engine.py
```

---

## ❓ WHICH BOT HAS SMART EXEMPTIONS?

### Timeline Analysis

```
Smart Exemptions Deployment: 2026-08-06 05:40 UTC

Bot #1 (Manual):
  Started: 07:07 UTC (1h 27min AFTER deployment)
  Code: validation_engine.py modified Jul 24 (old)
  Execution: paper_mainnet_trader.py (need to check timestamp)
  Status: UNKNOWN — need to verify

Bot #2 (Docker):
  Started: 14:44 UTC (9h AFTER deployment)
  Code: Container image (depends on when built)
  Status: UNKNOWN — need to verify
```

### To Determine Which Has Smart Exemptions

**Check #1: paper_mainnet_trader.py timestamp**
```bash
stat -c '%Y' execution/paper_mainnet_trader.py
# If modified AFTER 05:40 UTC → has smart exemptions
# If modified BEFORE 05:40 UTC → old code
```

**Check #2: Search for smart exemption code**
```bash
grep -n "large_move.*exemption\|Volume spike" execution/paper_mainnet_trader.py
# If found → has smart exemptions
# If not found → old code
```

**Check #3: Docker image build time**
```bash
docker inspect nexus_signal_copy --format '{{.Created}}'
# If built AFTER 05:40 UTC → might have smart exemptions
# If built BEFORE 05:40 UTC → old code
```

---

## ⚠️ CURRENT PROBLEM

### Duplicate Execution Risk

```
2 bots monitoring 29 channels simultaneously
   ↓
Signal BTCUSDT LONG arrives
   ↓
Bot #1 parses → executes (position #1)
Bot #2 parses → executes (position #2)
   ↓
DUPLICATE ORDER! 2x exposure! ❌
```

### Evidence
```
Both bots processing messages:
  Bot #1: Last activity 15:33 UTC
  Bot #2: Last activity 15:26 UTC
  
Both accessing same:
  - Telegram channels (29 channels)
  - Gateway (port 8787)
  - Journal files (open_positions.json)
```

---

## 🎯 RECOMMENDATION FLOWCHART

```
┌─────────────────────────────────────────────┐
│ Which bot has SMART EXEMPTIONS?             │
└────────────┬────────────────────────────────┘
             │
    ┌────────┴────────┐
    │                 │
    ▼                 ▼
BOT #1          NEITHER/BOTH
(Manual)              │
    │                 │
    ▼                 ▼
KEEP #1         CHECK CODE
KILL #2         TIMESTAMPS
    │                 │
    ▼                 ▼
DONE            REBUILD
                DOCKER IMAGE
                    │
                    ▼
                KEEP #2
                KILL #1
```

---

## 🔍 IMMEDIATE ACTIONS NEEDED

### Step 1: Check Code Timestamps
```bash
# Check if paper_mainnet_trader.py has smart exemptions
ls -lh execution/paper_mainnet_trader.py
grep -C5 "large_move" execution/paper_mainnet_trader.py
```

### Step 2: Check Which Bot Uses Latest Code
```bash
# Bot #1 uses host code directly
ls -lh /home/fusion_omega/fusion_omega_nexus/execution/

# Bot #2 uses container code (copied at build time)
docker exec nexus_signal_copy ls -lh execution/
```

### Step 3: Verify Smart Exemptions Active
```bash
# Check logs for "exemption" keyword
docker logs nexus_signal_copy 2>&1 | grep -i exemption
# or
tail -100 /proc/1338421/fd/1 | grep -i exemption
```

### Step 4: Decision
```
IF Bot #1 has smart exemptions:
  → KEEP Bot #1 (manual)
  → KILL Bot #2 (docker)
  → Update docker image later

ELSE IF Bot #2 has smart exemptions:
  → KEEP Bot #2 (docker)
  → KILL Bot #1 (manual)

ELSE IF NEITHER has smart exemptions:
  → Deploy smart exemptions to host code
  → RESTART Bot #1 (manual)
  → KILL Bot #2 (docker)

ELSE IF BOTH have smart exemptions:
  → Choose based on stability:
    - Bot #1: 8+ hours uptime ✅
    - Bot #2: 49 min uptime
  → KEEP Bot #1, KILL Bot #2
```

---

## 📋 NEXT STEPS

1. **Verify code versions** (which has smart exemptions)
2. **Choose one bot** (kill the other)
3. **Verify no duplicates** (check journal for duplicate orders)
4. **Update watchdog** (monitor correct bot)
5. **Document decision** (why chosen)

---

**Status:** ⏳ WAITING FOR CODE VERIFICATION  
**Risk:** ⚠️ HIGH (duplicate execution possible)  
**Action:** 🚨 URGENT — verify and kill one bot
