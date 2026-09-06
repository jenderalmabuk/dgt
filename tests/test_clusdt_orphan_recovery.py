import copy, hashlib, importlib.util, json
from pathlib import Path
from types import SimpleNamespace
import pytest

P=Path(__file__).parents[1]/"bots"/"autonomous_basket_trader.py"
spec=importlib.util.spec_from_file_location("auto_clusdt",P); auto=importlib.util.module_from_spec(spec); spec.loader.exec_module(auto)
IDENTITY="basket-1786685403-CLUSDT"
NOW=1786693051.0

def fixture():
    ledger={"day":"2026-08-14","loss_streak":0,"modeled_loss_reserved":"2.0","open_basket":IDENTITY,"submissions":1,"trades":{IDENTITY:{"modeled_loss":"2","status":"ambiguous"}}}
    monitor={"identity":IDENTITY,"submitted":True,"winner":"CLUSDT","winnerPlan":{"plans":[{"symbol":"CLUSDT"}]},"plan":{"plans":[{"symbol":"CLUSDT"}]},"disarmed":[]}
    executor={"status":"watching","candidate":None,"winner":None,"body":None,"orderLinkId":None,"orderId":None,"approved":{"issued_at":1786685403.3571663,"nonce":"a1786685403"},"planDocument":{"plans":[{"symbol":"CLUSDT"}]},"executorSourceSha256":"f7ccbf216643b8bea284826604b846cc75e110ef3f7ebe524c5d3d7b1e9682d4","exposure_possible":None}
    return ledger,monitor,executor

def put(root,values):
    for name,value in zip(("ledger","monitor","executor"),values): auto.atomic_json(root/f"{name}.json",value)

def hashes(root):
    return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob("*.json")}

class GETOnly:
    now=NOW
    def __init__(self,exposure=None): self.calls=[]; self.exposure=exposure
    def __call__(self,path,params=None,private=False,**kw):
        assert "method" not in kw and "body" not in kw
        self.calls.append((path,params,private))
        rows=[]
        if self.exposure=="order" and path=="/v5/order/realtime": rows=[{"symbol":"CLUSDT","orderStatus":"New","orderLinkId":IDENTITY}]
        if self.exposure=="history" and path=="/v5/order/history": rows=[{"symbol":"CLUSDT","orderStatus":"Cancelled","orderLinkId":IDENTITY}]
        if self.exposure=="fill" and path=="/v5/execution/list": rows=[{"symbol":"CLUSDT","orderLinkId":IDENTITY,"execQty":"1"}]
        if self.exposure=="position" and path=="/v5/position/list": rows=[{"symbol":"CLUSDT","size":"1"}]
        return {"_response_ts":NOW,"list":rows}

def test_exact_orphan_recovers_via_public_run_cycle_get_only(tmp_path):
    put(tmp_path,fixture()); t=GETOnly()
    result=auto.run_cycle("live",t,NOW,state_dir=tmp_path,wall_clock=lambda:NOW)
    assert result=={"mode":"live","status":"recovered","identity":IDENTITY}
    ledger=json.loads((tmp_path/"ledger.json").read_text()); monitor=json.loads((tmp_path/"monitor.json").read_text()); executor=json.loads((tmp_path/"executor.json").read_text())
    assert (ledger["trades"][IDENTITY]["status"],ledger["open_basket"],ledger["modeled_loss_reserved"],ledger["submissions"])==("failed_prewrite",None,"0.0",0)
    assert monitor["submitted"] is False and monitor["state"]=="recovered" and monitor["failedIdentity"]==IDENTITY
    assert executor["status"]=="watching" and executor["exposure_possible"] is False
    assert [x[0] for x in t.calls]==["/v5/order/realtime","/v5/order/history","/v5/execution/list","/v5/position/list"]

def test_recovered_terminal_consumed_next_public_cycle_and_scanner_runs(tmp_path,monkeypatch):
    put(tmp_path,fixture()); transport=GETOnly()
    assert auto.run_cycle("live",transport,NOW,state_dir=tmp_path,wall_clock=lambda:NOW)["status"]=="recovered"
    recovered=json.loads((tmp_path/"monitor.json").read_text()); audit=copy.deepcopy(json.loads((tmp_path/"ledger.json").read_text())["trades"])
    scans=[]
    monkeypatch.setattr(auto,"load_policy",lambda *_:SimpleNamespace(document={},digest="policy"))
    monkeypatch.setattr(auto,"require_live_gate",lambda loaded,_now:loaded)
    monkeypatch.setattr(auto,"scan_market",lambda *_:scans.append(1) or {"plans":[],"generated_at":"2026-08-14T10:17:31Z","expires_at":"2026-08-14T11:17:31Z"})
    second=auto.run_cycle("live",transport,NOW,state_dir=tmp_path,wall_clock=lambda:NOW)
    third=auto.run_cycle("live",transport,NOW,state_dir=tmp_path,wall_clock=lambda:NOW)
    assert second["status"]==third["status"]=="watching" and scans==[1,1], (second,third,json.loads((tmp_path/"health.json").read_text()))
    assert not (tmp_path/"clusdt_terminal_reset.json").exists()
    ledger=json.loads((tmp_path/"ledger.json").read_text()); monitor=json.loads((tmp_path/"monitor.json").read_text())
    assert ledger["trades"]==audit and ledger["open_basket"] is None and ledger["submissions"]==0 and ledger["modeled_loss_reserved"]=="0.0"
    assert monitor.get("failedIdentity")==recovered["failedIdentity"] and monitor.get("recoveryConsumedAt")==NOW

@pytest.mark.parametrize("stage",["after_journal","after_monitor"])
def test_recovered_terminal_consumption_crash_restart_idempotent(tmp_path,stage):
    put(tmp_path,fixture()); runtime=auto.Executor.Runtime(GETOnly(),"live"); auto.recover_clusdt_orphan(runtime,tmp_path,NOW)
    with pytest.raises(RuntimeError,match="injected "+stage): auto.consume_clusdt_recovered_terminal(tmp_path,NOW,_fault=stage)
    assert (tmp_path/"clusdt_terminal_reset.json").exists()
    auto.consume_clusdt_recovered_terminal(tmp_path,NOW)
    before=hashes(tmp_path); assert auto.consume_clusdt_recovered_terminal(tmp_path,NOW) is False; assert hashes(tmp_path)==before

@pytest.mark.parametrize("field,value",[("submitted",True),("state","active"),("failedIdentity","basket-other")])
def test_recovered_terminal_consumption_ambiguous_fails_closed_byte_preserved(tmp_path,field,value):
    put(tmp_path,fixture()); auto.recover_clusdt_orphan(auto.Executor.Runtime(GETOnly(),"live"),tmp_path,NOW)
    monitor=json.loads((tmp_path/"monitor.json").read_text()); monitor[field]=value; auto.atomic_json(tmp_path/"monitor.json",monitor); before=hashes(tmp_path)
    with pytest.raises(auto.FailClosed): auto.consume_clusdt_recovered_terminal(tmp_path,NOW)
    assert hashes(tmp_path)==before

@pytest.mark.parametrize("exposure",["order","history","fill","position"])
def test_exact_orphan_exposure_fails_closed_byte_preserved(tmp_path,exposure):
    put(tmp_path,fixture()); before=hashes(tmp_path)
    result=auto.run_cycle("live",GETOnly(exposure),NOW,state_dir=tmp_path,wall_clock=lambda:NOW)
    assert result["status"]=="degraded"
    after=hashes(tmp_path); assert all(after[k]==v for k,v in before.items())

@pytest.mark.parametrize("name,key,value",[("ledger","submissions",2),("monitor","winner","BTCUSDT"),("executor","executorSourceSha256","0"*64)])
def test_exact_orphan_tamper_fails_closed_byte_preserved(tmp_path,name,key,value):
    values=list(fixture()); idx=("ledger","monitor","executor").index(name); values[idx][key]=value; put(tmp_path,values); before=hashes(tmp_path)
    assert auto.run_cycle("live",GETOnly(),NOW,state_dir=tmp_path,wall_clock=lambda:NOW)["status"]=="degraded"
    after=hashes(tmp_path); assert all(after[k]==v for k,v in before.items())

@pytest.mark.parametrize("stage",["after_journal","after_ledger","after_monitor","after_executor"])
def test_exact_orphan_recovery_crash_rolls_forward(tmp_path,stage):
    put(tmp_path,fixture()); runtime=auto.Executor.Runtime(GETOnly(),"live")
    with pytest.raises(RuntimeError): auto.recover_clusdt_orphan(runtime,tmp_path,NOW,_fault=stage)
    assert auto.recover_clusdt_orphan(runtime,tmp_path,NOW)["status"]=="recovered"
    assert auto.classify_durable_tuple(*[json.loads((tmp_path/f"{n}.json").read_text()) for n in ("ledger","monitor","executor")])[0]=="INERT"


def test_recovery_journal_tamper_fails_closed(tmp_path):
    put(tmp_path,fixture()); runtime=auto.Executor.Runtime(GETOnly(),"live")
    with pytest.raises(RuntimeError): auto.recover_clusdt_orphan(runtime,tmp_path,NOW,_fault="after_journal")
    p=tmp_path/"clusdt_orphan_recovery.json"; j=json.loads(p.read_text()); j["identity"]="basket-1-BTCUSDT"; auto.atomic_json(p,j); before=hashes(tmp_path)
    with pytest.raises(auto.FailClosed): auto.recover_clusdt_orphan(runtime,tmp_path,NOW)
    after=hashes(tmp_path); assert all(after[k]==v for k,v in before.items())
