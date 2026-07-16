import pandas as pd

import option_strategy_regime as osr


def _flat_df(n=60, price=22000.0):
    return pd.DataFrame({
        "open": [price] * n, "high": [price * 1.001] * n,
        "low": [price * 0.999] * n, "close": [price] * n,
        "volume": [1000] * n,
    })


def _trending_df(n=60, start=21000.0, step=15.0):
    closes = [start + i * step for i in range(n)]
    return pd.DataFrame({
        "open": closes, "high": [c * 1.002 for c in closes],
        "low": [c * 0.998 for c in closes], "close": closes,
        "volume": [1000] * n,
    })


def test_low_vol_overlay_independent_of_primary(monkeypatch):
    monkeypatch.setattr(osr, "_CACHE", {})
    monkeypatch.setattr("expiry_regime.get_expiry_regime",
                         lambda symbol="NIFTY": {"is_expiry_day": False, "days_to_expiry": 5, "next_expiry": "2026-07-22", "regime_label": "NORMAL"})
    monkeypatch.setattr("time_regime.get_time_zone", lambda now=None: type("Z", (), {"value": "PRIMARY_TREND"})())
    monkeypatch.setattr("greeks_live.get_event_playbook", lambda symbol="NIFTY": {"events": [], "has_event": False})

    result = osr.detect_regime(_flat_df(), vix=10.0, symbol="NIFTY")
    assert result["low_vol"] is True
    assert result["vix"] == 10.0


def test_high_vol_primary_from_classify_regime(monkeypatch):
    monkeypatch.setattr(osr, "_CACHE", {})
    monkeypatch.setattr("expiry_regime.get_expiry_regime",
                         lambda symbol="NIFTY": {"is_expiry_day": False, "days_to_expiry": 5, "next_expiry": "", "regime_label": "NORMAL"})
    monkeypatch.setattr("time_regime.get_time_zone", lambda now=None: type("Z", (), {"value": "PRIMARY_TREND"})())
    monkeypatch.setattr("greeks_live.get_event_playbook", lambda symbol="NIFTY": {"events": [], "has_event": False})

    result = osr.detect_regime(_flat_df(), vix=25.0, symbol="NIFTY")
    assert result["primary"] == "HIGH_VOL"


def test_expiry_and_event_overlays_can_coexist_with_any_primary(monkeypatch):
    monkeypatch.setattr(osr, "_CACHE", {})
    monkeypatch.setattr("expiry_regime.get_expiry_regime",
                         lambda symbol="NIFTY": {"is_expiry_day": True, "days_to_expiry": 0, "next_expiry": "2026-07-16", "regime_label": "WEEKLY_EXPIRY"})
    monkeypatch.setattr("time_regime.get_time_zone", lambda now=None: type("Z", (), {"value": "PRIMARY_TREND"})())
    monkeypatch.setattr("greeks_live.get_event_playbook",
                         lambda symbol="NIFTY": {"events": ["RBI MPC TODAY"], "has_event": True})

    result = osr.detect_regime(_flat_df(), vix=25.0, symbol="NIFTY")
    # A day can legitimately be EXPIRY, EVENT, and HIGH_VOL simultaneously --
    # that's the whole point of overlay flags instead of one exclusive label.
    assert result["primary"] == "HIGH_VOL"
    assert result["expiry_day"] is True
    assert result["event"] is True
    assert result["events"] == ["RBI MPC TODAY"]


def test_gap_pct_computed_from_today_open_vs_prev_close():
    today = pd.DataFrame({"open": [22200.0], "high": [22210.0], "low": [22190.0],
                           "close": [22205.0], "volume": [1000]})
    daily = pd.DataFrame({"open": [21900.0], "high": [22000.0], "low": [21850.0],
                          "close": [22000.0], "volume": [50000]})
    gap = osr._gap_pct(today, daily)
    assert abs(gap - (200.0 / 22000.0)) < 1e-9


def test_gap_pct_safe_default_when_inputs_missing():
    assert osr._gap_pct(None, None) == 0.0
    assert osr._gap_pct(pd.DataFrame(), pd.DataFrame()) == 0.0


def test_regime_cache_reused_within_ttl(monkeypatch):
    monkeypatch.setattr(osr, "_CACHE", {})
    monkeypatch.setattr("expiry_regime.get_expiry_regime",
                         lambda symbol="NIFTY": {"is_expiry_day": False, "days_to_expiry": 5, "next_expiry": "", "regime_label": "NORMAL"})
    monkeypatch.setattr("time_regime.get_time_zone", lambda now=None: type("Z", (), {"value": "PRIMARY_TREND"})())
    monkeypatch.setattr("greeks_live.get_event_playbook", lambda symbol="NIFTY": {"events": [], "has_event": False})

    first = osr.detect_regime(_flat_df(), vix=10.0, symbol="NIFTY")
    # Second call with a wildly different vix should still return the cached
    # (first) result within the TTL window -- proves the cache is consulted.
    second = osr.detect_regime(_flat_df(), vix=99.0, symbol="NIFTY")
    assert second["vix"] == first["vix"] == 10.0
