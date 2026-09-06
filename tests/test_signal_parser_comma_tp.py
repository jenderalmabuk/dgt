import unittest

from signal_copy.signal_parser import parse_signal


class CommaSeparatedTakeProfitTest(unittest.TestCase):
    def test_compact_comma_separated_take_profits(self):
        sig = parse_signal(
            "ETHUSDT SHORT\nEntry 3500\nTP 3400,3300,3200\nSL 3600\nLeverage 20x\nTF 15m"
        )
        self.assertIsNotNone(sig)
        self.assertEqual([3400.0, 3300.0, 3200.0], sig.take_profits)

    def test_entry_now_is_explicit_market_sentinel(self):
        sig = parse_signal("$BTC LONG\nEntry now\nTP 66000, 67000\nSL 64000")
        self.assertIsNotNone(sig)
        self.assertEqual("market", sig.entry_type)
        self.assertEqual((0.0, 0.0), (sig.entry_low, sig.entry_high))
        self.assertEqual([66000.0, 67000.0], sig.take_profits)


if __name__ == "__main__":
    unittest.main()
