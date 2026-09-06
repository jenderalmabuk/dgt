import json, os
from pathlib import Path
import pytest
from bots import autonomous_basket_trader as A

E=A.Executor

def test_b5_executor_save_fsyncs_parent_after_replace(tmp_path, monkeypatch):
    events=[]
    real_open=os.open; real_fsync=os.fsync; real_replace=os.replace
    monkeypatch.setattr(os,"replace",lambda a,b:(events.append("replace"),real_replace(a,b))[1])
    monkeypatch.setattr(os,"open",lambda p,flags,*a:(events.append("dir_open") if flags & os.O_DIRECTORY else None,real_open(p,flags,*a))[1])
    monkeypatch.setattr(os,"fsync",lambda fd:(events.append("fsync"),real_fsync(fd))[1])
    E.save_state({"ok":1},tmp_path/"state.json")
    assert events.index("replace") < events.index("dir_open") < len(events)-1

def test_b10_cost_units_require_qty_for_total():
    plan=E.Plan("Buy",(99,101),101,98,106)
    unit=E.modeled_round_trip_cost_per_unit(plan,100)
    assert E.modeled_round_trip_cost_usd(plan,2,100)==pytest.approx(2*unit)
    with pytest.raises(TypeError): E.modeled_round_trip_cost_usd(plan,entry=100)

def test_b12_expiry_instant_is_invalid():
    doc={"schema":"nexus.basket-plan","version":1,"generated_at":"2026-08-12T00:00:00Z","expires_at":"2026-08-12T00:15:00Z","source":"x","provenance":{},"plans":[{"symbol":"BTCUSDT","side":"Buy","zone":["99","100"],"trigger":"100","invalid":"99","stop":"98","tp":"102"}]}
    expiry=A.parse_z(doc["expires_at"])
    with pytest.raises(E.PermanentError): E.validate_plan_document(doc,expiry)
    approval=E.make_approval("x",expiry-1,ttl=1)
    with pytest.raises(E.PermanentError): E.validate_approval(approval,expiry)
    with pytest.raises(A.FailClosed): A.validate_scan_plan(doc,expiry,{"BTCUSDT"})

def test_b11_lock_is_canonical_to_resolved_state_dir(tmp_path,monkeypatch):
    state=tmp_path/"state"; monkeypatch.chdir(tmp_path.parent)
    assert A.canonical_lock_path(state)==state.resolve().with_suffix(".lock")

def test_b3_rollover_preserves_open_reservation(tmp_path):
    path=tmp_path/"ledger.json"; old=1723507199; new=1723507201
    ledger=A.DailyLedger(path,lambda:old); ledger.reserve("trade",2)
    rolled=A.DailyLedger(path,lambda:new)
    assert rolled.data["open_basket"]=="trade"
    assert rolled.data["trades"]["trade"]["status"]=="reserved"

@pytest.mark.parametrize("fault",["after_journal","after_ledger","after_monitor","after_executor","after_commit"])
def test_b4_reservation_journal_crashes_roll_forward(tmp_path,fault):
    ledger=A.DailyLedger(tmp_path/"ledger.json",lambda:1723500000); ledger._save()
    monitor={"plan":{"plans":[{"symbol":"BTCUSDT"}]},"submitted":False,"reservationAt":1723500000}; A.atomic_json(tmp_path/"monitor.json",monitor)
    targets=A._reservation_targets(tmp_path,monitor,"BTCUSDT",{"plans":[{"symbol":"BTCUSDT"}]},"auto-x-BTCUSDT",2,{"approved":1})
    with pytest.raises(RuntimeError): A._reservation_transaction(tmp_path,targets,fault)
    A._reservation_transaction(tmp_path,None)
    saved=json.loads((tmp_path/"ledger.json").read_text())
    assert saved["submissions"]==1 and saved["open_basket"]=="auto-x-BTCUSDT"
    assert json.loads((tmp_path/"monitor.json").read_text())["identity"]=="auto-x-BTCUSDT"
    assert json.loads((tmp_path/"executor.json").read_text())["approved"]==1
    assert not (tmp_path/"reservation_txn.json").exists()

def test_b4_reservation_journal_tamper_fails_closed(tmp_path):
    ledger=A.DailyLedger(tmp_path/"ledger.json",lambda:1723500000); ledger._save()
    monitor={"submitted":False,"reservationAt":1723500000}; A.atomic_json(tmp_path/"monitor.json",monitor)
    targets=A._reservation_targets(tmp_path,monitor,"BTCUSDT",{"plans":[]},"auto-x-BTCUSDT",2,{"approved":1})
    with pytest.raises(RuntimeError): A._reservation_transaction(tmp_path,targets,"after_journal")
    sealed=json.loads((tmp_path/"reservation_txn.json").read_text()); sealed["identity"]="tampered"; A.atomic_json(tmp_path/"reservation_txn.json",sealed)
    before=(tmp_path/"ledger.json").read_bytes()
    with pytest.raises(A.FailClosed): A._reservation_transaction(tmp_path,None)
    assert (tmp_path/"ledger.json").read_bytes()==before

def test_b6_historical_durable_binding_accepts_old_source_without_old_code():
    doc={"schema":"nexus.basket-plan","version":1,"generated_at":"2026-08-12T00:00:00Z","expires_at":"2026-08-12T00:15:00Z","source":"x","provenance":{},"plans":[{"symbol":"BTCUSDT","side":"Buy","zone":["99","100"],"trigger":"100","invalid":"99","stop":"98","tp":"102"}]}
    source="1"*64; approval=E.make_approval("old",A.parse_z(doc["generated_at"]),plan_document=doc,source_hash=source)
    state={"planDocument":doc,"executorSourceSha256":source,"planFingerprint":approval["fingerprint"],"approved":approval}
    assert E.validate_durable_recovery(state)[0]==doc

@pytest.mark.parametrize("mutation",["missing","malformed","fingerprint","approval_source"])
def test_b6_invalid_historical_durable_binding_fails_closed(mutation):
    doc={"schema":"nexus.basket-plan","version":1,"generated_at":"2026-08-12T00:00:00Z","expires_at":"2026-08-12T00:15:00Z","source":"x","provenance":{},"plans":[{"symbol":"BTCUSDT","side":"Buy","zone":["99","100"],"trigger":"100","invalid":"99","stop":"98","tp":"102"}]}
    source="1"*64; approval=E.make_approval("old",A.parse_z(doc["generated_at"]),plan_document=doc,source_hash=source)
    state={"planDocument":doc,"executorSourceSha256":source,"planFingerprint":approval["fingerprint"],"approved":approval}
    if mutation=="missing": state.pop("approved")
    elif mutation=="malformed": state["executorSourceSha256"]="bad"
    elif mutation=="fingerprint": state["planFingerprint"]="0"*64
    else: state["approved"]={**approval,"executorSourceSha256":"2"*64}
    with pytest.raises(E.PermanentError): E.validate_durable_recovery(state)
