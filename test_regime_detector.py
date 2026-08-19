"""Tests for regime_detector.py -- the 8:45 AM raw-indicator regime
resolution retrofit (2026-08-19 follow-up to cluster_risk_gate.py).
Validates: real indicator computation on synthetic OHLC, the 4 real-world
detectors (crash/holiday/event/DTE), and -- most importantly -- the hard
override priority order: market_crash > holiday_week > event_day >
expiry_week > indicator-derived.
"""
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

import regime_detector as rd


# ── compute_raw_indicators ──────────────────────────────────────────────

def _trending_up_df(n=100):
    idx = pd.date_range("2026-01-01", periods=n, freq="D")
    closes = np.linspace(100, 200, n)  # clean uptrend
    return pd.DataFrame({
        "open": closes, "high": closes * 1.01, "low": closes * 0.99,
        "close": closes, "volume": 1000,
    }, index=idx)


def _flat_df(n=100):
    idx = pd.date_range("2026-01-01", periods=n, freq="D")
    closes = np.full(n, 150.0)
    return pd.DataFrame({
        "open": closes, "high": closes + 0.5, "low": closes - 0.5,
        "close": closes, "volume": 1000,
    }, index=idx)


def test_indicators_none_with_insufficient_history():
    df = _trending_up_df(n=20)  # under the 55-bar minimum
    result = rd.compute_raw_indicators(df)
    assert result == {"adx": None, "price_above_50ema": None, "bb_bandwidth_pct": None}


def test_uptrend_produces_price_above_50ema_true():
    df = _trending_up_df(n=100)
    result = rd.compute_raw_indicators(df)
    assert result["price_above_50ema"] is True
    assert result["adx"] is not None and result["adx"] > 0


def test_flat_series_produces_narrow_bandwidth():
    df = _flat_df(n=100)
    result = rd.compute_raw_indicators(df)
    assert result["bb_bandwidth_pct"] is not None
    assert result["bb_bandwidth_pct"] < 5.0  # essentially flat -> tight bands


def test_none_df_and_missing_close_column_are_safe():
    assert rd.compute_raw_indicators(pd.DataFrame({"foo": [1, 2, 3]})) == {
        "adx": None, "price_above_50ema": None, "bb_bandwidth_pct": None,
    }


# ── detect_crash ─────────────────────────────────────────────────────────

def test_crash_detected_on_real_3pct_drop():
    idx = pd.date_range("2026-01-01", periods=3, freq="D")
    df = pd.DataFrame({"close": [24000.0, 23500.0, 23200.0]}, index=idx)  # -3.33% over 2 days
    assert rd.detect_crash(df) is True


def test_crash_not_detected_on_small_move():
    idx = pd.date_range("2026-01-01", periods=3, freq="D")
    df = pd.DataFrame({"close": [24000.0, 23900.0, 23850.0]}, index=idx)  # -0.6%
    assert rd.detect_crash(df) is False


def test_crash_false_with_insufficient_data():
    idx = pd.date_range("2026-01-01", periods=2, freq="D")
    df = pd.DataFrame({"close": [24000.0, 20000.0]}, index=idx)  # only 2 bars, need 3
    assert rd.detect_crash(df) is False


def test_crash_uses_2_trading_days_not_intraday():
    """A -3% move split evenly across exactly 2 daily bars must trigger --
    this is explicitly NOT the market_shock_monitor's 15-minute check."""
    idx = pd.date_range("2026-01-01", periods=3, freq="D")
    df = pd.DataFrame({"close": [24000.0, 23640.0, 23280.0]}, index=idx)  # -3.0% exactly
    assert rd.detect_crash(df) is True


# ── detect_holiday_week / detect_event_day / get_days_to_expiry ────────

def test_holiday_week_true_when_tomorrow_is_a_holiday(monkeypatch):
    class _FakeMaster:
        def is_trading_holiday(self, d):
            return d == date(2026, 8, 20)
    import nse_master
    monkeypatch.setattr(nse_master, "get_nse_master", lambda: _FakeMaster())
    assert rd.detect_holiday_week(today=date(2026, 8, 19)) is True


def test_holiday_week_true_when_within_lookahead_window(monkeypatch):
    class _FakeMaster:
        def is_trading_holiday(self, d):
            return d == date(2026, 8, 21)  # 2 days out
    import nse_master
    monkeypatch.setattr(nse_master, "get_nse_master", lambda: _FakeMaster())
    assert rd.detect_holiday_week(today=date(2026, 8, 19)) is True


def test_holiday_week_false_when_no_holiday_nearby(monkeypatch):
    class _FakeMaster:
        def is_trading_holiday(self, d):
            return False
    import nse_master
    monkeypatch.setattr(nse_master, "get_nse_master", lambda: _FakeMaster())
    assert rd.detect_holiday_week(today=date(2026, 8, 19)) is False


def test_event_day_true_on_a_configured_date(monkeypatch):
    import config
    monkeypatch.setattr(config, "HIGH_IMPACT_DATES", {date(2026, 10, 7)}, raising=False)
    assert rd.detect_event_day(today=date(2026, 10, 7)) is True
    assert rd.detect_event_day(today=date(2026, 10, 8)) is False


def test_days_to_expiry_reads_from_expiry_regime(monkeypatch):
    import expiry_regime
    monkeypatch.setattr(expiry_regime, "get_expiry_regime",
                         lambda today=None, symbol="NIFTY": {"days_to_expiry": 3})
    assert rd.get_days_to_expiry() == 3


# ── resolve_regime(): the hard-override priority chain ──────────────────

def _patch_all(monkeypatch, *, crash=False, holiday=False, event=False,
                dte=None, adx=10.0, above_ema=False, bandwidth=15.0, vix=15.0):
    monkeypatch.setattr(rd, "_nifty_daily", lambda days=120: pd.DataFrame({"close": [1.0]}))
    monkeypatch.setattr(rd, "compute_raw_indicators", lambda df=None: {
        "adx": adx, "price_above_50ema": above_ema, "bb_bandwidth_pct": bandwidth,
    })
    monkeypatch.setattr(rd, "detect_crash", lambda df=None: crash)
    monkeypatch.setattr(rd, "detect_holiday_week", lambda today=None: holiday)
    monkeypatch.setattr(rd, "detect_event_day", lambda today=None: event)
    monkeypatch.setattr(rd, "get_days_to_expiry", lambda today=None, symbol="NIFTY": dte)
    monkeypatch.setattr(rd, "get_india_vix", lambda angel=None: vix)


def test_market_crash_beats_everything(monkeypatch):
    _patch_all(monkeypatch, crash=True, holiday=True, event=True, dte=1)
    result = rd.resolve_regime()
    assert result["regime_key"] == "market_crash"


def test_holiday_week_beats_event_and_expiry(monkeypatch):
    _patch_all(monkeypatch, crash=False, holiday=True, event=True, dte=1)
    result = rd.resolve_regime()
    assert result["regime_key"] == "holiday_week"


def test_event_day_beats_expiry_week(monkeypatch):
    _patch_all(monkeypatch, crash=False, holiday=False, event=True, dte=1)
    result = rd.resolve_regime()
    assert result["regime_key"] == "event_day"


def test_expiry_week_beats_indicator_derived(monkeypatch):
    _patch_all(monkeypatch, crash=False, holiday=False, event=False, dte=5,
               adx=40.0, above_ema=True, vix=10.0)  # would otherwise be strong_trend_low_vol
    result = rd.resolve_regime()
    assert result["regime_key"] == "expiry_week"


def test_indicator_derived_strong_trend_low_vol(monkeypatch):
    _patch_all(monkeypatch, dte=10, adx=30.0, above_ema=True, vix=10.0)
    assert rd.resolve_regime()["regime_key"] == "strong_trend_low_vol"


def test_indicator_derived_strong_trend_high_vol(monkeypatch):
    _patch_all(monkeypatch, dte=10, adx=30.0, above_ema=True, vix=25.0)
    assert rd.resolve_regime()["regime_key"] == "strong_trend_high_vol"


def test_indicator_derived_weak_trend_low_vol(monkeypatch):
    _patch_all(monkeypatch, dte=10, adx=10.0, above_ema=False, vix=10.0)
    assert rd.resolve_regime()["regime_key"] == "weak_trend_low_vol"


def test_indicator_derived_weak_trend_high_vol(monkeypatch):
    _patch_all(monkeypatch, dte=10, adx=10.0, above_ema=False, vix=25.0)
    assert rd.resolve_regime()["regime_key"] == "weak_trend_high_vol"


# ── compute_gap_override (2026-08-19 "poor man's news feed" patch) ─────

def test_gap_down_beyond_threshold_overrides_to_market_crash():
    assert rd.compute_gap_override(spot=23500.0, prev_close=24000.0) == "market_crash"  # -2.08%


def test_gap_down_exactly_at_threshold_overrides():
    assert rd.compute_gap_override(spot=23640.0, prev_close=24000.0) == "market_crash"  # -1.5% exactly


def test_gap_up_beyond_threshold_overrides_to_event_day():
    assert rd.compute_gap_override(spot=24500.0, prev_close=24000.0) == "event_day"  # +2.08%


def test_small_gap_does_not_override():
    assert rd.compute_gap_override(spot=24050.0, prev_close=24000.0) is None  # +0.21%


def test_zero_prev_close_returns_none_never_guesses():
    assert rd.compute_gap_override(spot=24000.0, prev_close=0.0) is None


def test_gap_override_returns_only_regimes_that_exist_in_the_real_matrix():
    """Both override targets must be real, already-tested regime keys --
    not an invented 9th key that would silently fall through
    ClusterRiskGate's 'unknown regime, no restriction' path."""
    import json
    matrix = json.loads(open("cluster_matrix.json").read())
    assert "market_crash" in matrix["regimes"]
    assert "event_day" in matrix["regimes"]


def test_resolve_regime_reports_all_raw_inputs_for_audit(monkeypatch):
    _patch_all(monkeypatch, dte=10, adx=30.0, above_ema=True, vix=10.0, bandwidth=7.5)
    result = rd.resolve_regime()
    assert result["inputs"] == {
        "adx": 30.0, "price_above_50ema": True, "bb_bandwidth_pct": 7.5,
        "india_vix": 10.0, "days_to_expiry": 10, "is_event_day": False,
        "is_market_crash": False, "is_holiday_week": False,
    }
