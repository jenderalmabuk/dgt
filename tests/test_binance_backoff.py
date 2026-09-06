def test_rate_limit_backoff(monkeypatch):
    import collector.binance as b

    monkeypatch.setattr(b.time, "time", lambda: 1000.0)
    b._rate_limited_until = 0.0
    assert b._rate_limited() is False
    b._trip_rate_limit()
    assert b._rate_limited() is True
    assert b._rate_limited_until == 1300.0
