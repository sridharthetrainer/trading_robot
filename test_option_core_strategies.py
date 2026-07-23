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


def test_c1_and_c3_persist_real_volume_and_spread_not_zero_default(monkeypatch):
    """2026-07-23 bug: _persist_shadow_legs/_persist_multi_side_shadow_legs
    dropped the volume/spread_pct that _leg_quote() already computes, so every
    C1/C3 leg landed with the schema's volume=0/spread_pct=NULL defaults. The
    nightly labeller (option_multistrike_signals.label_multistrike_outcomes)
    then fed volume=0.0 into shadow_execution.simulate_option_round_trip(),
    which reads observed_volume=0.0 as "confirmed zero liquidity" (not
    "unknown") and marks the leg REJECTED/insufficient_observed_volume even
    though real volume existed in the chain at signal time -- 3 days of
    shadow trades produced zero usable evidence as a result. This asserts the
    real chain volume/spread survive into the persisted row."""
    monkeypatch.setattr(ocs.odj, "record_option_decision", lambda **kw: kw)
    option_data = _synthetic_chain()  # _mk_row's default vol=10000, bid/ask 1% apart
    conn = sqlite3.connect(":memory:")
    ocs.evaluate_c1_short_straddle(symbol="NIFTY", option_data=option_data, regime=_CALM_REGIME, conn=conn)
    ocs.evaluate_c3_iron_condor(symbol="NIFTY", option_data=option_data, regime=_CALM_REGIME, conn=conn)

    for strategy in ("C1", "C3"):
        rows_db = conn.execute(
            "SELECT volume, spread_pct FROM option_strike_signals WHERE strategy=?",
            (strategy,)).fetchall()
        assert rows_db, f"no rows persisted for {strategy}"
        for volume, spread_pct in rows_db:
            assert volume > 0, f"{strategy} leg persisted with volume<=0: {volume}"
            assert spread_pct is not None and spread_pct > 0, \
                f"{strategy} leg persisted with spread_pct not captured: {spread_pct}"


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


def test_oi_direction_classifies_buildup_unwinding_flat():
    buildup = ocs._oi_direction(10000, 2000)   # +25% vs prior 8000
    assert buildup["oi_direction"] == "BUILDUP"
    assert buildup["oi_emoji"] == "🟢"

    unwinding = ocs._oi_direction(8000, -2000)  # -20% vs prior 10000
    assert unwinding["oi_direction"] == "UNWINDING"
    assert unwinding["oi_emoji"] == "🔴"

    flat = ocs._oi_direction(10000, 50)         # +0.5%
    assert flat["oi_direction"] == "FLAT"
    assert flat["oi_emoji"] == "⚪"

    # no prior OI (new contract) must not divide by zero
    fresh = ocs._oi_direction(500, 500)
    assert fresh["oi_direction"] == "FLAT"


def test_c1_legs_carry_oi_direction_fields(monkeypatch):
    fake = _RecorderFake()
    monkeypatch.setattr(ocs.odj, "record_option_decision", fake)
    conn = sqlite3.connect(":memory:")

    result = ocs.evaluate_c1_short_straddle(
        symbol="NIFTY", option_data=_synthetic_chain(), regime=_CALM_REGIME, conn=conn)

    assert result["status"] == "selected"
    legs = fake.calls[0]["selected"]["legs"]
    assert len(legs) == 2
    for leg in legs:
        assert leg["oi_direction"] in {"BUILDUP", "UNWINDING", "FLAT"}
        assert leg["oi_emoji"] in {"🟢", "🔴", "⚪"}


# ── D1: Pre-Event Long Strangle ─────────────────────────────────────────────

class _FakeEventCalendar:
    def __init__(self, has_event: bool):
        self._has_event = has_event

    def get_today_events(self):
        if not self._has_event:
            return []
        return [{"date": "2026-07-30", "event": "US Fed FOMC", "impact": "HIGH"}]


def _mock_event(monkeypatch, has_event=True):
    monkeypatch.setattr("event_calendar.get_event_calendar",
                         lambda: _FakeEventCalendar(has_event))


class _FakeGapRiskManager:
    def __init__(self, ivp=20.0):
        self._ivp = ivp

    def get_iv_percentile(self, underlying):
        return self._ivp


def test_d1_selects_long_strangle_in_delta_band_and_journals_buy_side(monkeypatch):
    fake = _RecorderFake()
    monkeypatch.setattr(ocs.odj, "record_option_decision", fake)
    _mock_event(monkeypatch, True)
    option_data = _synthetic_chain()
    conn = sqlite3.connect(":memory:")

    result = ocs.evaluate_d1_pre_event_strangle(
        symbol="NIFTY", option_data=option_data, regime=_CALM_REGIME, conn=conn,
        gap_risk_manager=_FakeGapRiskManager(20.0))

    assert result["status"] == "selected"
    assert result["net_debit"] > 0
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["side"] == "BUY"
    assert call["selected"]["risk_profile"] == "defined_risk"
    legs = call["selected"]["legs"]
    assert {leg["option_type"] for leg in legs} == {"CE", "PE"}
    ce = next(leg for leg in legs if leg["option_type"] == "CE")
    pe = next(leg for leg in legs if leg["option_type"] == "PE")
    assert ce["strike"] != pe["strike"]  # strangle, not a straddle
    for leg in (ce, pe):
        assert 0.30 - 1e-6 <= leg["delta"] <= 0.35 + 1e-6


def test_d1_blocked_when_no_event_today(monkeypatch):
    fake = _RecorderFake()
    monkeypatch.setattr(ocs.odj, "record_option_decision", fake)
    _mock_event(monkeypatch, False)
    option_data = _synthetic_chain()

    result = ocs.evaluate_d1_pre_event_strangle(
        symbol="NIFTY", option_data=option_data, regime=_CALM_REGIME,
        gap_risk_manager=_FakeGapRiskManager(20.0))

    assert result["status"] == "blocked_no_event_today"
    assert fake.calls[0]["side"] == "BUY"
    assert fake.calls[0]["decision"] == "blocked_no_event_today"


def test_d1_blocked_when_gap_risk_manager_missing_or_incompatible(monkeypatch):
    monkeypatch.setattr(ocs.odj, "record_option_decision", lambda **kw: kw)
    _mock_event(monkeypatch, True)
    option_data = _synthetic_chain()

    result_none = ocs.evaluate_d1_pre_event_strangle(
        symbol="NIFTY", option_data=option_data, regime=_CALM_REGIME, gap_risk_manager=None)
    assert result_none["status"] == "blocked_ivp_unavailable_fail_closed"

    result_incompatible = ocs.evaluate_d1_pre_event_strangle(
        symbol="NIFTY", option_data=option_data, regime=_CALM_REGIME, gap_risk_manager=object())
    assert result_incompatible["status"] == "blocked_ivp_unavailable_fail_closed"


def test_d1_blocked_when_ivp_above_max_including_neutral_default(monkeypatch):
    monkeypatch.setattr(ocs.odj, "record_option_decision", lambda **kw: kw)
    _mock_event(monkeypatch, True)
    option_data = _synthetic_chain()

    # 50.0 is GapRiskManager.get_iv_percentile()'s neutral default for BOTH a
    # genuinely-computed 50 and "insufficient IV history" -- both must block.
    blocked_neutral = ocs.evaluate_d1_pre_event_strangle(
        symbol="NIFTY", option_data=option_data, regime=_CALM_REGIME,
        gap_risk_manager=_FakeGapRiskManager(50.0))
    assert blocked_neutral["status"] == "blocked_ivp_too_high"

    blocked_high = ocs.evaluate_d1_pre_event_strangle(
        symbol="NIFTY", option_data=option_data, regime=_CALM_REGIME,
        gap_risk_manager=_FakeGapRiskManager(36.0))
    assert blocked_high["status"] == "blocked_ivp_too_high"

    at_boundary = ocs.evaluate_d1_pre_event_strangle(
        symbol="NIFTY", option_data=option_data, regime=_CALM_REGIME,
        gap_risk_manager=_FakeGapRiskManager(35.0))
    assert at_boundary["status"] != "blocked_ivp_too_high"


def test_d1_blocked_on_missing_chain_data(monkeypatch):
    monkeypatch.setattr(ocs.odj, "record_option_decision", lambda **kw: kw)
    result = ocs.evaluate_d1_pre_event_strangle(
        symbol="NIFTY", option_data={}, regime=_CALM_REGIME,
        gap_risk_manager=_FakeGapRiskManager(20.0))
    assert result["status"] == "blocked_no_chain_data"


def test_d1_delta_band_enforced_via_delta_min_param(monkeypatch):
    monkeypatch.setattr(ocs.odj, "record_option_decision", lambda **kw: kw)
    monkeypatch.setenv("OPT_STRAT_D1_DELTA_MIN", "0.45")  # impossible: delta_max stays 0.35
    _mock_event(monkeypatch, True)
    option_data = _synthetic_chain()

    result = ocs.evaluate_d1_pre_event_strangle(
        symbol="NIFTY", option_data=option_data, regime=_CALM_REGIME,
        gap_risk_manager=_FakeGapRiskManager(20.0))

    assert result["status"] == "blocked_delta_out_of_target_band"


def test_d1_persists_buy_side_legs_with_shared_combo_id(monkeypatch):
    monkeypatch.setattr(ocs.odj, "record_option_decision", lambda **kw: kw)
    _mock_event(monkeypatch, True)
    option_data = _synthetic_chain()
    conn = sqlite3.connect(":memory:")

    ocs.evaluate_d1_pre_event_strangle(
        symbol="NIFTY", option_data=option_data, regime=_CALM_REGIME, conn=conn,
        gap_risk_manager=_FakeGapRiskManager(20.0))

    rows_db = conn.execute(
        "SELECT strike, option_type, side, combo_id FROM option_strike_signals "
        "WHERE strategy='D1'").fetchall()
    assert len(rows_db) == 2
    assert all(side == "BUY" for _, _, side, _ in rows_db)
    combo_ids = {combo_id for *_, combo_id in rows_db}
    assert len(combo_ids) == 1
    strikes = {strike for strike, *_ in rows_db}
    assert len(strikes) == 2


def test_d1_legs_carry_oi_direction_fields(monkeypatch):
    fake = _RecorderFake()
    monkeypatch.setattr(ocs.odj, "record_option_decision", fake)
    _mock_event(monkeypatch, True)
    conn = sqlite3.connect(":memory:")

    result = ocs.evaluate_d1_pre_event_strangle(
        symbol="NIFTY", option_data=_synthetic_chain(), regime=_CALM_REGIME, conn=conn,
        gap_risk_manager=_FakeGapRiskManager(20.0))

    assert result["status"] == "selected"
    legs = fake.calls[0]["selected"]["legs"]
    assert len(legs) == 2
    for leg in legs:
        assert leg["oi_direction"] in {"BUILDUP", "UNWINDING", "FLAT"}
        assert leg["oi_emoji"] in {"🟢", "🔴", "⚪"}


def test_d1_persists_real_volume_and_spread_not_zero_default(monkeypatch):
    """Same 2026-07-23 fix as C1/C3 -- see test_c1_and_c3_persist_real_volume_
    and_spread_not_zero_default's docstring."""
    monkeypatch.setattr(ocs.odj, "record_option_decision", lambda **kw: kw)
    _mock_event(monkeypatch, True)
    conn = sqlite3.connect(":memory:")

    ocs.evaluate_d1_pre_event_strangle(
        symbol="NIFTY", option_data=_synthetic_chain(), regime=_CALM_REGIME, conn=conn,
        gap_risk_manager=_FakeGapRiskManager(20.0))

    rows_db = conn.execute(
        "SELECT volume, spread_pct FROM option_strike_signals WHERE strategy='D1'").fetchall()
    assert rows_db
    for volume, spread_pct in rows_db:
        assert volume > 0
        assert spread_pct is not None and spread_pct > 0


def test_d1_net_debit_equals_max_loss_defined_risk(monkeypatch):
    fake = _RecorderFake()
    monkeypatch.setattr(ocs.odj, "record_option_decision", fake)
    _mock_event(monkeypatch, True)

    result = ocs.evaluate_d1_pre_event_strangle(
        symbol="NIFTY", option_data=_synthetic_chain(), regime=_CALM_REGIME,
        gap_risk_manager=_FakeGapRiskManager(20.0))

    assert result["status"] == "selected"
    assert fake.calls[0]["selected"]["max_loss"] == result["net_debit"]
