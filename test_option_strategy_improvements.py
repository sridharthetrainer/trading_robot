import copy
import json
import sqlite3
import time
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import option_core_strategies as core
import option_quality as quality
import option_strategy_regime as regime_module
import option_strategy_registry as registry
from option_multistrike_signals import build_multistrike_signals, persist_multistrike_signals
from option_strategy_risk_checks import can_enter_trade
from test_option_core_strategies import _synthetic_chain, _CALM_REGIME, _FakeGapRiskManager


@pytest.fixture(autouse=True)
def prevent_journal_writes(monkeypatch):
    monkeypatch.setattr(core.odj, "record_option_decision", lambda **kw: kw)


def chain():
    result = _synthetic_chain()
    # A single ATM row is enough for the buying quality check.
    result["chain"] = [row for row in result["chain"] if row["strikePrice"] == 22000]
    return result


@pytest.mark.parametrize("clock", [None, float("nan"), float("inf"), "invalid", "future"])
def test_unknown_or_invalid_freshness_rejects_buying(clock):
    data = chain()
    if clock is None:
        del data["ts"]
    else:
        data["ts"] = time.time() + 600 if clock == "future" else clock
    assert not quality.evaluate_option_buy_quality(data).ok


def test_fetch_clock_cannot_override_stale_exchange_clock():
    data = chain()
    stale = datetime.fromtimestamp(time.time() - 600, ZoneInfo("Asia/Kolkata"))
    data["records"] = {"timestamp": stale.strftime("%d-%b-%Y %H:%M:%S")}
    assert quality.chain_age_sec(data) >= 600
    assert quality.evaluate_option_buy_quality(data).reason == "option_chain_stale"


@pytest.mark.parametrize("bid,ask", [(0, 150), (155, 150), (float("inf"), 151), (149, float("nan"))])
def test_buy_quality_rejects_unusable_quotes(bid, ask):
    data = chain()
    data["chain"][0]["CE"].update(bidprice=bid, askPrice=ask)
    assert not quality.evaluate_option_buy_quality(data).ok


def test_quality_rejects_nonfinite_limits_and_spot():
    assert not quality.evaluate_option_buy_quality(chain(), min_atm_leg_volume=float("nan")).ok
    data = chain()
    data["spot"] = float("inf")
    assert not quality.evaluate_option_buy_quality(data).ok


def test_nested_and_flat_quotes_have_identical_liquidity():
    nested = chain()
    leg = nested["chain"][0]["CE"]
    flat = {"chain": [{"strikePrice": 22000, **{f"CE_{key}": value for key, value in leg.items()}}]}
    assert quality.extract_contract_liquidity(nested, 22000, "CE") == quality.extract_contract_liquidity(flat, 22000, "CE")


def test_mixed_expiry_chain_does_not_select_first_row_at_same_strike():
    current = chain()["chain"][0]
    wanted = {**copy.deepcopy(current), "expiryDate": "22-Jul-2026"}
    wrong = {**copy.deepcopy(current), "expiryDate": "29-Jul-2026"}
    wrong["CE"]["lastPrice"] = 999
    filtered = quality.filter_expiry_rows([wrong, wanted], "2026-07-22")
    assert len(filtered) == 1
    assert core._leg_quote(filtered, 22000, "CE")["last_price"] == 150


def test_nested_leg_expiry_mismatch_is_removed():
    row = chain()["chain"][0]
    row["expiryDate"] = "22-Jul-2026"
    row["CE"]["expiryDate"] = "29-Jul-2026"
    filtered = quality.filter_expiry_rows([row], "2026-07-22")
    assert "CE" not in filtered[0]
    assert "PE" in filtered[0]


def test_buy_quality_checks_expiry_tags_inside_legs():
    data = chain()
    data["chain"][0]["CE"]["expiryDate"] = "29-Jul-2026"
    data["chain"][0]["PE"]["expiryDate"] = "29-Jul-2026"
    result = quality.evaluate_option_buy_quality(data, expiry="2026-07-22")
    assert not result.ok


def test_session_range_uses_latest_session_only():
    df = pd.DataFrame({"high": [24000, 22030, 22040], "low": [20000, 22000, 22010]},
                      index=pd.to_datetime(["2026-10-13 15:25", "2026-10-14 09:15", "2026-10-14 09:20"]))
    assert quality._session_move_pct(df, 22000) == pytest.approx(40 / 22000 * 100)


@pytest.mark.parametrize("strategy", [core.evaluate_c1_short_straddle, core.evaluate_c3_iron_condor, core.evaluate_d1_pre_event_strangle])
def test_every_implemented_strategy_rejects_unknown_chain_age(strategy):
    data = _synthetic_chain()
    del data["ts"]
    result = strategy(symbol="NIFTY", option_data=data, regime=_CALM_REGIME)
    assert result["status"] == "blocked_chain_timestamp_missing"


@pytest.mark.parametrize("strategy", [core.evaluate_c1_short_straddle, core.evaluate_c3_iron_condor])
def test_short_volatility_strategies_block_event_days(strategy):
    result = strategy(symbol="NIFTY", option_data=_synthetic_chain(),
                      regime=dict(_CALM_REGIME, event=True))
    assert result["status"] == "blocked_event_risk_for_theta"


def test_short_straddle_enforces_adx_ceiling():
    result = core.evaluate_c1_short_straddle(symbol="NIFTY", option_data=_synthetic_chain(),
                                            regime=dict(_CALM_REGIME, adx=30))
    assert result["status"] == "blocked_adx_too_high_for_theta"


@pytest.mark.parametrize("ivp", [None, 49, 81, float("nan")])
def test_condor_requires_iv_percentile_inside_configured_band(ivp):
    manager = None if ivp is None else _FakeGapRiskManager(ivp)
    result = core.evaluate_c3_iron_condor(symbol="NIFTY", option_data=_synthetic_chain(),
                                         regime=_CALM_REGIME, gap_risk_manager=manager)
    assert result["status"] in {"blocked_ivp_unavailable_fail_closed", "blocked_ivp_out_of_target_band"}


def test_neutral_ivp_fallback_does_not_qualify_condor():
    manager = _FakeGapRiskManager(50)
    manager.iv_tracker = SimpleNamespace(_history={"NIFTY": [{"iv": 14}] * 5})
    assert core._iv_percentile(manager, "NIFTY") is None


def test_missing_iv_does_not_choose_delta_zero_strike():
    data = _synthetic_chain(iv=0)
    assert core._select_delta_strike(data["chain"], 22000, 50, .35, "CE", 5 / 365) is None


def test_expiry_day_greeks_use_remaining_hours():
    now = datetime(2026, 10, 14, 10, 30, tzinfo=ZoneInfo("Asia/Kolkata"))
    assert core._dte_years({"days_to_expiry": 0}, now) == pytest.approx(5 / 24 / 365)


@pytest.mark.parametrize("field,value", [("vix", float("nan")), ("adx", float("inf")),
                                        ("api_latency_sec", -1), ("open_undefined_risk_count", 0.5)])
def test_catalog_risk_inputs_fail_closed(field, value):
    inputs = dict(regime={"gap_pct": 0}, vix=15)
    inputs[field] = value
    assert can_enter_trade(**inputs) == (False, "invalid_risk_inputs")


def test_defined_risk_candidate_is_not_misclassified_as_second_naked_trade():
    assert can_enter_trade(regime={}, vix=15, open_undefined_risk_count=1,
                           risk_profile="defined_risk")[0]


@pytest.mark.parametrize("window", ["25:00-26:00", "09:70_sharp", "11:00-09:30"])
def test_invalid_entry_windows_fail_closed(window):
    assert not registry.entry_window_ok(window, datetime(2026, 10, 14, 10))


def test_aware_clock_is_converted_to_indian_market_time():
    utc = datetime(2026, 10, 14, 4, 30, tzinfo=ZoneInfo("UTC"))
    assert registry.entry_window_ok("09:30-10:30", utc)


@pytest.mark.parametrize("strategy_id", [item.id for item in registry.OPTION_STRATEGY_CATALOG])
def test_every_catalog_entry_has_honest_readiness(strategy_id):
    row = next(item for item in registry.catalog_readiness() if item["id"] == strategy_id)
    assert row["evaluation_mode"] == "shadow" and not row["live_ready"]
    if row["logic_status"] == "PLACEHOLDER":
        assert "entry_logic_not_implemented" in row["blockers"]


def test_gap_uses_current_session_open_and_previous_session_close():
    df = pd.DataFrame({"open": [21000, 21990, 22200, 22220],
                       "close": [21010, 22000, 22210, 22230]},
                      index=pd.to_datetime(["2026-10-13 09:15", "2026-10-13 15:25",
                                            "2026-10-14 09:15", "2026-10-14 09:20"]))
    assert regime_module._gap_pct(df) == pytest.approx(200 / 22000)


@pytest.mark.parametrize("previous_expiry,previous_time", [
    ("2026-07-29", "2026-07-20T10:00:00+05:30"),
    ("2026-07-22", "2026-07-19T10:00:00+05:30"),
    ("2026-07-22", "2026-07-20T09:00:00+05:30"),
])
def test_flow_warms_up_after_expiry_roll_overnight_or_data_gap(monkeypatch, previous_expiry, previous_time):
    monkeypatch.setenv("OPTION_EDGE_POLICY_REQUIRE_PROMISING", "false")
    monkeypatch.setenv("OPTION_FOCUSED_PAPER_ENABLED", "false")
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE option_chain_snapshots(ts REAL,snapshot_time TEXT,underlying TEXT,expiry TEXT,ok INTEGER,rows_json TEXT)")
    conn.execute("INSERT INTO option_chain_snapshots VALUES(?,?,?,?,?,?)",
                 (1, previous_time, "NIFTY", previous_expiry, 1, json.dumps(chain()["chain"])))
    result = persist_multistrike_signals(conn=conn, snapshot_time="2026-07-20T10:05:00+05:30",
                                        underlying="NIFTY", expiry="2026-07-22",
                                        current_rows=chain()["chain"], source="test")
    assert result["tradable"] == 0
    assert all("warmup_no_previous_snapshot" in item["reason"] for item in result["signals"])
    conn.close()


def test_zero_candidate_cap_disables_tradable_flow_signals():
    before = chain()["chain"]
    after = copy.deepcopy(before)
    for ot in ("CE", "PE"):
        after[0][ot].update(lastPrice=180, openInterest=80000, totalTradedVolume=20000)
    signals = build_multistrike_signals(underlying="NIFTY", current_rows=after,
                                       previous_rows=before, min_score=0, max_score=101,
                                       max_tradable_per_snapshot=0)
    assert signals and not any(item.tradable for item in signals)


def test_condor_uses_next_configured_wing_when_first_hedge_is_unusable(monkeypatch):
    data = _synthetic_chain(steps=20)
    short = core._select_delta_strike(data["chain"], 22000, 50, .16, "CE", 5 / 365)
    bad_wing = next(row for row in data["chain"] if row["strikePrice"] == short["strike"] + 100)
    bad_wing["CE"]["bidprice"] = 0
    decisions = []
    monkeypatch.setattr(core.odj, "record_option_decision", lambda **kw: decisions.append(kw) or kw)
    with sqlite3.connect(":memory:") as conn:
        result = core.evaluate_c3_iron_condor(symbol="NIFTY", option_data=data,
                                             regime=_CALM_REGIME, conn=conn,
                                             gap_risk_manager=_FakeGapRiskManager(60))
    assert result["status"] == "selected"
    assert decisions[0]["selected"]["wing_width"] == 150


def test_pre_event_long_strangle_prices_both_legs_at_ask(monkeypatch):
    monkeypatch.setattr(core, "_todays_high_impact_events", lambda: [{"impact": "HIGH"}])
    data = _synthetic_chain()
    decisions = []
    monkeypatch.setattr(core.odj, "record_option_decision", lambda **kw: decisions.append(kw) or kw)
    with sqlite3.connect(":memory:") as conn:
        result = core.evaluate_d1_pre_event_strangle(symbol="NIFTY", option_data=data,
                                                    regime=_CALM_REGIME, conn=conn,
                                                    gap_risk_manager=_FakeGapRiskManager(20))
    assert result["status"] == "selected"
    for leg in decisions[0]["selected"]["legs"]:
        quote = core._leg_quote(data["chain"], leg["strike"], leg["option_type"])
        assert leg["price"] == quote["ask"]
    assert result["net_debit"] == round(sum(leg["price"] for leg in decisions[0]["selected"]["legs"]), 2)


def test_regime_refreshes_event_and_time_overlays_with_cached_classification(monkeypatch):
    monkeypatch.setattr(regime_module, "_CACHE", {})
    calls = []
    monkeypatch.setattr("market_regime.classify_regime", lambda *a, **kw: calls.append(1) or ("MEAN_REVERT", .8, "range"))
    monkeypatch.setattr("expiry_regime.get_expiry_regime", lambda **kw: {"days_to_expiry": 5})
    monkeypatch.setattr("time_regime.get_time_zone", lambda now: SimpleNamespace(value=str(now.hour)))
    event = {"has_event": False}
    monkeypatch.setattr("greeks_live.get_event_playbook", lambda symbol: dict(event))
    df = pd.DataFrame({"open": [100] * 40, "high": [101] * 40,
                       "low": [99] * 40, "close": [100] * 40})
    first = regime_module.detect_regime(df, vix=15, now=datetime(2026, 10, 14, 10))
    event["has_event"] = True
    second = regime_module.detect_regime(df, vix=15, now=datetime(2026, 10, 14, 11))
    assert len(calls) == 1
    assert not first["event"] and second["event"]
    assert first["time_zone"] == "10" and second["time_zone"] == "11"


def test_live_catalog_adapter_does_not_manufacture_quote_freshness(monkeypatch):
    import config
    from live_signal_engine import LiveSignalEngine
    monkeypatch.setattr(config, "ENABLE_OPTION_STRATEGY_CATALOG", True)
    monkeypatch.setattr(regime_module, "detect_regime", lambda *a, **kw: dict(_CALM_REGIME))
    received = []
    monkeypatch.setattr(registry, "evaluate_catalog", lambda **kw: received.append(kw) or [])
    raw = {"records": {"data": chain()["chain"]}}
    result = SimpleNamespace(raw_json=raw, spot=22000, expiry="2099-01-01")
    engine = SimpleNamespace(_gap_risk_manager=None, _catalog_error_logged=set())
    LiveSignalEngine._run_option_strategy_catalog_shadow(
        engine, "NIFTY", None, None, {}, option_result=result)
    assert len(received) == 1
    assert quality.chain_age_sec(received[0]["option_data"]) is None
    assert "ts" not in raw
