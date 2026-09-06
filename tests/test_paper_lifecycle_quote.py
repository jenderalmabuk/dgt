from execution.paper_mainnet_trader import PaperMainnetTrader


def test_lifecycle_price_accepts_fresh_multi_exchange_quote():
    quote = {
        "price": 0.002355,
        "secondary_price": 0.002356,
        "price_fresh": True,
        "price_source": "binance_futures_rest",
    }
    assert PaperMainnetTrader._lifecycle_price(quote) == 0.002355


def test_lifecycle_price_rejects_unavailable_or_conflicting_quote():
    assert PaperMainnetTrader._lifecycle_price({"price": 0, "price_fresh": False}) is None


def test_short_tp_uses_same_fresh_lifecycle_price():
    pos = {"side": "SHORT"}
    assert PaperMainnetTrader._level_hit(pos, 0.002355, 0.002361, favorable=True)
    assert not PaperMainnetTrader._level_hit(pos, 0.002355, 0.0025246, favorable=False)
