"""
Test dynamic exit layers (ported from FusionXomegabot).

Each layer has a focused test proving it fires under exact conditions and honors
the safety invariant (SL only tightens, never loosens). Real money hard-disabled.
"""
import pytest
import os
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch
from execution.paper_mainnet_trader import PaperMainnetTrader

UTC = timezone.utc


@pytest.fixture
def trader():
    """Paper trader with minimal init (no journal/risk)."""
    t = PaperMainnetTrader(journal=None, risk_mgr=None)
    t.positions = {}
    t._running = True
    t.poll_interval = 1
    return t


@pytest.fixture
def pos_base():
    """Minimal position dict matching paper trader schema."""
    return {
        "status": "OPEN",
        "qty": 1.0,
        "entry_price": 100.0,
        "sl_price": 95.0,
        "side": "LONG",
        "opened_at": datetime.now(UTC) - timedelta(minutes=60),
        "provider_sl_original": 95.0,
        "regime": "RANGING",
    }


class TestDynamicExitMasterGate:
    """Master gate: paper-only, env-gated."""

    @patch.dict(os.environ, {"REAL_MONEY": "true", "SIGNALCOPY_PAPER_DYNAMIC_EXIT": "true"})
    async def test_real_money_hard_disabled(self, trader, pos_base):
        """Real money = hard block, even if env flag is true."""
        pos = pos_base.copy()
        trader.positions["BTCUSDT"] = pos
        closed = await trader._apply_dynamic_exits("BTCUSDT", pos, 98.0)
        assert closed is False, "Real money must block dynamic exit"

    @patch.dict(os.environ, {"REAL_MONEY": "false", "SIGNALCOPY_PAPER_DYNAMIC_EXIT": "false"})
    async def test_env_gate_off(self, trader, pos_base):
        """Paper but env gate off = no-op."""
        pos = pos_base.copy()
        pos["opened_at"] = datetime.now(UTC) - timedelta(minutes=50)
        trader.positions["BTCUSDT"] = pos
        closed = await trader._apply_dynamic_exits("BTCUSDT", pos, 100.1)  # scratch cond met
        assert closed is False, "Env gate off = no dynamic exit"

    @patch.dict(os.environ, {"REAL_MONEY": "false", "SIGNALCOPY_PAPER_DYNAMIC_EXIT": "true"})
    async def test_env_gate_on(self, trader, pos_base):
        """Paper + env on = layers active."""
        pos = pos_base.copy()
        pos["opened_at"] = datetime.now(UTC) - timedelta(minutes=50)
        trader._close = AsyncMock()
        trader.positions["BTCUSDT"] = pos
        closed = await trader._apply_dynamic_exits("BTCUSDT", pos, 100.05)  # scratch
        assert closed is True, "Env gate on = scratch fires"
        trader._close.assert_called_once()


class TestScratchExit:
    """SCRATCH_EXIT: zombie trade near breakeven after regime wait."""

    @patch.dict(os.environ, {"REAL_MONEY": "false", "SIGNALCOPY_PAPER_DYNAMIC_EXIT": "true",
                             "SCRATCH_EXIT_ENABLED": "true", "SCRATCH_EXIT_RANGING_MINUTES": "30",
                             "SCRATCH_EXIT_MAX_ABS_PNL_PCT": "0.4"})
    async def test_scratch_ranging_fires(self, trader, pos_base):
        """Ranging 30min + |PnL| <0.4% → scratch."""
        pos = pos_base.copy()
        pos["regime"] = "RANGING"
        pos["opened_at"] = datetime.now(UTC) - timedelta(minutes=35)
        trader._close = AsyncMock()
        closed = await trader._apply_dynamic_exits("BTCUSDT", pos, 100.2)  # +0.2%
        assert closed is True
        trader._close.assert_called_with("BTCUSDT", 100.2, "SCRATCH_EXIT")

    @patch.dict(os.environ, {"REAL_MONEY": "false", "SIGNALCOPY_PAPER_DYNAMIC_EXIT": "true",
                             "SCRATCH_EXIT_ENABLED": "true", "SCRATCH_EXIT_TRENDING_MINUTES": "45"})
    async def test_scratch_trending_delay(self, trader, pos_base):
        """Trending requires 45min (not 30)."""
        pos = pos_base.copy()
        pos["regime"] = "TRENDING"
        pos["opened_at"] = datetime.now(UTC) - timedelta(minutes=35)
        trader._close = AsyncMock()
        closed = await trader._apply_dynamic_exits("BTCUSDT", pos, 100.2)
        assert closed is False, "35min < 45min trending threshold"

    @patch.dict(os.environ, {"REAL_MONEY": "false", "SIGNALCOPY_PAPER_DYNAMIC_EXIT": "true",
                             "SCRATCH_EXIT_ENABLED": "true", "SCRATCH_EXIT_RANGING_MINUTES": "30"})
    async def test_scratch_post_tp1_blocked(self, trader, pos_base):
        """Post-TP1 = blocked (old bot pre-TP1 only logic)."""
        pos = pos_base.copy()
        pos["tp1_hit"] = True
        pos["opened_at"] = datetime.now(UTC) - timedelta(minutes=50)
        trader._close = AsyncMock()
        closed = await trader._apply_dynamic_exits("BTCUSDT", pos, 100.1)
        assert closed is False, "Scratch blocked post-TP1"

    @patch.dict(os.environ, {"REAL_MONEY": "false", "SIGNALCOPY_PAPER_DYNAMIC_EXIT": "true",
                             "SCRATCH_EXIT_ENABLED": "true", "SCRATCH_EXIT_MAX_ABS_PNL_PCT": "0.4"})
    async def test_scratch_pnl_too_large(self, trader, pos_base):
        """PnL |±1%| > 0.4% threshold = no scratch."""
        pos = pos_base.copy()
        pos["opened_at"] = datetime.now(UTC) - timedelta(minutes=50)
        trader._close = AsyncMock()
        closed = await trader._apply_dynamic_exits("BTCUSDT", pos, 101.5)  # +1.5%
        assert closed is False, "|+1.5%| > 0.4% = no scratch"


class TestDamageReducer:
    """DAMAGE_REDUCER: cut losses before full SL (still above SL)."""

    @patch.dict(os.environ, {"REAL_MONEY": "false", "SIGNALCOPY_PAPER_DYNAMIC_EXIT": "true",
                             "DAMAGE_REDUCER_ENABLED": "true", "DAMAGE_REDUCER_MIN_HOLD_MINUTES": "45",
                             "DAMAGE_REDUCER_MAX_LOSS_PCT": "-2.5"})
    async def test_damage_reducer_fires(self, trader, pos_base):
        """45min + -2.6% (below -2.5) + above SL → damage reducer."""
        pos = pos_base.copy()
        pos["opened_at"] = datetime.now(UTC) - timedelta(minutes=50)
        trader._close = AsyncMock()
        closed = await trader._apply_dynamic_exits("BTCUSDT", pos, 97.4)  # -2.6%
        assert closed is True
        trader._close.assert_called_with("BTCUSDT", 97.4, "DAMAGE_REDUCER")

    @patch.dict(os.environ, {"REAL_MONEY": "false", "SIGNALCOPY_PAPER_DYNAMIC_EXIT": "true",
                             "DAMAGE_REDUCER_ENABLED": "true"})
    async def test_damage_reducer_at_sl_blocked(self, trader, pos_base):
        """At or below SL = blocked (canonical HARD_SL path owns it)."""
        pos = pos_base.copy()
        pos["opened_at"] = datetime.now(UTC) - timedelta(minutes=50)
        trader._close = AsyncMock()
        closed = await trader._apply_dynamic_exits("BTCUSDT", pos, 94.0)  # below SL 95
        assert closed is False, "Below SL = canonical path, not damage reducer"


class TestProfitLock:
    """PROFIT_LOCK: move SL to BE+buffer once green."""

    @patch.dict(os.environ, {"REAL_MONEY": "false", "SIGNALCOPY_PAPER_DYNAMIC_EXIT": "true",
                             "PROFIT_LOCK_MIN_MINUTES": "15", "PROFIT_LOCK_TRIGGER_PCT": "1.0",
                             "PROFIT_LOCK_BUFFER_PCT": "0.3"})
    async def test_profit_lock_long(self, trader, pos_base):
        """LONG: 20min + +1.2% → SL moves to BE+buffer."""
        pos = pos_base.copy()
        pos["opened_at"] = datetime.now(UTC) - timedelta(minutes=20)
        trader.positions["BTCUSDT"] = pos
        closed = await trader._apply_dynamic_exits("BTCUSDT", pos, 101.2)  # +1.2%
        assert closed is False, "Profit lock is SL mutation, not close"
        assert pos["locked_profit"] is True
        assert pos["sl_kind"] == "BREAKEVEN"
        # LONG: new_sl = max(95, 100 + max(100*0.003, 5*0.15))
        #       = max(95, 100 + max(0.3, 0.75)) = max(95, 100.75) = 100.75
        assert pos["sl_price"] > 95.0, "SL tightened"
        assert pos["sl_price"] < 101.2, "SL below mark (not instant trigger)"

    @patch.dict(os.environ, {"REAL_MONEY": "false", "SIGNALCOPY_PAPER_DYNAMIC_EXIT": "true",
                             "PROFIT_LOCK_MIN_MINUTES": "15", "PROFIT_LOCK_TRIGGER_PCT": "1.0"})
    async def test_profit_lock_short(self, trader, pos_base):
        """SHORT: same logic, inverted."""
        pos = pos_base.copy()
        pos["side"] = "SHORT"
        pos["entry_price"] = 100.0
        pos["sl_price"] = 105.0
        pos["provider_sl_original"] = 105.0
        pos["opened_at"] = datetime.now(UTC) - timedelta(minutes=20)
        trader.positions["BTCUSDT"] = pos
        closed = await trader._apply_dynamic_exits("BTCUSDT", pos, 98.8)  # +1.2% for SHORT
        assert closed is False
        assert pos["locked_profit"] is True
        assert pos["sl_kind"] == "BREAKEVEN"
        assert pos["sl_price"] < 105.0, "SHORT SL tightened"
        assert pos["sl_price"] > 98.8, "SL above mark"

    @patch.dict(os.environ, {"REAL_MONEY": "false", "SIGNALCOPY_PAPER_DYNAMIC_EXIT": "true",
                             "PROFIT_LOCK_MIN_MINUTES": "15"})
    async def test_profit_lock_once_only(self, trader, pos_base):
        """locked_profit=True → no second lock."""
        pos = pos_base.copy()
        pos["locked_profit"] = True
        pos["opened_at"] = datetime.now(UTC) - timedelta(minutes=20)
        old_sl = pos["sl_price"]
        trader.positions["BTCUSDT"] = pos
        await trader._apply_dynamic_exits("BTCUSDT", pos, 102.0)
        assert pos["sl_price"] == old_sl, "No second profit lock"


class TestTrailing:
    """TRAILING: only after profit lock + 10min (two-phase)."""

    @patch.dict(os.environ, {"REAL_MONEY": "false", "SIGNALCOPY_PAPER_DYNAMIC_EXIT": "true",
                             "TRAIL_SL_ENABLED": "true", "TRAIL_SL_PCT": "0.8"})
    async def test_trailing_long(self, trader, pos_base):
        """LONG: mark rises → watermark rises → SL trails."""
        pos = pos_base.copy()
        pos["locked_profit"] = True
        pos["sl_price"] = 100.5  # already at BE
        pos["opened_at"] = datetime.now(UTC) - timedelta(minutes=15)
        trader.positions["BTCUSDT"] = pos
        # First tick: mark 102
        await trader._apply_dynamic_exits("BTCUSDT", pos, 102.0)
        sl1 = pos["sl_price"]
        assert sl1 > 100.5, "SL trails up"
        assert pos["sl_kind"] == "TRAILING"
        # Second tick: mark 103 → SL rises again
        await trader._apply_dynamic_exits("BTCUSDT", pos, 103.0)
        assert pos["sl_price"] > sl1, "SL continues trailing"

    @patch.dict(os.environ, {"REAL_MONEY": "false", "SIGNALCOPY_PAPER_DYNAMIC_EXIT": "true",
                             "TRAIL_SL_ENABLED": "true"})
    async def test_trailing_requires_profit_lock(self, trader, pos_base):
        """locked_profit=False → no trailing."""
        pos = pos_base.copy()
        pos["opened_at"] = datetime.now(UTC) - timedelta(minutes=15)
        old_sl = pos["sl_price"]
        trader.positions["BTCUSDT"] = pos
        await trader._apply_dynamic_exits("BTCUSDT", pos, 102.0)
        assert pos.get("sl_kind") != "TRAILING", "No trailing without profit lock"


class TestTimeExit:
    """TIME_EXIT: regime max hold + one +30min extension if green."""

    @patch.dict(os.environ, {"REAL_MONEY": "false", "SIGNALCOPY_PAPER_DYNAMIC_EXIT": "true",
                             "MAX_HOLD_RANGING": "240"})
    async def test_time_exit_ranging(self, trader, pos_base):
        """Ranging 240min + red → TIME_EXIT."""
        pos = pos_base.copy()
        pos["regime"] = "RANGING"
        pos["opened_at"] = datetime.now(UTC) - timedelta(minutes=245)
        trader._close = AsyncMock()
        closed = await trader._apply_dynamic_exits("BTCUSDT", pos, 99.0)  # -1%
        assert closed is True
        trader._close.assert_called_with("BTCUSDT", 99.0, "TIME_EXIT")

    @patch.dict(os.environ, {"REAL_MONEY": "false", "SIGNALCOPY_PAPER_DYNAMIC_EXIT": "true",
                             "MAX_HOLD_RANGING": "240"})
    async def test_time_exit_extension(self, trader, pos_base):
        """240min + green → +30min extension once."""
        pos = pos_base.copy()
        pos["regime"] = "RANGING"
        pos["opened_at"] = datetime.now(UTC) - timedelta(minutes=245)
        trader._close = AsyncMock()
        trader.positions["BTCUSDT"] = pos
        # First tick green: extension
        closed = await trader._apply_dynamic_exits("BTCUSDT", pos, 101.0)
        assert closed is False, "Green = extension"
        assert pos["dyn_time_extended"] is True
        assert pos["dyn_max_hold_minutes"] == 270
        # Second tick at 275min: still green but already extended → exit
        pos["opened_at"] = datetime.now(UTC) - timedelta(minutes=275)
        closed = await trader._apply_dynamic_exits("BTCUSDT", pos, 101.0)
        assert closed is True
        trader._close.assert_called_with("BTCUSDT", 101.0, "TIME_EXIT_EXTENDED")


class TestSafetyInvariant:
    """SL only ever tightens (max for LONG, min for SHORT)."""

    @patch.dict(os.environ, {"REAL_MONEY": "false", "SIGNALCOPY_PAPER_DYNAMIC_EXIT": "true",
                             "PROFIT_LOCK_MIN_MINUTES": "15", "PROFIT_LOCK_TRIGGER_PCT": "1.0",
                             "TRAIL_SL_ENABLED": "true"})
    async def test_sl_only_tightens_long(self, trader, pos_base):
        """LONG: every dynamic layer uses max(cur_sl, candidate) → monotonic."""
        pos = pos_base.copy()
        pos["opened_at"] = datetime.now(UTC) - timedelta(minutes=20)
        trader.positions["BTCUSDT"] = pos
        sl_history = [pos["sl_price"]]
        # Profit lock
        await trader._apply_dynamic_exits("BTCUSDT", pos, 102.0)
        sl_history.append(pos["sl_price"])
        # Trail
        await trader._apply_dynamic_exits("BTCUSDT", pos, 104.0)
        sl_history.append(pos["sl_price"])
        await trader._apply_dynamic_exits("BTCUSDT", pos, 105.0)
        sl_history.append(pos["sl_price"])
        # Monotonic: each >= previous (LONG tightens = rises)
        for i in range(1, len(sl_history)):
            assert sl_history[i] >= sl_history[i - 1], f"SL loosened: {sl_history}"

    @patch.dict(os.environ, {"REAL_MONEY": "false", "SIGNALCOPY_PAPER_DYNAMIC_EXIT": "true",
                             "PROFIT_LOCK_MIN_MINUTES": "15", "TRAIL_SL_ENABLED": "true"})
    async def test_sl_only_tightens_short(self, trader, pos_base):
        """SHORT: min(cur_sl, candidate) → monotonic down."""
        pos = pos_base.copy()
        pos["side"] = "SHORT"
        pos["entry_price"] = 100.0
        pos["sl_price"] = 105.0
        pos["provider_sl_original"] = 105.0
        pos["opened_at"] = datetime.now(UTC) - timedelta(minutes=20)
        trader.positions["BTCUSDT"] = pos
        sl_history = [pos["sl_price"]]
        # Profit lock
        await trader._apply_dynamic_exits("BTCUSDT", pos, 98.0)
        sl_history.append(pos["sl_price"])
        # Trail
        await trader._apply_dynamic_exits("BTCUSDT", pos, 96.0)
        sl_history.append(pos["sl_price"])
        # Monotonic: each <= previous (SHORT tightens = falls)
        for i in range(1, len(sl_history)):
            assert sl_history[i] <= sl_history[i - 1], f"SHORT SL loosened: {sl_history}"
