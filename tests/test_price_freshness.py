from nexus.data_bridge import price_metadata


def test_price_metadata_marks_stale_cache_unsafe_for_execution():
    meta = price_metadata("stale_cache", 1900.0)
    assert meta == {
        "price_source": "stale_cache",
        "price_age_sec": 1900.0,
        "price_fresh": False,
    }


def test_price_metadata_marks_live_rest_fresh():
    assert price_metadata("binance_rest", 0.0)["price_fresh"] is True
