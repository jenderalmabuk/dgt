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
MAX_ORDERS = int(os.getenv("MAX_ORDERS", "10"))
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
        """Load symbols scanned by nexus_scanner from runtime/revo."""
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
                        if sym and sym.endswith("USDT") and sym not in symbols:
                            symbols.append(sym)
                elif path.suffix == ".txt":
                    lines = path.read_text().splitlines()
                    for line in lines:
                        sym = line.strip().replace("/", "").replace(":USDT", "")
                        if sym and sym.endswith("USDT") and sym not in symbols:
                            symbols.append(sym)
                if len(symbols) >= 50:
                    break
            except Exception as e:
                logger.debug(f"Failed to load universe from {path}: {e}")
                
        if not symbols:
            # Fallback default universe
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
        
        # Latest completed bar index is n - 2 (n - 1 is currently forming)
        i = n - 2
        bar_time = str(df["open_time"].iloc[i])
        
        if self.last_candle_checked.get(symbol) == bar_time:
            return
        self.last_candle_checked[symbol] = bar_time

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

        # ── 1. DGT Golden Trio Setup Filter ──
        if not (c[i] > ema21[i] > ema55[i]):
            return
        if v_sma20[i] <= 0 or v[i] < 2.0 * v_sma20[i]:
            return
        if (c[i] - o[i]) < 1.0 * atr14[i]:
            return
            
        bar_range = h[i] - l[i]
        if bar_range <= 0 or (h[i] - c[i]) / bar_range > 0.35:
            return  # Skip long upper wick
            
        # DGT Institutional Volume (1.618 x SMA89)
        if not (v[i] > 1.618 * v_sma89[i]):
            return
        # DGT Anti-Climax Peak (< 4.0 x SMA89)
        if not (v[i] < 4.0 * v_sma89[i]):
            return
        # DGT Volatility Expansion (> 2.0 x ATR11)
        if not (bar_range > 2.0 * atr11[i]):
            return
            
        # ── 2. Micro-FVG on M5 (Low[i] > High[i-2]) ──
        gap_bottom = float(h[i - 2])
        gap_top = float(l[i])
        if not (gap_top > gap_bottom):
            return

        entry_px = float((gap_top + gap_bottom) / 2.0)
        sl_px = float(gap_bottom - 0.25 * atr14[i])
        risk = entry_px - sl_px
        
        if sl_px >= entry_px or risk / entry_px < 0.003:
            return

        tp1_px = float(entry_px + 1.0 * risk)
        tp2_px = float(entry_px + 2.5 * risk)

        # Capital & Sizing (Risk 1% of current balance)
        risk_usd = self.balance * RISK_PCT
        pos_size = risk_usd / risk
        pos_notional = pos_size * entry_px

        order_id = f"ORD_{symbol}_{int(time.time())}"
        order = {
            "order_id": order_id,
            "symbol": symbol,
            "side": "BUY",
            "entry_px": entry_px,
            "sl_px": sl_px,
            "tp1_px": tp1_px,
            "tp2_px": tp2_px,
            "risk": risk,
            "risk_usd": risk_usd,
            "pos_size": pos_size,
            "pos_notional": pos_notional,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": time.time() + (6 * 300), # 30 minutes expiry
            "status": "PENDING",
        }

        if len(self.pending_orders) < MAX_ORDERS:
            self.pending_orders[symbol] = order
            logger.info(f"🎯 [NEW SETUP] {symbol} M5 Micro-FVG Entry: {entry_px:.4f} | SL: {sl_px:.4f} | TP1: {tp1_px:.4f} | TP2: {tp2_px:.4f}")
            send_telegram(
                f"🎯 <b>[DGT Momentum] New Setup</b>\n"
                f"<b>Pair:</b> #{symbol}\n"
                f"<b>Entry (50% FVG):</b> {entry_px:.4f}\n"
                f"<b>Stop Loss:</b> {sl_px:.4f} (-1.0R)\n"
                f"<b>TP 1:</b> {tp1_px:.4f} (+1.0R, 33% + BEP)\n"
                f"<b>TP 2:</b> {tp2_px:.4f} (+2.5R, 67% / Runner)\n"
                f"<b>Risk:</b> ${risk_usd:.2f} ({RISK_PCT*100:.1f}%)"
            )
            self.save_state()

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
            cur_low = float(df["low"].iloc[-1])
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
                
                logger.info(f"🚀 [FILLED] {symbol} Long filled @ {order['entry_px']:.4f}")
                send_telegram(
                    f"🚀 <b>[DGT Momentum] Order Filled</b>\n"
                    f"<b>Pair:</b> #{symbol}\n"
                    f"<b>Entry Price:</b> {order['entry_px']:.4f}\n"
                    f"<b>SL:</b> {order['sl_px']:.4f}\n"
                    f"<b>Position Value:</b> ${pos['pos_notional']:.2f}"
                )
                self.save_state()
                continue
                
            # Check Expiry (30 minutes)
            if now_ts >= order["expires_at"]:
                logger.info(f"⏳ [EXPIRED] Order for {symbol} expired without fill.")
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
                self.balance += (rem_size * close_px) + net_trade_pnl
                
                r_ret = net_trade_pnl / pos["risk_usd"]
                status = "WIN" if net_trade_pnl > 0 else ("BE" if pos["hit_tp1"] else "LOSS")
                
                logger.info(f"🛑 [CLOSED] {symbol} hit SL/BEP @ {close_px:.4f} | PnL: ${net_trade_pnl:.2f} ({r_ret:+.2f}R)")
                send_telegram(
                    f"🛑 <b>[DGT Momentum] Position Closed ({status})</b>\n"
                    f"<b>Pair:</b> #{symbol}\n"
                    f"<b>Exit Price:</b> {close_px:.4f}\n"
                    f"<b>Net PnL:</b> ${net_trade_pnl:+.2f} ({r_ret:+.2f}R)\n"
                    f"<b>Account Balance:</b> ${self.balance:.2f}"
                )
                
                pos["exit_px"] = close_px
                pos["exit_time"] = datetime.now(timezone.utc).isoformat()
                pos["net_pnl"] = net_trade_pnl
                pos["net_r"] = r_ret
                pos["status"] = status
                pos["exit_reason"] = "SL_OR_BEP"
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
                pos["rem_ratio"] -= close_ratio
                pos["cur_sl"] = entry # MOVE SL TO BREAKEVEN!
                
                logger.info(f"💰 [TP1 HIT] {symbol} reached +1.0R! 33% closed, SL moved to BEP {entry:.4f}")
                send_telegram(
                    f"💰 <b>[DGT Momentum] TP1 Hit (+1.0R)</b>\n"
                    f"<b>Pair:</b> #{symbol}\n"
                    f"<b>Price:</b> {close_px:.4f}\n"
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
                    logger.info(f"🔥 [RUNNER MODE] {symbol} passed +2.5R with strong momentum! Trailing to +3.5R with SL @ {pos['cur_sl']:.4f}")
                    send_telegram(
                        f"🔥 <b>[DGT Momentum] Strong Momentum Detected (+2.5R)</b>\n"
                        f"<b>Pair:</b> #{symbol}\n"
                        f"<b>Action:</b> Keeping runner alive!\n"
                        f"<b>SL Locked at:</b> {pos['cur_sl']:.4f} (+2.0R guaranteed!)\n"
                        f"<b>Trailing Target:</b> {pos['tp3_px']:.4f} (+3.5R)"
                    )
                else:
                    # Full close remaining 67% at +2.5R
                    close_px = pos["tp2_px"]
                    rem_size = pos["pos_size"] * pos["rem_ratio"]
                    pnl_portion = rem_size * (close_px - entry)
                    fee = rem_size * close_px * MAKER_FEE
                    net_trade_pnl = pos["realized_pnl"] + pnl_portion - fee
                    
                    self.balance += (rem_size * close_px) + net_trade_pnl
                    r_ret = net_trade_pnl / pos["risk_usd"]
                    
                    logger.info(f"🏆 [FULL TP2] {symbol} closed remaining at +2.5R | Total PnL: ${net_trade_pnl:.2f} ({r_ret:+.2f}R)")
                    send_telegram(
                        f"🏆 <b>[DGT Momentum] Full TP2 Target Hit!</b>\n"
                        f"<b>Pair:</b> #{symbol}\n"
                        f"<b>Exit Price:</b> {close_px:.4f} (+2.5R)\n"
                        f"<b>Net PnL:</b> ${net_trade_pnl:+.2f} ({r_ret:+.2f}R)\n"
                        f"<b>Balance:</b> ${self.balance:.2f}"
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

            # ── D. RUNNER Phase: Trailing Swing Low & Emergency DGT Red Light ──
            if pos.get("stage") == "RUNNER":
                # Emergency DGT Red Light Exit (Exhaustion volume spike)
                if dgt_red_light:
                    close_px = cur_close
                    rem_size = pos["pos_size"] * pos["rem_ratio"]
                    pnl_portion = rem_size * (close_px - entry)
                    fee = rem_size * close_px * TAKER_FEE
                    net_trade_pnl = pos["realized_pnl"] + pnl_portion - fee
                    self.balance += (rem_size * close_px) + net_trade_pnl
                    r_ret = net_trade_pnl / pos["risk_usd"]
                    
                    logger.info(f"🚦 [RED LIGHT EMERGENCY EXIT] {symbol} volume spike! Climax exit @ {close_px:.4f} | PnL: ${net_trade_pnl:.2f}")
                    send_telegram(
                        f"🚦 <b>[DGT Momentum] DGT Red Light Exhaustion!</b>\n"
                        f"<b>Pair:</b> #{symbol}\n"
                        f"<b>Warning:</b> Extreme volume spike detected. Emergency exit at top!\n"
                        f"<b>Exit Price:</b> {close_px:.4f}\n"
                        f"<b>Total PnL:</b> ${net_trade_pnl:+.2f} ({r_ret:+.2f}R)"
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

                # Trailing Stop: Update to Swing Low if higher
                if swing_low_3 > pos["cur_sl"] and swing_low_3 < cur_close:
                    pos["cur_sl"] = swing_low_3
                    logger.info(f"📈 [TRAILING UPDATE] {symbol} SL raised to Swing Low: {swing_low_3:.4f}")

                # Check Max Target TP3 (+3.5R)
                if cur_high >= pos.get("tp3_px", entry + 3.5 * risk):
                    close_px = pos.get("tp3_px", entry + 3.5 * risk)
                    rem_size = pos["pos_size"] * pos["rem_ratio"]
                    pnl_portion = rem_size * (close_px - entry)
                    fee = rem_size * close_px * MAKER_FEE
                    net_trade_pnl = pos["realized_pnl"] + pnl_portion - fee
                    self.balance += (rem_size * close_px) + net_trade_pnl
                    r_ret = net_trade_pnl / pos["risk_usd"]
                    
                    logger.info(f"🚀 [TP3 MAX HIT] {symbol} reached +3.5R max target! Total PnL: ${net_trade_pnl:.2f}")
                    send_telegram(
                        f"🚀 <b>[DGT Momentum] Mega-Pump TP3 Reached (+3.5R)!</b>\n"
                        f"<b>Pair:</b> #{symbol}\n"
                        f"<b>Exit Price:</b> {close_px:.4f}\n"
                        f"<b>Net PnL:</b> ${net_trade_pnl:+.2f} ({r_ret:+.2f}R)\n"
                        f"<b>Balance:</b> ${self.balance:.2f}"
                    )
                    pos["exit_px"] = close_px
                    pos["exit_time"] = datetime.now(timezone.utc).isoformat()
                    pos["net_pnl"] = net_trade_pnl
                    pos["net_r"] = r_ret
                    pos["status"] = "WIN"
                    pos["exit_reason"] = "TP3_MAX"
                    self.log_closed_trade(pos)
                    del self.open_positions[symbol]
                    self.save_state()
                    continue

            # ── E. Max Hold Expiry (6 hours) ──
            if now_ts - pos.get("filled_ts", now_ts) > 6 * 3600:
                close_px = cur_close
                rem_size = pos["pos_size"] * pos["rem_ratio"]
                pnl_portion = rem_size * (close_px - entry)
                fee = rem_size * close_px * TAKER_FEE
                net_trade_pnl = pos["realized_pnl"] + pnl_portion - fee
                self.balance += (rem_size * close_px) + net_trade_pnl
                r_ret = net_trade_pnl / pos["risk_usd"]
                
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
