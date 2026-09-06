import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, "/app/fusionnew")
from backtest.faithful_imbalance import (
    _annotate_ob_invalidation,
    _imbalances,
    _trend,
    _trend_ok,
    _valid_obs,
    compute_fibonacci_entry,
)

SYMBOL = "FILUSDT"
END = pd.Timestamp("2026-07-30T00:33:00Z")


def fetch(interval: str, start: pd.Timestamp) -> pd.DataFrame:
    rows = []
    cursor = int(start.timestamp() * 1000)
    end = int(END.timestamp() * 1000)
    while cursor < end:
        r = requests.get("https://fapi.binance.com/fapi/v1/klines", params={
            "symbol": SYMBOL, "interval": interval, "startTime": cursor,
            "endTime": end, "limit": 1500,
        }, timeout=20)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        rows.extend(batch)
        cursor = batch[-1][0] + 1
    df = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume", "close_time", "q", "n", "tb", "tq", "x"])
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.tz_localize(None)
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = df[c].astype(float)
    return df.drop_duplicates("open_time").reset_index(drop=True)


zone = fetch("30m", END - pd.Timedelta(days=10))
ltf = fetch("3m", END - pd.Timedelta(days=4))
trend = _trend(zone)
obs = _valid_obs(zone, "BEAR")
_annotate_ob_invalidation(obs, zone, "BEAR")
imbs = _imbalances(ltf, "BEAR")
setup_time = np.datetime64("2026-07-29T19:06:00")
chosen_im = next(x for x in imbs if x["t"] == setup_time)

matches = []
for ob in obs:
    if ob["t"] >= chosen_im["t"]:
        continue
    if ob["t_inv"] is not None and ob["t_inv"] <= chosen_im["t"]:
        continue
    if chosen_im["leg_low"] <= ob["zhigh"] and chosen_im["leg_high"] >= ob["zlow"]:
        matches.append(ob)

candle = pd.Series({"open": chosen_im["leg_high"], "high": chosen_im["leg_high"], "low": chosen_im["leg_low"], "close": chosen_im["leg_low"]})
entry = compute_fibonacci_entry(candle, 0.618)
print(json.dumps({
    "zone_tf": "30m", "ltf": "3m", "setup_time": str(setup_time),
    "imbalance": chosen_im, "computed_entry": entry,
    "trend_ok": _trend_ok(trend, chosen_im["t"], "BEAR"),
    "matching_obs_chronological": matches,
    "legacy_would_pick": matches[-1] if matches else None,
}, default=str, indent=2))

print("\n3m candles around setup")
ce = chosen_im["ce"]
print(ltf.iloc[max(0, ce-8):ce+5][["open_time", "open", "high", "low", "close"]].to_string(index=False))
print("\n30m candles around matching OBs")
for ob in matches:
    i = ob["i"]
    print("OB", ob)
    print(zone.iloc[max(0, i-2):i+5][["open_time", "open", "high", "low", "close"]].to_string(index=False))
