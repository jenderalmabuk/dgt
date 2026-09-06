import importlib.util
from pathlib import Path

P = Path(__file__).parents[1] / "bots" / "spcx_live_executor.py"
spec = importlib.util.spec_from_file_location("spcx", P)
spcx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(spcx)


def test_safe_qty_caps_total_modeled_loss():
    qty = spcx.safe_qty(135.55, 133.90, 2.0, 0.01)
    assert qty > 0
    assert spcx.modeled_loss(qty, 135.55, 133.90) <= 2.0
    assert spcx.modeled_loss(qty + 0.01, 135.55, 133.90) > 2.0


def test_zone_touch_arms_reversal_but_does_not_enter():
    touched, valid = spcx.update_trigger(False, 135.40, 135.30)
    assert touched
    assert not valid


def test_reversal_only_valid_after_zone_touch():
    assert spcx.update_trigger(False, 135.90, 135.71) == (False, False)
    assert spcx.update_trigger(True, 135.80, 135.71) == (True, True)


def test_market_guards_fail_closed():
    assert spcx.market_ok(-0.0002, 0.05, 0.0, 0.0)
    assert not spcx.market_ok(0.0001, 0.05, 0.0, 0.0)
    assert not spcx.market_ok(-0.0002, 0.09, 0.0, 0.0)
    assert not spcx.market_ok(-0.0002, 0.05, -3.1, 0.0)
    assert not spcx.market_ok(-0.0002, 0.05, 0.0, -1.6)


def test_rr_and_invalidation():
    assert spcx.rr(135.55, 133.90, 139.40) >= 2
    assert spcx.invalidated(134.79, 135.0)
    assert spcx.invalidated(135.5, 137.21)
    assert not spcx.invalidated(135.5, 136.0)
