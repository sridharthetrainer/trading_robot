import numpy as np
import pandas as pd

from indicator_confluence import calculate_indicator_confluence


def _base_df(direction: str = "BUY") -> pd.DataFrame:
    rows = 40
    if direction == "BUY":
        close = np.linspace(100, 108, rows)
        ema_fast = close - 0.4
        ema_slow = close - 1.0
        ema_trend = close - 2.0
        vwap = close - 0.8
        rsi = np.full(rows, 61.0)
        macd_hist = np.full(rows, 0.4)
        plus_di = np.full(rows, 30.0)
        minus_di = np.full(rows, 14.0)
        supertrend_dir = np.ones(rows)
    else:
        close = np.linspace(108, 100, rows)
        ema_fast = close + 0.4
        ema_slow = close + 1.0
        ema_trend = close + 2.0
        vwap = close + 0.8
        rsi = np.full(rows, 39.0)
        macd_hist = np.full(rows, -0.4)
        plus_di = np.full(rows, 14.0)
        minus_di = np.full(rows, 30.0)
        supertrend_dir = -np.ones(rows)

    return pd.DataFrame({
        "open": close,
        "high": close + 0.6,
        "low": close - 0.6,
        "close": close,
        "volume": np.linspace(1000, 1800, rows),
        "ema_fast": ema_fast,
        "ema_slow": ema_slow,
        "ema_trend": ema_trend,
        "vwap": vwap,
        "rsi": rsi,
        "macd_hist": macd_hist,
        "adx": np.full(rows, 29.0),
        "plus_di": plus_di,
        "minus_di": minus_di,
        "supertrend_dir": supertrend_dir,
        "volume_ratio": np.full(rows, 1.45),
        "efficiency_ratio": np.full(rows, 0.52),
        "choppiness_index": np.full(rows, 34.0),
    })


def test_bullish_confluence_scores_above_neutral():
    result = calculate_indicator_confluence(
        _base_df("BUY"),
        direction="BUY",
        strategy="chart_pattern",
        signal_meta={
            "pattern": "ascending_triangle",
            "all_patterns": {
                "ascending_triangle": {"pattern": "ascending_triangle", "direction": "BUY"},
            },
            "breakout_confirmed": True,
            "volume_confirmation": True,
            "risk_reward": 2.0,
            "oi_direction": "BUY",
        },
        option_data={"pcr": 1.1},
    )
    assert result["score"] > 6.0
    assert result["score_modifier"] > 0
    assert result["group_scores"]["trend"] > 0
    assert result["group_scores"]["structure"] > 0


def test_bearish_confluence_scores_above_neutral():
    result = calculate_indicator_confluence(
        _base_df("SELL"),
        direction="SELL",
        strategy="chart_pattern",
        signal_meta={
            "pattern": "descending_triangle",
            "all_patterns": {
                "descending_triangle": {"pattern": "descending_triangle", "direction": "SELL"},
            },
            "breakout_confirmed": True,
            "volume_confirmation": True,
            "risk_reward": 2.0,
            "oi_direction": "SELL",
        },
        option_data={"pcr": 0.9},
    )
    assert result["score"] > 6.0
    assert result["score_modifier"] > 0
    assert result["group_scores"]["trend"] > 0
    assert result["group_scores"]["structure"] > 0


def test_breakout_volume_iv_vix_improve_confluence():
    result = calculate_indicator_confluence(
        _base_df("BUY"),
        direction="BUY",
        strategy="breakout",
        signal_meta={
            "pattern": "ascending_triangle",
            "all_patterns": {
                "ascending_triangle": {"pattern": "ascending_triangle", "direction": "BUY"},
            },
            "pattern_breakout_confirmed": True,
            "level_breakout_confirmed": True,
            "breakout_retest": True,
            "volume_confirmation": True,
            "risk_reward": 2.1,
            "oi_direction": "BUY",
        },
        option_data={"pcr": 1.12, "iv": 18.0, "iv_percentile": 48.0, "vix": 15.5},
    )
    assert result["group_scores"]["breakout"] > 1.0
    assert result["group_scores"]["volume"] > 0.5
    assert result["group_scores"]["volatility"] > 0
    assert "vix_tradeable" in result["breakdown"]["volatility"]["reasons"]
    assert "iv_reasonable" in result["breakdown"]["options_oi"]["reasons"]


def test_failed_breakout_rejects_wrong_side_confluence():
    df = _base_df("SELL")
    df["volume_ratio"] = 0.65
    result = calculate_indicator_confluence(
        df,
        direction="BUY",
        strategy="breakout",
        signal_meta={
            "pattern": "failed_breakout",
            "failed_breakout": True,
            "breakout_rejection": "resistance",
            "volume_ratio": 0.65,
            "risk_reward": 1.2,
        },
        option_data={"iv": 38.0, "iv_percentile": 90.0, "vix": 26.0},
    )
    assert result["group_scores"]["breakout"] < 0
    assert result["group_scores"]["volume"] < 0
    assert result["group_scores"]["volatility"] < 0
    assert result["score_modifier"] < 0


if __name__ == "__main__":
    test_bullish_confluence_scores_above_neutral()
    test_bearish_confluence_scores_above_neutral()
    test_breakout_volume_iv_vix_improve_confluence()
    test_failed_breakout_rejects_wrong_side_confluence()
    print("indicator_confluence tests passed")
