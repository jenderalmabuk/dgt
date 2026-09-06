import json


def test_lifecycle_tracks_distinct_execution_truth(tmp_path, monkeypatch):
    import signal_copy.signal_lifecycle as lifecycle

    monkeypatch.setattr(lifecycle, "JOURNAL_PATH", tmp_path / "lifecycle.jsonl")
    lifecycle.record("abc", "VALIDATED", symbol="BTCUSDT", execution_intended=True)
    lifecycle.record("abc", "GATEWAY_REJECTED", reason="bad fill", gateway_accepted=False)

    latest = lifecycle.latest("abc")
    assert latest["stage"] == "GATEWAY_REJECTED"
    assert latest["execution_intended"] is True
    assert latest["gateway_accepted"] is False
    assert latest["position_confirmed"] is False
    assert latest["terminal"] is True
    assert len(lifecycle.events("abc")) == 2
