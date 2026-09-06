import importlib.util, json, threading, urllib.error
from datetime import datetime, timezone
from pathlib import Path
import pytest

P=Path(__file__).parents[1]/"bots"/"autonomous_basket_trader.py"
spec=importlib.util.spec_from_file_location("auto",P); auto=importlib.util.module_from_spec(spec); spec.loader.exec_module(auto)
NOW=datetime(2026,8,10,12,tzinfo=timezone.utc).timestamp()

def _ttl_row():
 return {"symbol":"AAAUSDT","side":"Buy","zone":["99","100"],"trigger":"101","invalid":"98","stop":"97","tp":"105","score":1,"market":{}}

def test_setup_plan_lives_four_hours_but_not_beyond():
 plan=auto.build_plan([_ttl_row()],NOW,"scan-ttl")
 assert auto.parse_z(plan["expires_at"])-auto.parse_z(plan["generated_at"])==4*3600
 assert auto.validate_scan_plan(plan,NOW+901,{"AAAUSDT"}) is plan
 assert auto.validate_scan_plan(plan,NOW+4*3600-1,{"AAAUSDT"}) is plan
 with pytest.raises(auto.FailClosed,match="stale plan"):
  auto.validate_scan_plan(plan,NOW+4*3600,{"AAAUSDT"})

def test_m15_invalidation_disarms_touch_immediately():
 plan=auto.build_plan([_ttl_row()],NOW,"scan-invalid")
 floor=auto.m5_causal_floor(NOW); monitor={"plan":plan,"touches":{"AAAUSDT":floor},"triggerTs":{"AAAUSDT":floor+300},"submitted":False}
 assert auto.observe_trigger(monitor,{}, {"AAAUSDT":True},floor+900) is None
 assert monitor["touches"]["AAAUSDT"] is None and "AAAUSDT" not in monitor["triggerTs"]

def test_plan_refresh_preserves_valid_touch_chronology_until_setup_expiry():
 plan=auto.build_plan([_ttl_row()],NOW,"scan-touch")
 floor=auto.m5_causal_floor(NOW); monitor={"plan":plan,"touches":{"AAAUSDT":floor},"triggerTs":{},"submitted":False}
 auto.sanitize_trigger_observations(monitor)
 assert monitor["touches"]["AAAUSDT"]==floor
 candle={"ts":floor+300,"low":101,"high":102,"close":102,"closed":True}
 assert auto.observe_trigger(monitor,{"AAAUSDT":[candle]},{"AAAUSDT":False},floor+600)=="AAAUSDT"
 assert monitor["touches"]["AAAUSDT"]==floor and monitor["triggerTs"]["AAAUSDT"]==floor+300

def test_run_cycle_bybit_10002_write_ambiguity_degrades_and_next_cycle_runs(tmp_path):
 writes=[]; attempts=iter(["ambiguous","healthy"])
 def run_once(*args,**kwargs):
  if next(attempts)=="ambiguous": writes.append("POST"); raise auto.Executor.AmbiguousOrder("10002: timestamp expired")
  return {"mode":"live","status":"watching"}
 first=auto.run_cycle("live",object(),1000,state_dir=tmp_path,run_once_fn=run_once,wall_clock=lambda:1000)
 second=auto.run_cycle("live",object(),1001,state_dir=tmp_path,run_once_fn=run_once,wall_clock=lambda:1001)
 assert first=={"mode":"live","status":"degraded","code":"AmbiguousOrder"}
 assert second=={"mode":"live","status":"watching"}
 assert writes==["POST"]

def rejection_record(identity="auto-1-AAAUSDT",side="Buy",entry="100",stop="90",tp="120",cost="0.2"):
 return {"schema":"nexus.rejected-candidate","version":1,"identity":identity,"candidate":{"symbol":"AAAUSDT","side":side},
  "decision_ts":NOW,"rejection_ts":NOW,"trigger_m15":{"open_ts":NOW-900,"close_ts":NOW},"winner_evidence":{"symbol":"AAAUSDT"},
  "intended":{"entry":entry,"stop_loss":stop,"take_profit":tp,"qty":"1","rr":"2","modeled_round_trip_cost":cost,"modeled_loss":"10"},
  "observations":{},"rejection":{"code":"WINNER_CURRENT_MARKET_INVALID","message":"winner current market invalid","gate_evidence":{}},
  "bindings":{"executor_source_sha256":auto.EXECUTOR_SHA256,"policy_sha256":"f"*64},"zero_write_proof":{"body":None,"orderLinkId":None,"orderId":None,"exposure_possible":False},"reservation_disposition":"failed_prewrite"}

def test_rejection_jsonl_exact_dedup_crash_safe_and_no_secrets(tmp_path):
 path=tmp_path/"rejected_candidates.jsonl"; record=rejection_record(); record["observations"]={"api_key":"SECRET","bid":"99"}
 auto.append_rejection(path,record); auto.append_rejection(path,record)
 assert len(auto.load_rejections(path))==1
 assert b"SECRET" not in path.read_bytes() and auto.load_rejections(path)[0]["rejection"]["code"]=="WINNER_CURRENT_MARKET_INVALID"
 path.write_bytes(path.read_bytes()+b'{"partial"')
 auto.append_rejection(path,rejection_record("auto-2-AAAUSDT"))
 assert [x["identity"] for x in auto.load_rejections(path)]==["auto-1-AAAUSDT","auto-2-AAAUSDT"]

@pytest.mark.parametrize("side,candles,outcome",[("Buy",[(101,121,99)],"TP_FIRST"),("Buy",[(99,101,89)],"SL_FIRST"),("Buy",[(100,121,89)],"SL_FIRST"),("Sell",[(99,101,79)],"TP_FIRST")])
def test_counterfactual_first_touch_side_same_bar_and_chronology(side,candles,outcome):
 record=rejection_record(side=side,entry="100",stop="90" if side=="Buy" else "110",tp="120" if side=="Buy" else "80")
 rows=[{"ts":NOW-60,"open":100,"high":1000,"low":1,"close":100}]+[{"ts":NOW+60+i*60,"open":o,"high":h,"low":l,"close":o} for i,(o,h,l) in enumerate(candles)]
 assert auto.evaluate_rejection(record,rows,NOW+3600)["outcome"]==outcome

def test_counterfactual_cost_neither_ambiguous_and_historical_unverifiable():
 tp=auto.evaluate_rejection(rejection_record(),[{"ts":NOW+60,"open":100,"high":121,"low":99,"close":120}],NOW+3600)
 assert tp["gross_r"]==2 and tp["net_r"]==pytest.approx(1.98)
 assert auto.evaluate_rejection(rejection_record(),[{"ts":NOW+60,"open":100,"high":110,"low":95,"close":100}],NOW+3600)["outcome"]=="NEITHER"
 assert auto.evaluate_rejection(rejection_record(),[{"ts":NOW+60,"high":"bad","low":95}],NOW+3600)["outcome"]=="AMBIGUOUS_DATA"
 assert auto.evaluate_rejection({"identity":"legacy"},[],NOW+3600)["outcome"]=="UNVERIFIABLE"

def policy():
 return {"schema":"nexus.autonomous-basket-policy","version":1,"starts_at":"2026-08-10T00:00:00Z","max_entries_day":2,"max_open_baskets":1,"max_modeled_loss_trade_usd":"2","max_modeled_loss_day_usd":"4","entry":"PostOnly-no-chase","protection":"native-single-TP-plus-SL","partial_tp":False,"loss_pause":2,"executor_sha256":auto.EXECUTOR_SHA256}

def test_policy_canonical_hash_and_source_binding(tmp_path):
 p=policy(); path=tmp_path/"policy.json"; auto.write_policy(path,p)
 assert path.read_bytes().endswith(b"\n")
 loaded=auto.load_policy(path,NOW)
 assert loaded.digest==auto.canonical_hash(p)
 bad=dict(p,executor_sha256="0"*64)
 with pytest.raises(auto.FailClosed): auto.validate_policy(bad,NOW)

def test_ledger_atomic_rollover_limits_and_ambiguous_counts(tmp_path):
 l=auto.DailyLedger(tmp_path/"ledger.json",lambda:NOW)
 l.reserve("a",2); l.mark_ambiguous("a")
 with pytest.raises(auto.FailClosed,match="basket already open"): l.reserve("b",2)
 l.close("a",0); l.reserve("b",2)
 with pytest.raises(auto.FailClosed,match="entry cap"): l.reserve("c",0)
 assert auto.DailyLedger(l.path,lambda:NOW).data["submissions"]==2
 tomorrow=NOW+86400
 l.close("b",0)
 assert auto.DailyLedger(l.path,lambda:tomorrow).data["submissions"]==0

def test_ledger_rollover_preserves_open_trade_across_midnight(tmp_path):
 l=auto.DailyLedger(tmp_path/"ledger.json",lambda:NOW); l.reserve("open",2); before=l.path.read_bytes()
 rolled=auto.DailyLedger(l.path,lambda:NOW+86400)
 assert rolled.data["submissions"]==1 and rolled.data["open_basket"]=="open"
 assert rolled.data["trades"]["open"]["status"]=="reserved"
 assert l.path.read_bytes()==before

def test_ledger_rollover_without_open_basket_persists_current_utc_day(tmp_path):
 path=tmp_path/"ledger.json"
 path.write_text(json.dumps({"day":"2020-01-01","submissions":2,"modeled_loss_reserved":"4","loss_streak":2,"open_basket":None,"trades":{}}))
 ledger=auto.DailyLedger(path,lambda:NOW)
 assert json.loads(path.read_text())==ledger.data
 assert ledger.data["day"]==auto.utc_day(NOW)

def test_ledger_rollover_recomputes_submissions_when_only_failed_prewrite(tmp_path):
 path=tmp_path/"ledger.json"; trade={"status":"failed_prewrite","modeled_loss":"2"}
 path.write_text(json.dumps({"day":"2020-01-01","submissions":1,"modeled_loss_reserved":"2","loss_streak":1,"open_basket":None,"trades":{"old":trade}}))
 ledger=auto.DailyLedger(path,lambda:NOW)
 assert ledger.data["submissions"]==0 and ledger.data["trades"]=={"old":trade}
 assert ledger.data["modeled_loss_reserved"]=="0"

def test_reservation_targets_use_current_day_after_rollover(tmp_path):
 path=tmp_path/"ledger.json"
 path.write_text(json.dumps({"day":"2020-01-01","submissions":1,"modeled_loss_reserved":"0","loss_streak":0,"open_basket":None,"trades":{"old":{"status":"failed_prewrite","modeled_loss":"2"}}}))
 targets=auto._reservation_targets(tmp_path,{"plan":{"plans":[]},"reservationAt":NOW},"BTCUSDT",{},"new",2,{})
 assert targets["ledger"]["day"]==auto.utc_day(NOW) and targets["ledger"]["submissions"]==1

@pytest.mark.parametrize("status",["open","ambiguous"])
def test_ledger_rollover_preserves_open_or_ambiguous_basket_unchanged(tmp_path,status):
 path=tmp_path/"ledger.json"; doc={"day":"2020-01-01","submissions":1,"modeled_loss_reserved":"2","loss_streak":1,"open_basket":"active","trades":{"active":{"status":status,"modeled_loss":"2"}}}
 path.write_text(json.dumps(doc)); before=path.read_bytes()
 assert auto.DailyLedger(path,lambda:NOW).data==doc
 assert path.read_bytes()==before


def test_repaired_terminal_restart_routes_before_stale_policy(tmp_path,monkeypatch):
 state=tmp_path/"state"; state.mkdir(); identity="auto-1-DOGEUSDT"
 (state/"executor.json").write_text(json.dumps({"status":"failed_prewrite_repaired","exposure_possible":False,"autonomousPolicyRequired":True,"policy_snapshot":{"old":True},"policy_digest":"old"}))
 (state/"monitor.json").write_text(json.dumps({"state":"repair_complete","submitted":False,"identity":identity,"winner":"DOGEUSDT"}))
 (state/"ledger.json").write_text(json.dumps({"day":"2026-08-10","submissions":0,"modeled_loss_reserved":"0","loss_streak":0,"open_basket":None,"trades":{identity:{"status":"failed_prewrite","modeled_loss":"2"}}}))
 pp=tmp_path/"policy"; auto.write_policy(pp,policy()); loaded=auto.load_policy(pp,NOW)
 monkeypatch.setenv("AUTONOMOUS_BASKET_LIVE","YES"); monkeypatch.setenv("AUTONOMOUS_BASKET_POLICY_SHA256",loaded.digest)
 monkeypatch.setattr(auto,"scan_market",lambda runtime,now,scan_id:auto.build_plan([],now,scan_id))
 assert auto.run_once("live",FakeTransport({}),NOW,policy_path=pp,state_dir=state)["status"]=="watching"
 assert json.loads((state/"executor.json").read_text())==auto.Executor.new_state(None)
 assert json.loads((state/"ledger.json").read_text())["submissions"]==0


def test_run_once_resets_already_repaired_fatal_without_stale_monitor_ownership(tmp_path,monkeypatch):
 state=tmp_path/"state"; state.mkdir(); identity="auto-1786580127-BNBUSDT"
 executor={"status":"fatal","exposure_possible":False,"errors":{"BNBUSDT":"winner current market invalid"},"fatal_reason":"winner current market invalid"}
 monitor={"submitted":False,"identity":identity,"winner":"OLDUSDT","plan":{"plans":[{"symbol":"NEWUSDT"}]}}
 ledger={"day":"2026-08-10","submissions":0,"modeled_loss_reserved":"0","loss_streak":0,"open_basket":None,"trades":{identity:{"status":"failed_prewrite","modeled_loss":"2"}}}
 for name,doc in (("executor",executor),("monitor",monitor),("ledger",ledger)): (state/(name+".json")).write_text(json.dumps(doc))
 pp=tmp_path/"policy"; auto.write_policy(pp,policy()); loaded=auto.load_policy(pp,NOW)
 monkeypatch.setenv("AUTONOMOUS_BASKET_LIVE","YES"); monkeypatch.setenv("AUTONOMOUS_BASKET_POLICY_SHA256",loaded.digest)
 monkeypatch.setattr(auto,"scan_market",lambda runtime,now,scan_id:auto.build_plan([],now,scan_id))
 tx=FakeTransport({})
 assert auto.run_once("live",tx,NOW,policy_path=pp,state_dir=state)["status"]=="watching"
 assert auto.run_once("live",tx,NOW,policy_path=pp,state_dir=state)["status"]=="watching"
 assert json.loads((state/"executor.json").read_text())==auto.Executor.new_state(None)
 assert json.loads((state/"ledger.json").read_text())["trades"][identity]["status"]=="failed_prewrite"
 assert tx.calls==[]


def actual_production_released_fatal_files(tmp_path):
 state=tmp_path/"state"; state.mkdir(); issued_at=1786580127.5383234; symbol="BNBUSDT"; identity="auto-1786580127-BNBUSDT"
 row={"symbol":symbol,"side":"Buy","zone":["850","851"],"trigger":"851","invalid":"850","stop":"849","tp":"855"}
 plan=auto.build_plan([{**row,"score":1}],1786580100,"scan-bnb-production")
 source="52531ac1514b09f64076b4403acf764a884ca8c180a67601c9d1c838f30c0e08"
 approval=auto.Executor.make_approval("a1786580127",issued_at,300,plan,source)
 executor=auto.Executor.new_state(None); executor.update(status="fatal",candidate=symbol,winner=symbol,exposure_possible=False,approved=approval,planDocument=plan,planFingerprint=approval["fingerprint"],executorSourceSha256=source,errors={symbol:"final market invalid: candle stale or invalid"},fatal_reason="final market invalid: candle stale or invalid",finalGate={"ok":False,"code":"FINAL_CANDLE_STALE_OR_INVALID"})
 monitor={"submitted":False,"plan":plan}
 ledger={"day":auto.utc_day(issued_at),"submissions":0,"modeled_loss_reserved":"0","loss_streak":0,"open_basket":None,"trades":{identity:{"status":"failed_prewrite","modeled_loss":"2"}}}
 for name,doc in (("executor",executor),("monitor",monitor),("ledger",ledger)): auto.atomic_json(state/(name+".json"),doc)
 return state,identity


def test_run_once_resets_actual_production_released_fatal_without_monitor_identity(tmp_path,monkeypatch):
 state,identity=actual_production_released_fatal_files(tmp_path); pp=tmp_path/"policy"; auto.write_policy(pp,policy()); loaded=auto.load_policy(pp,NOW)
 monkeypatch.setenv("AUTONOMOUS_BASKET_LIVE","YES"); monkeypatch.setenv("AUTONOMOUS_BASKET_POLICY_SHA256",loaded.digest)
 monkeypatch.setattr(auto,"scan_market",lambda runtime,now,scan_id:auto.build_plan([],now,scan_id))
 tx=FakeTransport({})
 assert auto.run_once("live",tx,NOW,policy_path=pp,state_dir=state)["status"]=="watching"
 assert auto.run_once("live",tx,NOW,policy_path=pp,state_dir=state)["status"]=="watching"
 assert json.loads((state/"executor.json").read_text())==auto.Executor.new_state(None)
 assert json.loads((state/"ledger.json").read_text())["trades"][identity]["status"]=="failed_prewrite" and tx.calls==[]


@pytest.mark.parametrize("mutation",["nonce","multi_plan","candidate","fingerprint","approval","duplicate","near_match","reserve","open","exposure_true","exposure_none","body","orderLinkId","orderId","pendingOrder","closeBody","closeOrderLinkId"])
def test_actual_production_released_fatal_linkage_negatives_fail_closed(tmp_path,mutation):
 state,identity=actual_production_released_fatal_files(tmp_path); executor=json.loads((state/"executor.json").read_text()); ledger=json.loads((state/"ledger.json").read_text())
 if mutation=="nonce": executor["approved"]["nonce"]="a1786580126"
 elif mutation=="multi_plan": executor["planDocument"]["plans"].append({**executor["planDocument"]["plans"][0],"symbol":"ETHUSDT"})
 elif mutation=="candidate": executor["candidate"]="ETHUSDT"
 elif mutation=="fingerprint": executor["planFingerprint"]="0"*64
 elif mutation=="approval": executor.pop("approved")
 elif mutation=="duplicate": ledger["trades"][identity+"-duplicate"]={"status":"failed_prewrite"}
 elif mutation=="near_match": ledger["trades"]={"auto-1786580128-BNBUSDT":{"status":"failed_prewrite"}}
 elif mutation=="reserve": ledger["modeled_loss_reserved"]="0.0"
 elif mutation=="open": ledger["open_basket"]=identity
 elif mutation.startswith("exposure_"): executor["exposure_possible"]={"exposure_true":True,"exposure_none":None}[mutation]
 else: executor[mutation]={} if mutation.endswith("Body") or mutation in {"body","pendingOrder"} else "x"
 auto.atomic_json(state/"executor.json",executor); auto.atomic_json(state/"ledger.json",ledger); (state/"repair.lock").touch(); before={p.name:p.read_bytes() for p in state.iterdir()}
 with pytest.raises(auto.FailClosed): auto.run_once("live",FakeTransport({}),NOW,state_dir=state)
 assert before=={p.name:p.read_bytes() for p in state.iterdir()}


@pytest.mark.parametrize("target,key,value",[("ledger","open_basket","still-open"),("trade","status","reserved"),("executor","exposure_possible",True),("executor","exposure_possible",None),("executor","body",{}),("executor","orderLinkId","x")])
def test_run_once_does_not_reset_unproven_already_repaired_fatal(tmp_path,target,key,value):
 state=tmp_path/"state"; state.mkdir(); identity="auto-1786580127-BNBUSDT"
 docs={"executor":{"status":"fatal","exposure_possible":False},"monitor":{"submitted":False,"identity":identity},"ledger":{"open_basket":None,"modeled_loss_reserved":"0","trades":{identity:{"status":"failed_prewrite"}}}}
 doc=docs["ledger" if target=="trade" else target]
 if target=="trade": doc["trades"][identity][key]=value
 else: doc[key]=value
 for name,value0 in docs.items(): (state/(name+".json")).write_text(json.dumps(value0))
 (state/"repair.lock").touch(); before={p.name:p.read_bytes() for p in state.iterdir()}
 with pytest.raises(auto.FailClosed): auto.run_once("live",FakeTransport({}),NOW,state_dir=state)
 assert before=={p.name:p.read_bytes() for p in state.iterdir()}


def _orphan_prewrite_state(state, exposure=False, identity_field=None):
 identity="auto-1786580127-BNBUSDT"; winner="BNBUSDT"
 executor={"status":"fatal","exposure_possible":exposure,"errors":{winner:"winner current market invalid"},"fatal_reason":"winner current market invalid"}
 if identity_field: executor[identity_field]="write-identity"
 monitor={"submitted":False,"identity":identity,"winner":winner,"plan":{"plans":[]}}
 ledger={"day":"2026-08-10","submissions":1,"modeled_loss_reserved":"2.0","loss_streak":0,"open_basket":identity,"trades":{identity:{"status":"reserved","modeled_loss":"2"}}}
 for name,doc in (("executor",executor),("monitor",monitor),("ledger",ledger)): (state/(name+".json")).write_text(json.dumps(doc))
 return identity


def test_run_once_repairs_orphan_prewrite_fatal_before_scan_and_is_idempotent(tmp_path,monkeypatch):
 state=tmp_path/"state"; state.mkdir(); identity=_orphan_prewrite_state(state)
 tx=FakeTransport({"/v5/position/list":{"list":[],"_response_ts":NOW},"/v5/order/realtime":{"list":[],"_response_ts":NOW}})
 monkeypatch.setattr(auto,"scan_market",lambda *_a,**_k:pytest.fail("scan ran before repair"))
 assert auto.run_once("live",tx,NOW,state_dir=state)=={"mode":"live","status":"repaired","identity":identity}
 ledger=json.loads((state/"ledger.json").read_text())
 assert ledger["trades"][identity]["status"]=="failed_prewrite" and ledger["open_basket"] is None
 assert ledger["submissions"]==0 and float(ledger["modeled_loss_reserved"])==0
 assert not [call for call in tx.calls if call[0]!="GET"]
 before={p.name:p.read_bytes() for p in state.iterdir()}
 with pytest.raises(auto.FailClosed,match="prewrite fatal state ambiguous"):
  auto.repair_prewrite_fatal(auto.Executor.Runtime(tx,"live"),state,NOW)
 assert before=={p.name:p.read_bytes() for p in state.iterdir()}


@pytest.mark.parametrize("exposure,identity_field",[(True,None),(False,"body"),(False,"orderLinkId"),(False,"orderId"),(False,"pendingOrder"),(False,"closeBody"),(False,"closeOrderLinkId")])
def test_run_once_keeps_possible_write_or_exposure_orphan_fail_closed(tmp_path,monkeypatch,exposure,identity_field):
 state=tmp_path/"state"; state.mkdir(); identity=_orphan_prewrite_state(state,exposure,identity_field); (state/"repair.lock").touch()
 tx=FakeTransport({}); before={p.name:p.read_bytes() for p in state.iterdir()}
 monkeypatch.setattr(auto,"scan_market",lambda *_a,**_k:pytest.fail("unsafe scan"))
 with pytest.raises(auto.DurableTupleError,match="FATAL_TUPLE_MISMATCH"):
  auto.run_once("live",tx,NOW,state_dir=state)
 assert before=={p.name:p.read_bytes() for p in state.iterdir()}
 assert json.loads((state/"ledger.json").read_text())["open_basket"]==identity
 assert tx.calls==[]


@pytest.mark.parametrize("target,key,value",[("executor","exposure_possible",True),("executor","body",{}),("executor","orderId","x"),("executor","winner","DOGEUSDT"),("monitor","submitted",True),("monitor","state","watching"),("monitor","identity","bad"),("ledger","open_basket","split"),("trade","status","reserved")])
def test_repaired_terminal_restart_tamper_zero_writes(tmp_path,target,key,value):
 state=tmp_path/"state"; state.mkdir(); identity="auto-1-DOGEUSDT"
 docs={"executor":{"status":"failed_prewrite_repaired","exposure_possible":False},"monitor":{"state":"repair_complete","submitted":False,"identity":identity,"winner":"DOGEUSDT"},"ledger":{"open_basket":None,"trades":{identity:{"status":"failed_prewrite"}}}}
 doc=docs["ledger" if target=="trade" else target]
 if target=="trade": doc["trades"][identity][key]=value
 else: doc[key]=value
 for name,value0 in docs.items(): (state/(name+".json")).write_text(json.dumps(value0))
 (state/"repair.lock").touch(); before={p.name:p.read_bytes() for p in state.iterdir()}
 with pytest.raises(auto.FailClosed): auto.run_once("live",FakeTransport({}),NOW,state_dir=state)
 assert before=={p.name:p.read_bytes() for p in state.iterdir()}


def test_repaired_terminal_restart_journal_fails_closed_zero_writes(tmp_path):
 state=tmp_path/"state"; state.mkdir(); identity="auto-1-DOGEUSDT"
 docs={"executor":{"status":"failed_prewrite_repaired","exposure_possible":False},"monitor":{"state":"repair_complete","submitted":False,"identity":identity,"winner":"DOGEUSDT"},"ledger":{"open_basket":None,"trades":{identity:{"status":"failed_prewrite"}}},"prewrite_repair":{}}
 for name,value in docs.items(): (state/(name+".json")).write_text(json.dumps(value))
 (state/"repair.lock").touch(); before={p.name:p.read_bytes() for p in state.iterdir()}
 with pytest.raises(auto.FailClosed): auto.run_once("live",FakeTransport({}),NOW,state_dir=state)
 assert before=={p.name:p.read_bytes() for p in state.iterdir()}

def test_two_losses_pause_until_next_utc_day(tmp_path):
 l=auto.DailyLedger(tmp_path/"l",lambda:NOW); l.reserve("a",1); l.close("a",-1)
 l.reserve("b",1); l.close("b",-1)
 with pytest.raises(auto.FailClosed,match="loss pause"): l.assert_entry_allowed()
 assert auto.DailyLedger(l.path,lambda:NOW+86400).data["loss_streak"]==0

def test_first_winner_concurrent_and_manual_exposure_fail_closed(tmp_path):
 l=auto.DailyLedger(tmp_path/"l",lambda:NOW); got=[]
 def run(x):
  try: got.append(l.reserve(x,1))
  except auto.FailClosed: pass
 ts=[threading.Thread(target=run,args=(x,)) for x in "ab"]
 [t.start() for t in ts]; [t.join() for t in ts]
 assert len(got)==1 and l.data["open_basket"] in "ab"
 with pytest.raises(auto.FailClosed): auto.assert_account_isolated([{"symbol":"BTCUSDT","size":"1"}],{"ETHUSDT"},None)

def test_scan_plan_fresh_bound_universe_and_deterministic():
 rows=[{"symbol":"BETHUSDT","score":9,"side":"Buy","zone":["1","2"],"trigger":"2","invalid":"1","stop":".5","tp":"4"},{"symbol":"AUSDT","score":9,"side":"Sell","zone":["4","5"],"trigger":"4","invalid":"5","stop":"6","tp":"2"}]
 doc=auto.build_plan(rows,NOW,"scan-1")
 assert [x["symbol"] for x in doc["plans"]]==["AUSDT","BETHUSDT"]
 assert auto.parse_z(doc["expires_at"])-auto.parse_z(doc["generated_at"])==auto.SETUP_TTL_SECONDS
 with pytest.raises(auto.FailClosed): auto.validate_scan_plan(doc,NOW,{"AUSDT"})


def test_plan_provenance_captures_sanitized_scan_market_values_and_validates():
 rows=[{"symbol":"AAAUSDT","score":9,"side":"Buy","zone":["1","2"],"trigger":"2","invalid":"1","stop":".5","tp":"4",
        "market":{"bid":1.9,"ask":2.0,"spread":.05,"funding":.0001,"oiChange":.2,"btcChange":.1,"api_key":"SECRET"}}]
 doc=auto.build_plan(rows,NOW,"scan-values")
 scan=doc["provenance"]["scan_snapshots"]["AAAUSDT"]
 assert scan["bid"]==1.9 and scan["oiChange"]==.2 and "api_key" not in scan
 auto.Executor.validate_plan_document(doc,NOW)


def test_counterfactual_unavailable_cost_is_unverifiable_not_zero():
 record=rejection_record(); record["intended"]["modeled_round_trip_cost"]=None
 result=auto.evaluate_rejection(record,[{"ts":NOW+60,"high":121,"low":99}],NOW+3600)
 assert result["outcome"]=="UNVERIFIABLE" and result["net_r"] is None


def test_exact_modeled_round_trip_cost_formula_before_sizing():
 plan=auto.Executor.Plan("Buy",(99,101),101,90,120,99)
 assert auto.Executor.modeled_round_trip_cost(plan,100,90)==pytest.approx((100+90)*.00055+100*.004)


def test_historical_generic_rejection_remains_compatible():
 assert rejection_record()["rejection"]["code"]=="WINNER_CURRENT_MARKET_INVALID"


def test_exit_rules_only_closed_m5_tighten_and_confirmed_early_close():
 long=auto.ExitState("Buy",100,90,120,1,"ETHUSDT")
 assert auto.exit_action(long,{"closed":False,"close":111,"atr":2,"swing":106},.2)==None
 be=auto.exit_action(long,{"closed":True,"close":111,"atr":2,"swing":106},.2)
 assert be=={"kind":"stop","stop":100.2,"reason":"breakeven"}; long.stop=be["stop"]
 tr=auto.exit_action(long,{"closed":True,"close":121,"atr":2,"swing":116},.2)
 assert tr["stop"]==116 and tr["reason"]=="trailing"
 assert auto.exit_action(long,{"closed":True,"close":99,"atr":2,"swing":95,"structure_break":True},.2) is None
 close=auto.exit_action(long,{"closed":True,"close":99,"atr":2,"swing":95,"structure_break":True,"oi_confirm":True},.2)
 assert close=={"kind":"close","reduceOnly":True,"closeOnTrigger":True}

def test_protection_ownership_stale_and_notifier_redaction():
 with pytest.raises(auto.FailClosed): auto.require_native_protection({"stopLoss":"","takeProfit":"2"})
 with pytest.raises(auto.FailClosed): auto.require_fresh({"response_ts":NOW-31},NOW,30)
 assert auto.owned_position([{"symbol":"ETHUSDT","size":"1","orderLinkId":"bot-x"}],"ETHUSDT","bot-x")["size"]=="1"
 with pytest.raises(auto.FailClosed): auto.owned_position([{"symbol":"ETHUSDT","size":"1","orderLinkId":"manual"}],"ETHUSDT","bot-x")
 sent=[]; auto.Notifier(sent.append,"123:SECRET","-100123").send("fill id=-100123 https://x?token=123:SECRET")
 assert "SECRET" not in sent[0] and "-100123" not in sent[0]
 failing=auto.Notifier(lambda _text:(_ for _ in ()).throw(OSError("token=leak")),"123:SECRET","-100123")
 assert failing.send("close:-100123") is False


def test_cli_notifier_env_required_only_for_fresh_live(monkeypatch):
 monkeypatch.delenv("TELEGRAM_BOT_TOKEN",raising=False); monkeypatch.delenv("TELEGRAM_CHAT_ID",raising=False)
 assert auto.cli_notifier("dry-run").send("setup:0") is False
 with pytest.raises(auto.FailClosed,match="Telegram.*required"):
  auto.cli_notifier("live")


def test_cli_notifier_uses_stdlib_telegram_and_redacts_logs(monkeypatch):
 calls=[]; logs=[]
 monkeypatch.setenv("TELEGRAM_BOT_TOKEN","123:SECRET"); monkeypatch.setenv("TELEGRAM_CHAT_ID","-100123")
 monkeypatch.setattr(auto.urllib.request,"urlopen",lambda req,timeout:calls.append((req.full_url,req.data,timeout)) or type("R",(),{"close":lambda self:None})())
 notifier=auto.cli_notifier("live",log=logs.append); assert notifier.send("fill:-100123") is True
 assert len(calls)==1 and b"chat_id=-100123" in calls[0][1]
 assert all("SECRET" not in line and "-100123" not in line for line in logs)

def test_recovery_state_machine_and_dry_run_zero_writes(tmp_path):
 state=auto.StateStore(tmp_path/"s"); state.transition("WATCHING"); state.transition("SUBMITTING",order="x"); state.transition("AMBIGUOUS")
 assert auto.StateStore(state.path).data["state"]=="AMBIGUOUS"
 writes=[]; rt=auto.Controller(lambda *a,**k:writes.append((a,k)),live=False)
 with pytest.raises(auto.FailClosed): rt.write("/v5/order/create",{})
 assert writes==[]
 assert auto.recovery_action(state.data)=="reconcile_order"

def test_single_process_lock(tmp_path):
 with auto.process_lock(tmp_path/"watch.lock"):
  with pytest.raises(auto.FailClosed):
   with auto.process_lock(tmp_path/"watch.lock"): pass

class FakeTransport:
 def __init__(self, responses): self.responses=responses; self.calls=[]
 def __call__(self,path,params=None,method="GET",body=None,private=False):
  self.calls.append((method,path,params,body,private)); value=self.responses[path]
  return value(params or {}) if callable(value) else value

@pytest.mark.parametrize("case,code",[("orphan_ledger","ORPHAN_LEDGER"),("orphan_monitor","ORPHAN_MONITOR"),("missing_ledger","ACTIVE_EXECUTOR_LEDGER_MISSING"),("ledger_mismatch","ACTIVE_EXECUTOR_LEDGER_MISMATCH"),("monitor_mismatch","ACTIVE_EXECUTOR_SYMBOL_MISMATCH"),("unknown_executor","UNKNOWN_EXECUTOR_STATUS")])
def test_startup_durable_tuple_danger_matrix_blocks_before_transport_and_writes(tmp_path,case,code):
 state=tmp_path/"state"; state.mkdir(); identity="auto-1-AAAUSDT"
 ledger={"day":"2026-08-10","submissions":1,"modeled_loss_reserved":"2","loss_streak":0,"open_basket":identity,"trades":{identity:{"status":"reserved","modeled_loss":"2"}}}
 monitor={"submitted":True,"identity":identity,"winner":"AAAUSDT","winnerPlan":{"plans":[{"symbol":"AAAUSDT"}]}}
 executor={"status":"submitting","candidate":"AAAUSDT","winner":"AAAUSDT","orderLinkId":identity,"body":{"symbol":"AAAUSDT","orderLinkId":identity}}
 if case=="orphan_ledger": executor=auto.Executor.new_state(None); monitor={"submitted":False}
 elif case=="orphan_monitor": executor=auto.Executor.new_state(None); ledger={**ledger,"open_basket":None,"modeled_loss_reserved":"0","trades":{}}
 elif case=="missing_ledger": ledger={**ledger,"open_basket":None,"modeled_loss_reserved":"0","trades":{}}
 elif case=="ledger_mismatch": ledger["open_basket"]="auto-2-BBBUSDT"; ledger["trades"]={ledger["open_basket"]:{"status":"reserved"}}
 elif case=="monitor_mismatch": monitor["winner"]="BBBUSDT"
 elif case=="unknown_executor": executor={"status":"mystery"}; ledger={**ledger,"open_basket":None,"modeled_loss_reserved":"0","trades":{}}; monitor={"submitted":False}
 for name,doc in (("ledger",ledger),("monitor",monitor),("executor",executor)): auto.atomic_json(state/(name+".json"),doc)
 before={p.name:p.read_bytes() for p in state.iterdir()}
 class NoTransport:
  def __call__(self,*_a,**_k): raise AssertionError("transport called")
 with pytest.raises(auto.DurableTupleError) as caught: auto.run_once("live",NoTransport(),NOW,state_dir=state)
 assert (caught.value.code,caught.value.tuple_class)==(code,"DEGRADED")
 assert before=={p.name:p.read_bytes() for p in state.iterdir()}

def scan_responses(exposed=()):
 nowms=int(NOW*1000)
 def candles(base,minutes):
  step=minutes*60000; end=nowms-nowms%step-step
  return [[str(end-i*step),str(base),str(base+2),str(base-2),str(base+i),"100"] for i in range(4,-1,-1)]
 return {
  "/v5/market/instruments-info":{"list":[{"symbol":"AAAUSDT","status":"Trading","contractType":"LinearPerpetual","launchTime":str(nowms-91*86400000)},{"symbol":"BBBUSDT","status":"Trading","contractType":"LinearPerpetual","launchTime":str(nowms-91*86400000)}],"_response_ts":NOW},
  "/v5/market/tickers":{"list":[{"symbol":"AAAUSDT","turnover24h":"20000000","bid1Price":"100","ask1Price":"100.1","fundingRate":"0.0001"},{"symbol":"BBBUSDT","turnover24h":"10000000","bid1Price":"200","ask1Price":"200.1","fundingRate":"0"}],"_response_ts":NOW},
  "/v5/market/kline":lambda p:{"list":candles(30000 if p["symbol"]=="BTCUSDT" else (100 if p["symbol"]=="AAAUSDT" else 200),int(p["interval"])),"_response_ts":NOW},
  "/v5/market/open-interest":lambda p:{"list":[{"timestamp":str(nowms-3600000),"openInterest":"100"},{"timestamp":str(nowms),"openInterest":"101"}],"_response_ts":NOW},
  "/v5/position/list":{"list":[{"symbol":s,"size":"1"} for s in exposed],"_response_ts":NOW},
  "/v5/order/realtime":{"list":[],"_response_ts":NOW}}

def test_market_scan_deterministic_max5_excludes_account_exposure():
 tx=FakeTransport(scan_responses({"BBBUSDT"}))
 plan=auto.scan_market(auto.Executor.Runtime(tx,"dry-run"),NOW,"scan-x")
 assert [p["symbol"] for p in plan["plans"]]==["AAAUSDT"]
 assert all(c[0]=="GET" for c in tx.calls) and plan["plans"][0]["side"] in {"Buy","Sell"}

def test_mid_scan_rate_limit_retries_complete_scan_with_deterministic_cadence():
 responses=scan_responses(); calls=0; sleeps=[]; ticks=[0.0]
 base=responses["/v5/market/kline"]
 def limited(params):
  nonlocal calls
  calls+=1
  if calls==3: raise auto.Executor.TransientError("10006: Too many visits")
  return base(params)
 responses["/v5/market/kline"]=limited
 def sleep(seconds): sleeps.append(seconds); ticks.__setitem__(0,ticks[0]+seconds)
 runtime=auto.ScanRuntime(auto.Executor.Runtime(FakeTransport(responses),"dry-run"),clock=lambda:ticks[0],sleep=sleep,min_interval=.1,retries=2)
 plan=auto.scan_market(runtime,NOW,"scan-rate")
 assert {p["symbol"] for p in plan["plans"]}=={"AAAUSDT","BBBUSDT"}
 assert calls==8 and sleeps.count(1)==1 and all(min(abs(x-.1),abs(x-1))<1e-9 for x in sleeps)


def test_repeated_rate_limit_returns_degraded_without_state_or_private_reads(tmp_path):
 responses=scan_responses(); responses["/v5/market/instruments-info"]=lambda _p:(_ for _ in ()).throw(auto.Executor.TransientError("10006 token=SECRET"))
 tx=FakeTransport(responses); notices=[]
 result=auto.run_once("dry-run",tx,NOW,state_dir=tmp_path/"state",notify=notices.append,scan_clock=lambda:0,scan_sleep=lambda _s:None)
 assert result=={"mode":"dry-run","status":"degraded","backoff":30}
 assert not (tmp_path/"state").exists() and not [c for c in tx.calls if c[4] or c[0]=="POST"]
 assert len(notices)==1 and "SECRET" not in notices[0]


def test_daemon_backoff_grows_then_success_resets():
 assert [auto.next_cycle_delay(x,15) for x in (0,1,2,3,0)]==[15,30,60,120,15]


def test_scan_wall_clock_rejects_mid_scan_stale_response_and_next_cycle_survives(tmp_path):
 responses=scan_responses(); wall=[NOW]; base=responses["/v5/market/kline"]
 def advancing(params):
  wall[0]+=4000
  return base(params)
 responses["/v5/market/kline"]=advancing
 with pytest.raises(auto.FailClosed):
  auto.run_once("dry-run",FakeTransport(responses),NOW,state_dir=tmp_path/"state",scan_clock=lambda:0,scan_sleep=lambda _s:None,wall_clock=lambda:wall[0])
 assert not (tmp_path/"state").exists()
 wall[0]=NOW
 second=auto.run_once("dry-run",FakeTransport(scan_responses()),NOW,state_dir=tmp_path/"state",scan_clock=lambda:0,scan_sleep=lambda _s:None,wall_clock=lambda:wall[0])
 assert second["mode"]=="dry-run" and "plan" in second


def test_scan_plan_ttl_uses_completion_wall_time():
 responses=scan_responses(); wall=[NOW]
 def advancing(params):
  wall[0]+=10
  return scan_responses()["/v5/market/kline"](params) | {"_response_ts":wall[0]}
 responses["/v5/market/kline"]=advancing
 runtime=auto.ScanRuntime(auto.Executor.Runtime(FakeTransport(responses),"dry-run"),clock=lambda:0,sleep=lambda _s:None,min_interval=0,wall_clock=lambda:wall[0])
 plan=auto.scan_market(runtime,NOW,"completion")
 assert auto.parse_z(plan["generated_at"])==wall[0]
 assert auto.parse_z(plan["expires_at"])==wall[0]+auto.SETUP_TTL_SECONDS


def test_response_timestamp_future_skew_rejected():
 with pytest.raises(auto.FailClosed,match="stale or ambiguous API"):
  auto.require_fresh({"_response_ts":NOW+3},NOW,30)


@pytest.mark.parametrize("error",[urllib.error.URLError("down"),TimeoutError(),ConnectionError()])
def test_public_network_errors_retry_then_degrade(error,tmp_path):
 responses=scan_responses(); attempts=[]
 def fail(_params): attempts.append(1); raise error
 responses["/v5/market/instruments-info"]=fail
 result=auto.run_once("dry-run",FakeTransport(responses),NOW,state_dir=tmp_path/"state",scan_clock=lambda:0,scan_sleep=lambda _s:None)
 assert result["status"]=="degraded" and len(attempts)==3 and not (tmp_path/"state").exists()


@pytest.mark.parametrize("timestamp",[None,NOW-31,NOW+3,"bad"])
def test_instruments_freshness_fail_closed_zero_mutation(timestamp,tmp_path):
 responses=scan_responses(); instruments=responses["/v5/market/instruments-info"]
 if timestamp is None: instruments.pop("_response_ts")
 else: instruments["_response_ts"]=timestamp
 state=tmp_path/"state"; state.mkdir(); marker=state/"marker"; marker.write_bytes(b"unchanged")
 with pytest.raises(auto.FailClosed): auto.run_once("dry-run",FakeTransport(responses),NOW,state_dir=state,wall_clock=lambda:NOW)
 assert marker.read_bytes()==b"unchanged" and list(state.iterdir())==[marker]


@pytest.mark.parametrize("endpoint",["/v5/position/list","/v5/order/realtime"])
@pytest.mark.parametrize("timestamp",[NOW-31,NOW+3,"bad"])
def test_private_scan_freshness_fail_closed_partial_state_untouched(endpoint,timestamp,tmp_path):
 responses=scan_responses(); responses[endpoint]["_response_ts"]=timestamp
 state=tmp_path/"state"; state.mkdir(); marker=state/"marker"; marker.write_bytes(b"unchanged")
 with pytest.raises(auto.FailClosed): auto.run_once("dry-run",FakeTransport(responses),NOW,state_dir=state,wall_clock=lambda:NOW)
 assert marker.read_bytes()==b"unchanged" and list(state.iterdir())==[marker]


@pytest.mark.parametrize("code",[429,503])
def test_public_http_transient_retries_with_clamped_retry_after(code):
 responses=scan_responses(); attempts=[]; sleeps=[]
 def flaky(_params):
  attempts.append(1)
  if len(attempts)==1: raise urllib.error.HTTPError("https://public",code,"busy",{"Retry-After":"999"},None)
  return scan_responses()["/v5/market/instruments-info"]
 responses["/v5/market/instruments-info"]=flaky
 runtime=auto.ScanRuntime(auto.Executor.Runtime(FakeTransport(responses),"dry-run"),clock=lambda:0,sleep=sleeps.append,min_interval=0,wall_clock=lambda:NOW)
 assert auto.scan_market(runtime,NOW,"http")["schema"]=="nexus.basket-plan"
 assert len(attempts)==2 and sleeps==[4.0]


def test_public_http_400_not_retried_and_degrades_safely(tmp_path):
 responses=scan_responses(); attempts=[]
 def bad(_params):
  attempts.append(1); raise urllib.error.HTTPError("https://public",400,"bad",{},None)
 responses["/v5/market/instruments-info"]=bad
 result=auto.run_once("dry-run",FakeTransport(responses),NOW,state_dir=tmp_path/"state",scan_clock=lambda:0,scan_sleep=lambda _s:None)
 assert result["status"]=="degraded" and len(attempts)==1 and not (tmp_path/"state").exists()


def test_scan_rejects_missing_required_metadata_and_market_data():
 responses=scan_responses(); responses["/v5/market/instruments-info"]["list"][0].pop("launchTime")
 assert "AAAUSDT" not in {p["symbol"] for p in auto.scan_market(auto.Executor.Runtime(FakeTransport(responses),"dry-run"),NOW,"x")["plans"]}

def test_live_gate_requires_env_digest_and_exact_executor(tmp_path,monkeypatch):
 path=tmp_path/"policy"; auto.write_policy(path,policy()); loaded=auto.load_policy(path,NOW)
 monkeypatch.setenv("AUTONOMOUS_BASKET_LIVE","YES"); monkeypatch.setenv("AUTONOMOUS_BASKET_POLICY_SHA256",loaded.digest)
 assert auto.require_live_gate(loaded,NOW).digest==loaded.digest
 monkeypatch.setenv("AUTONOMOUS_BASKET_POLICY_SHA256","0"*64)
 with pytest.raises(auto.FailClosed,match="digest"): auto.require_live_gate(loaded,NOW)

def test_dry_run_once_has_no_files_or_posts(tmp_path):
 tx=FakeTransport(scan_responses())
 result=auto.run_once("dry-run",tx,NOW,policy_path=tmp_path/"missing",state_dir=tmp_path/"state")
 assert result["mode"]=="dry-run" and not (tmp_path/"state").exists()
 assert not [c for c in tx.calls if c[0]=="POST"]

def test_exit_write_durable_owned_and_manual_untouched(tmp_path):
 writes=[]; runtime=auto.Executor.Runtime(lambda path,params=None,method="GET",body=None,private=False:writes.append((path,body)) or {},"live")
 store=auto.StateStore(tmp_path/"intent"); store.data={"state":"OPEN"}
 position={"symbol":"AAAUSDT","size":"1","side":"Buy","positionIdx":0,"orderLinkId":"owned","stopLoss":"90","takeProfit":"120"}; action={"kind":"close","reduceOnly":True,"closeOnTrigger":True}
 auto.execute_exit(runtime,store,position,"owned",action,NOW)
 assert json.loads((tmp_path/"intent").read_text())["closeBody"]["reduceOnly"] is True and writes[-1][0]=="/v5/order/create"
 with pytest.raises(auto.FailClosed): auto.execute_exit(runtime,store,dict(position,orderLinkId="manual"),"owned",action,NOW)

def test_live_recovery_runs_before_any_scan(tmp_path,monkeypatch):
 state=tmp_path/"state"; state.mkdir(); executor=state/"executor.json"
 executor.write_text(json.dumps({"status":"reconciling","planDocument":{"plans":[]}}))
 tx=FakeTransport({}); seen=[]
 monkeypatch.setattr(auto,"_recover_executor",lambda runtime,path,now,notify: seen.append("recover") or {"status":"reconciling"})
 assert auto.run_once("live",tx,NOW,state_dir=state)=={"mode":"live","status":"reconciling"}
 assert seen==["recover"] and tx.calls==[]

def test_closed_m5_touch_then_strict_later_trigger_without_manual_state():
 generated=auto.parse_z("2026-08-11T16:47:54Z"); floor=auto.m5_causal_floor(generated)
 row={"symbol":"AAAUSDT","side":"Buy","zone":["99","101"],"trigger":"101","invalid":"99","stop":"98","tp":"105"}
 monitor={"plan":{"generated_at":"2026-08-11T16:47:54Z","plans":[row]},"touches":{"AAAUSDT":None}}
 historical=[{"ts":floor-i*300,"low":100,"high":102,"close":102,"closed":True} for i in range(1,6)]
 assert auto.observe_trigger(monitor,{"AAAUSDT":historical},{"AAAUSDT":False},floor+300) is None
 assert monitor["touches"]["AAAUSDT"] is None
 touch={"ts":floor,"low":100,"high":102,"close":100.5,"closed":True}
 forming={"ts":floor+300,"low":101,"high":103,"close":102,"closed":True}
 assert auto.observe_trigger(monitor,{"AAAUSDT":[touch,forming]},{"AAAUSDT":False},floor+599) is None
 assert monitor["touches"]["AAAUSDT"]==floor and not monitor["triggerTs"]
 assert auto.observe_trigger(monitor,{"AAAUSDT":[touch,forming]},{"AAAUSDT":False},floor+600)=="AAAUSDT"
 assert monitor["triggerTs"]["AAAUSDT"]==floor+300

def test_earliest_trigger_timestamp_wins_and_persists():
 rows=[{"symbol":s,"side":"Buy","zone":["9","10"],"trigger":"10","invalid":"9","stop":"8","tp":"12"} for s in ("AAAUSDT","ZZZUSDT")]
 monitor={"plan":{"generated_at":"1970-01-01T00:00:00Z","plans":rows},"touches":{"AAAUSDT":100,"ZZZUSDT":100}}
 candles={"AAAUSDT":[{"ts":300,"low":10,"high":11,"close":11,"closed":True}],"ZZZUSDT":[{"ts":200,"low":10,"high":11,"close":11,"closed":True}]}
 assert auto.observe_trigger(monitor,candles,{"AAAUSDT":False,"ZZZUSDT":False},600)=="ZZZUSDT"
 assert monitor["triggerTs"]["ZZZUSDT"]==200

def test_restart_poisoned_preplan_observations_clear_before_selection():
 row={"symbol":"AAAUSDT","side":"Buy","zone":["99","101"],"trigger":"101","invalid":"99","stop":"98","tp":"105"}
 monitor={"plan":{"generated_at":"2026-08-11T16:47:54Z","plans":[row]},"touches":{"AAAUSDT":1786466100},"triggerTs":{"AAAUSDT":1786466400},"submitted":False,"winner":"AAAUSDT","identity":"stale","winnerPlan":{},"disarmed":["OTHER"]}
 floor=auto.sanitize_trigger_observations(monitor)
 assert floor==auto.parse_z("2026-08-11T16:50:00Z")
 assert monitor["touches"]=={"AAAUSDT":None} and monitor["triggerTs"]=={}
 assert all(key not in monitor for key in ("winner","identity","winnerPlan","disarmed"))

def test_poisoned_reserved_monitor_fails_closed():
 row={"symbol":"AAAUSDT","side":"Buy","zone":["99","101"],"trigger":"101","invalid":"99","stop":"98","tp":"105"}
 monitor={"plan":{"generated_at":"2026-08-11T16:47:54Z","plans":[row]},"touches":{"AAAUSDT":1},"submitted":True,"reservation":{"identity":"x"}}
 with pytest.raises(auto.FailClosed,match="causally invalid reserved"):
  auto.sanitize_trigger_observations(monitor)

def test_scan_geometry_uses_real_ohlcv_and_rejects_nonpositive_stop():
 rows=[[str(i),"1",str(h),str(l),str(c),str(v)] for i,(h,l,c,v) in enumerate([(1.1,.9,1,10),(1.2,.95,1.1,20),(1.3,1,1.2,30),(1.4,1.1,1.3,40),(1.5,1.2,1.4,100)])]
 row=auto._scan_row("XUSDT",rows,20_000_000,110,100)
 assert row and float(row["stop"])>0 and row["market"]["volumeRatio"]>1
 tiny=[[str(i),".01",".02",".001",".01","1"] for i in range(5)]
 assert auto._scan_row("TINYUSDT",tiny,20_000_000,110,100) is None

def test_reversal_confirmation_uses_closed_candle_volume_baseline():
 assert auto.volume_confirmed([10,10,10,16]) is True
 assert auto.volume_confirmed([10,10,10,14]) is False

def test_freshness_prefers_executor_production_key():
 assert auto.require_fresh({"_response_ts":NOW},NOW,30)["_response_ts"]==NOW
 assert auto.require_fresh({"_response_ts":NOW+1},NOW,30)["_response_ts"]==NOW+1
 with pytest.raises(auto.FailClosed): auto.require_fresh({"response_ts":NOW,"_response_ts":NOW-99},NOW,30)
 with pytest.raises(auto.FailClosed): auto.require_fresh({"_response_ts":NOW+31},NOW,30)


def test_production_api_attaches_local_receive_time_not_exchange_time(monkeypatch):
 class Response:
  def __enter__(self): return self
  def __exit__(self,*_): pass
  def read(self): return json.dumps({"retCode":0,"time":1,"result":{"list":[]}}).encode()
 monkeypatch.setattr(auto.Executor.urllib.request,"urlopen",lambda *_a,**_k:Response())
 monkeypatch.setattr(auto.Executor.time,"time",lambda:NOW)
 result=auto.Executor.api("/v5/market/tickers",{"category":"linear"})
 assert result=={"list":[],"_response_ts":NOW}


def open_files(tmp_path, *, stop="90"):
 state=tmp_path/"state"; state.mkdir(); link="basket-a1723291200-AAAUSDT"
 body={"category":"linear","symbol":"AAAUSDT","side":"Buy","orderType":"Limit","qty":"1","price":"100","timeInForce":"PostOnly","positionIdx":0,"takeProfit":"120","stopLoss":"90","tpTriggerBy":"MarkPrice","slTriggerBy":"MarkPrice","orderLinkId":link}
 (state/"executor.json").write_text(json.dumps({"status":"filled","winner":"AAAUSDT","orderLinkId":link,"orderId":"entry-1","body":body}))
 (state/"ledger.json").write_text(json.dumps({"day":"2026-08-10","submissions":1,"modeled_loss_reserved":"2","loss_streak":0,"open_basket":link,"trades":{link:{"status":"reserved","modeled_loss":"2"}}}))
 return state,body


def open_transport(body, *, position=True, manual=False, close=False, fail_write=False):
 calls=[]; applied_stop=None
 def tx(path,params=None,method="GET",body_arg=None,body=None,private=False):
  nonlocal applied_stop
  payload=body_arg or body; calls.append((method,path,params,payload,private))
  if method=="POST":
   if fail_write: raise TimeoutError("ambiguous")
   if path=="/v5/position/trading-stop": applied_stop=payload["stopLoss"]
   return {"orderId":"exit-1"}
  if path=="/v5/position/list":
   rows=[]
   if position: rows=[{"symbol":"MANUALUSDT" if manual else "AAAUSDT","size":"1","side":"Buy","positionIdx":0,"entryPrice":"100","stopLoss":applied_stop or "90","takeProfit":"120"}]
   return {"list":rows,"_response_ts":NOW}
  if path=="/v5/order/realtime":
   link=(params or {}).get("orderLinkId")
   if link and link.startswith("autoclose-"): return {"list":[{"orderLinkId":link,"symbol":"AAAUSDT","side":"Sell","positionIdx":0,"qty":"1","orderType":"Market","reduceOnly":True,"orderStatus":"Filled"}],"_response_ts":NOW}
   return {"list":[{"orderLinkId":body0["orderLinkId"],"symbol":"AAAUSDT","side":"Buy","positionIdx":0,"qty":"1","price":"100","orderType":"Limit","orderStatus":"Filled","cumExecQty":"1"}],"_response_ts":NOW}
  if path=="/v5/market/kline":
   end=int(NOW//300*300-300); closes=[100,105,111]
   return {"list":[[str((end-i*300)*1000),str(closes[2-i]),str(closes[2-i]+2),str(closes[2-i]-2),str(closes[2-i]),"1000"] for i in range(3)],"_response_ts":NOW}
  if path=="/v5/market/open-interest": return {"list":[{"timestamp":str(int((NOW-3600)*1000)),"openInterest":"100"},{"timestamp":str(int(NOW*1000)),"openInterest":"110"}],"_response_ts":NOW}
  if path=="/v5/market/tickers": return {"list":[{"turnover24h":"20000000"}],"_response_ts":NOW}
  if path=="/v5/position/closed-pnl": return {"list":[{"orderId":"entry-1","closedPnl":"1.25"}],"_response_ts":NOW}
  raise AssertionError(path)
 body0=body
 return tx,calls


def enable_live(tmp_path,monkeypatch):
 path=tmp_path/"policy.json"; auto.write_policy(path,policy()); loaded=auto.load_policy(path,NOW)
 monkeypatch.setenv("AUTONOMOUS_BASKET_LIVE","YES"); monkeypatch.setenv("AUTONOMOUS_BASKET_POLICY_SHA256",loaded.digest)
 return path


def test_open_lifecycle_tightens_breakeven_before_scan(tmp_path,monkeypatch):
 state,body=open_files(tmp_path); tx,calls=open_transport(body)
 result=auto.run_once("live",tx,NOW,policy_path=enable_live(tmp_path,monkeypatch),state_dir=state)
 posts=[x for x in calls if x[0]=="POST"]
 assert result["status"]=="open" and posts[0][1]=="/v5/position/trading-stop"
 assert float(posts[0][3]["stopLoss"])>90
 assert not [x for x in calls if x[1]=="/v5/market/instruments-info"]
 assert json.loads((state/"executor.json").read_text())["exitIntent"]["kind"]=="stop"


def test_open_lifecycle_unrelated_manual_position_allows_owned_stop_and_no_scan(tmp_path,monkeypatch):
 state,body=open_files(tmp_path); tx,calls=open_transport(body); base=tx
 def coexist(path,params=None,method="GET",body=None,private=False):
  result=base(path,params,method,body,private)
  if method=="GET" and path=="/v5/position/list": result["list"] += [{"symbol":f"MANUAL{i}USDT","size":"2","side":"Buy","positionIdx":0} for i in range(10)]
  if method=="GET" and path=="/v5/order/realtime" and not (params or {}).get("orderLinkId"): result["list"] += [{"symbol":f"ORDER{i}USDT","orderLinkId":f"manual-{i}","orderStatus":"New"} for i in range(20)]
  return result
 result=auto.run_once("live",coexist,NOW,policy_path=enable_live(tmp_path,monkeypatch),state_dir=state)
 assert result["action"]=="stop" and [x for x in calls if x[0]=="POST"][-1][3]["symbol"]=="AAAUSDT"
 assert not [x for x in calls if x[1]=="/v5/market/instruments-info"]


def test_open_lifecycle_at_two_r_writes_tighter_trailing_stop(tmp_path,monkeypatch):
 state,body=open_files(tmp_path)
 tx,calls=open_transport(body)
 base=tx
 def trailing(path,params=None,method="GET",body=None,private=False):
  if path=="/v5/market/kline":
   end=int(NOW//300*300-300); closes=[100,110,121]
   return {"list":[[str((end-i*300)*1000),str(closes[2-i]),str(closes[2-i]+2),str(closes[2-i]-2),str(closes[2-i]),"1000"] for i in range(3)],"_response_ts":NOW}
  return base(path,params,method,body,private)
 result=auto.run_once("live",trailing,NOW,policy_path=enable_live(tmp_path,monkeypatch),state_dir=state)
 posts=[x for x in calls if x[0]=="POST"]
 assert result["action"]=="stop" and posts[-1][1]=="/v5/position/trading-stop"
 assert float(posts[-1][3]["stopLoss"])>90


def test_exit_timeout_restart_exact_close_pnl_once(tmp_path):
 state,body=open_files(tmp_path); notices=[]; post_count=0; phase="write"
 close_link=("autoclose-"+body["orderLinkId"])[-36:]
 def tx(path,params=None,method="GET",body=None,private=False):
  nonlocal post_count,phase
  if method=="POST": post_count+=1; raise TimeoutError("unknown")
  if path=="/v5/order/realtime":
   return {"list":[] if phase=="write" else [{"orderId":"close-1","orderLinkId":close_link,"symbol":"AAAUSDT","side":"Sell","positionIdx":0,"qty":"1","orderType":"Market","reduceOnly":True,"orderStatus":"Filled"}],"_response_ts":NOW}
  if path=="/v5/position/list":
   rows=[] if phase=="filled" else [{"symbol":"AAAUSDT","size":"1","side":"Buy","positionIdx":0,"entryPrice":"100","stopLoss":"90","takeProfit":"120"}]
   return {"list":rows,"_response_ts":NOW}
  if path=="/v5/position/closed-pnl": return {"list":[{"orderId":"close-1","closedPnl":"-1.25"}],"_response_ts":NOW}
  raise AssertionError(path)
 runtime=auto.Executor.Runtime(tx,"live"); durable=auto.Executor.load_state(state/"executor.json")
 position=tx("/v5/position/list")["list"][0]
 with pytest.raises(TimeoutError): auto.execute_exit(runtime,auto.StateStore(state/"executor.json"),position,durable["orderLinkId"],{"kind":"close"},NOW)
 saved=json.loads((state/"executor.json").read_text())
 assert saved["status"]=="exit_reconciling" and saved["closeOrderLinkId"]==close_link and post_count==1
 phase="pending"
 with pytest.raises(auto.Executor.AmbiguousOrder): auto.run_once("live",tx,NOW,state_dir=state,notify=notices.append)
 assert post_count==1
 phase="filled"; result=auto.run_once("live",tx,NOW,state_dir=state,notify=notices.append)
 assert result=={"mode":"live","status":"closed","pnl":"-1.25"} and post_count==1
 assert json.loads((state/"ledger.json").read_text())["open_basket"] is None and notices==["closed:AAAUSDT:-1.25"]
 assert auto.run_once("live",tx,NOW,state_dir=state,notify=notices.append)["status"]=="closed" and notices==["closed:AAAUSDT:-1.25"]


class ChronologicalLiveTransport:
 def __init__(self,state_dir,exposure=None):
  self.state_dir=state_dir; self.exposure=exposure; self.phase=0; self.calls=[]; self.posts=[]; self.now=NOW; self.winner="AAAUSDT"; self.created=None
 def _flat(self,symbol): return [{"symbol":symbol,"size":"0","side":"","positionIdx":0}]
 def _candles(self,symbol,minutes):
  end=int(self.now//(minutes*60)*(minutes*60))
  if end+minutes*60>self.now: end-=minutes*60
  if symbol=="BTCUSDT": end=(int(self.now)-1)//3600*3600; closes=[30000,30001,30002,30003,30004]
  elif minutes in (15,60):
   end=int(self.now-minutes*60)
   closes=[96,97,98,99,100] if symbol=="AAAUSDT" else [196,197,198,199,200]
  else:
   base=100 if symbol=="AAAUSDT" else 200
   closes=[base-10,base-9,base-8,base-7,base] if self.phase==0 else [base-8,base-7,base,base+3,base+4]
  return [[str((end-(len(closes)-1-i)*minutes*60)*1000),str(v),str(v+2),str(v-2),str(v),"1000"] for i,v in enumerate(closes)]
 def __call__(self,path,params=None,method="GET",body=None,private=False):
  params=params or {}; self.calls.append((method,path,dict(params),body,private))
  if method=="POST":
   self.posts.append((path,json.loads(json.dumps(body))))
   if path!="/v5/order/create": raise AssertionError("unexpected write "+path)
   executor=json.loads((self.state_dir/"executor.json").read_text())
   assert executor["winner"]==self.winner and executor["body"]==body
   assert all(x["symbol"]==self.winner for x in executor["planDocument"]["plans"])
   monitor=json.loads((self.state_dir/"monitor.json").read_text()) if (self.state_dir/"monitor.json").exists() else {}
   if monitor:
    ledger=json.loads((self.state_dir/"ledger.json").read_text())
    assert ledger["open_basket"]==monitor["identity"] and monitor["winner"]==self.winner
    assert monitor["winnerPlan"]["plans"]==[next(x for x in monitor["plan"]["plans"] if x["symbol"]==self.winner)]
   if monitor.get("policy_snapshot") is not None:
    assert monitor["policy_digest"]==auto.canonical_hash(monitor["policy_snapshot"])
    assert executor["policy_snapshot"]==monitor["policy_snapshot"]
    assert executor["policy_digest"]==monitor["policy_digest"]
   self.created=json.loads(json.dumps(body)); return {"orderId":"entry-1"}
  if path=="/v5/market/instruments-info":
   symbol=params.get("symbol")
   if symbol: return {"list":[{"symbol":symbol,"priceFilter":{"tickSize":"0.001" if symbol=="DOGEUSDT" else "0.1"},"lotSizeFilter":{"qtyStep":"0.001","minOrderQty":"0.001","minNotionalValue":"1"}}],"_response_ts":self.now}
   return {"list":[{"symbol":s,"status":"Trading","contractType":"LinearPerpetual","launchTime":str(int((self.now-91*86400)*1000))} for s in ("AAAUSDT","BBBUSDT")],"_response_ts":self.now}
  if path=="/v5/market/tickers":
   symbols=[params["symbol"]] if params.get("symbol") else ["AAAUSDT","BBBUSDT"]
   return {"list":[{"symbol":s,"turnover24h":"20000000" if s=="AAAUSDT" else "15000000","bid1Price":"100" if s=="AAAUSDT" else "200","ask1Price":"100.05" if s=="AAAUSDT" else "200.05","lastPrice":"100" if s=="AAAUSDT" else "200","fundingRate":"0"} for s in symbols],"_response_ts":self.now}
  if path=="/v5/market/kline": return {"list":self._candles(params["symbol"],int(params["interval"])),"_response_ts":self.now}
  if path=="/v5/market/open-interest": return {"list":[{"timestamp":str(int((self.now-3600)*1000)),"openInterest":"100"},{"timestamp":str(int(self.now*1000)),"openInterest":"101"}],"_response_ts":self.now}
  if path=="/v5/account/wallet-balance": return {"list":[{"totalAvailableBalance":"1000"}],"_response_ts":self.now}
  if path=="/v5/position/list":
   symbol=params.get("symbol")
   if self.exposure and self.exposure(self,"position",symbol): return {"list":[{"symbol":symbol or self.winner,"size":"1","side":"Buy","positionIdx":0,"entryPrice":"100","stopLoss":"98","takeProfit":"108"}],"_response_ts":self.now}
   if self.created and symbol==self.winner: return {"list":[{"symbol":self.winner,"size":self.created["qty"],"side":"Buy","positionIdx":0,"entryPrice":self.created["price"],"stopLoss":self.created["stopLoss"],"takeProfit":self.created["takeProfit"]}],"_response_ts":self.now}
   return {"list":self._flat(symbol or self.winner),"_response_ts":self.now}
  if path=="/v5/order/realtime":
   symbol=params.get("symbol")
   if self.exposure and self.exposure(self,"order",symbol): return {"list":[{"symbol":symbol or self.winner,"orderStatus":"New","orderLinkId":"manual"}],"_response_ts":self.now}
   if self.created and params.get("orderLinkId")==self.created["orderLinkId"]: return {"list":[{**self.created,"orderId":"entry-1","orderStatus":"Filled","cumExecQty":self.created["qty"],"leavesQty":"0"}],"_response_ts":self.now}
   return {"list":[],"_response_ts":self.now}
  raise AssertionError((path,params))


def test_real_run_once_chronological_winner_is_durable_before_single_create(tmp_path,monkeypatch):
 state=tmp_path/"state"; tx=ChronologicalLiveTransport(state); policy_path=enable_live(tmp_path,monkeypatch)
 first=auto.run_once("live",tx,NOW,policy_path=policy_path,state_dir=state)
 initial=json.loads((state/"monitor.json").read_text())
 assert first["status"]=="watching" and {x["symbol"] for x in initial["plan"]["plans"]}=={"AAAUSDT","BBBUSDT"}
 assert not tx.posts
 tx.phase=1; tx.now=NOW+600
 result=auto.run_once("live",tx,NOW+600,policy_path=policy_path,state_dir=state)
 assert result=={"mode":"live","status":"filled","candidate":"AAAUSDT"}
 assert len(tx.posts)==1 and tx.posts[0][0]=="/v5/order/create"
 body=tx.posts[0][1]
 assert body["symbol"]=="AAAUSDT" and body["orderType"]=="Limit" and body["timeInForce"]=="PostOnly"
 assert body["takeProfit"] and body["stopLoss"]
 plan=auto.Executor.validate_plan_document(json.loads((state/"monitor.json").read_text())["winnerPlan"],NOW+600)[1]["AAAUSDT"]
 assert auto.Executor.modeled_loss(plan,float(body["qty"]),float(body["price"]),float(body["stopLoss"]))<=2
 monitor=json.loads((state/"monitor.json").read_text())
 assert monitor["disarmed"]==["BBBUSDT"] and "BBBUSDT" not in json.dumps(json.loads((state/"executor.json").read_text())["body"])


def _arm_reservation_run(tmp_path,monkeypatch,fault):
 state=tmp_path/"state"; tx=ChronologicalLiveTransport(state); policy_path=enable_live(tmp_path,monkeypatch)
 assert auto.run_once("live",tx,NOW,policy_path=policy_path,state_dir=state,wall_clock=lambda:tx.now)["status"]=="watching"
 tx.phase=1; tx.now=NOW+600; monkeypatch.setattr(auto,"_reservation_fault",fault)
 with pytest.raises(RuntimeError,match="injected "+fault): auto.run_once("live",tx,tx.now,policy_path=policy_path,state_dir=state,wall_clock=lambda:tx.now)
 assert not tx.posts and (state/"reservation_txn.json").exists()
 return state,tx,policy_path


@pytest.mark.parametrize("fault",["after_journal","after_ledger","after_monitor","after_executor","after_commit"])
def test_b4_public_run_once_reservation_fault_recovers_exactly_once(tmp_path,monkeypatch,fault):
 state,tx,policy_path=_arm_reservation_run(tmp_path,monkeypatch,fault)
 sealed=json.loads((state/"reservation_txn.json").read_text()); targets=sealed["targets"]; identity=sealed["identity"]
 monkeypatch.setattr(auto,"_reservation_fault",None)
 assert auto.run_once("live",tx,tx.now,policy_path=policy_path,state_dir=state,wall_clock=lambda:tx.now)=={"mode":"live","status":"filled","candidate":"AAAUSDT"}
 ledger=json.loads((state/"ledger.json").read_text()); monitor=json.loads((state/"monitor.json").read_text()); executor=json.loads((state/"executor.json").read_text())
 assert ledger==targets["ledger"] and monitor==targets["monitor"]
 assert executor["winner"]==monitor["winner"]=="AAAUSDT" and monitor["identity"]==ledger["open_basket"]==identity
 assert executor["orderLinkId"]==tx.posts[0][1]["orderLinkId"]
 assert ledger["submissions"]==1 and len(tx.posts)==1 and not (state/"reservation_txn.json").exists()
 auto.run_once("live",tx,tx.now,policy_path=policy_path,state_dir=state,wall_clock=lambda:tx.now)
 assert len(tx.posts)==1


def test_b4_public_run_once_tampered_reservation_seal_fails_closed(tmp_path,monkeypatch):
 state,tx,policy_path=_arm_reservation_run(tmp_path,monkeypatch,"after_journal")
 path=state/"reservation_txn.json"; sealed=json.loads(path.read_text()); sealed["identity"]="tampered"; auto.atomic_json(path,sealed)
 before={p.name:p.read_bytes() for p in state.glob("*.json")}; monkeypatch.setattr(auto,"_reservation_fault",None)
 with pytest.raises(auto.FailClosed,match="invalid reservation journal"): auto.run_once("live",tx,tx.now,policy_path=policy_path,state_dir=state,wall_clock=lambda:tx.now)
 assert not tx.posts and {p.name:p.read_bytes() for p in state.glob("*.json")}==before


def test_b4_public_run_once_impossible_mixed_reservation_fails_closed(tmp_path,monkeypatch):
 state,tx,policy_path=_arm_reservation_run(tmp_path,monkeypatch,"after_ledger")
 monitor=json.loads((state/"monitor.json").read_text()); monitor["submitted"]="impossible"; auto.atomic_json(state/"monitor.json",monitor)
 before={p.name:p.read_bytes() for p in state.glob("*.json")}; monkeypatch.setattr(auto,"_reservation_fault",None)
 with pytest.raises(auto.FailClosed,match="journal state mismatch"): auto.run_once("live",tx,tx.now,policy_path=policy_path,state_dir=state,wall_clock=lambda:tx.now)
 assert not tx.posts and {p.name:p.read_bytes() for p in state.glob("*.json")}==before


def test_b4_public_run_once_cross_midnight_keeps_open_reservation(tmp_path,monkeypatch):
 state,tx,policy_path=_arm_reservation_run(tmp_path,monkeypatch,"after_commit")
 identity=json.loads((state/"ledger.json").read_text())["open_basket"]
 tx.now=(int(tx.now//86400)+1)*86400+1; monkeypatch.setattr(auto,"_reservation_fault",None)
 def stop_before_exchange(**_): raise RuntimeError("stop after public recovery")
 monkeypatch.setattr(auto,"delegate_entry",stop_before_exchange)
 with pytest.raises(RuntimeError,match="stop after public recovery"): auto.run_once("live",tx,tx.now,policy_path=policy_path,state_dir=state,wall_clock=lambda:tx.now)
 ledger=json.loads((state/"ledger.json").read_text())
 assert not tx.posts and not (state/"reservation_txn.json").exists()
 assert ledger["open_basket"]==identity and ledger["trades"][identity]["status"]=="reserved" and ledger["submissions"]==1


def test_prewrite_rejection_repairs_with_response_time_not_cycle_time(tmp_path,monkeypatch):
 state=tmp_path/"state"; tx=ChronologicalLiveTransport(state); policy_path=enable_live(tmp_path,monkeypatch)
 assert auto.run_once("live",tx,NOW,policy_path=policy_path,state_dir=state,wall_clock=lambda:tx.now)["status"]=="watching"
 tx.phase=1; tx.now=NOW+600
 def reject(**kwargs):
  auto.Executor.save_state({"status":"fatal","exposure_possible":False,"errors":{"AAAUSDT":"winner current market invalid"}},state/"executor.json")
  tx.now=NOW+905
  raise auto.Executor.PrewriteRejected("winner current market invalid")
 monkeypatch.setattr(auto,"delegate_entry",reject)
 result=auto.run_once("live",tx,NOW+600,policy_path=policy_path,state_dir=state,wall_clock=lambda:tx.now)
 assert result=={"mode":"live","status":"rejected","candidate":"AAAUSDT"}
 assert not tx.posts and json.loads((state/"ledger.json").read_text())["open_basket"] is None


def test_exact_doge_separate_candle_handoff_does_not_require_second_trigger(tmp_path,monkeypatch):
 state=tmp_path/"state"; state.mkdir(); tx=ChronologicalLiveTransport(state); tx.winner="DOGEUSDT"; tx.now=1786400100
 row={"symbol":"DOGEUSDT","side":"Buy","zone":["0.20","0.21"],"trigger":"0.21","invalid":"0.20","stop":"0.19","tp":"0.25"}
 plan=auto.build_plan([{**row,"score":1}],tx.now-600,"scan-doge")
 # Orchestrator evidence came from distinct closed M5 candles; delegated snapshot need not trigger again.
 touch,trigger=1786399500,1786399800
 def snapshot(_runtime,symbol,now):
  assert symbol=="DOGEUSDT"
  return {"symbol":symbol,"price":.205,"bid":.205,"ask":.206,"ticker_ts":now,"response_ts":now,
          "m5_close":.205,"m5_ts":1786399800,"m15_close":.205,"m15_ts":1786399200,
          "oi_change":0,"oi_ts":now,"btc_change":0,"btc_ts":now,"funding":0,"spread":.01,"source_response_ts":now}
 approval=auto.Executor.make_approval("adoge",tx.now,ttl=300,plan_document=plan,source_hash=auto.EXECUTOR_SHA256)
 result=auto.Executor.run_live_session(auto.Executor.Runtime(tx,"live"),approval,approval["nonce"]+":"+approval["fingerprint"],tx.now,
  state_path=state/"executor.json",lock_path=state/"executor.lock",plan_document=plan,source_hash=auto.EXECUTOR_SHA256,
  snapshot_fetch=snapshot,winner_evidence={"symbol":"DOGEUSDT","touchTs":touch,"triggerTs":trigger,"triggerM15Ts":1786399200,"triggerM15Close":.205})
 assert result["status"]=="filled" and len(tx.posts)==1


def test_final_snapshot_uses_clock_sample_after_22_second_fetch(tmp_path):
 state=tmp_path/"state"; state.mkdir(); tx=ChronologicalLiveTransport(state); tx.winner="DOGEUSDT"; start=1786400100; tx.now=start
 row={"symbol":"DOGEUSDT","side":"Buy","zone":["0.20","0.21"],"trigger":"0.21","invalid":"0.20","stop":"0.19","tp":"0.25"}
 plan=auto.build_plan([{**row,"score":1}],start-600,"scan-clock")
 def snapshot(_runtime,symbol,_now):
  tx.now=start+22
  return {"symbol":symbol,"price":.205,"bid":.205,"ask":.206,"ticker_ts":tx.now,"response_ts":tx.now,"m5_close":.205,"m5_ts":start-300,"m15_close":.205,"m15_ts":start-900,"oi_change":0,"oi_ts":tx.now,"btc_change":0,"btc_ts":tx.now,"funding":0,"spread":.01,"source_response_ts":tx.now}
 approval=auto.Executor.make_approval("clock22",start,300,plan,auto.EXECUTOR_SHA256)
 evidence={"symbol":"DOGEUSDT","touchTs":start-600,"triggerTs":start-300,"triggerM15Ts":start-900,"triggerM15Close":.205}
 result=auto.Executor.run_live_session(auto.Executor.Runtime(tx,"live"),approval,approval["nonce"]+":"+approval["fingerprint"],start,state_path=state/"executor.json",lock_path=state/"lock",plan_document=plan,source_hash=auto.EXECUTOR_SHA256,snapshot_fetch=snapshot,winner_evidence=evidence,now_fn=lambda:tx.now)
 assert result["status"]=="filled"
 saved=auto.Executor.load_state(state/"executor.json")
 assert saved["finalObservations"]["ages"]["response_ts"]==0
 stale={**snapshot(None,"DOGEUSDT",start),"response_ts":start-39,"ticker_ts":start-39,"source_response_ts":start-39}
 assert auto.Executor.final_market_diagnostic(stale,start+22)["code"]=="FINAL_RESPONSE_STALE"
 expiring=auto.Executor.make_approval("expiry",start,300)
 with pytest.raises(auto.Executor.PermanentError,match="approval expired"):
  auto.Executor.validate_approval(expiring,start+300)


@pytest.mark.parametrize("evidence,m15,exposure,error",[
 ({"symbol":"DOGEUSDT","touchTs":1786399200,"triggerTs":1786399200},.205,False,"chronology"),
 ({"symbol":"DOGEUSDT","touchTs":1786398000,"triggerTs":1786398300},.205,False,"chronology"),
 ({"symbol":"BTCUSDT","touchTs":1786398900,"triggerTs":1786399200},.205,False,"symbol"),
 ({"symbol":"DOGEUSDT","touchTs":1786398900,"triggerTs":1786399200},.19,False,"market invalid"),
 ({"symbol":"DOGEUSDT","touchTs":1786398900,"triggerTs":1786399200},.205,True,"candidate exposure"),
])
def test_winner_handoff_rejects_bad_evidence_or_current_state(tmp_path,evidence,m15,exposure,error):
 state=tmp_path/"state"; state.mkdir(); tx=ChronologicalLiveTransport(state,lambda _tx,kind,symbol: exposure and kind=="position" and symbol is None); tx.winner="DOGEUSDT"; tx.now=1786400100
 row={"symbol":"DOGEUSDT","side":"Buy","zone":["0.20","0.21"],"trigger":"0.21","invalid":"0.20","stop":"0.19","tp":"0.25"}; plan=auto.build_plan([{**row,"score":1}],tx.now-600,"scan-doge")
 def snapshot(_runtime,symbol,now): return {"symbol":symbol,"price":.205,"bid":.205,"ask":.206,"ticker_ts":now,"response_ts":now,"m5_close":.205,"m5_ts":1786399500,"m15_close":m15,"m15_ts":1786398600,"oi_change":0,"oi_ts":now,"btc_change":0,"btc_ts":now,"funding":0,"spread":.01,"source_response_ts":now}
 evidence={**evidence,"triggerM15Ts":1786399200,"triggerM15Close":m15}
 approval=auto.Executor.make_approval("adoge",tx.now,300,plan,auto.EXECUTOR_SHA256)
 with pytest.raises(auto.Executor.PermanentError,match=error):
  auto.Executor.run_live_session(auto.Executor.Runtime(tx,"live"),approval,approval["nonce"]+":"+approval["fingerprint"],tx.now,state_path=state/"executor.json",lock_path=state/"lock",plan_document=plan,source_hash=auto.EXECUTOR_SHA256,snapshot_fetch=snapshot,winner_evidence=evidence)
 assert not tx.posts


def production_legacy_repair_files(tmp_path):
 state=tmp_path/"state"; state.mkdir(); now=1786399502; identity="auto-1786399502-DOGEUSDT"
 doge={"symbol":"DOGEUSDT","side":"Buy","zone":["0.20","0.21"],"trigger":"0.21","invalid":"0.20","stop":"0.19","tp":"0.25"}
 plan=auto.build_plan([{**doge,"score":1}],now,"scan-doge-legacy")
 approval=auto.Executor.make_approval("a1786399502",now,300,plan,auto.EXECUTOR_SHA256)
 executor=auto.Executor.new_state(None); executor.update(status="fatal",exposure_possible=False,approved=approval,errors={"unknown":"winner current market invalid"},planDocument=plan,planFingerprint=approval["fingerprint"],executorSourceSha256=auto.EXECUTOR_SHA256)
 monitor={"state":"watching","submitted":True,"winner":"DOGEUSDT","identity":identity,"winnerPlan":plan}
 ledger={"day":auto.utc_day(now),"submissions":1,"modeled_loss_reserved":"2","loss_streak":0,"open_basket":identity,"trades":{identity:{"status":"ambiguous","modeled_loss":"2"}}}
 for name,value in (("executor",executor),("monitor",monitor),("ledger",ledger)): auto.atomic_json(state/(name+".json"),value)
 clear=lambda path,params=None,method="GET",body=None,private=False:{"list":[],"_response_ts":now}
 return state,clear


def test_exact_production_legacy_prewrite_fatal_repairs(tmp_path):
 state,clear=production_legacy_repair_files(tmp_path)
 assert auto.repair_prewrite_fatal(auto.Executor.Runtime(clear,"live"),state,1786399502)=={"status":"repaired","identity":"auto-1786399502-DOGEUSDT"}
 saved=json.loads((state/"ledger.json").read_text())
 assert saved["open_basket"] is None and saved["trades"]["auto-1786399502-DOGEUSDT"]["status"]=="failed_prewrite"


def test_exact_current_basket_prewrite_fatal_recovers_through_public_cycle(tmp_path,monkeypatch):
 state=tmp_path/"state"; state.mkdir(); issued=1786630501; identity="basket-1786630501-MSFTUSDT"
 row={"symbol":"MSFTUSDT","side":"Buy","zone":["400","410"],"trigger":"410","invalid":"400","stop":"395","tp":"430"}
 plan=auto.build_plan([{**row,"score":1}],issued,"exact-msft-prewrite-fatal")
 approval=auto.Executor.make_approval("a1786630501",issued,300,plan,auto.EXECUTOR_SHA256)
 error="final market invalid: candle stale or invalid"
 executor=auto.Executor.new_state(approval,auto.Executor.validate_plan_document(plan,issued)[1],plan,approval["fingerprint"])
 executor.update(status="fatal",candidate="MSFTUSDT",winner="MSFTUSDT",exposure_possible=False,fatal_reason=error,
  errors={"MSFTUSDT":error},finalGate={"code":"FINAL_CANDLE_STALE_OR_INVALID","message":error},
  executorSourceSha256=auto.EXECUTOR_SHA256)
 monitor={"state":"watching","submitted":False,"winner":"MSFTUSDT","identity":identity,"winnerPlan":plan}
 ledger={"day":auto.utc_day(issued),"submissions":1,"modeled_loss_reserved":"2","loss_streak":0,
  "open_basket":identity,"trades":{identity:{"status":"reserved","modeled_loss":"2"}}}
 for name,value in (("executor",executor),("monitor",monitor),("ledger",ledger)): auto.atomic_json(state/(name+".json"),value)
 policy_path=enable_live(tmp_path,monkeypatch); writes=[]
 def clear(path,params=None,method="GET",body=None,private=False):
  if method!="GET": writes.append((path,body)); raise AssertionError("exchange write")
  return {"list":[],"_response_ts":NOW}
 result=auto.run_cycle("live",clear,NOW,policy_path=policy_path,state_dir=state,wall_clock=lambda:NOW)
 assert result["status"]=="repaired" and writes==[]
 ledger=json.loads((state/"ledger.json").read_text())
 assert ledger["open_basket"] is None and ledger["trades"]["basket-1786630501-MSFTUSDT"]["status"]=="failed_prewrite"


def test_durable_notifier_dedup_restart_and_failure_nonfatal(tmp_path):
 sent=[]; path=tmp_path/"notices.json"
 notice=auto.DurableNotifier(auto.Notifier(lambda text:sent.append(text),"t","c"),path)
 assert notice.send("fill:x",identity="x",event="fill") is True
 assert auto.DurableNotifier(auto.Notifier(lambda text:sent.append(text),"t","c"),path).send("fill:x",identity="x",event="fill") is True
 assert sent==["fill:x"]
 assert auto.DurableNotifier(auto.Notifier(lambda _text:(_ for _ in ()).throw(OSError("down")),"t","c"),path).send("close:y",identity="y",event="close") is False


def test_durable_notifier_failed_delivery_retries_and_only_then_journals(tmp_path):
 path=tmp_path/"notices.json"; attempts=[]
 def flaky(text):
  attempts.append(text)
  if len(attempts)==1: raise OSError("down")
 notice=auto.DurableNotifier(auto.Notifier(flaky,"t","c"),path)
 assert notice.send("degraded: TIMEOUT exact message",identity="TIMEOUT",event="degraded") is False
 assert not path.exists()
 assert auto.DurableNotifier(auto.Notifier(flaky,"t","c"),path).send("degraded: TIMEOUT exact message",identity="TIMEOUT",event="degraded") is True
 assert len(attempts)==2 and json.loads(path.read_text())["sent"]==["degraded:TIMEOUT"]


def test_exact_eden_final_oi_prewrite_fatal_recovers_through_public_cycle(tmp_path,monkeypatch):
 state=tmp_path/"state"; state.mkdir(); issued=1786706120; identity="basket-1786706120-EDENUSDT"
 row={"symbol":"EDENUSDT","side":"Sell","zone":["0.05722","0.06064"],"trigger":"0.05722","invalid":"0.06064","stop":"0.06235","tp":"0.05038"}
 plan=auto.build_plan([{**row,"score":1}],issued-1339,"scan-1786704786")
 approval=auto.Executor.make_approval("a1786706120",issued,300,plan,auto.EXECUTOR_SHA256); error="final OI change below minimum"
 executor=auto.Executor.new_state(approval,auto.Executor.validate_plan_document(plan,issued)[1],plan,approval["fingerprint"])
 executor.update(status="fatal",candidate="EDENUSDT",winner="EDENUSDT",exposure_possible=False,fatal_reason=error,errors={"EDENUSDT":error},finalGate={"code":"FINAL_OI_CHANGE_BELOW_MIN","message":error},executorSourceSha256=auto.EXECUTOR_SHA256)
 monitor={"state":"watching","submitted":True,"winner":"EDENUSDT","identity":identity,"winnerPlan":plan,"plan":plan,"disarmed":["HYPEUSDT","ADAUSDT","CLUSDT","DOGEUSDT"]}
 ledger={"day":auto.utc_day(issued),"submissions":1,"modeled_loss_reserved":"2.0","loss_streak":0,"open_basket":identity,"trades":{identity:{"status":"reserved","modeled_loss":"2"}}}
 for name,value in (("executor",executor),("monitor",monitor),("ledger",ledger)): auto.atomic_json(state/(name+".json"),value)
 policy_path=enable_live(tmp_path,monkeypatch); calls=[]
 def clear(path,params=None,method="GET",body=None,private=False):
  calls.append((path,method)); assert method=="GET"; return {"list":[],"_response_ts":NOW}
 result=auto.run_cycle("live",clear,NOW,policy_path=policy_path,state_dir=state,wall_clock=lambda:NOW)
 assert result=={"mode":"live","status":"repaired","identity":identity}
 assert calls==[("/v5/position/list","GET"),("/v5/order/realtime","GET")]
 saved={name:json.loads((state/(name+".json")).read_text()) for name in ("executor","monitor","ledger")}
 assert saved["executor"]["status"]=="failed_prewrite_repaired"
 assert saved["monitor"]["submitted"] is False and saved["monitor"]["disarmed"]==monitor["disarmed"]
 assert saved["ledger"]["open_basket"] is None and saved["ledger"]["submissions"]==0 and saved["ledger"]["modeled_loss_reserved"]=="0.0"
 assert saved["ledger"]["trades"][identity]["status"]=="failed_prewrite" and not (state/"prewrite_repair.json").exists()


def production_eth_legacy_repair_files(tmp_path):
 state=tmp_path/"state"; state.mkdir(); now=1786442424; identity="auto-1786442424-ETHUSDT"
 eth={"symbol":"ETHUSDT","side":"Buy","zone":["4280","4300"],"trigger":"4300","invalid":"4280","stop":"4260","tp":"4380"}
 plan=auto.build_plan([{**eth,"score":1}],now,"scan-eth-legacy")
 approval=auto.Executor.make_approval("a1786442424",now,300,plan,auto.LEGACY_BASKET_EXPOSURE_PREWRITE_SHA256)
 executor=auto.Executor.new_state(None); executor.update(status="fatal",exposure_possible=False,fatal_reason="basket exposure not clear",errors={"unknown":"basket exposure not clear"},approved=approval,planDocument=plan,planFingerprint=approval["fingerprint"],executorSourceSha256=auto.LEGACY_BASKET_EXPOSURE_PREWRITE_SHA256)
 monitor={"state":"watching","submitted":True,"winner":"ETHUSDT","identity":identity,"winnerPlan":plan}
 ledger={"day":auto.utc_day(now),"submissions":1,"modeled_loss_reserved":"2","loss_streak":0,"open_basket":identity,"trades":{identity:{"status":"ambiguous","modeled_loss":"2"}}}
 for name,value in (("executor",executor),("monitor",monitor),("ledger",ledger)): auto.atomic_json(state/(name+".json"),value)
 return state


def test_exact_eth_basket_exposure_legacy_prewrite_fatal_repairs_with_manual_eth(tmp_path):
 state=production_eth_legacy_repair_files(tmp_path); calls=[]
 def private(path,params=None,method="GET",body=None,private=False):
  calls.append((path,params,method)); rows=[{"symbol":"ETHUSDT","size":"0.5","side":"Buy"}] if path.endswith("position/list") else []
  return {"list":rows,"_response_ts":1786442500}
 result=auto.repair_prewrite_fatal(auto.Executor.Runtime(private,"live"),state,1786442500)
 assert result=={"status":"repaired","identity":"auto-1786442424-ETHUSDT"}
 assert [x[0] for x in calls]==["/v5/position/list","/v5/order/realtime"] and all(x[2]=="GET" for x in calls)
 executor=json.loads((state/"executor.json").read_text()); monitor=json.loads((state/"monitor.json").read_text()); ledger=json.loads((state/"ledger.json").read_text())
 assert executor["status"]=="failed_prewrite_repaired" and monitor["submitted"] is False
 assert ledger["open_basket"] is None and ledger["submissions"]==0 and ledger["modeled_loss_reserved"]=="0.0"
 assert ledger["trades"]["auto-1786442424-ETHUSDT"]["status"]=="failed_prewrite" and not (state/"prewrite_repair.json").exists()


def test_exact_eth_legacy_repair_allows_twenty_unrelated_empty_link_orders(tmp_path):
 state=production_eth_legacy_repair_files(tmp_path); manual=[{"symbol":f"MANUAL{i}USDT","orderStatus":"New","orderLinkId":""} for i in range(20)]; writes=[]
 def transport(path,params=None,method="GET",body=None,private=False):
  if method!="GET": writes.append((path,body)); raise AssertionError("write")
  return {"list":[] if path.endswith("position/list") else manual,"_response_ts":1786442500}
 assert auto.repair_prewrite_fatal(auto.Executor.Runtime(transport,"live"),state,1786442500)=={"status":"repaired","identity":"auto-1786442424-ETHUSDT"}
 assert writes==[] and all(x["orderLinkId"]=="" for x in manual)


@pytest.mark.parametrize("target,key,value",[
 ("executor","body",{"symbol":"ETHUSDT"}),
 ("executor","orderLinkId","auto-1786442424-ETHUSDT"),
 ("executor","exposure_possible",True),
 ("executor","fatal_reason","other"),
 ("executor","approved",{"executorSourceSha256":"0"*64}),
 ("monitor","identity","auto-1786442425-ETHUSDT"),
 ("monitor","winner","BTCUSDT"),
 ("monitor","winnerPlan",{"plans":[{"symbol":"BTCUSDT"}]}),
])
def test_eth_legacy_repair_mismatch_preserves_bytes(tmp_path,target,key,value):
 state=production_eth_legacy_repair_files(tmp_path); path=state/(target+".json"); document=json.loads(path.read_text())
 if target=="executor" and key=="approved": document[key].update(value)
 else: document[key]=value
 path.write_text(json.dumps(document)); before={p.name:p.read_bytes() for p in state.glob("*.json")}
 clear=lambda path,params=None,method="GET",body=None,private=False:{"list":[],"_response_ts":1786442500}
 with pytest.raises(auto.FailClosed): auto.repair_prewrite_fatal(auto.Executor.Runtime(clear,"live"),state,1786442500)
 assert {p.name:p.read_bytes() for p in state.glob("*.json")}==before


def test_eth_legacy_repair_bot_linked_order_preserves_bytes(tmp_path):
 state=production_eth_legacy_repair_files(tmp_path); before={p.name:p.read_bytes() for p in state.glob("*.json")}
 def linked(path,params=None,method="GET",body=None,private=False):
  rows=[{"symbol":"BTCUSDT","orderStatus":"New","orderLinkId":"auto-other"}] if path.endswith("order/realtime") else []
  return {"list":rows,"_response_ts":1786442500}
 with pytest.raises(auto.FailClosed,match="account exposure not clear"): auto.repair_prewrite_fatal(auto.Executor.Runtime(linked,"live"),state,1786442500)
 assert {p.name:p.read_bytes() for p in state.glob("*.json")}==before


@pytest.mark.parametrize("target,key,value",[
 ("executor","approved",{"nonce":"1786399502"}),
 ("executor","errors",{"unknown":"prewrite"}),
 ("executor","planFingerprint","0"*64),
 ("monitor","winner","BTCUSDT"),
 ("monitor","identity","auto-1786399503-DOGEUSDT"),
])
def test_exact_production_legacy_prewrite_fatal_mismatch_fails_closed(tmp_path,target,key,value):
 state,clear=production_legacy_repair_files(tmp_path); path=state/(target+".json"); document=json.loads(path.read_text())
 if key=="approved": document[key].update(value)
 else: document[key]=value
 path.write_text(json.dumps(document))
 with pytest.raises(auto.FailClosed,match="ownership mismatch"):
  auto.repair_prewrite_fatal(auto.Executor.Runtime(clear,"live"),state,1786399502)
 assert json.loads((state/"ledger.json").read_text())["open_basket"]=="auto-1786399502-DOGEUSDT" and not (state/"prewrite_repair.json").exists()


def test_prewrite_fatal_repair_is_restart_safe_and_exposure_fails_closed(tmp_path):
 state=tmp_path/"state"; state.mkdir(); identity="auto-1786399500-DOGEUSDT"
 (state/"executor.json").write_text(json.dumps({"status":"fatal","exposure_possible":False,"errors":{"DOGEUSDT":"exactly one candidate required"}}))
 (state/"monitor.json").write_text(json.dumps({"submitted":True,"winner":"DOGEUSDT","identity":identity}))
 ledger=auto.DailyLedger(state/"ledger.json",lambda:1786399500); ledger.reserve(identity,2); ledger.mark_ambiguous(identity)
 clear=lambda path,params=None,method="GET",body=None,private=False:{"list":[],"_response_ts":1786399500}
 assert auto.repair_prewrite_fatal(auto.Executor.Runtime(clear,"live"),state,1786399500)["status"]=="repaired"
 saved=json.loads((state/"ledger.json").read_text()); assert saved["open_basket"] is None and saved["submissions"]==0 and saved["trades"][identity]["status"]=="failed_prewrite"
 with pytest.raises(auto.FailClosed): auto.repair_prewrite_fatal(auto.Executor.Runtime(clear,"live"),state,1786399500)


def repair_files(tmp_path):
 state=tmp_path/"state"; state.mkdir(); identity="auto-1786399500-DOGEUSDT"
 executor={"status":"fatal","exposure_possible":False,"errors":{"DOGEUSDT":"prewrite"},"manualNote":"keep"}
 monitor={"submitted":True,"winner":"DOGEUSDT","identity":identity,"manualNote":"keep"}
 (state/"executor.json").write_text(json.dumps(executor)); (state/"monitor.json").write_text(json.dumps(monitor))
 ledger=auto.DailyLedger(state/"ledger.json",lambda:1786399500); ledger.reserve(identity,2); ledger.mark_ambiguous(identity)
 clear=lambda path,params=None,method="GET",body=None,private=False:{"list":[],"_response_ts":1786399500}
 return state,identity,clear


@pytest.mark.parametrize("fault",["after_journal","after_ledger","after_monitor","after_executor"])
def test_prewrite_repair_journal_rolls_forward_each_crash(tmp_path,fault):
 state,identity,clear=repair_files(tmp_path)
 with pytest.raises(RuntimeError,match="injected"):
  auto.repair_prewrite_fatal(auto.Executor.Runtime(clear,"live"),state,1786399500,_fault=fault)
 journal=json.loads((state/"prewrite_repair.json").read_text())
 assert journal["identity"]==identity and set(journal["expectedHashes"])=={"executor","monitor","ledger"}
 assert auto.repair_prewrite_fatal(auto.Executor.Runtime(clear,"live"),state,1786399501)=={"status":"repaired","identity":identity}
 ledger=json.loads((state/"ledger.json").read_text()); monitor=json.loads((state/"monitor.json").read_text()); executor=json.loads((state/"executor.json").read_text())
 assert ledger["submissions"]==0 and ledger["modeled_loss_reserved"]=="0.0" and ledger["trades"][identity]["status"]=="failed_prewrite"
 assert monitor["manualNote"]==executor["manualNote"]=="keep"
 assert monitor["submitted"] is False and executor["status"]=="failed_prewrite_repaired"
 assert not (state/"prewrite_repair.json").exists()


def test_prewrite_repair_journal_tamper_fails_closed(tmp_path):
 state,identity,clear=repair_files(tmp_path)
 with pytest.raises(RuntimeError): auto.repair_prewrite_fatal(auto.Executor.Runtime(clear,"live"),state,1786399500,_fault="after_ledger")
 monitor=json.loads((state/"monitor.json").read_text()); monitor["manualNote"]="tampered"; (state/"monitor.json").write_text(json.dumps(monitor))
 with pytest.raises(auto.FailClosed,match="journal state mismatch"):
  auto.repair_prewrite_fatal(auto.Executor.Runtime(clear,"live"),state,1786399501)
 assert json.loads((state/"ledger.json").read_text())["submissions"]==0


def repaired_terminal_files(tmp_path):
 state=tmp_path/"state"; state.mkdir(); identity="auto-1786399500-DOGEUSDT"
 executor=auto.Executor.new_state(None); executor.update(status="failed_prewrite_repaired",exposure_possible=False)
 monitor={"state":"repair_complete","submitted":False,"winner":"DOGEUSDT","identity":identity}
 ledger={"open_basket":None,"submissions":1,"modeled_loss_reserved":"2","trades":{identity:{"status":"failed_prewrite","modeled_loss":"2"}}}
 for name,value in (("executor",executor),("monitor",monitor),("ledger",ledger)): auto.atomic_json(state/(name+".json"),value)
 return state,identity


@pytest.mark.parametrize("fault",["after_journal","after_executor","after_monitor"])
def test_repaired_terminal_reset_journal_rolls_forward_each_crash(tmp_path,fault):
 state,identity=repaired_terminal_files(tmp_path); ledger_before=(state/"ledger.json").read_bytes()
 with pytest.raises(RuntimeError,match="injected"): auto._reset_repaired_terminal(state,_fault=fault)
 journal=json.loads((state/"terminal_reset.json").read_text())
 assert journal["identity"]==identity and journal["monitorTombstone"] is True
 auto._reset_repaired_terminal(state)
 assert json.loads((state/"executor.json").read_text())==auto.Executor.new_state(None)
 assert not (state/"monitor.json").exists() and not (state/"terminal_reset.json").exists()
 assert (state/"ledger.json").read_bytes()==ledger_before


def test_repaired_terminal_reset_journal_tamper_and_mismatch_fail_closed(tmp_path):
 state,_=repaired_terminal_files(tmp_path)
 with pytest.raises(RuntimeError): auto._reset_repaired_terminal(state,_fault="after_journal")
 journal=json.loads((state/"terminal_reset.json").read_text()); journal["monitorTombstone"]=False; auto.atomic_json(state/"terminal_reset.json",journal)
 before={p.name:p.read_bytes() for p in state.iterdir()}
 with pytest.raises(auto.FailClosed,match="invalid reset journal"): auto._reset_repaired_terminal(state)
 assert before=={p.name:p.read_bytes() for p in state.iterdir()}
 state2=tmp_path/"state2"; state2.mkdir(); state,_=repaired_terminal_files_at(state2)


def repaired_terminal_files_at(state):
 identity="auto-1786399500-DOGEUSDT"; executor=auto.Executor.new_state(None); executor.update(status="failed_prewrite_repaired",exposure_possible=False)
 values={"executor":executor,"monitor":{"state":"repair_complete","submitted":False,"winner":"DOGEUSDT","identity":identity},"ledger":{"open_basket":None,"submissions":1,"modeled_loss_reserved":"2","trades":{identity:{"status":"failed_prewrite","modeled_loss":"2"}}}}
 for name,value in values.items(): auto.atomic_json(state/(name+".json"),value)
 with pytest.raises(RuntimeError): auto._reset_repaired_terminal(state,_fault="after_journal")
 executor["status"]="fatal"; auto.atomic_json(state/"executor.json",executor)
 with pytest.raises(auto.FailClosed,match="journal state mismatch"): auto._reset_repaired_terminal(state)
 return state,identity


def test_run_once_resumes_reset_journal_before_inert_status_routing(tmp_path,monkeypatch):
 state,_=repaired_terminal_files(tmp_path)
 with pytest.raises(RuntimeError): auto._reset_repaired_terminal(state,_fault="after_executor")
 monkeypatch.setattr(auto,"load_policy",lambda *_: (_ for _ in ()).throw(RuntimeError("policy reached")))
 with pytest.raises(RuntimeError,match="policy reached"): auto.run_once("live",lambda *a,**k:None,1786399500,state_dir=state)
 assert not (state/"terminal_reset.json").exists() and not (state/"monitor.json").exists()


def test_durable_identity_builder_parser_binds_issued_nonce_and_symbol():
 assert auto.build_durable_identity(1786399500,"a1786399500","DOGEUSDT")=="basket-1786399500-DOGEUSDT"
 assert auto.parse_durable_identity("basket-1786399500-DOGEUSDT")== (1786399500,"a1786399500","DOGEUSDT")
 assert auto.parse_durable_identity("auto-1786399500-DOGEUSDT")== (1786399500,"a1786399500","DOGEUSDT")
 for bad in ("basket-a1786399500-DOGEUSDT","basket-1786399500-dogeusdt","basket-1786399500-DOGE/USDT","auto-x-DOGEUSDT",""):
  assert auto.parse_durable_identity(bad) is None
 with pytest.raises(auto.FailClosed): auto.build_durable_identity(1786399500,"a1786399501","DOGEUSDT")
 with pytest.raises(auto.FailClosed): auto.build_durable_identity(True,"a1","DOGEUSDT")


@pytest.mark.parametrize("executor_patch",[
 {"exposure_possible":True},{"body":{"symbol":"DOGEUSDT"}},{"orderId":"x"},{"orderLinkId":"x"},{"closeBody":{"symbol":"DOGEUSDT"}},{"closeOrderLinkId":"x"}
])
def test_prewrite_repair_rejects_any_write_identity(tmp_path,executor_patch):
 state,identity,clear=repair_files(tmp_path); executor=json.loads((state/"executor.json").read_text()); executor.update(executor_patch); (state/"executor.json").write_text(json.dumps(executor))
 with pytest.raises(auto.FailClosed): auto.repair_prewrite_fatal(auto.Executor.Runtime(clear,"live"),state,1786399500)
 assert json.loads((state/"ledger.json").read_text())["open_basket"]==identity and not (state/"prewrite_repair.json").exists()


@pytest.mark.parametrize("identity",[None,"","bad identity","auto-x-ETHUSDT"])
def test_prewrite_repair_rejects_malformed_or_mismatched_identity(tmp_path,identity):
 state,owned,clear=repair_files(tmp_path); monitor=json.loads((state/"monitor.json").read_text()); monitor["identity"]=identity; (state/"monitor.json").write_text(json.dumps(monitor))
 with pytest.raises(auto.FailClosed): auto.repair_prewrite_fatal(auto.Executor.Runtime(clear,"live"),state,1786399500)
 assert json.loads((state/"ledger.json").read_text())["open_basket"]==owned


def test_prewrite_repair_rejects_active_order_and_private_read_failure(tmp_path):
 state,identity,_=repair_files(tmp_path)
 active=lambda path,params=None,method="GET",body=None,private=False:{"list":[{"symbol":"DOGEUSDT","orderStatus":"New"}] if path.endswith("order/realtime") else [],"_response_ts":1786399500}
 with pytest.raises(auto.FailClosed,match="exposure"): auto.repair_prewrite_fatal(auto.Executor.Runtime(active,"live"),state,1786399500)
 stale=lambda *a,**k:{"list":[],"_response_ts":1786399400}
 with pytest.raises(auto.FailClosed,match="stale"): auto.repair_prewrite_fatal(auto.Executor.Runtime(stale,"live"),state,1786399500)
 failing=lambda *a,**k:(_ for _ in ()).throw(ConnectionError("private"))
 with pytest.raises(ConnectionError): auto.repair_prewrite_fatal(auto.Executor.Runtime(failing,"live"),state,1786399500)
 assert json.loads((state/"ledger.json").read_text())["open_basket"]==identity and not (state/"prewrite_repair.json").exists()


def repair_account(rows_by_path,ts=1786399500):
 def transport(path,params=None,method="GET",body=None,private=False):
  return {"list":rows_by_path.get(path,[]),"_response_ts":ts}
 return transport


def test_prewrite_repair_allows_unrelated_manual_exposure_byte_preserved(tmp_path):
 state,identity,_=repair_files(tmp_path)
 manual_position={"symbol":"ETHUSDT","size":"2.5","side":"Buy"}
 manual_order={"symbol":"BTCUSDT","orderStatus":"New","orderLinkId":"manual-42","price":"60000"}
 before=(json.dumps(manual_position,sort_keys=True).encode(),json.dumps(manual_order,sort_keys=True).encode())
 tx=repair_account({"/v5/position/list":[manual_position],"/v5/order/realtime":[manual_order]})
 assert auto.repair_prewrite_fatal(auto.Executor.Runtime(tx,"live"),state,1786399500)=={"status":"repaired","identity":identity}
 assert before==(json.dumps(manual_position,sort_keys=True).encode(),json.dumps(manual_order,sort_keys=True).encode())


@pytest.mark.parametrize("rows",[
 {"/v5/position/list":[{"symbol":"DOGEUSDT","size":"1"}]},
 {"/v5/order/realtime":[{"symbol":"DOGEUSDT","orderStatus":"New","orderLinkId":"manual-42"}]},
 {"/v5/order/realtime":[{"symbol":"ETHUSDT","orderStatus":"New","orderLinkId":"auto-other-ETHUSDT"}]},
])
def test_prewrite_repair_blocks_winner_or_bot_exposure(tmp_path,rows):
 state,identity,_=repair_files(tmp_path)
 with pytest.raises(auto.FailClosed,match="exposure"):
  auto.repair_prewrite_fatal(auto.Executor.Runtime(repair_account(rows),"live"),state,1786399500)
 assert json.loads((state/"ledger.json").read_text())["open_basket"]==identity
 assert not (state/"prewrite_repair.json").exists()


def test_prewrite_repair_stale_account_read_blocks(tmp_path):
 state,identity,_=repair_files(tmp_path)
 with pytest.raises(auto.FailClosed,match="stale"):
  auto.repair_prewrite_fatal(auto.Executor.Runtime(repair_account({},1786399400),"live"),state,1786399500)
 assert json.loads((state/"ledger.json").read_text())["open_basket"]==identity


def test_prewrite_fatal_repair_retains_reservation_on_exposure(tmp_path):
 state=tmp_path/"state"; state.mkdir(); identity="auto-x-DOGEUSDT"
 (state/"executor.json").write_text(json.dumps({"status":"fatal","exposure_possible":False,"errors":{"DOGEUSDT":"x"}})); (state/"monitor.json").write_text(json.dumps({"submitted":True,"winner":"DOGEUSDT","identity":identity}))
 ledger=auto.DailyLedger(state/"ledger.json",lambda:NOW); ledger.reserve(identity,2)
 def exposed(path,params=None,method="GET",body=None,private=False): return {"list":[{"symbol":"DOGEUSDT","size":"1"}] if path.endswith("position/list") else [],"_response_ts":NOW}
 with pytest.raises(auto.FailClosed,match="exposure"): auto.repair_prewrite_fatal(auto.Executor.Runtime(exposed,"live"),state,NOW)
 assert json.loads((state/"ledger.json").read_text())["open_basket"]==identity


def test_policy_snapshot_persisted_before_entry_post_and_tamper_rejected(tmp_path,monkeypatch):
 state=tmp_path/"state"; tx=ChronologicalLiveTransport(state); policy_path=enable_live(tmp_path,monkeypatch)
 assert auto.run_once("live",tx,NOW,policy_path=policy_path,state_dir=state)["status"]=="watching"
 tx.phase=1; tx.now=NOW+600
 assert auto.run_once("live",tx,NOW+600,policy_path=policy_path,state_dir=state)["status"]=="filled"
 monitor=json.loads((state/"monitor.json").read_text()); executor=json.loads((state/"executor.json").read_text())
 for durable in (monitor,executor):
  assert durable["policy_snapshot"]==policy()
  assert durable["policy_digest"]==auto.canonical_hash(durable["policy_snapshot"])
 executor["policy_snapshot"]["max_entries_day"]=3
 (state/"executor.json").write_text(json.dumps(executor))
 with pytest.raises(auto.FailClosed,match="policy"):
  auto.run_once("live",tx,NOW+301,policy_path=tmp_path/"missing",state_dir=state)


def test_open_recovery_uses_durable_policy_without_external_file(tmp_path,monkeypatch):
 state=tmp_path/"state"; tx=ChronologicalLiveTransport(state); policy_path=enable_live(tmp_path,monkeypatch)
 assert auto.run_once("live",tx,NOW,policy_path=policy_path,state_dir=state)["status"]=="watching"
 tx.phase=1; tx.now=NOW+600
 assert auto.run_once("live",tx,NOW+600,policy_path=policy_path,state_dir=state)["status"]=="filled"
 monkeypatch.delenv("AUTONOMOUS_BASKET_LIVE"); monkeypatch.delenv("AUTONOMOUS_BASKET_POLICY_SHA256")
 policy_path.unlink(); tx.now=NOW+601
 assert auto.run_once("live",tx,NOW+601,policy_path=policy_path,state_dir=state)["status"]=="open"


@pytest.mark.parametrize("kind,late",[("position",False),("order",False),("position",True),("order",True)])
def test_real_run_once_manual_exposure_before_create_is_untouched(tmp_path,monkeypatch,kind,late):
 reads=0
 def exposure(tx,seen,symbol):
  nonlocal reads
  if seen!=kind or symbol not in (None,tx.winner): return False
  reads+=1
  # Bybit account-wide settleCoin query includes winner exposure. Late case
  # withholds it until executor's final account-wide reread.
  account_wide=symbol is None
  return tx.phase==1 and (account_wide if not late else account_wide and reads>1)
 state=tmp_path/"state"; tx=ChronologicalLiveTransport(state,exposure); policy_path=enable_live(tmp_path,monkeypatch)
 assert auto.run_once("live",tx,NOW,policy_path=policy_path,state_dir=state)["status"]=="watching"
 before={"kind":kind,"symbol":tx.winner,"size":"1"}; tx.phase=1; tx.now=NOW+600
 result=auto.run_once("live",tx,NOW+600,policy_path=policy_path,state_dir=state)
 assert result=={"mode":"live","status":"rejected","candidate":"AAAUSDT"}
 assert not tx.posts and before=={"kind":kind,"symbol":"AAAUSDT","size":"1"}
 assert not [c for c in tx.calls if c[0]=="POST" and c[1] in {"/v5/order/cancel","/v5/position/trading-stop"}]
 ledger=json.loads((state/"ledger.json").read_text()); monitor=json.loads((state/"monitor.json").read_text())
 assert ledger["submissions"]==0 and ledger["open_basket"] is None and monitor["submitted"] is False
 tx.phase=0; tx.now=NOW+601
 assert auto.run_once("live",tx,NOW+601,policy_path=policy_path,state_dir=state)["status"]=="watching"


def test_missing_native_protection_repairs_and_confirms(tmp_path,monkeypatch):
 state,body=open_files(tmp_path); calls=[]; reads=0
 def tx(path,params=None,method="GET",body=None,private=False):
  nonlocal reads
  calls.append((method,path,body))
  if method=="POST":
   saved=json.loads((state/"executor.json").read_text())
   assert saved["exit_intent_type"]=="protection_repair"
   assert saved["protectionBody"]==body
   return {}
  if path=="/v5/position/list":
   reads+=1
   return {"list":[{"symbol":"AAAUSDT","size":"1","side":"Buy","positionIdx":0,"entryPrice":"100","stopLoss":"" if reads==1 else "90","takeProfit":"" if reads==1 else "120"}],"_response_ts":NOW}
  if path=="/v5/order/realtime": return {"list":[],"_response_ts":NOW}
  raise AssertionError(path)
 notices=[]; result=auto.run_once("live",tx,NOW,policy_path=enable_live(tmp_path,monkeypatch),state_dir=state,notify=notices.append)
 posts=[c for c in calls if c[0]=="POST"]
 assert result=={"mode":"live","status":"open","action":"protection_repair"} and notices==["protection:AAAUSDT"]
 assert posts==[("POST","/v5/position/trading-stop",{"category":"linear","symbol":"AAAUSDT","positionIdx":0,"stopLoss":"90","takeProfit":"120"})]
 assert json.loads((state/"executor.json").read_text())["status"]=="open"


def test_ambiguous_protection_repair_restart_then_emergency_close(tmp_path,monkeypatch):
 state,body=open_files(tmp_path); posts=[]
 durable=json.loads((state/"executor.json").read_text()); durable.update(status="protection_reconciling",exit_intent_type="protection_repair",protectionBody={"category":"linear","symbol":"AAAUSDT","positionIdx":0,"stopLoss":"90","takeProfit":"120"},repairAttempts=1); (state/"executor.json").write_text(json.dumps(durable))
 def tx(path,params=None,method="GET",body=None,private=False):
  if method=="POST": posts.append((path,body)); return {}
  if path=="/v5/position/list": return {"list":[{"symbol":"AAAUSDT","size":"1","side":"Buy","positionIdx":0,"entryPrice":"100","stopLoss":"","takeProfit":""}],"_response_ts":NOW}
  if path=="/v5/order/realtime": return {"list":[],"_response_ts":NOW}
  raise AssertionError(path)
 result=auto.run_once("live",tx,NOW,policy_path=enable_live(tmp_path,monkeypatch),state_dir=state)
 saved=json.loads((state/"executor.json").read_text())
 assert result["action"]=="emergency_close" and posts[-1][0]=="/v5/order/create"
 assert posts[-1][1]["reduceOnly"] is True and saved["status"]=="close_reconciling"


def test_ambiguous_stop_update_reconciles_without_close_order_link(tmp_path,monkeypatch):
 state,body=open_files(tmp_path); durable=json.loads((state/"executor.json").read_text()); durable.update(status="stop_reconciling",exit_intent_type="stop",stopBody={"category":"linear","symbol":"AAAUSDT","positionIdx":0,"stopLoss":"100.2"},repairAttempts=0); (state/"executor.json").write_text(json.dumps(durable))
 def tx(path,params=None,method="GET",body=None,private=False):
  assert method=="GET" and path=="/v5/position/list"
  return {"list":[{"symbol":"AAAUSDT","size":"1","side":"Buy","positionIdx":0,"entryPrice":"100","stopLoss":"101","takeProfit":"120"}],"_response_ts":NOW}
 assert auto.run_once("live",tx,NOW,policy_path=enable_live(tmp_path,monkeypatch),state_dir=state)=={"mode":"live","status":"open","action":"stop_reconciled"}
 assert "closeOrderLinkId" not in json.loads((state/"executor.json").read_text())


def test_unapplied_stop_retry_then_emergency_close(tmp_path,monkeypatch):
 state,body=open_files(tmp_path); posts=[]; durable=json.loads((state/"executor.json").read_text()); durable.update(status="stop_reconciling",exit_intent_type="stop",stopBody={"category":"linear","symbol":"AAAUSDT","positionIdx":0,"stopLoss":"100.2"},repairAttempts=0); (state/"executor.json").write_text(json.dumps(durable))
 def tx(path,params=None,method="GET",body=None,private=False):
  if method=="POST": posts.append((path,body)); return {}
  if path=="/v5/position/list": return {"list":[{"symbol":"AAAUSDT","size":"1","side":"Buy","positionIdx":0,"entryPrice":"100","stopLoss":"90","takeProfit":"120"}],"_response_ts":NOW}
  if path=="/v5/order/realtime": return {"list":[],"_response_ts":NOW}
  raise AssertionError(path)
 auto.run_once("live",tx,NOW,policy_path=enable_live(tmp_path,monkeypatch),state_dir=state)
 assert posts==[("/v5/position/trading-stop",durable["stopBody"])]
 saved=json.loads((state/"executor.json").read_text()); assert saved["status"]=="stop_reconciling" and saved["repairAttempts"]==1
 auto.run_once("live",tx,NOW,policy_path=enable_live(tmp_path,monkeypatch),state_dir=state)
 assert posts[-1][0]=="/v5/order/create" and posts[-1][1]["reduceOnly"] is True

def test_exact_durable_policy_contract_rejects_semantic_expansion():
 p=policy(); digest=auto.canonical_hash(p)
 auto.Executor.validate_autonomous_policy(p,digest)
 for key,value in (("entry","Market"),("protection","advisory"),("loss_pause",3),("max_entries_day",1),("max_modeled_loss_trade_usd","1")):
  bad=dict(p); bad[key]=value
  with pytest.raises(auto.Executor.PermanentError): auto.Executor.validate_autonomous_policy(bad,auto.canonical_hash(bad))

def test_open_action_uses_real_m5_volume_high_low_and_directional_oi():
 body={"symbol":"AAAUSDT","side":"Buy","positionIdx":0,"qty":"1","price":"100"}; durable={"winner":"AAAUSDT","orderLinkId":"x","body":body}; calls=[]
 def tx(path,params=None,method="GET",body=None,private=False):
  calls.append(path)
  if path=="/v5/market/kline":
   end=int(NOW//300*300-300); vals=[(100,110,90,100,10),(100,112,91,101,10),(101,130,92,99,10),(99,140,80,98,16)]
   return {"list":[[str((end-(3-i)*300)*1000),str(o),str(h),str(l),str(c),str(v)] for i,(o,h,l,c,v) in enumerate(vals)],"_response_ts":NOW}
  if path=="/v5/market/open-interest": return {"list":[{"openInterest":"100"},{"openInterest":"110"}],"_response_ts":NOW}
  raise AssertionError(path)
 action=auto._open_action(auto.Executor.Runtime(tx,"live"),durable,{"symbol":"AAAUSDT","side":"Buy","positionIdx":0,"size":"1","entryPrice":"100","stopLoss":"90","takeProfit":"120"},NOW)
 assert action and action["kind"]=="close" and "/v5/market/tickers" not in calls

def test_partial_fill_open_ownership_uses_durable_filled_qty(tmp_path):
 state,body=open_files(tmp_path); durable=json.loads((state/"executor.json").read_text()); durable["filledQty"]="0.4"; (state/"executor.json").write_text(json.dumps(durable))
 rows=[{"symbol":"AAAUSDT","size":"0.4","side":"Buy","positionIdx":0}]
 rt=auto.Executor.Runtime(lambda *a,**k:{"list":rows,"_response_ts":NOW},"live")
 assert auto._owned_open_position(rt,durable,NOW)["size"]=="0.4"


def _stale_inert_monitor(state, old_policy):
 monitor=json.loads((state/"monitor.json").read_text()); digest=auto.canonical_hash(old_policy)
 monitor["touches"]={k:None for k in monitor["touches"]}; monitor.pop("triggerTs",None)
 monitor.update(policy_snapshot=old_policy,policy_digest=digest,autonomousPolicySnapshot=old_policy,autonomousPolicyDigest=digest)
 (state/"monitor.json").write_bytes(auto.canonical_bytes(monitor)+b"\n")
 (state/"executor.json").write_bytes(auto.canonical_bytes(auto.Executor.new_state(None))+b"\n")
 return monitor


def test_stale_inert_monitor_policy_rollover_e2e_reaches_watching(tmp_path,monkeypatch):
 state=tmp_path/"state"; tx=ChronologicalLiveTransport(state); pp=enable_live(tmp_path,monkeypatch)
 assert auto.run_once("live",tx,NOW,policy_path=pp,state_dir=state)["status"]=="watching"
 old=dict(policy(),executor_sha256="0"*64); _stale_inert_monitor(state,old)
 ledger_before=(state/"ledger.json").read_bytes() if (state/"ledger.json").exists() else None
 tx.now=NOW+1
 assert auto.run_once("live",tx,NOW+1,policy_path=pp,state_dir=state)=={"mode":"live","status":"watching"}
 monitor=json.loads((state/"monitor.json").read_text())
 assert monitor["policy_snapshot"]==policy() and monitor["policy_digest"]==auto.canonical_hash(policy())
 assert json.loads((state/"executor.json").read_text())==auto.Executor.new_state(None,{p["symbol"]:None for p in monitor["plan"]["plans"]})
 assert ((state/"ledger.json").read_bytes() if (state/"ledger.json").exists() else None)==ledger_before
 assert not [call for call in tx.calls if call[0]=="POST"]


def test_stale_observational_production_monitor_rolls_over(tmp_path,monkeypatch):
 state=tmp_path/"state"; tx=ChronologicalLiveTransport(state); pp=enable_live(tmp_path,monkeypatch)
 auto.run_once("live",tx,NOW,policy_path=pp,state_dir=state)
 monitor=_stale_inert_monitor(state,dict(policy(),executor_sha256="0"*64))
 monitor.update(touches={"AAAUSDT":NOW-900,"BBBUSDT":None},triggerTs={"AAAUSDT":NOW-600},triggerM15Ts=NOW-600,triggerM15Close="100",disarmed={"BBBUSDT":"lost"},setupNotified={"AAAUSDT":True})
 (state/"monitor.json").write_bytes(auto.canonical_bytes(monitor)+b"\n")
 ledger={"day":auto.utc_day(NOW),"submissions":0,"modeled_loss_reserved":"0","loss_streak":0,"open_basket":None,"trades":{}}
 (state/"ledger.json").write_bytes(auto.canonical_bytes(ledger)+b"\n"); before=(state/"ledger.json").read_bytes(); tx.calls.clear(); tx.now=NOW+1
 assert auto.run_once("live",tx,NOW+1,policy_path=pp,state_dir=state)=={"mode":"live","status":"watching"}
 assert (state/"ledger.json").read_bytes()==before
 assert not [call for call in tx.calls if call[0]=="POST"]


@pytest.mark.parametrize("key,value",[("reservation",{}),("identity","auto-x-AAAUSDT"),("winnerPlan",{}),("submitted",True)])
def test_observational_rollover_reservation_flip_fails_closed_byte_preserved(tmp_path,monkeypatch,key,value):
 state=tmp_path/"state"; tx=ChronologicalLiveTransport(state); pp=enable_live(tmp_path,monkeypatch)
 auto.run_once("live",tx,NOW,policy_path=pp,state_dir=state)
 monitor=_stale_inert_monitor(state,dict(policy(),executor_sha256="0"*64)); monitor.update(touches={"AAAUSDT":NOW-900},triggerTs={"AAAUSDT":NOW-600},disarmed={"BBBUSDT":"lost"},setupNotified=True); monitor[key]=value
 (state/"monitor.json").write_bytes(auto.canonical_bytes(monitor)+b"\n"); before={p.name:p.read_bytes() for p in state.iterdir()}; tx.calls.clear()
 with pytest.raises(auto.FailClosed): auto.run_once("live",tx,NOW+1,policy_path=pp,state_dir=state)
 assert before=={p.name:p.read_bytes() for p in state.iterdir()} and not [c for c in tx.calls if c[0]=="POST"]


@pytest.mark.parametrize("status",["reserved","ambiguous"])
def test_observational_rollover_ledger_reservation_fails_closed_byte_preserved(tmp_path,monkeypatch,status):
 state=tmp_path/"state"; tx=ChronologicalLiveTransport(state); pp=enable_live(tmp_path,monkeypatch)
 auto.run_once("live",tx,NOW,policy_path=pp,state_dir=state)
 monitor=_stale_inert_monitor(state,dict(policy(),executor_sha256="0"*64)); monitor.update(touches={"AAAUSDT":NOW-900},triggerTs={"AAAUSDT":NOW-600},disarmed={"BBBUSDT":"lost"})
 (state/"monitor.json").write_bytes(auto.canonical_bytes(monitor)+b"\n")
 ledger={"day":auto.utc_day(NOW),"submissions":1,"modeled_loss_reserved":"2","loss_streak":0,"open_basket":None,"trades":{"auto-x-AAAUSDT":{"status":status,"modeled_loss":"2"}}}
 (state/"ledger.json").write_bytes(auto.canonical_bytes(ledger)+b"\n"); before={p.name:p.read_bytes() for p in state.iterdir()}; tx.calls.clear()
 with pytest.raises(auto.FailClosed): auto.run_once("live",tx,NOW+1,policy_path=pp,state_dir=state)
 assert before=={p.name:p.read_bytes() for p in state.iterdir()} and not [c for c in tx.calls if c[0]=="POST"]


@pytest.mark.parametrize("target,key,value",[
 ("monitor","submitted",True),("monitor","winner","AAAUSDT"),("monitor","identity","auto-x-AAAUSDT"),
 ("monitor","reservation",{}),("monitor","winnerPlan",{}),
 ("executor","status","reconciling"),("executor","body",{}),("executor","orderLinkId","x"),
 ("ledger","open_basket","x"),("journal",None,None),("repair",None,None),
])
def test_stale_policy_rollover_ambiguity_fails_closed_byte_preserved(tmp_path,monkeypatch,target,key,value):
 state=tmp_path/"state"; tx=ChronologicalLiveTransport(state); pp=enable_live(tmp_path,monkeypatch)
 auto.run_once("live",tx,NOW,policy_path=pp,state_dir=state); _stale_inert_monitor(state,dict(policy(),executor_sha256="0"*64))
 if target=="monitor":
  doc=json.loads((state/"monitor.json").read_text()); doc[key]=value; (state/"monitor.json").write_text(json.dumps(doc))
 elif target=="executor":
  doc=json.loads((state/"executor.json").read_text()); doc[key]=value; (state/"executor.json").write_text(json.dumps(doc))
 elif target=="ledger": (state/"ledger.json").write_text(json.dumps({"open_basket":value,"trades":{}}))
 elif target=="journal": (state/"terminal_reset.json").write_text("{}")
 else: (state/"prewrite_repair.json").write_text("{}")
 if target=="journal": (state/"repair.lock").touch()
 before={p.name:p.read_bytes() for p in state.iterdir()}; tx.calls.clear()
 with pytest.raises(auto.FailClosed): auto.run_once("live",tx,NOW+1,policy_path=pp,state_dir=state)
 assert before=={p.name:p.read_bytes() for p in state.iterdir()} and not [c for c in tx.calls if c[0]=="POST"]


def test_stale_policy_rollover_malformed_monitor_byte_preserved(tmp_path,monkeypatch):
 state=tmp_path/"state"; state.mkdir(); pp=enable_live(tmp_path,monkeypatch)
 (state/"monitor.json").write_bytes(b"{broken"); (state/"executor.json").write_text(json.dumps(auto.Executor.new_state(None)))
 before={p.name:p.read_bytes() for p in state.iterdir()}
 with pytest.raises(auto.FailClosed): auto.run_once("live",FakeTransport(scan_responses()),NOW,policy_path=pp,state_dir=state)
 assert before=={p.name:p.read_bytes() for p in state.iterdir()}


@pytest.mark.parametrize("fault",["after_journal","after_monitor","after_executor"])
def test_stale_policy_rollover_fault_resume_converges(tmp_path,monkeypatch,fault):
 state=tmp_path/"state"; tx=ChronologicalLiveTransport(state); pp=enable_live(tmp_path,monkeypatch)
 auto.run_once("live",tx,NOW,policy_path=pp,state_dir=state); _stale_inert_monitor(state,dict(policy(),executor_sha256="0"*64))
 loaded=auto.load_policy(pp,NOW)
 with pytest.raises(RuntimeError,match="injected"):
  auto._rollover_inert_monitor(state,loaded,auto.scan_market(auto.Executor.Runtime(tx,"live"),NOW+1,"scan-fault"),_fault=fault)
 assert (state/"monitor_rollover.json").exists()
 auto._rollover_inert_monitor(state,loaded,None)
 assert not (state/"monitor_rollover.json").exists()
 monitor=json.loads((state/"monitor.json").read_text())
 assert monitor["policy_digest"]==loaded.digest
 assert json.loads((state/"executor.json").read_text())==auto.Executor.new_state(None,{p["symbol"]:None for p in monitor["plan"]["plans"]})


def test_expired_monitor_replacement_preserves_policy_bindings_and_rescans(tmp_path,monkeypatch):
 state=tmp_path/"state"; tx=ChronologicalLiveTransport(state); pp=enable_live(tmp_path,monkeypatch)
 assert auto.run_once("live",tx,NOW,policy_path=pp,state_dir=state)["status"]=="watching"
 old=json.loads((state/"monitor.json").read_text()); keys=("policy_snapshot","policy_digest","autonomousPolicySnapshot","autonomousPolicyDigest")
 tx.now=NOW+auto.SETUP_TTL_SECONDS
 assert auto.run_once("live",tx,tx.now,policy_path=pp,state_dir=state)["status"]=="watching"
 new=json.loads((state/"monitor.json").read_text())
 assert new["plan"]["generated_at"]!=old["plan"]["generated_at"]
 assert {k:new[k] for k in keys}=={k:old[k] for k in keys}
 # Replacement remains watched beyond old 15m horizon, then refreshes at 4h boundary.
 tx.phase=0; tx.now=NOW+auto.SETUP_TTL_SECONDS+1501
 assert auto.run_once("live",tx,tx.now,policy_path=pp,state_dir=state)["status"]=="watching"
 assert json.loads((state/"monitor.json").read_text())["plan"]["generated_at"]==new["plan"]["generated_at"]
 tx.phase=1; tx.now=NOW+2*auto.SETUP_TTL_SECONDS
 assert auto.run_once("live",tx,tx.now,policy_path=pp,state_dir=state)["status"]=="watching"
 assert not tx.posts
 latest=json.loads((state/"monitor.json").read_text())
 assert latest["plan"]["generated_at"]!=new["plan"]["generated_at"]


@pytest.mark.parametrize("volume,oi_last,posts",[(16,100,1),(10,90,1),(10,100,0)])
def test_open_run_once_reversal_requires_real_volume_or_directional_oi(tmp_path,monkeypatch,volume,oi_last,posts):
 state,body=open_files(tmp_path); calls=[]
 def tx(path,params=None,method="GET",body=None,private=False):
  calls.append((method,path,body))
  if method=="POST": return {"orderId":"close-1"}
  if path=="/v5/position/list": return {"list":[{"symbol":"AAAUSDT","size":"1","side":"Buy","positionIdx":0,"entryPrice":"100","stopLoss":"90","takeProfit":"120"}],"_response_ts":NOW}
  if path=="/v5/order/realtime": return {"list":[],"_response_ts":NOW}
  if path=="/v5/market/kline":
   end=int(NOW//300*300-300); vals=[(100,102,98,101,10),(101,103,99,102,10),(102,104,100,103,10),(103,104,95,99,volume)]
   return {"list":[[str((end-(3-i)*300)*1000),str(o),str(h),str(l),str(c),str(v)] for i,(o,h,l,c,v) in enumerate(vals)],"_response_ts":NOW}
  if path=="/v5/market/open-interest": return {"list":[{"openInterest":"100"},{"openInterest":str(oi_last)}],"_response_ts":NOW}
  raise AssertionError(path)
 result=auto.run_once("live",tx,NOW,policy_path=enable_live(tmp_path,monkeypatch),state_dir=state)
 assert len([c for c in calls if c[0]=="POST" and c[1]=="/v5/order/create"])==posts
 assert result.get("action")==("close" if posts else None)
 assert "/v5/market/tickers" not in [c[1] for c in calls]


@pytest.mark.parametrize("status,kind",[("protection_reconciling","protection_repair"),("stop_reconciling","stop"),("protection_reconciling","emergency")])
def test_late_unrelated_manual_exposure_allows_exact_protection_recovery_post(tmp_path,monkeypatch,status,kind):
 state,body=open_files(tmp_path); d=json.loads((state/"executor.json").read_text()); target={"category":"linear","symbol":"AAAUSDT","positionIdx":0,"stopLoss":"100.2"}
 d.update(status=status,exit_intent_type="stop" if kind=="stop" else "protection_repair",repairAttempts=1 if kind=="emergency" else 0)
 d["stopBody" if kind=="stop" else "protectionBody"]=target if kind=="stop" else {**target,"stopLoss":"90","takeProfit":"120"}; (state/"executor.json").write_text(json.dumps(d))
 reads=0; calls=[]; manual={"symbol":"MANUALUSDT","size":"2","side":"Buy","positionIdx":0}
 def tx(path,params=None,method="GET",body=None,private=False):
  nonlocal reads
  calls.append((method,path,params,body))
  if method=="POST": return {}
  if path=="/v5/position/list":
   reads+=1; rows=[{"symbol":"AAAUSDT","size":"1","side":"Buy","positionIdx":0,"entryPrice":"100","stopLoss":"90" if kind=="stop" else "","takeProfit":"120" if kind=="stop" else ""}]
   if reads>=2: rows.append(manual)
   return {"list":rows,"_response_ts":NOW}
  if path=="/v5/order/realtime": return {"list":[],"_response_ts":NOW}
  raise AssertionError(path)
 auto.run_once("live",tx,NOW,policy_path=enable_live(tmp_path,monkeypatch),state_dir=state)
 posts=[c for c in calls if c[0]=="POST"]
 assert manual=={"symbol":"MANUALUSDT","size":"2","side":"Buy","positionIdx":0} and len(posts)==1
 assert posts[0][3]["symbol"]=="AAAUSDT"
 assert json.loads((state/"executor.json").read_text()).get("status") in {status,"close_reconciling"}

@pytest.mark.parametrize("extra",[
 {"symbol":"AAAUSDT","size":"2","side":"Buy","positionIdx":0},
 {"symbol":"AAAUSDT","size":"1","side":"Sell","positionIdx":2},
 {"symbol":"AAAUSDT","size":"1","side":"Buy","positionIdx":0},
 {"symbol":"OTHERUSDT","size":"1","side":"Buy","positionIdx":0,"orderLinkId":"basket-other"},
])
def test_owned_open_position_blocks_candidate_ambiguity_and_other_bot_identity(tmp_path,extra):
 state,body=open_files(tmp_path); durable=json.loads((state/"executor.json").read_text())
 exact={"symbol":"AAAUSDT","size":"1","side":"Buy","positionIdx":0}
 rt=auto.Executor.Runtime(lambda *a,**k:{"list":[exact,extra],"_response_ts":NOW},"live")
 with pytest.raises(auto.FailClosed,match="ownership"): auto._owned_open_position(rt,durable,NOW)

def test_execute_exit_late_race_blocks_stop_and_close_after_durable_intent(tmp_path):
 for action in ({"kind":"stop","stop":100.2},{"kind":"close"}):
  writes=[]; store=auto.StateStore(tmp_path/("s"+action["kind"])); store.data={"state":"OPEN"}
  position={"symbol":"AAAUSDT","size":"1","side":"Buy","positionIdx":0,"stopLoss":"90","takeProfit":"120"}
  def verify():
   assert store.path.exists(); raise auto.FailClosed("position ownership ambiguous")
  with pytest.raises(auto.FailClosed,match="ownership"):
   auto.execute_exit(type("R",(),{"write":lambda self,*x:writes.append(x)})(),store,position,"owned",action,NOW,verify=verify)
  assert writes==[]

def test_execute_exit_stop_requires_immediate_intended_or_tighter_confirmation(tmp_path):
 store=auto.StateStore(tmp_path/"s"); store.data={"state":"OPEN"}; writes=[]
 position={"symbol":"AAAUSDT","size":"1","side":"Buy","positionIdx":0,"stopLoss":"90","takeProfit":"120"}
 result=auto.execute_exit(type("R",(),{"write":lambda self,*x:writes.append(x)})(),store,position,"owned",{"kind":"stop","stop":100.2},NOW,
                          verify=lambda:None,confirm_stop=lambda body:False)
 assert result["status"]=="reconciling" and json.loads(store.path.read_text())["status"]=="stop_reconciling" and len(writes)==1

@pytest.mark.parametrize("key,value",[("starts_at","2026-08-10T00:00:01Z"),("executor_sha256","0"*64)])
def test_durable_policy_rejects_approved_binding_tamper(key,value):
 p=policy(); p[key]=value
 with pytest.raises(auto.Executor.PermanentError): auto.Executor.validate_autonomous_policy(p,auto.canonical_hash(p))
