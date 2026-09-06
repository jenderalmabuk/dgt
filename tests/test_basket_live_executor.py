import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from decimal import Decimal
import urllib.request

P = Path(__file__).parents[1] / "bots" / "basket_live_executor.py"
spec = importlib.util.spec_from_file_location("basket", P)
basket = importlib.util.module_from_spec(spec)
spec.loader.exec_module(basket)
SYMS = ["ZECUSDT", "XAUUSDT", "SKHYNIXUSDT", "PENGUUSDT", "XAGUSDT"]

class _Response:
    def __init__(self,value): self.value=value
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def read(self): return json.dumps(self.value).encode()

def test_bybit_10002_private_get_syncs_time_once_then_retries_with_fresh_timestamp(monkeypatch):
    calls=[]; clocks=iter([10.000,10.001,10.002,20.001])
    monkeypatch.setenv("BYBIT_API_KEY","key"); monkeypatch.setenv("BYBIT_API_SECRET","secret")
    monkeypatch.setattr(basket.time,"time",lambda:next(clocks))
    def urlopen(request,timeout):
        calls.append(request)
        if len(calls)==1: return _Response({"retCode":10002,"retMsg":"timestamp expired"})
        if len(calls)==2: return _Response({"retCode":0,"result":{"timeSecond":"20"}})
        return _Response({"retCode":0,"result":{"list":[]}})
    monkeypatch.setattr(urllib.request,"urlopen",urlopen)
    assert basket.api("/v5/order/realtime",{"category":"linear"},private=True)["list"]==[]
    assert [x.full_url.split("api.bybit.com")[1].split("?")[0] for x in calls]==["/v5/order/realtime","/v5/market/time","/v5/order/realtime"]
    assert calls[2].headers["X-bapi-timestamp"]=="20001"

def test_bybit_10002_post_is_ambiguous_and_never_retried(monkeypatch):
    calls=[]
    monkeypatch.setenv("BYBIT_API_KEY","key"); monkeypatch.setenv("BYBIT_API_SECRET","secret")
    monkeypatch.setattr(basket.time,"time",lambda:10)
    monkeypatch.setattr(urllib.request,"urlopen",lambda request,timeout:(calls.append(request) or _Response({"retCode":10002,"retMsg":"timestamp expired"})))
    with pytest.raises(basket.AmbiguousOrder,match="10002"):
        basket.api("/v5/order/create",method="POST",body={"symbol":"BTCUSDT"},private=True)
    assert len(calls)==1

def test_bybit_10002_private_get_second_failure_is_transient_after_one_retry(monkeypatch):
    calls=[]
    monkeypatch.setenv("BYBIT_API_KEY","key"); monkeypatch.setenv("BYBIT_API_SECRET","secret")
    monkeypatch.setattr(basket.time,"time",lambda:10)
    def urlopen(request,timeout):
        calls.append(request)
        if request.full_url.endswith("/v5/market/time"):
            return _Response({"retCode":0,"result":{"timeSecond":"10"}})
        return _Response({"retCode":10002,"retMsg":"timestamp expired"})
    monkeypatch.setattr(urllib.request,"urlopen",urlopen)
    with pytest.raises(basket.TransientError,match="10002"):
        basket.api("/v5/order/realtime",private=True)
    assert [request.get_method() for request in calls]==["GET","GET","GET"]
    assert sum(request.full_url.endswith("/v5/order/realtime") for request in calls)==2

@pytest.mark.parametrize("timestamp,max_age",[
    (None,30),(True,30),(float("nan"),30),(1002.001,30),(969.999,30),(939.999,60),
])
def test_b2a_response_freshness_rejects_invalid_boundaries(timestamp,max_age):
    envelope={} if timestamp is None else {"_response_ts":timestamp}
    with pytest.raises(basket.TransientError,match="freshness"):
        basket.require_response_fresh(envelope,1000,max_age,"private" if max_age==30 else "metadata")


def test_b2a_response_freshness_accepts_exact_boundaries():
    assert basket.require_response_fresh({"_response_ts":970},1000,30,"private")["_response_ts"]==970
    assert basket.require_response_fresh({"_response_ts":1002},1000,30,"private")["_response_ts"]==1002

def test_recovery_ownership_allows_unrelated_manual_rows():
    body={"symbol":"ZECUSDT","side":"Sell","positionIdx":2,"orderLinkId":"basket-owned"}
    manual_positions=[{"symbol":f"MANUAL{i}USDT","side":"Buy","positionIdx":1,"size":"2"} for i in range(10)]
    manual_orders=[{"symbol":f"ORDER{i}USDT","orderLinkId":f"manual-{i}","orderStatus":"New"} for i in range(20)]
    def tx(path,params=None,method="GET",body=None,private=False):
        rows=[{"symbol":"ZECUSDT","side":"Sell","positionIdx":2,"size":"1"},*manual_positions] if path.endswith("position/list") else manual_orders
        return {"_response_ts":101,"list":rows}
    tx.now=101
    basket._verify_owned(basket.Runtime(tx,"live"),"ZECUSDT",body,1)

@pytest.mark.parametrize("extra",[
    {"symbol":"ZECUSDT","side":"Sell","positionIdx":2,"size":"1"},
    {"symbol":"ZECUSDT","side":"Buy","positionIdx":1,"size":"1"},
    {"symbol":"OTHERUSDT","side":"Buy","positionIdx":1,"size":"1","orderLinkId":"basket-other"},
])
def test_recovery_ownership_blocks_candidate_or_other_bot_position(extra):
    body={"symbol":"ZECUSDT","side":"Sell","positionIdx":2,"orderLinkId":"basket-owned"}
    def tx(path,params=None,method="GET",body=None,private=False):
        return {"_response_ts":101,"list":[{"symbol":"ZECUSDT","side":"Sell","positionIdx":2,"size":"1"},extra] if path.endswith("position/list") else []}
    tx.now=101
    with pytest.raises(basket.PermanentError,match="ownership"): basket._verify_owned(basket.Runtime(tx,"live"),"ZECUSDT",body,1)

FRESH_PLAN_DOC = {
    "schema":"nexus.basket-plan", "version":1,
    "generated_at":"2026-08-10T17:21:20Z", "expires_at":"2026-08-10T21:21:20Z",
    "source":"manual-proposal", "provenance":{"proposal_at":"2026-08-10T17:21:20Z"},
    "plans":[
        {"symbol":"BEATUSDT","side":"Sell","zone":["1.9056","1.9581"],"trigger":"1.9056","invalid":"1.9581","stop":"2.0302","tp":"1.5843"},
        {"symbol":"CYSUSDT","side":"Buy","zone":["1.1838","1.2054"],"trigger":"1.2054","invalid":"1.1838","stop":"1.1541","tp":"1.3378"},
        {"symbol":"EPICUSDT","side":"Sell","zone":[".4446",".4494"],"trigger":".4446","invalid":".4494","stop":".4560","tp":".4151"},
        {"symbol":"MUBARAKUSDT","side":"Buy","zone":[".021960",".022318"],"trigger":".022318","invalid":".021960","stop":".021469","tp":".024506"},
        {"symbol":"ZECUSDT","side":"Sell","zone":["496.82","498.04"],"trigger":"496.82","invalid":"498.04","stop":"499.73","tp":"489.31"},
    ]}


def test_dynamic_plan_document_validates_exact_snapshot_and_rejects_stale():
    canonical, plans = basket.validate_plan_document(FRESH_PLAN_DOC, 1786382480)
    assert canonical == FRESH_PLAN_DOC and list(plans) == [p["symbol"] for p in FRESH_PLAN_DOC["plans"]]
    assert plans["MUBARAKUSDT"].stop == Decimal(".021469")
    with pytest.raises(basket.PermanentError, match="stale"):
        basket.validate_plan_document(FRESH_PLAN_DOC, 1786398001)


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(extra=1),
    lambda d: d["plans"].append(dict(d["plans"][0])),
    lambda d: d["plans"][0].update(side="Long"),
    lambda d: d["plans"][0].update(stop="nan"),
    lambda d: d["plans"][0].update(tp="2"),
])
def test_dynamic_plan_document_rejects_noncanonical_or_unsafe(mutate):
    doc=json.loads(json.dumps(FRESH_PLAN_DOC)); mutate(doc)
    with pytest.raises(basket.PermanentError): basket.validate_plan_document(doc,1786380000)


def test_dynamic_approval_binds_document_and_executor_source():
    now=1786382480
    document,_=basket.validate_plan_document(FRESH_PLAN_DOC,now)
    approval=basket.make_approval("dynamic",now,60,document)
    basket.validate_approval(approval,now,document)
    assert approval["executorSourceSha256"] == basket.executor_source_sha256()
    changed=json.loads(json.dumps(document)); changed["source"]="changed"
    with pytest.raises(basket.PermanentError,match="fingerprint"):
        basket.validate_approval(approval,now,changed)


def fresh(symbol, price, close, ts=1000):
    return {"symbol": symbol, "price": price, "bid": price, "ask": price,
            "ticker_ts": ts, "response_ts": ts, "m5_close": close, "m5_ts": ts,
            "m15_close": close, "m15_ts": ts,
            "oi_change": 0, "oi_ts": ts, "btc_change": 0, "btc_ts": ts,
            "funding": 0, "spread": .01}


class FinalSnapshotTransport:
    def __init__(self, btc_rows):
        self.btc_rows=btc_rows; self.now=1786153484; self.writes=[]
    def __call__(self,path,params=None,method="GET",body=None,private=False):
        if method != "GET": self.writes.append((path,body)); raise AssertionError("exchange write")
        symbol=(params or {}).get("symbol")
        if path.endswith("kline"):
            interval=int(params["interval"])
            rows=self.btc_rows if symbol=="BTCUSDT" else [
                [str((self.now-interval*60)*1000),".002698",".00270",".00269",".002698","10"],
                [str((self.now-interval*120)*1000),".002697",".00270",".00269",".002697","10"]]
            return {"_response_ts":self.now,"list":rows}
        if path.endswith("open-interest"):
            return {"_response_ts":self.now,"list":[{"timestamp":str((self.now-7200)*1000),"openInterest":"100"},{"timestamp":str(self.now*1000),"openInterest":"101"}]}
        if path.endswith("tickers"):
            return {"_response_ts":self.now,"list":[{"lastPrice":".002698","bid1Price":".002698","ask1Price":".002699","fundingRate":"0"}]}
        raise AssertionError(path)


def test_final_snapshot_refetch_uses_fresh_0100_btc_reference_not_stale_scan_btc():
    tx=FinalSnapshotTransport([
        [str(1786150800*1000),"100","102","99","101","10"],
        [str(1786147200*1000),"99","101","98","100","10"]])
    stale_scan={"btc_ts":1786147200,"btc_change":-9}
    final=basket.fetch_snapshot(basket.Runtime(tx,"live"),"1000PEPEUSDT",tx.now)
    gate=basket.final_market_diagnostic(final,tx.now,stale_scan)
    assert gate["ok"] is True and final["btc_ts"]==1786150800
    assert gate["observations"]["scan"]==stale_scan
    assert tx.writes==[]


def test_fetch_snapshot_accepts_latest_btc_reference_boundary_at_0144():
    tx=FinalSnapshotTransport([
        [str(1786150800*1000),"100","102","99","101","10"],
        [str(1786147200*1000),"99","101","98","100","10"]])
    assert basket.fetch_snapshot(basket.Runtime(tx,"live"),"1000PEPEUSDT",tx.now)["btc_ts"]==1786150800


def test_fetch_snapshot_rejects_btc_reference_before_latest_boundary_at_0144():
    tx=FinalSnapshotTransport([
        [str(1786147200*1000),"99","101","98","100","10"],
        [str(1786143600*1000),"98","100","97","99","10"]])
    with pytest.raises(basket.PermanentError,match="reference boundary"):
        basket.fetch_snapshot(basket.Runtime(tx,"live"),"1000PEPEUSDT",tx.now)


def test_fetch_snapshot_rejects_malformed_btc_reference():
    tx=FinalSnapshotTransport([
        [str(1786150800*1000),"bad","102","99","101","10"],
        [str(1786147200*1000),"99","101","98","100","10"]])
    with pytest.raises(basket.PermanentError,match="invalid candles"):
        basket.fetch_snapshot(basket.Runtime(tx,"live"),"1000PEPEUSDT",tx.now)


@pytest.mark.parametrize("btc_rows", [
    [[str(1786147200*1000),"99","101","98","100","10"],[str(1786143600*1000),"98","100","97","99","10"]],
    [[str(1786150800*1000),"bad","102","99","101","10"]],
])
def test_final_snapshot_refetch_stale_or_malformed_btc_fails_closed_without_write(tmp_path,btc_rows):
    tx=FinalSnapshotTransport(btc_rows)
    try:
        final=basket.fetch_snapshot(basket.Runtime(tx,"live"),"1000PEPEUSDT",tx.now)
    except basket.PermanentError:
        pass
    else:
        gate=basket.final_market_diagnostic(final,tx.now)
        assert gate["ok"] is False and gate["gate"]=="btc_ts" and gate["threshold"]==3660
    assert tx.writes==[] and not (tmp_path/"reservation.json").exists()


@pytest.mark.parametrize("mutate,code,field,operator,threshold", [
    (lambda d: d.update(bid=0), "FINAL_BID_ASK_INVALID", "bid_ask", "0 < bid <= ask", None),
    (lambda d: d.update(response_ts=939), "FINAL_RESPONSE_STALE", "response_ts", "age <=", 60),
    (lambda d: d.update(ticker_ts=939), "FINAL_TICKER_STALE", "ticker_ts", "age <=", 60),
    (lambda d: d.update(spread=.081), "FINAL_SPREAD_EXCEEDED", "spread", "<=", .08),
    (lambda d: d.update(funding=.00051), "FINAL_FUNDING_EXCEEDED", "abs(funding)", "<=", .0005),
    (lambda d: d.update(oi_change=-3.01), "FINAL_OI_CHANGE_BELOW_MIN", "oi_change", ">=", -3),
    (lambda d: d.update(btc_change=-1.51), "FINAL_BTC_CHANGE_BELOW_MIN", "btc_change", ">=", -1.5),
    (lambda d: d.update(m5_ts=639), "FINAL_CANDLE_STALE_OR_INVALID", "m5_ts", "age <=", 360),
])
def test_final_market_diagnostic_exact_reason_and_gate_evidence(mutate,code,field,operator,threshold):
    data=fresh("PENGUUSDT",.00664,.00664); data["source_response_ts"]=1000; mutate(data)
    result=basket.final_market_diagnostic(data,1000)
    assert result["ok"] is False and result["code"]==code
    assert result["gate"]==field and result["operator"]==operator and result["threshold"]==threshold
    assert result["observations"]["bid"]==data["bid"] and result["observations"]["ask"]==data["ask"]


def test_final_market_diagnostic_first_failure_is_deterministic():
    data=fresh("PENGUUSDT",.00664,.00664); data.update(source_response_ts=0,bid=0,spread=1,funding=1)
    assert basket.final_market_diagnostic(data,1000)["code"]=="FINAL_BID_ASK_INVALID"


def test_final_zone_or_invalidation_diagnostic_contains_scan_snapshot():
    data=fresh("PENGUUSDT",.007,.007); data["source_response_ts"]=1000
    result=basket.final_entry_diagnostic(basket.PLANS["PENGUUSDT"],data,1000,scan_snapshot={"bid":.00664})
    assert result["code"]=="FINAL_ZONE_OR_INVALIDATION_FAILED"
    assert result["observations"]["scan"]=={"bid":.00664}


def identity(link="L", qty="1", price=".00664"):
    return {"orderLinkId":link,"symbol":"PENGUUSDT","side":"Buy","positionIdx":0,
            "qty":qty,"price":price,"orderType":"Limit"}


def test_approved_plans_exact():
    assert list(basket.PLANS) == SYMS
    expected = {
        "ZECUSDT": ("Sell", (509.05, 509.80), 509.05, 510.45, 510.83, 504.60),
        "XAUUSDT": ("Buy", (4345.41, 4348.53), 4348.53, 4342.69, 4341.14, 4367),
        "SKHYNIXUSDT": ("Sell", (1010.37, 1013.22), 1010.37, 1015.71, 1017.14, 993.45),
        "PENGUUSDT": ("Buy", (.006630, .006651), .006651, .006612, .006602, .006773),
        "XAGUSDT": ("Buy", (64.09, 64.18), 64.18, 64.02, 63.98, 64.68),
    }
    for symbol, values in expected.items():
        p = basket.PLANS[symbol]
        assert (p.side, p.zone, p.trigger, p.invalid, p.stop, p.tp) == values
    assert basket.EXPIRY == 4 * 3600


def test_load_or_create_resumes_and_terminal_state_never_reorders(tmp_path):
    path = tmp_path / "state.json"
    original = basket.new_state(900)
    original.update(status="submitted", winner="PENGUUSDT", orderLinkId="stable")
    basket.save_state(original, path)
    resumed = basket.load_or_create_state(900, path)
    assert resumed == original
    data = {"ZECUSDT": fresh("ZECUSDT", 509.40, 508.90, 800)}
    assert basket.select_candidate(resumed, data, 1000) is None
    assert resumed["winner"] == "PENGUUSDT"


def test_first_valid_uses_trigger_candle_timestamp_not_plan_order():
    s = basket.new_state(900)
    s["touched"].update({"ZECUSDT": 900, "PENGUUSDT": 900})
    data = {"ZECUSDT": fresh("ZECUSDT", 508.90, 508.90, 990),
            "PENGUUSDT": fresh("PENGUUSDT", .00666, .00666, 980)}
    assert basket.select_candidate(s, data, 1000) == "PENGUUSDT"
    assert s["trigger_ts"] == 980


def test_equal_trigger_timestamp_has_stable_symbol_tiebreak():
    s = basket.new_state(900); s["touched"].update({"ZECUSDT": 999, "PENGUUSDT": 999})
    data = {"PENGUUSDT": fresh("PENGUUSDT", .00666, .00666),
            "ZECUSDT": fresh("ZECUSDT", 508.90, 508.90)}
    assert basket.select_candidate(s, data, 1000) == "PENGUUSDT"


@pytest.mark.parametrize("field", ["ticker_ts", "response_ts", "m5_ts", "m15_ts", "oi_ts", "btc_ts"])
def test_missing_or_stale_timestamp_fails_closed(field):
    d = fresh("PENGUUSDT", .0700, .0700)
    d[field] = None
    assert not basket.data_fresh(d, 1000)
    limits={"ticker_ts":61,"response_ts":61,"m5_ts":361,"m15_ts":961,"oi_ts":7201,"btc_ts":3661}
    d[field] = 1000-limits[field]
    assert not basket.data_fresh(d, 1000)


def test_invalid_uses_m15_only_when_plan_has_rule():
    assert basket.is_invalid(basket.PLANS["ZECUSDT"], 510.46)
    assert basket.is_invalid(basket.PLANS["PENGUUSDT"], .006611)
    assert not basket.is_invalid(basket.PLANS["SKHYNIXUSDT"], 1015.70)


def test_metadata_preflight_and_payload_risk_cap():
    meta = {"tick": .000001, "qty_step": 1, "min_qty": 1, "min_notional": 5}
    body = basket.build_payload("PENGUUSDT", .00664, meta, "link", 1)
    assert body == {"category":"linear", "symbol":"PENGUUSDT", "side":"Buy",
        "orderType":"Limit", "qty":"22590", "price":"0.00664", "timeInForce":"PostOnly",
        "positionIdx":1, "takeProfit":"0.006773", "stopLoss":"0.006602",
        "tpTriggerBy":"MarkPrice", "slTriggerBy":"MarkPrice", "orderLinkId":"link"}
    assert basket.modeled_loss(basket.PLANS["PENGUUSDT"], 27838, .00664) <= 2
    assert basket.modeled_loss(basket.PLANS["PENGUUSDT"], 27839, .00664) > 2
    with pytest.raises(basket.PermanentError):
        basket.build_payload("PENGUUSDT", .00664, {**meta, "min_notional": 200}, "x", 1)


def test_live_capability_requires_atomic_single_tp_sl_payload():
    assert basket.SUPPORTS_ATOMIC_TP_SL is True
    approval = basket.make_approval("n", 100, 60)
    basket.validate_mode("live", approval, "n:" + approval["fingerprint"], 100)
    body = basket.build_payload("PENGUUSDT", .00664,
        {"tick":.000001,"qty_step":1,"min_qty":1,"min_notional":1}, "L", 0)
    assert body["takeProfit"] == "0.006773" and body["stopLoss"] == "0.006602"


def test_position_mode_controls_payload_and_arming():
    assert basket.position_index("MergedSingle", "Buy") == 0
    assert basket.position_index("BothSide", "Buy") == 1
    assert basket.position_index("BothSide", "Sell") == 2
    with pytest.raises(basket.PermanentError): basket.position_index("unknown", "Buy")


def test_account_clear_allows_unrelated_manual_and_blocks_known_candidate():
    calls = []
    def api(path, params=None, **_):
        calls.append((path, params))
        return {"_response_ts":101,"list":[{"symbol":"SKHYNIXUSDT","size":"1"}] if path.endswith("position/list") else []}
    assert basket.basket_account_clear(api)
    assert not basket.basket_account_clear(api,"SKHYNIXUSDT")
    assert all(params.get("settleCoin")=="USDT" for _,params in calls)


def test_final_create_allows_unrelated_manual_exposure_and_preserves_rows():
    manual_position={"symbol":"ETHUSDT","size":"2","side":"Buy","positionIdx":0}
    manual_order={"symbol":"BTCUSDT","orderStatus":"New","orderLinkId":"manual-42","qty":".1"}
    posts=[]
    def tx(path,params=None,method="GET",body=None,private=False):
        if method=="POST": posts.append((path,body)); return {"orderId":"new"}
        if path.endswith("position/list"): return {"_response_ts":basket.time.time(),"list":[manual_position]}
        if path.endswith("order/realtime"): return {"_response_ts":basket.time.time(),"list":[manual_order]}
        raise AssertionError(path)
    body={"symbol":"DOGEUSDT","orderLinkId":"basket-n-DOGEUSDT"}
    assert basket._create_if_account_clear(basket.Runtime(tx,"live"),body)=={"orderId":"new"}
    assert posts==[("/v5/order/create",body)]
    assert manual_position=={"symbol":"ETHUSDT","size":"2","side":"Buy","positionIdx":0}
    assert manual_order["orderLinkId"]=="manual-42"


@pytest.mark.parametrize("positions,orders",[
    ([{"symbol":"DOGEUSDT","size":"1","side":"Buy"}],[]),
    ([],[{"symbol":"DOGEUSDT","orderStatus":"New","orderLinkId":"manual-42"}]),
    ([],[{"symbol":"ETHUSDT","orderStatus":"New","orderLinkId":"basket-old-ETHUSDT"}]),
    ([],[{"symbol":"ETHUSDT","orderStatus":"New","orderLinkId":"auto-old-ETHUSDT"}]),
])
def test_final_create_blocks_candidate_or_nonmanual_active_exposure(positions,orders):
    posts=[]
    def tx(path,params=None,method="GET",body=None,private=False):
        if method=="POST": posts.append((path,body)); return {}
        return {"_response_ts":basket.time.time(),"list":positions if path.endswith("position/list") else orders}
    with pytest.raises(basket.PermanentError,match="exposure"):
        basket._create_if_account_clear(basket.Runtime(tx,"live"),{"symbol":"DOGEUSDT","orderLinkId":"basket-n-DOGEUSDT"})
    assert posts==[]


def test_order_link_persisted_before_create_and_ambiguous_reconciled(tmp_path):
    path = tmp_path / "s.json"; s = basket.new_state(900); events = []
    def save(st, p):
        events.append(("save", st.get("orderLinkId"))); basket.save_state(st, p)
    def create(_body): events.append(("create", None)); raise TimeoutError("unknown outcome")
    def lookup(link): events.append(("lookup", link)); return {"orderId":"oid", "orderStatus":"New"}
    result = basket.submit_once(s, path, {"orderLinkId":"stable"}, create, lookup, save=save)
    assert events[:2] == [("save", "stable"), ("create", None)]
    assert result["orderId"] == "oid" and s["status"] == "submitted"


def test_ambiguous_unresolved_blocks_other_candidate(tmp_path):
    s = basket.new_state(900)
    with pytest.raises(basket.AmbiguousOrder):
        basket.submit_once(s, tmp_path/"s", {"orderLinkId":"stable"},
                           Mock(side_effect=TimeoutError()), Mock(return_value=None))
    assert s["status"] == "reconciling" and s["winner"] == "reconcile"
    assert basket.select_candidate(s, {}, 1000) is None


def test_permanent_rejection_falls_back_but_transient_has_separate_bound(tmp_path):
    s = basket.new_state(900); s["candidate"] = "ZECUSDT"
    basket.record_failure(s, "ZECUSDT", basket.PermanentError("110126"), max_transient=2)
    assert "ZECUSDT" in s["ineligible"] and s["candidate"] is None
    s["candidate"] = "PENGUUSDT"
    basket.record_failure(s, "PENGUUSDT", basket.TransientError("busy"), max_transient=2)
    assert "PENGUUSDT" not in s["ineligible"] and s["transient_attempts"]["PENGUUSDT"] == 1
    basket.record_failure(s, "PENGUUSDT", basket.TransientError("busy"), max_transient=2)
    assert "PENGUUSDT" in s["ineligible"]


def test_global_lock_is_atomic(tmp_path):
    lock = tmp_path / "lock"
    with basket.global_lock(lock):
        with pytest.raises(basket.LockBusy):
            with basket.global_lock(lock): pass


def test_risk_warning_documents_gap_limit():
    assert "cannot guarantee actual gap loss <=$2" in basket.RISK_WARNING


def test_v3_approval_snapshot_metadata_and_mode():
    now = 10_000
    approval = basket.make_approval("nonce", now, 60)
    basket.validate_approval(approval, now + 59)
    with pytest.raises(basket.PermanentError):
        basket.validate_approval({**approval, "fingerprint": "bad"}, now)
    with pytest.raises(basket.PermanentError): basket.validate_approval(approval, now + 61)
    info = {"priceFilter":{"tickSize":"0.01"}, "lotSizeFilter":{
        "qtyStep":"0.1", "minOrderQty":"0.2", "minNotionalValue":"5"}}
    assert basket.parse_instrument(info) == {"tick":.01,"qty_step":.1,"min_qty":.2,"min_notional":5.0}
    assert basket.detect_position_mode([{"positionIdx":0}]) == "MergedSingle"
    assert basket.detect_position_mode([{"positionIdx":1},{"positionIdx":2}]) == "BothSide"


def test_v3_closed_series_and_snapshot_fail_closed():
    now = 3600
    rows = [["3000","1","2",".5","1.5","3","4"], ["3300","1","2",".5","1.6","3","4"]]
    assert basket.parse_closed_candles(rows, 300, now)[-1]["ts"] == 3300
    with pytest.raises(basket.PermanentError): basket.parse_closed_candles(rows[1:], 300, now)
    with pytest.raises(basket.PermanentError): basket.parse_closed_candles([["3500","1","2",".5","1.5"]], 300, now)


def test_v3_dry_run_forbids_writes_and_live_confirm_exact():
    transport = Mock()
    rt = basket.Runtime(transport, mode="dry-run")
    with pytest.raises(basket.PermanentError): rt.write("/v5/order/create", {})
    transport.assert_not_called()
    approval = basket.make_approval("random-nonce", 100, 100)
    basket.validate_mode("live", approval, "random-nonce:" + approval["fingerprint"], 101)
    with pytest.raises(basket.PermanentError): basket.validate_mode("live", approval, "wrong", 101)


def test_v3_lifecycle_partial_missing_protection_emergency(tmp_path):
    calls=[]
    def write(path, body): calls.append((path, body)); return {}
    states = iter([
        {**identity(),"orderStatus":"PartiallyFilled","cumExecQty":"2","leavesQty":"1"},
        {"size":"2","stopLoss":"","takeProfit":""},
        {"size":"2","stopLoss":"","takeProfit":""}])
    events=[]
    result = basket.monitor_order("PENGUUSDT", basket.PLANS["PENGUUSDT"], "L", next(states),
        lambda: next(states), write, events.append, stale=True, position_idx=0, durable_body=identity())
    assert result == "cancelling"
    assert [x[0] for x in calls] == ["/v5/order/cancel"]


def test_v3_stale_lock_recovery_requires_dead_pid_and_reconciliation(tmp_path):
    lock=tmp_path/"x.lock"; lock.write_text("99999999")
    with basket.recoverable_lock(lock, reconciled=lambda: True): assert lock.exists()
    lock.write_text(str(__import__('os').getpid()))
    with pytest.raises(basket.LockBusy):
        with basket.recoverable_lock(lock, reconciled=lambda: True): pass


def _market_transport(now=7200):
    calls = []
    def transport(path, params=None, **kwargs):
        calls.append((path, params, kwargs))
        interval = (params or {}).get("interval")
        if path == "/v5/market/kline":
            step = int(interval) * 60
            end = now - now % step
            closes = [".0700", ".0698", ".0696"] if params.get("symbol") != "BTCUSDT" else ["100", "99", "98"]
            rows = [[str((end-i*step)*1000),"1","2",".5",closes[i],"3","4"] for i in range(3)]
            return {"_response_ts":101,"list": rows, "_response_ts": now}
        if path == "/v5/market/open-interest":
            return {"_response_ts":101,"list":[{"timestamp":str((now-7200)*1000),"openInterest":"100"},
                            {"timestamp":str(now*1000),"openInterest":"110"}], "_response_ts":now}
        if path == "/v5/market/tickers":
            return {"_response_ts":101,"list":[{"lastPrice":".0700","bid1Price":".06993","ask1Price":".07007",
                             "fundingRate":"0.0001"}], "_response_ts":now}
        raise AssertionError(path)
    return transport, calls


def test_fetch_snapshot_uses_response_time_and_last_closed_seconds():
    transport, calls = _market_transport()
    snap = basket.fetch_snapshot(basket.Runtime(transport), "PENGUUSDT", 7200)
    assert snap["response_ts"] == snap["ticker_ts"] == 7200
    assert snap["m5_ts"] == 6900 and snap["m15_ts"] == 6300
    assert snap["oi_change"] == pytest.approx(10) and snap["oi_ts"] == 7200
    assert snap["btc_ts"] == 3600
    assert snap["spread"] == pytest.approx(.2)
    assert all(path not in basket.WRITE_PATHS for path, _, _ in calls)


def test_preflight_reads_all_metadata_and_rejects_exposure_and_expired_approval():
    calls=[]
    def transport(path, params=None, **kwargs):
        calls.append((path, params.get("symbol")))
        if path.endswith("instruments-info"):
            return {"_response_ts":101,"list":[{"priceFilter":{"tickSize":".01"},"lotSizeFilter":{
                "qtyStep":"1","minOrderQty":"1","minNotionalValue":"1"}}]}
        if path.endswith("position/list"): return {"_response_ts":101,"list":[{"positionIdx":0,"size":"0"}]}
        return {"_response_ts":101,"list":[]}
    approval=basket.make_approval("n",100,10)
    with pytest.raises(basket.PermanentError, match="expired"):
        basket.preflight_readonly(basket.Runtime(transport), "live", approval, "n:"+approval["fingerprint"], 111)
    assert {s for p,s in calls if p.endswith("instruments-info")} == set(SYMS)


def test_reconcile_startup_exact_link_and_ambiguity(tmp_path):
    state=basket.new_state(1); state.update(status="submitting",candidate="PENGUUSDT",orderLinkId="L")
    path=tmp_path/"s.json"; basket.save_state(state,path)
    def transport(endpoint, params=None, **_):
        if endpoint.endswith("order/realtime"):
            return {"_response_ts":101,"list":[{"symbol":"PENGUUSDT","orderLinkId":"L","orderId":"O","orderStatus":"New"}]}
        if endpoint.endswith("position/list"): return {"_response_ts":101,"list":[{"symbol":params["symbol"],"size":"0","positionIdx":0}]}
        raise AssertionError(endpoint)
    resolved=basket.reconcile_state(basket.Runtime(transport),state,path)
    assert resolved["status"] == "submitted" and resolved["winner"] == "PENGUUSDT"
    def ambiguous(endpoint, params=None, **_):
        if endpoint.endswith("order/realtime"): return {"_response_ts":101,"list":[]}
        return {"_response_ts":101,"list":[{"symbol":"PENGUUSDT","size":"1","positionIdx":0}]}
    with pytest.raises(basket.AmbiguousOrder): basket.reconcile_state(basket.Runtime(ambiguous),state,path)


def test_run_cycle_dry_run_has_no_write_paths(tmp_path):
    transport,calls=_market_transport()
    rt=basket.Runtime(transport)
    state=basket.new_state(1)
    result=basket.run_cycle(rt,state,7200,state_path=tmp_path/"s",symbols=["PENGUUSDT"])
    assert result["mode"] == "dry-run" and "snapshots" in result
    assert not any(path in basket.WRITE_PATHS for path,_,_ in calls)


def test_live_injected_lifecycle_persists_and_notifies(tmp_path):
    transport,_=_market_transport()
    rt=basket.Runtime(transport, mode="live")
    state=basket.new_state(7000); state["touched"]["PENGUUSDT"]=7199
    approval=basket.make_approval("nonce",7100,200)
    events=[]; writes=[]
    result=basket.run_cycle(rt,state,7200,state_path=tmp_path/"s",symbols=["PENGUUSDT"],
        approval=approval,confirmation="nonce:"+approval["fingerprint"],metadata={
        "PENGUUSDT":{"tick":.00000001,"qty_step":1,"min_qty":1,"min_notional":1}},
        position_mode="MergedSingle", write=lambda p,b: writes.append((p,b)) or {"orderId":"O"},
        lookup=lambda _:{"orderId":"O","orderStatus":"New"}, notify=events.append,
        lock_path=tmp_path/"lock", snapshot_fetch=lambda *_:{**fresh("PENGUUSDT",.00666,.00666,7200),
                                                             "bid":.00664,"ask":.006641})
    assert result["status"] == "submitted"
    assert [p for p,_ in writes] == ["/v5/order/create"]
    assert "submitted:PENGUUSDT" in events
    assert basket.load_state(tmp_path/"s")["orderId"] == "O"


def test_monitor_poll_timeout_persists_and_alerts(tmp_path):
    state=basket.new_state(1); state.update(status="submitted",winner="PENGUUSDT",orderLinkId="L",body=identity())
    events=[]; writes=[]
    orders=iter([{**identity(),"orderStatus":"New","cumExecQty":"0","leavesQty":"1"},{**identity(),"orderStatus":"New","cumExecQty":"0","leavesQty":"1"},{**identity(),"orderStatus":"Cancelled","cumExecQty":"0","leavesQty":"0"}])
    result=basket.poll_order(state,tmp_path/"s",lambda _:next(orders),
        lambda: {},lambda p,b:writes.append((p,b)),events.append,now=lambda:10,sleep=lambda _:None,timeout=0)
    assert result == "cancelled"
    assert [p for p,_ in writes] == ["/v5/order/cancel"]
    assert basket.load_state(tmp_path/"s")["status"] == "cancelled"


def test_timeframe_aware_freshness_boundaries():
    d = fresh("PENGUUSDT", .07, .07, 10_000)
    d.update(m5_ts=9640, m15_ts=9040, oi_ts=2800, btc_ts=6340)
    assert basket.data_fresh(d, 10_000)
    for field, age in (("ticker_ts",61),("response_ts",61),("m5_ts",361),
                       ("m15_ts",961),("oi_ts",7201),("btc_ts",3661)):
        bad = dict(d); bad[field] = 10_000-age
        assert not basket.data_fresh(bad, 10_000)


def test_approval_time_and_state_binding(tmp_path):
    now = 20_000; a = basket.make_approval("n", now, 60)
    basket.validate_approval(a, now)
    for bad in ({**a,"issued_at":now+1}, {**a,"issued_at":now-301},
                {**a,"expires_at":now+basket.EXPIRY+1}):
        with pytest.raises(basket.PermanentError): basket.validate_approval(bad, now)
    path=tmp_path/"state"; state=basket.load_or_create_state(a,path)
    assert state["approved"] == a
    with pytest.raises(basket.PermanentError, match="approval state mismatch"):
        basket.load_or_create_state({**a,"nonce":"other"},path)


def test_dry_run_does_not_write_or_mutate_state(tmp_path):
    rt=basket.Runtime(_market_transport()[0]); state=basket.new_state(None)
    before=json.loads(json.dumps(state)); path=tmp_path/"production-state"
    basket.run_cycle(rt,state,7200,state_path=path,symbols=["PENGUUSDT"])
    assert state == before and not path.exists()


@pytest.mark.parametrize("symbol,bid,ask,expected", [
    ("PENGUUSDT",.00664,.006641,.00664), ("ZECUSDT",508.90,509.40,509.40)])
def test_maker_entry_uses_fresh_side_quote_and_approved_zone(symbol,bid,ask,expected):
    assert basket.maker_entry(symbol,{"bid":bid,"ask":ask}) == expected
    plan=basket.PLANS[symbol]
    bad = plan.zone[1]+.01 if plan.side == "Buy" else plan.zone[0]-.01
    with pytest.raises(basket.PermanentError):
        basket.maker_entry(symbol,{"bid":bad,"ask":bad})


def test_timeout_cancels_then_confirms(tmp_path):
    state=basket.new_state(None); state.update(status="submitted",winner="PENGUUSDT",orderLinkId="L",body=identity())
    orders=iter([{**identity(),"orderStatus":"New","cumExecQty":"0","leavesQty":"1"},
                 {**identity(),"orderStatus":"New","cumExecQty":"0","leavesQty":"1"},
                 {**identity(),"orderStatus":"Cancelled","cumExecQty":"0","leavesQty":"0"}])
    writes=[]; clock=iter([0,1,2,3,4,5])
    result=basket.poll_order(state,tmp_path/"s",lambda _:next(orders),lambda:{},
        lambda p,b:writes.append((p,b)),lambda _:None,now=lambda:next(clock),sleep=lambda _:None,timeout=1)
    assert result == "cancelled"
    assert [p for p,_ in writes] == ["/v5/order/cancel"]
    assert basket.load_state(tmp_path/"s")["status"] == "cancelled"


class FakeLive:
    mode = "live"
    def __init__(self, orders, positions, reject=None, race=False):
        self.orders=iter(orders); self.positions=iter(positions); self.calls=[]
        self.reject=reject; self.race=race; self.last_order={}
    def wall_clock(self): return 101
    def read(self,path,params=None,private=False):
        self.calls.append(("GET",path,dict(params or {})))
        if path.endswith("instruments-info"):
            return {"_response_ts":101,"list":[{"priceFilter":{"tickSize":".00000001"},"lotSizeFilter":{"qtyStep":"1","minOrderQty":"1","minNotionalValue":"1"}}]}
        if path.endswith("wallet-balance"): return {"_response_ts":101,"list":[{"totalAvailableBalance":"1000"}]}
        if path.endswith("position/list"):
            if (params or {}).get("settleCoin"):
                rows=[]
                for _ in SYMS:
                    try: rows.append(next(self.positions))
                    except StopIteration: break
                return {"_response_ts":101,"list":rows}
            try: return {"_response_ts":101,"list":[next(self.positions)]}
            except StopIteration: return {"_response_ts":101,"list":[{"symbol":(params or {}).get("symbol"),"positionIdx":0,"size":"0"}]}
        if path.endswith("order/realtime"):
            if self.race and not (params or {}).get("orderLinkId"): return {"_response_ts":101,"list":[{"symbol":"PENGUUSDT","orderStatus":"New","orderLinkId":""}]}
            if not (params or {}).get("orderLinkId"): return {"_response_ts":101,"list":[]}
            try: self.last_order=next(self.orders)
            except StopIteration: pass
            return {"_response_ts":101,"list":[self.last_order] if self.last_order else []}
        raise AssertionError(path)
    def write(self,path,body):
        self.calls.append(("POST",path,dict(body)))
        if path.endswith("order/create") and self.reject: raise basket.PermanentError(self.reject)
        return {"orderId":"O"}


def _legacy_doc():
    rows=[]
    for symbol,p in basket.PLANS.items():
        rows.append({"symbol":symbol,"side":p.side,"zone":[str(p.zone[0]),str(p.zone[1])],
            "trigger":str(p.trigger),"invalid":str(p.zone[0] if p.side=="Buy" else p.zone[1]),
            "stop":str(p.stop),"tp":str(p.tp)})
    return {"schema":"nexus.basket-plan","version":1,"generated_at":"1970-01-01T00:01:00Z",
        "expires_at":"1970-01-01T01:00:00Z","source":"legacy-test","provenance":{},"plans":rows}


def _live_state(tmp_path, status="watching"):
    doc=_legacy_doc(); plans=basket.validate_plan_document(doc,100)[1]
    a=basket.make_approval("n",100,60,doc); s=basket.new_state(a,plans,doc,a["fingerprint"])
    s["executorSourceSha256"]=a["executorSourceSha256"]; s["touched"]["PENGUUSDT"]=100
    s["status"]=status; p=tmp_path/"state"; basket.save_state(s,p)
    return a,s,p


def test_e2e_new_partial_cancel_confirm_exact_protection_terminal(tmp_path):
    a,s,p=_live_state(tmp_path)
    body=identity("basket-n-PENGUUSDT","22590","0.00664")
    orders=[{**body,"orderId":"O","orderStatus":"PartiallyFilled","cumExecQty":"2","leavesQty":"1"},
            {**body,"orderStatus":"PartiallyFilled","cumExecQty":"2","leavesQty":"1"},
            {**body,"orderStatus":"Cancelled","cumExecQty":"2","leavesQty":"0"}]
    positions=[*({"symbol":x,"positionIdx":0,"size":"0"} for x in SYMS),
               *({"symbol":x,"positionIdx":0,"size":"0"} for x in SYMS),
               *({"symbol":x,"side":"Buy" if x=="PENGUUSDT" else "","positionIdx":0,
                  "size":"2" if x=="PENGUUSDT" else "0"} for x in SYMS),
               {"symbol":"PENGUUSDT","side":"Buy","positionIdx":0,"size":"2","stopLoss":".006602","takeProfit":".006773","slTriggerBy":"MarkPrice","tpTriggerBy":"MarkPrice"}]
    rt=FakeLive(orders,positions)
    snap=lambda *_:{**fresh("PENGUUSDT",.00666,.00666,101),"bid":.00664,"ask":.006641}
    result=basket.run_live_session(rt,a,"n:"+a["fingerprint"],101,p,tmp_path/"lock",symbols=["PENGUUSDT"],snapshot_fetch=snap,now_fn=lambda:101,sleep=lambda _:None,timeout=0,plan_document=s["planDocument"])
    assert result["status"]=="filled" and basket.load_state(p)["status"]=="filled"
    assert [x[:2] for x in rt.calls if x[0]=="POST"]==[("POST","/v5/order/create"),("POST","/v5/order/cancel")]


def test_e2e_restart_expired_approval_recovers(tmp_path):
    body={"symbol":"PENGUUSDT","side":"Buy","qty":"2","price":".0694","orderType":"Limit","positionIdx":0,
          "orderLinkId":"L","stopLoss":".006602","takeProfit":".006773"}
    a,s,p=_live_state(tmp_path,"submitted"); s.update(winner="PENGUUSDT",orderLinkId="L",body=body,instrumentMetadata={"tick":.0001}); basket.save_state(s,p)
    rt=FakeLive([{**body,"orderId":"O","orderStatus":"Filled","cumExecQty":"2","leavesQty":"0"}],
                [*({"symbol":x,"positionIdx":0,"size":"0"} for x in SYMS),
                 {"symbol":"PENGUUSDT","side":"Buy","positionIdx":0,"size":"2","stopLoss":".006602","takeProfit":".006773","slTriggerBy":"MarkPrice","tpTriggerBy":"MarkPrice"}])
    assert basket.run_live_session(rt,a,"bad",1000,p,tmp_path/"lock",sleep=lambda _:None)["status"]=="filled"
    assert not [c for c in rt.calls if c[:2]==("POST","/v5/order/create")]


def test_e2e_exposure_race_and_permanent_reject_persist_fatal(tmp_path):
    a,s,p=_live_state(tmp_path); snap=lambda *_:{**fresh("PENGUUSDT",.00664,.00666,101),"bid":.00664,"ask":.006641}
    rt=FakeLive([],[{"symbol":"PENGUUSDT","positionIdx":0,"size":"0"}],race=True)
    with pytest.raises(basket.PermanentError,match="exactly one candidate required|exposure"):
        basket.run_live_session(rt,a,"n:"+a["fingerprint"],101,p,tmp_path/"l",symbols=["PENGUUSDT"],snapshot_fetch=snap,plan_document=s["planDocument"])
    assert not [c for c in rt.calls if c[0]=="POST"]
    a,s,p=_live_state(tmp_path); rt=FakeLive([],[],reject="110126")
    with pytest.raises(basket.PermanentError):
        basket.run_live_session(rt,a,"n:"+a["fingerprint"],101,p,tmp_path/"l2",symbols=["PENGUUSDT"],snapshot_fetch=snap,plan_document=s["planDocument"])
    saved=basket.load_state(p); assert saved["status"]=="fatal" and saved["ineligible"]==["PENGUUSDT"]


def test_e2e_two_orchestrators_lock(tmp_path):
    a,s,p=_live_state(tmp_path); lock=tmp_path/"lock"
    with basket.global_lock(lock):
        with pytest.raises(basket.LockBusy):
            basket.run_live_session(FakeLive([],[]),a,"n:"+a["fingerprint"],101,p,lock,plan_document=s["planDocument"])


def test_payload_tick_geometry_notional_and_margin_guards():
    meta={"tick":.00000001,"qty_step":1,"min_qty":1,"min_notional":1}
    body=basket.build_payload("PENGUUSDT",.00664,meta,"L",0)
    assert body["price"]=="0.00664" and body["stopLoss"]=="0.006602" and body["takeProfit"]=="0.006773"
    assert float(body["qty"])*float(body["price"]) <= 150
    with pytest.raises(basket.PermanentError,match="margin"):
        basket.check_margin(body,0.01)


def test_wrong_nonempty_protection_repairs_then_requires_exact(tmp_path):
    state=basket.new_state(None); state.update(status="submitted",winner="PENGUUSDT",orderLinkId="L",
        body={"symbol":"PENGUUSDT","side":"Buy","positionIdx":0,"stopLoss":".006602","takeProfit":".006773","tickSize":".0001"})
    orders=iter([{"orderStatus":"Filled","cumExecQty":"2","leavesQty":"0"}])
    positions=iter([
        {"symbol":"PENGUUSDT","side":"Buy","positionIdx":0,"size":"2","stopLoss":".0680","takeProfit":".0730","slTriggerBy":"MarkPrice","tpTriggerBy":"MarkPrice"},
        {"symbol":"PENGUUSDT","side":"Buy","positionIdx":0,"size":"2","stopLoss":".006602","takeProfit":".006773","slTriggerBy":"MarkPrice","tpTriggerBy":"MarkPrice"}])
    writes=[]
    result=basket.poll_order(state,tmp_path/"s",lambda _:next(orders),lambda:next(positions),
        lambda p,b:writes.append((p,b)) or {},lambda _:None,sleep=lambda _:None,metadata={"tick":.0001})
    assert result=="filled"
    assert [p for p,_ in writes]==["/v5/position/trading-stop"]


def test_recovery_rejects_manual_or_mixed_exposure_before_write(tmp_path):
    a,s,p=_live_state(tmp_path,"submitted")
    s.update(winner="PENGUUSDT",orderLinkId="L",body={"category":"linear","symbol":"PENGUUSDT","side":"Buy",
        "qty":"2","price":".0694","orderType":"Limit","positionIdx":0,"orderLinkId":"L","stopLoss":".006602","takeProfit":".006773"})
    basket.save_state(s,p)
    rt=FakeLive([{"symbol":"PENGUUSDT","side":"Buy","positionIdx":0,"qty":"2","price":".0694","orderType":"Limit",
        "orderLinkId":"L","orderStatus":"PartiallyFilled","cumExecQty":"1","leavesQty":"1"}],
        [{"symbol":"PENGUUSDT","side":"Buy","positionIdx":0,"size":"2"}])
    events=[]
    with pytest.raises(basket.PermanentError,match="ownership"):
        basket.run_live_session(rt,a,"bad",1000,p,tmp_path/"lock",notify=events.append,sleep=lambda _:None)
    assert not [c for c in rt.calls if c[0]=="POST"]
    assert basket.load_state(p)["status"]=="fatal" and any(x.startswith("fatal:") for x in events)


def test_emergency_close_ambiguous_reconciles_filled_and_zero(tmp_path):
    doc=_legacy_doc(); plans=basket.validate_plan_document(doc,100)[1]
    approval=basket.make_approval("legacy",100,60,doc,"0"*64)
    state=basket.new_state(approval,plans,doc,approval["fingerprint"]); state["executorSourceSha256"]="0"*64
    state.update(status="submitted",winner="PENGUUSDT",orderLinkId="L",
        body={"symbol":"PENGUUSDT","side":"Buy","positionIdx":0,"stopLoss":".006602","takeProfit":".006773"})
    orders={"L":iter([{"orderLinkId":"L","orderStatus":"Filled","cumExecQty":"2","leavesQty":"0"}]),
            "L-close":iter([{"orderLinkId":"L-close","orderStatus":"New"},{"orderLinkId":"L-close","orderStatus":"Filled"}])}
    positions=iter([
        {"symbol":"PENGUUSDT","side":"Buy","positionIdx":0,"size":"2","stopLoss":"","takeProfit":""},
        {"symbol":"PENGUUSDT","side":"Buy","positionIdx":0,"size":"2","stopLoss":"","takeProfit":""},
        {"symbol":"PENGUUSDT","side":"Buy","positionIdx":0,"size":"0"}])
    writes=[]; clock=iter(range(20))
    def write(path,body):
        writes.append((path,dict(body)))
        if path.endswith("order/create"): raise TimeoutError("ambiguous")
        return {}
    with pytest.raises(basket.AmbiguousOrder):
        basket.poll_order(state,tmp_path/"s",lambda link:next(orders[link]),lambda:next(positions),write,
            lambda _:None,now=lambda:next(clock),sleep=lambda _:None,timeout=15,metadata={"tick":.0001})
    saved=basket.load_state(tmp_path/"s")
    assert saved["status"]=="reconciling" and saved["closeOrderLinkId"]=="L-close"
    close={**saved["closeBody"],"orderStatus":"Filled","cumExecQty":"2","leavesQty":"0"}
    rt=FakeLive([close],[{"symbol":x,"positionIdx":0,"size":"0"} for x in SYMS])
    result=basket.run_live_session(rt,None,"",999,tmp_path/"s",tmp_path/"lock",sleep=lambda _:None)
    assert result["status"]==basket.load_state(tmp_path/"s")["status"]=="emergency_closed"


def test_e2e_cancel_late_fill_then_cancelled_protects_final_qty(tmp_path):
    a,s,p=_live_state(tmp_path)
    link="basket-n-PENGUUSDT"
    body=identity(link,"22590","0.00664")
    orders=[{**body,"orderStatus":"New","cumExecQty":"0","leavesQty":"22590"},
            {**body,"orderStatus":"New","cumExecQty":"0","leavesQty":"22590"},
            {**body,"orderStatus":"PartiallyFilled","cumExecQty":"2","leavesQty":"22588"},
            {**body,"orderStatus":"Cancelled","cumExecQty":"3","leavesQty":"0"}]
    positions=[*({"symbol":x,"positionIdx":0,"size":"0"} for x in SYMS),
        *({"symbol":x,"positionIdx":0,"size":"0"} for x in SYMS),
        *({"symbol":x,"positionIdx":0,"size":"0"} for x in SYMS),
        {"symbol":"PENGUUSDT","side":"Buy","positionIdx":0,"size":"3","stopLoss":"","takeProfit":""},
        *({"symbol":x,"side":"Buy" if x=="PENGUUSDT" else "","positionIdx":0,
           "size":"3" if x=="PENGUUSDT" else "0"} for x in SYMS),
        {"symbol":"PENGUUSDT","side":"Buy","positionIdx":0,"size":"3","stopLoss":".006602","takeProfit":".006773","slTriggerBy":"MarkPrice","tpTriggerBy":"MarkPrice"}]
    rt=FakeLive(orders,positions); snap=lambda *_:{**fresh("PENGUUSDT",.00666,.00666,101),"bid":.00664,"ask":.006641}
    result=basket.run_live_session(rt,a,"n:"+a["fingerprint"],101,p,tmp_path/"lock",symbols=["PENGUUSDT"],
        snapshot_fetch=snap,now_fn=lambda:101,sleep=lambda _:None,timeout=0,plan_document=s["planDocument"])
    posts=[c for c in rt.calls if c[0]=="POST"]
    assert result["status"]==basket.load_state(p)["status"]=="filled"
    assert [x[1] for x in posts]==["/v5/order/create","/v5/order/cancel","/v5/position/trading-stop"]


def test_runtime_notifier_stdout_survives_telegram_failure(monkeypatch,capsys):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN","x"); monkeypatch.setenv("TELEGRAM_CHAT_ID","y")
    monkeypatch.setattr(basket.urllib.request,"urlopen",Mock(side_effect=OSError("down")))
    basket.runtime_notifier("fatal:test")
    assert "fatal:test" in capsys.readouterr().out


def test_expired_zero_terminal_and_partial_protects():
    writes=[]
    assert basket.monitor_order("PENGUUSDT",basket.PLANS["PENGUUSDT"],"L",
        {"orderStatus":"Expired","cumExecQty":"0","leavesQty":"0"},lambda:{},lambda p,b:writes.append((p,b)),lambda _:None)=="expired"
    position={"symbol":"PENGUUSDT","side":"Buy","positionIdx":0,"size":"2","stopLoss":".006602","takeProfit":".006773","slTriggerBy":"MarkPrice","tpTriggerBy":"MarkPrice"}
    assert basket.monitor_order("PENGUUSDT",basket.PLANS["PENGUUSDT"],"L",
        {"orderStatus":"Expired","cumExecQty":"2","leavesQty":"0"},lambda:position,lambda p,b:writes.append((p,b)),lambda _:None,tick=.0001)=="filled"
    assert writes==[]


def test_protection_rechecks_basket_ownership_before_write():
    writes=[]
    with pytest.raises(basket.PermanentError,match="ownership"):
        basket.monitor_order("PENGUUSDT",basket.PLANS["PENGUUSDT"],"L",
            {"orderStatus":"Filled","cumExecQty":"2","leavesQty":"0"},
            lambda:{"symbol":"PENGUUSDT","side":"Buy","positionIdx":0,"size":"2"},
            lambda p,b:writes.append((p,b)),lambda _:None,tick=.0001,
            ownership_check=lambda _qty:(_ for _ in ()).throw(basket.PermanentError("ownership changed")))
    assert writes==[]


def test_generic_recovery_lookup_error_persists_reconciling_and_notifies(tmp_path):
    a,s,p=_live_state(tmp_path,"submitted"); s.update(winner="PENGUUSDT",orderLinkId="L",body={"symbol":"PENGUUSDT"},instrumentMetadata={"tick":.0001}); basket.save_state(s,p)
    class Broken:
        mode="live"
        def read(self,*_a,**_k): raise KeyError("parse")
    events=[]
    with pytest.raises(basket.AmbiguousOrder): basket.run_live_session(Broken(),a,"bad",999,p,tmp_path/"lock",notify=events.append)
    saved=basket.load_state(p)
    assert saved["status"] == "reconciling" and saved["exposure_possible"] is True
    assert events and events[-1].startswith("reconciling:")


def test_recovery_uses_durable_exchange_tick(tmp_path):
    a,s,p=_live_state(tmp_path,"submitted")
    body={"symbol":"PENGUUSDT","side":"Buy","qty":"2","price":".0694","orderType":"Limit","positionIdx":0,"orderLinkId":"L","stopLoss":".006602","takeProfit":".006773"}
    s.update(winner="PENGUUSDT",orderLinkId="L",body=body,instrumentMetadata={"tick":.00000001}); basket.save_state(s,p)
    rt=FakeLive([{**body,"orderStatus":"Filled","cumExecQty":"2","leavesQty":"0"}],
        [{"symbol":x,"positionIdx":0,"size":"0"} for x in SYMS]+[{"symbol":"PENGUUSDT","side":"Buy","positionIdx":0,"size":"2","stopLoss":".006602","takeProfit":".006773","slTriggerBy":"MarkPrice","tpTriggerBy":"MarkPrice"}])
    assert basket.run_live_session(rt,a,"bad",999,p,tmp_path/"lock",sleep=lambda _:None)["status"]=="filled"


def test_emergency_restart_reconciles_close_without_resubmit(tmp_path):
    a,s,p=_live_state(tmp_path,"emergency")
    body={"symbol":"PENGUUSDT","side":"Buy","qty":"2","price":".0694","orderType":"Limit","positionIdx":0,"orderLinkId":"L","stopLoss":".006602","takeProfit":".006773"}
    close={"category":"linear","symbol":"PENGUUSDT","side":"Sell","orderType":"Market","qty":"2","reduceOnly":True,"positionIdx":0,"orderLinkId":"L-close"}
    s.update(winner="PENGUUSDT",orderLinkId="L",body=body,closeOrderLinkId="L-close",closeBody=close,instrumentMetadata={"tick":.0001}); basket.save_state(s,p)
    rt=FakeLive([{**close,"orderStatus":"Filled","cumExecQty":"2","leavesQty":"0"}], [{"symbol":x,"positionIdx":0,"size":"0"} for x in SYMS])
    assert basket.run_live_session(rt,a,"bad",999,p,tmp_path/"lock",sleep=lambda _:None)["status"]=="emergency_closed"
    assert not [c for c in rt.calls if c[0]=="POST"]


def test_emergency_basket_mixed_exposure_fatal_no_close_write(tmp_path):
    a,s,p=_live_state(tmp_path,"emergency")
    body={"symbol":"PENGUUSDT","side":"Buy","qty":"2","price":".0694","orderType":"Limit","positionIdx":0,"orderLinkId":"L","stopLoss":".006602","takeProfit":".006773"}
    close={"symbol":"PENGUUSDT","side":"Sell","qty":"2","orderType":"Market","positionIdx":0,"orderLinkId":"L-close"}
    s.update(winner="PENGUUSDT",orderLinkId="L",body=body,closeOrderLinkId="L-close",closeBody=close,instrumentMetadata={"tick":.0001}); basket.save_state(s,p)
    rt=FakeLive([{**close,"orderStatus":"Filled"}], [{"symbol":"PENGUUSDT","side":"Sell","positionIdx":2,"size":"1"}])
    with pytest.raises((basket.PermanentError,basket.AmbiguousOrder),match="ownership|exposure"):
        basket.run_live_session(rt,a,"bad",999,p,tmp_path/"lock",sleep=lambda _:None)
    assert basket.load_state(p)["status"]=="reconciling" and not [c for c in rt.calls if c[0]=="POST"]


def test_fresh_live_wires_basket_ownership_check(tmp_path, monkeypatch):
    a,s,p = _live_state(tmp_path)
    state=s
    seen = {}
    monkeypatch.setattr(basket, "poll_order", lambda *args, **kwargs: seen.setdefault("check", kwargs["ownership_check"]) or "filled")
    rt = FakeLive([{"orderId":"oid","orderStatus":"New","cumExecQty":"0","leavesQty":"1"}],
                  [{"symbol":x,"positionIdx":0,"size":"0"} for x in SYMS])
    snap = lambda *_: {**fresh("PENGUUSDT",.00666,.00666,101),"bid":.00664,"ask":.006641}
    basket.run_live_session(rt,a,"n:"+a["fingerprint"],101,p,tmp_path/"lock",
        symbols=["PENGUUSDT"],snapshot_fetch=snap,now_fn=lambda:101,sleep=lambda _:None,plan_document=s["planDocument"])
    assert callable(seen["check"])
    seen["check"](0)


def test_main_dry_run_creates_no_state_or_lock_files(tmp_path, monkeypatch):
    state_path, lock_path = tmp_path / "state.json", tmp_path / "state.lock"
    monkeypatch.setattr(basket, "STATE_PATH", state_path)
    monkeypatch.setattr(basket, "LOCK_PATH", lock_path)
    monkeypatch.setattr(basket, "preflight_readonly", lambda *_: {"clear": True})
    monkeypatch.setattr(basket, "run_cycle", lambda _rt, state, _now: {"status": state["status"]})
    monkeypatch.setenv("BASKET_EXECUTION_MODE", "dry-run")
    basket.main()
    assert not state_path.exists() and not lock_path.exists() and list(tmp_path.iterdir()) == []


def test_persisted_stale_touch_cannot_trigger_candidate():
    state = basket.new_state(None); state["touched"]["PENGUUSDT"] = 1
    data = {"PENGUUSDT": fresh("PENGUUSDT", .007, .00666, 1000)}
    assert basket.select_candidate(state, data, 1000) is None


def test_payload_loss_uses_exact_rounded_stop():
    body = basket.build_payload("XAGUSDT", 64.18,
        {"tick":.03,"qty_step":.001,"min_qty":.001,"min_notional":.01}, "L", 0)
    loss = basket.modeled_loss(basket.PLANS["XAGUSDT"], float(body["qty"]),
                               float(body["price"]), float(body["stopLoss"]))
    assert loss <= basket.RISK_CAP


def test_frozen_clock_emergency_poll_has_hard_bound(tmp_path):
    state=basket.new_state(None); state.update(status="submitted",winner="PENGUUSDT",orderLinkId="L",body=identity())
    calls=0
    def lookup(link):
        nonlocal calls; calls += 1
        return {"orderLinkId":link,"orderStatus":"Filled" if link=="L" else "New",
                "cumExecQty":"2","leavesQty":"0"}
    with pytest.raises(basket.AmbiguousOrder, match="emergency close unconfirmed"):
        basket.poll_order(state,tmp_path/"s",lookup,
            lambda:{"symbol":"PENGUUSDT","side":"Buy","positionIdx":0,"size":"2"},
            lambda *_:{},lambda _:None,now=lambda:1,sleep=lambda _:None,timeout=2,interval=1,
            metadata={"tick":.0001})
    assert calls <= 8


def test_ambiguous_recovery_remains_reconciling_and_exact(tmp_path):
    a,s,p=_live_state(tmp_path,"submitted")
    body={"symbol":"PENGUUSDT","side":"Buy","qty":"2","price":".00664","orderType":"Limit","positionIdx":0,"orderLinkId":"L"}
    s.update(winner="PENGUUSDT",orderLinkId="L",body=body,instrumentMetadata={"tick":.0001}); basket.save_state(s,p)
    class Missing:
        mode="live"
        def read(self,*args,**kwargs): return {"_response_ts":101,"list":[]}
        def write(self,*_): raise AssertionError("write")
    with pytest.raises(basket.AmbiguousOrder):
        basket.run_live_session(Missing(),a,"bad",999,p,tmp_path/"lock",sleep=lambda _:None)
    saved=basket.load_state(p)
    assert saved["status"] == "reconciling" and saved["orderLinkId"] == "L"
    assert basket.select_candidate(saved, {}, 1000) is None


def test_runtime_notifier_redacts_exception_url(monkeypatch,capsys):
    token="SECRET_TOKEN"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN",token); monkeypatch.setenv("TELEGRAM_CHAT_ID","y")
    url="https://api.telegram.org/bot"+token+"/sendMessage"
    monkeypatch.setattr(basket.urllib.request,"urlopen",Mock(side_effect=OSError(url)))
    basket.runtime_notifier("fatal:test")
    output=capsys.readouterr().out
    assert token not in output and url not in output and "notifier_error" in output


def test_manual_order_identity_blocks_cancel_write():
    writes=[]
    with pytest.raises(basket.PermanentError, match="ownership"):
        basket.monitor_order("PENGUUSDT",basket.PLANS["PENGUUSDT"],"BOT",
            {"symbol":"PENGUUSDT","orderLinkId":"MANUAL","orderStatus":"New","leavesQty":"2"},
            lambda:{},lambda p,b:writes.append((p,b)),lambda _:None,stale=True)
    assert writes == []


def test_cancel_rechecks_complete_durable_identity_immediately_before_write():
    body={"orderLinkId":"L","symbol":"PENGUUSDT","side":"Buy","positionIdx":0,"qty":"2","price":".00664","orderType":"Limit"}
    writes=[]; checks=[]
    basket.monitor_order("PENGUUSDT",basket.PLANS["PENGUUSDT"],"L",{**body,"orderStatus":"New","cumExecQty":"0","leavesQty":"2"},lambda:{},lambda p,b:writes.append((p,b)),lambda _:None,stale=True,durable_body=body,ownership_check=lambda _qty:checks.append("check"))
    assert checks == ["check"] and len(writes) == 1
    for field in body:
        bad={**body,"orderStatus":"New","cumExecQty":"0","leavesQty":"2"}; bad.pop(field)
        with pytest.raises(basket.PermanentError,match="ownership"):
            basket.monitor_order("PENGUUSDT",basket.PLANS["PENGUUSDT"],"L",bad,lambda:{},lambda *_:writes.append("bad"),lambda _:None,stale=True,durable_body=body)
    assert "bad" not in writes


def test_verify_owned_requires_exactly_one_expected_nonzero_position():
    body={"side":"Buy","positionIdx":0}
    class R:
        def __init__(self, rows): self.rows=rows
        def wall_clock(self): return 101
        def read(self,p,params,private=True): return {"_response_ts":101,"list":[] if p.endswith("order/realtime") else sum(self.rows.values(),[])}
    pos={"symbol":"PENGUUSDT","side":"Buy","positionIdx":0,"size":"2"}
    basket._verify_owned(R({"PENGUUSDT":[pos]}),"PENGUUSDT",body,2)
    for rows in ({},{"PENGUUSDT":[pos,pos]},{"PENGUUSDT":[pos],"ZECUSDT":[{"symbol":"ZECUSDT","side":"Sell","positionIdx":0,"size":"1","orderLinkId":"basket-other"}]}):
        with pytest.raises(basket.PermanentError,match="ownership"): basket._verify_owned(R(rows),"PENGUUSDT",body,2)


def test_ambiguous_emergency_create_persists_close_and_raises(tmp_path):
    state=basket.new_state(None); state.update(status="submitted",winner="PENGUUSDT",orderLinkId="L",body={"symbol":"PENGUUSDT","side":"Buy","positionIdx":0})
    def lookup(link): return {"orderLinkId":"L","orderStatus":"Filled","cumExecQty":"2","leavesQty":"0"} if link=="L" else {}
    def write(path,_body):
        if path.endswith("order/create"): raise TimeoutError("unknown")
    with pytest.raises(basket.AmbiguousOrder):
        basket.poll_order(state,tmp_path/"s",lookup,lambda:{"symbol":"PENGUUSDT","side":"Buy","positionIdx":0,"size":"2"},write,lambda _:None,now=lambda:1,sleep=lambda _:None,timeout=1,metadata={"tick":.0001})
    saved=basket.load_state(tmp_path/"s")
    assert saved["status"] in {"emergency_closing","reconciling"} and saved["closeOrderLinkId"]=="L-close"


def test_touch_on_trigger_candle_waits_for_next_closed_candle():
    state=basket.new_state(None)
    assert basket.select_candidate(state,{"PENGUUSDT":fresh("PENGUUSDT",.00664,.00666,1000)},1000) is None
    assert state["touched"]["PENGUUSDT"] == 1000
    assert basket.select_candidate(state,{"PENGUUSDT":fresh("PENGUUSDT",.007,.00666,1300)},1300) == "PENGUUSDT"


def test_final_locked_clear_check_blocks_create_on_new_exposure(tmp_path):
    a,s,p=_live_state(tmp_path)
    class Race(FakeLive):
        def __init__(self):
            super().__init__([], []); self.position_reads=0
        def read(self,path,params=None,private=False):
            if path.endswith("position/list") and (params or {}).get("settleCoin"):
                self.position_reads += 1
                if self.position_reads > 1:
                    self.calls.append(("GET",path,dict(params or {})))
                    return {"_response_ts":101,"list":[{"symbol":params.get("symbol","PENGUUSDT"),"positionIdx":0,"size":"1"}]}
            return super().read(path,params,private)
    rt=Race(); snap=lambda *_:{**fresh("PENGUUSDT",.00666,.00666,101),"bid":.00664,"ask":.006641}
    with pytest.raises(basket.PermanentError,match="exposure"):
        basket.run_live_session(rt,a,"n:"+a["fingerprint"],101,p,tmp_path/"lock",symbols=["PENGUUSDT"],snapshot_fetch=snap,plan_document=s["planDocument"])
    assert not [c for c in rt.calls if c[:2]==("POST","/v5/order/create")]


def test_stale_unfilled_cancel_uses_fresh_exact_order_and_zero_positions():
    body=identity(); writes=[]; checks=[]
    order={**body,"orderStatus":"New","cumExecQty":"0","leavesQty":"2"}
    result=basket.monitor_order("PENGUUSDT",basket.PLANS["PENGUUSDT"],"L",order,
        lambda:{},lambda p,b:writes.append((p,b)),lambda _:None,stale=True,durable_body=body,
        ownership_check=lambda qty:checks.append(qty),cancel_lookup=lambda:order)
    assert result == "cancelling" and checks == [0.0]
    assert [p for p,_ in writes] == ["/v5/order/cancel"]


@pytest.mark.parametrize("changed", [{"orderStatus":"Filled","cumExecQty":"2","leavesQty":"0"},
    {"orderStatus":"PartiallyFilled","cumExecQty":"1","leavesQty":"1"},{"orderLinkId":"OTHER"}])
def test_cancel_second_lookup_race_fails_closed_without_cancel(changed):
    body=identity(); first={**body,"orderStatus":"New","cumExecQty":"0","leavesQty":"2"}
    writes=[]
    with pytest.raises((basket.AmbiguousOrder,basket.PermanentError)):
        basket.monitor_order("PENGUUSDT",basket.PLANS["PENGUUSDT"],"L",first,lambda:{},
            lambda p,b:writes.append((p,b)),lambda _:None,stale=True,durable_body=body,
            ownership_check=lambda _qty:None,cancel_lookup=lambda:{**first,**changed})
    assert writes == []


def test_stale_final_cancel_authorization_writes_zero(tmp_path):
    body=identity(); order={**body,"orderStatus":"New","cumExecQty":"0","leavesQty":"2"}; writes=[]
    state=basket.new_state(None); state.update(status="submitted",winner="PENGUUSDT",orderLinkId="L",body=body)
    def final_order():
        envelope={"_response_ts":69,"list":[order]}
        basket.require_response_fresh(envelope,100,30,"cancel order")
        return envelope["list"][0]
    with pytest.raises(basket.AmbiguousOrder,match="freshness"):
        basket.poll_order(state,tmp_path/"s",lambda _:order,lambda:{},lambda p,b:writes.append((p,b)),
            lambda _:None,now=lambda:100,sleep=lambda _:None,timeout=0,read_retries=0,
            cancel_lookup=final_order)
    assert writes == [] and basket.load_state(tmp_path/"s")["status"] == "reconciling"


def test_stale_final_protection_authorization_writes_zero(tmp_path):
    body=identity(); order={**body,"orderStatus":"Filled","cumExecQty":"2","leavesQty":"0"}; writes=[]
    position={"symbol":"PENGUUSDT","side":"Buy","positionIdx":0,"size":"2"}
    state=basket.new_state(None); state.update(status="submitted",winner="PENGUUSDT",orderLinkId="L",body=body)
    def final_authorization():
        envelope={"_response_ts":69,"list":[order]}
        basket.require_response_fresh(envelope,100,30,"protection order")
        return envelope["list"][0],position
    with pytest.raises(basket.AmbiguousOrder,match="freshness"):
        basket.poll_order(state,tmp_path/"s",lambda _:order,lambda:position,lambda p,b:writes.append((p,b)),
            lambda _:None,now=lambda:100,sleep=lambda _:None,read_retries=0,
            protection_lookup=final_authorization)
    assert writes == [] and basket.load_state(tmp_path/"s")["status"] == "reconciling"


def test_verify_owned_zero_requires_zero_basket_positions():
    body={"side":"Buy","positionIdx":0}
    class R:
        def __init__(self, rows): self.rows=rows
        def read(self,p,params,private=True): return {"_response_ts":101,"list":[] if p.endswith("order/realtime") else sum(self.rows.values(),[])}
        def wall_clock(self): return 101
    basket._verify_owned(R({}),"PENGUUSDT",body,0)
    with pytest.raises(basket.PermanentError,match="ownership"):
        basket._verify_owned(R({"PENGUUSDT":[{"symbol":"PENGUUSDT","side":"Buy","positionIdx":0,"size":"1"}]}),"PENGUUSDT",body,0)


@pytest.mark.parametrize("timestamp", [None, 69, 103, float("nan"), float("inf")])
def test_b2b2_stale_emergency_ownership_writes_zero_and_keeps_close_identity(tmp_path,timestamp):
    body=identity(); order={**body,"orderStatus":"Filled","cumExecQty":"2","leavesQty":"0"}; writes=[]
    state=basket.new_state(None); state.update(status="submitted",winner="PENGUUSDT",orderLinkId="L",body=body)
    class Runtime:
        def __init__(self): self.reads=0
        def wall_clock(self): return 100
        def read(self,path,params=None,private=False):
            self.reads += 1
            envelope={"_response_ts":100,"list":[{"symbol":"PENGUUSDT","side":"Buy","positionIdx":0,"size":"2"}] if path.endswith("position/list") else []}
            if self.reads > 2:
                if timestamp is None: envelope.pop("_response_ts")
                else: envelope["_response_ts"]=timestamp
            return envelope
    runtime=Runtime()
    with pytest.raises(basket.AmbiguousOrder,match="freshness"):
        basket.poll_order(state,tmp_path/"s",lambda _:order,
            lambda:{"symbol":"PENGUUSDT","side":"Buy","positionIdx":0,"size":"2"},
            lambda p,b:writes.append((p,b)),lambda _:None,now=lambda:100,sleep=lambda _:None,
            ownership_check=lambda qty:basket._verify_owned(runtime,"PENGUUSDT",body,qty))
    saved=basket.load_state(tmp_path/"s")
    assert not [w for w in writes if w[0]=="/v5/order/create"] and saved["status"] == "reconciling"
    assert saved["closeOrderLinkId"] == "L-close" and saved["closeBody"]["orderLinkId"] == "L-close"


def test_b2b2_stale_recovery_ownership_writes_zero():
    class Runtime:
        def wall_clock(self): return 100
        def read(self,path,params=None,private=False): return {"_response_ts":69,"list":[]}
    with pytest.raises(basket.TransientError,match="freshness"):
        basket._verify_owned(Runtime(),"PENGUUSDT",identity(),0)


def test_nonce_rejects_unsafe_or_too_long_before_state_write():
    for nonce in ("bad space", "bad/slash", "x" * 14):
        with pytest.raises(basket.PermanentError, match="nonce"):
            basket.make_approval(nonce, 100)
    assert len("basket-Ab9_--SKHYNIXUSDT") <= 36


def test_rounded_entry_must_remain_zone_and_postonly():
    meta={"tick":.00002,"qty_step":1,"min_qty":1,"min_notional":1}
    with pytest.raises(basket.PermanentError, match="rounded entry"):
        basket.build_payload("PENGUUSDT",.006651,meta,"L",0,bid=.006651,ask=.006652)
    meta["tick"] = .000001
    with pytest.raises(basket.PermanentError, match="PostOnly"):
        basket.build_payload("PENGUUSDT",.00664,meta,"L",0,bid=.00663,ask=.00664)


def test_exact_position_selector_supports_hedge_and_rejects_duplicates():
    rows=[{"symbol":"PENGUUSDT","side":"Sell","positionIdx":2,"size":"4"},
          {"symbol":"PENGUUSDT","side":"Buy","positionIdx":1,"size":"2"}]
    assert basket._exact_position(rows,"PENGUUSDT",1,"Buy")["size"] == "2"
    with pytest.raises(basket.AmbiguousOrder):
        basket._exact_position(rows+[dict(rows[1])],"PENGUUSDT",1,"Buy")


def test_poll_transient_reads_are_bounded_and_reconciling(tmp_path):
    state=basket.new_state(None); state.update(status="submitted",winner="PENGUUSDT",orderLinkId="L",body=identity())
    calls=0
    def lookup(_):
        nonlocal calls; calls += 1
        raise basket.TransientError("busy")
    with pytest.raises(basket.AmbiguousOrder):
        basket.poll_order(state,tmp_path/"s",lookup,lambda:{},lambda *_:None,lambda _:None,
                          now=lambda:1,sleep=lambda _:None,read_retries=2)
    assert calls == 3 and basket.load_state(tmp_path/"s")["status"] == "reconciling"


def test_trading_stop_timeout_persists_and_attempts_emergency_close(tmp_path):
    state=basket.new_state(None); state.update(status="submitted",winner="PENGUUSDT",orderLinkId="L",body=identity())
    positions=iter([{"symbol":"PENGUUSDT","side":"Buy","positionIdx":0,"size":"2"},
                    {"symbol":"PENGUUSDT","side":"Buy","positionIdx":0,"size":"2"}])
    writes=[]
    def write(path,body):
        writes.append((path,body))
        raise TimeoutError("unknown")
    with pytest.raises(basket.AmbiguousOrder):
        basket.poll_order(state,tmp_path/"s",lambda _:{**identity(),"orderStatus":"Filled","cumExecQty":"2","leavesQty":"0"},
                          lambda:next(positions),write,lambda _:None,now=lambda:1,sleep=lambda _:None,
                          timeout=1,metadata={"tick":.0001})
    saved=basket.load_state(tmp_path/"s")
    assert [p for p,_ in writes] == ["/v5/position/trading-stop","/v5/order/create"]


@pytest.mark.parametrize("rows", [[], [identity(), identity()]])
def test_submit_exact_lookup_empty_or_multiple_stays_reconciling(tmp_path, rows):
    state=basket.new_state(None); state["candidate"]="PENGUUSDT"
    path=tmp_path/"s"
    with pytest.raises(basket.AmbiguousOrder):
        basket.submit_once(state,path,identity(),lambda _:{"orderId":"ack"},lambda _:{"list":rows})
    saved=basket.load_state(path)
    assert saved["status"] == "reconciling" and saved["orderLinkId"] == "L"


def test_malformed_read_after_possible_fill_stays_recoverable(tmp_path):
    state=basket.new_state(None); state.update(status="submitted",winner="PENGUUSDT",orderLinkId="L",body=identity())
    with pytest.raises(basket.AmbiguousOrder):
        basket.poll_order(state,tmp_path/"s",lambda _:{"broken":object()},lambda:{},lambda *_:None,
                          lambda _:None,now=lambda:1,sleep=lambda _:None,read_retries=0)
    assert basket.load_state(tmp_path/"s")["status"] == "reconciling"


def test_prior_fatal_exposure_possible_restarts_recovery_and_never_enters(tmp_path, monkeypatch):
    a,s,p=_live_state(tmp_path,"fatal")
    s.update(winner="PENGUUSDT",orderLinkId="L",body=identity(),instrumentMetadata={"tick":.0001},
             exposure_possible=True,fatal_reason="old uncertainty")
    basket.save_state(s,p); entered=[]
    monkeypatch.setattr(basket,"select_candidate",lambda *_:entered.append(True))
    class Uncertain:
        mode="live"
        def read(self,*_,**__): raise basket.TransientError("busy")
        def write(self,*_): raise AssertionError("write")
    with pytest.raises(basket.AmbiguousOrder):
        basket.run_live_session(Uncertain(),a,"bad",999,p,tmp_path/"lock",sleep=lambda _:None)
    saved=basket.load_state(p)
    assert saved["status"] == "reconciling" and saved["exposure_possible"] is True and entered == []


def test_inert_null_approval_rebind_requires_locked_final_clear(tmp_path):
    old=basket.new_state(None); old["exposure_possible"]=False
    path=tmp_path/"state"; lock=tmp_path/"lock"; basket.save_state(old,path)
    approval=basket.make_approval("fresh",100,60); reads=[]
    class Clear:
        def wall_clock(self): return 101
        def read(self,endpoint,params,private=False):
            assert lock.exists() and private
            reads.append((endpoint,params.get("settleCoin")))
            return {"_response_ts":101,"list":[]}
    with basket.global_lock(lock):
        rebound=basket.rebind_inert_state(Clear(),approval,path)
    assert rebound == basket.new_state(approval)
    assert basket.load_state(path) == basket.new_state(approval)
    assert reads == [("/v5/position/list","USDT"),("/v5/order/realtime","USDT")]


@pytest.mark.parametrize("change", [
    {"candidate":"PENGUUSDT"}, {"winner":"PENGUUSDT"}, {"orderLinkId":"L"},
    {"body":identity()}, {"status":"reconciling"}, {"exposure_possible":True},
    {"unexpected":None},
])
def test_non_inert_state_blocks_rebind_unchanged(tmp_path, change):
    state=basket.new_state(None); state["exposure_possible"]=False; state.update(change)
    path=tmp_path/"state"; basket.save_state(state,path); before=path.read_bytes()
    class Clear:
        def read(self,*_,**__): return {"_response_ts":101,"list":[]}
    with pytest.raises(basket.PermanentError,match="approval state mismatch"):
        basket.rebind_inert_state(Clear(),basket.make_approval("fresh",100,60),path)
    assert path.read_bytes() == before


def test_unrelated_manual_exposure_allows_inert_rebind(tmp_path):
    state=basket.new_state(None); state["exposure_possible"]=False
    path=tmp_path/"state"; basket.save_state(state,path); before=path.read_bytes()
    class Exposed:
        def wall_clock(self): return 101
        def read(self,endpoint,params,private=False):
            if endpoint.endswith("order/realtime"):
                return {"_response_ts":101,"list":[{"symbol":"MANUALUSDT","orderStatus":"New","orderLinkId":"manual"}]}
            return {"_response_ts":101,"list":[{"symbol":"OTHERUSDT","size":"1"}]}
    rebound=basket.rebind_inert_state(Exposed(),basket.make_approval("fresh",100,60),path)
    assert rebound["status"] == "watching" and path.read_bytes() != before


def test_malformed_state_blocks_rebind_unchanged(tmp_path):
    path=tmp_path/"state"; path.write_text("{"); before=path.read_bytes()
    with pytest.raises(basket.PermanentError,match="approval state mismatch"):
        basket.rebind_inert_state(None,basket.make_approval("fresh",100,60),path)
    assert path.read_bytes() == before


def _dynamic(now=1786382480):
    doc=json.loads(json.dumps(FRESH_PLAN_DOC)); plans=basket.validate_plan_document(doc,now)[1]
    approval=basket.make_approval("dyn",now,60,doc)
    return doc,plans,approval


def _historical_live_state(tmp_path,status="submitted"):
    doc,plans,_=_dynamic(); old="1"*64; approval=basket.make_approval("old",1786382480,60,doc,old)
    state=basket.new_state(approval,plans,doc,approval["fingerprint"])
    body={"symbol":"BEATUSDT","side":"Sell","qty":"2","price":"1.92","orderType":"Limit","positionIdx":0,
          "orderLinkId":"L","stopLoss":"2.0302","takeProfit":"1.5843"}
    state.update(status=status,winner="BEATUSDT",orderLinkId="L",body=body,instrumentMetadata={"tick":.0001},
                 executorSourceSha256=old,planFingerprint=approval["fingerprint"])
    path=tmp_path/"state"; basket.save_state(state,path)
    return doc,approval,state,path


def test_fresh_live_requires_plan_before_private_or_state_mutation(tmp_path):
    class Silent:
        mode="live"
        def read(self,*a,**k): raise AssertionError("private read")
        def write(self,*a,**k): raise AssertionError("write")
    with pytest.raises(basket.PermanentError,match="plan document required"):
        basket.run_live_session(Silent(),{},"",1,tmp_path/"state",tmp_path/"lock")
    assert list(tmp_path.iterdir()) == []


def test_live_validate_mode_uses_dynamic_fingerprint():
    doc,_,approval=_dynamic()
    basket.validate_mode("live",approval,"dyn:"+approval["fingerprint"],1786382480,
                         plan_document=doc,source_hash=approval["executorSourceSha256"])
    with pytest.raises(basket.PermanentError,match="fingerprint"):
        basket.validate_mode("live",approval,"dyn:"+approval["fingerprint"],1786382480)


def test_submit_snapshot_is_durable_before_post(tmp_path):
    doc,plans,approval=_dynamic(); path=tmp_path/"state"
    state=basket.new_state(approval,plans,doc,approval["fingerprint"])
    state["executorSourceSha256"]=approval["executorSourceSha256"]
    body={"symbol":"BEATUSDT","orderLinkId":"L"}
    def create(_):
        saved=basket.load_state(path)
        assert saved["planDocument"] == doc
        assert saved["planFingerprint"] == approval["fingerprint"]
        assert saved["executorSourceSha256"] == approval["executorSourceSha256"]
    basket.submit_once(state,path,body,create,lambda _:{"orderId":"O"},plans=plans)


def test_recovery_rebuilds_only_durable_dynamic_plan(tmp_path,monkeypatch):
    doc,plans,approval=_dynamic(); path=tmp_path/"state"
    body={"symbol":"BEATUSDT","side":"Sell","qty":"2","price":"1.92","orderType":"Limit",
          "positionIdx":0,"orderLinkId":"L","stopLoss":"2.0302","takeProfit":"1.5843"}
    state=basket.new_state(approval,plans,doc,approval["fingerprint"])
    state.update(status="submitted",winner="BEATUSDT",orderLinkId="L",body=body,
                 instrumentMetadata={"tick":.0001},executorSourceSha256=approval["executorSourceSha256"])
    basket.save_state(state,path); monkeypatch.setattr(basket,"PLANS",{})
    class Recovery:
        mode="live"
        def wall_clock(self): return 101
        def read(self,endpoint,params=None,private=False):
            if endpoint.endswith("order/realtime"): return {"_response_ts":101,"list":[{**body,"orderStatus":"Filled","cumExecQty":"2","leavesQty":"0"}]}
            symbol=(params or {}).get("symbol")
            return {"_response_ts":101,"list":[{"symbol":symbol,"side":"Sell" if symbol=="BEATUSDT" else "","positionIdx":0,
                "size":"2" if symbol=="BEATUSDT" else "0","stopLoss":"2.0302","takeProfit":"1.5843",
                "slTriggerBy":"MarkPrice","tpTriggerBy":"MarkPrice"}]}
        def write(self,*_): raise AssertionError("write")
    assert basket.run_live_session(Recovery(),None,"",1786400000,path,tmp_path/"lock",sleep=lambda _:None)["status"]=="filled"
    state["status"]="submitted"; basket.save_state(state,path)
    bad=json.loads(json.dumps(doc)); bad["source"]="other"
    assert basket.run_live_session(Recovery(),None,"",1786400000,path,tmp_path/"lock",
                                   plan_document=bad,sleep=lambda _:None)["status"]=="filled"


def test_deployment_safe_historical_recovery_accepts_durable_source_rebind(tmp_path):
    doc,approval,state,path=_historical_live_state(tmp_path)
    assert approval["executorSourceSha256"] != basket.executor_source_sha256()
    assert approval["fingerprint"] == basket.plan_fingerprint(doc,state["executorSourceSha256"],approval)
    body=state["body"]
    class Recovery:
        mode="live"
        def __init__(self): self.calls=[]
        def wall_clock(self): return 1786400000
        def read(self,endpoint,params=None,private=False):
            self.calls.append(("GET",endpoint,dict(params or {})))
            if endpoint.endswith("order/realtime"):
                return {"_response_ts":1786400000,"list":[{**body,"orderStatus":"Filled","cumExecQty":"2","leavesQty":"0"}]}
            symbol=(params or {}).get("symbol")
            return {"_response_ts":1786400000,"list":[{"symbol":symbol,"side":"Sell" if symbol=="BEATUSDT" else "",
                "positionIdx":0,"size":"2" if symbol=="BEATUSDT" else "0","stopLoss":"2.0302","takeProfit":"1.5843",
                "slTriggerBy":"MarkPrice","tpTriggerBy":"MarkPrice"}]}
        def write(self,path,body): self.calls.append(("POST",path,dict(body))); raise AssertionError("write")
    rt=Recovery()
    assert basket.run_live_session(rt,None,"",1786400000,path,tmp_path/"lock",sleep=lambda _:None)["status"]=="filled"
    assert not [c for c in rt.calls if c[0]=="POST"]


@pytest.mark.parametrize("mutate",[
    lambda s: s.update(executorSourceSha256="bad"),
    lambda s: s.update(planFingerprint="0"*64),
    lambda s: s.pop("approved"),
])
def test_deployment_safe_historical_recovery_bad_metadata_fails_closed_zero_writes(tmp_path,mutate):
    _doc,_approval,state,path=_historical_live_state(tmp_path); mutate(state); basket.save_state(state,path); before=path.read_bytes()
    class Silent:
        mode="live"
        def read(self,*_args,**_kwargs): raise AssertionError("read")
        def write(self,*_args,**_kwargs): raise AssertionError("write")
    with pytest.raises(basket.PermanentError):
        basket.run_live_session(Silent(),None,"",1786400000,path,tmp_path/"lock",sleep=lambda _:None)
    assert path.read_bytes() == before


def test_deployment_safe_fresh_entry_rejects_stale_historical_hash_zero_writes(tmp_path):
    doc,_,approval=_dynamic(); old="1"*64; stale=basket.make_approval("old",1786382480,60,doc,old)
    class Silent:
        mode="live"
        def read(self,*_args,**_kwargs): raise AssertionError("read")
        def write(self,*_args,**_kwargs): raise AssertionError("write")
    with pytest.raises(basket.PermanentError,match="current executor source"):
        basket.run_live_session(Silent(),stale,"old:"+stale["fingerprint"],1786382480,tmp_path/"state",tmp_path/"lock",
                                plan_document=doc,source_hash=old,sleep=lambda _:None)
    assert list(tmp_path.iterdir()) == []


def test_deployment_safe_terminal_historical_state_still_requires_current_gate(tmp_path):
    doc,approval,state,path=_historical_live_state(tmp_path,"cancelled"); before=path.read_bytes()
    class Silent:
        mode="live"
        def read(self,*_args,**_kwargs): raise AssertionError("read")
        def write(self,*_args,**_kwargs): raise AssertionError("write")
    with pytest.raises(basket.PermanentError,match="current executor source"):
        basket.run_live_session(Silent(),approval,"old:"+approval["fingerprint"],1786382480,path,tmp_path/"lock",
                                plan_document=doc,source_hash=state["executorSourceSha256"],sleep=lambda _:None)
    assert path.read_bytes() == before


def test_main_live_recovery_dispatches_without_external_plan_or_new_entry(tmp_path,monkeypatch,capsys):
    doc,plans,approval=_dynamic(); path=tmp_path/"state"
    state=basket.new_state(approval,plans,doc,approval["fingerprint"])
    state.update(status="submitted",winner="BEATUSDT",orderLinkId="L",body={"symbol":"BEATUSDT"},
                 executorSourceSha256=approval["executorSourceSha256"])
    basket.save_state(state,path); calls=[]
    monkeypatch.setattr(basket,"STATE_PATH",path)
    monkeypatch.setattr(basket,"run_live_session",lambda *a,**k: calls.append((a,k)) or {"status":"filled"})
    monkeypatch.setenv("BASKET_EXECUTION_MODE","live")
    monkeypatch.delenv("BASKET_PLAN_PATH",raising=False)
    monkeypatch.delenv("BASKET_APPROVAL_JSON",raising=False)
    monkeypatch.delenv("BASKET_LIVE_CONFIRM",raising=False)
    basket.main()
    assert len(calls)==1 and calls[0][0][1:3] == (None,None)
    assert calls[0][1]["plan_document"] is None
    assert json.loads(capsys.readouterr().out)["cycle"]["status"] == "filled"


def test_main_live_malformed_durable_recovery_never_falls_to_entry(tmp_path,monkeypatch):
    path=tmp_path/"state"; path.write_text(json.dumps({"status":"reconciling","exposure_possible":True}))
    entered=[]; monkeypatch.setattr(basket,"STATE_PATH",path)
    monkeypatch.setattr(basket,"run_live_session",lambda *a,**k: entered.append(True))
    monkeypatch.setenv("BASKET_EXECUTION_MODE","live")
    monkeypatch.delenv("BASKET_PLAN_PATH",raising=False)
    with pytest.raises(SystemExit,match="durable plan"):
        basket.main()
    assert entered == []

@pytest.mark.parametrize("kind",["position","order"])
def test_account_wide_late_unrelated_manual_exposure_allows_owned_write(kind):
    body=identity(); calls=[]
    class R:
        def wall_clock(self): return 101
        def read(self,path,params,private=False):
            calls.append((path,params))
            if path.endswith("position/list"):
                rows=[] if kind=="order" else [{"symbol":"MANUALUSDT","size":"1","side":"Buy","positionIdx":0}]
            else:
                rows=[] if kind=="position" else [{"symbol":"MANUALUSDT","orderStatus":"New","orderLinkId":"manual"}]
            return {"_response_ts":101,"list":rows}
    basket._verify_owned(R(),body["symbol"],body,0,{body["symbol"]:basket.PLANS[body["symbol"]]})
    assert calls==[("/v5/position/list",{"category":"linear","settleCoin":"USDT"}),
                   ("/v5/order/realtime",{"category":"linear","settleCoin":"USDT","openOnly":0})]

def test_account_wide_cancel_allows_exact_own_active_entry_order():
    body=identity()
    class R:
        def wall_clock(self): return 101
        def read(self,path,params,private=False):
            return {"_response_ts":101,"list":[] if path.endswith("position/list") else [{**body,"orderStatus":"New"}]}
    basket._verify_owned(R(),body["symbol"],body,0,{body["symbol"]:basket.PLANS[body["symbol"]]})


def test_entry_exposure_allows_many_unrelated_manual_rows_and_blocks_candidate():
    positions=[{"symbol":f"MANUAL{i}USDT","size":"1","side":"Buy","positionIdx":0} for i in range(10)]
    orders=[{"symbol":f"ORDER{i}USDT","orderStatus":"New","orderLinkId":f"manual-{i}"} for i in range(20)]
    basket._require_entry_clear(positions,orders,"ETHUSDT")
    with pytest.raises(basket.PrewriteRejected,match="candidate exposure"):
        basket._require_entry_clear(positions+[{"symbol":"ETHUSDT","size":"1"}],orders,"ETHUSDT")
    with pytest.raises(basket.PrewriteRejected,match="candidate exposure"):
        basket._require_entry_clear(positions,orders+[{"symbol":"ETHUSDT","orderStatus":"New","orderLinkId":"manual"}],"ETHUSDT")


def test_final_create_allows_twenty_unrelated_empty_link_manual_orders():
    orders=[{"symbol":f"MANUAL{i}USDT","orderStatus":"New","orderLinkId":""} for i in range(20)]; posts=[]
    def tx(path,params=None,method="GET",body=None,private=False):
        if method=="POST": posts.append((path,body)); return {"orderId":"new"}
        return {"_response_ts":basket.time.time(),"list":[] if path.endswith("position/list") else orders}
    body={"symbol":"ETHUSDT","orderLinkId":"basket-n-ETHUSDT"}
    assert basket._create_if_account_clear(basket.Runtime(tx,"live"),body)=={"orderId":"new"}
    assert posts==[("/v5/order/create",body)] and all(o["orderLinkId"]=="" for o in orders)


def test_final_create_empty_link_candidate_order_rejects_without_post():
    posts=[]
    def tx(path,params=None,method="GET",body=None,private=False):
        if method=="POST": posts.append((path,body)); return {}
        return {"_response_ts":basket.time.time(),"list":[] if path.endswith("position/list") else [{"symbol":"ETHUSDT","orderStatus":"New","orderLinkId":""}]}
    with pytest.raises(basket.PrewriteRejected,match="candidate exposure"):
        basket._create_if_account_clear(basket.Runtime(tx,"live"),{"symbol":"ETHUSDT","orderLinkId":"basket-n-ETHUSDT"})
    assert posts==[]


@pytest.mark.parametrize("row",[
    {"symbol":"OTHERUSDT","orderStatus":"New","orderLinkId":"basket-old-OTHERUSDT"},
    {"symbol":"OTHERUSDT","orderStatus":"New","orderLinkId":"basketmalformed"},
])
def test_entry_exposure_blocks_bot_or_ambiguous_active_order_anywhere(row):
    with pytest.raises(basket.PermanentError,match="bot or ambiguous"):
        basket._require_entry_clear([], [row], "ETHUSDT")


@pytest.mark.parametrize("evidence", [[], {"symbol":"BEATUSDT"},
    {"symbol":"NOPE","touchTs":1,"triggerTs":2,"triggerM15Ts":1,"triggerM15Close":1.0},
    {"symbol":"BEATUSDT","touchTs":True,"triggerTs":2,"triggerM15Ts":1,"triggerM15Close":1.0}])
def test_winner_evidence_rejected_before_irrelevant_account_exposure(evidence,tmp_path):
    doc,_,approval=_dynamic(); reads=[]
    class Exposed:
        mode="live"
        def read(self,path,params=None,private=False):
            reads.append((path,params))
            if path.endswith("position/list"):
                return {"_response_ts":101,"list":[{"symbol":"OTHERUSDT","size":"1","orderLinkId":"basket-old"}]}
            return {"_response_ts":101,"list":[{"symbol":"OTHERUSDT","orderStatus":"New","orderLinkId":""}]}
        def write(self,*_): raise AssertionError("write")
    error="symbol mismatch" if isinstance(evidence,dict) and set(evidence)=={"symbol","touchTs","triggerTs","triggerM15Ts","triggerM15Close"} and evidence["symbol"]=="NOPE" else "invalid winner evidence"
    with pytest.raises(basket.PermanentError,match=error):
        basket.run_live_session(Exposed(),approval,"dyn:"+approval["fingerprint"],1786382480,
            tmp_path/"state",tmp_path/"lock",plan_document=doc,winner_evidence=evidence)
    assert not reads and not (tmp_path/"state").exists()


def test_run_live_session_prewrite_rejection_persists_nested_decimal_snapshot(tmp_path, monkeypatch):
    doc,_,_=_dynamic(); doc["plans"]=doc["plans"][:1]
    approval=basket.make_approval("dyn",1786382480,60,doc); state_path=tmp_path/"state"
    evidence={"symbol":"BEATUSDT","touchTs":1786381000,"triggerTs":1786381600,
              "triggerM15Ts":1786381200,"triggerM15Close":1.9}
    diagnostic={"ok":False,"code":"FINAL_SPREAD_EXCEEDED","message":"spread exact",
                "gate":"spread","observed":Decimal("0.0100"),"operator":"<=","threshold":Decimal("0.0010"),
                "observations":{"final":{"price":Decimal("1.905600")}}}
    monkeypatch.setattr(basket,"final_entry_diagnostic",lambda *_:diagnostic)
    monkeypatch.setattr(basket,"_final_observations",lambda *_:{"nested":{"price":Decimal("1.905600")}})
    class ReadOnly:
        mode="live"
        def wall_clock(self): return 1786382480
        def read(self,path,params=None,private=False):
            if path.endswith("position/list"): rows=[{"symbol":"BEATUSDT","positionIdx":0,"size":"0"}]
            elif path.endswith("order/realtime"): rows=[]
            elif path.endswith("instruments-info"):
                rows=[{"priceFilter":{"tickSize":".0001"},"lotSizeFilter":{"qtyStep":"1","minOrderQty":"1","minNotionalValue":"1"}}]
            else: raise AssertionError(path)
            return {"_response_ts":1786382480,"list":rows}
        def write(self,*_): raise AssertionError("write")
    with pytest.raises(basket.PrewriteRejected,match="spread exact"):
        basket.run_live_session(ReadOnly(),approval,"dyn:"+approval["fingerprint"],1786382480,
            state_path,tmp_path/"lock",snapshot_fetch=lambda *_:{"symbol":"BEATUSDT","bid":1.90,"ask":1.91},
            now_fn=lambda:1786382480,plan_document=doc,winner_evidence=evidence,sleep=lambda _:None)
    saved=json.loads(state_path.read_text())
    assert saved["status"]=="fatal" and saved["snapshots"]["BEATUSDT"]["nested"]["price"]=="1.905600"
    assert saved["finalGate"]["observed"]=="0.0100" and saved["finalGate"]["threshold"]=="0.0010"


def test_legacy_preflight_allows_manual_plan_exposure_and_reports_exclusions():
    manual={"symbol":"XAUUSDT","size":"1","side":"Buy","positionIdx":0}
    calls=[]
    def transport(path,params=None,**kwargs):
        calls.append((path,params))
        if path.endswith("instruments-info"):
            return {"_response_ts":101,"list":[{"priceFilter":{"tickSize":".01"},"lotSizeFilter":{"qtyStep":"1","minOrderQty":"1","minNotionalValue":"1"}}]}
        if path.endswith("position/list"): return {"_response_ts":101,"list":[manual]}
        return {"_response_ts":101,"list":[]}
    result=basket.preflight_readonly(basket.Runtime(transport,"dry-run"),"dry-run",now=100)
    assert result["clear"] is True and result["exclusions"]==["XAUUSDT"]
    assert not [c for c in calls if c[0] in basket.WRITE_PATHS]


@pytest.mark.parametrize("order",[
    {"symbol":"OTHERUSDT","orderStatus":"New","orderLinkId":"basket-old"},
    {"symbol":"OTHERUSDT","orderStatus":"New","orderLinkId":"automalformed"}])
def test_legacy_preflight_blocks_bot_or_ambiguous_exposure(order):
    def transport(path,params=None,**kwargs):
        if path.endswith("instruments-info"):
            return {"_response_ts":101,"list":[{"priceFilter":{"tickSize":".01"},"lotSizeFilter":{"qtyStep":"1","minOrderQty":"1","minNotionalValue":"1"}}]}
        return {"_response_ts":101,"list":[{"symbol":"PENGUUSDT","size":"0","positionIdx":0}] if path.endswith("position/list") else [order]}
    with pytest.raises(basket.PermanentError,match="bot or ambiguous"):
        basket.preflight_readonly(basket.Runtime(transport,"dry-run"),"dry-run",now=100)


def test_post_identity_permanent_failure_allows_unrelated_manual_plan_exposure(tmp_path):
    a,s,p=_live_state(tmp_path); snap=lambda *_:{**fresh("PENGUUSDT",.00666,.00666,101),"bid":.00664,"ask":.006641}
    class ManualReject(FakeLive):
        def read(self,path,params=None,private=False):
            if path.endswith("position/list") and (params or {}).get("settleCoin"):
                self.calls.append(("GET",path,dict(params or {})))
                return {"_response_ts":101,"list":[{"symbol":"ZECUSDT","size":"1","side":"Buy","positionIdx":0}]}
            return super().read(path,params,private)
    rt=ManualReject([],[],reject="110126")
    with pytest.raises(basket.PermanentError,match="110126"):
        basket.run_live_session(rt,a,"n:"+a["fingerprint"],101,p,tmp_path/"lock",symbols=["PENGUUSDT"],snapshot_fetch=snap,plan_document=s["planDocument"])
    saved=basket.load_state(p)
    assert saved["status"]=="fatal" and saved["exposure_possible"] is False
    assert [c[1] for c in rt.calls if c[0]=="POST"]==["/v5/order/create"]


def test_b7_exact_bot_fill_position_allows_protection_write(tmp_path):
    body=identity("basket-n-PENGUUSDT","2","0.00664"); order={**body,"orderStatus":"Filled","cumExecQty":"2","leavesQty":"0"}
    state=basket.new_state(None); state.update(status="submitted",winner="PENGUUSDT",orderLinkId=body["orderLinkId"],body=body)
    unprotected={"symbol":"PENGUUSDT","side":"Buy","positionIdx":0,"size":"2","orderLinkId":body["orderLinkId"]}
    protected={**unprotected,"stopLoss":"0.006602","takeProfit":"0.006773","slTriggerBy":"MarkPrice","tpTriggerBy":"MarkPrice"}
    class R:
        def wall_clock(self): return 101
        def read(self,path,params,private=True): return {"_response_ts":101,"list":[unprotected] if path.endswith("position/list") else [order]}
    writes=[]; positions=iter([unprotected,protected])
    result=basket.poll_order(state,tmp_path/"s",lambda _:order,lambda:next(positions),lambda p,b:writes.append((p,b)) or {},lambda _:None,
        now=lambda:101,sleep=lambda _:None,metadata={"tick":.0001},ownership_check=lambda q:basket._verify_owned(R(),"PENGUUSDT",body,q))
    assert result=="filled" and [p for p,_ in writes]==["/v5/position/trading-stop"]

@pytest.mark.parametrize("link",["",None,"manual-1"])
def test_b7_manual_identical_position_blocks_protection_and_emergency_writes(tmp_path,link):
    body=identity("basket-n-PENGUUSDT","2","0.00664"); order={**body,"orderStatus":"Filled","cumExecQty":"2","leavesQty":"0"}
    state=basket.new_state(None); state.update(status="submitted",winner="PENGUUSDT",orderLinkId=body["orderLinkId"],body=body)
    position={"symbol":"PENGUUSDT","side":"Buy","positionIdx":0,"size":"2","orderLinkId":link}
    class R:
        def wall_clock(self): return 101
        def read(self,path,params,private=True): return {"_response_ts":101,"list":[position] if path.endswith("position/list") else [order]}
    writes=[]
    with pytest.raises(basket.PermanentError,match="ownership"):
        basket.poll_order(state,tmp_path/"s",lambda _:order,lambda:position,lambda p,b:writes.append((p,b)),lambda _:None,
            now=lambda:101,sleep=lambda _:None,metadata={"tick":.0001},ownership_check=lambda q:basket._verify_owned(R(),"PENGUUSDT",body,q))
    assert writes==[]


def test_b7_mixed_manual_bot_net_size_mismatch_blocks_zero_writes(tmp_path):
    a,s,p=_live_state(tmp_path,"submitted"); body=identity("basket-n-PENGUUSDT","2","0.00664")
    s.update(winner="PENGUUSDT",orderLinkId=body["orderLinkId"],body=body,instrumentMetadata={"tick":.0001}); basket.save_state(s,p)
    order={**body,"orderStatus":"Filled","cumExecQty":"2","leavesQty":"0"}
    rt=FakeLive([order],[{"symbol":"PENGUUSDT","side":"Buy","positionIdx":0,"size":"3","orderLinkId":body["orderLinkId"]}])
    with pytest.raises(basket.PermanentError,match="ownership|mismatch"):
        basket.run_live_session(rt,a,"bad",999,p,tmp_path/"lock",sleep=lambda _:None)
    assert not [c for c in rt.calls if c[0]=="POST"]


def test_b7_unrelated_manual_exposure_untouched_during_recovery_write(tmp_path):
    body=identity("basket-n-PENGUUSDT","2","0.00664"); order={**body,"orderStatus":"Filled","cumExecQty":"2","leavesQty":"0"}
    state=basket.new_state(None); state.update(status="submitted",winner="PENGUUSDT",orderLinkId=body["orderLinkId"],body=body)
    owned={"symbol":"PENGUUSDT","side":"Buy","positionIdx":0,"size":"2","orderLinkId":body["orderLinkId"]}
    protected={**owned,"stopLoss":"0.006602","takeProfit":"0.006773","slTriggerBy":"MarkPrice","tpTriggerBy":"MarkPrice"}
    manual={"symbol":"ETHUSDT","side":"Buy","positionIdx":0,"size":"1","orderLinkId":""}
    class R:
        def wall_clock(self): return 101
        def read(self,path,params,private=True): return {"_response_ts":101,"list":[owned,manual] if path.endswith("position/list") else [order]}
    writes=[]; positions=iter([owned,protected])
    basket.poll_order(state,tmp_path/"s",lambda _:order,lambda:next(positions),lambda p,b:writes.append((p,b)) or {},lambda _:None,
        now=lambda:101,sleep=lambda _:None,metadata={"tick":.0001},ownership_check=lambda q:basket._verify_owned(R(),"PENGUUSDT",body,q))
    assert writes[0][1]["symbol"]=="PENGUUSDT" and manual["size"]=="1"


def _emergency_state(tmp_path, status="reconciling"):
    a,s,p=_live_state(tmp_path,status)
    body=identity("basket-n-PENGUUSDT","2","0.00664")
    close={"category":"linear","symbol":"PENGUUSDT","side":"Sell","orderType":"Market","qty":"2",
           "reduceOnly":True,"positionIdx":0,"orderLinkId":body["orderLinkId"]+"-close"}
    s.update(winner="PENGUUSDT",orderLinkId=body["orderLinkId"],body=body,closeOrderLinkId=close["orderLinkId"],
             closeBody=close,instrumentMetadata={"tick":.0001},executorSourceSha256=a["executorSourceSha256"])
    basket.save_state(s,p)
    return a,s,p,body,close


def test_b8_unresolved_close_restart_stays_reconciling_zero_duplicate(tmp_path):
    a,s,p,body,close=_emergency_state(tmp_path)
    rt=FakeLive([{**close,"orderStatus":"New","cumExecQty":"0"}],[])
    with pytest.raises(basket.AmbiguousOrder,match="close reconciliation unresolved"):
        basket.run_live_session(rt,a,"bad",1000,p,tmp_path/"lock",sleep=lambda _:None)
    assert basket.load_state(p)["status"]=="reconciling"
    assert not [c for c in rt.calls if c[:2]==("POST","/v5/order/create")]


def test_b8_close_filled_flat_restart_terminal_zero_duplicate(tmp_path):
    a,s,p,body,close=_emergency_state(tmp_path)
    rt=FakeLive([{**close,"orderStatus":"Filled","cumExecQty":"2"}],
        [*({"symbol":x,"positionIdx":0,"size":"0"} for x in SYMS), *({"symbol":x,"positionIdx":0,"size":"0"} for x in SYMS)])
    assert basket.run_live_session(rt,a,"bad",1000,p,tmp_path/"lock",sleep=lambda _:None)["status"]=="emergency_closed"
    assert not [c for c in rt.calls if c[:2]==("POST","/v5/order/create")]


def test_b8_close_filled_but_owned_exposure_remains_reconciling(tmp_path):
    a,s,p,body,close=_emergency_state(tmp_path)
    rt=FakeLive([{**close,"orderStatus":"Filled","cumExecQty":"2"}],
        [{"symbol":"PENGUUSDT","side":"Buy","positionIdx":0,"size":"2","orderLinkId":body["orderLinkId"]},
         *({"symbol":x,"positionIdx":0,"size":"0"} for x in SYMS[1:])])
    with pytest.raises(basket.AmbiguousOrder,match="candidate exposure"):
        basket.run_live_session(rt,a,"bad",1000,p,tmp_path/"lock",sleep=lambda _:None)
    assert basket.load_state(p)["status"]=="reconciling"


def test_b8_stale_late_close_stays_reconciling_zero_duplicate(tmp_path):
    a,s,p,body,close=_emergency_state(tmp_path)
    class Stale(FakeLive):
        def read(self,path,params=None,private=False):
            out=super().read(path,params,private); out["_response_ts"]=900; return out
    rt=Stale([{**close,"orderStatus":"Filled","cumExecQty":"2"}],[])
    with pytest.raises(basket.AmbiguousOrder):
        basket.run_live_session(rt,a,"bad",1000,p,tmp_path/"lock",sleep=lambda _:None)
    assert basket.load_state(p)["status"]=="reconciling"
    assert not [c for c in rt.calls if c[:2]==("POST","/v5/order/create")]


def test_b8_emergency_poll_timeout_stays_reconciling_not_fatal(tmp_path):
    state=basket.new_state(None); body=identity("basket-n-PENGUUSDT","2","0.00664")
    state.update(status="submitted",winner="PENGUUSDT",orderLinkId=body["orderLinkId"],body=body)
    order={**body,"orderStatus":"Filled","cumExecQty":"2","leavesQty":"0"}
    unprotected={"symbol":"PENGUUSDT","side":"Buy","positionIdx":0,"size":"2","orderLinkId":body["orderLinkId"]}
    writes=[]
    with pytest.raises(basket.AmbiguousOrder,match="emergency close unconfirmed"):
        basket.poll_order(state,tmp_path/"s",lambda link:{**order,"orderLinkId":link,"orderStatus":"New"} if link.endswith("-close") else order,
            lambda:unprotected,lambda p,b:writes.append((p,b)) or {},lambda _:None,now=lambda:101,sleep=lambda _:None,timeout=0,
            metadata={"tick":.0001},ownership_check=lambda _qty:None,protection_lookup=lambda:(order,unprotected))
    saved=basket.load_state(tmp_path/"s")
    assert saved["status"]=="reconciling" and saved["closeOrderLinkId"]==body["orderLinkId"]+"-close"
