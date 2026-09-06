# SignalCopy Bot — Architecture & Cara Kerja

**Date:** 2026-08-06 15:04 UTC  
**Author:** Hermes Agent Analysis  
**Purpose:** Penjelasan lengkap struktur, komponen, dan flow eksekusi

---

## 📊 ARSITEKTUR OVERVIEW

```
┌─────────────────────────────────────────────────────────────────┐
│                      SIGNALCOPY ECOSYSTEM                       │
└─────────────────────────────────────────────────────────────────┘

┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
│  TELEGRAM        │    │  NEXUS SCANNER   │    │  BYBIT EXCHANGE  │
│  Signal Channels │───▶│  (Data Source)   │◀───│  (Live Data)     │
│  29 channels     │    │  691 pairs       │    │  Market API      │
└──────────────────┘    └──────────────────┘    └──────────────────┘
         │                       │                        │
         │                       ▼                        │
         │              ┌──────────────────┐              │
         │              │  DATA BRIDGE     │              │
         │              │  nexus/          │              │
         │              │  (Cache Layer)   │              │
         │              └──────────────────┘              │
         │                       │                        │
         ▼                       ▼                        │
┌──────────────────────────────────────────────┐         │
│         SIGNALCOPY CONTAINER                 │         │
│         nexus_signal_copy                    │         │
│                                              │         │
│  ┌────────────────────────────────────────┐ │         │
│  │  1. TELEGRAM LISTENER                  │ │         │
│  │     signal_copy/listeners/             │ │         │
│  │     - Parse incoming messages          │ │         │
│  │     - Extract LONG/SHORT signals       │ │         │
│  └────────────────────────────────────────┘ │         │
│                    │                         │         │
│                    ▼                         │         │
│  ┌────────────────────────────────────────┐ │         │
│  │  2. ORCHESTRATOR                       │ │         │
│  │     signal_copy/orchestrator.py        │ │         │
│  │     - Coordinate all components        │ │         │
│  │     - Route signals through pipeline   │ │         │
│  └────────────────────────────────────────┘ │         │
│                    │                         │         │
│         ┌──────────┼──────────┐              │         │
│         │          │          │              │         │
│         ▼          ▼          ▼              │         │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐        │         │
│  │ PARSER  │ │VALIDATOR│ │ADVERSARY│        │         │
│  │         │ │         │ │         │        │         │
│  └─────────┘ └─────────┘ └─────────┘        │         │
│         │          │          │              │         │
│         └──────────┼──────────┘              │         │
│                    ▼                         │         │
│  ┌────────────────────────────────────────┐ │         │
│  │  3. EXECUTOR                           │ │         │
│  │     signal_copy/executor.py            │ │         │
│  │     - Prepare trade parameters         │ │         │
│  │     - Send to Gateway via HTTP         │ │         │
│  └────────────────────────────────────────┘ │         │
│                    │                         │         │
└────────────────────┼─────────────────────────┘         │
                     │                                   │
                     ▼                                   │
        ┌──────────────────────────────┐                │
        │   GATEWAY CONTAINER          │                │
        │   nexus_gateway              │                │
        │                              │                │
        │   Port: 8787                 │                │
        │   API: FastAPI/Uvicorn       │                │
        │                              │                │
        │   Routes:                    │                │
        │   - POST /execute_order      │───────────────▶│
        │   - GET /portfolio           │                │
        │   - GET /open_positions      │                │
        └──────────────────────────────┘                │
                                                         │
                                        ┌────────────────▼──┐
                                        │  BYBIT FUTURES    │
                                        │  (Real Execution) │
                                        └───────────────────┘
```

---

## 🔧 KOMPONEN UTAMA

### 1. nexus_signal_copy (CONTAINER)

**Fungsi:** Parse, validate, dan execute signals  
**Status:** ✅ RUNNING (baru restart 14:45 UTC)  
**Image:** docker-signal_copy  
**Entry:** `python -u signal_copy/run.py`

**Sub-komponen:**

#### A. Telegram Listener
```
File: signal_copy/listeners/telegram_listener.py
Fungsi:
  - Connect ke Telegram API (Telethon)
  - Monitor 29 channels
  - Parse text messages
  - Extract LONG/SHORT/CLOSE signals
  - Forward ke Orchestrator

Status: RUNNING (29 channels monitored)
```

#### B. Orchestrator
```
File: signal_copy/orchestrator.py (55KB, 1,400+ lines)
Fungsi:
  - Main coordinator
  - Route signals through pipeline:
    1. Parser (extract symbol, side, entry, SL, TP)
    2. Validator (check confluence, data quality)
    3. Adversarial (bull vs bear debate)
    4. Executor (send to gateway)
  - Manage pending orders
  - Track outcomes
  - Update journal

Status: RUNNING
```

#### C. Validator
```
File: signal_copy/validation_engine.py
Fungsi:
  - Check signal quality
  - Verify:
    • Price freshness (entry zone valid?)
    • Geometry (SL distance, RR ratio)
    • OI (open interest rising?)
    • CVD (order flow aligned?)
    • Funding (not crowded?)
    • RSI (momentum not exhausted?)
    • Trend (structure aligned?)
  - Output: VALID / WEAK / REJECT

Config: signal_copy/validation_config.py
```

#### D. Executor
```
File: signal_copy/executor.py
Fungsi:
  - Prepare trade params (symbol, side, qty, SL, TP)
  - Calculate position size
  - Send HTTP POST to Gateway
  - Wait for response
  - Log outcome

Gateway URL: http://nexus_gateway:8787/execute_order
```

---

### 2. nexus_gateway (CONTAINER)

**Fungsi:** Execution Gateway — route trades ke exchange  
**Status:** ✅ RUNNING (port 8787)  
**Image:** nexus-bot:latest  
**Entry:** `uvicorn gateway.run_gateway:app --host 0.0.0.0 --port 8787`

**API Endpoints:**

```python
POST /execute_order
  Body: {
    "symbol": "BTCUSDT",
    "side": "LONG",
    "entry_price": 45000,
    "stop_loss": 44500,
    "take_profits": [45500, 46000, 46500],
    "quantity": 0.01
  }
  Returns: {
    "status": "success",
    "order_id": "...",
    "fills": [...]
  }

GET /portfolio
  Returns: {
    "balance": 10000,
    "equity": 10500,
    "open_position_count": 20,
    "max_positions": 30
  }

GET /open_positions
  Returns: [
    {"symbol": "BTCUSDT", "side": "LONG", "qty": 0.01, ...},
    ...
  ]
```

**Cara Kerja:**
1. Receive HTTP request dari executor
2. Validate params (symbol, side, qty)
3. Check risk limits (max positions, balance)
4. Place order ke Bybit API
5. Monitor fills
6. Return response

**Config:**
- `MAX_OPEN_POS_GLOBAL=30` (max concurrent positions)
- `PAPER_MAX_RUNNING_POSITIONS=30` (risk manager limit)
- `GATEWAY_PAPER_MAINNET=true` (live mode)

---

### 3. Nexus Scanner (DATA SOURCE)

**Fungsi:** Collect market data untuk validator  
**Status:** ✅ RUNNING (691 pairs)  
**Containers:**
- `nexus_binance_collector` — Top 150 pairs
- `nexus_bybit_collector` — All 691 pairs

**Data Collected:**
```
For each pair (691 total):
  - CVD (cumulative volume delta)
  - Funding rate
  - Open Interest (OI) change
  - Price, volume
  - RSI, trend indicators

Saved to: runtime/revo/revo_flow_context.json
Update frequency: 50-70 seconds
```

**Data Bridge:**
```
File: nexus/data_bridge.py
Fungsi:
  - Load scanner cache (revo_flow_context.json)
  - Provide to validator via API
  - SignalCopy executor calls:
    bridge.get_pair_data("BTCUSDT")
  - Returns: CVD, funding, OI, flow direction
```

---

## 🔄 EXECUTION FLOW (Step-by-Step)

### Flow 1: Signal Parsing → Execution

```
┌─────────────────────────────────────────────────────────────────┐
│  STEP 1: TELEGRAM MESSAGE ARRIVES                               │
└─────────────────────────────────────────────────────────────────┘

Telegram Channel: "#BTCUSDT LONG 🚀 Entry: 45000-45500 SL: 44500"
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│  STEP 2: LISTENER PARSES                                        │
│  telegram_listener.py                                           │
└─────────────────────────────────────────────────────────────────┘

Extract:
  - Symbol: BTCUSDT
  - Side: LONG
  - Entry: 45000-45500 (zone)
  - SL: 44500
  - TP: [extracted from text or image]
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│  STEP 3: ORCHESTRATOR RECEIVES                                  │
│  orchestrator.py: handle_signal()                               │
└─────────────────────────────────────────────────────────────────┘

Check:
  1. Duplicate? (already executed)
  2. Max positions? (20/30 now, OK)
  3. Symbol in universe? (BTCUSDT in 691 pairs)
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│  STEP 4: VALIDATOR CHECKS                                       │
│  validation_engine.py: validate_signal()                        │
└─────────────────────────────────────────────────────────────────┘

Get data from bridge:
  - BTCUSDT CVD: -1.68
  - Funding: 0.00003134
  - OI change: +0.5%
  - RSI: 65
  - Flow: NO_TRADE

Calculate confluence:
  - Price freshness: 20/22 points
  - Geometry: 15/18 points
  - OI: 7/14 points (rising OK)
  - CVD: 0/14 points (negative, not aligned)
  - Funding: 8/10 points (neutral OK)
  - RSI: 8/10 points (not overbought)
  - Trend: 5/10 points (weak)
  
  Total: 63/100 → VALID ✅ (>55 threshold)
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│  STEP 5: ADVERSARIAL (Optional)                                 │
│  fusionnew/adversarial.py                                       │
└─────────────────────────────────────────────────────────────────┘

LLM debate (if enabled):
  - Bull case: Why LONG makes sense
  - Bear case: Why LONG will fail
  - Verdict: PASS / BLOCK

Result: PASS (advisory only)
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│  STEP 6: EXECUTOR PREPARES                                      │
│  executor.py: execute_signal()                                  │
└─────────────────────────────────────────────────────────────────┘

Calculate:
  - Entry price: 45000 (inside zone, use market)
  - Stop loss: 44500
  - Take profits: [45500, 46000, 46500, 47000]
  - Position size: 0.01 BTC ($450)
  - Risk: 1% account

Build payload:
  {
    "symbol": "BTCUSDT",
    "side": "LONG",
    "entry_price": 45000,
    "stop_loss": 44500,
    "take_profits": [45500, 46000, 46500, 47000],
    "quantity": 0.01
  }
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│  STEP 7: HTTP POST TO GATEWAY                                   │
│  POST http://nexus_gateway:8787/execute_order                   │
└─────────────────────────────────────────────────────────────────┘

Gateway receives:
  - Validate symbol (BTCUSDT OK)
  - Check balance ($10,000 available)
  - Check max positions (20/30 OK)
  - Place order to Bybit
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│  STEP 8: BYBIT EXECUTION                                        │
│  ccxt / Bybit API                                               │
└─────────────────────────────────────────────────────────────────┘

Bybit:
  - Market buy 0.01 BTC @ 45020 (slippage)
  - Set SL order @ 44500
  - Set TP orders @ [45500, 46000, 46500, 47000]
  
Response:
  {
    "order_id": "12345",
    "fill_price": 45020,
    "quantity": 0.01,
    "status": "filled"
  }
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│  STEP 9: GATEWAY RESPONDS                                       │
│  Return to executor                                             │
└─────────────────────────────────────────────────────────────────┘

Response:
  {
    "status": "success",
    "fills": [{
      "price": 45020,
      "qty": 0.01
    }]
  }
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│  STEP 10: ORCHESTRATOR LOGS                                     │
│  Update journal & notify                                        │
└─────────────────────────────────────────────────────────────────┘

Save to:
  - journal/open_positions.json (add BTCUSDT)
  - journal/trade_history.json (entry record)
  - Telegram notification: "✅ BTCUSDT LONG @ 45020 (21/30 aktif)"

DONE ✅
```

---

## ❓ MENJAWAB PERTANYAAN KAMU

### Q1: "Nexus_signal_copy tadi mati, tapi bot masih parsing dan eksekusi?"

**JAWABAN: SALAH ANGGAPAN**

Yang terjadi:
- ❌ Bot TIDAK parsing dan eksekusi saat mati
- ✅ Bot mati = TIDAK ADA parsing/eksekusi sama sekali
- ✅ Journal stale (last update 12h ago) = bukti bot mati

**Timeline:**
```
02:01 UTC: Bot stop (nexus_signal_copy container exited)
02:01-14:45: BOT MATI (12.7 hours downtime)
  → No parsing
  → No validation
  → No execution
  → Shadow data stale (last entry 02:01)
  
14:45 UTC: Bot restart (kamu minta saya start)
  → Parsing active ✅
  → Monitoring 29 channels ✅
  → Ready to execute ✅
```

**Bukti bot mati:**
- Shadow data last update: Aug 6 02:01 (12h ago)
- Trade history last 24h: 0 trades
- Container status: NOT RUNNING (before restart)
- Watchdog alert: "nexus_signal_copy container NOT running"

**Kesimpulan:**
Tidak ada eksekusi saat bot mati. Semua activity STOP total.

---

### Q2: "Gateway itu apa? Kenapa ada gateway dan signal_copy terpisah?"

**JAWABAN: SEPARATION OF CONCERNS**

**Gateway = Execution Layer**
```
Fungsi:
  - Handle HTTP requests
  - Route trades ke exchange (Bybit)
  - Manage open positions
  - Risk limits enforcement
  - ONE gateway = MULTIPLE bots

Analogi:
  Gateway seperti "kasir" di supermarket.
  Banyak pelanggan (bots) bisa antri ke kasir yang sama.
```

**SignalCopy = Strategy Layer**
```
Fungsi:
  - Parse Telegram signals
  - Validate signal quality
  - Decide: execute or reject
  - Send request ke Gateway

Analogi:
  SignalCopy seperti "pelanggan" yang pilih barang.
  Setelah pilih, bayar ke kasir (gateway).
```

**Kenapa dipisah?**

1. **Reusability**
   - 1 Gateway bisa serve multiple bots:
     - nexus_signal_copy (SignalCopy)
     - nexus_fusion_quantum_paper (FusionQuantum)
     - nexus_h1_imbalance (Imbalance bot)
   - Tidak perlu duplicate exchange logic

2. **Safety**
   - Gateway enforce global limits:
     - Max 30 positions TOTAL
     - Balance check
     - Prevent duplicate orders
   - Semua bot share limits yang sama

3. **Maintenance**
   - Update exchange API → only change Gateway
   - Update strategy → only change SignalCopy
   - Independent deployment

**Flow:**
```
SignalCopy (Strategy) → HTTP POST → Gateway (Execution) → Bybit (Exchange)

Multiple bots:
  SignalCopy ────┐
  FusionQuantum ─┼─→ Gateway → Bybit
  Imbalance ─────┘
```

---

### Q3: "Strukturnya bagaimana? Folder-folder apa saja?"

**JAWABAN: STRUKTUR LENGKAP**

```
fusion_omega_nexus/
│
├── signal_copy/               ← SignalCopy bot code
│   ├── run.py                 ← Entry point (container CMD)
│   ├── orchestrator.py        ← Main coordinator (55KB)
│   ├── listeners/
│   │   └── telegram_listener.py  ← Parse Telegram messages
│   ├── validation_engine.py   ← Validate signals
│   ├── validation_config.py   ← Thresholds, weights
│   ├── executor.py            ← Send to Gateway
│   ├── outcome_tracker.py     ← Track TP/SL hits
│   ├── parser.py              ← Extract symbol/side/SL/TP
│   ├── shadow_tracker.py      ← Shadow trailing logic
│   └── ... (34 files total)
│
├── gateway/                   ← Execution Gateway
│   ├── run_gateway.py         ← FastAPI app (port 8787)
│   ├── execution_gateway.py   ← Core logic
│   ├── adapters/              ← Exchange adapters
│   └── ... (6 files total)
│
├── nexus/                     ← Data bridge
│   ├── data_bridge.py         ← Scanner cache → Validator
│   └── ... (2 files total)
│
├── bots/                      ← Scanner bots
│   ├── nexus_scanner.py       ← Main scanner (691 pairs)
│   └── ...
│
├── docker/                    ← Docker configs
│   ├── docker-compose.yml     ← All services definition
│   ├── signal_copy/
│   │   └── Dockerfile         ← SignalCopy image
│   └── ...
│
├── journal/                   ← Trade logs
│   ├── open_positions.json    ← Current positions (20/30)
│   ├── trade_history.json     ← All closed trades
│   ├── shadow_trailing.jsonl  ← Shadow comparison data
│   └── ...
│
├── runtime/                   ← Runtime data
│   └── revo/
│       └── revo_flow_context.json  ← Scanner cache (691 pairs)
│
└── .env                       ← Config (MAX_OPEN_POS_GLOBAL=30)
```

**Key Directories:**

| Directory | Purpose | Size |
|-----------|---------|------|
| `signal_copy/` | SignalCopy bot logic | 34 files |
| `gateway/` | Execution Gateway API | 6 files |
| `nexus/` | Data bridge | 2 files |
| `journal/` | Trade logs & history | Runtime |
| `runtime/revo/` | Scanner data cache | Runtime |
| `docker/` | Container definitions | Config |

---

## 🔄 FLOW SUMMARY (Simplified)

```
1. TELEGRAM SIGNAL arrives
     ↓
2. LISTENER parses (extract symbol, side, SL, TP)
     ↓
3. ORCHESTRATOR coordinates
     ↓
4. VALIDATOR checks (confluence, data quality)
     ↓
5. EXECUTOR prepares (calc size, entry price)
     ↓
6. HTTP POST to GATEWAY (port 8787)
     ↓
7. GATEWAY validates (balance, max positions)
     ↓
8. GATEWAY places order on BYBIT
     ↓
9. BYBIT fills order
     ↓
10. GATEWAY responds to EXECUTOR
     ↓
11. ORCHESTRATOR logs & notifies
     ↓
DONE ✅
```

---

## 📊 CONTAINER RELATIONSHIP

```
┌──────────────────────┐
│  nexus_signal_copy   │  ← SignalCopy bot
│  Port: (none)        │     Parse → Validate → Execute
│  Network: docker     │
└──────────┬───────────┘
           │ HTTP POST
           ▼
┌──────────────────────┐
│  nexus_gateway       │  ← Execution Gateway
│  Port: 8787          │     Handle orders → Bybit API
│  Network: docker     │
└──────────┬───────────┘
           │ REST API
           ▼
┌──────────────────────┐
│  Bybit Exchange      │  ← Live trading
│  Port: 443 (HTTPS)   │     Futures market
│  Network: Internet   │
└──────────────────────┘

Data Source:
┌──────────────────────┐
│ nexus_scanner        │  ← Collect market data
│ binance_collector    │     691 pairs
│ bybit_collector      │
└──────────┬───────────┘
           │ Write cache
           ▼
┌──────────────────────┐
│ revo_flow_context    │  ← Data cache
│ .json                │     CVD, funding, OI
└──────────┬───────────┘
           │ Read
           ▼
┌──────────────────────┐
│ nexus/data_bridge    │  ← Data bridge
│ .py                  │     Provide to validator
└──────────────────────┘
```

---

## 💡 KESIMPULAN

### Komponen Utama:

1. **nexus_signal_copy** (Container)
   - Parse Telegram signals
   - Validate quality
   - Execute via Gateway
   - Status: ✅ RUNNING (baru restart 14:45)

2. **nexus_gateway** (Container)
   - HTTP API (port 8787)
   - Route orders ke Bybit
   - Enforce risk limits (max 30 positions)
   - Status: ✅ RUNNING

3. **Nexus Scanner** (Data Source)
   - Collect 691 pairs data
   - Save to cache (revo_flow_context.json)
   - Status: ✅ RUNNING

### Yang Terjadi Sebelumnya:

- nexus_signal_copy **MATI** sejak 02:01 UTC
- Downtime: 12.7 hours
- **TIDAK ADA** parsing/eksekusi selama mati
- Shadow data stale (last 02:01)
- Bot restart: 14:45 UTC (sekarang HIDUP)

### Separation Gateway vs SignalCopy:

- **Gateway** = Execution layer (1 for all bots)
- **SignalCopy** = Strategy layer (specific bot)
- **Benefit:** Reusability, safety, maintenance

---

**Report generated:** 2026-08-06 15:04 UTC  
**Next:** Lanjut audit shadow data setelah paham struktur ✅
