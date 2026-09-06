import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class SignalCopyStatePersistenceTest(unittest.TestCase):
    def test_outcome_empty_store_loads_cleanly_and_save_is_valid_json(self):
        from signal_copy import outcome_tracker

        with tempfile.TemporaryDirectory() as td:
            store = Path(td) / "outcomes.json"
            store.write_text("")
            with patch.object(outcome_tracker, "STORE", store):
                tracker = outcome_tracker.SignalOutcomeTracker()
                self.assertEqual({}, tracker._open)
                tracker._save()
                self.assertEqual("{}", store.read_text().strip())

    def test_channel_empty_store_loads_cleanly_and_save_is_valid_json(self):
        from signal_copy import channel_performance

        with tempfile.TemporaryDirectory() as td:
            store = Path(td) / "channels.json"
            store.write_text("")
            with patch.object(channel_performance, "DB_FILE", str(store)):
                tracker = channel_performance.ChannelPerformanceTracker()
                self.assertEqual({}, tracker.data)
                tracker._save()
                self.assertEqual("{}", store.read_text().strip())


if __name__ == "__main__":
    unittest.main()
