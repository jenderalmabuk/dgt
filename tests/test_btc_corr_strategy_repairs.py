#!/usr/bin/env python3
import pandas as pd

from fusionnew.clean_core.engine import (
    _closed_at_or_before, btc_corr_allows, btc_vwap15_regime,
    dynamic_fib_expiry_bars, journal_once, prune_journal_seen,
    setup_observability, smart_exit_step,
)


def test_bull_regime_blocks_all_bear_setups():
    for note in ("FOLLOWING_BTC", "PARTIAL_DECOUPLE", "DECOUPLED", "NEGATIVE_RELATION"):
        assert not btc_corr_allows("BEAR", "bull", note, True), note


def test_vwap15_regime_and_side_permissions():
    frame = pd.DataFrame({
        "open_time": pd.date_range("2026-08-01", periods=20, freq="15min", tz="UTC"),
        "high": [101.0] * 20,
        "low": [99.0] * 20,
        "close": [100.0] * 19 + [100.2],
        "volume": [10.0] * 20,
    })
    bull = btc_vwap15_regime(frame, buffer_pct=0.0005, atr_mult=0.0)
    assert bull["regime"] == "bull"
    assert btc_corr_allows("BULL", bull["regime"], "FOLLOWING_BTC", True)
    assert not btc_corr_allows("BEAR", bull["regime"], "FOLLOWING_BTC", True)
    frame.loc[19, "close"] = 99.8
    bear = btc_vwap15_regime(frame, buffer_pct=0.0005, atr_mult=0.0)
    assert bear["regime"] == "bear"
    assert not btc_corr_allows("BULL", bear["regime"], "FOLLOWING_BTC", True)
    assert btc_corr_allows("BEAR", bear["regime"], "FOLLOWING_BTC", True)
    frame.loc[19, "close"] = 100.01
    neutral = btc_vwap15_regime(frame, buffer_pct=0.0005, atr_mult=0.0)
    assert neutral["regime"] == "neutral"
    assert btc_corr_allows("BULL", neutral["regime"], "PARTIAL_DECOUPLE", True)
    assert btc_corr_allows("BEAR", neutral["regime"], "PARTIAL_DECOUPLE", True)
    assert not btc_corr_allows("BULL", neutral["regime"], "NEGATIVE_RELATION", True)


def test_dynamic_fib_expiry_only_extends_strong_aligned():
    assert dynamic_fib_expiry_bars("BULL", "bull", "FOLLOWING_BTC", False, 12, 24)[0] == 24
    assert dynamic_fib_expiry_bars("BEAR", "bear", "PARTIAL_DECOUPLE", False, 12, 24)[0] == 24
    assert dynamic_fib_expiry_bars("BULL", "neutral", "FOLLOWING_BTC", False, 12, 24)[0] == 12
    assert dynamic_fib_expiry_bars("BULL", "bull", "NEGATIVE_RELATION", True, 12, 24)[0] == 12


def test_smart_exit_uses_prior_floor_before_closed_m5_update():
    trade = {"side": "BUY", "entry": 100.0, "sl": 90.0, "smart_peak_r": 0.0, "smart_floor_r": None}
    # Same closed M5 bar reaches +1R but closes below future +0.25R floor: arm only, no lookahead exit.
    exit_price, reason = smart_exit_step(trade, hi=111.0, lo=99.0, close=101.0, closed_m5=True)
    assert exit_price is None and reason is None
    assert trade["smart_peak_r"] == 0.1
    assert trade["smart_floor_r"] is None


def test_smart_exit_tiers_and_giveback():
    trade = {"side": "BUY", "entry": 100.0, "sl": 90.0}
    assert smart_exit_step(trade, 111.0, 100.0, 111.0, True) == (None, None)
    assert trade["smart_peak_r"] == 1.1 and trade["smart_floor_r"] == 0.25
    assert smart_exit_step(trade, 106.0, 102.0, 102.0, True) == (102.0, "SMART_EXIT_1R_GIVEBACK")
    trade = {"side": "BUY", "entry": 100.0, "sl": 90.0}
    smart_exit_step(trade, 121.0, 110.0, 121.0, True)
    assert trade["smart_floor_r"] == 1.0
    assert smart_exit_step(trade, 112.0, 109.0, 109.0, True) == (109.0, "SMART_EXIT_2R_GIVEBACK")



def test_setup_observability_excludes_future_rows():
    idx = pd.date_range("2026-08-01", periods=22, freq="30min", tz="UTC")
    frame = pd.DataFrame({"open": range(100, 122), "high": range(102, 124),
                          "low": range(99, 121), "close": range(101, 123),
                          "volume": [10.0] * 21 + [10000.0]}, index=idx)
    got = setup_observability(frame, frame, idx[-2])
    assert got["pair_volume_ratio"] == 1.0
    assert got["pair_ret_1"] == 121 / 120 - 1


def test_runtime_range_index_uses_open_time_column():
    frame = pd.DataFrame({"open_time": pd.date_range("2026-08-01", periods=3, freq="30min", tz="UTC"),
                          "close": [100.0, 101.0, 999.0]})
    got = _closed_at_or_before(frame, "2026-08-01T00:30:00Z")
    assert got["close"].tolist() == [100.0, 101.0]


def test_none_frame_fails_closed():
    assert _closed_at_or_before(None, "2026-08-01T00:30:00Z").empty
    assert setup_observability(pd.DataFrame(), None, "2026-08-01T00:30:00Z") == {}


def test_journal_seen_prunes_old_setup_keys():
    seen = {("btc_corr_check", "OLD", "BULL", "2026-01-01T00:00:00Z"),
            ("btc_corr_check", "NEW", "BULL", "2099-01-01T00:00:00Z")}
    assert prune_journal_seen(seen, days=7) == {("btc_corr_check", "NEW", "BULL", "2099-01-01T00:00:00Z")}


def test_journal_once_survives_repeated_cycles():
    seen = set()
    assert journal_once(seen, "btc_corr_check", "BTCUSDT", "BULL", "2026-08-31T00:00:00")
    assert not journal_once(seen, "btc_corr_check", "BTCUSDT", "BULL", "2026-08-31T00:00:00")
    restored = set(tuple(x) for x in [list(x) for x in seen])
    assert not journal_once(restored, "btc_corr_check", "BTCUSDT", "BULL", "2026-08-31T00:00:00")
    assert journal_once(restored, "btc_corr_reject", "BTCUSDT", "BULL", "2026-08-31T00:00:00")


def test_smart_exit_ignores_sell_and_non_m5_bar():
    sell = {"side": "SELL", "entry": 100.0, "sl": 110.0}
    assert smart_exit_step(sell, 101.0, 89.0, 89.0, True) == (None, None)
    assert "smart_peak_r" not in sell
    buy = {"side": "BUY", "entry": 100.0, "sl": 90.0}
    assert smart_exit_step(buy, 120.0, 100.0, 120.0, False) == (None, None)
    assert "smart_peak_r" not in buy


if __name__ == "__main__":
    test_bull_regime_blocks_all_bear_setups()
    test_vwap15_regime_and_side_permissions()
    test_dynamic_fib_expiry_only_extends_strong_aligned()
    test_smart_exit_uses_prior_floor_before_closed_m5_update()
    test_smart_exit_tiers_and_giveback()

    test_setup_observability_excludes_future_rows()
    test_runtime_range_index_uses_open_time_column()
    test_none_frame_fails_closed()
    test_journal_seen_prunes_old_setup_keys()
    test_journal_once_survives_repeated_cycles()
    test_smart_exit_ignores_sell_and_non_m5_bar()
    print("btc_corr_strategy_repairs: PASS")
