from signal_copy.fresh_quote import choose_quote


def test_conflicting_exchanges_are_advisory_not_terminal():
    q = choose_quote("BEATUSDT", 1.02, 1.00)
    assert q["price"] == 1.02
    assert q["price_fresh"] is True
    assert q["price_source"] == "binance_futures_rest"
    assert q["cross_exchange_divergence_pct"] > 1


def test_strong_exchange_conflict_fails_closed():
    q = choose_quote("ARCUSDT", 0.05155, 0.07072)
    assert q["price_fresh"] is False
    assert q["price_source"] == "strong_multi_exchange_conflict"


def test_one_live_exchange_is_usable():
    q = choose_quote("ARCUSDT", 0, 0.07072)
    assert q["price"] == 0.07072
    assert q["price_source"] == "bybit_linear_rest"
