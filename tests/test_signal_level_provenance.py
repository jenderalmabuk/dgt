from signal_copy.normalizer import normalize_signal
from signal_copy.signal_schema import ParsedSignal, SignalSide
from signal_copy.validation_engine import Verdict, validate_signal


def test_malformed_provider_levels_are_explicit_and_rejected():
    sig = ParsedSignal("SANDUSDT", SignalSide.LONG, 0.3299, 0.3299,
                       take_profits=[2.0], stop_loss=None)
    normalize_signal(sig, {"price": 0.0417, "atr_pct": 1.0})
    assert sig.sl_source == "improvised_atr"
    assert sig.tp_source == "improvised_R"
    assert "PROVIDER_TP_OUTLIER:2" in sig.level_anomalies
    assert "MALFORMED_PROVIDER_LEVELS" in sig.level_anomalies
    result = validate_signal(sig, {"price": 0.0417, "data_valid": True})
    assert result.verdict == Verdict.REJECT
    assert any("MALFORMED_PROVIDER_LEVELS" in reason for reason in result.hard_blocks)


def test_single_provider_tp_outlier_warns_without_rewriting_valid_ladder():
    sig = ParsedSignal("HEMIUSDT", SignalSide.LONG, 0.00545, 0.00552,
                       stop_loss=0.00532,
                       take_profits=[0.005825, 0.00635, 0.007, 0.0563])
    normalize_signal(sig, {"price": 0.0055})
    assert sig.take_profits == [0.005825, 0.00635, 0.007]
    assert "PROVIDER_TP_OUTLIER:0.0563" in sig.level_anomalies
    assert "MALFORMED_PROVIDER_LEVELS" not in sig.level_anomalies
