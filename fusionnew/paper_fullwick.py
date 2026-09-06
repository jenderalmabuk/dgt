"""Isolated mainnet-priced paper daemon for H1 FVG -> M5 full-wick+volume."""
from __future__ import annotations
import argparse, json, os, time
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import requests
from backtest.data import fetch_recent, fetch_oi
from backtest.h1_fvg_m5_ob_replay import causal_setups, engulfing_setups, net_r

UTC = timezone.utc
TG_TOKEN = os.getenv("PAPER_TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.getenv("PAPER_TELEGRAM_CHAT_ID", "")
ARM = os.getenv("PAPER_ARM", "fullwick")


def tg(text: str) -> None:
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                          json={"chat_id": TG_CHAT, "text": f"[{ARM}] {text}"}, timeout=15)
        r.raise_for_status()
    except Exception as exc:
        print(f"telegram_error={type(exc).__name__}:{exc}", flush=True)


def load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {"pending": {}, "open": {}, "closed": [], "seen": []}


def save(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp"); tmp.write_text(json.dumps(state, indent=2)); os.replace(tmp, path)


def event(journal: Path, kind: str, **data) -> None:
    journal.parent.mkdir(parents=True, exist_ok=True)
    with journal.open("a") as f:
        f.write(json.dumps({"ts": datetime.now(UTC).isoformat(), "event": kind, **data}) + "\n")
    if kind in ("SETUP", "FILLED", "EXPIRED", "CLOSED"):
        text = f"{kind} {data.get('side', '')} {data.get('symbol', '')} entry={data.get('entry')} sl={data.get('sl')} tp={data.get('tp')}"
        if kind == "CLOSED": text += f" reason={data.get('reason')} netR={data.get('netR', 0):.3f}"
        tg(text)


def closed(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    cutoff = pd.Timestamp.now(tz="UTC").tz_localize(None) - pd.Timedelta(minutes=minutes)
    return df[df.open_time <= cutoff].reset_index(drop=True)


def latest_setup(symbol: str, tp_mode: str = 'fixed'):
    h1 = closed(fetch_recent(symbol, "1h", 300), 60)
    m5 = closed(fetch_recent(symbol, "5m", 1000), 5)
    setups = engulfing_setups(causal_setups(h1, m5, rr=3), m5, rr=3, tp_mode=tp_mode)
    return (setups[-1] if setups else None), m5


def oi_meta(symbol: str, decision_time: str) -> dict:
    try:
        d = fetch_oi(symbol, "5m", 22).sort_values("oi_time"); d["delta"] = d.oi.diff()
        d = d[d.oi_time <= pd.Timestamp(decision_time)]
        if len(d) < 21: return {"oi_available": False}
        delta = float(d.delta.iloc[-1]); pos = d.delta.iloc[-21:-1]; pos = pos[pos > 0]
        ratio = delta / float(pos.median()) if len(pos) and float(pos.median()) else None
        return {"oi_available": True, "oi_delta": delta, "oi_spike_ratio": ratio,
                "oi_spike": bool(delta > 0 and ratio is not None and ratio >= 1.5)}
    except Exception as exc:
        return {"oi_available": False, "oi_error": f"{type(exc).__name__}:{exc}"}


def scan_symbol(symbol: str, state: dict, journal: Path, oi_gate: bool = False, tp_mode: str = 'fixed') -> None:
    setup, m5 = latest_setup(symbol, tp_mode)
    if setup:
        key = f"{symbol}:{setup['side']}:{setup['decision_time']}"
        if key not in state["seen"] and symbol not in state["pending"] and symbol not in state["open"]:
            # Check if already expired before sending SETUP
            expires = setup["decision_time"] + pd.Timedelta(minutes=60)
            if pd.Timestamp.now(tz="UTC").tz_localize(None) >= expires:
                state["seen"].append(key)  # Mark as seen but skip (already expired)
                return
            
            oi = oi_meta(symbol, str(setup["decision_time"]))
            state["seen"].append(key)
            if oi_gate and not oi.get("oi_spike", False):
                event(journal, "OI_REJECT", symbol=symbol, side=setup["side"],
                      decision_time=str(setup["decision_time"]), **oi)
            else:
                row = {"symbol": symbol, "side": setup["side"], "decision_time": str(setup["decision_time"]),
                       "entry": setup["entry"], "sl": setup["sl"], "tp": setup["tp"],
                       "expires": str(expires),
                       "volume_ratio": setup["volume_ratio"], **oi}
                state["pending"][symbol] = row; event(journal, "SETUP", **row)
    pending = state["pending"].get(symbol)
    if pending:
        decision = pd.Timestamp(pending["decision_time"]); after = m5[m5.open_time >= decision]
        if pd.Timestamp.now(tz="UTC").tz_localize(None) >= pd.Timestamp(pending["expires"]):
            state["pending"].pop(symbol); event(journal, "EXPIRED", **pending)
        elif not after.empty and ((after.low <= pending["entry"]) & (after.high >= pending["entry"])).any():
            pending["filled_at"] = str(after.loc[(after.low <= pending["entry"]) & (after.high >= pending["entry"]), "open_time"].iloc[0])
            state["open"][symbol] = state["pending"].pop(symbol); event(journal, "FILLED", **pending)
    pos = state["open"].get(symbol)
    if pos:
        after = m5[m5.open_time >= pd.Timestamp(pos["filled_at"])]
        for _, bar in after.iterrows():
            sl = bar.low <= pos["sl"] if pos["side"] == "BULL" else bar.high >= pos["sl"]
            tp = bar.high >= pos["tp"] if pos["side"] == "BULL" else bar.low <= pos["tp"]
            if sl or tp:
                reason, price = ("SL", pos["sl"]) if sl else ("TP", pos["tp"])
                row = {**state["open"].pop(symbol), "closed_at": str(bar.open_time), "reason": reason,
                       "netR": net_r(pos["side"], pos["entry"], pos["sl"], price)}
                state["closed"].append(row); event(journal, "CLOSED", **row); break


def cycle(pairs: list[str], state: dict, journal: Path, oi_gate: bool = False, tp_mode: str = 'fixed') -> list[str]:
    errors = []
    for n, symbol in enumerate(pairs, 1):
        try: scan_symbol(symbol, state, journal, oi_gate, tp_mode)
        except Exception as exc: errors.append(f"{symbol}:{type(exc).__name__}:{exc}")
        if n % 25 == 0: print(f"scan {n}/{len(pairs)} pending={len(state['pending'])} open={len(state['open'])} closed={len(state['closed'])}", flush=True)
    state["seen"] = state["seen"][-10000:]
    return errors


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--pairs-file", required=True); ap.add_argument("--state-dir", required=True)
    ap.add_argument("--cycle-sec", type=int, default=300); ap.add_argument("--oi-gate", action="store_true")
    ap.add_argument("--tp-mode", choices=("fixed", "dynamic"), default="fixed")
    ap.add_argument("--once", action="store_true"); a = ap.parse_args()
    root = Path(a.state_dir); path = root / "state.json"; journal = root / "journal.jsonl"
    pairs = Path(a.pairs_file).read_text().split(); state = load(path); oi_mode = "HARD_GATE" if a.oi_gate else "ADVISORY"
    print(f"FULLWICK PAPER arm={ARM} pairs={len(pairs)} rr=3 tp={a.tp_mode} oi={oi_mode} execution=SIMULATED", flush=True)
    tg(f"STARTED pairs={len(pairs)} rr=3 tp={a.tp_mode} oi={oi_mode} execution=SIMULATED")
    while True:
        errors = cycle(pairs, state, journal, a.oi_gate, a.tp_mode); state["last_cycle"] = datetime.now(UTC).isoformat(); state["errors"] = errors
        save(path, state); summary = f"cycle done errors={len(errors)} pending={len(state['pending'])} open={len(state['open'])} closed={len(state['closed'])}"
        print(summary, flush=True)
        if a.once: break
        time.sleep(a.cycle_sec)


if __name__ == "__main__": main()
