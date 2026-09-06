from collector.binance import KLINE_TFS, _due_tfs


def test_only_due_timeframes_are_fetched():
    last = {tf: 1_000.0 for tf in KLINE_TFS}
    assert _due_tfs(1_059.0, last) == []
    assert _due_tfs(1_060.0, last) == ["1m"]
    assert _due_tfs(1_300.0, last) == ["1m", "3m", "5m"]