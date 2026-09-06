from copy import deepcopy

from execution.paper_mainnet_trader import PaperMainnetTrader


def bars(count=20, *, start=100.0):
    out = []
    for i in range(count):
        close = start + i * 0.2
        out.append({
            "open_time": f"2026-08-28T{(i // 4):02d}:{(i % 4) * 15:02d}:00+00:00",
            "open": close - 0.1, "high": close + 0.5,
            "low": close - 0.5, "close": close, "volume": 1000,
        })
    # Latest row represents forming candle and must never affect setup.
    out.append({"open_time": "2026-08-28T05:00:00+00:00", "open": 110,
                "high": 999, "low": 1, "close": 500, "volume": 1000})
    return out


def position(side="LONG"):
    return {
        "symbol": "BTCUSDT", "side": side, "entry_price": 104.0,
        "sl_price": 95.0, "provider_sl_original": 95.0,
        "tp_ladder": [110.0, 115.0], "qty": 2.0, "initial_qty": 2.0,
        "notional": 208.0, "entry_fee": 0.104,
    }


def test_smart_plan_uses_closed_15m_structure_atr_and_one_r_tp1():
    plan = PaperMainnetTrader._build_smart_exit_plan(
        position(), bars(), equity=1000.0, risk_pct=0.01)
    assert plan["candidate_available"] is True
    assert plan["timeframe"] == "15m"
    assert plan["atr_period"] == 14
    assert plan["sl_price"] < min(float(x["low"]) for x in bars()[-6:-1])
    assert plan["tp1_price"] >= 104.0 + (104.0 - plan["sl_price"])
    assert plan["risk_budget_usd"] == 10.0
    assert plan["qty"] != position()["qty"]
    assert plan["qty"] * 104.0 <= plan["notional_cap_usd"]


def test_smart_plan_ignores_forming_candle_and_preserves_provider_fields():
    pos = position(); original = deepcopy(pos)
    first = PaperMainnetTrader._build_smart_exit_plan(pos, bars(), equity=1000.0, risk_pct=0.01)
    changed = bars(); changed[-1].update(high=99999, low=0.0001, close=50000)
    second = PaperMainnetTrader._build_smart_exit_plan(pos, changed, equity=1000.0, risk_pct=0.01)
    assert first["sl_price"] == second["sl_price"]
    assert first["tp1_price"] == second["tp1_price"]
    assert first["last_candle_id"] == bars()[-2]["open_time"]
    assert pos == original


def test_smart_shadow_stops_first_when_sl_and_tp_touch_same_bar():
    plan = PaperMainnetTrader._build_smart_exit_plan(
        position(), bars(), equity=1000.0, risk_pct=0.01)
    candle = {"open": 104.0, "high": plan["tp1_price"] + 1,
              "low": plan["sl_price"] - 1, "close": 104.0,
              "closed_at": "2026-08-28T06:00:00+00:00"}
    PaperMainnetTrader._advance_smart_exit_plan(plan, candle)
    assert plan["status"] == "CLOSED"
    assert plan["exit_reason"] == "SMART_SL"


def test_smart_shadow_tp1_activates_monotonic_atr_trailing():
    plan = PaperMainnetTrader._build_smart_exit_plan(
        position(), bars(), equity=1000.0, risk_pct=0.01)
    tp = plan["tp1_price"]
    PaperMainnetTrader._advance_smart_exit_plan(plan, {
        "open": 104, "high": tp + 0.5, "low": 103.5, "close": tp,
        "closed_at": "2026-08-28T06:00:00+00:00"})
    assert plan["tp1_hit"] is True
    first_stop = plan["active_stop"]
    PaperMainnetTrader._advance_smart_exit_plan(plan, {
        "open": tp, "high": tp + 3, "low": tp - 0.1, "close": tp + 2,
        "closed_at": "2026-08-28T06:15:00+00:00"})
    assert plan["active_stop"] >= first_stop
    assert plan["status"] == "OPEN"


def test_smart_plan_unavailable_with_insufficient_closed_bars():
    plan = PaperMainnetTrader._build_smart_exit_plan(
        position(), bars(10), equity=1000.0, risk_pct=0.01)
    assert plan == {"candidate_available": False, "reason": "INSUFFICIENT_COMPLETED_15M_BARS"}


def test_smart_shadow_rejects_candle_closed_before_actual_fill():
    pos = position()
    pos["smart_exit_shadow"] = PaperMainnetTrader._build_smart_exit_plan(
        pos, bars(), equity=1000.0, opened_at="2026-08-28T05:10:00+00:00")
    changed = bars()
    changed[-2]["open_time"] = "2026-08-28T05:00:00+00:00"  # closes 05:15, valid
    PaperMainnetTrader._update_smart_exit_shadow(pos, changed)
    assert len(pos["smart_exit_shadow"]["observations"]) == 1
    pos["smart_exit_shadow"]["opened_at"] = "2026-08-28T05:20:00+00:00"
    pos["smart_exit_shadow"]["last_candle_id"] = "older"
    PaperMainnetTrader._update_smart_exit_shadow(pos, changed)
    assert len(pos["smart_exit_shadow"]["observations"]) == 1


def test_smart_shadow_processes_each_completed_15m_candle_once():
    pos = position()
    pos["smart_exit_shadow"] = PaperMainnetTrader._build_smart_exit_plan(
        pos, bars(), equity=1000.0, risk_pct=0.01)
    provider = {key: deepcopy(pos[key]) for key in
                ("sl_price", "provider_sl_original", "tp_ladder", "qty", "initial_qty")}
    latest = bars()
    PaperMainnetTrader._update_smart_exit_shadow(pos, latest)
    observations = len(pos["smart_exit_shadow"]["observations"])
    PaperMainnetTrader._update_smart_exit_shadow(pos, latest)
    assert len(pos["smart_exit_shadow"]["observations"]) == observations
    assert {key: pos[key] for key in provider} == provider


def liquidity_grab_bars(side="LONG"):
    rows = bars(20)
    if side == "LONG":
        # Confirmed pivot low at index 8; later candle sweeps then reclaims it.
        rows[8].update(low=98.0, high=101.0, close=100.0)
        rows[9].update(low=99.0); rows[10].update(low=99.2)
        rows[14].update(low=97.8, high=101.0, close=98.5)
    else:
        rows[8].update(high=106.0, low=101.0, close=104.0)
        rows[9].update(high=105.0); rows[10].update(high=104.8)
        rows[14].update(high=106.2, low=102.0, close=105.5)
    return rows


def test_liquidity_grab_plan_uses_confirmed_sweep_reclaim_before_entry():
    plan = PaperMainnetTrader._build_liquidity_grab_exit_plan(
        position(), liquidity_grab_bars(), equity=1000.0, risk_pct=0.01)
    assert plan["candidate_available"] is True
    assert plan["structure_type"] == "LIQUIDITY_GRAB"
    assert plan["sweep_index"] == 14
    assert plan["sl_price"] < 97.8
    assert plan["tp1_r"] == 1.0
    assert plan["config_version"] == "smart_exit_liquidity_grab_v1"


def test_liquidity_grab_plan_is_side_aware_for_short():
    pos = position("SHORT"); pos["entry_price"] = 104.0
    plan = PaperMainnetTrader._build_liquidity_grab_exit_plan(
        pos, liquidity_grab_bars("SHORT"), equity=1000.0, risk_pct=0.01)
    assert plan["candidate_available"] is True
    assert plan["sl_price"] > 106.2
    assert plan["tp1_price"] < pos["entry_price"]


def test_liquidity_grab_plan_fails_closed_without_confirmed_sweep():
    plan = PaperMainnetTrader._build_liquidity_grab_exit_plan(
        position(), bars(), equity=1000.0, risk_pct=0.01)
    assert plan == {"candidate_available": False, "reason": "NO_CONFIRMED_LIQUIDITY_GRAB"}


def test_parallel_smart_arms_advance_without_provider_mutation():
    pos = position(); original = deepcopy(pos)
    pos["smart_exit_shadow"] = PaperMainnetTrader._build_smart_exit_plan(
        pos, liquidity_grab_bars(), equity=1000.0)
    pos["smart_exit_liquidity_shadow"] = PaperMainnetTrader._build_liquidity_grab_exit_plan(
        pos, liquidity_grab_bars(), equity=1000.0)
    pos["smart_exit_router_shadow"] = PaperMainnetTrader._build_smart_exit_router_plan(
        pos, liquidity_grab_bars(), equity=1000.0)
    latest = liquidity_grab_bars()
    latest[-2]["open_time"] = "2026-08-28T06:00:00+00:00"
    PaperMainnetTrader._update_smart_exit_shadows(pos, latest)
    assert len(pos["smart_exit_shadow"]["observations"]) == 1
    assert len(pos["smart_exit_liquidity_shadow"]["observations"]) == 1
    assert len(pos["smart_exit_router_shadow"]["observations"]) == 1
    assert {key: pos[key] for key in original} == original


def test_router_prefers_confirmed_liquidity_grab_and_freezes_entry_features():
    pos = position()
    pos["adv_snapshot"] = {"regime": "TRENDING", "mtf_alignment": "BULLISH", "rsi": 55}
    plan = PaperMainnetTrader._build_smart_exit_router_plan(
        pos, liquidity_grab_bars(), equity=1000.0,
        risk_pct=0.01, opened_at="2026-08-28T05:10:00+00:00")
    assert plan["candidate_available"] is True
    assert plan["setup_family"] == "LIQUIDITY_GRAB"
    assert plan["selected_plan"] == "smart_exit_liquidity_grab_v1"
    assert plan["feature_snapshot"] == pos["adv_snapshot"]
    assert plan["scores"]["LIQUIDITY_GRAB"] > plan["scores"]["WIDE_TREND"]


def test_router_selects_wide_trend_without_liquidity_grab():
    pos = position()
    pos["adv_snapshot"] = {
        "regime": "TRENDING", "mtf_alignment": "BULLISH",
        "cvd_trend": "BULLISH", "rsi": 55,
    }
    plan = PaperMainnetTrader._build_smart_exit_router_plan(
        pos, bars(), equity=1000.0, risk_pct=0.01)
    assert plan["candidate_available"] is True
    assert plan["setup_family"] == "WIDE_TREND"
    assert plan["trail_atr_mult"] == 2.0
    assert plan["tp1_r"] == 1.5


def test_router_fails_closed_when_entry_evidence_is_ambiguous():
    pos = position(); pos["adv_snapshot"] = {"regime": "UNKNOWN"}
    plan = PaperMainnetTrader._build_smart_exit_router_plan(
        pos, bars(), equity=1000.0, risk_pct=0.01)
    assert plan["candidate_available"] is False
    assert plan["reason"] == "INSUFFICIENT_ROUTER_EVIDENCE"


def test_smart_shadow_finalizes_net_pnl_with_fees_and_slippage():
    pos = position()
    plan = PaperMainnetTrader._build_smart_exit_plan(
        pos, bars(), equity=1000.0, risk_pct=0.01)
    pos["smart_exit_shadow"] = plan
    PaperMainnetTrader._advance_smart_exit_plan(plan, {
        "open": 104, "high": 104.1, "low": plan["sl_price"] - 1, "close": 100,
        "closed_at": "2026-08-28T06:00:00+00:00"})
    result = PaperMainnetTrader._finalize_smart_exit_shadow(pos, provider_exit=110.0)
    assert result["triggered"] is True
    assert result["exit_fee_usd"] > 0
    assert result["entry_fee_usd"] > 0
    assert result["net_pnl_usd"] < result["gross_pnl_usd"]
    assert result["qty"] == plan["qty"]
