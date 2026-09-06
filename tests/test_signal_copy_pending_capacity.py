import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from signal_copy.orchestrator import SignalCopyOrchestrator
from signal_copy.executor import ExecutionOutcome
from signal_copy.signal_schema import ParsedSignal, SignalSide


def test_touched_pending_at_capacity_remains_waiting_for_slot():
    sig = ParsedSignal(
        symbol="WAITUSDT", side=SignalSide.LONG,
        entry_low=100, entry_high=110, active_entry=110,
        stop_loss=90, take_profits=[120, 130], signal_id="wait-slot",
    )
    result = SimpleNamespace(signal=sig, metrics_snapshot={}, score=80)
    orch = SignalCopyOrchestrator.__new__(SignalCopyOrchestrator)
    orch._pending_limits = {
        sig.signal_id: {"result": result, "created": time.time(),
                        "path_high": 115.0, "path_low": 115.0}
    }
    orch._pending_restored = True
    orch._fetch_execution_quote = AsyncMock(return_value={"price": 109.0, "price_fresh": True})
    orch._gate_on_max_positions = AsyncMock(return_value=True)
    orch._notify_trades_channel = AsyncMock()
    orch.confirmations = SimpleNamespace(mark=AsyncMock())
    orch.executor = SimpleNamespace(execute=AsyncMock())

    with patch("signal_copy.pending_journal.record") as record:
        asyncio.run(orch.check_pending_limits())

    assert sig.signal_id in orch._pending_limits
    assert any(call.args[0] == "WAITING_FOR_SLOT" for call in record.call_args_list)
    orch.executor.execute.assert_not_awaited()


def _pending_orchestrator(price=109.0):
    sig = ParsedSignal(
        symbol="WAITUSDT", side=SignalSide.LONG,
        entry_low=100, entry_high=110, active_entry=110,
        stop_loss=90, take_profits=[120, 130], signal_id="wait-slot",
    )
    result = SimpleNamespace(signal=sig, metrics_snapshot={}, score=80)
    orch = SignalCopyOrchestrator.__new__(SignalCopyOrchestrator)
    orch._pending_limits = {sig.signal_id: {
        "result": result, "created": time.time(), "path_high": 115.0, "path_low": 115.0}}
    orch._pending_restored = True
    orch._fetch_execution_quote = AsyncMock(return_value={"price": price, "price_fresh": True})
    orch._gate_on_max_positions = AsyncMock(return_value=False)
    orch._notify_trades_channel = AsyncMock()
    orch.dry_run = False
    orch.confirmations = SimpleNamespace(mark=AsyncMock())
    orch.sizer = SimpleNamespace(calc=lambda *args: 0.01)
    return orch, sig


def test_pending_adverse_drift_over_point_one_r_does_not_fill():
    orch, sig = _pending_orchestrator(price=107.9)
    orch.executor = SimpleNamespace(execute=AsyncMock())
    with patch("signal_copy.pending_journal.record") as record:
        asyncio.run(orch.check_pending_limits())
    assert sig.signal_id in orch._pending_limits
    assert any(call.args[0] == "DRIFT_WAITING" for call in record.call_args_list)
    orch.executor.execute.assert_not_awaited()


def test_gateway_capacity_race_rejection_retains_pending_waiting_for_slot():
    orch, sig = _pending_orchestrator()
    orch.executor = SimpleNamespace(execute=AsyncMock(return_value=ExecutionOutcome(
        False, "MAX_OPEN_POSITIONS", sig.symbol, sig.side.value,
        gateway_accepted=False, position_confirmed=False)))
    with patch("signal_copy.pending_journal.record") as record:
        asyncio.run(orch.check_pending_limits())
    assert sig.signal_id in orch._pending_limits
    assert any(call.args[0] == "WAITING_FOR_SLOT" for call in record.call_args_list)
