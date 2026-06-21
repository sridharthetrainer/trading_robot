import numpy as np
import pandas as pd

from market_context_builder import build_market_context
from mtf_context import build_mtf_context
from signal_engine import _passes_volume_gate


def _df(volume_ratio=1.3):
    rows = 50
    close = np.linspace(100, 106, rows)
    return pd.DataFrame({
        "open": close - 0.1,
        "high": close + 0.5,
        "low": close - 0.5,
        "close": close,
        "volume": np.linspace(1000, 1800, rows),
        "volume_ratio": np.full(rows, volume_ratio),
        "ema_fast": close - 0.2,
        "ema_slow": close - 0.7,
        "ema_trend": close - 1.2,
        "vwap": close - 0.3,
        "rsi": np.full(rows, 60.0),
        "macd_hist": np.full(rows, 0.3),
        "adx": np.full(rows, 28.0),
        "plus_di": np.full(rows, 31.0),
        "minus_di": np.full(rows, 12.0),
        "supertrend_dir": np.ones(rows),
    })


def test_market_context_builder_normalizes_core_inputs():
    ctx = build_market_context(
        symbol="NIFTY",
        df=_df(),
        intel={
            "vix": 15.7,
            "cross_asset_bias": "BULLISH",
            "news_score": 0.2,
            "expiry_dte": 2,
            "expiry_regime": "EXPIRY_WEEK",
            "whale_index": {"NIFTY": 0.3},
        },
        global_bias={"bias": "BULLISH", "change_pct": 0.006, "source": "test"},
        iv_percentile=45,
    )
    assert ctx["vix"] == 15.7
    assert ctx["iv_percentile"] == 45
    assert ctx["global_bias"] == "BULLISH"
    assert ctx["volume_data_quality"] == "ok"
    assert ctx["data_confidence"] >= 0.8


def test_missing_volume_blocks_stocks_but_allows_indices():
    df = _df(volume_ratio=0.0)
    cfg = {"min_volume_ratio_entry": 0.40}
    assert _passes_volume_gate(df, cfg, symbol="NIFTY")
    assert not _passes_volume_gate(df, cfg, symbol="RELIANCE")


def test_generate_signal_preserves_decision_inputs_context():
    import signal_engine

    def context_probe_strategy(df, df_htf, option_data):
        return {
            "strategy": "context_probe",
            "score": 4.0,
            "direction": "BUY",
            "pattern": "ascending_triangle",
            "all_patterns": {
                "ascending_triangle": {"pattern": "ascending_triangle", "direction": "BUY"},
            },
            "breakout_confirmed": True,
            "volume_confirmation": True,
            "risk_reward": 2.0,
        }

    old = signal_engine.STRATEGIES
    signal_engine.STRATEGIES = [context_probe_strategy]
    try:
        out = signal_engine.generate_signal(
            _df(),
            _df(),
            "NIFTY",
            option_data={
                "vix": 16.2,
                "iv": 18.0,
                "iv_percentile": 50.0,
                "pcr": 1.12,
                "global_bias": "BULLISH",
                "global_change_pct": 0.007,
                "news_score": 0.2,
                "volume_data_quality": "ok",
                "data_confidence": 1.0,
                "mtf_context": build_mtf_context({"primary": _df(), "htf": _df()}),
            },
            config={
                "paper_training_mode": True,
                "require_htf_alignment": False,
                "vote_threshold": 0.1,
                "post_confluence_min_score": 1.0,
                "enable_entry_quality_filter": False,
            },
        )
    finally:
        signal_engine.STRATEGIES = old

    decision_inputs = out.get("signal_meta", {}).get("decision_inputs", {})
    assert decision_inputs["vix"] == 16.2
    assert decision_inputs["pcr"] == 1.12
    assert decision_inputs["global_bias"] == "BULLISH"
    assert decision_inputs["indicator_confluence_score"] is not None
    assert decision_inputs["mtf_indicator_bias"] == "BUY"
    assert decision_inputs["mtf_indicator_mod"] is not None
    assert decision_inputs["mtf_indicator_context"]["bias"] == "BUY"


if __name__ == "__main__":
    test_market_context_builder_normalizes_core_inputs()
    test_missing_volume_blocks_stocks_but_allows_indices()
    test_generate_signal_preserves_decision_inputs_context()
    print("market context integration tests passed")
