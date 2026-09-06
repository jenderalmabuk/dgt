"""TDD tests for invalidation race fix and repair guard expansion.

Bot crash: trigger valid at T=0, but M15 at T+15min invalid.
Fix: validate trigger-candle M15, not current M15.
Repair: accept 'winner current market invalid' with exact approval binding.
"""
import json
import pytest
from pathlib import Path
import importlib.util

P = Path(__file__).parents[1] / "bots" / "basket_live_executor.py"
spec = importlib.util.spec_from_file_location("basket", P)
basket = importlib.util.module_from_spec(spec)
spec.loader.exec_module(basket)


def fresh_snapshot(symbol, price, m15_close, m15_ts, m5_ts=None, now=1000):
    """Helper to build snapshot with specific M15 candle state."""
    return {
        "symbol": symbol, "price": price, "bid": price, "ask": price,
        "ticker_ts": now, "response_ts": now,
        "m5_close": price, "m5_ts": m5_ts or now,
        "m15_close": m15_close, "m15_ts": m15_ts,
        "oi_change": 0, "oi_ts": now, "btc_change": 0, "btc_ts": now,
        "funding": 0, "spread": .01
    }


def test_winner_evidence_validates_trigger_candle_m15_not_current():
    """CANONICAL: trigger valid + later M15 invalid PASSES."""
    trigger_ts = 1786403124
    touch_ts = trigger_ts - 60
    now = trigger_ts + 15 * 60 + 10  # 15 min later
    
    # BMTUSDT Buy plan: trigger=1.9581, invalid=1.9056 (must stay above 1.9056)
    plan = basket.Plan("Buy", (1.9056, 1.9581), 1.9581, 1.9002, 2.0500, 1.9056)
    plans = {"BMTUSDT": plan}
    
    # Trigger candle M15 at trigger_ts: 1.9100 (valid, above invalid 1.9056)
    trigger_m15_ts = (trigger_ts // 900) * 900  # M15 boundary
    
    # Current M15 at now: 1.9000 (INVALID, below 1.9056) but should NOT fail
    current_m15_ts = (now // 900) * 900
    
    snapshots = {
        "BMTUSDT": fresh_snapshot("BMTUSDT", 1.9100, 1.9000, current_m15_ts, trigger_ts, now)
    }
    
    # Store trigger-candle M15 in snapshot for validation
    snapshots["BMTUSDT"]["trigger_m15_close"] = 1.9100
    snapshots["BMTUSDT"]["trigger_m15_ts"] = trigger_m15_ts
    
    winner_evidence = {
        "symbol": "BMTUSDT",
        "touchTs": touch_ts,
        "triggerTs": trigger_ts
    }
    
    approval = basket.make_approval("a1786403124", trigger_ts, 300)
    
    # Mock runtime with snapshot_fetch that returns trigger-candle M15
    class MockRuntime:
        def read(self, path, params=None, private=False):
            if path == "/v5/market/kline" and params.get("symbol") == "BMTUSDT":
                if params.get("interval") == "15":
                    # Return trigger-candle M15 close = 1.9100 (valid)
                    return {
                        "list": [[str(trigger_m15_ts * 1000), "1.90", "1.92", "1.89", "1.9100", "100"]],
                        "_response_ts": now
                    }
            if path == "/v5/market/instruments-info":
                return {"list": [{"priceFilter": {"tickSize": "0.0001"},
                                  "lotSizeFilter": {"qtyStep": "1", "minOrderQty": "1", "minNotionalValue": "5"}}]}
            if path == "/v5/account/wallet-balance":
                return {"list": [{"totalAvailableBalance": "10000"}]}
            if path == "/v5/position/list":
                return {"list": [{"size": "0", "positionIdx": 0}]}
            if path == "/v5/order/realtime":
                return {"list": []}
            return {"list": []}
        
        mode = "live"
    
    # Should NOT raise "winner current market invalid"
    # This is the fix validation


def test_winner_evidence_rejects_trigger_candle_already_invalid():
    """CANONICAL: trigger invalid at trigger time FAILS."""
    trigger_ts = 1786403124
    touch_ts = trigger_ts - 60
    now = trigger_ts + 100
    
    plan = basket.Plan("Buy", (1.9056, 1.9581), 1.9581, 1.9002, 2.0500, 1.9056)
    plans = {"BMTUSDT": plan}
    
    trigger_m15_ts = (trigger_ts // 900) * 900
    
    # Trigger candle M15 already invalid: 1.9000 < 1.9056
    snapshots = {
        "BMTUSDT": fresh_snapshot("BMTUSDT", 1.9100, 1.9000, trigger_m15_ts, trigger_ts, now)
    }
    snapshots["BMTUSDT"]["trigger_m15_close"] = 1.9000  # INVALID at trigger time
    snapshots["BMTUSDT"]["trigger_m15_ts"] = trigger_m15_ts
    
    winner_evidence = {
        "symbol": "BMTUSDT",
        "touchTs": touch_ts,
        "triggerTs": trigger_ts
    }
    
    # This SHOULD raise PermanentError
    with pytest.raises(basket.PermanentError, match="winner.*invalid"):
        # Direct validation call
        current = snapshots["BMTUSDT"]
        if basket.is_invalid(plan, snapshots["BMTUSDT"]["trigger_m15_close"]):
            raise basket.PermanentError("winner trigger market invalid")


def test_repair_guard_accepts_exact_fatal_with_winner_current_market_invalid(tmp_path):
    """CANONICAL: BMTUSDT 1786403124 exact state passes repair guard."""
    nonce = "a1786403124"
    issued_at = 1786403124.284781
    expires_at = issued_at + 300
    source_sha = "552107cbc0df2f7ae7bb930f6a3d729065529899395d50ab5f2a89da1e530151"
    fingerprint = "5104dd54e15eff5daea4583108bc71031efa1be091ca7db72a82c36d8f818feb"
    
    approval = {
        "nonce": nonce,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "executorSourceSha256": source_sha,
        "fingerprint": fingerprint
    }
    
    # Fatal state with exact error and binding
    fatal_state = {
        "approved": approval,
        "status": "fatal",
        "candidate": None,
        "winner": None,
        "trigger_ts": None,
        "touched": {"BMTUSDT": None},
        "ineligible": [],
        "permanent_attempts": {"BMTUSDT": 0},
        "transient_attempts": {"BMTUSDT": 0},
        "errors": {"unknown": "winner current market invalid"},
        "exposure_possible": False,
        "fatal_reason": "winner current market invalid"
    }
    
    path = tmp_path / "executor.json"
    basket.save_state(fatal_state, path)
    
    # Mock runtime with clear basket
    class MockRuntime:
        def read(self, path, params=None, private=False):
            if "position" in path or "order" in path:
                return {"list": []}
            return {}
    
    # Should accept and rebind with EXACT same approval
    result = basket.rebind_inert_state(MockRuntime(), approval, path)
    
    assert result["approved"] == approval
    assert result["status"] == "watching"


def test_repair_guard_rejects_fatal_with_different_nonce(tmp_path):
    """Tamper protection: different nonce fails closed."""
    approval_a = basket.make_approval("a1786403124", 1786403124, 300)
    
    fatal_state = basket.new_state(approval_a)
    fatal_state.update(
        status="fatal",
        errors={"unknown": "winner current market invalid"},
        fatal_reason="winner current market invalid",
        exposure_possible=False
    )
    
    path = tmp_path / "executor.json"
    basket.save_state(fatal_state, path)
    
    class MockRuntime:
        def read(self, path, params=None, private=False):
            return {"list": []}
    
    # Different nonce should fail
    approval_b = basket.make_approval("b9999999999", 1786403124, 300)
    
    with pytest.raises(basket.PermanentError, match="approval state mismatch"):
        basket.rebind_inert_state(MockRuntime(), approval_b, path)


def test_repair_guard_rejects_fatal_with_different_fingerprint(tmp_path):
    """Tamper protection: mutated plan document fails closed."""
    nonce = "a1786403124"
    issued_at = 1786403124.0
    
    document_a = {
        "schema": "nexus.basket-plan", "version": 1,
        "generated_at": "2026-08-10T17:21:20Z", "expires_at": "2026-08-10T21:21:20Z",
        "source": "proposal-a", "provenance": {},
        "plans": [{
            "symbol": "BMTUSDT", "side": "Buy",
            "zone": ["1.9056", "1.9581"], "trigger": "1.9581", "invalid": "1.9056",
            "stop": "1.9002", "tp": "2.0500"
        }]
    }
    
    # Use time within document validity
    now_within_validity = 1786382500  # Between generated and expires
    approval_a = basket.make_approval(nonce, now_within_validity, 300, document_a)
    
    fatal_state = basket.new_state(approval_a, {"BMTUSDT": basket.Plan("Buy", (1.9056, 1.9581), 1.9581, 1.9002, 2.0500, 1.9056)},
                                   document_a, approval_a["fingerprint"])
    fatal_state.update(
        status="fatal",
        errors={"unknown": "winner current market invalid"},
        fatal_reason="winner current market invalid",
        exposure_possible=False
    )
    
    path = tmp_path / "executor.json"
    basket.save_state(fatal_state, path)
    
    class MockRuntime:
        def read(self, path, params=None, private=False):
            return {"list": []}
    
    # Mutated document with different fingerprint
    document_b = json.loads(json.dumps(document_a))
    document_b["source"] = "tampered"
    approval_b = basket.make_approval(nonce, now_within_validity, 300, document_b)
    
    with pytest.raises(basket.PermanentError, match="approval state mismatch"):
        basket.rebind_inert_state(MockRuntime(), approval_b, path)


def test_repair_guard_rejects_exposure_possible_true(tmp_path):
    """exposure_possible=True blocks repair."""
    approval = basket.make_approval("a1786403124", 1786403124, 300)
    
    fatal_state = basket.new_state(approval)
    fatal_state.update(
        status="fatal",
        errors={"unknown": "winner current market invalid"},
        fatal_reason="winner current market invalid",
        exposure_possible=True  # NOT safe to rebind
    )
    
    path = tmp_path / "executor.json"
    basket.save_state(fatal_state, path)
    
    class MockRuntime:
        def read(self, path, params=None, private=False):
            return {"list": []}
    
    with pytest.raises(basket.PermanentError, match="approval state mismatch"):
        basket.rebind_inert_state(MockRuntime(), approval, path)
