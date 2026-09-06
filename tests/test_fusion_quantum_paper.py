import asyncio
import json

import numpy as np
import pandas as pd
import pytest

from fusion_quantum import paper_runner as runner
from fusion_quantum.paper_engine import intent_from_setup, submit_paper


def test_paper_intent_is_bounded_and_gateway_compatible():
    i = intent_from_setup({
        "symbol": "BTCUSDT", "side": "LONG", "entry_price": 100,
        "sl_price": 95, "tp_price": 110,
    })
    assert i.source == "TEST"
    assert i.risk_pct == 0.0025
    assert i.validate() is None
    assert i.tag == "fusion_quantum_h4_15m"


def test_confirmed_setup_matches_backtest_formula(monkeypatch):
    ltf = pd.DataFrame({
        "open_time": pd.date_range("2026-01-01", periods=15, freq="15min"),
        "open": np.full(15, 100.0), "high": np.full(15, 102.0),
        "low": np.full(15, 98.0), "close": np.full(15, 100.0),
        "volume": np.ones(15),
    })
    zone = {"t": pd.Timestamp("2025-12-31"), "zlow": 95.0, "zhigh": 105.0}
    imbalance = {"t": pd.Timestamp("2026-01-01"), "ce": 5, "leg_low": 90.0, "leg_high": 110.0}
    monkeypatch.setattr(runner, "_valid_obs", lambda *_: [zone])
    monkeypatch.setattr(runner, "_imbalances", lambda *_: [imbalance])
    monkeypatch.setattr(runner, "mss_confirm", lambda *_: {"i": 9, "sweep": 96.0, "disp": 106.0, "side": "BULL"})
    monkeypatch.setattr(runner, "_atr", lambda _: np.full(15, 4.0))
    ltf.loc[6, "low"] = 100.0

    setups = runner.confirmed_setups("BTCUSDT", pd.DataFrame(), ltf)

    assert len(setups) == 1
    assert setups[0]["entry_price"] == 101.59
    assert setups[0]["sl_price"] == 94.0
    assert setups[0]["tp_price"] == pytest.approx(116.77)
    assert setups[0]["expires_at"] == str(ltf.open_time.iloc[9] + pd.Timedelta(minutes=90))
    evidence = setups[0]["evidence"]
    assert evidence["h4_zone"] == {"origin_at": "2025-12-31T00:00:00+00:00", "low": 95.0, "high": 105.0}
    assert evidence["m15_imbalance"]["leg_low"] == 90.0
    assert evidence["first_touch_at"] == str(ltf.open_time.iloc[6])
    assert evidence["sweep_price"] == 96.0
    assert evidence["displacement_price"] == 106.0
    assert evidence["atr"] == 4.0
    assert evidence["mss_candle"]["open_time"] == str(ltf.open_time.iloc[9])
    assert evidence["formula"] == {"entry_ratio": 0.559, "atr_sl_buffer": 0.5, "rr": 2.0}


def test_pending_limit_waits_fills_and_expires():
    setup = {"side": "LONG", "entry_price": 100.0, "confirmed_at": "2026-01-01 00:00:00", "expires_at": "2026-01-01 06:00:00"}
    bars = pd.DataFrame({
        "open_time": pd.date_range("2026-01-01 01:00:00", periods=3, freq="15min", tz="UTC"),
        "open": [101.0, 101.0, 99.0], "high": [102.0, 102.0, 102.0],
        "low": [100.5, 100.5, 100.5], "close": [101.0, 101.0, 100.2],
    })
    pending_since = pd.Timestamp("2026-01-01 00:30:00", tz="UTC")
    now = pd.Timestamp("2026-01-01 01:45:00", tz="UTC")
    assert runner.pending_action(setup, bars, pending_since, now) == "wait"

    bars.loc[2, "low"] = 99.5
    assert runner.pending_action(setup, bars, pending_since, now) == "fill"

    now_expired = pd.Timestamp("2026-01-01 07:00:00", tz="UTC")
    assert runner.pending_action(setup, bars, pending_since, now_expired) == "expired"


def test_pending_cap_is_twenty(tmp_path):
    state = runner.State(tmp_path / "state.json")
    for i in range(20):
        state.set_status(str(i), "pending")
    assert state.pending_count() == 20
    assert not state.can_add_pending()


def test_state_persists_dedup_and_lifecycle(tmp_path):
    state = runner.State(tmp_path / "state.json")
    assert state.claim("setup-1")
    state.set_status("setup-1", "would_deploy")
    reloaded = runner.State(tmp_path / "state.json")
    assert not reloaded.claim("setup-1")
    assert reloaded.data["setups"]["setup-1"]["status"] == "would_deploy"


def test_gateway_result_only_accepts_opened_paper_position():
    assert runner.gateway_accepted({"ok": True, "status": "paper_opened", "would_deploy": True})
    assert not runner.gateway_accepted({"ok": True, "status": "would_deploy"})
    assert not runner.gateway_accepted({"ok": True, "reason": "DRY_RUN (no order placed)"})
    assert not runner.gateway_accepted({"ok": True, "status": "deployed"})


def test_submit_paper_requires_validation_then_opens(monkeypatch, tmp_path):
    class Client:
        def __init__(self): self.calls = []
        async def execute(self, intent, *, dry_run=False):
            self.calls.append(dry_run)
            return {"ok": True, "reason": "DRY_RUN (no order placed)"} if dry_run else {"ok": True, "reason": "opened"}
    client = Client()
    monkeypatch.setattr("fusion_quantum.paper_engine.AUDIT_PATH", tmp_path / "audit.jsonl")
    result = asyncio.run(submit_paper({"symbol": "BTCUSDT", "side": "LONG", "entry_price": 100, "sl_price": 95, "tp_price": 110}, client=client))
    assert client.calls == [True, False]
    assert result["status"] == "paper_opened"
    assert result["would_deploy"] is True


def test_jsonl_and_heartbeat_are_atomic(tmp_path):
    audit = tmp_path / "audit.jsonl"
    heartbeat = tmp_path / "heartbeat.json"
    runner.append_jsonl(audit, {"event": "scan", "count": 2})
    runner.write_heartbeat(heartbeat, {"status": "ok"})
    assert json.loads(audit.read_text()) == {"event": "scan", "count": 2}
    assert json.loads(heartbeat.read_text())["status"] == "ok"


def test_setup_and_fill_messages_are_separate():
    setup = {"symbol": "BTCUSDT", "side": "SHORT", "entry_price": 100.0, "sl_price": 102.0, "tp_price": 96.0}
    placed = runner.setup_message(setup)
    filled = runner.fill_message(setup, {"execution": {"trader_response": {"qty": 2.5, "entry_price": 99.5}}})
    assert "🆕 SETUP H4 SELL BTCUSDT [fusion_quantum_h4_15m]" in placed
    assert "Entry 100 | SL 102 | TP 96 | RR 2.0" in placed
    assert "[PAPER] LIMIT placed" in placed
    assert "Basis:" not in placed
    audited = dict(setup, evidence={
        "h4_zone": {"origin_at": "2025-12-31T00:00:00", "low": 95.0, "high": 105.0},
        "h4_zone_age_hours": 48.0, "first_touch_at": "2026-01-02 00:00:00",
        "pivot_high": 104.0, "pivot_low": 98.0, "sweep_price": 106.0,
        "displacement_price": 96.0, "atr": 2.0,
        "mss_candle": {"open_time": "2026-01-02 01:00:00", "open": 101.0, "high": 103.0, "low": 96.0, "close": 97.0, "volume": 1.0},
    })
    audited_message = runner.setup_message(audited)
    assert "Basis: H4 BEAR zone 95-105 (age 48.0h)" in audited_message
    assert "M15 sweep 106 | pivots H104/L98 | MSS close 97 | ATR 2" in audited_message
    assert "FILLED" not in placed
    assert filled.startswith("✅ FILLED BTCUSDT SELL @~99.5")
    assert "SETUP" not in filled
