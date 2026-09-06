import copy, json
from pathlib import Path
import pytest
from bots import autonomous_basket_trader as auto


def base(code="FINAL_SPREAD_EXCEEDED", side="Buy"):
 return {"schema":"nexus.shadow-double-gate","version":1,"identity":"id-1","decision_ts":1000,
  "candidate":{"symbol":"AAAUSDT","side":side},"live":{"decision":"REJECT","reason":code},
  "shadow":{"eligible":True,"reason":"ELIGIBLE","entry":"100","stop_loss":"98","take_profit":"104","qty":"1","rr":"2","modeled_round_trip_cost":"0.2"},
  "observations":{"scan":{},"final":{}},"bindings":{"executor_source_sha256":"a"*64,"policy_sha256":"b"*64},
  "zero_write_proof":{"exchange_writes":0,"risk_reserved":False,"ledger_mutated":False}}


def test_shadow_only_exact_final_codes_and_exact_geometry():
 record=auto.build_double_gate_shadow("id-1","FINAL_SPREAD_EXCEEDED",{"symbol":"AAAUSDT","side":"Buy","stop":"98","tp":"104","zone":[99,101]},
  {"bid":100,"ask":100.1},{"tick":.1,"qty_step":.01,"min_qty":.01,"max_qty":10,"min_notional":1},2,1000,"a"*64,"b"*64)
 assert record["shadow"]=={"eligible":True,"reason":"ELIGIBLE","entry":"100","stop_loss":"98","take_profit":"104","qty":"0.79","rr":"2","modeled_round_trip_cost":"0.5089"}
 assert record["zero_write_proof"]=={"exchange_writes":0,"risk_reserved":False,"ledger_mutated":False}
 assert auto.build_double_gate_shadow("x","STALE_OR_INVALID_WINNER_CHRONOLOGY",{}, {}, {},2,1,"a"*64,"b"*64) is None

@pytest.mark.parametrize("code",["FINAL_FRESHNESS_FAILED","FINAL_FUNDING_EXCEEDED","FINAL_SPREAD_EXCEEDED","FINAL_OI_CHANGE_FAILED","FINAL_BTC_CHANGE_FAILED"])
def test_named_final_market_codes_eligible(code):
 assert auto.is_shadow_final_reason(code)

@pytest.mark.parametrize("blocker",["exposure","ownership","zone","risk","margin","malformed"])
def test_non_final_safety_blockers_remain(blocker):
 kwargs=dict(identity="x",code="FINAL_SPREAD_EXCEEDED",candidate={"symbol":"AAAUSDT","side":"Sell","stop":"102","tp":"96","zone":[99,101]},quote={"bid":99.9,"ask":100},metadata={"tick":.1,"qty_step":.01,"min_qty":.01,"max_qty":10,"min_notional":1},risk_usd=2,decision_ts=1,executor_hash="a"*64,policy_hash="b"*64)
 if blocker=="exposure": kwargs["positions"]=[{"symbol":"AAAUSDT","size":"1"}]
 elif blocker=="ownership": kwargs["orders"]=[{"symbol":"AAAUSDT","orderStatus":"New","orderLinkId":"manual"}]
 elif blocker=="zone": kwargs["candidate"]["zone"]=[101,103]
 elif blocker=="risk": kwargs["risk_usd"]=3
 elif blocker=="margin": kwargs["available_balance"]=0
 else: kwargs["quote"]={"bid":None,"ask":100}
 assert auto.build_double_gate_shadow(**kwargs)["shadow"]["eligible"] is False


def test_append_dedup_crash_and_recursive_redaction(tmp_path):
 p=tmp_path/"shadow.jsonl"; r=base(); r["observations"]["final"]={"nested":{"api_token":"CANARY"}}
 assert auto.append_shadow(p,r); assert not auto.append_shadow(p,r)
 p.write_bytes(p.read_bytes()+b'{"broken"')
 r2=base(); r2["identity"]="id-2"; assert auto.append_shadow(p,r2)
 raw=p.read_text(); assert "CANARY" not in raw and len(raw.splitlines())==2


def test_audit_long_short_same_bar_chronology_and_aggregation():
 records=[base(),base("FINAL_FUNDING_EXCEEDED","Sell"),base("FINAL_SPREAD_EXCEEDED")]
 records[1]["identity"]="id-2"; records[1]["shadow"].update(entry="100",stop_loss="102",take_profit="96")
 records[2]["identity"]="id-3"
 candles={"id-1":[{"ts":1000,"high":200,"low":1},{"ts":1060,"high":104,"low":99}],
  "id-2":[{"ts":1060,"high":103,"low":95}],"id-3":[{"ts":1060,"high":101,"low":99}]}
 report=auto.audit_double_gate_records(records,lambda r,s,e:candles[r["identity"]],2000)
 assert report["outcomes"]=={"TP_FIRST":1,"SL_FIRST":1,"NEITHER":1,"UNVERIFIABLE":0}
 assert report["missed_winners"]==1 and report["avoided_losers"]==1
 assert report["by_reason"]["FINAL_SPREAD_EXCEEDED"]["total"]==2
 assert report["delta_net_r"]==pytest.approx(.7)


def test_historical_or_missing_cost_unverifiable_without_fetch():
 called=[]; old={"identity":"old","candidate":{"symbol":"AAAUSDT","side":"Buy"},"decision_ts":1}
 missing=base(); missing["shadow"]["modeled_round_trip_cost"]=None
 report=auto.audit_double_gate_records([old,missing],lambda *x:called.append(x),2000)
 assert report["outcomes"]["UNVERIFIABLE"]==2 and called==[]


def test_shadow_helpers_never_mutate_inputs():
 candidate={"symbol":"AAAUSDT","side":"Buy","stop":"98","tp":"104","zone":[99,101]}; before=copy.deepcopy(candidate)
 auto.build_double_gate_shadow("x","FINAL_SPREAD_EXCEEDED",candidate,{"bid":100,"ask":101},{"tick":.1,"qty_step":.1,"min_qty":.1,"max_qty":10,"min_notional":1},2,1,"a"*64,"b"*64)
 assert candidate==before
