import unittest

from execution.paper_mainnet_trader import PaperMainnetTrader


class DynamicSlExperimentMetadataTest(unittest.TestCase):
    def setUp(self):
        self.trader = PaperMainnetTrader()
        self.pos = {
            "symbol": "TESTUSDT", "side": "LONG", "entry_price": 100.0,
            "sl_price": 90.0, "provider_sl_original": 90.0, "qty": 1.0,
            "initial_qty": 1.0, "tp_ladder": [110.0, 120.0, 130.0],
            "tp1_hit": True, "adv_snapshot": {
                "regime_label": "TRENDING", "rsi": 55.0,
                "price_change_15m_pct": 1.0,
            },
        }

    def test_exit_variants_have_frozen_metadata_and_review_status(self):
        self.trader._update_audit_shadows(self.pos, 110.0)
        states = self.pos["shadow_audit"]["exit_variants"]
        self.assertEqual(
            {(state["experiment_epoch"], state["config_version"])
             for state in states.values()},
            {("signalcopy_dynamic_sl_2026q3", "dynamic_sl_shadow_v1")},
        )
        self.assertEqual(states["current"]["variant_status"], "candidate")
        self.assertEqual(states["regime_hybrid"]["variant_status"], "candidate")
        for name in ("mfe_giveback", "economic_be"):
            self.assertIn(states[name]["variant_status"], ("rejected", "diagnostic_only"))

    def test_tp_variants_are_diagnostic_and_provider_fields_stay_unchanged(self):
        provider_fields = ("sl_price", "provider_sl_original", "qty", "tp_ladder", "tp1_hit")
        before = {key: self.pos.get(key) for key in provider_fields}

        self.trader._update_audit_shadows(self.pos, 110.0)
        self.trader._record_shadow_tp(self.pos, 0, 110.0)
        smart = self.trader._smart_sl_tp_snapshot(self.pos, 110.0)

        self.assertEqual({key: self.pos.get(key) for key in provider_fields}, before)
        for name in ("frontload_40", "frontload_50"):
            ledger = self.pos["shadow_audit"]["tp_ledgers"][name]
            for outcome in (ledger, smart[name]):
                self.assertEqual(outcome["experiment_epoch"], "signalcopy_dynamic_sl_2026q3")
                self.assertEqual(outcome["config_version"], "dynamic_sl_shadow_v1")
                self.assertIn(outcome["variant_status"], ("rejected", "diagnostic_only"))


if __name__ == "__main__":
    unittest.main()
