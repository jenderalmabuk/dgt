import importlib.util
from pathlib import Path

P = Path(__file__).parents[1] / "bots" / "mmt_live_executor.py"
spec = importlib.util.spec_from_file_location("mmt", P)
mmt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mmt)


def test_qty_keeps_modeled_loss_below_two_dollars():
    qty = mmt.safe_qty(entry=0.2195, stop=0.2142, risk_cap=2.0, qty_step=0.1)
    assert qty == 311.6
    assert mmt.modeled_loss(qty, 0.2195, 0.2142) <= 2.0
    assert mmt.modeled_loss(qty + 0.1, 0.2195, 0.2142) > 2.0


def test_valid_gate_accepts_only_approved_zone():
    assert mmt.valid_gate(price=0.2195, m5_close=0.2190, funding=-0.0002,
                          spread_pct=0.05, oi_change_pct=0.0, btc_1h_pct=0.0)


def test_gate_rejects_chase_and_bad_market_conditions():
    assert not mmt.valid_gate(0.2210, 0.2190, -0.0002, 0.05, 0.0, 0.0)
    assert not mmt.valid_gate(0.2195, 0.2179, -0.0002, 0.05, 0.0, 0.0)
    assert not mmt.valid_gate(0.2195, 0.2190, 0.0001, 0.05, 0.0, 0.0)
    assert not mmt.valid_gate(0.2195, 0.2190, -0.0002, 0.11, 0.0, 0.0)
    assert not mmt.valid_gate(0.2195, 0.2190, -0.0002, 0.05, -3.1, 0.0)
    assert not mmt.valid_gate(0.2195, 0.2190, -0.0002, 0.05, 0.0, -1.6)


def test_rr_and_expiry_guards():
    assert mmt.rr(0.2195, 0.2142, 0.2375) > 3
    assert not mmt.plan_active(now_ts=200, approved_ts=0, expiry_s=100)
    assert mmt.plan_active(now_ts=99, approved_ts=0, expiry_s=100)
