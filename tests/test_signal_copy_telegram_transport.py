import asyncio
import types
import unittest
from unittest.mock import AsyncMock, patch

from signal_copy.telegram_transport import _plain_text, _send_html_then_plain, _send_with_retry


class TelegramRetryTest(unittest.TestCase):
    def test_html_fallback_is_valid_plain_text(self):
        self.assertEqual(_plain_text('<b>XRP</b> <broken & text'), 'XRP <broken & text')

    def test_html_send_preserves_expandable_then_falls_back_plain(self):
        bot = AsyncMock()
        bot.send_message.side_effect = [ValueError("parse entities"), None]
        ok = asyncio.run(_send_html_then_plain(bot, 1, '📄 <blockquote expandable>raw</blockquote>'))
        self.assertTrue(ok)
        self.assertEqual(bot.send_message.await_args_list[0].kwargs["parse_mode"], "HTML")
        self.assertIsNone(bot.send_message.await_args_list[1].kwargs["parse_mode"])

    def test_long_chart_report_uses_safe_caption_then_full_html_message(self):
        from signal_copy.telegram_transport import send_parser_notification
        import signal_copy.telegram_transport as transport
        bot = AsyncMock()
        transport._parser_bot = bot
        report = "X" * 1020 + "<blockquote expandable>raw</blockquote>"
        with patch.object(transport, "BOT_AVAILABLE", True), \
             patch.object(transport, "PARSER_BOT_TOKEN", "token"), \
             patch.object(transport, "_ensure_bot_ready", new=AsyncMock()), \
             patch.dict("sys.modules", {"telegram": types.SimpleNamespace(InputFile=lambda fh, filename: fh)}), \
             patch("builtins.open", unittest.mock.mock_open(read_data=b"x")):
            self.assertTrue(asyncio.run(send_parser_notification(report, "/tmp/chart.png")))
        caption = bot.send_photo.await_args.kwargs["caption"]
        self.assertLessEqual(len(caption), 1024)
        self.assertNotIn("<blockquote", caption)
        self.assertEqual(bot.send_message.await_args.kwargs["text"], report)

    def test_transient_failure_retries_then_succeeds(self):
        send = AsyncMock(side_effect=[TimeoutError("slow"), None])
        with patch("signal_copy.telegram_transport.asyncio.sleep", new=AsyncMock()):
            ok = asyncio.run(_send_with_retry(send, attempts=3))
        self.assertTrue(ok)
        self.assertEqual(send.await_count, 2)

    def test_permanent_failure_stops_after_attempt_limit(self):
        send = AsyncMock(side_effect=TimeoutError("slow"))
        with patch("signal_copy.telegram_transport.asyncio.sleep", new=AsyncMock()):
            ok = asyncio.run(_send_with_retry(send, attempts=3))
        self.assertFalse(ok)
        self.assertEqual(send.await_count, 3)


if __name__ == "__main__":
    unittest.main()
