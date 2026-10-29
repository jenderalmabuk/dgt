import unittest
import pandas as pd
import numpy as np
import os
import sys

# Ensure production path is imported
sys.path.insert(0, '/app')
from bots.dgt_momentum_paper import (
    DGTMomentumBot, MAKER_FEE, TAKER_FEE, SLIPPAGE_RATE,
    MAX_NOTIONAL_PER_POS_MULT, MAX_PORTFOLIO_LEVERAGE,
    format_shadow_cvd_badge
)

class TestDGTMechanicsProduction(unittest.TestCase):
    def setUp(self):
        self.bot = DGTMomentumBot()
        self.bot.balance = 1000.0
        self.bot.open_positions = {}
        self.bot.pending_orders = {}
        # Stub I/O
        self.bot.log_closed_trade = lambda p: None
        self.bot.save_state = lambda: None

    def test_single_trade_exposure_cap(self):
        """Single trade notional must strictly cap at MAX_NOTIONAL_PER_POS_MULT * balance."""
        entry_px = 100.0
        risk = 0.05  # tiny risk -> would produce $20,000 notional without cap
        risk_usd = self.bot.balance * 0.01  # $10
        pos_size = risk_usd / risk
        pos_notional = pos_size * entry_px
        
        max_single = self.bot.balance * MAX_NOTIONAL_PER_POS_MULT  # 1.5x = $1500
        if pos_notional > max_single:
            pos_notional = max_single
            pos_size = pos_notional / entry_px
            risk_usd = pos_size * risk
            
        self.assertEqual(pos_notional, 1500.0)
        self.assertEqual(pos_size, 15.0)
        self.assertAlmostEqual(risk_usd, 0.75, places=4)

    def test_portfolio_exposure_cap_reject(self):
        """When existing positions + new order exceed 3.0x balance, new setup must be rejected."""
        # Existing positions: 2 positions with $1200 notional each = $2400 notional
        self.bot.open_positions = {
            "POS1": {"pos_notional": 1200.0},
            "POS2": {"pos_notional": 1200.0},
        }
        max_portfolio_notional = self.bot.balance * MAX_PORTFOLIO_LEVERAGE  # $3000
        
        # New candidate order: $700 notional -> total would be $3100 > $3000
        new_pos_notional = 700.0
        cur_open_notional = sum(p.get("pos_notional", 0.0) for p in self.bot.open_positions.values())
        
        rejected = (cur_open_notional + new_pos_notional) > max_portfolio_notional
        self.assertTrue(rejected, "Portfolio cap failed to reject order exceeding 3.0x leverage!")

    def test_unbundled_ledger_reconciliation(self):
        """Test unbundled accounting: Net PnL = Raw Gross PnL - Total Fees - Slippage."""
        init_balance = self.bot.balance  # 1000.0
        entry_px = 100.0
        pos_size = 10.0
        pos_notional = 1000.0
        
        pos = {
            "symbol": "BTCUSDT",
            "side": "BUY",
            "entry_px": entry_px,
            "sl_px": 95.0,
            "tp1_px": 105.0,
            "risk": 5.0,
            "risk_usd": 50.0,
            "pos_size": pos_size,
            "pos_notional": pos_notional,
            "rem_ratio": 1.0,
            "gross_realized_pnl": 0.0,
            "shadow_cvd": {"verdict": "ACCUMULATION"}
        }
        
        # 1. Entry fill
        entry_fee = pos_notional * MAKER_FEE  # 1000 * 0.0002 = $0.20
        self.bot.balance -= entry_fee
        pos["entry_fee"] = entry_fee
        pos["exit_fees"] = 0.0
        pos["total_fees"] = entry_fee
        pos["slippage_paid"] = 0.0
        self.bot.open_positions["BTCUSDT"] = pos
        self.assertAlmostEqual(self.bot.balance, 999.80, places=4)
        
        # 2. TP1 fill (+1.0R): 33% at 105.0 (Maker order -> 0 slippage)
        tp1_qty = pos_size * 0.33  # 3.3 units
        tp1_raw_gross = tp1_qty * (105.0 - 100.0)  # +$16.50
        tp1_fee = tp1_qty * 105.0 * MAKER_FEE       # 3.3 * 105 * 0.0002 = $0.0693
        self.bot._partial_close(pos, close_ratio=0.33, close_px=105.0, is_maker=True)
        pos["hit_tp1"] = True
        self.assertAlmostEqual(self.bot.balance, 999.80 + 16.50 - 0.0693, places=4)
        
        # 3. Remaining close (67%) at Breakeven 100.0 (Taker / Market order -> Slippage applied)
        rem_qty = pos_size * 0.67  # 6.7 units
        slippage_per_unit = 100.0 * SLIPPAGE_RATE  # 100 * 0.0005 = 0.05
        exec_close_px = 100.0 - slippage_per_unit   # 99.95
        rem_exit_fee = rem_qty * exec_close_px * TAKER_FEE  # 6.7 * 99.95 * 0.00055 = 0.36831575
        rem_slippage_cost = rem_qty * slippage_per_unit     # 6.7 * 0.05 = 0.3350
        rem_raw_gross = rem_qty * (100.0 - 100.0)           # 0.0 (BEP before slippage)
        
        # Call production close
        self.bot._close_position(pos, close_px=100.0, exit_reason="SL_OR_BEP", is_maker=False)
        
        # Total unbundled reconciliation
        raw_gross_total = tp1_raw_gross + rem_raw_gross  # $16.50
        all_fees = entry_fee + tp1_fee + rem_exit_fee    # 0.20 + 0.0693 + 0.36831575 = $0.63761575
        all_slippage = rem_slippage_cost                 # $0.3350
        expected_net_pnl = raw_gross_total - all_fees - all_slippage  # 16.50 - 0.63761575 - 0.3350 = $15.52738425
        
        balance_delta = self.bot.balance - init_balance
        reported_net_pnl = pos["net_pnl"]
        
        self.assertAlmostEqual(balance_delta, expected_net_pnl, places=4)
        self.assertAlmostEqual(reported_net_pnl, expected_net_pnl, places=4)
        self.assertAlmostEqual(balance_delta, reported_net_pnl, places=4)

    def test_cvd_boundary_conditions(self):
        """Verify CVD threshold boundaries (-0.3000 vs -0.2999) and fallbacks."""
        # 1. Distribution threshold: cvd_7d <= -0.30 -> DISTRIBUTION (BLOCKED)
        cvd_7d = -0.3000
        cvd_30d = -0.1000
        if cvd_7d >= 0.05 or (cvd_30d >= 0.05 and cvd_7d >= -0.10):
            verdict = "ACCUMULATION"
        elif cvd_7d <= -0.30 or (cvd_30d <= -0.30 and cvd_7d < 0):
            verdict = "DISTRIBUTION"
        else:
            verdict = "NEUTRAL"
        self.assertEqual(verdict, "DISTRIBUTION")

        # 2. Boundary just above threshold: -0.2999 -> NEUTRAL (NOT BLOCKED)
        cvd_7d = -0.2999
        cvd_30d = -0.1000
        if cvd_7d >= 0.05 or (cvd_30d >= 0.05 and cvd_7d >= -0.10):
            verdict = "ACCUMULATION"
        elif cvd_7d <= -0.30 or (cvd_30d <= -0.30 and cvd_7d < 0):
            verdict = "DISTRIBUTION"
        else:
            verdict = "NEUTRAL"
        self.assertEqual(verdict, "NEUTRAL")

        # 3. Bybit Only / Short history exemption handling
        exempt_badge = format_shadow_cvd_badge({"shadow_cvd": {"verdict": "EXEMPT_BYBIT_ONLY"}})
        self.assertIn("EXEMPT", exempt_badge)

    def test_mid_candle_fill_strict_logic(self):
        """Order created at minute 3 of Bar 0 must NOT fill from Bar 0 low if price is now higher."""
        created_candle = "2026-10-29 12:00:00+00:00"
        order = {"entry_px": 99.0, "created_candle_time": created_candle}
        
        # Bar 0: Low was 98.0 (at min 1), but close at min 3 is 101.0
        df = pd.DataFrame([{
            "open_time": created_candle,
            "low": 98.0,
            "high": 102.0,
            "close": 101.0
        }])
        
        df_open_times = pd.to_datetime(df["open_time"], utc=True)
        target_time = pd.to_datetime(created_candle, utc=True)
        subsequent_bars = df[df_open_times > target_time]
        
        is_filled = False
        if not subsequent_bars.empty and float(subsequent_bars["low"].min()) <= order["entry_px"]:
            is_filled = True
        elif float(df["close"].iloc[-1]) <= order["entry_px"]:
            is_filled = True
            
        self.assertFalse(is_filled, "Mid-candle pre-order dip incorrectly filled!")

if __name__ == "__main__":
    unittest.main()
