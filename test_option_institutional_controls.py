import pytest

from option_institutional_controls import (
    OptionQuote, aggregate_greeks, clustered_evidence, combo_fillable,
    portfolio_risk_gate, retail_algo_readiness, simulate_limit_fill,
    smart_limit_schedule, volatility_edge,
)
from option_strategy_contract import (
    OptionLegContract, OptionStrategyContract, assert_phase_parity,
)


def test_contract_is_stable_and_phase_drift_fails_closed():
    contract = OptionStrategyContract(
        strategy_id="T1", version=1, underlying="NIFTY",
        entry_rule="09:30 and edge", exit_rule="target_or_stop",
        max_holding_minutes=30, max_loss_rupees=500,
        legs=(OptionLegContract("CE", "BUY", 1, "weekly", "delta:0.40"),),
        hypothesis_id="H-001", research_cutoff="2026-07-28",
    )
    assert contract.validate() == ()
    assert len(contract.contract_hash) == 64
    assert_phase_parity({"backtest": contract.contract_hash, "paper": contract.contract_hash})
    with pytest.raises(ValueError):
        assert_phase_parity({"backtest": contract.contract_hash, "paper": "different"})


def test_quote_reality_model_partial_fill_and_combo_skew():
    quote = OptionQuote(ts=101, bid=99, ask=100, bid_qty=10, ask_qty=20)
    fill = simulate_limit_fill(
        side="BUY", quantity=50, limit_price=100, submitted_at=100,
        quotes=[quote], latency_sec=0.5,
    )
    assert fill.filled and fill.quantity == 20 and fill.reason == "partial_fill"
    other = simulate_limit_fill(
        side="SELL", quantity=10, limit_price=99, submitted_at=100,
        quotes=[OptionQuote(ts=104, bid=99, ask=100, bid_qty=10, ask_qty=10)],
    )
    assert combo_fillable([fill, other], max_timestamp_skew_sec=1)[0] is False


def test_smart_limit_never_crosses_full_spread():
    schedule = smart_limit_schedule(side="BUY", bid=99, ask=101, attempts=3)
    assert schedule[0] == 100
    assert schedule[-1] < 101


def test_portfolio_risk_uses_signed_greeks_and_scenarios():
    positions = [
        {"side": "BUY", "quantity": 50, "spot": 24000, "delta": 0.5,
         "gamma": 0.0001, "theta": -2, "vega": 3},
    ]
    assert aggregate_greeks(positions)["delta"] == 25
    result = portfolio_risk_gate(
        positions, max_abs_delta=20, max_abs_gamma=1, max_abs_theta=200,
        max_abs_vega=200, max_stress_loss=100000,
    )
    assert not result["allowed"] and "delta_limit" in result["breaches"]
    assert len(result["scenarios"]) == 8


def test_clustered_evidence_counts_snapshot_as_one_decision():
    rows = [
        {"underlying": "NIFTY", "snapshot_time": "2026-07-28T10:00:01", "direction": "BUY", "net_r": 1},
        {"underlying": "NIFTY", "snapshot_time": "2026-07-28T10:00:40", "direction": "BUY", "net_r": -1},
    ]
    result = clustered_evidence(rows)
    assert result["rows"] == 2
    assert result["independent_clusters"] == 1
    assert result["mean_net_r"] == 0


def test_volatility_edge_requires_cost_hurdle():
    assert volatility_edge(
        forecast_realized_vol=18, executable_implied_vol=17,
        spread_vol_points=0.5, hedging_cost_vol_points=0.4,
        uncertainty_vol_points=0.3,
    )["tradable"] is False
    assert volatility_edge(
        forecast_realized_vol=22, executable_implied_vol=17,
        spread_vol_points=0.5, hedging_cost_vol_points=0.4,
        uncertainty_vol_points=0.3,
    )["direction"] == "LONG_VOL"


def test_retail_algo_readiness_fails_closed_without_external_approvals():
    result = retail_algo_readiness(
        paper_trading=True, enable_real_trading=False, static_ips=[],
        broker_algo_registered=False, strategy_registered=False,
        order_tags_enabled=True, audit_chain_ok=True,
    )
    assert result["live_ready"] is False
    assert "static_ip" in result["blocks"]
