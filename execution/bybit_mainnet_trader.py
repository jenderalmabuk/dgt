"""
Bybit Mainnet Trader for SignalCopy with Dynamic Exit Layer.

Real money execution on Bybit USDT Perpetual Futures with:
- Fixed $2 risk per trade
- Dynamic exits: SCRATCH, DAMAGE_REDUCER, PROFIT_LOCK, TRAILING
- Provider SL/TP preservation
- Multi-exchange quote freshness
"""
import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Dict, Optional, Any

from execution.bybit_api_client import BybitClient, BybitAPIError


logger = logging.getLogger(__name__)


class BybitMainnetTrader:
    """
    Real money trader for Bybit USDT perpetual futures.
    
    Features:
    - Fixed risk per trade (default $2)
    - Dynamic exit layer (scratch/damage/profit/trailing)
    - Position lifecycle management
    - Journal logging
    """
    
    # Dynamic exit epoch marker - trades before this are pre-epoch
    DYNAMIC_EXIT_EPOCH_UTC = "2026-09-06T12:43:00Z"
    
    def __init__(self, journal_path: str = "/app/journal"):
        """Initialize Bybit mainnet trader."""
        self.client = BybitClient()
        self.journal_path = Path(journal_path)
        self.journal_path.mkdir(parents=True, exist_ok=True)
        
        # Configuration from env
        self.capital = float(os.getenv("SIGNALCOPY_CAPITAL", "100"))
        self.risk_per_trade = float(os.getenv("SIGNALCOPY_RISK_PER_TRADE_USD", "2"))
        self.max_leverage = int(os.getenv("SIGNALCOPY_MAX_LEVERAGE", "10"))
        self.max_concurrent = int(os.getenv("SIGNALCOPY_MAX_CONCURRENT", "5"))
        
        # Dynamic exit configuration
        self.dynamic_exit_enabled = self._config_bool("SIGNALCOPY_BYBIT_DYNAMIC_EXIT", default=True)
        
        # Load positions and state
        self.positions: Dict[str, Dict] = {}
        self._load_positions()
        
        logger.info(
            f"[BYBIT_MAINNET] Initialized: capital=${self.capital}, "
            f"risk=${self.risk_per_trade}/trade, max_leverage={self.max_leverage}x, "
            f"max_concurrent={self.max_concurrent}, dynamic_exit={self.dynamic_exit_enabled}"
        )
    
    def _config_bool(self, key: str, default: bool = False) -> bool:
        """Get boolean config from env."""
        val = os.getenv(key, "").lower()
        if not val:
            return default
        return val in ("1", "true", "yes", "on")
    
    async def _get_fresh_open_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Fresh Bybit quote with receipt-time provenance.
        
        Returns dict with price, source, timestamp, freshness flag.
        Gateway uses this for price validation before entry.
        """
        try:
            ticker = self.client.get_ticker(symbol)
            if ticker and "lastPrice" in ticker:
                price = float(ticker["lastPrice"])
                if price > 0:
                    return {
                        "price": price,
                        "price_source": "bybit_usdt_perp",
                        "price_observed_at": datetime.now(timezone.utc).isoformat(),
                        "price_age_sec": 0.0,
                        "price_fresh": True
                    }
        except Exception as e:
            logger.warning(f"[BYBIT_MAINNET] Fresh open price fetch failed {symbol}: {e}")
        return None
    
    async def _get_fresh_close_quote(self, symbol: str) -> Dict[str, float]:
        """
        Get fresh mark price for close/trailing operations.
        
        Returns dict with mark_price for dynamic exit calculations.
        """
        try:
            ticker = self.client.get_ticker(symbol)
            if ticker and "markPrice" in ticker:
                mark_price = float(ticker["markPrice"])
                if mark_price > 0:
                    return {
                        "mark_price": mark_price,
                        "last_price": float(ticker.get("lastPrice", mark_price)),
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }
        except Exception as e:
            logger.warning(f"[BYBIT_MAINNET] Fresh close quote fetch failed {symbol}: {e}")
        
        # Fallback to last price if mark price not available
        try:
            ticker = self.client.get_ticker(symbol)
            if ticker and "lastPrice" in ticker:
                price = float(ticker["lastPrice"])
                return {
                    "mark_price": price,
                    "last_price": price,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
        except:
            pass
        
        raise Exception(f"Failed to get fresh close quote for {symbol}")
    
    async def _get_fresh_open_price(self, symbol: str) -> tuple[float, str]:
        """Get fresh open price for entry execution."""
        quote = await self._get_fresh_open_quote(symbol)
        if quote:
            return (float(quote["price"]), str(quote["price_source"]))
        return (0.0, "fresh_price_unavailable")
    
    @staticmethod
    def _lifecycle_price(quote: Dict[str, Any]) -> Optional[float]:
        """Return only fresh multi-exchange lifecycle truth."""
        if not isinstance(quote, dict) or quote.get("price_fresh") is not True:
            return None
        price = float(quote.get("price") or 0)
        return price if price > 0 else None
    
    def _load_positions(self):
        """Load open positions from journal."""
        positions_file = self.journal_path / "open_positions.json"
        if positions_file.exists():
            try:
                data = json.loads(positions_file.read_text())
                # Handle both dict (symbol->position) and list formats
                if isinstance(data, dict):
                    self.positions = {k: v for k, v in data.items() if v.get("exchange") == "bybit"}
                else:
                    self.positions = {p["symbol"]: p for p in data if p.get("exchange") == "bybit"}
                logger.info(f"[BYBIT_MAINNET] Loaded {len(self.positions)} positions from journal")
            except Exception as e:
                logger.error(f"[BYBIT_MAINNET] Failed to load positions: {e}")
                self.positions = {}
    
    def _save_positions(self):
        """Save positions to journal."""
        positions_file = self.journal_path / "open_positions.json"
        try:
            # Load existing positions from other exchanges
            existing = {}
            if positions_file.exists():
                data = json.loads(positions_file.read_text())
                # Handle both dict and list formats
                if isinstance(data, dict):
                    existing = {k: v for k, v in data.items() if v.get("exchange") != "bybit"}
                else:
                    # Convert list to dict
                    existing = {p["symbol"]: p for p in data if p.get("exchange") != "bybit"}
            
            # Merge our positions
            all_positions = {**existing, **self.positions}
            
            # Write atomically
            tmp_file = positions_file.with_suffix(".tmp")
            tmp_file.write_text(json.dumps(all_positions, indent=2))
            tmp_file.replace(positions_file)
            
        except Exception as e:
            logger.error(f"[BYBIT_MAINNET] Failed to save positions: {e}")
    
    def _append_history(self, trade: Dict):
        """Append closed trade to history."""
        history_file = self.journal_path / "trade_history.json"
        try:
            history = []
            if history_file.exists():
                history = json.loads(history_file.read_text())
            
            history.append(trade)
            
            tmp_file = history_file.with_suffix(".tmp")
            tmp_file.write_text(json.dumps(history, indent=2))
            tmp_file.replace(history_file)
            
        except Exception as e:
            logger.error(f"[BYBIT_MAINNET] Failed to append history: {e}")
    
    def _quantize(self, value: float, tick_size: float) -> str:
        """Round value to tick size."""
        if tick_size >= 1:
            precision = 0
        else:
            precision = len(str(tick_size).rstrip('0').split('.')[-1])
        
        d = Decimal(str(value))
        quantized = d.quantize(Decimal(str(tick_size)), rounding=ROUND_DOWN)
        return f"{quantized:.{precision}f}"
    
    async def _fetch_fresh_quote(self, symbol: str) -> Optional[Dict[str, float]]:
        """Fetch fresh multi-exchange quote."""
        try:
            ticker = self.client.get_ticker(symbol)
            
            mark = float(ticker.get("markPrice", 0))
            bid = float(ticker.get("bid1Price", 0))
            ask = float(ticker.get("ask1Price", 0))
            
            if not all([mark, bid, ask]):
                logger.warning(f"[BYBIT_MAINNET] Incomplete ticker for {symbol}")
                return None
            
            return {
                "mark": mark,
                "bid": bid,
                "ask": ask,
                "timestamp": time.time()
            }
        
        except Exception as e:
            logger.error(f"[BYBIT_MAINNET] Failed to fetch quote for {symbol}: {e}")
            return None
    
    def _calculate_position_size(
        self,
        symbol: str,
        entry: float,
        sl: float,
        lot_size: float
    ) -> tuple[str, float]:
        """
        Calculate position size for fixed risk.
        
        Returns: (qty_str, notional_usd)
        """
        # Risk distance in price
        risk_distance = abs(entry - sl)
        
        if risk_distance == 0:
            logger.error(f"[BYBIT_MAINNET] {symbol} SL == entry, cannot size")
            return ("0", 0.0)
        
        # Qty for fixed risk
        qty_for_risk = self.risk_per_trade / risk_distance
        
        # Round down to lot size
        qty_rounded = int(qty_for_risk / lot_size) * lot_size
        
        if qty_rounded <= 0:
            logger.warning(f"[BYBIT_MAINNET] {symbol} qty rounded to 0 (risk_distance={risk_distance:.4f})")
            return ("0", 0.0)
        
        # Quantize
        qty_str = self._quantize(qty_rounded, lot_size)
        notional = qty_rounded * entry
        
        return (qty_str, notional)
    
    async def open_position(
        self,
        symbol: str,
        side: str,  # "LONG" or "SHORT"
        entry_price: float,
        sl_price: float,
        tp_prices: list,
        signal_metadata: Dict = None
    ) -> bool:
        """
        Open new position with fixed risk.
        
        Args:
            symbol: Trading pair (e.g. "BTCUSDT")
            side: "LONG" or "SHORT"
            entry_price: Entry price from signal
            sl_price: Stop loss from signal
            tp_prices: List of TP levels from signal
            signal_metadata: Original signal data
        
        Returns: True if position opened successfully
        """
        try:
            # Check capacity
            if len(self.positions) >= self.max_concurrent:
                logger.warning(
                    f"[BYBIT_MAINNET] Max concurrent reached ({self.max_concurrent}), "
                    f"rejecting {symbol}"
                )
                return False
            
            # Get instrument info
            instrument = self.client.get_instrument_info(symbol)
            lot_size = float(instrument["lotSizeFilter"]["qtyStep"])
            tick_size = float(instrument["priceFilter"]["tickSize"])
            
            # Calculate position size
            qty_str, notional = self._calculate_position_size(symbol, entry_price, sl_price, lot_size)
            
            if qty_str == "0":
                logger.error(f"[BYBIT_MAINNET] {symbol} size calculation failed")
                return False
            
            # Check leverage
            required_margin = notional / self.max_leverage
            logger.info(
                f"[BYBIT_MAINNET] {symbol} {side}: qty={qty_str}, "
                f"notional=${notional:.2f}, margin=${required_margin:.2f}"
            )
            
            # Quantize prices
            entry_str = self._quantize(entry_price, tick_size)
            sl_str = self._quantize(sl_price, tick_size)
            tp1_str = self._quantize(tp_prices[0], tick_size) if tp_prices else None
            
            # Determine order type (market vs limit)
            quote = await self._fetch_fresh_quote(symbol)
            if not quote:
                logger.error(f"[BYBIT_MAINNET] {symbol} no quote available")
                return False
            
            mark = quote["mark"]
            order_type = "Market"  # Start with market for simplicity
            
            # Place order
            bybit_side = "Buy" if side == "LONG" else "Sell"
            
            order_result = self.client.create_order(
                symbol=symbol,
                side=bybit_side,
                order_type=order_type,
                qty=qty_str,
                price=entry_str if order_type == "Limit" else None,
                stop_loss=sl_str,
                take_profit=tp1_str,
                order_link_id=f"SC_{symbol}_{int(time.time() * 1000)}"
            )
            
            order_id = order_result.get("orderId")
            
            if not order_id:
                logger.error(f"[BYBIT_MAINNET] {symbol} order failed: {order_result}")
                return False
            
            # Store position
            now = datetime.now(timezone.utc)
            position = {
                "symbol": symbol,
                "exchange": "bybit",
                "side": side,
                "entry_price": entry_price,
                "sl_price": sl_price,
                "tp_prices": tp_prices,
                "qty": qty_str,
                "notional": notional,
                "order_id": order_id,
                "opened_at": now.isoformat(),
                "opened_ts": now.timestamp(),
                "sl_kind": "ORIGINAL",
                "locked_profit": False,
                "metadata": signal_metadata or {}
            }
            
            self.positions[symbol] = position
            self._save_positions()
            
            logger.info(
                f"[BYBIT_MAINNET] OPENED {side} {symbol} @ {entry_price:.6f} | "
                f"qty={qty_str} | SL={sl_price:.6f} | TP1={tp_prices[0]:.6f}"
            )
            
            # Send entry notification (rich format)
            try:
                from signal_copy.telegram_formatter import build_execution_message
                from signal_copy.telegram_transport import send_trades_notification
                
                msg = build_execution_message({
                    "symbol": symbol,
                    "side": side,
                    "entry_price": entry_price,
                    "sl_price": sl_price,
                    "tp_prices": tp_prices,
                    "notional": notional,
                    "qty": float(qty_str),
                    "status": "MARKET_FILL",
                    "exchange": "bybit",
                })
                await send_trades_notification(msg)
            except Exception as notif_err:
                logger.warning(f"[BYBIT_MAINNET] Notification failed: {notif_err}")
            
            return True
            
        except Exception as e:
            logger.error(f"[BYBIT_MAINNET] Failed to open {symbol}: {e}")
            return False
    
    async def submit_open(self, timeout_sec: float = 30.0, **params) -> Optional[Dict[str, Any]]:
        """
        Gateway-compatible entry point for opening positions.
        Validates params and delegates to open_position().
        
        Returns dict with execution status for gateway.
        """
        try:
            symbol = str(params.get("symbol", ""))
            side = str(params.get("side", "")).upper()
            sl_price = float(params.get("sl", params.get("sl_price", 0)) or 0)
            
            # Parse TP ladder
            tp_ladder = []
            for i in range(1, 21):
                value = float(params.get(f"tp{i}", 0) or 0)
                if value:
                    tp_ladder.append(value)
            if not tp_ladder and params.get("tp_full"):
                tp_ladder.append(float(params["tp_full"]))
            
            # Validate basic params
            if not symbol or side not in ("LONG", "SHORT"):
                logger.warning(f"[BYBIT_MAINNET] Invalid params symbol={symbol} side={side}")
                return {"ok": False, "executed": False, "code": "INVALID_PARAMS",
                        "reason": "Invalid symbol or side"}
            
            # Check if already open
            if symbol in self.positions:
                logger.info(f"[BYBIT_MAINNET] {symbol} already open — skip dup")
                return {"ok": False, "executed": False, "code": "ALREADY_OPEN",
                        "reason": f"{symbol} already open"}
            
            # Validate price quote
            quote = params.get("execution_price_quote")
            if not isinstance(quote, dict) or quote.get("price_fresh") is not True:
                return {"ok": False, "executed": False, "code": "PRICE_PROVENANCE_REQUIRED",
                        "reason": "Fresh price quote required"}
            
            try:
                observed_at = datetime.fromisoformat(str(quote["price_observed_at"]).replace("Z", "+00:00"))
                age_sec = (datetime.now(timezone.utc) - observed_at.astimezone(timezone.utc)).total_seconds()
                mark = float(quote["price"])
                mark_source = str(quote["price_source"])
            except (KeyError, TypeError, ValueError):
                return {"ok": False, "executed": False, "code": "INVALID_QUOTE",
                        "reason": "Invalid price quote format"}
            
            if mark <= 0 or age_sec > 30.0:
                return {"ok": False, "executed": False, "code": "STALE_GATEWAY_MARK",
                        "reason": f"Stale mark: age={age_sec:.1f}s source={mark_source}"}
            
            requested_entry = float(params.get("entry_price", 0) or 0)
            
            # Check if entry diverges too much from mark
            if requested_entry > 0 and mark > 0 and abs(mark - requested_entry) / requested_entry > 0.05:
                logger.warning(f"[BYBIT_MAINNET] {symbol} stale mark {mark:.6f} vs route {requested_entry:.6f}")
                return {"ok": False, "executed": False, "code": "PRICE_DIVERGENCE",
                        "reason": f"Mark {mark:.6f} vs entry {requested_entry:.6f} >5%"}
            
            # Use requested entry or mark
            entry_price = requested_entry if requested_entry > 0 else mark
            
            # Sanity: check SL hasn't been hit already
            if side == "LONG" and sl_price and mark <= sl_price:
                logger.warning(f"[BYBIT_MAINNET] {symbol} LONG mark {mark:.6f} <= SL {sl_price:.6f}")
                return {"ok": False, "executed": False, "code": "SL_ALREADY_HIT",
                        "reason": f"Mark {mark:.6f} already at/below SL {sl_price:.6f}"}
            if side == "SHORT" and sl_price and mark >= sl_price:
                logger.warning(f"[BYBIT_MAINNET] {symbol} SHORT mark {mark:.6f} >= SL {sl_price:.6f}")
                return {"ok": False, "executed": False, "code": "SL_ALREADY_HIT",
                        "reason": f"Mark {mark:.6f} already at/above SL {sl_price:.6f}"}
            
            # Filter TPs that haven't been hit
            tp_ladder = [tp for tp in tp_ladder
                        if (tp > mark if side == "LONG" else tp < mark)]
            
            if not tp_ladder:
                logger.warning(f"[BYBIT_MAINNET] {symbol} {side} all TPs already passed")
                return {"ok": False, "executed": False, "code": "ALL_TPS_PASSED",
                        "reason": "All take profit levels already passed"}
            
            # Sanity: SL must be on correct side of entry
            if side == "LONG" and sl_price >= entry_price:
                logger.warning(f"[BYBIT_MAINNET] {symbol} LONG SL {sl_price:.6f} >= entry {entry_price:.6f}")
                return {"ok": False, "executed": False, "code": "INVALID_SL",
                        "reason": "SL must be below entry for LONG"}
            if side == "SHORT" and sl_price and sl_price <= entry_price:
                logger.warning(f"[BYBIT_MAINNET] {symbol} SHORT SL {sl_price:.6f} <= entry {entry_price:.6f}")
                return {"ok": False, "executed": False, "code": "INVALID_SL",
                        "reason": "SL must be above entry for SHORT"}
            
            # Attempt to open position
            success = await self.open_position(
                symbol=symbol,
                side=side,
                entry_price=entry_price,
                sl_price=sl_price,
                tp_prices=tp_ladder,
                signal_metadata=params
            )
            
            if success:
                return {
                    "ok": True,
                    "executed": True,
                    "symbol": symbol,
                    "side": side,
                    "entry_price": entry_price,
                    "sl_price": sl_price,
                    "tp_prices": tp_ladder
                }
            else:
                return {"ok": False, "executed": False, "code": "EXECUTION_FAILED",
                        "reason": "Failed to execute order on exchange"}
                
        except Exception as e:
            logger.error(f"[BYBIT_MAINNET] submit_open error: {e}", exc_info=True)
            return {"ok": False, "executed": False, "code": "EXCEPTION",
                    "reason": str(e)}
    
    async def _close(
        self,
        symbol: str,
        reason: str,
        sl_kind: str = None,
        exit_price: float = None
    ):
        """Close position and log to journal."""
        pos = self.positions.get(symbol)
        if not pos:
            logger.warning(f"[BYBIT_MAINNET] Cannot close {symbol}: not in positions")
            return
        
        try:
            # Get current price if not provided
            if exit_price is None:
                quote = await self._fetch_fresh_quote(symbol)
                if quote:
                    exit_price = quote["mark"]
                else:
                    logger.warning(f"[BYBIT_MAINNET] {symbol} no quote for close, using entry")
                    exit_price = pos["entry_price"]
            
            # Calculate PnL
            qty = float(pos["qty"])
            entry = pos["entry_price"]
            side = pos["side"]
            
            if side == "LONG":
                pnl_usd = qty * (exit_price - entry)
            else:
                pnl_usd = qty * (entry - exit_price)
            
            pnl_pct = (pnl_usd / (qty * entry)) * 100
            
            # Close position on exchange
            bybit_side = "Sell" if side == "LONG" else "Buy"
            
            close_result = self.client.create_order(
                symbol=symbol,
                side=bybit_side,
                order_type="Market",
                qty=pos["qty"],
                reduce_only=True,
                order_link_id=f"SC_CLOSE_{symbol}_{int(time.time() * 1000)}"
            )
            
            # Log close
            now = datetime.now(timezone.utc)
            opened_at = datetime.fromisoformat(pos["opened_at"].replace("Z", "+00:00"))
            hold_minutes = (now - opened_at).total_seconds() / 60
            
            trade = {
                "symbol": symbol,
                "exchange": "bybit",
                "side": side,
                "entry_price": entry,
                "exit_price": exit_price,
                "qty": pos["qty"],
                "pnl_usd": round(pnl_usd, 2),
                "pnl_pct": round(pnl_pct, 2),
                "close_reason": reason,
                "sl_kind": sl_kind or pos.get("sl_kind", "ORIGINAL"),
                "opened_at": pos["opened_at"],
                "closed_at": now.isoformat(),
                "hold_minutes": round(hold_minutes, 1),
                "metadata": pos.get("metadata", {})
            }
            
            self._append_history(trade)
            
            # Remove from positions
            self.positions.pop(symbol, None)
            self._save_positions()
            
            logger.info(
                f"[BYBIT_MAINNET] CLOSED {side} {symbol} @ {exit_price:.4f} | "
                f"{reason} | PnL ${pnl_usd:+.2f} ({pnl_pct:+.2f}%) | hold {hold_minutes:.0f}m"
            )
            
            # Send close notification (rich format)
            try:
                from signal_copy.telegram_formatter import build_close_message
                from signal_copy.telegram_transport import send_trades_notification
                
                msg = build_close_message({
                    "symbol": symbol,
                    "side": side,
                    "exit_price": exit_price,
                    "pnl_amount": pnl_usd,
                    "pnl_pct": pnl_pct,
                    "hold_minutes": hold_minutes,
                    "reason": reason,
                    "normalized_reason": reason,
                    "sl_kind": sl_kind if sl_kind else "NONE",
                    "exchange": "bybit",
                })
                await send_trades_notification(msg)
            except Exception as notif_err:
                logger.warning(f"[BYBIT_MAINNET] Close notification failed: {notif_err}")
        
        except Exception as e:
            logger.exception(f"[BYBIT_MAINNET] Close {symbol} failed: {e}")
    
    async def sync_positions(self):
        """Sync local positions with exchange reality."""
        try:
            exchange_positions = self.client.get_positions()
            
            # Map exchange positions
            exchange_map = {
                p["symbol"]: p for p in exchange_positions
                if float(p.get("size", 0)) > 0
            }
            
            # Check for missing positions (closed externally)
            for symbol in list(self.positions.keys()):
                if symbol not in exchange_map:
                    logger.warning(
                        f"[BYBIT_MAINNET] {symbol} closed externally, "
                        f"removing from local state"
                    )
                    await self._close(symbol, "EXTERNAL_CLOSE")
            
            # Check for new positions (opened externally)
            for symbol, ex_pos in exchange_map.items():
                if symbol not in self.positions:
                    logger.warning(
                        f"[BYBIT_MAINNET] {symbol} found on exchange but not in local state, "
                        f"importing..."
                    )
                    # Import external position
                    side = "LONG" if ex_pos["side"] == "Buy" else "SHORT"
                    self.positions[symbol] = {
                        "symbol": symbol,
                        "exchange": "bybit",
                        "side": side,
                        "entry_price": float(ex_pos["avgPrice"]),
                        "sl_price": float(ex_pos.get("stopLoss", 0)) or 0,
                        "tp_prices": [float(ex_pos.get("takeProfit", 0))] if ex_pos.get("takeProfit") else [],
                        "qty": ex_pos["size"],
                        "notional": float(ex_pos["positionValue"]),
                        "opened_at": datetime.now(timezone.utc).isoformat(),
                        "opened_ts": time.time(),
                        "sl_kind": "ORIGINAL",
                        "locked_profit": False,
                        "metadata": {"imported": True}
                    }
            
            # Always save to update journal timestamp (for shadow watchdog)
            self._save_positions()
        
        except Exception as e:
            logger.error(f"[BYBIT_MAINNET] Position sync failed: {e}")
    
    async def check_and_execute_exits(self):
        """Check all positions for exit conditions (SL/TP/dynamic)."""
        for symbol in list(self.positions.keys()):
            pos = self.positions[symbol]
            
            # Fetch fresh quote
            quote = await self._fetch_fresh_quote(symbol)
            if not quote:
                continue
            
            mark = quote["mark"]
            
            # Check dynamic exits first
            if self.dynamic_exit_enabled:
                if await self._apply_dynamic_exits(symbol, pos, mark):
                    continue  # Position closed, skip SL/TP check
            
            # Check provider SL
            sl = pos.get("sl_price", 0)
            if sl and self._level_hit(pos, mark, sl, favorable=False):
                await self._close(symbol, "HARD_SL", sl_kind=pos.get("sl_kind", "ORIGINAL"), exit_price=mark)
                continue
            
            # Check TP levels
            tp_prices = pos.get("tp_prices", [])
            for i, tp in enumerate(tp_prices):
                if self._level_hit(pos, mark, tp, favorable=True):
                    await self._close(symbol, f"TP{i+1}", exit_price=mark)
                    break
    
    def _level_hit(self, pos: Dict, mark: float, level: float, favorable: bool) -> bool:
        """Check if price level hit."""
        if level == 0:
            return False
        
        side = pos["side"]
        
        if favorable:
            # TP hit
            if side == "LONG":
                return mark >= level
            else:
                return mark <= level
        else:
            # SL hit
            if side == "LONG":
                return mark <= level
            else:
                return mark >= level
    
    async def _apply_dynamic_exits(self, symbol: str, pos: Dict, mark: float) -> bool:
        """
        Apply dynamic exit layer (SCRATCH/DAMAGE/PROFIT/TRAILING).
        
        Returns True if position was closed.
        """
        # Skip pre-epoch positions
        opened_at_str = pos.get("opened_at", "")
        if opened_at_str < self.DYNAMIC_EXIT_EPOCH_UTC:
            return False
        
        # Parse opened timestamp
        try:
            opened_at = datetime.fromisoformat(opened_at_str.replace("Z", "+00:00"))
        except:
            logger.warning(f"[DYN_EXIT] {symbol} invalid opened_at: {opened_at_str}")
            return False
        
        now = datetime.now(timezone.utc)
        hold_minutes = (now - opened_at).total_seconds() / 60
        
        # Calculate unrealized PnL
        entry = pos["entry_price"]
        qty = float(pos["qty"])
        side = pos["side"]
        
        if side == "LONG":
            unrealized_usd = qty * (mark - entry)
        else:
            unrealized_usd = qty * (entry - mark)
        
        unrealized_pct = (unrealized_usd / (qty * entry)) * 100
        
        # Log heartbeat
        logger.info(
            f"[DYN_EXIT_CHECK] {symbol} {side} | "
            f"opened {hold_minutes:.0f}m ago | pnl {unrealized_pct:+.2f}%"
        )
        
        # LAYER 1: SCRATCH_EXIT (kill zombies)
        if self._config_bool("SCRATCH_EXIT_ENABLED", default=True):
            scratch_min = float(os.getenv("SCRATCH_EXIT_MIN_HOLD_MINUTES", "45"))
            scratch_max_abs_pnl = float(os.getenv("SCRATCH_EXIT_MAX_ABS_PNL_PCT", "0.4"))
            
            if hold_minutes >= scratch_min and abs(unrealized_pct) <= scratch_max_abs_pnl:
                logger.info(f"[SCRATCH_EXIT] {symbol} stagnant near BE")
                await self._close(symbol, "SCRATCH_EXIT", sl_kind=pos.get("sl_kind", "ORIGINAL"), exit_price=mark)
                return True
        
        # LAYER 2: DAMAGE_REDUCER (cut chronic losers)
        if self._config_bool("DAMAGE_REDUCER_ENABLED", default=True):
            damage_min = float(os.getenv("DAMAGE_REDUCER_MIN_HOLD_MINUTES", "45"))
            damage_max_loss = float(os.getenv("DAMAGE_REDUCER_MAX_LOSS_PCT", "-2.5"))
            
            if hold_minutes >= damage_min and unrealized_pct <= damage_max_loss:
                logger.info(f"[DAMAGE_REDUCER] {symbol} chronic bleeder")
                await self._close(symbol, "DAMAGE_REDUCER", sl_kind=pos.get("sl_kind", "ORIGINAL"), exit_price=mark)
                return True
        
        # LAYER 3: PROFIT_LOCK (move SL to BE+buffer)
        if self._config_bool("PROFIT_LOCK_ENABLED", default=True):
            if not pos.get("locked_profit", False):
                lock_min = float(os.getenv("PROFIT_LOCK_MIN_MINUTES", "15"))
                lock_trigger = float(os.getenv("PROFIT_LOCK_TRIGGER_PCT", "1.0"))
                lock_buffer = float(os.getenv("PROFIT_LOCK_BUFFER_PCT", "0.3"))
                
                if hold_minutes >= lock_min and unrealized_pct >= lock_trigger:
                    # Calculate new SL (breakeven + buffer)
                    sl = pos.get("sl_price", 0)
                    risk_distance = abs(entry - sl)
                    buffer_distance = risk_distance * (lock_buffer / 100)
                    
                    if side == "LONG":
                        new_sl = entry + buffer_distance
                        # Safety: don't set too tight
                        if new_sl >= mark * 0.998:
                            new_sl = mark * 0.995
                    else:
                        new_sl = entry - buffer_distance
                        if new_sl <= mark * 1.002:
                            new_sl = mark * 1.005
                    
                    # Update SL on exchange
                    try:
                        self.client.set_trading_stop(
                            symbol=symbol,
                            stop_loss=self._quantize(new_sl, 0.01)  # TODO: use real tick size
                        )
                        
                        pos["sl_price"] = new_sl
                        pos["sl_kind"] = "BREAKEVEN"
                        pos["locked_profit"] = True
                        self._save_positions()
                        
                        logger.info(
                            f"[PROFIT_LOCK] {symbol} locked! SL moved {sl:.4f} → {new_sl:.4f}"
                        )
                    except Exception as e:
                        logger.error(f"[PROFIT_LOCK] {symbol} failed to update SL: {e}")
        
        # LAYER 4: TRAILING (adaptive trailing after lock)
        # Activate after 1.5R profit, trail 0.6R from peak (shadow proven config)
        for symbol, pos in list(self.positions.items()):
            if not pos.get("locked_profit"):
                continue
            
            # Get fresh mark price for this symbol
            try:
                quote = await self._get_fresh_close_quote(symbol)
                mark = quote["mark_price"]
            except Exception as e:
                logger.warning(f"[TRAILING_LOCK] {symbol} failed to get mark price: {e}")
                continue
            
            side = pos["side"]
            entry = pos["entry_price"]
            sl = pos.get("sl_price", 0)
            risk_distance = abs(entry - sl) if sl else abs(entry * 0.01)
            
            # Track high/low watermark (initialize with current mark if missing)
            if "high_watermark" not in pos:
                pos["high_watermark"] = mark
            if "low_watermark" not in pos:
                pos["low_watermark"] = mark
            
            # Update watermarks
            if side == "LONG":
                pos["high_watermark"] = max(pos["high_watermark"], mark)
                peak = pos["high_watermark"]
            else:
                pos["low_watermark"] = min(pos["low_watermark"], mark)
                peak = pos["low_watermark"]
            
            # Calculate profit in R
            if side == "LONG":
                profit_r = (mark - entry) / risk_distance if risk_distance else 0
            else:
                profit_r = (entry - mark) / risk_distance if risk_distance else 0
            
            # Trailing activation threshold: 1.5R
            if profit_r < 1.5:
                continue
            
            # Calculate trailing SL: peak - 0.6R
            trail_distance = risk_distance * 0.6
            
            if side == "LONG":
                trailing_sl = peak - trail_distance
                # Only move SL up (never down)
                if trailing_sl > sl:
                    new_sl = trailing_sl
                else:
                    continue
            else:
                trailing_sl = peak + trail_distance
                # Only move SL down (never up)
                if trailing_sl < sl:
                    new_sl = trailing_sl
                else:
                    continue
            
            # Update SL on exchange
            try:
                self.client.set_trading_stop(
                    symbol=symbol,
                    stop_loss=self._quantize(new_sl, 0.01)
                )
                
                pos["sl_price"] = new_sl
                pos["sl_kind"] = "TRAILING"
                self._save_positions()
                
                logger.info(
                    f"[TRAILING_LOCK] {symbol} {side} | SL {sl:.4f} → {new_sl:.4f} | "
                    f"peak={peak:.4f} trail_dist={trail_distance:.4f} profit={profit_r:.2f}R"
                )
            except Exception as e:
                logger.error(f"[TRAILING_LOCK] {symbol} failed to update SL: {e}")
        
        return False
    
    async def run_maintenance_loop(self):
        """Main maintenance loop - check exits every 3s."""
        logger.info("[BYBIT_MAINNET] Starting maintenance loop")
        
        while True:
            try:
                # Sync with exchange
                await self.sync_positions()
                
                # Check exits
                await self.check_and_execute_exits()
                
                # Sleep
                await asyncio.sleep(3)
            
            except KeyboardInterrupt:
                logger.info("[BYBIT_MAINNET] Shutting down...")
                break
            except Exception as e:
                logger.exception(f"[BYBIT_MAINNET] Maintenance loop error: {e}")
                await asyncio.sleep(5)
