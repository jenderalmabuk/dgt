import os
import unittest
from unittest.mock import patch

from execution import paper_mainnet_trader
from execution.paper_mainnet_trader import PaperMainnetTrader


class ShadowIsolationTest(unittest.TestCase):
    def test_shadow_only_does_not_mutate_canonical_stop_metadata(self):
        trader = PaperMainnetTrader()
        pos = {
            "symbol": "TESTUSDT",
            "side": "LONG",
            "entry_price": 100.0,
            "sl": 90.0,
            "stop_loss": 90.0,
            "sl_price": 90.0,
            "sl_kind": "ORIGINAL",
            "tp1_hit": True,
            "qty": 1.0,
            "initial_qty": 1.0,
            "tp_ladder": [110.0, 120.0],
        }
        with patch.dict(os.environ, {"SHADOW_TRAILING_ENABLED": "true"}), \
             patch("execution.paper_mainnet_trader.TRAILING_EXECUTION_ENABLED", False):
            trader._update_shadow_trailing(pos, 111.0)
        self.assertNotIn("locked_profit", pos)
        self.assertEqual(pos["sl_kind"], "ORIGINAL")
        self.assertEqual(pos["sl_price"], 90.0)
        self.assertTrue(pos["shadow_trailing"]["locked_profit"])

    def test_stale_live_toggle_cannot_mutate_provider_stop(self):
        trader = PaperMainnetTrader()
        pos = {
            "symbol": "TESTUSDT", "side": "LONG", "entry_price": 100.0,
            "sl": 90.0, "stop_loss": 90.0, "sl_price": 90.0,
            "sl_kind": "ORIGINAL", "tp1_hit": True,
            "qty": 1.0, "initial_qty": 1.0, "tp_ladder": [110.0],
        }
        with patch.dict(os.environ, {"SHADOW_TRAILING_ENABLED": "true"}), \
             patch.object(paper_mainnet_trader, "TRAILING_EXECUTION_ENABLED", True):
            trader._update_shadow_trailing(pos, 120.0)
        self.assertEqual((pos["sl"], pos["stop_loss"], pos["sl_price"], pos["sl_kind"]),
                         (90.0, 90.0, 90.0, "ORIGINAL"))


if __name__ == "__main__":
    unittest.main()
