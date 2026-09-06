import json
from contextlib import nullcontext
from pathlib import Path

import pytest
from bots import autonomous_basket_trader as A

NOW = 1786363200.0


class NoTransport:
    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("transport called")


def _live_released_snapshot(path):
    issued=1786580127.5383234; source="52531ac1514b09f64076b4403acf764a884ca8c180a67601c9d1c838f30c0e08"
    plan={"schema":"nexus.basket-plan","version":1,"generated_at":"2026-08-13T00:01:14Z","expires_at":"2026-08-13T00:16:14Z","source":"deterministic-market-wide-scan","provenance":{"scan_id":"scan-1786579258"},"plans":[{"invalid":"609.4","side":"Buy","stop":"608.75","symbol":"BNBUSDT","tp":"613.3000000000002","trigger":"610.7","zone":["609.4","610.7"]}]}
    approval=A.Executor.make_approval("a1786580127",issued,ttl=300,plan_document=plan,source_hash=source)
    executor={"approved":approval,"candidate":"BNBUSDT","errors":{"BNBUSDT":"final market invalid: candle stale or invalid"},"executorSourceSha256":source,"exposure_possible":False,"fatal_reason":"final market invalid: candle stale or invalid","finalGate":{"ok":False,"code":"FINAL_CANDLE_STALE_OR_INVALID"},"finalObservations":{"btc_ts":1786575600},"ineligible":["BNBUSDT"],"permanent_attempts":{"BNBUSDT":0},"planDocument":plan,"planFingerprint":approval["fingerprint"],"snapshots":{"BNBUSDT":{"btc_ts":1786575600}},"status":"fatal","touched":{"BNBUSDT":None},"transient_attempts":{"BNBUSDT":0},"trigger_ts":None,"winner":"BNBUSDT"}
    ids=["auto-1786497320-DOGEUSDT","auto-1786498200-ADAUSDT","auto-1786508104-BEATUSDT","auto-1786512604-BNBUSDT","auto-1786513528-BNBUSDT","auto-1786515322-BLUAIUSDT","auto-1786516216-BEATUSDT","auto-1786517127-AAPLUSDT","auto-1786520703-ADAUSDT","auto-1786523400-BEATUSDT","auto-1786524329-BEATUSDT","auto-1786527009-CLUSDT","auto-1786530616-CYSUSDT","auto-1786531504-CYSUSDT","auto-1786580127-BNBUSDT"]
    ledger={"day":"2026-08-12","loss_streak":0,"modeled_loss_reserved":"0","open_basket":None,"submissions":1,"trades":{i:{"modeled_loss":"2","status":"failed_prewrite"} for i in ids}}
    monitor={"submitted":False,"setupNotified":True,"touches":{"APRUSDT":1786590300},"triggerTs":{},"plan":{"plans":[{"symbol":"APRUSDT"}]}}
    path.mkdir()
    for name,doc in (("ledger",ledger),("monitor",monitor),("executor",executor)): A.atomic_json(path/f"{name}.json",doc)
    return ledger


def test_exact_live_released_terminal_resets_before_transport(tmp_path,monkeypatch):
    state=tmp_path/"state"; ledger=_live_released_snapshot(state)
    policy=A.load_policy(Path(A.__file__).with_name("configs")/"autonomous_basket_policy.json",1786590300)
    monkeypatch.setenv("AUTONOMOUS_BASKET_LIVE","YES"); monkeypatch.setenv("AUTONOMOUS_BASKET_POLICY_SHA256",policy.digest)
    monkeypatch.setattr(A,"scan_market",lambda *a,**k:A.build_plan([],1786590300,"scan-test"))
    transport=NoTransport(); result=A.run_once("live",transport,1786590300,state_dir=state,wall_clock=lambda:1786590300)
    assert result=={"mode":"live","status":"watching"} and transport.calls==[]
    assert json.loads((state/"ledger.json").read_text())==ledger and A.Executor.is_inert_state(A.Executor.load_state(state/"executor.json"))


@pytest.mark.parametrize("mutation",["exposure","reserve","open","submitted","body","source","fingerprint","candidate","trade"])
def test_exact_live_released_one_field_negative_zero_write(tmp_path,mutation):
    state=tmp_path/"state"; _live_released_snapshot(state)
    target=state/({"reserve":"ledger","open":"ledger","trade":"ledger","submitted":"monitor"}.get(mutation,"executor")+".json"); doc=json.loads(target.read_text())
    if mutation=="exposure": doc["exposure_possible"]=None
    elif mutation=="reserve": doc["modeled_loss_reserved"]="0.0"
    elif mutation=="open": doc["open_basket"]="auto-1786580127-BNBUSDT"
    elif mutation=="submitted": doc["submitted"]=True
    elif mutation=="body": doc["body"]={"symbol":"BNBUSDT"}
    elif mutation=="source": doc["executorSourceSha256"]="0"*64
    elif mutation=="fingerprint": doc["planFingerprint"]="0"*64
    elif mutation=="candidate": doc["candidate"]="BTCUSDT"
    else: doc["trades"]["auto-1786580127-BNBUSDT"]["status"]="reserved"
    A.atomic_json(target,doc); before={p.name:p.read_bytes() for p in state.glob("*.json")}
    with pytest.raises(A.FailClosed): A._reset_repaired_terminal(state)
    assert {p.name:p.read_bytes() for p in state.glob("*.json")}==before


def _tuple_state(path, case):
    path.mkdir()
    identity = "auto-1-AAAUSDT"
    ledger = {"day": "2026-08-10", "submissions": 1, "modeled_loss_reserved": "2", "loss_streak": 0,
              "open_basket": identity, "trades": {identity: {"status": "reserved", "modeled_loss": "2"}}}
    monitor = {"submitted": True, "identity": identity, "winner": "AAAUSDT"}
    executor = {"status": "submitting", "candidate": "AAAUSDT", "winner": "AAAUSDT",
                "orderLinkId": identity, "body": {"symbol": "AAAUSDT", "orderLinkId": identity}}
    if case == "orphan_ledger":
        executor, monitor = A.Executor.new_state(None), {"submitted": False}
    elif case == "orphan_monitor":
        executor = A.Executor.new_state(None)
        ledger.update(open_basket=None, modeled_loss_reserved="0", trades={})
    elif case == "missing_ledger":
        ledger.update(open_basket=None, modeled_loss_reserved="0", trades={})
    elif case == "ledger_mismatch":
        ledger["open_basket"] = "auto-2-BBBUSDT"
        ledger["trades"] = {ledger["open_basket"]: {"status": "reserved"}}
    elif case == "monitor_mismatch":
        monitor["winner"] = "BBBUSDT"
    elif case == "unknown_executor":
        executor = {"status": "mystery"}
        ledger.update(open_basket=None, modeled_loss_reserved="0", trades={})
        monitor = {"submitted": False}
    for name, doc in (("ledger", ledger), ("monitor", monitor), ("executor", executor)):
        A.atomic_json(path / f"{name}.json", doc)


CASES = [
    ("orphan_ledger", "ORPHAN_LEDGER"), ("orphan_monitor", "ORPHAN_MONITOR"),
    ("missing_ledger", "ACTIVE_EXECUTOR_LEDGER_MISSING"),
    ("ledger_mismatch", "ACTIVE_EXECUTOR_LEDGER_MISMATCH"),
    ("monitor_mismatch", "ACTIVE_EXECUTOR_SYMBOL_MISMATCH"),
    ("unknown_executor", "UNKNOWN_EXECUTOR_STATUS"),
]


def test_durable_tuple_error_twenty_cycles_exact_health_one_call_each(tmp_path):
    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise A.DurableTupleError("ORPHAN_LEDGER")

    for tick in range(20):
        assert A.run_cycle("live", NoTransport(), NOW + tick, state_dir=tmp_path,
                           wall_clock=lambda tick=tick: NOW + tick, run_once_fn=fail) == {
            "mode": "live", "status": "degraded", "code": "ORPHAN_LEDGER"}
    assert len(calls) == 20
    assert json.loads((tmp_path / "health.json").read_text()) == {
        "schema": "nexus.autonomous-basket-health", "version": 1, "state": "degraded",
        "code": "ORPHAN_LEDGER", "tuple_class": "DEGRADED",
        "exception": {"type": "DurableTupleError", "message": "ORPHAN_LEDGER"},
        "ts": NOW + 19}


@pytest.mark.parametrize("case,code", CASES)
def test_stage_a_tuple_six_scenarios_survive_twenty_cycles_byte_preserved(tmp_path, case, code):
    state = tmp_path / "state"
    _tuple_state(state, case)
    before = {p.name: p.read_bytes() for p in state.iterdir()}
    transport = NoTransport()
    for tick in range(20):
        result = A.run_cycle("live", transport, NOW + tick, state_dir=state,
                             wall_clock=lambda tick=tick: NOW + tick)
        assert result == {"mode": "live", "status": "degraded", "code": code}
    assert transport.calls == []
    assert {p.name: p.read_bytes() for p in state.iterdir() if p.name != "health.json"} == before
    assert json.loads((state / "health.json").read_text()) == {
        "schema": "nexus.autonomous-basket-health", "version": 1, "state": "degraded",
        "code": code, "tuple_class": "DEGRADED",
        "exception": {"type": "DurableTupleError", "message": code}, "ts": NOW + 19}


@pytest.mark.parametrize("target,code", [
    ("ledger", "MALFORMED_LEDGER"), ("monitor", "MALFORMED_MONITOR"),
    ("executor", "MALFORMED_EXECUTOR"), ("journal", "FailClosed")])
def test_malformed_state_and_tampered_journal_survive_twenty_cycles(tmp_path, target, code):
    state = tmp_path / "state"
    state.mkdir()
    if target == "journal":
        (state / "terminal_reset.json").write_text("{}")
        (state / "repair.lock").touch()
    else:
        (state / f"{target}.json").write_text("{")
    before = {p.name: p.read_bytes() for p in state.iterdir()}
    transport = NoTransport()
    for tick in range(20):
        result = A.run_cycle("live", transport, NOW + tick, state_dir=state,
                             wall_clock=lambda tick=tick: NOW + tick)
        assert result == {"mode": "live", "status": "degraded", "code": code}
    assert transport.calls == []
    assert {p.name: p.read_bytes() for p in state.iterdir() if p.name != "health.json"} == before
    assert json.loads((state / "health.json").read_text())["state"] == "degraded"


@pytest.mark.parametrize("error", [RuntimeError("bug"), AssertionError("assertion")])
def test_programmer_errors_propagate_without_health(tmp_path, error):
    def fail(*args, **kwargs):
        raise error
    with pytest.raises(type(error), match=str(error)):
        A.run_cycle("live", NoTransport(), NOW, state_dir=tmp_path, run_once_fn=fail)
    assert not (tmp_path / "health.json").exists()


@pytest.mark.parametrize("error,code", [
    (A.Executor.AmbiguousOrder("maybe"), "AmbiguousOrder"),
    (A.Executor.TransientError("provider"), "TransientError")])
def test_ambiguity_and_transient_have_exact_code_without_same_cycle_retry(tmp_path, error, code):
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise error
    assert A.run_cycle("live", NoTransport(), NOW, state_dir=tmp_path, wall_clock=lambda: NOW,
                       run_once_fn=fail) == {"mode": "live", "status": "degraded", "code": code}
    assert calls == [1]
    assert json.loads((tmp_path / "health.json").read_text())["code"] == code


def test_healthy_result_writes_exact_healthy_health(tmp_path):
    result = A.run_cycle("live", NoTransport(), NOW, state_dir=tmp_path, wall_clock=lambda: NOW + 1,
                         run_once_fn=lambda *a, **k: {"mode": "live", "status": "watching"})
    assert result == {"mode": "live", "status": "watching"}
    assert json.loads((tmp_path / "health.json").read_text()) == {
        "schema": "nexus.autonomous-basket-health", "version": 1, "state": "healthy",
        "status": "watching", "ts": NOW + 1}


def test_run_cycle_live_normalizes_stale_inert_ledger_before_healthy_watching(tmp_path):
    stale = {"day": "2026-08-12", "submissions": 1, "modeled_loss_reserved": "0", "loss_streak": 0,
             "open_basket": None, "trades": {"old": {"status": "failed_prewrite", "modeled_loss": "2"}}}
    A.atomic_json(tmp_path / "ledger.json", stale)
    A.atomic_json(tmp_path / "monitor.json", {"submitted": False})
    A.atomic_json(tmp_path / "executor.json", A.Executor.new_state(None))

    def watching(*args, **kwargs):
        ledger = json.loads((tmp_path / "ledger.json").read_text())
        assert ledger["day"] == A.utc_day(NOW) and ledger["submissions"] == 0
        assert not (tmp_path / "health.json").exists()
        return {"mode": "live", "status": "watching"}

    assert A.run_cycle("live", NoTransport(), NOW, state_dir=tmp_path, wall_clock=lambda: NOW,
                       run_once_fn=watching) == {"mode": "live", "status": "watching"}
    assert json.loads((tmp_path / "health.json").read_text())["state"] == "healthy"


def test_main_max_cycles_twenty_in_process_no_executor_api(tmp_path, monkeypatch, capsys):
    calls = []
    def cycle(mode, transport, now, **kwargs):
        assert transport is A.Executor.api
        calls.append(1)
        A.atomic_json(Path(kwargs["state_dir"]) / "health.json", {"state": "healthy"})
        return {"mode": mode, "status": "watching"}
    monkeypatch.setattr(A, "run_cycle", cycle)
    monkeypatch.setattr(A, "cli_notifier", lambda *a, **k: type("N", (), {"send": lambda self, _: None})())
    monkeypatch.setattr(A, "process_lock", lambda *a, **k: nullcontext())
    A.main(["live", "--max-cycles", "20", "--interval", "0", "--state-dir", str(tmp_path)])
    assert len(calls) == 20
    assert len(capsys.readouterr().out.splitlines()) == 20
    assert (tmp_path / "health.json").exists()


def _legacy_production_shape(state):
    state.mkdir()
    identity, symbol, issued = "auto-1786580127-BNBUSDT", "BNBUSDT", 1786580127.5383234
    plan = A.build_plan([{"symbol": symbol, "side": "Buy", "zone": ["850", "851"], "trigger": "851",
                          "invalid": "850", "stop": "849", "tp": "855", "score": 1}], 1786580100, "scan-bnb-production")
    approval = A.Executor.make_approval("a1786580127", issued, 300, plan, A.EXECUTOR_SHA256)
    executor = A.Executor.new_state(None)
    executor.update(status="fatal", candidate=symbol, winner=symbol, exposure_possible=False,
                    approved=approval, planDocument=plan, planFingerprint=approval["fingerprint"],
                    executorSourceSha256=A.EXECUTOR_SHA256,
                    errors={symbol: "final market invalid: candle stale or invalid"},
                    fatal_reason="final market invalid: candle stale or invalid",
                    finalGate={"ok": False, "code": "FINAL_CANDLE_STALE_OR_INVALID"})
    docs = {"executor": executor, "monitor": {"submitted": False, "plan": plan},
            "ledger": {"day": A.utc_day(issued), "submissions": 0, "modeled_loss_reserved": "0",
                       "loss_streak": 0, "open_basket": None,
                       "trades": {identity: {"status": "failed_prewrite", "modeled_loss": "2"}}}}
    for name, doc in docs.items():
        A.atomic_json(state / f"{name}.json", doc)
    return identity


def _write_tuple(state, ledger, monitor, executor):
    state.mkdir()
    for name, doc in (("ledger", ledger), ("monitor", monitor), ("executor", executor)):
        A.atomic_json(state / f"{name}.json", doc)


def _inert_docs(status="closed"):
    return ({"modeled_loss_reserved": "0", "open_basket": None, "trades": {}},
            {"submitted": False}, {"status": status, "exposure_possible": False})


@pytest.mark.parametrize("mutate,code", [
    (lambda l, m, e: m.update(submitted=True), "CLOSED_TUPLE_MISMATCH"),
    (lambda l, m, e: l.update(open_basket="orphan", modeled_loss_reserved="2",
                              trades={"orphan": {"status": "reserved"}}), "CLOSED_TUPLE_MISMATCH"),
    (lambda l, m, e: e.update(orderLinkId="auto-residual", body={"symbol": "AAAUSDT"}),
     "CLOSED_TUPLE_MISMATCH"),
    (lambda l, m, e: e.update(status="fatal", exposure_possible=True), "FATAL_TUPLE_MISMATCH"),
    (lambda l, m, e: (e.update(status="fatal"), l.update(modeled_loss_reserved="2")),
     "FATAL_TUPLE_MISMATCH"),
])
def test_terminal_mismatch_fails_before_transport_with_exact_code(tmp_path, mutate, code):
    ledger, monitor, executor = _inert_docs()
    mutate(ledger, monitor, executor)
    state = tmp_path / "state"
    _write_tuple(state, ledger, monitor, executor)
    before = {p.name: p.read_bytes() for p in state.iterdir()}
    transport = NoTransport()
    assert A.run_cycle("live", transport, NOW, state_dir=state, wall_clock=lambda: NOW) == {
        "mode": "live", "status": "degraded", "code": code}
    assert transport.calls == []
    assert {p.name: p.read_bytes() for p in state.iterdir() if p.name != "health.json"} == before


def test_active_same_symbol_wrong_ledger_identity_fails_without_transport(tmp_path):
    ledger, monitor, executor = _inert_docs()
    ledger.update(open_basket="auto-wrong-AAAUSDT", modeled_loss_reserved="2",
                  trades={"auto-wrong-AAAUSDT": {"status": "reserved"}})
    monitor["submitted"] = False
    executor.update(status="submitting", candidate="AAAUSDT", winner="AAAUSDT",
                    orderLinkId="auto-right-AAAUSDT",
                    body={"symbol": "AAAUSDT", "orderLinkId": "auto-right-AAAUSDT"})
    state = tmp_path / "state"
    _write_tuple(state, ledger, monitor, executor)
    transport = NoTransport()
    assert A.run_cycle("live", transport, NOW, state_dir=state, wall_clock=lambda: NOW) == {
        "mode": "live", "status": "degraded", "code": "ACTIVE_EXECUTOR_LEDGER_MISMATCH"}
    assert transport.calls == []


def test_active_exact_identity_conflicting_body_symbol_fails_without_transport(tmp_path):
    identity = "auto-1-AAAUSDT"
    ledger = {"modeled_loss_reserved": "2", "open_basket": identity,
              "trades": {identity: {"status": "reserved"}}}
    monitor = {"submitted": True, "identity": identity, "winner": "AAAUSDT"}
    executor = {"status": "submitting", "candidate": "AAAUSDT", "winner": "AAAUSDT",
                "orderLinkId": identity, "body": {"symbol": "BBBUSDT", "orderLinkId": identity}}
    state = tmp_path / "state"
    _write_tuple(state, ledger, monitor, executor)
    transport = NoTransport()
    assert A.run_cycle("live", transport, NOW, state_dir=state, wall_clock=lambda: NOW) == {
        "mode": "live", "status": "degraded", "code": "ACTIVE_EXECUTOR_SYMBOL_MISMATCH"}
    assert transport.calls == []


def test_closed_nonzero_decimal_reserve_fails_without_transport(tmp_path):
    ledger, monitor, executor = _inert_docs()
    ledger["modeled_loss_reserved"] = "0.0000000001"
    state = tmp_path / "state"
    _write_tuple(state, ledger, monitor, executor)
    transport = NoTransport()
    assert A.run_cycle("live", transport, NOW, state_dir=state, wall_clock=lambda: NOW) == {
        "mode": "live", "status": "degraded", "code": "CLOSED_TUPLE_MISMATCH"}
    assert transport.calls == []


@pytest.mark.parametrize("status", ["closed", "watching"])
def test_valid_inert_terminal_shapes_are_classified(status):
    ledger, monitor, executor = _inert_docs(status)
    if status == "watching":
        executor.pop("exposure_possible")
    assert A.classify_durable_tuple(ledger, monitor, executor) == ("INERT", None)


def test_valid_active_exact_order_link_is_classified():
    identity = "auto-right-AAAUSDT"
    ledger = {"modeled_loss_reserved": "2", "open_basket": identity,
              "trades": {identity: {"status": "reserved"}}}
    executor = {"status": "submitting", "candidate": "AAAUSDT", "winner": "AAAUSDT",
                "orderLinkId": identity, "body": {"symbol": "AAAUSDT", "orderLinkId": identity}}
    assert A.classify_durable_tuple(ledger, {"submitted": False}, executor) == ("ACTIVE_OWNED", None)


def test_valid_narrow_historical_active_identity_is_classified():
    issued, symbol = 1786580127.5383234, "BNBUSDT"
    identity = f"basket-{int(issued)}-{symbol}"
    plan = A.build_plan([{"symbol": symbol, "side": "Buy", "zone": ["850", "851"], "trigger": "851",
                          "invalid": "850", "stop": "849", "tp": "855", "score": 1}], 1786580100, "scan-historical")
    approval = A.Executor.make_approval(f"a{int(issued)}", issued, 300, plan, A.EXECUTOR_SHA256)
    executor = {"status": "submitting", "candidate": symbol, "winner": symbol, "approved": approval,
                "planDocument": plan, "planFingerprint": approval["fingerprint"],
                "executorSourceSha256": A.EXECUTOR_SHA256}
    ledger = {"modeled_loss_reserved": "2", "open_basket": identity,
              "trades": {identity: {"status": "reserved", "symbol": symbol}}}
    assert A.classify_durable_tuple(ledger, {"submitted": False}, executor) == ("ACTIVE_OWNED", None)


@pytest.mark.parametrize("nonce,status", [
    ("aa1786580127", "reserved"),
    ("a1786580127", "failed_prewrite"),
])
def test_historical_identity_rejects_wrong_nonce_or_inactive_trade(nonce, status):
    issued, symbol = 1786580127.5383234, "BNBUSDT"
    plan = A.build_plan([{"symbol": symbol, "side": "Buy", "zone": ["850", "851"], "trigger": "851",
                          "invalid": "850", "stop": "849", "tp": "855", "score": 1}], 1786580100, "scan-historical")
    approval = A.Executor.make_approval(f"a{int(issued)}", issued, 300, plan, A.EXECUTOR_SHA256)
    approval["nonce"] = nonce
    executor = {"candidate": symbol, "winner": symbol, "approved": approval, "planDocument": plan,
                "planFingerprint": approval["fingerprint"], "executorSourceSha256": A.EXECUTOR_SHA256}
    assert A._derived_historical_identity(executor, status) is None


def test_actual_legacy_lf_mp_ef_shape_twenty_cycles_first_reset_then_inert(tmp_path, monkeypatch):
    state = tmp_path / "state"
    identity = _legacy_production_shape(state)
    policy = tmp_path / "policy.json"
    source = json.loads((Path(A.__file__).with_name("configs") / "autonomous_basket_policy.json").read_text())
    A.write_policy(policy, source)
    loaded = A.load_policy(policy, NOW)
    monkeypatch.setenv("AUTONOMOUS_BASKET_LIVE", "YES")
    monkeypatch.setenv("AUTONOMOUS_BASKET_POLICY_SHA256", loaded.digest)
    monkeypatch.setattr(A, "scan_market", lambda runtime, now, scan_id: A.build_plan([], now, scan_id))
    transport = NoTransport()
    results = [A.run_cycle("live", transport, NOW + tick, policy_path=policy, state_dir=state,
                           wall_clock=lambda tick=tick: NOW + tick) for tick in range(20)]
    assert all(result["status"] == "watching" for result in results)
    assert json.loads((state / "executor.json").read_text()) == A.Executor.new_state(None)
    assert json.loads((state / "ledger.json").read_text())["trades"][identity]["status"] == "failed_prewrite"
    assert transport.calls == []


def _rewrite_legacy_source(state, source):
    executor = json.loads((state / "executor.json").read_text())
    approval = executor["approved"]
    approval["executorSourceSha256"] = source
    approval["fingerprint"] = A.Executor.plan_fingerprint(executor["planDocument"], source, approval)
    executor.update(executorSourceSha256=source, planFingerprint=approval["fingerprint"])
    A.atomic_json(state / "executor.json", executor)


def _assert_reset_rejected_byte_preserving(state):
    before = {p.name: p.read_bytes() for p in state.glob("*.json")}
    with pytest.raises(A.FailClosed):
        A._reset_repaired_terminal(state)
    assert {p.name: p.read_bytes() for p in state.glob("*.json")} == before


def test_legacy_reset_rejects_coordinated_arbitrary_source_tamper(tmp_path):
    state = tmp_path / "state"; _live_released_snapshot(state)
    _rewrite_legacy_source(state, "d" * 64)
    _assert_reset_rejected_byte_preserving(state)


@pytest.mark.parametrize("extra_identity", [
    "basket-1786580127-BNBUSDT",
    "auto-1786580127-BNBUSDT-extra",
    "auto-1786580127-BNBUSDTx",
    "auto-01786580127-BNBUSDT",
])
def test_legacy_reset_rejects_equivalent_or_malformed_near_match(tmp_path, extra_identity):
    state = tmp_path / "state"; _live_released_snapshot(state)
    ledger = json.loads((state / "ledger.json").read_text())
    ledger["trades"][extra_identity] = {"modeled_loss": "2", "status": "failed_prewrite"}
    A.atomic_json(state / "ledger.json", ledger)
    _assert_reset_rejected_byte_preserving(state)


@pytest.mark.parametrize("error", ["anything", "winner current market invalid", "FINAL_CANDLE_STALE_OR_INVALID "])
def test_legacy_reset_rejects_unapproved_fatal_error_evidence(tmp_path, error):
    state = tmp_path / "state"; _live_released_snapshot(state)
    executor = json.loads((state / "executor.json").read_text())
    executor["errors"] = {"BNBUSDT": error}
    A.atomic_json(state / "executor.json", executor)
    _assert_reset_rejected_byte_preserving(state)


def test_legacy_reset_rejects_fatal_code_under_wrong_candidate_key(tmp_path):
    state = tmp_path / "state"; _live_released_snapshot(state)
    executor = json.loads((state / "executor.json").read_text())
    executor["errors"] = {"BTCUSDT": "FINAL_CANDLE_STALE_OR_INVALID"}
    A.atomic_json(state / "executor.json", executor)
    _assert_reset_rejected_byte_preserving(state)
