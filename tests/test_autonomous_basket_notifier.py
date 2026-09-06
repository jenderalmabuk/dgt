import json
import tempfile
import unittest
from pathlib import Path

from bots.autonomous_basket_notifier import (
    TelegramNotifier,
    format_close,
    format_degraded,
    format_entry,
    format_rejection,
    format_setups,
)


class Sender:
    def __init__(self, outcomes=None):
        self.outcomes = iter(outcomes or [True] * 20)
        self.calls = []

    def __call__(self, token, chat_id, text, timeout):
        self.calls.append((token, chat_id, text, timeout))
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class NotifierTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_restart_deduplicates_delivered_event(self):
        first = Sender()
        self.assertTrue(TelegramNotifier(self.state_dir, "token", "chat", first).emit("e1", "hello"))
        second = Sender()
        self.assertTrue(TelegramNotifier(self.state_dir, "token", "chat", second).emit("e1", "hello"))
        self.assertEqual(second.calls, [])
        self.assertEqual(json.loads((self.state_dir / "notifications.json").read_text()), ["e1"])

    def test_failed_delivery_retries_without_journaling(self):
        sender = Sender([OSError("offline"), True])
        notifier = TelegramNotifier(self.state_dir, "token", "chat", sender)
        self.assertFalse(notifier.emit("e2", "retry"))
        self.assertFalse((self.state_dir / "notifications.json").exists())
        self.assertTrue(notifier.emit("e2", "retry"))
        self.assertEqual(len(sender.calls), 2)

    def test_every_string_and_structured_path_is_html_escaped(self):
        class FormerSafeHTML(str):
            pass

        sender = Sender()
        notifier = TelegramNotifier(self.state_dir, "token", "chat", sender)
        notifier.emit("plain", FormerSafeHTML('<b><a href="https://bad">go</a></b>'))
        notifier.emit("structured", {"<key>": "<danger>&\""})
        self.assertEqual(sender.calls[0][2], '&lt;b&gt;&lt;a href=&quot;https://bad&quot;&gt;go&lt;/a&gt;&lt;/b&gt;')
        self.assertNotIn("<b>", sender.calls[0][2])
        self.assertIn("&lt;key&gt;", sender.calls[1][2])
        self.assertNotIn("<danger>", sender.calls[1][2])
        self.assertIn("&lt;danger&gt;&amp;&quot;", sender.calls[1][2])
        self.assertEqual(sender.calls[0][3], 15)

    def test_formatter_is_plain_then_dynamic_symbol_is_escaped_at_emit(self):
        sender = Sender()
        notifier = TelegramNotifier(self.state_dir, "token", "chat", sender)
        payload = format_setups([{"symbol": "<X>", "side": "LONG", "zone": "1-2", "trigger": "break", "sl": 1, "tp": 2, "expiry": "10m"}])
        self.assertIs(type(payload), str)
        self.assertNotIn("<b>", payload)
        self.assertIn("<X>", payload)
        self.assertTrue(notifier.emit("setup:x", payload))
        self.assertIn("Setup", sender.calls[0][2])
        self.assertIn("&lt;X&gt;", sender.calls[0][2])
        self.assertNotIn("<X>", sender.calls[0][2])

    def test_setups_cap_five_and_include_required_fields(self):
        setups = [{"symbol": f"S{i}<", "side": "LONG", "zone": "1-2", "trigger": "break", "sl": 1, "tp": 2, "expiry": "10m"} for i in range(6)]
        text = format_setups(setups)
        self.assertNotIn("S5", text)
        for field in ("symbol", "side", "zone", "trigger", "SL", "TP", "expiry"):
            self.assertIn(field, text)
        self.assertIn("S0<", text)

    def test_entry_includes_all_payload_fields_and_tradingview(self):
        text = format_entry("BTC<", "LONG", 10, 2, 9, 12, 2, "order&1", "https://www.tradingview.com/chart/?symbol=BTC&x=1")
        for value in ("symbol", "side", "entry", "qty", "SL", "TP", "RR", "orderLinkId", "TradingView"):
            self.assertIn(value, text)
        self.assertIn("BTC<", text)
        self.assertIn("order&1", text)

    def test_rejection_close_and_degraded_payloads(self):
        rejection = format_rejection("ETH", "RISK", "bad <risk>")
        close = format_close("target", -12.5, 99)
        degraded = format_degraded("STALE", "feed <old>")
        for value in ("symbol", "code", "message"):
            self.assertIn(value, rejection)
        for value in ("reason", "PnL", "price", "-12.5"):
            self.assertIn(value, close)
        for value in ("code", "message", "STALE", "feed <old>"):
            self.assertIn(value, degraded)


if __name__ == "__main__":
    unittest.main()
