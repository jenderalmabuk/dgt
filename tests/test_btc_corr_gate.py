import numpy as np
import pandas as pd

from fusionnew.clean_core.engine import btc_corr_context, btc_corr_allows, unique_setups


def test_btc_corr_labels_and_countertrend_gate():
    idx = pd.date_range("2026-01-01", periods=21, freq="15min", tz="UTC")
    returns = np.linspace(-0.02, 0.02, 20)
    btc = pd.Series(100 * np.r_[1.0, np.cumprod(1 + returns)], index=idx)
    follower = pd.Series(200 * np.r_[1.0, np.cumprod(1 + returns)], index=idx)
    inverse = pd.Series(200 * np.r_[1.0, np.cumprod(1 - returns)], index=idx)
    assert btc_corr_context(follower, btc)["note"] == "FOLLOWING_BTC"
    assert btc_corr_context(inverse, btc)["note"] == "NEGATIVE_RELATION"
    assert not btc_corr_allows("BULL", "bear", "FOLLOWING_BTC", True)
    assert btc_corr_allows("BULL", "bear", "DECOUPLED", True)
    assert btc_corr_allows("BEAR", "bull", "NEGATIVE_RELATION", True)


def test_timestamp_alignment_rejects_mismatched_bars():
    btc_idx = pd.date_range("2026-01-01", periods=21, freq="15min", tz="UTC")
    pair_idx = btc_idx + pd.Timedelta(minutes=3)
    btc = pd.Series(np.arange(100.0, 121.0), index=btc_idx)
    pair = pd.Series(np.arange(200.0, 221.0), index=pair_idx)
    assert btc_corr_context(pair, btc)["note"] == "BTC_CORR_UNAVAILABLE"


def test_unknown_correlation_falls_back_to_existing_btc_gate():
    assert not btc_corr_allows("BULL", "bear", "UNKNOWN", False)
    assert btc_corr_allows("BULL", "bear", "UNKNOWN", True)
    assert btc_corr_allows("BEAR", "bear", "FOLLOWING_BTC", False)


def test_deduplicates_same_setup():
    a = {"side": "BEAR", "t_complete": "2026-01-01T00:00:00Z", "entry": 1.0}
    assert unique_setups([a, dict(a)]) == [a]


if __name__ == "__main__":
    test_btc_corr_labels_and_countertrend_gate()
    test_timestamp_alignment_rejects_mismatched_bars()
    test_unknown_correlation_falls_back_to_existing_btc_gate()
    test_deduplicates_same_setup()
    print("btc_corr_gate: PASS")
