import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


class PaperPositionPersistenceTest(unittest.TestCase):
    def test_positions_survive_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "open.json"
            with patch.dict(os.environ, {"FQ_PAPER_POSITION_STATE": str(state)}):
                import importlib
                import execution.paper_mainnet_trader as module
                module = importlib.reload(module)
                original = {"BTCUSDT": {"symbol": "BTCUSDT", "opened_at": datetime.now(timezone.utc)}}
                module._persist_positions(original)
                restored = module._load_positions()
                self.assertEqual(restored["BTCUSDT"]["symbol"], "BTCUSDT")
                self.assertIsInstance(restored["BTCUSDT"]["opened_at"], datetime)

    def test_load_migrates_legacy_shadow_roots_without_touching_provider_fields(self):
        import json
        import execution.paper_mainnet_trader as module
        protected = {"sl_price": 90.0, "provider_sl_original": 90.0,
                     "tp_ladder": [110.0, 120.0], "qty": 2.5}
        legacy = {"symbol": "BTCUSDT", **protected, "shadow_trailing_active": True,
                  "shadow_activated_at": datetime.now(timezone.utc).isoformat(),
                  "shadow_high_watermark": 115.0, "shadow_low_watermark": 99.0,
                  "shadow_stop": 113.0, "shadow_exit_price": 112.0,
                  "shadow_exit_at": datetime.now(timezone.utc).isoformat(),
                  "shadow_remaining_net_pnl": 7.5, "shadow_exit_logged": True}
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "open.json"
            state.write_text(json.dumps({"BTCUSDT": legacy}))
            with patch.object(module, "PAPER_STATE_PATH", state):
                restored = module._load_positions()["BTCUSDT"]
        self.assertEqual({key: restored[key] for key in protected}, protected)
        self.assertTrue(restored["shadow_trailing"]["active"])
        self.assertEqual(restored["shadow_trailing"]["high_watermark"], 115.0)
        self.assertEqual(restored["shadow_trailing"]["stop"], 113.0)
        self.assertEqual(restored["shadow_trailing"]["exit_price"], 112.0)
        self.assertEqual(restored["shadow_trailing"]["remaining_net_pnl"], 7.5)
        for key in ("shadow_trailing_active", "shadow_activated_at",
                    "shadow_high_watermark", "shadow_low_watermark", "shadow_stop",
                    "shadow_exit_price", "shadow_exit_at", "shadow_remaining_net_pnl",
                    "shadow_exit_logged"):
            self.assertNotIn(key, restored)

    def test_update_stop_persists_before_reporting_success(self):
        from execution.paper_mainnet_trader import PaperMainnetTrader
        trader = PaperMainnetTrader()
        trader.positions = {"BTCUSDT": {"side": "LONG", "entry_price": 100.0,
                                         "sl_price": 90.0}}
        with patch("execution.paper_mainnet_trader._persist_positions", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                asyncio.run(trader.update_stop("BTCUSDT", 95.0))


class PaperEquityPersistenceTest(unittest.TestCase):
    def test_equity_survives_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "equity.json"
            with patch.dict(os.environ, {"FQ_PAPER_EQUITY_STATE": str(state)}):
                import importlib
                import execution.paper_mainnet_trader as module
                module = importlib.reload(module)
                self.assertIsNone(module.load_equity())
                module.persist_equity(987.65)
                self.assertAlmostEqual(module.load_equity(), 987.65)

    def test_daily_baseline_survives_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "equity.json"
            with patch.dict(os.environ, {"FQ_PAPER_EQUITY_STATE": str(state)}):
                import importlib
                import execution.paper_mainnet_trader as module
                module = importlib.reload(module)
                module.persist_equity(987.65, daily_start_balance=1000.0)
                self.assertEqual(module.load_equity_state(), {
                    "equity": 987.65, "daily_start_balance": 1000.0,
                })


class PaperMaxAgeCloseTest(unittest.TestCase):
    def _trader(self, *, real_money="false", enabled="true"):
        from execution.paper_mainnet_trader import PaperMainnetTrader
        trader = PaperMainnetTrader()
        trader._close = unittest.mock.AsyncMock()
        return trader, patch.dict(os.environ, {
            "SIGNALCOPY_PAPER_MAX_AGE_CLOSE": enabled,
            "REAL_MONEY": real_money,
        })

    def test_paper_max_age_pre_tp1_closes_canonical_with_fresh_mark(self):
        trader, env = self._trader()
        pos = {"symbol": "ETHUSDT", "side": "LONG", "opened_at": datetime.now(timezone.utc) - timedelta(hours=73),
               "tp1_hit": False, "status": "OPEN", "qty": 1.0}
        with env:
            asyncio.run(trader._check_aged_position("ETHUSDT", pos, 2450.0))
        trader._close.assert_awaited_once_with("ETHUSDT", 2450.0, "MAX_AGE_PRE_TP1")
        self.assertEqual(pos["max_age_shadow"]["reason"], "MAX_AGE_PRE_TP1")

    def test_paper_max_age_post_tp1_closes_canonical_with_fresh_mark(self):
        trader, env = self._trader()
        pos = {"symbol": "LINKUSDT", "side": "LONG", "opened_at": datetime.now(timezone.utc) - timedelta(hours=169),
               "tp1_hit": True, "status": "OPEN", "qty": 0.5}
        with env:
            asyncio.run(trader._check_aged_position("LINKUSDT", pos, 12.0))
        trader._close.assert_awaited_once_with("LINKUSDT", 12.0, "MAX_AGE_POST_TP1")

    def test_max_age_close_hard_disabled_for_real_money(self):
        trader, env = self._trader(real_money="true")
        pos = {"symbol": "ETHUSDT", "side": "LONG", "opened_at": datetime.now(timezone.utc) - timedelta(hours=200),
               "tp1_hit": False, "status": "OPEN", "qty": 1.0}
        with env:
            asyncio.run(trader._check_aged_position("ETHUSDT", pos, 2450.0))
        trader._close.assert_not_awaited()
        self.assertEqual(pos["max_age_shadow"]["reason"], "MAX_AGE_PRE_TP1")

    def test_max_age_close_disabled_by_default(self):
        trader, env = self._trader(enabled="false")
        pos = {"symbol": "ETHUSDT", "side": "LONG", "opened_at": datetime.now(timezone.utc) - timedelta(hours=200),
               "tp1_hit": False, "status": "OPEN", "qty": 1.0}
        with env:
            asyncio.run(trader._check_aged_position("ETHUSDT", pos, 2450.0))
        trader._close.assert_not_awaited()


class PaperCostModelTest(unittest.TestCase):
    def test_adverse_slippage_both_sides(self):
        from execution.paper_mainnet_trader import PaperMainnetTrader
        with patch.dict(os.environ, {"PAPER_SLIPPAGE_PCT": "0.0005"}):
            self.assertAlmostEqual(PaperMainnetTrader._adverse_fill(100, "LONG", opening=True), 100.05)
            self.assertAlmostEqual(PaperMainnetTrader._adverse_fill(100, "LONG", opening=False), 99.95)
            self.assertAlmostEqual(PaperMainnetTrader._adverse_fill(100, "SHORT", opening=True), 99.95)
            self.assertAlmostEqual(PaperMainnetTrader._adverse_fill(100, "SHORT", opening=False), 100.05)

    def test_partial_exit_charges_exit_fee_and_slippage(self):
        from execution.paper_mainnet_trader import PaperMainnetTrader

        class Risk:
            current = 1000.0
            def get_current_equity(self):
                return self.current
            def sync_balance(self, value):
                self.current = value

        trader = PaperMainnetTrader()
        trader.risk_mgr = Risk()
        trader._notify_close = lambda payload: asyncio.sleep(0)
        pos = {"symbol": "TEST", "side": "LONG", "entry_price": 100.0,
               "opened_at": datetime.now(timezone.utc), "qty": 0.5}
        with patch.dict(os.environ, {"PAPER_SLIPPAGE_PCT": "0.0005", "PAPER_TAKER_FEE_PCT": "0.0005"}), \
             patch("execution.paper_mainnet_trader.persist_equity"):
            asyncio.run(trader._realize_partial(pos, 110.0, 0.5, "TP1_PARTIAL"))
        self.assertAlmostEqual(trader.risk_mgr.current, 1004.94501375)


class ShadowTrailingTest(unittest.TestCase):
    def test_shadow_trailing_never_changes_provider_stop_or_qty(self):
        from execution.paper_mainnet_trader import PaperMainnetTrader
        trader = PaperMainnetTrader()
        pos = {"symbol": "TEST", "side": "LONG", "entry_price": 100.0,
               "sl_price": 90.0, "qty": 1.0, "next_tp_index": 1,
               "tp1_hit": True, "adv_snapshot": {"atr_pct": 0.5}}
        with patch.dict(os.environ, {"SHADOW_TRAILING_ENABLED": "true",
                                    "SHADOW_TRAIL_MIN_PCT": "0.01",
                                    "PAPER_SLIPPAGE_PCT": "0.0005",
                                    "PAPER_TAKER_FEE_PCT": "0.0005"}):
            trader._update_shadow_trailing(pos, 110.0)
            trader._update_shadow_trailing(pos, 108.8)
        self.assertEqual(pos["sl_price"], 90.0)
        self.assertEqual(pos["qty"], 1.0)
        self.assertAlmostEqual(pos["shadow_trailing"]["stop"], 108.9)
        self.assertTrue(pos["shadow_trailing"]["exit_price"] > 0)
        self.assertNotIn("shadow_exit_price", pos)

    def test_shadow_inactive_before_explicit_tp1_event(self):
        from execution.paper_mainnet_trader import PaperMainnetTrader
        trader = PaperMainnetTrader()
        pos = {"symbol": "TEST", "side": "SHORT", "entry_price": 100.0,
               "sl_price": 110.0, "qty": 1.0, "next_tp_index": 1,
               "tp1_hit": False}
        trader._update_shadow_trailing(pos, 90.0)
        self.assertNotIn("shadow_trailing_active", pos)

    def test_legacy_position_without_tp1_field_is_shadow_ineligible(self):
        from execution.paper_mainnet_trader import PaperMainnetTrader
        trader = PaperMainnetTrader()
        pos = {"symbol": "LEGACY", "side": "LONG", "entry_price": 100.0,
               "sl_price": 90.0, "qty": 1.0, "next_tp_index": 2}
        trader._update_shadow_trailing(pos, 120.0)
        self.assertNotIn("shadow_trailing_active", pos)

    def test_parallel_shadow_variants_are_counterfactual(self):
        from execution.paper_mainnet_trader import PaperMainnetTrader
        trader = PaperMainnetTrader()
        pos = {"symbol": "VAR", "side": "LONG", "entry_price": 100.0,
               "sl_price": 90.0, "qty": 1.0, "tp1_hit": True}
        result = trader._shadow_variant_snapshot(pos, 120.0, conflict_count=3)
        self.assertEqual(set(result), {"current", "conflict3", "wide_continuation", "mfe_giveback",
                                       "reversal_reduced_size"})
        self.assertEqual(pos["sl_price"], 90.0)
        self.assertEqual(pos["qty"], 1.0)

    def test_smart_sl_tp_variants_are_counterfactual(self):
        from execution.paper_mainnet_trader import PaperMainnetTrader
        trader = PaperMainnetTrader()
        pos = {"symbol": "SMART", "side": "LONG", "entry_price": 100.0,
               "sl_price": 90.0, "qty": 10.0, "initial_qty": 10.0,
               "tp_ladder": [105.0, 110.0, 120.0], "tp1_hit": False}
        result = trader._smart_sl_tp_snapshot(pos, 94.0, conflict_count=3, confirmed_bars=2)
        self.assertEqual(set(result), {"pre_tp1_conflict", "frontload_40", "frontload_50", "hybrid"})
        self.assertTrue(result["pre_tp1_conflict"]["would_exit"])
        self.assertEqual(pos["sl_price"], 90.0)
        self.assertEqual(pos["qty"], 10.0)

    def test_audit_shadow_state_is_independent_persistent_and_non_mutating(self):
        import importlib
        import execution.paper_mainnet_trader as module
        before = {"sl_price": 90.0, "tp_ladder": [105.0, 110.0, 120.0],
                  "qty": 10.0, "status": "OPEN", "next_tp_index": 0}
        pos = {"symbol": "AUDIT", "side": "LONG", "entry_price": 100.0,
               "initial_qty": 10.0, "tp1_hit": True, "opened_at": datetime.now(timezone.utc),
               **before}
        trader = module.PaperMainnetTrader()
        with patch.dict(os.environ, {"PAPER_SLIPPAGE_PCT": "0.001",
                                    "PAPER_TAKER_FEE_PCT": "0.001"}):
            trader._update_audit_shadows(pos, 120.0)
            trader._update_audit_shadows(pos, 108.0)
        self.assertEqual({key: pos[key] for key in before}, before)
        states = pos["shadow_audit"]["exit_variants"]
        self.assertEqual(set(states), {"current", "conflict3", "wide_continuation", "mfe_giveback", "economic_be", "regime_hybrid"})
        for state in states.values():
            self.assertIn("high_watermark", state)
            self.assertIn("hypothetical_stop", state)
            self.assertIn("triggered_at", state)
            self.assertIn("fill_price", state)
            self.assertIn("adverse_slippage_usd", state)
            self.assertIn("fees_usd", state)
            self.assertIn("remaining_qty_net_pnl", state)
        self.assertIsNot(states["current"], states["wide_continuation"])

        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "positions.json"
            with patch.object(module, "PAPER_STATE_PATH", state_path):
                module._persist_positions({"AUDIT": pos})
                restored = module._load_positions()["AUDIT"]
        self.assertEqual(restored["shadow_audit"], pos["shadow_audit"])

    def test_economic_be_shadow_locks_cost_buffer_after_tp1_without_mutation(self):
        from execution.paper_mainnet_trader import PaperMainnetTrader
        trader = PaperMainnetTrader()
        pos = {"symbol": "BE", "side": "LONG", "entry_price": 100.0,
               "sl_price": 90.0, "qty": 1.0, "tp1_hit": True,
               "tp_ladder": [105.0, 110.0], "next_tp_index": 1, "status": "OPEN"}
        before = {key: pos[key] for key in ("sl_price", "tp_ladder", "qty", "next_tp_index", "status")}
        with patch.dict(os.environ, {"PAPER_SLIPPAGE_PCT": "0.0005",
                                    "PAPER_TAKER_FEE_PCT": "0.0005",
                                    "SHADOW_ECONOMIC_BE_BUFFER_PCT": "0.002"}):
            trader._update_audit_shadows(pos, 106.0)
            state = pos["shadow_audit"]["exit_variants"]["economic_be"]
            self.assertAlmostEqual(state["hypothetical_stop"], 100.2)
            trader._update_audit_shadows(pos, 100.1)
        self.assertIsNotNone(state["triggered_at"])
        self.assertAlmostEqual(state["trigger_price"], 100.2)
        self.assertGreater(state["remaining_qty_net_pnl"], 0)
        self.assertEqual({key: pos[key] for key in before}, before)

    def test_regime_hybrid_routes_healthy_trend_wide_and_extended_market_to_economic_be(self):
        from execution.paper_mainnet_trader import PaperMainnetTrader
        trader = PaperMainnetTrader()
        base = {"symbol": "ROUTE", "side": "LONG", "entry_price": 100.0,
                "sl_price": 90.0, "qty": 1.0, "tp1_hit": True,
                "tp_ladder": [105.0, 110.0], "next_tp_index": 1, "status": "OPEN"}
        trend = {**base, "adv_snapshot": {"regime_label": "TRENDING", "rsi": 55.0,
                                          "price_change_15m_pct": 1.0}}
        extended = {**base, "adv_snapshot": {"regime_label": "HIGH_VOL", "rsi": 75.0,
                                             "price_change_15m_pct": 7.0}}
        with patch.dict(os.environ, {"SHADOW_ECONOMIC_BE_BUFFER_PCT": "0.002"}):
            trader._update_audit_shadows(trend, 106.0)
            trader._update_audit_shadows(extended, 106.0)
        healthy = trend["shadow_audit"]["exit_variants"]["regime_hybrid"]
        defensive = extended["shadow_audit"]["exit_variants"]["regime_hybrid"]
        self.assertEqual(healthy["setup_family"], "TREND_CONTINUATION")
        self.assertEqual(healthy["route"], "WIDE_CONTINUATION")
        self.assertGreater(healthy["hypothetical_stop"], 100.2)
        self.assertEqual(defensive["setup_family"], "EXTENDED_HIGH_VOL")
        self.assertEqual(defensive["route"], "ECONOMIC_BE")
        self.assertAlmostEqual(defensive["hypothetical_stop"], 100.2)
        self.assertEqual(trend["sl_price"], 90.0)
        self.assertEqual(extended["sl_price"], 90.0)

    def test_bad_signal_classification_is_deterministic_and_joined_to_outcome(self):
        from execution.paper_mainnet_trader import PaperMainnetTrader
        snapshot = {"rsi": 75.0, "price_change_15m_pct": 7.0,
                    "flow_direction": "SHORT_ONLY", "entry_low": 99.0, "entry_high": 100.0}
        result = PaperMainnetTrader._classify_bad_signal("LONG", 101.0, snapshot)
        self.assertEqual(set(result["categories"]), {"DIRECTIONAL_OVEREXTENSION", "ENTRY_OUTSIDE_PROVIDER_ZONE", "FLOW_CONFLICT"})
        self.assertEqual(result["evidence"]["fill"], 101.0)
        joined = PaperMainnetTrader._classify_bad_signal_outcome(result, baseline_net_pnl=-5.0,
                                                                  best_shadow_net_pnl=1.0)
        self.assertEqual(joined["verdict"], "AVOIDED_LOSS")
        self.assertEqual(joined["delta_usd"], 6.0)

    def test_bad_signal_missing_fields_are_recorded_unavailable_not_negative_evidence(self):
        from execution.paper_mainnet_trader import PaperMainnetTrader
        result = PaperMainnetTrader._classify_bad_signal("SHORT", 100.0, {})
        self.assertEqual(result["categories"], [])
        self.assertIn("provider_entry_zone", result["unavailable"])
        self.assertIn("RSI_UNAVAILABLE", result["unavailable"])
        self.assertIn("PRICE_CHANGE_15M_UNAVAILABLE", result["unavailable"])
        self.assertIn("FLOW_DIRECTION_UNAVAILABLE:UNKNOWN", result["unavailable"])

    def test_tp_allocation_ledgers_track_hypothetical_realizations_without_mutation(self):
        from execution.paper_mainnet_trader import PaperMainnetTrader
        trader = PaperMainnetTrader()
        pos = {"symbol": "LEDGER", "side": "LONG", "entry_price": 100.0,
               "sl_price": 90.0, "initial_qty": 10.0, "qty": 10.0,
               "tp_ladder": [105.0, 110.0, 120.0], "next_tp_index": 0,
               "status": "OPEN", "tp1_hit": False, "opened_at": datetime.now(timezone.utc)}
        before = {key: pos[key] for key in ("sl_price", "tp_ladder", "qty", "status", "next_tp_index")}
        with patch.dict(os.environ, {"PAPER_SLIPPAGE_PCT": "0.001", "PAPER_TAKER_FEE_PCT": "0.001"}):
            trader._record_shadow_tp(pos, 0, 105.0)
        ledgers = pos["shadow_audit"]["tp_ledgers"]
        self.assertEqual(set(ledgers), {"provider", "frontload_40", "frontload_50"})
        self.assertAlmostEqual(ledgers["provider"]["remaining_qty"], 10.0 - 10.0 / 3.0)
        self.assertEqual(ledgers["frontload_40"]["remaining_qty"], 6.0)
        self.assertEqual(ledgers["frontload_50"]["remaining_qty"], 5.0)
        self.assertGreater(ledgers["provider"]["realized_net_pnl"], 0)
        self.assertEqual({key: pos[key] for key in before}, before)

    def test_completed_bar_scorer_counts_independent_reversal_categories(self):
        from execution.paper_mainnet_trader import PaperMainnetTrader
        base = datetime.now(timezone.utc) - timedelta(minutes=5)
        bars = [
            {"close": close, "volume": volume,
             "open_time": (base + timedelta(minutes=index)).isoformat()}
            for index, (close, volume) in enumerate(((110, 100), (108, 120),
                                                      (105, 180), (101, 300),
                                                      (99, 50)))
        ]
        candle = PaperMainnetTrader._score_completed_bars(bars, "LONG", entry=100, sl=90, mfe_r=1.1)
        self.assertTrue(candle["completed"])
        self.assertGreaterEqual(candle["conflict_categories"], 3)
        self.assertIn("structure", candle["categories"])
        self.assertIn("momentum", candle["categories"])
        self.assertIn("progress", candle["categories"])

    def test_post_tp1_exit_variants_do_not_accumulate_before_tp1(self):
        from execution.paper_mainnet_trader import PaperMainnetTrader
        trader = PaperMainnetTrader()
        pos = {"symbol": "PRE", "side": "LONG", "entry_price": 100.0,
               "sl_price": 90.0, "initial_qty": 1.0, "qty": 1.0,
               "tp_ladder": [110.0], "tp1_hit": False, "status": "OPEN"}
        trader._update_audit_shadows(pos, 120.0)
        self.assertNotIn("exit_variants", pos.get("shadow_audit", {}))

    def test_missing_or_stale_completed_candle_keeps_conflict_candidate_inert(self):
        from execution.paper_mainnet_trader import PaperMainnetTrader
        trader = PaperMainnetTrader()
        pos = {"symbol": "FRESH", "side": "LONG", "entry_price": 100.0,
               "sl_price": 90.0, "initial_qty": 1.0, "qty": 1.0,
               "tp_ladder": [110.0], "tp1_hit": True, "status": "OPEN"}
        trader._update_audit_shadows(pos, 94.0, completed_candle=None)
        conflict = pos["shadow_audit"]["exit_variants"]["conflict3"]
        self.assertFalse(conflict["candidate_available"])
        self.assertIsNone(conflict["triggered_at"])
        stale = {"completed": True, "closed_at": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),
                 "conflict_categories": 4, "timeframe_seconds": 60}
        trader._update_audit_shadows(pos, 94.0, completed_candle=stale)
        self.assertFalse(conflict["candidate_available"])
        fresh = {"completed": True, "closed_at": datetime.now(timezone.utc).isoformat(),
                 "conflict_categories": 3, "timeframe_seconds": 60}
        trader._update_audit_shadows(pos, 94.0, completed_candle=fresh)
        self.assertTrue(conflict["candidate_available"])
        self.assertIsNotNone(conflict["triggered_at"])

    def test_missing_candle_clears_previous_untriggered_conflict_evidence(self):
        from execution.paper_mainnet_trader import PaperMainnetTrader
        trader = PaperMainnetTrader()
        pos = {"symbol": "STALE", "side": "LONG", "entry_price": 100.0,
               "sl_price": 90.0, "provider_sl_original": 90.0, "qty": 1.0,
               "tp_ladder": [110.0], "tp1_hit": True,
               "shadow_conflict_candle": {"conflict_categories": 4},
               "shadow_conflict_last_candle": "old", "shadow_conflict_confirmed_bars": 2,
               "shadow_audit": {"exit_variants": {"conflict3": {
                   "candidate_available": True, "conflict_categories": 4,
                   "categories": ["structure"], "triggered_at": None,
                   "high_watermark": 100.0, "low_watermark": 94.0}}}}
        protected = {key: pos[key] for key in
                     ("sl_price", "provider_sl_original", "tp_ladder", "qty")}
        trader._update_audit_shadows(pos, 96.0, completed_candle=None)
        state = pos["shadow_audit"]["exit_variants"]["conflict3"]
        self.assertFalse(state["candidate_available"])
        self.assertNotIn("conflict_categories", state)
        self.assertNotIn("categories", state)
        self.assertNotIn("shadow_conflict_candle", pos)
        self.assertNotIn("shadow_conflict_last_candle", pos)
        self.assertEqual(pos["shadow_conflict_confirmed_bars"], 0)
        self.assertEqual({key: pos[key] for key in protected}, protected)

    def test_stale_conflict_shadow_only_never_closes_position(self):
        from execution.paper_mainnet_trader import PaperMainnetTrader
        trader = PaperMainnetTrader()
        pos = {"symbol": "STALE", "side": "LONG", "entry_price": 100.0,
               "sl_price": 90.0, "qty": 1.0, "tp1_hit": False,
               "opened_at": datetime.now(timezone.utc),
               "adv_snapshot": {"stale_conflict_categories": 3}}
        pos["opened_at"] = pos["opened_at"] - timedelta(hours=19)
        with patch.dict(os.environ, {"STALE_SHADOW_ENABLED": "true"}):
            self.assertTrue(trader._record_stale_conflict_shadow(pos, 95.0))
        self.assertNotIn("shadow_stale_closed", pos)
        self.assertTrue(pos["shadow_stale_candidate"])


if __name__ == "__main__":
    unittest.main()
