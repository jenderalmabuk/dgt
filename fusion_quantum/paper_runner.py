"""Isolated Fusion Quantum H4+15m paper runner; gateway validation only."""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from bots.nexus_data import fetch_recent
from fusion_quantum.backtest_quantum import (
    CONFIRM_WINDOW, RR, _atr, _imbalances, _valid_obs, mss_confirm,
)
from fusion_quantum.candle_verification import verify_setup_candles
from fusion_quantum.paper_engine import submit_paper
from gateway.client import GatewayClient
from fusion_quantum.warm_runtime import Dispatcher, observe, preload

FIB_EXPIRY = int(os.getenv("FQ_FIB_EXPIRY", "6"))
MAX_RUNNING = int(os.getenv("FQ_MAX_RUNNING_POSITIONS", "20"))

# Pending limit orders intentionally have no cap. Running positions alone capped by gateway.
MAX_PENDING = None
RUNTIME = Path(os.getenv("FQ_RUNTIME_DIR", "runtime/fusion_quantum"))
STATE_PATH = Path(os.getenv("FQ_STATE_PATH", str(RUNTIME / "state.json")))
AUDIT_PATH = Path(os.getenv("FQ_PAPER_AUDIT", str(RUNTIME / "paper_audit.jsonl")))
HEARTBEAT_PATH = Path(os.getenv("FQ_HEARTBEAT_PATH", str(RUNTIME / "heartbeat.json")))
POLL_SEC = max(30, int(os.getenv("FQ_POLL_SEC", "300")))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(value, separators=(",", ":"), default=str) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def write_heartbeat(path: Path, value: dict[str, Any]) -> None:
    atomic_json(path, {"updated_at": now_iso(), **value})


class State:
    def __init__(self, path: Path = STATE_PATH):
        self.path = path
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            self.data = loaded if isinstance(loaded, dict) else {}
        except FileNotFoundError:
            self.data = {}
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"invalid state file {path}: {exc}") from exc
        self.data.setdefault("setups", {})

    def save(self) -> None:
        atomic_json(self.path, self.data)

    def claim(self, setup_id: str) -> bool:
        row = self.data["setups"].get(setup_id)
        if row and row.get("status") not in {"rejected", "error"}:
            return False
        self.data["setups"][setup_id] = {"status": "confirmed", "updated_at": now_iso()}
        self.save()
        return True

    def set_status(self, setup_id: str, status: str, **extra: Any) -> None:
        row = self.data["setups"].setdefault(setup_id, {})
        row.update(status=status, updated_at=now_iso(), **extra)
        self.save()

    def pending_count(self) -> int:
        return sum(1 for row in self.data["setups"].values() if row.get("status") == "pending")

    def can_add_pending(self) -> bool:
        return self.pending_count() < MAX_RUNNING

    def pending_for(self, symbol: str) -> list[tuple[str, dict[str, Any]]]:
        return [(sid, row["setup"]) for sid, row in self.data["setups"].items() if row.get("status") == "pending" and row.get("setup", {}).get("symbol") == symbol]


def reconcile_pending(state: State, running_symbols: set[str], *, symbol: str | None = None, keep_id: str | None = None) -> int:
    """Terminalize limits that cannot remain executable; never alter trade levels."""
    changed = 0
    for sid, row in state.data["setups"].items():
        setup = row.get("setup", {})
        setup_symbol = setup.get("symbol")
        if row.get("status") != "pending" or (symbol and setup_symbol != symbol):
            continue
        status = "blocked_running" if setup_symbol in running_symbols else "superseded"
        if status == "superseded" and (keep_id is None or sid == keep_id):
            continue
        state.set_status(sid, status, setup=setup, lifecycle_reason=status)
        append_jsonl(AUDIT_PATH, {"at": now_iso(), "event": status, "setup_id": sid, "setup": setup})
        changed += 1
    return changed


def setup_id(setup: dict[str, Any]) -> str:
    raw = "|".join(str(setup[k]) for k in ("symbol", "side", "confirmed_at", "entry_price"))
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def canonical_zone_key(key: list[Any] | tuple[Any, ...] | None) -> list[Any] | None:
    if not key or len(key) != 4:
        return None
    stamp = pd.Timestamp(key[1])
    if pd.isna(stamp):
        return None
    stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
    return [str(key[0]), stamp.isoformat(), float(key[2]), float(key[3])]


def lifecycle_key(setup: dict[str, Any]) -> str | None:
    """Stable one-zone identity. Backtest rule: one zone, one retest."""
    key = canonical_zone_key(setup.get("zone_key"))
    return "|".join(str(x) for x in key) if key else None


# Terminal statuses consume the zone permanently. awaiting is soft and may later confirm.
_ZONE_TERMINAL = {
    "failed_confirmation", "paper_opened", "expired", "rejected",
    "blocked_running", "superseded", "pending",
}


def lifecycle_claimed(state: State, setup: dict[str, Any]) -> bool:
    key = lifecycle_key(setup)
    if not key:
        return False
    for row in state.data["setups"].values():
        old = row.get("setup") or {}
        if lifecycle_key(old) == key and row.get("status") in _ZONE_TERMINAL:
            return True
    return False


def zone_key(zone: dict[str, Any], side: str) -> list[Any]:
    t = zone["t"]
    try:
        stamp = pd.Timestamp(t)
        if pd.isna(stamp):
            raise ValueError("NaT")
        stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
        t = stamp.isoformat()
    except (TypeError, ValueError):
        t = str(t)
    return [side, str(t), float(zone["zlow"]), float(zone["zhigh"])]


@dataclass
class LifecycleScan:
    setups: list[dict[str, Any]]
    consumed: list[dict[str, Any]]


def lifecycle_scan(symbol: str, htf: pd.DataFrame, ltf: pd.DataFrame) -> LifecycleScan:
    """Consume each structural zone on its first retest, matching backtest."""
    if len(ltf) < 10:
        return LifecycleScan([], [])
    atr = _atr(ltf); lows, highs = ltf.low.to_numpy(), ltf.high.to_numpy()
    found, consumed, touched = [], [], set()
    for side in ("BULL", "BEAR"):
        obs = _valid_obs(htf, side)
        for im in _imbalances(ltf, side):
            ce = int(im["ce"])
            prior = [ob for ob in obs if ob["t"] < im["t"] and im["leg_low"] <= ob["zhigh"] and im["leg_high"] >= ob["zlow"]]
            if not prior: continue
            zone = prior[-1]; zkey = zone_key(zone, side); key = "|".join(str(x) for x in zkey)
            entry0 = im["leg_low"] + .618*(im["leg_high"]-im["leg_low"]) if side == "BULL" else im["leg_high"] - .618*(im["leg_high"]-im["leg_low"])
            start = next((i for i in range(ce+1, min(ce+CONFIRM_WINDOW+1, len(ltf))) if (lows[i] <= entry0 if side == "BULL" else highs[i] >= entry0)), None)
            if start is None or key in touched: continue
            touched.add(key)
            conf_end = min(start + CONFIRM_WINDOW, len(ltf))
            window_complete = conf_end >= start + CONFIRM_WINDOW
            event = {
                "symbol": symbol,
                "side": "LONG" if side == "BULL" else "SHORT",
                "zone_key": zkey,
                "first_touch_at": str(ltf.open_time.iloc[start]),
                "outcome": "awaiting_confirmation",
            }
            conf = mss_confirm(ltf, zone, side, start, conf_end)
            if not conf:
                if window_complete:
                    event["outcome"] = "failed_confirmation"
                consumed.append(event)
                continue
            event["outcome"] = "confirmed"
            consumed.append(event)
            if conf["i"]+FIB_EXPIRY < len(ltf)-1 or not np.isfinite(atr[conf["i"]]): continue
            lo,hi = (conf["sweep"],conf["disp"]) if side=="BULL" else (conf["disp"],conf["sweep"])
            entry = lo+.559*(hi-lo) if side=="BULL" else hi-.559*(hi-lo)
            risk = entry-(lo-.5*atr[conf["i"]]) if side=="BULL" else (hi+.5*atr[conf["i"]])-entry
            if risk <= 0: continue
            candle = ltf.iloc[conf["i"]]
            confirmed_time = pd.Timestamp(ltf.open_time.iloc[conf["i"]])
            zone_time = pd.Timestamp(zone["t"])
            if confirmed_time.tzinfo is not None and zone_time.tzinfo is None:
                zone_time = zone_time.tz_localize(confirmed_time.tzinfo)
            elif confirmed_time.tzinfo is None and zone_time.tzinfo is not None:
                confirmed_time = confirmed_time.tz_localize(zone_time.tzinfo)
            evidence = {
                "h4_zone": {"origin_at": zkey[1], "low": zkey[2], "high": zkey[3]},
                "h4_zone_age_hours": float((confirmed_time - zone_time).total_seconds() / 3600),
                "m15_imbalance": {"origin_at": str(im["t"]), "leg_low": float(im["leg_low"]), "leg_high": float(im["leg_high"]), "entry0": float(entry0)},
                "first_touch_at": str(ltf.open_time.iloc[start]),
                "pivot_high": float(conf["pivot_high"]) if conf.get("pivot_high") is not None else None,
                "pivot_low": float(conf["pivot_low"]) if conf.get("pivot_low") is not None else None,
                "sweep_price": float(conf["sweep"]), "displacement_price": float(conf["disp"]),
                "atr": float(atr[conf["i"]]), "leg_low": float(lo), "leg_high": float(hi),
                "mss_candle": {k: str(candle[k]) if k == "open_time" else float(candle[k]) for k in ("open_time", "open", "high", "low", "close", "volume")},
                "formula": {"entry_ratio": 0.559, "atr_sl_buffer": 0.5, "rr": float(RR)},
            }
            found.append({"symbol":symbol,"side":event["side"],"zone_key":zkey,"entry_price":float(entry),"sl_price":float(entry-risk if side=="BULL" else entry+risk),"tp_price":float(entry+RR*risk if side=="BULL" else entry-RR*risk),"confirmed_at":str(ltf.open_time.iloc[conf["i"]]),"expires_at":str(pd.Timestamp(ltf.open_time.iloc[conf["i"]])+pd.Timedelta(minutes=15*FIB_EXPIRY)),"evidence":evidence})
    return LifecycleScan(found, consumed)


def confirmed_setups(symbol: str, htf: pd.DataFrame, ltf: pd.DataFrame) -> list[dict[str, Any]]:
    """Reproduce backtest confirmation, entry, SL and TP formulas."""
    return lifecycle_scan(symbol, htf, ltf).setups


def _legacy_confirmed_setups(symbol: str, htf: pd.DataFrame, ltf: pd.DataFrame) -> list[dict[str, Any]]:
    if len(ltf) < 10:
        return []
    atr = _atr(ltf)
    lows, highs = ltf.low.to_numpy(), ltf.high.to_numpy()
    found: list[dict[str, Any]] = []
    for side in ("BULL", "BEAR"):
        obs = _valid_obs(htf, side)
        for im in _imbalances(ltf, side):
            ce = int(im["ce"])
            prior = [ob for ob in obs if ob["t"] < im["t"] and im["leg_low"] <= ob["zhigh"] and im["leg_high"] >= ob["zlow"]]
            if not prior:
                continue
            entry0 = (im["leg_low"] + .618 * (im["leg_high"] - im["leg_low"])) if side == "BULL" else (im["leg_high"] - .618 * (im["leg_high"] - im["leg_low"]))
            start = next((i for i in range(ce + 1, min(ce + CONFIRM_WINDOW + 1, len(ltf))) if (lows[i] <= entry0 if side == "BULL" else highs[i] >= entry0)), None)
            if start is None:
                continue
            conf = mss_confirm(ltf, prior[-1], side, start, min(start + CONFIRM_WINDOW, len(ltf)))
            # Only emit limit setups still inside six-bar fill window. Initial
            # startup must not replay months of historical confirmations.
            if not conf or conf["i"] + FIB_EXPIRY < len(ltf) - 1 or not np.isfinite(atr[conf["i"]]):
                continue
            lo, hi = ((conf["sweep"], conf["disp"]) if side == "BULL" else (conf["disp"], conf["sweep"]))
            entry = (lo + .559 * (hi - lo)) if side == "BULL" else (hi - .559 * (hi - lo))
            risk = (entry - (lo - .5 * atr[conf["i"]])) if side == "BULL" else ((hi + .5 * atr[conf["i"]]) - entry)
            if risk <= 0:
                continue
            found.append({
                "symbol": symbol, "side": "LONG" if side == "BULL" else "SHORT",
                "zone_key": zone_key(prior[-1], side),
                "entry_price": float(entry), "sl_price": float(entry - risk if side == "BULL" else entry + risk),
                "tp_price": float(entry + RR * risk if side == "BULL" else entry - RR * risk),
                "confirmed_at": str(ltf.open_time.iloc[conf["i"]]),
                "expires_at": str(pd.Timestamp(ltf.open_time.iloc[conf["i"]]) + pd.Timedelta(minutes=15 * FIB_EXPIRY)),
            })
    return found


def gateway_accepted(result: dict[str, Any]) -> bool:
    return bool(result.get("ok") and result.get("would_deploy") is True and result.get("status") == "paper_opened")


def pending_action(setup: dict[str, Any], bars: pd.DataFrame, pending_since: pd.Timestamp, now: pd.Timestamp) -> str:
    expires_at = pd.Timestamp(setup["expires_at"])
    expires_at = expires_at.tz_localize("UTC") if expires_at.tzinfo is None else expires_at.tz_convert("UTC")
    pending_since = pending_since.tz_localize("UTC") if pending_since.tzinfo is None else pending_since.tz_convert("UTC")
    now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
    if now > expires_at:
        return "expired"
    eligible = bars[pd.to_datetime(bars["open_time"], utc=True) >= pending_since]
    if eligible.empty:
        return "wait"
    entry = float(setup["entry_price"])
    if setup["side"] == "LONG":
        return "fill" if float(eligible["low"].min()) <= entry else "wait"
    return "fill" if float(eligible["high"].max()) >= entry else "wait"


def notify(text: str) -> None:
    token, chat = os.getenv("FQ_TELEGRAM_BOT_TOKEN"), os.getenv("FQ_TELEGRAM_CHAT_ID")
    if not token or not chat:
        return
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=10):
        pass


def setup_message(setup: dict[str, Any]) -> str:
    side = "BUY" if setup["side"] == "LONG" else "SELL"
    symbol = setup["symbol"]

    rr = abs((setup["tp_price"] - setup["entry_price"]) / (setup["entry_price"] - setup["sl_price"]))
    evidence = setup.get("evidence") or {}
    basis = ""
    if evidence:
        zone = evidence["h4_zone"]
        direction = "BULL" if setup["side"] == "LONG" else "BEAR"
        candle = evidence["mss_candle"]
        basis = (
            f"Basis: H4 {direction} zone {zone['low']:.12g}-{zone['high']:.12g} (age {evidence['h4_zone_age_hours']:.1f}h)\n"
            f"M15 sweep {evidence['sweep_price']:.12g} | pivots H{evidence['pivot_high']:.12g}/L{evidence['pivot_low']:.12g} | "
            f"MSS close {candle['close']:.12g} | ATR {evidence['atr']:.12g}\n"
        )
    tv = f"https://www.tradingview.com/chart/?symbol={symbol}.P"
    return (
        f"🆕 SETUP H4 {side} {symbol} [fusion_quantum_h4_15m]\n"
        f"Entry {setup['entry_price']:.12g} | SL {setup['sl_price']:.12g} | TP {setup['tp_price']:.12g} | RR {rr:.1f}\n"
        f"{basis}[PAPER] LIMIT placed\n"
        f"☁️ TradingView: {tv}"
    )


def fill_message(setup: dict[str, Any], result: dict[str, Any]) -> str:
    side = "BUY" if setup["side"] == "LONG" else "SELL"
    tr = ((result.get("execution") or {}).get("trader_response") or {})
    fill = tr.get("entry_price", setup["entry_price"])
    return (
        f"✅ FILLED {setup['symbol']} {side} @~{float(fill):.12g} "
        f"[fusion_quantum_h4_15m] → software SL/TP "
        f"(SL {setup['sl_price']:.12g} / TP {setup['tp_price']:.12g})"
    )


def symbols() -> list[str]:
    raw = os.getenv("FQ_SYMBOLS", "")
    if raw:
        return [x.strip().upper() for x in raw.split(",") if x.strip()]
    path = Path(os.getenv("FQ_UNIVERSE_FILE", "runtime/revo/canonical_universe.txt"))
    return [x.strip().upper() for x in path.read_text().splitlines() if x.strip()]


def setup_fresh_at(setup: dict[str, Any], cutoff: pd.Timestamp) -> bool:
    """Accept delayed confirmations only while original limit window remains open."""
    confirmed = pd.Timestamp(setup["confirmed_at"])
    expires = pd.Timestamp(setup["expires_at"])
    confirmed = confirmed.tz_localize("UTC") if confirmed.tzinfo is None else confirmed.tz_convert("UTC")
    expires = expires.tz_localize("UTC") if expires.tzinfo is None else expires.tz_convert("UTC")
    cutoff = cutoff.tz_localize("UTC") if cutoff.tzinfo is None else cutoff.tz_convert("UTC")
    return confirmed <= cutoff <= expires


class WarmPaperScanner:
    """Update closed-candle cache, scan snapshots concurrently, serialize mutations."""

    def __init__(self, state: State, cache: Any, mutation_lock: asyncio.Lock,
                 mutate: Any, scanner: Any = lifecycle_scan):
        self.state = state
        self.cache = cache
        self.mutation_lock = mutation_lock
        self.mutate = mutate
        self.scanner = scanner

    async def handle(self, event: dict[str, Any]) -> None:
        started = time.time()
        symbol = event["symbol"]
        timeframe = event.get("timeframe", "15m")
        try:
            candle = event.get("candle")
            if candle is None:
                raise ValueError("closed event missing candle")
            self.cache.upsert(symbol, timeframe, candle)
            if timeframe == "4h":
                append_jsonl(AUDIT_PATH, {"at": now_iso(), "event": "warm_h4_update", "symbol": symbol, "candle_start": event["start"]})
                return
            cutoff = pd.to_datetime(event["start"], unit="ms", utc=True)
            htf = self.cache.snapshot(symbol, "4h", cutoff)
            ltf = self.cache.snapshot(symbol, "15m", cutoff)
            scan = self.scanner(symbol, htf, ltf)
            if asyncio.iscoroutine(scan):
                scan = await scan
            if isinstance(scan, LifecycleScan):
                fresh_setups = [x for x in scan.setups if setup_fresh_at(x, cutoff)]
                fresh_consumed = []
                for x in scan.consumed:
                    touch = pd.Timestamp(x["first_touch_at"])
                    touch = touch.tz_localize("UTC") if touch.tzinfo is None else touch.tz_convert("UTC")
                    terminal_at = touch + pd.Timedelta(minutes=15 * CONFIRM_WINDOW)
                    if x.get("outcome") == "failed_confirmation" and abs((terminal_at - cutoff).total_seconds()) < 1:
                        fresh_consumed.append(x)
                    elif x.get("outcome") == "awaiting_confirmation" and touch <= cutoff < terminal_at:
                        fresh_consumed.append(x)
                scan = LifecycleScan(fresh_setups, fresh_consumed)
            async with self.mutation_lock:
                result = self.mutate(self.state, symbol, ltf, scan)
                if asyncio.iscoroutine(result):
                    await result
            append_jsonl(AUDIT_PATH, {"at": now_iso(), "event": "warm_scan", "symbol": symbol, "candle_start": event["start"], "latency_sec": round(time.time() - event["received_at"], 3), "setups": len(scan.setups)})
        except Exception as exc:
            append_jsonl(AUDIT_PATH, {"at": now_iso(), "event": "warm_scan_error", "symbol": symbol, "timeframe": timeframe, "latency_sec": round(time.time() - started, 3), "error": repr(exc)})


async def apply_warm_scan(state: State, symbol: str, ltf: pd.DataFrame,
                          scan: LifecycleScan) -> None:
    """Apply only lifecycle/setup mutations; polling remains pending-fill manager."""
    portfolio = await GatewayClient().portfolio()
    running_symbols = {str(x).upper() for x in portfolio.get("open_symbols", [])}
    for event in scan.consumed:
        outcome = event.get("outcome")
        if outcome not in {"failed_confirmation", "awaiting_confirmation"}:
            continue
        key = lifecycle_key(event)
        if not key:
            continue
        zid = "zone_" + hashlib.sha256(key.encode()).hexdigest()[:19]
        row = state.data["setups"].get(zid)
        if row and row.get("status") in _ZONE_TERMINAL:
            continue
        if outcome == "awaiting_confirmation":
            state.set_status(zid, outcome, setup=event, lifecycle_reason="first_retest_awaiting_mss")
            append_jsonl(AUDIT_PATH, {"at": now_iso(), "event": outcome, "setup_id": zid, "setup": event})
        elif state.claim(zid) or (row and row.get("status") == "awaiting_confirmation"):
            state.set_status(zid, outcome, setup=event, lifecycle_reason="first_retest_failed_mss")
            append_jsonl(AUDIT_PATH, {"at": now_iso(), "event": outcome, "setup_id": zid, "setup": event})
    for setup in scan.setups:
        sid = setup_id(setup)
        if lifecycle_claimed(state, setup) or not state.claim(sid):
            continue

        # Verify MSS candle OHLC parity vs Bybit REST before emitting setup
        verification = await asyncio.to_thread(verify_setup_candles, setup)
        if not verification["ok"]:
            state.set_status(sid, "rejected", setup=setup, lifecycle_reason="candle_verification_failed")
            append_jsonl(AUDIT_PATH, {
                "at": now_iso(),
                "event": "candle_verification_failed",
                "setup_id": sid,
                "setup": setup,
                "verification": verification
            })
            continue

        if symbol in running_symbols:
            state.set_status(sid, "blocked_running", setup=setup, lifecycle_reason="blocked_running")
            append_jsonl(AUDIT_PATH, {"at": now_iso(), "event": "blocked_running", "setup_id": sid, "setup": setup})
            continue
        reconcile_pending(state, running_symbols, symbol=symbol, keep_id=sid)
        state.set_status(sid, "pending", setup=setup, pending_since=now_iso())
        append_jsonl(AUDIT_PATH, {"at": now_iso(), "event": "pending", "setup_id": sid, "setup": setup})
        try:
            await asyncio.to_thread(notify, setup_message(setup))
            state.set_status(sid, "pending", setup=setup, setup_notification_sent=True)
        except Exception as exc:
            append_jsonl(AUDIT_PATH, {"at": now_iso(), "event": "telegram_error", "error": repr(exc)})


async def reconcile_setups_once(state: State, mutation_lock: asyncio.Lock) -> dict[str, int]:
    """REST fallback: recover still-live confirmations missed by warm cache."""
    cutoff = pd.Timestamp.now(tz="UTC")
    semaphore = asyncio.Semaphore(int(os.getenv("FQ_RECONCILE_CONCURRENCY", "12")))
    counts = {"symbols": 0, "setups": 0, "errors": 0}

    async def scan_symbol(symbol: str) -> None:
        try:
            async with semaphore:
                htf, ltf = await asyncio.gather(
                    asyncio.to_thread(fetch_recent, symbol, "4h", 1000),
                    asyncio.to_thread(fetch_recent, symbol, "15m", 1000),
                )
            if htf.attrs.get("stale") or ltf.attrs.get("stale"):
                return
            scan = lifecycle_scan(symbol, htf, ltf)
            scan = LifecycleScan([x for x in scan.setups if setup_fresh_at(x, cutoff)], [])
            counts["symbols"] += 1
            counts["setups"] += len(scan.setups)
            if scan.setups:
                async with mutation_lock:
                    await apply_warm_scan(state, symbol, ltf, scan)
        except Exception as exc:
            counts["errors"] += 1
            append_jsonl(AUDIT_PATH, {"at": now_iso(), "event": "reconcile_error", "symbol": symbol, "error": repr(exc)})

    await asyncio.gather(*(scan_symbol(symbol) for symbol in symbols()))
    append_jsonl(AUDIT_PATH, {"at": now_iso(), "event": "reconcile_complete", "counts": counts})
    return counts


async def manage_pending_once(state: State) -> dict[str, int]:
    """Manage existing limits only; closed-candle WebSocket owns new setup detection."""
    counts = {"pending": state.pending_count(), "filled": 0, "submitted": 0, "expired": 0, "errors": 0}
    portfolio = await GatewayClient().portfolio()
    running_symbols = {str(x).upper() for x in portfolio.get("open_symbols", [])}
    reconcile_pending(state, running_symbols)
    pending_symbols = sorted({row.get("setup", {}).get("symbol") for row in state.data["setups"].values() if row.get("status") == "pending"} - {None})
    for symbol in pending_symbols:
        try:
            ltf = await asyncio.to_thread(fetch_recent, symbol, "15m", 1000)
            for sid, setup in state.pending_for(symbol):
                row = state.data["setups"][sid]
                pending_since = pd.Timestamp(row.get("pending_since", row["updated_at"]))
                pending_since = pending_since.tz_localize("UTC") if pending_since.tzinfo is None else pending_since.tz_convert("UTC")
                action = pending_action(setup, ltf, pending_since, pd.Timestamp.now(tz="UTC"))
                if action == "wait":
                    continue
                if action == "expired":
                    counts["expired"] += 1
                    state.set_status(sid, "expired", setup=setup)
                    append_jsonl(AUDIT_PATH, {"at": now_iso(), "event": "expired", "setup_id": sid, "setup": setup})
                    continue
                result = await submit_paper(setup)
                status = "paper_opened" if gateway_accepted(result) else "rejected"
                state.set_status(sid, status, setup=setup, result=result)
                append_jsonl(AUDIT_PATH, {"at": now_iso(), "event": status, "setup_id": sid, "setup": setup, "result": result})
                if status == "paper_opened":
                    counts["filled"] += 1
                    counts["submitted"] += 1
                    try:
                        await asyncio.to_thread(notify, fill_message(setup, result))
                    except Exception as exc:
                        append_jsonl(AUDIT_PATH, {"at": now_iso(), "event": "telegram_error", "error": repr(exc)})
        except Exception as exc:
            counts["errors"] += 1
            append_jsonl(AUDIT_PATH, {"at": now_iso(), "event": "pending_error", "symbol": symbol, "error": repr(exc)})
    counts["pending"] = state.pending_count()
    return counts


async def scan_once(state: State) -> dict[str, int]:
    counts = {"symbols": 0, "confirmed": 0, "pending": state.pending_count(), "filled": 0, "submitted": 0, "expired": 0, "blocked_running": 0, "superseded": 0, "errors": 0}
    cycle_started = time.monotonic()
    portfolio = await GatewayClient().portfolio()
    running_symbols = {str(x).upper() for x in portfolio.get("open_symbols", [])}
    counts["blocked_running"] += reconcile_pending(state, running_symbols)
    for symbol in symbols():
        try:
            htf, ltf = await asyncio.gather(
                asyncio.to_thread(fetch_recent, symbol, "4h", 1000),
                asyncio.to_thread(fetch_recent, symbol, "15m", 1000),
            )
            counts["symbols"] += 1
            if htf.attrs.get("stale") or ltf.attrs.get("stale"):
                append_jsonl(AUDIT_PATH, {"at": now_iso(), "event": "stale", "symbol": symbol})
                continue
            for sid, setup in state.pending_for(symbol):
                row = state.data["setups"][sid]
                pending_since = pd.Timestamp(row.get("pending_since", row["updated_at"]))
                pending_since = pending_since.tz_localize("UTC") if pending_since.tzinfo is None else pending_since.tz_convert("UTC")
                action = pending_action(setup, ltf, pending_since, pd.Timestamp.now(tz="UTC"))
                if action == "wait":
                    continue
                if action == "expired":
                    counts["expired"] += 1
                    state.set_status(sid, "expired", setup=setup)
                    append_jsonl(AUDIT_PATH, {"at": now_iso(), "event": "expired", "setup_id": sid, "setup": setup})
                    continue
                result = await submit_paper(setup)
                status = "paper_opened" if gateway_accepted(result) else "rejected"
                state.set_status(sid, status, setup=setup, result=result)
                append_jsonl(AUDIT_PATH, {"at": now_iso(), "event": status, "setup_id": sid, "setup": setup, "result": result})
                if status == "paper_opened":
                    counts["filled"] += 1
                    counts["submitted"] += 1
                    try:
                        await asyncio.to_thread(notify, fill_message(setup, result))
                    except Exception as exc:
                        append_jsonl(AUDIT_PATH, {"at": now_iso(), "event": "telegram_error", "error": repr(exc)})
            scan = lifecycle_scan(symbol, htf, ltf)
            for event in scan.consumed:
                outcome = event.get("outcome")
                if outcome not in {"failed_confirmation", "awaiting_confirmation"}:
                    continue
                key = lifecycle_key(event)
                if not key:
                    continue
                zid = "zone_" + hashlib.sha256(key.encode()).hexdigest()[:19]
                row = state.data["setups"].get(zid)
                if row and row.get("status") in _ZONE_TERMINAL:
                    continue
                if outcome == "awaiting_confirmation":
                    state.set_status(zid, "awaiting_confirmation", setup=event, lifecycle_reason="first_retest_awaiting_mss")
                    append_jsonl(AUDIT_PATH, {"at": now_iso(), "event": "awaiting_confirmation", "setup_id": zid, "setup": event})
                    continue
                if state.claim(zid) or (row and row.get("status") == "awaiting_confirmation"):
                    state.set_status(zid, "failed_confirmation", setup=event, lifecycle_reason="first_retest_failed_mss")
                    append_jsonl(AUDIT_PATH, {"at": now_iso(), "event": "failed_confirmation", "setup_id": zid, "setup": event})
            for setup in scan.setups:
                sid = setup_id(setup)
                if lifecycle_claimed(state, setup):
                    append_jsonl(AUDIT_PATH, {"at": now_iso(), "event": "blocked_zone_lifecycle", "setup_id": sid, "setup": setup})
                    continue
                # Drop soft awaiting row for same zone so confirmed path owns it.
                zkey = lifecycle_key(setup)
                if zkey:
                    zid = "zone_" + hashlib.sha256(zkey.encode()).hexdigest()[:19]
                    row = state.data["setups"].get(zid)
                    if row and row.get("status") == "awaiting_confirmation":
                        state.set_status(zid, "confirmed", setup=setup, lifecycle_reason="awaiting_to_confirmed")
                if not state.claim(sid):
                    continue
                counts["confirmed"] += 1
                if symbol in running_symbols:
                    state.set_status(sid, "blocked_running", setup=setup, lifecycle_reason="blocked_running")
                    counts["blocked_running"] += 1
                    append_jsonl(AUDIT_PATH, {"at": now_iso(), "event": "blocked_running", "setup_id": sid, "setup": setup})
                    continue
                counts["superseded"] += reconcile_pending(state, running_symbols, symbol=symbol, keep_id=sid)
                row = state.data["setups"].get(sid, {})
                already_notified = bool(row.get("setup_notification_sent"))
                state.set_status(sid, "pending", setup=setup, pending_since=now_iso())
                counts["pending"] += 1
                append_jsonl(AUDIT_PATH, {"at": now_iso(), "event": "pending", "setup_id": sid, "setup": setup})
                if not already_notified:
                    try:
                        await asyncio.to_thread(notify, setup_message(setup))
                        state.set_status(sid, "pending", setup=setup, setup_notification_sent=True)
                    except Exception as exc:
                        append_jsonl(AUDIT_PATH, {"at": now_iso(), "event": "telegram_error", "error": repr(exc)})
        except Exception as exc:
            counts["errors"] += 1
            append_jsonl(AUDIT_PATH, {"at": now_iso(), "event": "scan_error", "symbol": symbol, "error": repr(exc)})
    counts["pending"] = state.pending_count()
    append_jsonl(AUDIT_PATH, {"at": now_iso(), "event": "cycle_timing", "duration_sec": round(time.monotonic() - cycle_started, 3), "counts": counts})
    return counts


async def run() -> None:
    state = State()
    syms = symbols()
    cache = await preload(syms, int(os.getenv("FQ_CACHE_PRELOAD_CONCURRENCY", "24")))
    mutation_lock = asyncio.Lock()
    scanner = WarmPaperScanner(state, cache, mutation_lock, apply_warm_scan)
    dispatcher = Dispatcher(scanner.handle, int(os.getenv("FQ_WS_CONCURRENCY", "12")))
    print(f"[{now_iso()}] Fusion Quantum paper runner started poll_sec={POLL_SEC}", flush=True)

    async def poll() -> None:
        while True:
            started = time.monotonic()
            async with mutation_lock:
                counts = await manage_pending_once(state)
            status = "ok" if not counts["errors"] else "degraded"
            write_heartbeat(HEARTBEAT_PATH, {"status": status, **counts})
            print(
                f"[{now_iso()}] pending status={status} pending={counts['pending']} "
                f"filled={counts['filled']} expired={counts['expired']} submitted={counts['submitted']} errors={counts['errors']}",
                flush=True,
            )
            await asyncio.sleep(max(1, POLL_SEC - (time.monotonic() - started)))

    async def reconcile() -> None:
        while True:
            started = time.monotonic()
            counts = await reconcile_setups_once(state, mutation_lock)
            print(f"[{now_iso()}] reconcile symbols={counts['symbols']} setups={counts['setups']} errors={counts['errors']}", flush=True)
            await asyncio.sleep(max(1, 900 - (time.monotonic() - started)))

    await asyncio.gather(
        poll(),
        reconcile(),
        observe(syms, dispatcher.dispatch, int(os.getenv("BYBIT_WS_DURATION", "315360000"))),
    )


if __name__ == "__main__":
    asyncio.run(run())
