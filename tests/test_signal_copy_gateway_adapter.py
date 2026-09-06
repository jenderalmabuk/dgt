import asyncio

from gateway.adapters.signal_copy_adapter import GatewayTraderShim


class Client:
    def __init__(self, result):
        self.result = result

    async def execute(self, intent):
        return self.result


def test_success_exposes_confirmable_fill_from_nested_trader_response():
    result = {
        "ok": True,
        "reason": "opened",
        "notional": 125.0,
        "trader_response": {
            "ok": True,
            "entry_price": 8.31,
            "notional": 124.8,
        },
    }
    opened = asyncio.run(GatewayTraderShim(Client(result)).submit_open(
        symbol="LINKUSDT", side="LONG", entry_price=8.3, sl=7.9,
        tp1=8.5, notional=125.0, signal_id="sig1",
    ))
    assert opened["ok"] is True
    assert opened["entry_price"] == 8.31
    assert opened["notional"] == 124.8
    assert opened["position_confirmed"] is True


def test_success_without_positive_fill_is_not_confirmed():
    result = {"ok": True, "reason": "opened", "notional": 125.0, "trader_response": {"ok": True}}
    opened = asyncio.run(GatewayTraderShim(Client(result)).submit_open(
        symbol="LINKUSDT", side="LONG", entry_price=8.3, sl=7.9,
        tp1=8.5, notional=125.0, signal_id="sig2",
    ))
    assert opened["position_confirmed"] is False
