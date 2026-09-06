import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from execution.paper_mainnet_trader import PaperMainnetTrader
from execution.trade_journal import TradeJournalWriter


class ShadowJournalIntegrityTest(unittest.TestCase):
    def test_nested_datetime_is_persisted_as_iso_string(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            writer = TradeJournalWriter(str(out))
            writer._append_trade(
                out / "trade_history.csv",
                out / "trade_history.json",
                {
                    "symbol": "TESTUSDT",
                    "timestamp_close": datetime(2026, 8, 4, tzinfo=UTC),
                    "fill_ledger": [
                        {"filled_at": datetime(2026, 8, 4, 0, 0, 1, tzinfo=UTC)}
                    ],
                    "joined_shadow_outcomes": {
                        "frontload_50": {
                            "fills": [{"timestamp": datetime(2026, 8, 4, 0, 0, 2, tzinfo=UTC)}]
                        }
                    },
                },
            )
            row = json.loads((out / "trade_history.json").read_text())[0]
            self.assertEqual(row["fill_ledger"][0]["filled_at"], "2026-08-04T00:00:01+00:00")
            self.assertEqual(
                row["joined_shadow_outcomes"]["frontload_50"]["fills"][0]["timestamp"],
                "2026-08-04T00:00:02+00:00",
            )

    def test_signal_entry_zone_keys_are_classified(self):
        result = PaperMainnetTrader._classify_bad_signal(
            "LONG",
            101.0,
            {"signal_entry_low": 99.0, "signal_entry_high": 100.0},
        )
        self.assertIn("ENTRY_OUTSIDE_PROVIDER_ZONE", result["categories"])
        self.assertNotIn("provider_entry_zone", result["unavailable"])

    def test_old_unavailable_zone_classification_is_self_healed(self):
        pos = {
            "side": "LONG",
            "entry_price": 101.0,
            "adv_snapshot": {"signal_entry_low": 99.0, "signal_entry_high": 100.0},
            "bad_signal_shadow": {"categories": [], "unavailable": ["provider_entry_zone"]},
        }
        PaperMainnetTrader._ensure_bad_signal_classification(pos)
        self.assertIn("ENTRY_OUTSIDE_PROVIDER_ZONE", pos["bad_signal_shadow"]["categories"])
        self.assertNotIn("provider_entry_zone", pos["bad_signal_shadow"]["unavailable"])

if __name__ == "__main__":
    unittest.main()
