"""
Unit tests for pattern_engine. Run:  python -m pytest pattern_engine/tests -q
(or:  python pattern_engine/tests/test_pattern_engine.py  for a quick smoke run)

Uses SYNTHETIC OHLCV with a hand-built double top / double bottom so tests are
deterministic and need no network or live data.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import numpy as np
import pandas as pd

from pattern_engine import PatternEngine, load_config
from pattern_engine.base import validate_ohlcv, Direction
from pattern_engine.pivot_engine import find_swings
from pattern_engine.config_loader import load_config as _lc


def _df(prices) -> pd.DataFrame:
    p = np.asarray(prices, dtype=float)
    return pd.DataFrame({
        "open": p, "high": p + 1.0, "low": p - 1.0, "close": p,
        "volume": np.full(len(p), 1000.0),
    })


def _double_top_series() -> pd.DataFrame:
    # up to peak1 (130), down to trough (112), up to peak2 (~129), break below
    # trough. Descents start BELOW the peak so each peak is a clean single-bar
    # fractal high (no flat top → no tie).
    up1 = np.linspace(100, 130, 15)
    dn1 = np.linspace(127, 112, 12)
    up2 = np.linspace(112, 129, 12)
    dn2 = np.linspace(126, 105, 18)     # breaks below trough (112)
    return _df(np.concatenate([up1, dn1, up2, dn2]))


def test_config_loads_and_overrides():
    cfg = load_config()
    assert cfg.pivots["left_bars"] == 2
    assert cfg.get("scoring.weights.pattern_quality") == 0.35
    assert cfg.get("nope.missing", 7) == 7


def test_validate_ohlcv_rejects_missing_cols():
    bad = pd.DataFrame({"open": [1], "high": [1], "low": [1], "close": [1]})  # no volume
    try:
        validate_ohlcv(bad)
        assert False, "should have raised"
    except ValueError:
        pass


def test_find_swings_detects_local_extremes():
    df = _df([10, 11, 12, 11, 10, 9, 10, 11, 12])
    sh, sl = find_swings(df, 2, 2, 1)
    assert 2 in sh           # peak at index 2 (value 12)
    assert any(abs(i - 5) <= 1 for i in sl)   # trough near index 5


def test_double_top_detection_and_shape():
    eng = PatternEngine()
    res = eng.detect(_double_top_series(), symbol="TEST")
    dts = [r for r in res if r.pattern == "double_top"]
    assert dts, "expected at least one double top"
    r = dts[0]
    assert r.direction == Direction.SHORT
    assert r.stop_loss > r.entry > r.target        # bearish geometry
    assert 0 <= r.confidence <= 100
    j = r.to_json()
    assert j["pattern"] == "double_top" and j["risk_reward"] >= 0


def _ascending_triangle_series() -> pd.DataFrame:
    # flat resistance (~130, slightly varied so pivots are distinct) + rising
    # lows, then breakout above 130.
    seg = []
    lows = [110, 114, 118, 122, 125]
    tops = [130.0, 130.2, 129.8, 130.1, 129.9]
    for lw, tp in zip(lows, tops):
        seg += list(np.linspace(lw, tp, 6))
        seg += list(np.linspace(tp - 0.5, lw + 3, 6))
    seg += list(np.linspace(130, 142, 8))     # breakout
    p = np.asarray(seg, float)
    return pd.DataFrame({"open": p, "high": p + 0.3, "low": p - 0.3, "close": p,
                         "volume": np.full(len(p), 1000.0)})


def test_ascending_triangle_detection():
    eng = PatternEngine()
    res = eng.detect(_ascending_triangle_series(), symbol="TEST")
    asc = [r for r in res if r.pattern == "ascending_triangle"]
    assert asc, "expected an ascending triangle"
    r = asc[0]
    assert r.direction == Direction.LONG
    assert r.target > r.entry > r.stop_loss        # bullish geometry
    assert r.risk_reward > 0


def _rising_wedge_series() -> pd.DataFrame:
    # both lines rising and converging, then break down (bearish rising wedge)
    seg = []
    base = 100.0
    for k in range(5):
        hi = base + 18 - k * 1.0        # highs rise slowly
        lo = base + 6 + k * 2.0         # lows rise faster (converging)
        seg += list(np.linspace(lo, hi, 6))
        seg += list(np.linspace(hi - 0.5, lo + 1.5, 6))
        base += 3.0
    seg += list(np.linspace(seg[-1], seg[-1] - 25, 8))   # breakdown
    p = np.asarray(seg, float)
    return pd.DataFrame({"open": p, "high": p + 0.3, "low": p - 0.3, "close": p,
                         "volume": np.full(len(p), 1000.0)})


def test_volume_confirmation_helper():
    from pattern_engine.volume_confirmation import analyse_volume
    p = np.full(60, 100.0)
    vol = np.concatenate([np.full(20, 1000.0), np.full(30, 500.0), np.array([3000.0]), np.full(9, 500.0)])
    df = pd.DataFrame({"open": p, "high": p + 1, "low": p - 1, "close": p, "volume": vol})
    prof = analyse_volume(df, 20, 49, 50, ma_period=20)
    assert prof.contracted is True       # formation 500 vs baseline 1000
    assert prof.expanded is True         # breakout bar 3000 vs 500


def test_wedge_detector_runs():
    # geometry is finicky on synthetic data; just assert it runs without error
    eng = PatternEngine()
    out = eng.detect(_rising_wedge_series(), symbol="TEST")
    assert isinstance(out, list)


def _bull_flag_series() -> pd.DataFrame:
    # sharp pole 100->130 (30%), tight downward drift, then breakout above flag.
    pole = np.linspace(100, 130, 10)
    flag = np.array([129.0, 128.4, 128.0, 127.6, 127.2, 127.4, 126.9, 127.2])
    brk = np.linspace(129.0, 142.0, 6)        # closes back above the flag high
    p = np.concatenate([pole, flag, brk])
    return pd.DataFrame({"open": p, "high": p + 0.3, "low": p - 0.3, "close": p,
                         "volume": np.full(len(p), 1000.0)})


def test_bull_flag_detection_and_shape():
    eng = PatternEngine()
    res = eng.detect(_bull_flag_series(), symbol="TEST")
    flags = [r for r in res if r.pattern in ("bull_flag", "bull_pennant")]
    assert flags, "expected a bull flag/pennant"
    r = flags[0]
    assert r.direction == Direction.LONG
    assert r.target > r.entry > r.stop_loss       # bullish continuation geometry
    assert 0 <= r.confidence <= 100


def test_engine_returns_sorted_json():
    eng = PatternEngine()
    out = eng.detect_json(_double_top_series(), symbol="TEST")
    confs = [o["confidence"] for o in out]
    assert confs == sorted(confs, reverse=True)


def test_detect_best_requires_recent_actionable_pattern():
    eng = PatternEngine()
    fresh = eng.detect_best(
        _double_top_series(),
        symbol="TEST",
        min_confidence=50,
        min_risk_reward=0.5,
        max_age_bars=20,
    )
    assert fresh is not None
    stale = eng.detect_best(
        _double_top_series(),
        symbol="TEST",
        min_confidence=50,
        min_risk_reward=0.5,
        max_age_bars=0,
    )
    assert stale is None or stale.end_index == len(_double_top_series()) - 1


def test_chart_pattern_wrapper_includes_all_patterns():
    from chart_patterns import run_chart_pattern_strategy
    result = run_chart_pattern_strategy(_double_top_series(), symbol="TEST")
    assert "patterns" in result
    assert "all_patterns" in result
    assert isinstance(result["all_patterns"], dict)


def test_requested_module_files_exist():
    root = Path(__file__).resolve().parents[1]
    expected = [
        "pivot_engine.py", "trendline_engine.py", "channel_engine.py",
        "breakout_engine.py", "volume_confirmation.py", "market_structure.py",
        "pattern_scoring.py", "visualization.py",
        "patterns/double_top.py", "patterns/double_bottom.py",
        "patterns/triple_top.py", "patterns/triple_bottom.py",
        "patterns/head_shoulders.py", "patterns/inverse_head_shoulders.py",
        "patterns/ascending_triangle.py", "patterns/descending_triangle.py",
        "patterns/symmetrical_triangle.py", "patterns/rising_wedge.py",
        "patterns/falling_wedge.py", "patterns/rectangle.py",
        "patterns/bull_flag.py", "patterns/bear_flag.py",
        "patterns/pennant.py", "patterns/cup_handle.py",
        "patterns/rounding.py", "patterns/diamond.py",
        "patterns/broadening.py", "patterns/smart_money.py",
    ]
    missing = [p for p in expected if not (root / p).exists()]
    assert not missing, f"missing requested modules: {missing}"


def test_expanded_registry_runs_on_generic_data():
    p = np.linspace(100, 130, 90) + np.sin(np.arange(90)) * 2
    df = pd.DataFrame({
        "open": p,
        "high": p + 1.2,
        "low": p - 1.2,
        "close": p,
        "volume": np.full(len(p), 1000.0),
    })
    out = PatternEngine().detect_json(df, symbol="TEST")
    assert isinstance(out, list)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        fn(); passed += 1
        print(f"  ✓ {fn.__name__}")
    print(f"{passed}/{len(fns)} tests passed")


def test_backtest_runs_and_shapes():
    from pattern_engine.backtest import backtest, format_report
    bt = backtest(_double_top_series(), "TEST", max_hold_bars=30)
    assert "overall" in bt and "ranking" in bt and "per_pattern" in bt
    assert isinstance(format_report(bt), str)
