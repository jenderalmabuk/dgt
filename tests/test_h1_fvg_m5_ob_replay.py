import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fusionnew.backtest.h1_fvg_m5_ob_replay import causal_setups, engulfing_setups, oi_spike_setups, net_r, time_split


def frame(rows, freq="5min"):
    start = "2025-12-31 22:00" if freq == "1h" else "2026-01-01 01:00"
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"],
                        index=pd.date_range(start, periods=len(rows), freq=freq)).reset_index(names="open_time")


def test_decision_occurs_after_m5_fvg_closes_and_sl_uses_ob():
    h1 = frame([
        (100, 102, 99, 101, 10),
        (101, 104, 100, 103, 10),
        (106, 108, 105, 107, 10),  # bullish H1 FVG: 102..105
    ], "1h")
    m5 = frame([
        (106, 106.5, 105.5, 106, 10),
        (105, 105.3, 103.0, 103.5, 10),  # retest H1 FVG; bearish OB candidate
        (103.5, 106.0, 103.4, 105.8, 10),
        (106.2, 108.0, 106.1, 107.8, 10),  # displacement closes; M5 FVG 105.3..106.1
        (107.8, 108.0, 105.0, 105.8, 10),  # only now may fill
    ])
    setups = causal_setups(h1, m5, atr_period=2, displacement_atr=0.5)
    assert len(setups) == 1
    s = setups[0]
    assert s["side"] == "BULL"
    assert s["decision_idx"] == 3
    assert s["fill_start_idx"] == 4
    assert s["sl"] < 103.0
    assert 105.3 <= s["entry"] <= 106.1


def test_rejects_ob_without_displacement_fvg():
    h1 = frame([(100, 102, 99, 101, 10), (101, 104, 100, 103, 10), (106, 108, 105, 107, 10)], "1h")
    m5 = frame([(106, 106.5, 105.5, 106, 10), (105, 105.3, 103, 103.5, 10),
                (103.5, 104.5, 103.4, 104, 10), (104, 105.2, 103.8, 105, 10)])
    assert causal_setups(h1, m5, atr_period=2, displacement_atr=0.5) == []


def test_h1_fvg_available_only_after_h1_close():
    h1 = frame([(100, 102, 99, 101, 10), (101, 104, 100, 103, 10), (106, 108, 105, 107, 10)], "1h")
    m5 = frame([(105, 105.3, 103, 103.5, 10), (103.5, 106, 103.4, 105.8, 10),
                (106.2, 108, 106.1, 107.8, 10)])
    m5["open_time"] -= pd.Timedelta(hours=1)
    assert causal_setups(h1, m5, atr_period=2, displacement_atr=0.5) == []


def test_rejects_m5_setup_after_h1_fvg_invalidation():
    h1 = frame([(100, 102, 99, 101, 10), (101, 104, 100, 103, 10),
                (106, 108, 105, 107, 10), (104, 105, 101, 101.5, 10)], "1h")
    m5 = frame([(105, 105.3, 103, 103.5, 10), (103.5, 106, 103.4, 105.8, 10),
                (106.2, 108, 106.1, 107.8, 10)])
    m5["open_time"] += pd.Timedelta(hours=1)
    assert causal_setups(h1, m5, atr_period=2, displacement_atr=0.5) == []


def test_full_wick_engulfing_volume_confirmation():
    # Build M5 with valid causal pattern (3 candles before engulfing at idx 22):
    # idx 19: Candle 1 (OB): bearish (close < open) for BULL side
    # idx 20: Candle 2: transitional
    # idx 21: Candle 3 (FVG): low > candle1.high (creates FVG up) + displaced
    # idx 22: engulfing candle (full wick)
    rows = [(100, 101, 99, 100, 10)] * 18  # 18 base candles for volume median
    rows += [
        (102, 102.5, 101.5, 101.8, 10),  # idx 18: bearish (C < O: 101.8 < 102)
        (101.8, 102, 101.6, 101.7, 10),  # idx 19: transitional
        (101.7, 104.5, 103.0, 104.2, 10),  # idx 20: FVG up (low 103 > c1.high 102.5) + displacement
        (104.2, 105, 104, 104.1, 10),   # idx 21: prev (bearish)
        (104.1, 106, 103, 106, 30),   # idx 22: engulfing (full wick, bullish, vol spike)
        (106, 106, 104, 105, 10),
    ]
    m5 = frame(rows)
    base = [{"side": "BULL", "decision_idx": 19, "invalidated": None}]
    s = engulfing_setups(base, m5, volume_ratio=1.5, body_ratio=0.6,
                         swing_bars=5, min_risk_pct=0.45, atr_period=14, displacement_atr=0.5)
    assert len(s) == 1 and s[0]["decision_idx"] == 22 and s[0]["fill_start_idx"] == 23
    assert 104.0 <= s[0]["entry"] <= 105.0 and s[0]["sl"] < s[0]["entry"]


def test_rejects_body_only_engulfing_or_weak_volume():
    # Must have causal pattern too
    rows = [(100, 101, 99, 100, 10)] * 18
    rows += [
        (102, 102.5, 101.5, 101.8, 10),  # bearish OB
        (101.8, 102, 101.6, 101.7, 10),  # transitional
        (101.7, 104, 103, 103.9, 10),  # FVG up + displacement
        (103.9, 104.5, 103.5, 103.6, 10),  # prev bearish
        (103.5, 104.4, 103.4, 104.3, 20)  # body engulfs, wick does not (H<prev.H, L>prev.L)
    ]
    m5 = frame(rows); base = [{"side": "BULL", "decision_idx": 19, "invalidated": None}]
    assert engulfing_setups(base, m5, volume_ratio=1.5, body_ratio=0.6, displacement_atr=0.5) == []
    # Fix full wick but kill volume
    m5.iloc[22, m5.columns.get_loc('high')] = 105
    m5.iloc[22, m5.columns.get_loc('low')] = 103
    m5.iloc[22, m5.columns.get_loc('volume')] = 14
    assert engulfing_setups(base, m5, volume_ratio=1.5, body_ratio=0.6, displacement_atr=0.5) == []


def test_engulfing_rr_changes_only_tp():
    rows = [(100, 101, 99, 100, 10)] * 18
    rows += [
        (102, 102.5, 101.5, 101.8, 10),
        (101.8, 102, 101.6, 101.7, 10),
        (101.7, 104.5, 103.0, 104.2, 10),
        (104.2, 105, 104, 104.1, 10),
        (104.1, 106, 103, 106, 30),
        (106, 106, 104, 105, 10),
    ]
    m5 = frame(rows); base = [{"side": "BULL", "decision_idx": 19, "invalidated": None}]
    s2 = engulfing_setups(base, m5, rr=2.0, displacement_atr=0.5)[0]
    s3 = engulfing_setups(base, m5, rr=3.0, displacement_atr=0.5)[0]
    assert s2["entry"] == s3["entry"] and s2["sl"] == s3["sl"]
    assert round((s2["tp"] - s2["entry"]) / (s2["entry"] - s2["sl"]), 6) == 2


def test_oi_spike_uses_only_closed_current_and_prior_bars():
    times = pd.date_range("2026-01-01", periods=22, freq="5min")
    oi = pd.DataFrame({"oi_time": times, "oi": [100 + i for i in range(21)] + [130]})
    setups = [{"decision_idx": 21, "decision_time": times[21]}]
    assert len(oi_spike_setups(setups, oi, ratio=1.5, lookback=20)) == 1
    oi.loc[21, "oi_time"] = times[21] + pd.Timedelta(minutes=5)
    assert oi_spike_setups(setups, oi, ratio=1.5, lookback=20) == []


def test_oi_spike_rejects_ordinary_positive_delta():
    times = pd.date_range("2026-01-01", periods=22, freq="5min")
    oi = pd.DataFrame({"oi_time": times, "oi": [100 + i for i in range(22)]})
    setups = [{"decision_idx": 21, "decision_time": times[21]}]
    assert oi_spike_setups(setups, oi, ratio=1.5, lookback=20) == []


def test_cost_is_price_cost_normalized_by_risk():
    assert round(net_r("BULL", 100, 99, 103, maker_fee=.0002, taker_fee=.0004, slip=.0003), 2) == 2.91
    assert round(net_r("BULL", 100, 99, 99, maker_fee=.0002, taker_fee=.0004, slip=.0003), 2) == -1.09


def test_time_split_uses_timestamp_not_trade_count():
    trades = [{"time": "2026-01-01", "netR": 1}, {"time": "2026-01-02", "netR": 1},
              {"time": "2026-01-09", "netR": 1}, {"time": "2026-01-10", "netR": 1}]
    ins, oos, cutoff = time_split(trades, 0.6)
    assert len(ins) == 2 and len(oos) == 2 and str(cutoff).startswith("2026-01-06")


if __name__ == "__main__":
    test_decision_occurs_after_m5_fvg_closes_and_sl_uses_ob()
    test_rejects_ob_without_displacement_fvg()
    test_h1_fvg_available_only_after_h1_close()
    test_rejects_m5_setup_after_h1_fvg_invalidation()
    test_full_wick_engulfing_volume_confirmation()
    test_rejects_body_only_engulfing_or_weak_volume()
    test_engulfing_rr_changes_only_tp()
    test_oi_spike_uses_only_closed_current_and_prior_bars()
    test_oi_spike_rejects_ordinary_positive_delta()
    test_cost_is_price_cost_normalized_by_risk()
    test_time_split_uses_timestamp_not_trade_count()
    print("h1_fvg_m5_ob_replay: PASS")
