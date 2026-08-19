"""Tests for market_shock_monitor.py -- the emergency-shutdown-trigger gap
found in the 2026-08-19 spec audit (kill_switch.py previously only tripped
on system-health/loss-lock events, never on a fast market move, VIX spike,
drawdown, or margin breach).
"""
import time

import pandas as pd
import pytest

import market_shock_monitor as msm


# ── check_nifty_shock ───────────────────────────────────────────────────

def _make_1m_df(closes):
    idx = pd.date_range("2026-08-19 09:15", periods=len(closes), freq="1min")
    return pd.DataFrame({"open": closes, "high": closes, "low": closes,
                          "close": closes, "volume": 1000}, index=idx)


def test_nifty_shock_detected_on_a_real_2pct_drop(monkeypatch):
    closes = [24000.0] * 16
    closes[-1] = 24000.0 * (1 - 0.025)  # -2.5% on the last bar
    df = _make_1m_df(closes)
    monkeypatch.setattr("candle_cache.get_cached_candles", lambda *a, **k: df)

    result = msm.check_nifty_shock()
    assert result is not None
    assert result["move_pct"] < -0.02


def test_nifty_shock_none_when_move_is_small(monkeypatch):
    closes = [24000.0] * 16
    closes[-1] = 24000.0 * 1.005  # +0.5%, well under threshold
    df = _make_1m_df(closes)
    monkeypatch.setattr("candle_cache.get_cached_candles", lambda *a, **k: df)

    result = msm.check_nifty_shock()
    assert result is not None
    assert abs(result["move_pct"]) < msm.NIFTY_SHOCK_PCT


def test_nifty_shock_returns_none_with_insufficient_bars(monkeypatch):
    df = _make_1m_df([24000.0] * 5)  # fewer than NIFTY_SHOCK_WINDOW_MIN+1
    monkeypatch.setattr("candle_cache.get_cached_candles", lambda *a, **k: df)
    assert msm.check_nifty_shock() is None


def test_nifty_shock_returns_none_when_cache_empty(monkeypatch):
    monkeypatch.setattr("candle_cache.get_cached_candles", lambda *a, **k: None)
    assert msm.check_nifty_shock() is None


# ── check_vix_spike ──────────────────────────────────────────────────────

class _FakeVixFeed:
    def __init__(self, history):
        self._history = history


def test_vix_spike_detected(monkeypatch):
    now = time.time()
    feed = _FakeVixFeed([{"ts": now - 1800, "vix": 18.0}, {"ts": now, "vix": 27.5}])
    result = msm.check_vix_spike(feed)
    assert result is not None
    assert result["current"] == 27.5


def test_vix_spike_none_with_empty_history():
    assert msm.check_vix_spike(_FakeVixFeed([])) is None


def test_vix_spike_none_with_single_sample():
    feed = _FakeVixFeed([{"ts": time.time(), "vix": 30.0}])
    assert msm.check_vix_spike(feed) is None  # not enough samples in-window to trust


# ── check_drawdown ───────────────────────────────────────────────────────

class _FakeCompounder:
    def __init__(self, peak, history):
        self._peak_equity = peak
        self._equity_history = history


def test_drawdown_computed_from_peak_and_latest_history():
    cc = _FakeCompounder(peak=1_000_000, history=[1_000_000, 950_000, 880_000])
    result = msm.check_drawdown(cc)
    assert result is not None
    assert result["drawdown_pct"] == pytest.approx(0.12)


def test_drawdown_none_when_compounder_missing():
    assert msm.check_drawdown(None) is None


def test_drawdown_none_when_no_history_yet():
    cc = _FakeCompounder(peak=1_000_000, history=[])
    assert msm.check_drawdown(cc) is None


# ── evaluate() / run_market_shock_check() ────────────────────────────────

class _FakeKillSwitch:
    def __init__(self):
        self.active = False
        self.triggered_with = None

    def is_active(self):
        return self.active

    def trigger(self, reason, source, force_close=False):
        self.active = True
        self.triggered_with = {"reason": reason, "source": source, "force_close": force_close}
        return True


def test_evaluate_returns_no_trips_when_all_checks_unavailable(monkeypatch):
    monkeypatch.setattr("candle_cache.get_cached_candles", lambda *a, **k: None)
    result = msm.evaluate(angel=None, vix_feed=None, capital_compounder=None)
    assert result["tripped"] == []


def test_run_market_shock_check_trips_kill_switch_on_drawdown(monkeypatch):
    monkeypatch.setattr("candle_cache.get_cached_candles", lambda *a, **k: None)
    cc = _FakeCompounder(peak=1_000_000, history=[880_000])  # 12% drawdown
    ks = _FakeKillSwitch()

    result = msm.run_market_shock_check(ks, capital_compounder=cc)

    assert result["kill_switch_triggered"] is True
    assert ks.active is True
    assert ks.triggered_with["force_close"] is True
    assert "Drawdown" in ks.triggered_with["reason"]


def test_run_market_shock_check_does_not_retrigger_an_already_active_switch(monkeypatch):
    monkeypatch.setattr("candle_cache.get_cached_candles", lambda *a, **k: None)
    cc = _FakeCompounder(peak=1_000_000, history=[880_000])
    ks = _FakeKillSwitch()
    ks.active = True  # already tripped by something else

    result = msm.run_market_shock_check(ks, capital_compounder=cc)

    assert "kill_switch_triggered" not in result
    assert ks.triggered_with is None


def test_run_market_shock_check_noop_when_nothing_tripped(monkeypatch):
    monkeypatch.setattr("candle_cache.get_cached_candles", lambda *a, **k: None)
    ks = _FakeKillSwitch()

    result = msm.run_market_shock_check(ks)

    assert result["tripped"] == []
    assert ks.active is False
