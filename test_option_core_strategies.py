import sqlite3

import option_core_strategies as ocs
from greeks_live import compute_greeks


def _mk_row(strike, ce_ltp, pe_ltp, iv=14.0, oi=50000, vol=10000):
    return {
        "strikePrice": strike,
        "CE": {"lastPrice": ce_ltp, "bidprice": ce_ltp * 0.99, "askPrice": ce_ltp * 1.01,
               "openInterest": oi, "totalTradedVolume": vol, "impliedVolatility": iv},
        "PE": {"lastPrice": pe_ltp, "bidprice": pe_ltp * 0.99, "askPrice": pe_ltp * 1.01,
               "openInterest": oi, "totalTradedVolume": vol, "impliedVolatility": iv},
    }


def _synthetic_chain(spot=22000.0, gap=50, steps=10, iv=14.0):
    rows = []
    for i in range(-steps, steps + 1):
        strike = spot + i * gap
        ce = max(5.0, 150 - max(0, i) * 15 - abs(min(0, i)) * 2)
        pe = max(5.0, 150 - max(0, -i) * 15 - abs(max(0, i)) * 2)
        rows.append(_mk_row(strike, ce, pe, iv=iv))
    return {"chain": rows, "spot": spot, "ts": 1_800_000_000.0}


_CALM_REGIME = {"primary": "MEAN_REVERT", "vix": 11.0, "gap": False,
                 "days_to_expiry": 5, "next_expiry": "2026-07-22"}


class _RecorderFake:
    def __init__(self):
        self.calls = []

    def __call__(self, **kw):
        self.calls.append(kw)
        return dict(kw)


def test_c1_selects_synthetic_atm_and_journals_sell_side(monkeypatch):
    fake = _RecorderFake()
    monkeypatch.setattr(ocs.odj, "record_option_decision", fake)
    option_data = _synthetic_chain()
    conn = sqlite3.connect(":memory:")

    result = ocs.evaluate_c1_short_straddle(
        symbol="NIFTY", option_data=option_data, regime=_CALM_REGIME, conn=conn)

    assert result["status"] == "selected"
    assert result["net_credit"] > 0
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["side"] == "SELL"
    assert call["selected"]["risk_profile"] == "undefined_risk"
    legs = call["selected"]["legs"]
    assert {leg["option_type"] for leg in legs} == {"CE", "PE"}
    assert legs[0]["strike"] == legs[1]["strike"]  # straddle: both legs same strike


def test_c1_cross_checked_delta_is_near_half_at_atm(monkeypatch):
    monkeypatch.setattr(ocs.odj, "record_option_decision", lambda **kw: kw)
    option_data = _synthetic_chain()
    conn = sqlite3.connect(":memory:")
    ocs.evaluate_c1_short_straddle(symbol="NIFTY", option_data=option_data,
                                    regime=_CALM_REGIME, conn=conn)
    rows_db = conn.execute(
        "SELECT strike, option_type FROM option_strike_signals WHERE strategy='C1'").fetchall()
    assert len(rows_db) == 2
    strike = rows_db[0][0]
    g_ce = compute_greeks(S=22000.0, K=strike, T=5 / 365.0, r=ocs.RISK_FREE_RATE, sigma=0.14, option="call")
    assert 0.35 < g_ce["delta"] < 0.65  # loosely ATM


def test_c1_blocked_on_strong_trend_regime(monkeypatch):
    fake = _RecorderFake()
    monkeypatch.setattr(ocs.odj, "record_option_decision", fake)
    option_data = _synthetic_chain()
    trending = dict(_CALM_REGIME, primary="STRONG_TREND")
    result = ocs.evaluate_c1_short_straddle(symbol="NIFTY", option_data=option_data, regime=trending)
    assert result["status"] == "blocked_regime_strong_trend"
    assert fake.calls[0]["decision"] == "blocked_regime_strong_trend"


def test_c1_blocked_on_gap_day(monkeypatch):
    monkeypatch.setattr(ocs.odj, "record_option_decision", lambda **kw: kw)
    option_data = _synthetic_chain()
    gapped = dict(_CALM_REGIME, gap=True)
    result = ocs.evaluate_c1_short_straddle(symbol="NIFTY", option_data=option_data, regime=gapped)
    assert result["status"] == "blocked_gap_too_large"


def test_c1_blocked_on_missing_chain_data(monkeypatch):
    monkeypatch.setattr(ocs.odj, "record_option_decision", lambda **kw: kw)
    result = ocs.evaluate_c1_short_straddle(symbol="NIFTY", option_data={}, regime=_CALM_REGIME)
    assert result["status"] == "blocked_no_chain_data"


def test_c3_iron_condor_is_defined_risk_and_four_legs(monkeypatch):
    fake = _RecorderFake()
    monkeypatch.setattr(ocs.odj, "record_option_decision", fake)
    option_data = _synthetic_chain()
    conn = sqlite3.connect(":memory:")

    result = ocs.evaluate_c3_iron_condor(
        symbol="NIFTY", option_data=option_data, regime=_CALM_REGIME, conn=conn)

    assert result["status"] == "selected"
    assert result["max_loss"] >= 0  # defined risk: never unbounded
    call = fake.calls[0]
    assert call["selected"]["risk_profile"] == "defined_risk"
    legs = call["selected"]["legs"]
    assert len(legs) == 4
    sides = sorted(leg["side"] for leg in legs)
    assert sides == ["BUY", "BUY", "SELL", "SELL"]
    # short call strike must be further OTM (higher) than long call is closer... actually
    # long call is the wing, further out than the short call.
    short_ce = next(l for l in legs if l["option_type"] == "CE" and l["side"] == "SELL")
    long_ce = next(l for l in legs if l["option_type"] == "CE" and l["side"] == "BUY")
    assert long_ce["strike"] > short_ce["strike"]
    short_pe = next(l for l in legs if l["option_type"] == "PE" and l["side"] == "SELL")
    long_pe = next(l for l in legs if l["option_type"] == "PE" and l["side"] == "BUY")
    assert long_pe["strike"] < short_pe["strike"]


def test_c3_short_leg_delta_is_near_target(monkeypatch):
    monkeypatch.setattr(ocs.odj, "record_option_decision", lambda **kw: kw)
    option_data = _synthetic_chain()
    conn = sqlite3.connect(":memory:")
    ocs.evaluate_c3_iron_condor(symbol="NIFTY", option_data=option_data, regime=_CALM_REGIME, conn=conn)
    rows_db = conn.execute(
        "SELECT strike, option_type, side FROM option_strike_signals WHERE strategy='C3'").fetchall()
    assert len(rows_db) == 4
    for strike, option_type, side in rows_db:
        if side == "SELL":
            delta = abs(ocs._delta_of(option_data["chain"], strike, option_type, 22000.0, 5 / 365.0))
            assert delta <= 0.16 + 1e-6


def test_c3_blocked_on_strong_trend_regime(monkeypatch):
    monkeypatch.setattr(ocs.odj, "record_option_decision", lambda **kw: kw)
    option_data = _synthetic_chain()
    trending = dict(_CALM_REGIME, primary="STRONG_TREND")
    result = ocs.evaluate_c3_iron_condor(symbol="NIFTY", option_data=option_data, regime=trending)
    assert result["status"] == "blocked_regime_strong_trend"


def test_c1_and_c3_combos_persist_with_shared_combo_id(monkeypatch):
    monkeypatch.setattr(ocs.odj, "record_option_decision", lambda **kw: kw)
    option_data = _synthetic_chain()
    conn = sqlite3.connect(":memory:")
    ocs.evaluate_c1_short_straddle(symbol="NIFTY", option_data=option_data, regime=_CALM_REGIME, conn=conn)
    ocs.evaluate_c3_iron_condor(symbol="NIFTY", option_data=option_data, regime=_CALM_REGIME, conn=conn)

    c1_combo_ids = {r[0] for r in conn.execute(
        "SELECT combo_id FROM option_strike_signals WHERE strategy='C1'")}
    c3_combo_ids = {r[0] for r in conn.execute(
        "SELECT combo_id FROM option_strike_signals WHERE strategy='C3'")}
    assert len(c1_combo_ids) == 1
    assert len(c3_combo_ids) == 1
    assert c1_combo_ids != c3_combo_ids


# ── Adjustment functions: pure, independently testable, never called live ──

def test_c1_adjustment_flags_delta_hedge_on_breach():
    result = ocs.evaluate_c1_adjustment(
        current_deltas={"CE": 0.40, "PE": -0.20}, current_spot=22050.0, entry_spot=22000.0)
    assert "delta_hedge_with_futures" in result["recommendations"]


def test_c1_adjustment_flags_shift_on_large_move():
    result = ocs.evaluate_c1_adjustment(
        current_deltas={"CE": 0.50, "PE": -0.50}, current_spot=22200.0, entry_spot=22000.0)
    assert "shift_to_new_atm" in result["recommendations"]


def test_c1_adjustment_no_action_when_calm():
    result = ocs.evaluate_c1_adjustment(
        current_deltas={"CE": 0.50, "PE": -0.50}, current_spot=22010.0, entry_spot=22000.0)
    assert result["recommendations"] == ["no_adjustment_needed"]


def test_c3_adjustment_flags_close_call_side_on_breach():
    result = ocs.evaluate_c3_adjustment(
        legs=[], current_spot=22500.0, short_ce_strike=22400.0, short_pe_strike=21650.0)
    assert "close_call_side_let_put_run_as_credit_vertical" in result["recommendations"]


def test_c3_adjustment_no_action_inside_range():
    result = ocs.evaluate_c3_adjustment(
        legs=[], current_spot=22000.0, short_ce_strike=22400.0, short_pe_strike=21650.0)
    assert result["recommendations"] == ["no_adjustment_needed"]
