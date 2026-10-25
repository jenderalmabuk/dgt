#!/usr/bin/env python3
"""Nexus DGT Momentum Paper Trading Bot (M5 Timeframe).

Strategy:
  1. Micro-FVG on M5 confirmed by DGT Golden Trio:
     - Volume > 1.618 * SMA89(Volume)  [Institutional participation]
     - Volume < 4.0 * SMA89(Volume)    [Anti-Climax / Blow-off top filter]
     - Bar Range > 2.0 * ATR11         [Volatility expansion]
     - Close > EMA21 > EMA55           [Aligned trend]
     - Close > Open with Upper Wick < 35%
     - Micro-FVG: Low[i] > High[i-2]
  2. Execution (Skenario 2):
     - Entry: Limit at 50% FVG retrace ((Low[i] + High[i-2]) / 2)
     - Initial SL: High[i-2] - 0.25 * ATR14
     - Expiry: 6 bars (30 min). Cancel if not filled within 30 min.
     - TP1: 33% close at +1.0R -> Move SL to Breakeven (0.0R)
     - TP2: At +2.5R, evaluate Dynamic Decision:
       * If EMA9 > EMA21 and Close > EMA9 -> Lock SL at +2.0R, trail runner to +3.5R
       * Else -> Full close remaining 67% at +2.5R
     - Runner Phase:
       * Trailing Stop: Swing Low (lowest low of last 3 bars) or +2.0R floor
       * Emergency Exit: DGT Red Light (Volume > 4.669 * SMA89) -> Market close immediately!
       * Max Hold: 72 bars (6 hours)
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import requests

# Adjust path so bots/nexus_data can be imported
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bots.nexus_data as nexus_data

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("dgt_momentum")

# --- Environment Configurations ---
STATE_DIR = Path(os.getenv("STATE_DIR", "./runtime/dgt_momentum"))
HEARTBEAT_DIR = Path(os.getenv("HEARTBEAT_DIR", "./runtime/dgt_momentum"))
REVO_RUNTIME = Path(os.getenv("REVO_RUNTIME_DIR", "./runtime/revo"))
TELEGRAM_TOKEN = os.getenv("FUSIONNEW_TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "578305627")
INITIAL_BALANCE = float(os.getenv("PAPER_BALANCE", "1000.0"))
RISK_PCT = float(os.getenv("RISK_PCT", "0.01"))  # 1% per trade ($10 on $1000)
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", "5"))
MAX_ORDERS = int(os.getenv("MAX_ORDERS", "25"))
SCAN_INTERVAL_SEC = int(os.getenv("SCAN_INTERVAL_SEC", "60"))
MAKER_FEE = 0.0002  # 0.02% Bybit maker fee
TAKER_FEE = 0.00055 # 0.055% Bybit taker fee

STATE_FILE = STATE_DIR / "dgt_momentum_state.json"
TRADES_FILE = STATE_DIR / "dgt_momentum_trades.jsonl"
HEARTBEAT_FILE = HEARTBEAT_DIR / "BOT_dgt_momentum_HEARTBEAT_LATEST.json"


def send_telegram(text: str) -> None:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": int(TELEGRAM_CHAT_ID), "text": text, "parse_mode": "HTML"}, timeout=8)
    except Exception as e:
        logger.debug(f"Failed to send telegram: {e}")


def fmt_px(price: Any) -> str:
    """Format price dynamically based on asset scale to prevent decimal truncation."""
    if price is None:
        return "N/A"
    try:
        p = float(price)
    except (ValueError, TypeError):
        return str(price)
    if p >= 100:
        return f"{p:.2f}"
    elif p >= 1:
        return f"{p:.4f}"
    elif p >= 0.01:
        return f"{p:.6f}"
    else:
        return f"{p:.8f}"


WHALE_RUNTIME = Path(os.getenv("WHALE_RUNTIME_DIR", "/app/runtime/whales"))
if not WHALE_RUNTIME.exists():
    WHALE_RUNTIME = Path(__file__).resolve().parent.parent / "runtime" / "whales"


def get_symbol_whale_flow(symbol: str, max_age_minutes: int = 30) -> Optional[Dict[str, Any]]:
    """Query recent Binance/Bybit whale orderflow & liquidation bias."""
    clean = symbol.upper()
    whale_file = WHALE_RUNTIME / f"latest_cex_whale_{clean}.json"
    if not whale_file.exists():
        return None
    try:
        data = json.loads(whale_file.read_text())
        t_str = data.get("timestamp_utc")
        if not t_str:
            return None
        t = datetime.fromisoformat(t_str)
        age_sec = (datetime.now(timezone.utc) - t).total_seconds()
        if age_sec > max_age_minutes * 60:
            return None
        data["age_minutes"] = round(age_sec / 60.0, 1)
        return data
    except Exception as e:
        logger.debug(f"Failed to read whale flow for {symbol}: {e}")
        return None


# ── Shadow CVD & 30d Taker Flow Accumulation Audit (rebalancedbot integration) ──
_cvd_cache: Dict[str, Dict[str, Any]] = {}

def get_symbol_shadow_cvd(symbol: str, ttl_sec: int = 600) -> Dict[str, Any]:
    """Fetch 30-day klines from Binance Futures and calculate Shadow CVD & Momentum.
    Caches results in memory for ttl_sec (default 10 minutes).
    Fallback to Bybit / Exempt if unlisted on Binance Futures.
    """
    clean = symbol.upper()
    now_ts = time.time()

    cached = _cvd_cache.get(clean)
    if cached and (now_ts - cached.get("cached_at", 0)) < ttl_sec:
        return cached["data"]

    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={clean}&interval=1d&limit=31"
    try:
        r = requests.get(url, headers={"User-Agent": "dgt-shadow/1.0"}, timeout=5)
        if r.status_code == 400:
            res = {
                "status": "EXEMPT",
                "source": "bybit_only",
                "symbol": clean,
                "verdict": "EXEMPT_BYBIT_ONLY",
                "cvd_7d": None,
                "cvd_30d": None,
                "mom_30d": None,
            }
        elif r.status_code == 200:
            data = r.json()
            if not data or len(data) < 5:
                res = {
                    "status": "EXEMPT",
                    "source": "binance_fapi",
                    "symbol": clean,
                    "verdict": "EXEMPT_SHORT_HISTORY",
                    "cvd_7d": None,
                    "cvd_30d": None,
                    "mom_30d": None,
                }
            else:
                flow_30 = [2.0 * float(bar[9]) - float(bar[5]) for bar in data]
                cvd_30 = sum(flow_30) / (sum(abs(f) for f in flow_30) + 1e-9)

                flow_7 = flow_30[-7:]
                cvd_7 = sum(flow_7) / (sum(abs(f) for f in flow_7) + 1e-9)

                mom_30 = float(data[-1][4]) / float(data[0][4]) - 1.0

                if cvd_7 >= 0.05 or (cvd_30 >= 0.05 and cvd_7 >= -0.10):
                    verdict = "ACCUMULATION"
                elif cvd_7 <= -0.30 or (cvd_30 <= -0.30 and cvd_7 < 0):
                    verdict = "DISTRIBUTION"
                else:
                    verdict = "NEUTRAL"

                res = {
                    "status": "ACTIVE",
                    "source": "binance_fapi",
                    "symbol": clean,
                    "verdict": verdict,
                    "cvd_7d": round(cvd_7, 4),
                    "cvd_30d": round(cvd_30, 4),
                    "mom_30d": round(mom_30, 4),
                }
        else:
            res = {
                "status": "ERROR",
                "source": "binance_fapi",
                "symbol": clean,
                "verdict": "BYPASS_HTTP_ERROR",
                "cvd_7d": None,
                "cvd_30d": None,
                "mom_30d": None,
            }
    except Exception as e:
        logger.debug(f"Failed to fetch shadow CVD for {clean}: {e}")
        res = {
            "status": "ERROR",
            "source": "binance_fapi",
            "symbol": clean,
            "verdict": "BYPASS_EXCEPTION",
            "cvd_7d": None,
            "cvd_30d": None,
            "mom_30d": None,
        }

    _cvd_cache[clean] = {"data": res, "cached_at": now_ts}
    return res


def format_shadow_cvd_badge(pos: Dict[str, Any]) -> str:
    """Format short summary badge of Shadow CVD audit for trade exit reports."""
    s_cvd = pos.get("shadow_cvd") or {}
    verdict = s_cvd.get("verdict", "")
    if verdict == "ACCUMULATION":
        return "\n🛡️ <b>Shadow CVD:</b> PASS ✅ [Whale Accumulation]"
    elif verdict == "DISTRIBUTION":
        return "\n⚠️ <b>Shadow CVD:</b> WOULD BE BLOCKED ❌ [Heavy Net Dump]"
    elif verdict == "NEUTRAL":
        return "\n⚪ <b>Shadow CVD:</b> PASS ⚪ [Neutral Flow]"
    elif "EXEMPT" in verdict:
        return "\n🛡️ <b>Shadow CVD:</b> EXEMPT [Bybit Only / Unlisted]"
    return ""


# --- Pure Crypto Filtering (Exclude TradFi Stocks, Commodities & Dead Tokens) ---
TRADFI_STOCKS = {
    "AAL", "AAOI", "AAPL", "ADBE", "AEHR", "AFRM", "ALAB", "AMC", "AMD", "AMDSTOCK",
    "AMGN", "AMZN", "APLD", "APP", "APPSTOCK", "ARKK", "ARM", "ASML", "ASTS", "AVGO", "AXTI",
    "BA", "BABA", "BAC", "BIIB", "BILL", "BITF", "BITO", "BRKB", "CAT", "CATSTOCK",
    "CIEN", "CIFR", "CLSK", "COHR", "COIN", "CORZ", "COST", "CRCL", "CRDO", "CRM",
    "CRWD", "CRWV", "CSCO", "CXMT", "DELL", "DIS", "DKNG", "DRAM", "EWY", "EWJ",
    "GDX", "GE", "GEV", "GIGADEVICE", "GILD", "GLW", "GME", "GMESTOCK", "GOOG", "GOOGL",
    "GS", "HIMS", "HOOD", "HPE", "HUT", "HYUNDAI", "IBM", "INTC", "INTU", "INTW",
    "IONQ", "IREN", "IWM", "JNJ", "JPM", "KLAC", "KO", "KORU", "LLY", "LRCX", "MARA",
    "META", "MRVL", "MSFT", "MSTR", "MU", "NBIS", "NET", "NFLX", "NOKIA", "NVD",
    "NVDA", "NVDL", "ONDS", "ORCL", "PANW", "PLTR", "PYPL", "QCOM", "RIOT", "ROKU",
    "SHOP", "SMCI", "SNOW", "SOFI", "SPX", "SQ", "TSL", "TSLA", "TXN", "UBER",
    "WMT", "WULF"
}

COMMODITIES_FIAT = {
    "XAU", "XAG", "PAXG", "XAUT", "GOLD", "SILVER", "OIL", "WTI", "BRENT",
    "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "DXY", "SPX", "NDX", "DJI"
}

DEAD_OR_DEPEGGED = {
    "USTC", "UST", "LUNC", "1000LUNC", "FTT", "VGX", "CEL", "USDC", "BUSD", "FDUSD", "TUSD", "USDE", "STABLE"
}

EXCLUDE_EXACT = TRADFI_STOCKS | COMMODITIES_FIAT | DEAD_OR_DEPEGGED


def is_crypto_pair(symbol: str) -> bool:
    """Return True only for legitimate cryptocurrency futures pairs."""
    # Must be valid ASCII and not dated futures contract (e.g. BTCUSDT-25DEC26)
    if not symbol.isascii() or "-" in symbol:
        return False
    if not symbol.endswith("USDT"):
        return False
    if "STOCK" in symbol or symbol.endswith("ETFUSDT"):
        return False
    base = symbol[:-4]
    base_no_1k = base[4:] if base.startswith("1000") else base
    if base in EXCLUDE_EXACT or base_no_1k in EXCLUDE_EXACT:
        return False
    # Filter leveraged tokens like BTCUPUSDT, ETHDOWNUSDT (allow real crypto like JUP, SYRUP)
    if len(base) > 4 and (base.endswith("UP") or base.endswith("DOWN") or base.endswith("BULL") or base.endswith("BEAR")):
        if base not in ("JUP", "COUP", "GROUP", "SYRUP"):
            return False
    return True


class DGTMomentumBot:
    def __init__(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        HEARTBEAT_DIR.mkdir(parents=True, exist_ok=True)
        self.balance = INITIAL_BALANCE
        self.pending_orders: Dict[str, Dict[str, Any]] = {}
        self.open_positions: Dict[str, Dict[str, Any]] = {}
        self.closed_trades: List[Dict[str, Any]] = []
        self.last_candle_checked: Dict[str, str] = {}
        self.load_state()

    def load_state(self) -> None:
        if STATE_FILE.exists():
            try:
                data = json.loads(STATE_FILE.read_text())
                self.balance = float(data.get("balance", INITIAL_BALANCE))
                self.pending_orders = data.get("pending_orders", {})
                self.open_positions = data.get("open_positions", {})
                if TRADES_FILE.exists():
                    try:
                        with open(TRADES_FILE) as tf:
                            self.closed_trades = [json.loads(line) for line in tf if line.strip()]
                    except Exception:
                        self.closed_trades = data.get("closed_trades", [])
                else:
                    self.closed_trades = data.get("closed_trades", [])
                logger.info(f"Loaded state: Balance=${self.balance:.2f}, Open={len(self.open_positions)}, Orders={len(self.pending_orders)}, Closed={len(self.closed_trades)}")
            except Exception as e:
                logger.error(f"Error loading state file: {e}")

    def save_state(self) -> None:
        try:
            data = {
                "balance": round(self.balance, 2),
                "pending_orders": self.pending_orders,
                "open_positions": self.open_positions,
                "total_trades": len(self.closed_trades),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            tmp = STATE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, default=str))
            tmp.rename(STATE_FILE)
            
            # Write heartbeat
            hb = {
                "bot": "dgt_momentum_paper",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "balance": round(self.balance, 2),
                "open_positions": len(self.open_positions),
                "pending_orders": len(self.pending_orders),
                "status": "RUNNING",
            }
            hb_tmp = HEARTBEAT_FILE.with_suffix(".tmp")
            hb_tmp.write_text(json.dumps(hb, indent=2, default=str))
            hb_tmp.rename(HEARTBEAT_FILE)
        except Exception as e:
            logger.error(f"Error saving state: {e}")

    def log_closed_trade(self, trade: Dict[str, Any]) -> None:
        self.closed_trades.append(trade)
        try:
            with open(TRADES_FILE, "a") as f:
                f.write(json.dumps(trade, default=str) + "\n")
        except Exception as e:
            logger.error(f"Error writing trade log: {e}")

    def get_universe(self) -> List[str]:
        """Load symbols scanned by nexus_scanner from runtime/revo, filtered for pure crypto."""
        candidates = [
            REVO_RUNTIME / "pair_universe_all.json",
            REVO_RUNTIME / "pair_universe_remote.json",
            REVO_RUNTIME / "canonical_universe.json",
            REVO_RUNTIME / "bybit_all_691.txt",
        ]
        symbols = []
        for path in candidates:
            if not path.exists():
                continue
            try:
                if path.suffix == ".json":
                    data = json.loads(path.read_text())
                    pairs = data.get("pairs", data) if isinstance(data, dict) else data
                    for item in pairs:
                        sym = item.get("symbol") if isinstance(item, dict) else str(item)
                        if sym and is_crypto_pair(sym) and sym not in symbols:
                            symbols.append(sym)
                elif path.suffix == ".txt":
                    lines = path.read_text().splitlines()
                    for line in lines:
                        sym = line.strip().replace("/", "").replace(":USDT", "")
                        if sym and is_crypto_pair(sym) and sym not in symbols:
                            symbols.append(sym)
                if len(symbols) >= 50:
                    break
            except Exception as e:
                logger.debug(f"Failed to load universe from {path}: {e}")
                
        if not symbols:
            # Fallback default universe (pure crypto only)
            symbols = [
                "BTCUSDT", "ETHUSDT", "SOLUSDT", "NEARUSDT", "AVAXUSDT", "SUIUSDT",
                "ENAUSDT", "ONDOUSDT", "LINKUSDT", "INJUSDT", "DOGEUSDT", "FETUSDT",
                "PEPEUSDT", "WIFUSDT", "APTUSDT", "ARBUSDT", "OPUSDT", "TIAUSDT",
                "SEIUSDT", "RENDERUSDT", "TAOUSDT", "AAVEUSDT", "KASUSDT", "PENDLEUSDT"
            ]
        return symbols

    def evaluate_symbol(self, symbol: str) -> None:
        """Fetch 5m klines and check DGT Golden Trio + Micro-FVG."""
        if symbol in self.open_positions or symbol in self.pending_orders:
            return

        df = nexus_data.fetch_recent(symbol, "5m", limit=100)
        if df.empty or len(df) < 60:
            return

        # Ensure numeric
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        c = df["close"].to_numpy(float)
        h = df["high"].to_numpy(float)
        l = df["low"].to_numpy(float)
        o = df["open"].to_numpy(float)
        v = df["volume"].to_numpy(float)
        n = len(c)

        now_dt = datetime.now(timezone.utc)
        now_ts = now_dt.timestamp()

        # Check whether df.iloc[-1] is already a closed bar or currently forming
        last_open_ts = pd.to_datetime(df["open_time"].iloc[-1], utc=True).timestamp()
        if now_ts >= last_open_ts + 300:
            latest_closed_idx = n - 1
        else:
            latest_closed_idx = n - 2

        if latest_closed_idx < 60:
            return

        # Check both latest closed bar and previous bar (in case surge formed 1 bar ago and is currently in pullback)
        candidate_indices = [latest_closed_idx]
        if latest_closed_idx - 1 >= 60:
            candidate_indices.append(latest_closed_idx - 1)

        s_c = pd.Series(c)
        s_v = pd.Series(v)
        ema21 = s_c.ewm(span=21, adjust=False).mean().to_numpy()
        ema55 = s_c.ewm(span=55, adjust=False).mean().to_numpy()
        v_sma89 = s_v.rolling(89).mean().to_numpy()
        v_sma20 = s_v.rolling(20).mean().to_numpy()

        pc = np.roll(c, 1); pc[0] = c[0]
        tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
        atr14 = pd.Series(tr).ewm(alpha=1/14, adjust=False).mean().to_numpy()
        atr11 = pd.Series(tr).ewm(alpha=1/11, adjust=False).mean().to_numpy()

        for i in candidate_indices:
            bar_time = str(df["open_time"].iloc[i])
            if self.last_candle_checked.get(symbol) == bar_time:
                continue

            # ── 1. DGT Golden Trio Setup Filter ──
            if not (c[i] > ema21[i] > ema55[i]):
                continue
            if v_sma20[i] <= 0 or v[i] < 2.0 * v_sma20[i]:
                continue
            if (c[i] - o[i]) < 1.0 * atr14[i]:
                continue

            bar_range = h[i] - l[i]
            if bar_range <= 0 or (h[i] - c[i]) / bar_range > 0.35:
                continue  # Skip long upper wick

            # DGT Institutional Volume (1.618 x SMA89)
            if not (v[i] > 1.618 * v_sma89[i]):
                continue
            # DGT Anti-Climax Peak (< 4.0 x SMA89)
            if not (v[i] < 4.0 * v_sma89[i]):
                continue
            # DGT Volatility Expansion (> 2.0 x ATR11)
            if not (bar_range > 2.0 * atr11[i]):
                continue

            # ── 2. Micro-FVG on M5 (Low[i] > High[i-2]) ──
            gap_bottom = float(h[i - 2])
            gap_top = float(l[i])
            if not (gap_top > gap_bottom):
                continue

            entry_px = float((gap_top + gap_bottom) / 2.0)
            sl_px = float(gap_bottom - 0.25 * atr14[i])
            risk = entry_px - sl_px

            if sl_px >= entry_px or risk / entry_px < 0.003:
                continue

            runaway_px = float(max(float(h[i]) + 0.1 * float(atr14[i]), float(entry_px + 3.5 * risk)))
            if i < latest_closed_idx:
                min_since = float(np.min(l[i+1:]))
                max_since = float(np.max(h[i+1:]))
                if min_since <= sl_px or max_since >= runaway_px or min_since <= entry_px:
                    continue

            self.last_candle_checked[symbol] = bar_time

            tp1_px = float(entry_px + 1.0 * risk)
            tp2_px = float(entry_px + 2.5 * risk)

            # Validasi Real-Time: Jika harga pasar saat ini sudah tembus runaway ceiling, setup sudah runaway
            cur_live_price = float(c[-1])
            if cur_live_price >= runaway_px:
                continue
            # Jika harga saat ini sudah di bawah entry, hindari order terlambat
            if cur_live_price <= entry_px:
                continue

            # ── 3. Sentiment Radar Confluence & Safety Shield ──
            sent_info = None
            sentiment_badge = ""
            try:
                from sentiment_radar.radar import get_symbol_sentiment
                sent_info = get_symbol_sentiment(symbol, max_age_hours=12)
                if sent_info:
                    # Negative Sentinel: Hindari koin jika ada berita exploit/hack/delisting keras
                    if sent_info.get("score", 0.0) <= -0.5:
                        logger.info(f"🚫 [SENTIMENT SHIELD] {symbol} skipped due to negative news: {sent_info.get('summary')}")
                        continue
                    s_score = sent_info.get("score", 0.0)
                    s_cat = sent_info.get("category", "")
                    s_summary = sent_info.get("summary", "")
                    if s_score >= 0.5 or s_cat == "LISTING":
                        sentiment_badge = f"\n🔥 <b>Sentiment:</b> Bullish ({s_score:+.2f}) [{s_cat}] - {s_summary}"
                    elif s_score > 0:
                        sentiment_badge = f"\n📰 <b>News Context:</b> {s_summary}"
            except Exception as e:
                logger.debug(f"Sentiment check bypassed: {e}")

            # ── 4. CEX Whale Orderflow & Liquidation Shield ──
            whale_info = None
            whale_badge = ""
            try:
                whale_info = get_symbol_whale_flow(symbol, max_age_minutes=30)
                if whale_info:
                    w_bias = whale_info.get("bias", "NEUTRAL")
                    w_val = float(whale_info.get("value_usd", 0.0))
                    w_type = whale_info.get("type", "")
                    w_side = whale_info.get("side", "")
                    w_age = whale_info.get("age_minutes", 0)

                    # Whale Dump Shield: Blokir order Long jika ada order Sell masif paus
                    if w_bias == "BEARISH":
                        if w_type == "TRADE" and w_val >= 1_000_000:
                            logger.info(f"🚫 [WHALE DUMP SHIELD] {symbol} skipped: Massive whale market sell (${w_val:,.0f}) detected {w_age}m ago.")
                            continue
                        elif w_type == "LIQUIDATION" and w_side == "SELL" and w_val >= 2_000_000:
                            logger.info(f"🚫 [WHALE FLUSH SHIELD] {symbol} skipped: Ongoing massive long liquidation cascade (${w_val:,.0f}) detected {w_age}m ago.")
                            continue

                    # Whale Confluence Badge: Jika paus akumulasi / short squeeze
                    if w_bias == "BULLISH" and w_val >= 100_000:
                        val_str = f"${w_val/1e6:.2f}M" if w_val >= 1e6 else f"${w_val/1e3:.0f}K"
                        action_desc = "Aggressive Buy" if w_type == "TRADE" else "Short Squeeze"
                        whale_badge = f"\n🐋 <b>Whale Flow:</b> Bullish {action_desc} ({val_str}) on Binance ({w_age}m ago)"
            except Exception as e:
                logger.debug(f"Whale flow check bypassed: {e}")

            # ── 5. Shadow CVD & Taker Accumulation Audit (rebalancedbot integration) ──
            shadow_cvd_info = None
            shadow_cvd_badge = ""
            try:
                shadow_cvd_info = get_symbol_shadow_cvd(symbol)
                if shadow_cvd_info:
                    s_status = shadow_cvd_info.get("status")
                    s_verdict = shadow_cvd_info.get("verdict", "NEUTRAL")
                    s_c7 = shadow_cvd_info.get("cvd_7d")
                    s_c30 = shadow_cvd_info.get("cvd_30d")
                    s_m30 = shadow_cvd_info.get("mom_30d")

                    if s_status == "EXEMPT":
                        shadow_cvd_badge = "\n🛡️ <b>[Shadow CVD]:</b> Exempt (Bybit Only / Unlisted on Binance)"
                    else:
                        c7_s = f"{s_c7:+.2f}" if s_c7 is not None else "N/A"
                        c30_s = f"{s_c30:+.2f}" if s_c30 is not None else "N/A"
                        m30_s = f"{s_m30*100:+.1f}%" if s_m30 is not None else "N/A"
                        if s_verdict == "ACCUMULATION":
                            shadow_cvd_badge = f"\n🛡️ <b>[Shadow CVD]:</b> PASS ✅ Accumulation (7d: {c7_s}, 30d: {c30_s}, Mom: {m30_s})"
                        elif s_verdict == "DISTRIBUTION":
                            shadow_cvd_badge = f"\n⚠️ <b>[Shadow CVD]:</b> WOULD BE BLOCKED ❌ Net Dump (7d: {c7_s}, 30d: {c30_s}, Mom: {m30_s})"
                        else:
                            shadow_cvd_badge = f"\n⚪ <b>[Shadow CVD]:</b> PASS ⚪ Neutral (7d: {c7_s}, 30d: {c30_s}, Mom: {m30_s})"
            except Exception as e:
                logger.debug(f"Shadow CVD check bypassed: {e}")

            # Capital & Sizing (Risk 1% of current balance)
            risk_usd = self.balance * RISK_PCT
            pos_size = risk_usd / risk
            pos_notional = pos_size * entry_px

            # Expiry: 6 bars (30 min) from close of setup bar i
            bar_i_open_ts = pd.to_datetime(df["open_time"].iloc[i], utc=True).timestamp()
            expires_at = bar_i_open_ts + 300 + (6 * 300)
            if now_ts >= expires_at:
                continue

            order_id = f"ORD_{symbol}_{int(time.time())}"
            order = {
                "order_id": order_id,
                "symbol": symbol,
                "side": "BUY",
                "entry_px": entry_px,
                "sl_px": sl_px,
                "tp1_px": tp1_px,
                "tp2_px": tp2_px,
                "runaway_px": runaway_px,
                "risk": risk,
                "risk_usd": risk_usd,
                "pos_size": pos_size,
                "pos_notional": pos_notional,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "created_candle_time": str(df["open_time"].iloc[-1]),
                "expires_at": expires_at,
                "sentiment": sent_info,
                "whale": whale_info,
                "shadow_cvd": shadow_cvd_info,
                "status": "PENDING",
            }

            if len(self.pending_orders) < MAX_ORDERS:
                self.pending_orders[symbol] = order
                logger.info(f"🎯 [NEW SETUP] {symbol} M5 Micro-FVG Entry: {fmt_px(entry_px)} | SL: {fmt_px(sl_px)} | TP1: {fmt_px(tp1_px)} | TP2: {fmt_px(tp2_px)}")
                send_telegram(
                    f"🎯 <b>[DGT Momentum] New Setup</b>\n"
                    f"<b>Pair:</b> #{symbol}\n"
                    f"<b>Entry (50% FVG):</b> {fmt_px(entry_px)}\n"
                    f"<b>Stop Loss:</b> {fmt_px(sl_px)} (-1.0R)\n"
                    f"<b>TP 1:</b> {fmt_px(tp1_px)} (+1.0R, 33% + BEP)\n"
                    f"<b>TP 2:</b> {fmt_px(tp2_px)} (+2.5R, 67% / Runner)\n"
                    f"<b>Risk:</b> ${risk_usd:.2f} ({RISK_PCT*100:.1f}%)\n"
                    f"<b>Expiry:</b> 30 menit (6 bar)"
                    f"{sentiment_badge}"
                    f"{whale_badge}"
                    f"{shadow_cvd_badge}"
                )
                self.save_state()
                break

    def update_orders_and_positions(self) -> None:
        """Process fills, SL, TP1, TP2, Trailing, and Emergency DGT Exits."""
        now_ts = time.time()

        # ── 1. Check Pending Orders ──
        for symbol, order in list(self.pending_orders.items()):
            if len(self.open_positions) >= MAX_OPEN_POSITIONS:
                break
                
            df = nexus_data.fetch_recent(symbol, "5m", limit=5)
            if df.empty:
                continue

            # Strict Fill Check: HANYA evaluasi candle yang terjadi pada atau setelah order dibuat!
            # Mencegah membaca low masa lalu (sebelum order dipasang)
            created_candle = order.get("created_candle_time")
            if created_candle:
                try:
                    df_open_times = pd.to_datetime(df["open_time"], utc=True)
                    target_time = pd.to_datetime(created_candle, utc=True)
                    df_valid = df[df_open_times >= target_time]
                    if df_valid.empty:
                        df_valid = df.iloc[-1:]
                except Exception:
                    df_valid = df.iloc[-1:]
            else:
                df_valid = df.iloc[-1:]

            cur_low = float(df_valid["low"].min())
            cur_high = float(df_valid["high"].max())
            cur_close = float(df["close"].iloc[-1])
            
            # Check Fill: Price dipped to entry
            if cur_low <= order["entry_px"]:
                pos = dict(order)
                pos["entry_time"] = datetime.now(timezone.utc).isoformat()
                pos["cur_sl"] = order["sl_px"]
                pos["stage"] = "HOLD" # HOLD -> TP1_HIT -> RUNNER
                pos["rem_ratio"] = 1.0
                pos["realized_pnl"] = 0.0
                pos["hit_tp1"] = False
                pos["hit_tp2"] = False
                pos["filled_ts"] = now_ts
                
                # Deduct maker entry fee
                fee = pos["pos_notional"] * MAKER_FEE
                self.balance -= fee
                pos["total_fees"] = fee
                
                self.open_positions[symbol] = pos
                del self.pending_orders[symbol]
                
                logger.info(f"🚀 [FILLED] {symbol} Long filled @ {fmt_px(order['entry_px'])}")
                send_telegram(
                    f"🚀 <b>[DGT Momentum] Order Filled</b>\n"
                    f"<b>Pair:</b> #{symbol}\n"
                    f"<b>Entry Price:</b> {fmt_px(order['entry_px'])}\n"
                    f"<b>SL:</b> {fmt_px(order['sl_px'])}\n"
                    f"<b>Position Value:</b> ${pos['pos_notional']:.2f}"
                )
                self.save_state()
                continue
                
            # Check Runaway Invalidation:
            # Jika pada candle setelah order dibuat harga terus terbang menembus runaway ceiling tanpa pullback ke entry!
            runaway_threshold = float(order.get("runaway_px", order["entry_px"] + 3.5 * order["risk"]))
            if cur_high >= runaway_threshold and cur_low > order["entry_px"]:
                logger.info(f"🚫 [RUNAWAY CANCEL] {symbol} reached runaway level ({fmt_px(runaway_threshold)}) without pullback to entry. Order cancelled.")
                send_telegram(
                    f"🚫 <b>[DGT Momentum] Setup Invalidation (Runaway)</b>\n"
                    f"<b>Pair:</b> #{symbol}\n"
                    f"<b>Entry (50% FVG):</b> {fmt_px(order['entry_px'])}\n"
                    f"<b>Runaway Ceiling:</b> {fmt_px(runaway_threshold)}\n"
                    f"<b>Alasan:</b> Harga terus terbang menembus puncak ({fmt_px(runaway_threshold)}) tanpa pullback.\n"
                    f"<b>Status:</b> Order Dibatalkan Otomatis (Mencegah Jebakan Dump / Risk-Free)"
                )
                del self.pending_orders[symbol]
                self.save_state()
                continue

            # Check Expiry (30 minutes / 6 bars)
            if now_ts >= order["expires_at"]:
                logger.info(f"⏳ [EXPIRED] Order for {symbol} expired without fill.")
                send_telegram(
                    f"⏳ <b>[DGT Momentum] Setup Expired</b>\n"
                    f"<b>Pair:</b> #{symbol}\n"
                    f"<b>Entry (50% FVG):</b> {fmt_px(order['entry_px'])}\n"
                    f"<b>Stop Loss:</b> {fmt_px(order['sl_px'])}\n"
                    f"<b>Alasan:</b> Harga tidak pullback ke 50% FVG dalam 30 menit (6 bar).\n"
                    f"<b>Status:</b> Setup Dibatalkan (Risk-Free / Tanpa Rugi)"
                )
                del self.pending_orders[symbol]
                self.save_state()

        # ── 2. Manage Open Positions ──
        for symbol, pos in list(self.open_positions.items()):
            df = nexus_data.fetch_recent(symbol, "5m", limit=30)
            if df.empty or len(df) < 15:
                continue

            c = df["close"].to_numpy(float)
            h = df["high"].to_numpy(float)
            l = df["low"].to_numpy(float)
            v = df["volume"].to_numpy(float)
            cur_low = l[-1]
            cur_high = h[-1]
            cur_close = c[-1]
            
            entry = pos["entry_px"]
            risk = pos["risk"]
            cur_sl = pos["cur_sl"]
            
            s_c = pd.Series(c)
            s_v = pd.Series(v)
            ema9 = s_c.ewm(span=9, adjust=False).mean().to_numpy()
            ema21 = s_c.ewm(span=21, adjust=False).mean().to_numpy()
            v_sma89 = s_v.rolling(89).mean().to_numpy() if len(s_v) >= 89 else s_v.rolling(len(s_v)).mean().to_numpy()
            dgt_red_light = v[-1] > (4.669 * v_sma89[-1])
            swing_low_3 = float(np.min(l[-4:-1])) # lowest low of previous 3 completed bars

            # ── A. Stop Loss / BEP / Trailing Stop Triggered ──
            if cur_low <= cur_sl:
                close_px = cur_sl
                rem_size = pos["pos_size"] * pos["rem_ratio"]
                pnl_portion = rem_size * (close_px - entry)
                fee = rem_size * close_px * TAKER_FEE
                
                net_trade_pnl = pos["realized_pnl"] + pnl_portion - fee
                self.balance += (pnl_portion - fee)
                
                r_ret = net_trade_pnl / pos["risk_usd"]
                status = "WIN" if net_trade_pnl > 0 else ("BE" if pos["hit_tp1"] else "LOSS")

                if pos.get("stage") == "MOONBAG":
                    exit_reason = "MOONBAG_TRAILING"
                    title = f"🏆 <b>[DGT Momentum] Moonbag Trailing Closed ({status})</b>"
                elif pos.get("stage") == "RUNNER":
                    exit_reason = "RUNNER_TRAILING"
                    title = f"💰 <b>[DGT Momentum] Runner Trailing Closed ({status})</b>"
                else:
                    exit_reason = "SL_OR_BEP"
                    title = f"🛑 <b>[DGT Momentum] Position Closed ({status})</b>"
                
                cvd_exit_badge = format_shadow_cvd_badge(pos)
                logger.info(f"🛑 [CLOSED] {symbol} hit {exit_reason} @ {fmt_px(close_px)} | PnL: ${net_trade_pnl:.2f} ({r_ret:+.2f}R)")
                send_telegram(
                    f"{title}\n"
                    f"<b>Pair:</b> #{symbol}\n"
                    f"<b>Exit Price:</b> {fmt_px(close_px)}\n"
                    f"<b>Net PnL:</b> ${net_trade_pnl:+.2f} ({r_ret:+.2f}R)\n"
                    f"<b>Account Balance:</b> ${self.balance:.2f}"
                    f"{cvd_exit_badge}"
                )
                
                pos["exit_px"] = close_px
                pos["exit_time"] = datetime.now(timezone.utc).isoformat()
                pos["net_pnl"] = net_trade_pnl
                pos["net_r"] = r_ret
                pos["status"] = status
                pos["exit_reason"] = exit_reason
                self.log_closed_trade(pos)
                del self.open_positions[symbol]
                self.save_state()
                continue

            # ── B. TP1 Trigger (+1.0R): Close 33%, move SL to Breakeven ──
            if not pos["hit_tp1"] and cur_high >= pos["tp1_px"]:
                pos["hit_tp1"] = True
                close_ratio = 0.33
                close_px = pos["tp1_px"]
                closed_size = pos["pos_size"] * close_ratio
                pnl_portion = closed_size * (close_px - entry)
                fee = closed_size * close_px * MAKER_FEE
                
                pos["realized_pnl"] += (pnl_portion - fee)
                self.balance += (pnl_portion - fee)
                pos["rem_ratio"] -= close_ratio
                pos["cur_sl"] = entry # MOVE SL TO BREAKEVEN!
                
                logger.info(f"💰 [TP1 HIT] {symbol} reached +1.0R! 33% closed, SL moved to BEP {fmt_px(entry)}")
                send_telegram(
                    f"💰 <b>[DGT Momentum] TP1 Hit (+1.0R)</b>\n"
                    f"<b>Pair:</b> #{symbol}\n"
                    f"<b>Price:</b> {fmt_px(close_px)}\n"
                    f"<b>Action:</b> 33% closed for profit, SL moved to Breakeven!\n"
                    f"<b>Position is now 100% Risk-Free.</b>"
                )
                self.save_state()

            # ── C. TP2 Trigger (+2.5R): Dynamic Decision ──
            if pos["hit_tp1"] and not pos["hit_tp2"] and cur_high >= pos["tp2_px"]:
                pos["hit_tp2"] = True
                
                # Check Dynamic Trailing Condition:
                strong_momentum = (ema9[-1] > ema21[-1]) and (cur_close > ema9[-1]) and not dgt_red_light
                
                if strong_momentum:
                    # Enters RUNNER mode: lock SL at +2.0R, target +3.5R
                    pos["stage"] = "RUNNER"
                    pos["cur_sl"] = float(entry + 2.0 * risk)
                    pos["tp3_px"] = float(entry + 3.5 * risk)
                    logger.info(f"🔥 [RUNNER MODE] {symbol} passed +2.5R with strong momentum! Trailing to +3.5R with SL @ {fmt_px(pos['cur_sl'])}")
                    send_telegram(
                        f"🔥 <b>[DGT Momentum] Strong Momentum Detected (+2.5R)</b>\n"
                        f"<b>Pair:</b> #{symbol}\n"
                        f"<b>Action:</b> Keeping runner alive!\n"
                        f"<b>SL Locked at:</b> {fmt_px(pos['cur_sl'])} (+2.0R guaranteed!)\n"
                        f"<b>Trailing Target:</b> {fmt_px(pos['tp3_px'])} (+3.5R)"
                    )
                else:
                    # Full close remaining 67% at +2.5R
                    close_px = pos["tp2_px"]
                    rem_size = pos["pos_size"] * pos["rem_ratio"]
                    pnl_portion = rem_size * (close_px - entry)
                    fee = rem_size * close_px * MAKER_FEE
                    net_trade_pnl = pos["realized_pnl"] + pnl_portion - fee
                    
                    self.balance += (pnl_portion - fee)
                    r_ret = net_trade_pnl / pos["risk_usd"]
                    
                    cvd_exit_badge = format_shadow_cvd_badge(pos)
                    logger.info(f"🏆 [FULL TP2] {symbol} closed remaining at +2.5R | Total PnL: ${net_trade_pnl:.2f} ({r_ret:+.2f}R)")
                    send_telegram(
                        f"🏆 <b>[DGT Momentum] Full TP2 Target Hit!</b>\n"
                        f"<b>Pair:</b> #{symbol}\n"
                        f"<b>Exit Price:</b> {fmt_px(close_px)} (+2.5R)\n"
                        f"<b>Net PnL:</b> ${net_trade_pnl:+.2f} ({r_ret:+.2f}R)\n"
                        f"<b>Balance:</b> ${self.balance:.2f}"
                        f"{cvd_exit_badge}"
                    )
                    
                    pos["exit_px"] = close_px
                    pos["exit_time"] = datetime.now(timezone.utc).isoformat()
                    pos["net_pnl"] = net_trade_pnl
                    pos["net_r"] = r_ret
                    pos["status"] = "WIN"
                    pos["exit_reason"] = "FULL_TP2"
                    self.log_closed_trade(pos)
                    del self.open_positions[symbol]
                    self.save_state()
                    continue

            # ── D. RUNNER & MOONBAG Phase: Dynamic Step Trailing, Swing Low & Whale Protection ──
            if pos.get("stage") in ("RUNNER", "MOONBAG"):
                # Emergency DGT Red Light Exit (Exhaustion volume spike)
                if dgt_red_light:
                    close_px = cur_close
                    rem_size = pos["pos_size"] * pos["rem_ratio"]
                    pnl_portion = rem_size * (close_px - entry)
                    fee = rem_size * close_px * TAKER_FEE
                    net_trade_pnl = pos["realized_pnl"] + pnl_portion - fee
                    self.balance += (pnl_portion - fee)
                    r_ret = net_trade_pnl / pos["risk_usd"]
                    
                    cvd_exit_badge = format_shadow_cvd_badge(pos)
                    logger.info(f"🚦 [RED LIGHT EMERGENCY EXIT] {symbol} volume spike! Climax exit @ {fmt_px(close_px)} | PnL: ${net_trade_pnl:.2f}")
                    send_telegram(
                        f"🚦 <b>[DGT Momentum] DGT Red Light Exhaustion!</b>\n"
                        f"<b>Pair:</b> #{symbol}\n"
                        f"<b>Warning:</b> Extreme volume spike detected. Emergency exit at top!\n"
                        f"<b>Exit Price:</b> {fmt_px(close_px)}\n"
                        f"<b>Total PnL:</b> ${net_trade_pnl:+.2f} ({r_ret:+.2f}R)"
                        f"{cvd_exit_badge}"
                    )
                    pos["exit_px"] = close_px
                    pos["exit_time"] = datetime.now(timezone.utc).isoformat()
                    pos["net_pnl"] = net_trade_pnl
                    pos["net_r"] = r_ret
                    pos["status"] = "WIN"
                    pos["exit_reason"] = "DGT_RED_LIGHT"
                    self.log_closed_trade(pos)
                    del self.open_positions[symbol]
                    self.save_state()
                    continue

                # Emergency Whale Climax Dump Exit: jika terdeteksi Mega/Ultra Sell >= $2M dalam 5 menit terakhir
                whale_dump = get_symbol_whale_flow(symbol, max_age_minutes=5)
                if whale_dump and whale_dump.get("bias") == "BEARISH" and float(whale_dump.get("value_usd", 0.0)) >= 2_000_000:
                    close_px = cur_close
                    rem_size = pos["pos_size"] * pos["rem_ratio"]
                    pnl_portion = rem_size * (close_px - entry)
                    fee = rem_size * close_px * TAKER_FEE
                    net_trade_pnl = pos["realized_pnl"] + pnl_portion - fee
                    self.balance += (pnl_portion - fee)
                    r_ret = net_trade_pnl / pos["risk_usd"]

                    w_val = float(whale_dump.get("value_usd", 0.0))
                    cvd_exit_badge = format_shadow_cvd_badge(pos)
                    logger.info(f"🐋🛑 [WHALE CLIMAX EXIT] {symbol} hit by Ultra-Mega Sell (${w_val:,.0f})! Profit locked @ {fmt_px(close_px)} | PnL: ${net_trade_pnl:.2f}")
                    send_telegram(
                        f"🐋🛑 <b>[DGT Momentum] Whale Climax Exit!</b>\n"
                        f"<b>Pair:</b> #{symbol}\n"
                        f"<b>Alert:</b> Ultra-Mega Market Sell Paus (${w_val/1e6:.2f}M) terdeteksi di Binance Futures!\n"
                        f"<b>Action:</b> Posisi Runner langsung diamankan di pucuk.\n"
                        f"<b>Exit Price:</b> {fmt_px(close_px)}\n"
                        f"<b>Total PnL:</b> ${net_trade_pnl:+.2f} ({r_ret:+.2f}R)"
                        f"{cvd_exit_badge}"
                    )
                    pos["exit_px"] = close_px
                    pos["exit_time"] = datetime.now(timezone.utc).isoformat()
                    pos["net_pnl"] = net_trade_pnl
                    pos["net_r"] = r_ret
                    pos["status"] = "WIN"
                    pos["exit_reason"] = "WHALE_CLIMAX_EXIT"
                    self.log_closed_trade(pos)
                    del self.open_positions[symbol]
                    self.save_state()
                    continue

                # 3. Dynamic Step Trailing Floors (Based on Empirical Pullback Depth)
                cur_r = (cur_high - entry) / risk
                if cur_r >= 10.0 and pos["cur_sl"] < (entry + 7.5 * risk):
                    pos["cur_sl"] = float(entry + 7.5 * risk)
                    logger.info(f"🚀 [STEP TRAIL] {symbol} crossed +10R! SL raised to +7.5R floor: {fmt_px(pos['cur_sl'])}")
                elif cur_r >= 7.0 and pos["cur_sl"] < (entry + 5.0 * risk):
                    pos["cur_sl"] = float(entry + 5.0 * risk)
                    logger.info(f"🚀 [STEP TRAIL] {symbol} crossed +7R! SL raised to +5.0R floor: {fmt_px(pos['cur_sl'])}")
                elif cur_r >= 5.0 and pos["cur_sl"] < (entry + 3.5 * risk):
                    pos["cur_sl"] = float(entry + 3.5 * risk)
                    logger.info(f"🚀 [STEP TRAIL] {symbol} crossed +5R! SL raised to +3.5R floor: {fmt_px(pos['cur_sl'])}")

                # Swing Low M5 ratchet if higher
                if swing_low_3 > pos["cur_sl"] and swing_low_3 < cur_close:
                    pos["cur_sl"] = swing_low_3
                    logger.info(f"📈 [TRAILING UPDATE] {symbol} SL raised to Swing Low: {fmt_px(swing_low_3)}")

                # 4. Trigger TP3 (+3.5R): Close 34%, Lock SL at +2.5R, Unlock Unlimited Moonbag!
                if pos.get("stage") == "RUNNER" and cur_high >= pos.get("tp3_px", entry + 3.5 * risk):
                    pos["stage"] = "MOONBAG"
                    close_ratio = 0.34  # Close half of remaining 67%, leaving ~33% Moonbag
                    close_px = pos.get("tp3_px", entry + 3.5 * risk)
                    closed_size = pos["pos_size"] * close_ratio
                    pnl_portion = closed_size * (close_px - entry)
                    fee = closed_size * close_px * MAKER_FEE

                    pos["realized_pnl"] += (pnl_portion - fee)
                    self.balance += (pnl_portion - fee)
                    pos["rem_ratio"] -= close_ratio
                    
                    # Lock SL strictly at +2.5R guaranteed floor!
                    pos["cur_sl"] = max(pos["cur_sl"], float(entry + 2.5 * risk))
                    
                    r_ret = pos["realized_pnl"] / pos["risk_usd"]
                    logger.info(f"🚀 [TP3 HIT & MOONBAG] {symbol} reached +3.5R! 34% closed, SL locked at +2.5R ({fmt_px(pos['cur_sl'])}). 33% Moonbag trailing freely!")
                    send_telegram(
                        f"🚀 <b>[DGT Momentum] TP3 Reached (+3.5R) & Moonbag Unlocked!</b>\n"
                        f"<b>Pair:</b> #{symbol}\n"
                        f"<b>Price:</b> {fmt_px(close_px)}\n"
                        f"<b>Action:</b> 34% closed for profit (+3.5R)!\n"
                        f"<b>SL Locked at:</b> {fmt_px(pos['cur_sl'])} (+2.5R guaranteed floor)\n"
                        f"<b>Status:</b> 33% Moonbag active with <b>NO CEILING trailing</b> to catch mega-pumps!"
                    )
                    self.save_state()

            # ── E. Max Hold Expiry (6 hours) ──
            if now_ts - pos.get("filled_ts", now_ts) > 6 * 3600:
                close_px = cur_close
                rem_size = pos["pos_size"] * pos["rem_ratio"]
                pnl_portion = rem_size * (close_px - entry)
                fee = rem_size * close_px * TAKER_FEE
                net_trade_pnl = pos["realized_pnl"] + pnl_portion - fee
                self.balance += (pnl_portion - fee)
                r_ret = net_trade_pnl / pos["risk_usd"]
                
                cvd_exit_badge = format_shadow_cvd_badge(pos)
                logger.info(f"⏰ [TIMEOUT EXIT] {symbol} hit max hold 6h | PnL: ${net_trade_pnl:.2f} ({r_ret:+.2f}R)")
                send_telegram(
                    f"⏰ <b>[DGT Momentum] Max Hold Timeout (6h)</b>\n"
                    f"<b>Pair:</b> #{symbol}\n"
                    f"<b>Exit Price:</b> {fmt_px(close_px)}\n"
                    f"<b>Total PnL:</b> ${net_trade_pnl:+.2f} ({r_ret:+.2f}R)\n"
                    f"<b>Balance:</b> ${self.balance:.2f}"
                    f"{cvd_exit_badge}"
                )
                pos["exit_px"] = close_px
                pos["exit_time"] = datetime.now(timezone.utc).isoformat()
                pos["net_pnl"] = net_trade_pnl
                pos["net_r"] = r_ret
                pos["status"] = "TIME_CLOSE"
                pos["exit_reason"] = "MAX_HOLD_TIMEOUT"
                self.log_closed_trade(pos)
                del self.open_positions[symbol]
                self.save_state()
                continue

    def run_cycle(self) -> None:
        """One scan cycle across the universe."""
        symbols = self.get_universe()
        logger.info(f"Scanning {len(symbols)} pairs across Nexus universe...")
        
        # 1. Update existing orders and positions first
        self.update_orders_and_positions()
        
        # 2. Scan pairs for new DGT Golden Trio setups
        for sym in symbols:
            try:
                self.evaluate_symbol(sym)
            except Exception as e:
                logger.debug(f"Error evaluating {sym}: {e}")
                
        self.save_state()
        logger.info(f"Cycle completed. Balance: ${self.balance:.2f} | Open: {len(self.open_positions)} | Orders: {len(self.pending_orders)} | Closed: {len(self.closed_trades)}")

    def start(self) -> None:
        logger.info("=== Starting DGT Momentum Paper Trading Bot (M5) ===")
        send_telegram(
            f"🤖 <b>[DGT Momentum Paper Bot Started]</b>\n"
            f"<b>Strategy:</b> DGT Golden Trio + Micro-FVG (M5)\n"
            f"<b>Initial Balance:</b> ${self.balance:.2f}\n"
            f"<b>Risk per Trade:</b> {RISK_PCT*100:.1f}%\n"
            f"<b>BEP Trigger:</b> +1.0R (33% TP1)\n"
            f"<b>Target TP2:</b> +2.5R (Dynamic Trailing)\n"
            f"<b>Universe:</b> All Nexus pairs"
        )
        while True:
            try:
                self.run_cycle()
            except Exception as e:
                logger.error(f"Unexpected error in run_cycle: {e}", exc_info=True)
            time.sleep(SCAN_INTERVAL_SEC)


if __name__ == "__main__":
    bot = DGTMomentumBot()
    bot.start()
