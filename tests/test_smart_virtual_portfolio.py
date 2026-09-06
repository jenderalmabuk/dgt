import asyncio
import json
from copy import deepcopy
from unittest.mock import patch

from execution.smart_virtual_portfolio import SmartVirtualPortfolioBook
from gateway.order_intent import OrderIntent
from gateway.service import ExecutionGateway


def bars():
    rows = []
    for i in range(20):
        close = 100 + i * .2
        rows.append({"open_time": f"2026-08-28T{(i // 4):02d}:{(i % 4) * 15:02d}:00+00:00",
                     "open": close - .1, "high": close + .5, "low": close - .5,
                     "close": close, "volume": 1000})
    rows.append({"open_time": "2026-08-28T05:00:00+00:00", "open": 104,
                 "high": 999, "low": 1, "close": 500, "volume": 1000})
    return rows


def intent(symbol="BTCUSDT", source="SIGNAL_COPY"):
    return OrderIntent(source=source, symbol=symbol, side="LONG", entry_price=104,
                       sl_price=95, tps=[110], notional=100,
                       adv_snapshot={"regime": "TRENDING", "mtf_alignment": "BULLISH",
                                     "cvd_trend": "BULLISH"})


class Trader:
    def __init__(self):
        self.positions = {"canonical": {"symbol": "OLDUSDT"}}

    async def _get_fresh_open_quote(self, symbol):
        return {"price": 104, "price_fresh": True, "price_source": "test"}

    async def _get_completed_bars(self, symbol, *, tf="15m", limit=21):
        assert tf == "15m"
        return bars()

    async def submit_open(self, **kwargs):
        raise AssertionError("canonical submit must remain blocked")


class Risk:
    def __init__(self):
        self.trader = None

    def get_current_equity(self): return 1000
    def get_max_running_positions(self): return 1
    def _position_count(self): return 1


def test_capture_opens_router_plan_with_fresh_quote_and_persists(tmp_path):
    trader = Trader(); original = deepcopy(trader.positions)
    book = SmartVirtualPortfolioBook(trader, state_path=tmp_path / "book.json",
                                     outcomes_path=tmp_path / "outcomes.jsonl", equity=1000)
    result = asyncio.run(book.capture(intent()))
    assert result["opened"] is True
    pos = book.positions["BTCUSDT"]
    assert pos["raw_entry_price"] == 104
    assert pos["entry_price"] > pos["raw_entry_price"]
    assert pos["plan"]["selected_plan"] == "smart_exit_wide_trend_v1"
    assert pos["risk_usd"] == pos["plan"]["economic_risk_usd"]
    assert book.equity < 1000  # entry fee charged
    assert trader.positions == original
    restored = SmartVirtualPortfolioBook(trader, state_path=tmp_path / "book.json",
                                         outcomes_path=tmp_path / "outcomes.jsonl", equity=1)
    assert restored.positions == book.positions
    assert restored.equity == book.equity


def test_capture_enforces_duplicate_slots_and_ten_percent_open_risk(tmp_path):
    trader = Trader()
    book = SmartVirtualPortfolioBook(trader, state_path=tmp_path / "s", outcomes_path=tmp_path / "o",
                                     equity=1000, max_positions=1)
    assert asyncio.run(book.capture(intent()))["opened"]
    assert asyncio.run(book.capture(intent()))["reason"] == "DUPLICATE_SYMBOL"
    assert asyncio.run(book.capture(intent("ETHUSDT")))["reason"] == "MAX_VIRTUAL_POSITIONS"
    book.positions.clear(); book.max_positions = 30; book.open_risk_cap = 0
    assert asyncio.run(book.capture(intent("ETHUSDT")))["reason"] == "VIRTUAL_OPEN_RISK_CAP"


def test_advance_closes_once_releases_risk_and_writes_fee_net_outcome(tmp_path):
    trader = Trader(); outcome = tmp_path / "outcomes.jsonl"
    book = SmartVirtualPortfolioBook(trader, state_path=tmp_path / "state.json",
                                     outcomes_path=outcome, equity=1000)
    asyncio.run(book.capture(intent()))
    plan = book.positions["BTCUSDT"]["plan"]
    next_rows = bars(); next_rows[-2] = {"open_time": "2026-08-28T06:00:00+00:00",
        "open": 104, "high": 104.1, "low": plan["sl_price"] - 1, "close": 100, "volume": 1}
    trader._get_completed_bars = lambda *a, **k: _async(next_rows)
    risk = book.open_risk_usd
    assert asyncio.run(book.advance())[0]["exit_reason"] == "SMART_SL"
    assert book.open_risk_usd == risk - plan["economic_risk_usd"]
    row = json.loads(outcome.read_text().strip())
    assert row["entry_fee_usd"] > 0 and row["exit_fee_usd"] > 0
    assert row["net_pnl_usd"] == row["gross_pnl_usd"] - row["entry_fee_usd"] - row["exit_fee_usd"]
    assert asyncio.run(book.advance()) == []


async def _async(value):
    return value


def test_gateway_captures_after_validation_before_capacity_rejection_only_paper_signal_copy(tmp_path):
    trader = Trader(); risk = Risk(); risk.trader = trader
    env = {"GATEWAY_PAPER_MAINNET": "true", "SMART_VIRTUAL_BOOK_STATE": str(tmp_path / "state"),
           "SMART_VIRTUAL_BOOK_OUTCOMES": str(tmp_path / "outcomes")}
    with patch.dict("os.environ", env, clear=False):
        gateway = ExecutionGateway(trader, risk)
        result = asyncio.run(gateway.execute(intent()))
    assert result.reason == "max running positions reached (1)"
    assert "BTCUSDT" in gateway.smart_virtual_book.positions

    with patch.dict("os.environ", {**env, "GATEWAY_PAPER_MAINNET": "false"}, clear=False):
        live = ExecutionGateway(trader, risk)
        asyncio.run(live.execute(intent("ETHUSDT")))
    assert live.smart_virtual_book is None

    with patch.dict("os.environ", env, clear=False):
        other = ExecutionGateway(trader, risk)
        asyncio.run(other.execute(intent("ETHUSDT", "TEST")))
    assert other.smart_virtual_book is None
