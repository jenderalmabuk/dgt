import json

from bots import autonomous_basket_trader as A
from bots.autonomous_basket_notifier import TelegramNotifier


class Sender:
    def __init__(self, outcomes=None):
        self.calls = []
        self.outcomes = iter(outcomes or [True] * 20)

    def __call__(self, token, chat, text, timeout):
        self.calls.append(text)
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def notifier(tmp_path, sender):
    return TelegramNotifier(tmp_path, "token", "chat", sender)


def test_setup_change_exact_rejection_and_no_stale_setup(tmp_path):
    sender = Sender(); n = notifier(tmp_path, sender)
    plan = {"generated_at": "2026-01-01T00:00:00Z", "expires_at": "2026-01-01T01:00:00Z", "plans": [{"symbol": "<X>", "side": "Buy", "zone": [1, 2], "trigger": 2, "stop": 1, "tp": 4}]}
    A.emit_lifecycle_events({}, {"monitor": {"plan": plan, "submitted": False}}, n)
    assert "Setup" in sender.calls[0] and "&lt;X&gt;" in sender.calls[0]
    A.emit_lifecycle_events({"monitor": {"plan": plan, "submitted": False}}, {"monitor": {"plan": plan, "submitted": False}}, n)
    fatal = {"status": "fatal", "candidate": "MSFT", "finalGate": {"code": "FINAL_SPREAD_EXCEEDED", "message": "spread exact"}}
    A.emit_lifecycle_events({}, {"executor": fatal}, n)
    assert all(x in sender.calls[-1] for x in ("MSFT", "FINAL_SPREAD_EXCEEDED", "spread exact"))
    assert len(sender.calls) == 2


def test_submitted_fill_close_fields_restart_dedup_and_zero_transport(tmp_path):
    sender = Sender(); n = notifier(tmp_path, sender); writes = []
    body = {"symbol": "BTCUSDT", "side": "Buy", "price": "100", "qty": "2", "stopLoss": "90", "takeProfit": "120", "orderLinkId": "basket-1-BTCUSDT"}
    submitted = {"status": "submitted", "body": body, "orderLinkId": body["orderLinkId"]}
    filled = {**submitted, "status": "open", "avgPrice": "101", "cumExecQty": "2"}
    closed = {**filled, "status": "closed", "actualPnl": "7.5", "closePrice": "119", "closeReason": "TP"}
    A.emit_lifecycle_events({}, {"executor": submitted}, n)
    A.emit_lifecycle_events({"executor": submitted}, {"executor": filled}, n)
    A.emit_lifecycle_events({"executor": filled}, {"executor": closed}, n)
    assert all(x in sender.calls[1] for x in ("BTCUSDT", "Buy", "101", "2", "90", "120", "1.7272727272727273", "basket-1-BTCUSDT", "TradingView"))
    assert all(x in sender.calls[2] for x in ("TP", "7.5", "119"))
    restarted = notifier(tmp_path, sender)
    A.emit_lifecycle_events({}, {"executor": submitted}, restarted)
    assert len(sender.calls) == 3 and writes == []


def test_orphan_ledger_dedup_until_recovery_uses_autonomous_basket_label_and_retries_failure(tmp_path):
    sender = Sender([OSError("offline"), True, True]); n = notifier(tmp_path, sender)
    healthy1={"state":"healthy","ts":1}; degraded1={"state":"degraded","code":"ORPHAN_LEDGER","incident_id":"ORPHAN_LEDGER:1","exception":{"message":"orphan"},"ts":2}
    degraded2={**degraded1,"ts":3}
    A.emit_lifecycle_events({"health":healthy1},{"health":degraded1},n)
    A.emit_lifecycle_events({"health":degraded1},{"health":degraded2},n)
    A.emit_lifecycle_events({"health":degraded2},{"health":degraded2},n)
    assert len(sender.calls)==2
    assert "Autonomous Basket" in sender.calls[-1] and "Imbalanced_bot" not in sender.calls[-1]
    healthy2={"state":"healthy","ts":4}
    A.emit_lifecycle_events({"health":healthy2},{"health":{**degraded1,"incident_id":"ORPHAN_LEDGER:2","ts":5}},n)
    assert len(sender.calls)==3


def _closing_state(path, identity, pnl):
    path.mkdir(exist_ok=True)
    body = {"symbol": identity.rsplit("-", 1)[-1], "side": "Buy", "positionIdx": 0,
            "qty": "1", "orderType": "Limit", "orderLinkId": identity}
    close_link = ("autoclose-" + identity)[-36:]
    executor = {"status": "exit_reconciling", "winner": body["symbol"], "body": body,
                "orderLinkId": identity, "closeOrderLinkId": close_link,
                "closeBody": {"symbol": body["symbol"], "side": "Sell", "positionIdx": 0,
                              "qty": "1", "orderType": "Market", "reduceOnly": True,
                              "orderLinkId": close_link}, "closeNotified": False}
    ledger = {"day": "2026-08-14", "submissions": 1, "modeled_loss_reserved": "2",
              "loss_streak": 0, "open_basket": identity,
              "trades": {identity: {"status": "reserved", "modeled_loss": "2"}}}
    A.atomic_json(path / "executor.json", executor); A.atomic_json(path / "ledger.json", ledger)

    def transport(endpoint, params=None, method="GET", body=None, private=False):
        if endpoint == "/v5/order/realtime":
            return {"list": [{**executor["closeBody"], "orderId": "close-" + identity,
                               "orderStatus": "Filled"}], "_response_ts": 100}
        if endpoint == "/v5/position/list": return {"list": [], "_response_ts": 100}
        if endpoint == "/v5/position/closed-pnl":
            return {"list": [{"orderId": "close-" + identity, "closedPnl": pnl}], "_response_ts": 100}
        raise AssertionError(endpoint)
    return transport


def test_close_delivery_false_retries_after_restart_then_dedups(tmp_path):
    identity = "basket-1786707916-APRUSDT"; pnl = "-1.93123065"
    transport = _closing_state(tmp_path, identity, pnl)
    first_sender = Sender([False]); first = notifier(tmp_path, first_sender)
    assert A.run_cycle("live", transport, 100, state_dir=tmp_path, wall_clock=lambda: 100,
                       telegram_notifier=first) == {"mode": "live", "status": "closed", "pnl": pnl}
    assert json.loads((tmp_path / "executor.json").read_text())["closeNotified"] is False
    assert not (tmp_path / "notifications.json").exists()

    second_sender = Sender([True]); restarted = notifier(tmp_path, second_sender)
    A.run_cycle("live", transport, 101, state_dir=tmp_path, wall_clock=lambda: 101,
                telegram_notifier=restarted)
    A.run_cycle("live", transport, 102, state_dir=tmp_path, wall_clock=lambda: 102,
                telegram_notifier=restarted)
    saved = json.loads((tmp_path / "executor.json").read_text())
    assert saved["closeNotified"] is True and saved["actualPnl"] == pnl
    assert len(second_sender.calls) == 1 and identity in second_sender.calls[0] and pnl in second_sender.calls[0]
    assert json.loads((tmp_path / "notifications.json").read_text()) == [f"close:{identity}:closed"]


def test_distinct_basket_closes_have_distinct_ids_and_messages(tmp_path):
    sender = Sender(); n = notifier(tmp_path, sender)
    for tick, identity, pnl in ((100, "basket-1-AAAUSDT", "1.00000001"),
                                (101, "basket-2-BBBUSDT", "-2.00000002")):
        state = tmp_path / identity; transport = _closing_state(state, identity, pnl)
        A.run_cycle("live", transport, tick, state_dir=state, wall_clock=lambda tick=tick: tick,
                    telegram_notifier=n)
    assert len(sender.calls) == 2
    assert "basket-1-AAAUSDT" in sender.calls[0] and "1.00000001" in sender.calls[0]
    assert "basket-2-BBBUSDT" in sender.calls[1] and "-2.00000002" in sender.calls[1]
    assert json.loads((tmp_path / "notifications.json").read_text()) == [
        "close:basket-1-AAAUSDT:closed", "close:basket-2-BBBUSDT:closed"]


def test_degraded_run_cycle_and_failed_send_retries_next_cycle(tmp_path):
    sender = Sender([OSError("offline"), True]); n = notifier(tmp_path, sender)
    def fail(*args, **kwargs):
        raise A.FailClosed("feed down")
    first = A.run_cycle("live", lambda *_: (_ for _ in ()).throw(AssertionError("exchange call")), 10, state_dir=tmp_path, run_once_fn=fail, wall_clock=lambda: 10, telegram_notifier=n)
    assert first == {"mode": "live", "status": "degraded", "code": "FailClosed"}
    (tmp_path / "health.json").unlink()
    second = A.run_cycle("live", lambda *_: (_ for _ in ()).throw(AssertionError("exchange call")), 10, state_dir=tmp_path, run_once_fn=fail, wall_clock=lambda: 10, telegram_notifier=n)
    assert second["status"] == "degraded" and len(sender.calls) == 2
    assert json.loads((tmp_path / "notifications.json").read_text())[0].startswith("degraded:")
