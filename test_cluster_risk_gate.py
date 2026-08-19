"""Tests for cluster_risk_gate.py -- the cluster-level capital-allocation
risk control from the 2026-08-19 spec audit (§4-6). Built against real
strategy classification (cluster_strategy_map.py, grounded in
strategy_clusters.factor_of()) and real measured correlation
(correlation_matrix.json via idle_engine.run_correlation_update, not a
hardcoded pair list for strategies this system doesn't have).
"""
import numpy as np
import pytest

from cluster_risk_gate import ClusterRiskGate


@pytest.fixture
def gate():
    return ClusterRiskGate()  # loads the real cluster_matrix.json


# ── resolve_regime_key ───────────────────────────────────────────────────

def test_regime_strong_trend_low_vol():
    key = ClusterRiskGate.resolve_regime_key(
        adx=30, price_above_50ema=True, bb_bandwidth_pct=10, india_vix=12)
    assert key == "strong_trend_low_vol"


def test_regime_weak_trend_high_vol():
    key = ClusterRiskGate.resolve_regime_key(
        adx=15, price_above_50ema=False, bb_bandwidth_pct=10, india_vix=25)
    assert key == "weak_trend_high_vol"


def test_regime_expiry_week_overrides_trend_signals():
    key = ClusterRiskGate.resolve_regime_key(
        adx=30, price_above_50ema=True, bb_bandwidth_pct=10, india_vix=12,
        days_to_expiry=3)
    assert key == "expiry_week"


def test_regime_market_crash_overrides_everything():
    key = ClusterRiskGate.resolve_regime_key(
        adx=30, price_above_50ema=True, bb_bandwidth_pct=10, india_vix=12,
        is_market_crash=True)
    assert key == "market_crash"


def test_regime_event_day_overrides_trend_signals():
    key = ClusterRiskGate.resolve_regime_key(
        adx=30, price_above_50ema=True, bb_bandwidth_pct=10, india_vix=12,
        is_event_day=True)
    assert key == "event_day"


# ── cluster membership / regime gating ──────────────────────────────────

def test_cluster_active_for_regime_passes(gate):
    # "orb" -> cluster A; A is active in weak_trend_low_vol
    allowed, size, reason = gate.can_enter(
        strategy_name="orb", symbol="RELIANCE", proposed_risk_pct=0.5,
        direction="BUY", regime_key="weak_trend_low_vol", open_positions=[])
    assert allowed is True
    assert size == 0.5


def test_cluster_disabled_for_regime_blocks(gate):
    # "orb" -> A; A is explicitly disabled in event_day
    allowed, size, reason = gate.can_enter(
        strategy_name="orb", symbol="RELIANCE", proposed_risk_pct=0.5,
        direction="BUY", regime_key="event_day", open_positions=[])
    assert allowed is False
    assert size == 0.0
    assert "CLUSTER" in reason


def test_cluster_not_in_active_list_blocks(gate):
    # "trend" -> E; E is neither active nor disabled in weak_trend_low_vol
    # (only listed as disabled) -- must still block since it's not active.
    allowed, size, reason = gate.can_enter(
        strategy_name="trend", symbol="RELIANCE", proposed_risk_pct=0.5,
        direction="BUY", regime_key="weak_trend_low_vol", open_positions=[])
    assert allowed is False


def test_unknown_regime_key_is_a_noop(gate):
    allowed, size, reason = gate.can_enter(
        strategy_name="orb", symbol="RELIANCE", proposed_risk_pct=0.5,
        direction="BUY", regime_key="not_a_real_regime", open_positions=[])
    assert allowed is True
    assert size == 0.5
    assert "unknown regime" in reason


# ── cross-cluster compatibility ─────────────────────────────────────────

def test_red_compatibility_blocks(gate):
    # cluster_matrix.json: B x D = red
    open_positions = [{"symbol": "TCS", "strategy": "gap_fill"}]  # gap_fill -> D
    allowed, size, reason = gate.can_enter(
        strategy_name="morning_momentum", symbol="RELIANCE", proposed_risk_pct=0.5,
        direction="BUY", regime_key="strong_trend_low_vol", open_positions=open_positions)
    assert allowed is False
    assert "CLUSTER_CONFLICT" in reason


def test_yellow_compatibility_halves_size(gate):
    # cluster_matrix.json: A x B = yellow. "orb" -> A, "morning_momentum" -> B
    open_positions = [{"symbol": "TCS", "strategy": "morning_momentum", "risk_pct": 0.3}]
    allowed, size, reason = gate.can_enter(
        strategy_name="orb", symbol="RELIANCE", proposed_risk_pct=0.5,
        direction="BUY", regime_key="weak_trend_low_vol", open_positions=open_positions)
    assert allowed is True
    assert size == pytest.approx(0.25)  # 0.5 * 0.5


def test_green_compatibility_no_reduction(gate):
    # A x C = green
    open_positions = [{"symbol": "TCS", "strategy": "gap_fill", "risk_pct": 0.3}]  # D, not C -- use a C strategy instead
    open_positions = [{"symbol": "TCS", "strategy": "stat_arb", "risk_pct": 0.3}]  # stat_arb -> G; A x G = green
    allowed, size, reason = gate.can_enter(
        strategy_name="orb", symbol="RELIANCE", proposed_risk_pct=0.5,
        direction="BUY", regime_key="weak_trend_low_vol", open_positions=open_positions)
    assert allowed is True
    assert size == 0.5


# ── intra-cluster sizing ─────────────────────────────────────────────────

def test_intra_cluster_cap_reduces_size_for_second_position(gate):
    # cluster A sizing: {"1": 0.5, "2": 0.3, "3": 0.2, "max_total": 0.6}.
    # Existing risk kept small (0.2) so this isolates the per-strategy tier
    # cap from the separate max_total check (covered by the next test).
    open_positions = [{"symbol": "TCS", "strategy": "orb", "risk_pct": 0.2}]
    allowed, size, reason = gate.can_enter(
        strategy_name="orb", symbol="RELIANCE", proposed_risk_pct=0.5,
        direction="BUY", regime_key="weak_trend_low_vol", open_positions=open_positions)
    assert allowed is True
    assert size == 0.3  # capped to the "2nd position" tier, not the requested 0.5


def test_intra_cluster_max_total_blocks(gate):
    # cluster A max_total = 0.6; two 0.5-risk orb positions already open = 1.0 > 0.6
    open_positions = [
        {"symbol": "TCS", "strategy": "orb", "risk_pct": 0.5},
        {"symbol": "INFY", "strategy": "orb", "risk_pct": 0.5},
    ]
    allowed, size, reason = gate.can_enter(
        strategy_name="orb", symbol="RELIANCE", proposed_risk_pct=0.2,
        direction="BUY", regime_key="weak_trend_low_vol", open_positions=open_positions)
    assert allowed is False
    assert "CLUSTER_RISK_CAP" in reason


# ── correlation downshift (real data, mocked) ───────────────────────────

def _mock_corr_matrix(monkeypatch, symbols, matrix):
    import portfolio_heat
    monkeypatch.setattr(portfolio_heat, "_load_correlation_matrix",
                         lambda: (symbols, np.array(matrix)))


def test_no_correlation_data_is_a_noop(gate, monkeypatch):
    import portfolio_heat
    monkeypatch.setattr(portfolio_heat, "_load_correlation_matrix", lambda: ([], np.array([])))
    open_positions = [{"symbol": "BANKNIFTY", "strategy": "stat_arb", "risk_pct": 0.3}]
    allowed, size, reason = gate.can_enter(
        strategy_name="orb", symbol="NIFTY", proposed_risk_pct=0.5,
        direction="BUY", regime_key="weak_trend_low_vol", open_positions=open_positions)
    assert allowed is True
    assert size == 0.5  # no correlation penalty applied


def test_moderate_correlation_075_reduces_size(gate, monkeypatch):
    _mock_corr_matrix(monkeypatch, ["NIFTY", "BANKNIFTY"], [[1.0, 0.75], [0.75, 1.0]])
    open_positions = [{"symbol": "BANKNIFTY", "strategy": "stat_arb", "risk_pct": 0.3}]
    allowed, size, reason = gate.can_enter(
        strategy_name="orb", symbol="NIFTY", proposed_risk_pct=0.4,
        direction="BUY", regime_key="weak_trend_low_vol", open_positions=open_positions)
    assert allowed is True
    assert size == pytest.approx(0.3)  # 0.4 * 0.75 tier


def test_high_correlation_085_reduces_more(gate, monkeypatch):
    _mock_corr_matrix(monkeypatch, ["NIFTY", "BANKNIFTY"], [[1.0, 0.85], [0.85, 1.0]])
    open_positions = [{"symbol": "BANKNIFTY", "strategy": "stat_arb", "risk_pct": 0.3}]
    allowed, size, reason = gate.can_enter(
        strategy_name="orb", symbol="NIFTY", proposed_risk_pct=0.4,
        direction="BUY", regime_key="weak_trend_low_vol", open_positions=open_positions)
    assert allowed is True
    assert size == pytest.approx(0.24)  # 0.4 * 0.60 tier


def test_extreme_correlation_090_blocks(gate, monkeypatch):
    _mock_corr_matrix(monkeypatch, ["NIFTY", "BANKNIFTY"], [[1.0, 0.95], [0.95, 1.0]])
    open_positions = [{"symbol": "BANKNIFTY", "strategy": "stat_arb", "risk_pct": 0.3}]
    allowed, size, reason = gate.can_enter(
        strategy_name="orb", symbol="NIFTY", proposed_risk_pct=0.4,
        direction="BUY", regime_key="weak_trend_low_vol", open_positions=open_positions)
    assert allowed is False
    assert "CORRELATION_TOO_HIGH" in reason


def test_below_070_correlation_no_penalty(gate, monkeypatch):
    _mock_corr_matrix(monkeypatch, ["NIFTY", "BANKNIFTY"], [[1.0, 0.5], [0.5, 1.0]])
    open_positions = [{"symbol": "BANKNIFTY", "strategy": "stat_arb", "risk_pct": 0.3}]
    allowed, size, reason = gate.can_enter(
        strategy_name="orb", symbol="NIFTY", proposed_risk_pct=0.4,
        direction="BUY", regime_key="weak_trend_low_vol", open_positions=open_positions)
    assert allowed is True
    assert size == 0.4


# ── directional caps ─────────────────────────────────────────────────────

def test_directional_cap_blocks_excess_long_exposure(gate):
    # 0915_1030 max_long = 3.0
    open_positions = [{"symbol": "TCS", "strategy": "stat_arb", "side": "BUY", "risk_pct": 2.8}]
    allowed, size, reason = gate.can_enter(
        strategy_name="orb", symbol="RELIANCE", proposed_risk_pct=0.5,
        direction="BUY", regime_key="weak_trend_low_vol",
        open_positions=open_positions, time_bucket="0915_1030")
    assert allowed is False
    assert "DIRECTIONAL_CAP" in reason


def test_directional_cap_allows_within_budget(gate):
    open_positions = [{"symbol": "TCS", "strategy": "stat_arb", "side": "BUY", "risk_pct": 1.0}]
    allowed, size, reason = gate.can_enter(
        strategy_name="orb", symbol="RELIANCE", proposed_risk_pct=0.5,
        direction="BUY", regime_key="weak_trend_low_vol",
        open_positions=open_positions, time_bucket="0915_1030")
    assert allowed is True


def test_no_time_bucket_skips_directional_check(gate):
    open_positions = [{"symbol": "TCS", "strategy": "stat_arb", "side": "BUY", "risk_pct": 10.0}]
    allowed, size, reason = gate.can_enter(
        strategy_name="orb", symbol="RELIANCE", proposed_risk_pct=0.5,
        direction="BUY", regime_key="weak_trend_low_vol", open_positions=open_positions)
    assert allowed is True


# ── missing matrix file is a safe no-op ─────────────────────────────────

def test_missing_matrix_file_disables_gate_safely():
    g = ClusterRiskGate(matrix_path="/nonexistent/path/does_not_exist.json")
    allowed, size, reason = g.can_enter(
        strategy_name="orb", symbol="NIFTY", proposed_risk_pct=0.5,
        direction="BUY", regime_key="weak_trend_low_vol", open_positions=[])
    assert allowed is True
    assert size == 0.5
    assert "not loaded" in reason
