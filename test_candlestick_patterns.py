import pandas as pd

from candlestick_patterns import DETECTORS, detect_candlestick_patterns, latest_pattern_summary
from candlestick_signals import candlestick_signal


def _df(rows):
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"])


def test_detector_registry_has_50_plus_patterns():
    assert len(DETECTORS) >= 50


def test_bullish_engulfing_detected():
    df = _df([
        [100, 101, 94, 95, 1000],
        [94, 103, 93, 102, 1800],
    ])
    names = {p.pattern_name for p in detect_candlestick_patterns(df, lookback=1)}
    assert "Bullish Engulfing" in names


def test_morning_star_detected_in_latest_summary():
    df = _df([
        [110, 111, 99, 100, 1000],
        [98, 99, 96, 98.2, 900],
        [99, 108, 98, 107, 1600],
    ])
    summary = latest_pattern_summary(df)
    names = {p["type"] for p in summary["patterns"]}
    assert "Morning Star" in names
    assert summary["signals"]["bullish_count"] >= 1


def test_candlestick_strategy_exports_all_patterns_metadata():
    df = _df([
        [100, 101, 94, 95, 1000],
        [94, 103, 93, 102, 1800],
        [102, 104, 101, 103, 1200],
        [103, 105, 102, 104, 1250],
        [104, 106, 103, 105, 1300],
    ])
    result = candlestick_signal(df, pivot_levels={"S1": 102})
    assert "patterns" in result
    assert "all_patterns" in result
    assert isinstance(result["all_patterns"], dict)


if __name__ == "__main__":
    test_detector_registry_has_50_plus_patterns()
    test_bullish_engulfing_detected()
    test_morning_star_detected_in_latest_summary()
    test_candlestick_strategy_exports_all_patterns_metadata()
    print("candlestick pattern tests passed")
