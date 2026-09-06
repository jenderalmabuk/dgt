from pathlib import Path


def test_pending_fill_has_no_drift_block_and_checks_actual_open_cap():
    source = Path("signal_copy/orchestrator.py").read_text()
    poll = source[source.index("async def check_pending_limits"):]
    assert "LIMIT_FILL_DRIFT_EXCEEDED" not in poll
    assert "DRIFT_REJECTED" not in poll
    assert "_gate_on_max_positions(sig)" in poll


def test_new_pending_is_unlimited():
    source = Path("signal_copy/orchestrator.py").read_text()
    route = source[source.index("if routing.action == EntryAction.LIMIT"):source.index("# Parse-but-don't-execute")]
    assert "_gate_on_max_positions" not in route
