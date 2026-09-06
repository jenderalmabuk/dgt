from execution.paper_mainnet_trader import PaperMainnetTrader


def test_false_cached_sl_requires_fresh_exchange_confirmation():
    pos = {"side": "LONG", "sl_price": 0.012}
    quote = {
        "price": 0.012532,
        "secondary_price": 0.012572,
        "price_fresh": True,
        "price_source": "binance_futures_rest",
    }
    assert PaperMainnetTrader._confirmed_sl_price(pos, 0.011994, quote) is None


def test_long_sl_confirmed_by_fresh_primary_quote():
    pos = {"side": "LONG", "sl_price": 0.012}
    quote = {
        "price": 0.01199,
        "secondary_price": 0.01201,
        "price_fresh": True,
        "price_source": "binance_futures_rest",
    }
    assert PaperMainnetTrader._confirmed_sl_price(pos, 0.01198, quote) == 0.01199


def test_short_sl_confirmed_by_fresh_primary_quote():
    pos = {"side": "SHORT", "sl_price": 0.012}
    quote = {
        "price": 0.01201,
        "secondary_price": 0.01199,
        "price_fresh": True,
        "price_source": "binance_futures_rest",
    }
    assert PaperMainnetTrader._confirmed_sl_price(pos, 0.01202, quote) == 0.01201


def test_unavailable_fresh_quote_fails_closed_without_sl():
    pos = {"side": "LONG", "sl_price": 0.012}
    quote = {"price": 0.0, "price_fresh": False, "price_source": "fresh_price_unavailable"}
    assert PaperMainnetTrader._confirmed_sl_price(pos, 0.0119, quote) is None
