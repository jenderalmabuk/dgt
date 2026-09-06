import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fusionnew"))
from clean_core.engine import btc_stability_gate, setup_volume_ratio


def frame(closes, volumes, start="2026-01-01"):
    idx = pd.date_range(start, periods=len(closes), freq="30min", tz="UTC")
    return pd.DataFrame({"close": closes, "volume": volumes}, index=idx)


def test_setup_volume_ratio_uses_setup_candle_and_prior_twenty_only():
    df = frame([100] * 22, [10] * 20 + [25, 999])
    setup_ts = df.index[-2]
    assert setup_volume_ratio(df, setup_ts) == 2.5


def test_setup_volume_ratio_fails_closed_without_twenty_prior_bars():
    df = frame([100] * 20, [10] * 19 + [25])
    assert setup_volume_ratio(df, df.index[-1]) is None


def test_btc_stability_is_four_candle_return_at_setup_time():
    df = frame([100, 99, 98, 97, 101, 1], [1] * 6)
    setup_ts = df.index[-2]
    assert btc_stability_gate(df, setup_ts, 4) is True


def test_btc_stability_fails_closed_without_history():
    df = frame([100, 101, 102, 103], [1] * 4)
    assert btc_stability_gate(df, df.index[-1], 4) is False


if __name__ == "__main__":
    test_setup_volume_ratio_uses_setup_candle_and_prior_twenty_only()
    test_setup_volume_ratio_fails_closed_without_twenty_prior_bars()
    test_btc_stability_is_four_candle_return_at_setup_time()
    test_btc_stability_fails_closed_without_history()
    print("imbalance_experiment_gates: PASS")
