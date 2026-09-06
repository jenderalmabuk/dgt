import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from execution.paper_mainnet_trader import PaperMainnetTrader
from signal_copy.telegram_formatter import build_close_message, build_execution_message


def test_fill_notice_shows_actual_fill_truth():
    outcome = SimpleNamespace(
        position_confirmed=True, reason="FILLED", entry_price=100.0, notional=500.0,
        sl_price=95.0, tp1=105.0, tp_full=120.0, risk_amount=25.0,
        entry_drift_r=0.1, rr_tp1=1.0, rr_full=4.0,
        raw={"qty": 5.0, "entry_fee": 0.25, "max_loss_usd_at_fill": 25.75},
    )
    sig = SimpleNamespace(symbol="ABCUSDT", side="LONG", take_profits=[105.0, 110.0, 120.0])
    msg = build_execution_message(outcome, sig, None)
    assert "Qty: 5" in msg
    assert "TP1: 105" in msg and "TP2: 110" in msg and "TP3: 120" in msg
    assert "Entry Fee: $0.25" in msg
    assert "Actual-fill Risk: $25.75" in msg


def test_partial_notice_is_explicit_and_complete():
    msg = build_close_message({
        "symbol": "ABCUSDT", "side": "LONG", "reason": "TP1_PARTIAL",
        "is_partial": True, "exit_price": 105.0, "entry_price": 100.0,
        "qty": 2.0, "remaining_qty": 3.0, "sl_original": 95.0,
        "next_tp": 110.0, "rr": 1.0, "fee_usd": 0.10,
        "pnl_pct": 5.0, "pnl_usd": 9.9,
    })
    assert "TP PARTIAL" in msg
    for text in ("Qty: 2", "Remaining: 3", "Entry: 100", "Original SL: 95",
                 "Next TP: 110", "RR: 1.00R", "Fee: $0.10"):
        assert text in msg


def test_final_notice_shows_entry_qty_notional_rr_and_fee():
    msg = build_close_message({
        "symbol": "ABCUSDT", "side": "LONG", "reason": "TP3",
        "exit_price": 120.0, "entry_price": 100.0, "qty": 3.0,
        "notional_usd": 300.0, "rr": 4.0, "fee_usd": 0.30,
        "pnl_pct": 20.0, "pnl_usd": 59.7,
    })
    for text in ("Entry: 100", "Qty: 3", "Notional: $300.00", "RR: 4.00R", "Fee: $0.30"):
        assert text in msg


def test_partial_fill_ledger_is_append_only_and_notice_gets_truth():
    trader = PaperMainnetTrader()
    trader.risk_mgr = None
    pos = {
        "symbol": "ABCUSDT", "side": "LONG", "entry_price": 100.0,
        "sl_price": 95.0, "initial_qty": 5.0, "qty": 3.0,
        "tp_ladder": [105.0, 110.0, 120.0], "next_tp_index": 1,
        "entry_fee": 0.25, "opened_at": datetime.now(timezone.utc),
    }
    trader._notify_close = AsyncMock()
    with patch.object(trader, "_adverse_fill", return_value=105.0), patch.object(trader, "_fee", return_value=0.1):
        asyncio.run(trader._realize_partial(pos, 105.0, 2.0, "TP1_PARTIAL"))
    first = dict(pos["fill_ledger"][0])
    with patch.object(trader, "_adverse_fill", return_value=110.0), patch.object(trader, "_fee", return_value=0.1):
        asyncio.run(trader._realize_partial(pos, 110.0, 1.0, "TP2_PARTIAL"))
    assert pos["fill_ledger"][0] == first
    assert len(pos["fill_ledger"]) == 2
    payload = trader._notify_close.await_args_list[0].args[0]
    assert payload["is_partial"] is True
    assert payload["qty"] == 2.0 and payload["remaining_qty"] == 3.0
    assert payload["entry_price"] == 100.0 and payload["next_tp"] == 110.0
    assert payload["rr"] == 1.0 and payload["fee_usd"] == 0.1
