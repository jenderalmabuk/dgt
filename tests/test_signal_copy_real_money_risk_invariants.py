import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from execution.paper_mainnet_trader import PaperMainnetTrader
from risk.risk_engine import RiskManager


class SignalCopyRiskInvariantTest(unittest.TestCase):
    def test_close_accounts_entry_fee_and_updates_daily_guard(self):
        trader = PaperMainnetTrader()
        risk = RiskManager(starting_balance=1000.0)
        trader.risk_mgr = risk
        trader._journal = None
        trader._notify_close = AsyncMock()
        trader.positions = {
            "TESTUSDT": {
                "symbol": "TESTUSDT", "side": "LONG", "entry_price": 100.0,
                "sl_price": 90.0, "provider_sl_original": 90.0,
                "tp_ladder": [110.0], "next_tp_index": 0,
                "initial_qty": 1.0, "qty": 1.0, "notional": 100.0,
                "opened_at": datetime.now(timezone.utc), "status": "OPEN",
                "tp1_hit": False, "entry_fee": 0.05, "adv_snapshot": {},
            }
        }
        with tempfile.TemporaryDirectory() as tmp, \
             patch("execution.paper_mainnet_trader.PAPER_STATE_PATH", Path(tmp) / "positions.json"), \
             patch("execution.paper_mainnet_trader.persist_equity"), \
             patch("execution.paper_mainnet_trader._append_shadow_result"), \
             patch.dict(os.environ, {"PAPER_SLIPPAGE_PCT": "0", "PAPER_TAKER_FEE_PCT": "0"}):
            asyncio.run(trader._close("TESTUSDT", 80.0, "HARD_SL"))
        payload = trader._notify_close.await_args.args[0]
        self.assertEqual(-20.05, payload["pnl_usd"])
        self.assertEqual(1, risk.losses)
        self.assertTrue(risk.hard_daily_stop_hit)

    def test_actual_fill_resizes_to_one_percent_net_stop_loss(self):
        trader = PaperMainnetTrader()
        risk = RiskManager(starting_balance=1000.0)
        trader.risk_mgr = risk
        quote = {"price": 100.0, "price_fresh": True, "price_source": "test",
                 "price_observed_at": datetime.now(timezone.utc).isoformat()}
        with tempfile.TemporaryDirectory() as tmp, \
             patch("execution.paper_mainnet_trader.PAPER_STATE_PATH", Path(tmp) / "positions.json"), \
             patch("execution.paper_mainnet_trader.persist_equity"), \
             patch.dict(os.environ, {"PAPER_SLIPPAGE_PCT": "0.001", "PAPER_TAKER_FEE_PCT": "0.001"}):
            result = asyncio.run(trader.submit_open(
                symbol="RISKUSDT", side="LONG", sl=95.0, tp1=110.0,
                notional=1000.0, entry_price=100.0, execution_price_quote=quote,
            ))
        pos = trader.positions["RISKUSDT"]
        adverse_exit = 95.0 * 0.999
        qty = pos["qty"]
        net_stop_loss = (pos["entry_price"] - adverse_exit) * qty + pos["entry_fee"] + adverse_exit * qty * 0.001
        self.assertLessEqual(net_stop_loss, 10.0 + 1e-9)
        self.assertAlmostEqual(result["max_loss_usd_at_fill"], net_stop_loss)

    def test_boundary_routed_fill_is_not_blocked_as_outside_provider_zone(self):
        trader = PaperMainnetTrader()
        quote = {"price": 110.05, "price_fresh": True, "price_source": "test",
                 "price_observed_at": datetime.now(timezone.utc).isoformat()}
        with tempfile.TemporaryDirectory() as tmp, \
             patch("execution.paper_mainnet_trader.PAPER_STATE_PATH", Path(tmp) / "positions.json"), \
             patch.dict(os.environ, {"PAPER_SLIPPAGE_PCT": "0", "PAPER_TAKER_FEE_PCT": "0"}):
            result = asyncio.run(trader.submit_open(
                symbol="BOUNDARYUSDT", side="LONG", sl=90.0, tp1=120.0,
                notional=100.0, entry_price=110.05, execution_price_quote=quote,
                entry_low=100.0, entry_high=110.0,
            ))
        self.assertTrue(result["ok"], result)

    def test_directional_overextension_is_shadow_only(self):
        trader = PaperMainnetTrader()
        quote = {"price": 100.0, "price_fresh": True, "price_source": "test",
                 "price_observed_at": datetime.now(timezone.utc).isoformat()}
        with tempfile.TemporaryDirectory() as tmp, \
             patch("execution.paper_mainnet_trader.PAPER_STATE_PATH", Path(tmp) / "positions.json"), \
             patch.object(trader, "_classify_bad_signal", return_value={
                 "categories": ["DIRECTIONAL_OVEREXTENSION"], "evidence": {"rsi": 80}}), \
             patch.dict(os.environ, {"PAPER_SLIPPAGE_PCT": "0", "PAPER_TAKER_FEE_PCT": "0"}):
            result = asyncio.run(trader.submit_open(
                symbol="EXTENDEDUSDT", side="LONG", sl=90.0, tp1=120.0,
                notional=100.0, entry_price=100.0, execution_price_quote=quote,
            ))
        self.assertTrue(result["ok"], result)
        self.assertEqual(["DIRECTIONAL_OVEREXTENSION"],
                         trader.positions["EXTENDEDUSDT"]["bad_signal_shadow"]["categories"])

    def test_aged_position_records_shadow_observation_without_close(self):
        trader = PaperMainnetTrader()
        opened = datetime.now(timezone.utc) - timedelta(hours=73)
        pos = {"symbol": "AGEDUSDT", "side": "LONG", "entry_price": 100.0,
               "sl_price": 90.0, "tp1_hit": False, "opened_at": opened}
        trader.positions = {"AGEDUSDT": pos}
        trader._close = AsyncMock()
        asyncio.run(trader._check_aged_position("AGEDUSDT", pos, 101.0))
        trader._close.assert_not_awaited()
        self.assertEqual("MAX_AGE_PRE_TP1", pos["max_age_shadow"]["reason"])
        self.assertEqual(101.0, pos["max_age_shadow"]["observed_price"])

    def test_fill_after_tp1_keeps_later_provider_targets(self):
        trader = PaperMainnetTrader()
        quote = {"price": 121.0, "price_fresh": True, "price_source": "test",
                 "price_observed_at": datetime.now(timezone.utc).isoformat()}
        with tempfile.TemporaryDirectory() as tmp, \
             patch("execution.paper_mainnet_trader.PAPER_STATE_PATH", Path(tmp) / "positions.json"), \
             patch.dict(os.environ, {"PAPER_SLIPPAGE_PCT": "0", "PAPER_TAKER_FEE_PCT": "0"}):
            result = asyncio.run(trader.submit_open(
                symbol="LADDERUSDT", side="LONG", sl=90.0,
                tp1=120.0, tp2=130.0, tp3=140.0,
                notional=100.0, entry_price=121.0, execution_price_quote=quote,
            ))
        self.assertTrue(result["ok"], result)
        self.assertEqual(trader.positions["LADDERUSDT"]["tp_ladder"], [130.0, 140.0])

    def test_provider_close_after_stop_is_labelled_sl_gap(self):
        trader = PaperMainnetTrader()
        trader.positions = {"TESTUSDT": {"side": "LONG", "sl_price": 90.0}}
        trader._get_mark_price = AsyncMock(return_value=80.0)
        trader._close = AsyncMock()
        result = asyncio.run(trader.close_position("TESTUSDT", "PROVIDER_CLOSE"))
        trader._close.assert_awaited_once_with("TESTUSDT", 80.0, "SL_GAP")
        self.assertEqual("SL_GAP", result["reason"])


if __name__ == "__main__":
    unittest.main()
