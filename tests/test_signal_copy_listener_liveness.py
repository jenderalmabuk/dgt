import unittest
from unittest.mock import AsyncMock

from signal_copy.listeners.telegram_listener import TelegramSignalListener


class TelegramSignalListenerLivenessTest(unittest.IsolatedAsyncioTestCase):
    async def test_unexpected_telethon_return_raises_so_container_restarts(self):
        listener = TelegramSignalListener(
            AsyncMock(), api_id=1, api_hash="hash", session_name=":memory:"
        )
        listener._start_telethon = AsyncMock(return_value=True)
        listener.running = True

        with self.assertRaisesRegex(RuntimeError, "Telethon listener stopped unexpectedly"):
            await listener.start()

    async def test_no_available_backend_raises_instead_of_zombie_loop(self):
        listener = TelegramSignalListener(AsyncMock())
        listener._start_aiogram = AsyncMock(return_value=False)

        with self.assertRaisesRegex(RuntimeError, "no Telegram backend"):
            await listener.start()


if __name__ == "__main__":
    unittest.main()
