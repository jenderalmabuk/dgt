"""Regression tests: zone reuse after terminal + candle mismatch rejection."""
import pandas as pd
import pytest
from fusion_quantum.paper_runner import State, lifecycle_key, canonical_zone_key


def test_terminal_zone_blocks_new_setup_canonical_identity():
    """Terminal zone (any timestamp format) blocks new setup with canonical identity."""
    state = State()
    
    # Historical zone: naive timestamp format (old bug)
    old_setup = {
        "symbol": "ACTUSDT",
        "side": "BEAR",
        "zone_key": ["BEAR", "2026-07-23T08:00:00", 0.5, 1.0],
        "first_touch_at": "2026-07-23 13:30:00"
    }
    
    old_key = lifecycle_key(old_setup)
    old_zid = f"zone_{old_key[:19]}" if old_key else "zone_old"
    
    # Mark as terminal
    state.set_status(old_zid, "failed_confirmation", setup=old_setup)
    
    # New setup: timezone-aware format (current)
    new_setup = {
        "symbol": "ACTUSDT",
        "side": "BEAR",
        "zone_key": ["BEAR", "2026-07-23T08:00:00+00:00", 0.5, 1.0],
        "first_touch_at": "2026-08-03 00:00:00"
    }
    
    new_key = lifecycle_key(new_setup)
    new_zid = f"zone_{new_key[:19]}" if new_key else "zone_new"
    
    # Canonical identity should make them identical
    assert old_key == new_key, "Canonical identity failed: naive vs aware should match"
    assert old_zid == new_zid, "Zone ID should be identical"
    
    # New setup cannot claim because zone already terminal
    assert not state.claim(new_zid), "Terminal zone allowed new claim"
    
    # Verify terminal status preserved
    assert state.data["setups"][old_zid]["status"] == "failed_confirmation"


def test_canonical_zone_key_deduplicates_timezone_variants():
    """All timezone variants of same zone produce identical canonical key."""
    # Naive
    key_naive = canonical_zone_key(["BULL", "2026-07-21T04:00:00", 0.5, 1.0])
    
    # UTC explicit
    key_utc = canonical_zone_key(["BULL", "2026-07-21T04:00:00+00:00", 0.5, 1.0])
    
    # Different timezone same instant
    key_tz = canonical_zone_key(["BULL", "2026-07-21T00:00:00-04:00", 0.5, 1.0])
    
    assert key_naive == key_utc, "Naive and UTC variants should match"
    assert key_naive == key_tz, "Different timezone same instant should match"
    assert key_naive[1] == "2026-07-21T04:00:00+00:00", "Canonical format should be UTC ISO"


def test_zone_reuse_prevented_across_restart():
    """Zone terminal state survives state reload (migration scenario)."""
    import tempfile
    from pathlib import Path
    
    with tempfile.TemporaryDirectory() as tmpdir:
        state_path = Path(tmpdir) / "state.json"
        
        # First session: terminal zone
        state1 = State(state_path)
        setup1 = {
            "symbol": "ACTUSDT",
            "side": "BEAR",
            "zone_key": ["BEAR", "2026-07-23T08:00:00", 0.5, 1.0]
        }
        zid = f"zone_{lifecycle_key(setup1)[:19]}"
        state1.set_status(zid, "failed_confirmation", setup=setup1)
        
        # Second session: reload state
        state2 = State(state_path)
        
        # New setup same zone
        setup2 = {
            "symbol": "ACTUSDT",
            "side": "BEAR",
            "zone_key": ["BEAR", "2026-07-23T08:00:00+00:00", 0.5, 1.0]
        }
        
        # Should not claim because terminal persisted
        assert not state2.claim(zid), "Terminal zone survived reload but allowed claim"
        assert state2.data["setups"][zid]["status"] == "failed_confirmation"


def test_awaiting_confirmation_non_terminal():
    """awaiting_confirmation status is non-terminal; later confirmation can proceed."""
    state = State()
    
    setup = {
        "symbol": "BTCUSDT",
        "side": "BULL",
        "zone_key": ["BULL", "2026-08-03T00:00:00+00:00", 60000.0, 61000.0],
        "first_touch_at": "2026-08-03 01:00:00"
    }
    
    zid = f"zone_{lifecycle_key(setup)[:19]}"
    
    # First: awaiting
    state.set_status(zid, "awaiting_confirmation", setup=setup)
    
    # Later: confirmed on same zone (MSS arrived)
    setup_confirmed = dict(setup, confirmed_at="2026-08-03 01:45:00")
    
    # Should be able to transition from awaiting to confirmed
    state.set_status(zid, "confirmed", setup=setup_confirmed)
    
    assert state.data["setups"][zid]["status"] == "confirmed"


def test_failed_confirmation_terminal():
    """failed_confirmation is terminal; no further confirmation allowed."""
    state = State()
    
    setup = {
        "symbol": "ETHUSDT",
        "side": "BEAR",
        "zone_key": ["BEAR", "2026-08-02T00:00:00+00:00", 3000.0, 3100.0],
        "first_touch_at": "2026-08-02 04:00:00"
    }
    
    zid = f"zone_{lifecycle_key(setup)[:19]}"
    
    # Failed confirmation (24 bars no MSS)
    state.set_status(zid, "failed_confirmation", setup=setup)
    
    # Attempt new confirmation should be blocked by lifecycle check
    # (in real code, lifecycle_claimed() checks terminal status)
    row = state.data["setups"].get(zid)
    assert row is not None
    assert row["status"] == "failed_confirmation"
    
    # Cannot claim again
    assert not state.claim(zid), "failed_confirmation allowed re-claim"
