import json

import pytest

from bots import autonomous_basket_trader as auto
from tests.test_autonomous_basket_trader import ChronologicalLiveTransport, NOW, enable_live, policy


def _symbols(document):
    return {row["symbol"] for row in document["plan"]["plans"]}


def _watching_symbols(document):
    return set(document["touched"]) == set(document["permanent_attempts"]) == set(document["transient_attempts"])


def test_run_cycle_keeps_executor_monitor_symbol_consensus_on_plan_creation_and_expiry(tmp_path, monkeypatch):
    state = tmp_path / "state"; tx = ChronologicalLiveTransport(state); pp = enable_live(tmp_path, monkeypatch)
    assert auto.run_cycle("live", tx, NOW, policy_path=pp, state_dir=state)["status"] == "watching"
    monitor = json.loads((state / "monitor.json").read_text()); executor = json.loads((state / "executor.json").read_text())
    assert _watching_symbols(executor) and set(executor["touched"]) == _symbols(monitor)
    old = set(executor["touched"]); tx.now = NOW + 901
    assert auto.run_cycle("live", tx, tx.now, policy_path=pp, state_dir=state)["status"] == "watching"
    monitor = json.loads((state / "monitor.json").read_text()); executor = json.loads((state / "executor.json").read_text())
    assert _watching_symbols(executor) and set(executor["touched"]) == _symbols(monitor)
    assert json.loads((state / "health.json").read_text())["state"] == "healthy"


def test_run_cycle_repairs_exact_stale_production_symbol_mismatch_once(tmp_path, monkeypatch):
    state = tmp_path / "state"; tx = ChronologicalLiveTransport(state); pp = enable_live(tmp_path, monkeypatch)
    auto.run_cycle("live", tx, NOW, policy_path=pp, state_dir=state)
    stale = auto.Executor.new_state(None, {s: None for s in ("PENGUUSDT", "SKHYNIXUSDT", "XAGUSDT", "XAUUSDT", "ZECUSDT")})
    stale["exposure_possible"] = False
    (state / "executor.json").write_bytes(auto.canonical_bytes(stale) + b"\n")
    tx.calls.clear(); tx.now = NOW + 1
    assert auto.run_cycle("live", tx, tx.now, policy_path=pp, state_dir=state)["status"] == "watching"
    repaired = (state / "executor.json").read_bytes(); monitor = json.loads((state / "monitor.json").read_text())
    assert set(json.loads(repaired)["touched"]) == _symbols(monitor) and not [c for c in tx.calls if c[0] == "POST"]
    tx.calls.clear(); tx.now += 1; auto.run_cycle("live", tx, tx.now, policy_path=pp, state_dir=state)
    assert (state / "executor.json").read_bytes() == repaired and not [c for c in tx.calls if c[0] == "POST"]


@pytest.mark.parametrize("target,key,value", [
    ("executor", "status", "open"), ("executor", "body", {"symbol":"DOGEUSDT"}),
    ("executor", "orderLinkId", "basket-x"), ("executor", "exposure_possible", True),
    ("ledger", "open_basket", "basket-x"),
])
def test_run_cycle_symbol_mismatch_active_or_ambiguous_refuses_byte_preserved_zero_writes(tmp_path, monkeypatch, target, key, value):
    state = tmp_path / "state"; tx = ChronologicalLiveTransport(state); pp = enable_live(tmp_path, monkeypatch)
    auto.run_cycle("live", tx, NOW, policy_path=pp, state_dir=state)
    executor = auto.Executor.new_state(None, {"PENGUUSDT": None}); executor["exposure_possible"] = False
    (state / "executor.json").write_bytes(auto.canonical_bytes(executor) + b"\n")
    path = state / (target + ".json")
    document = json.loads(path.read_text()) if path.exists() else {"open_basket":None,"trades":{},"modeled_loss_reserved":"0"}
    document[key] = value
    if target == "ledger" and value is not None: document.setdefault("trades", {})[value] = {"status":"open","modeled_loss":"2"}
    path.write_bytes(auto.canonical_bytes(document) + b"\n")
    before = {p.name:p.read_bytes() for p in state.iterdir()}; tx.calls.clear(); tx.now = NOW + 1
    result = auto.run_cycle("live", tx, tx.now, policy_path=pp, state_dir=state)
    assert result["status"] == "degraded" and not [c for c in tx.calls if c[0] == "POST"]
    assert {k:v for k,v in before.items() if k != "health.json"} == {p.name:p.read_bytes() for p in state.iterdir() if p.name != "health.json"}
