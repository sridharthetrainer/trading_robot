import option_strategy_risk_checks as orc


def test_mtm_stop_level_default_pct():
    assert orc.mtm_stop_level(100_000.0) == 1500.0  # 1.5% default


def test_mtm_stop_level_explicit_pct():
    assert orc.mtm_stop_level(200_000.0, pct=0.02) == 4000.0


def test_risk_profile_for_known_strategies():
    assert orc.risk_profile_for("C1") == "undefined_risk"
    assert orc.risk_profile_for("C3") == "defined_risk"
    assert orc.risk_profile_for("D5") == "not_implemented"


def test_risk_profile_for_unknown_id():
    assert orc.risk_profile_for("Z99") == "unknown"


def test_correlation_group_nifty_banknifty_share_group():
    assert orc.correlation_group_for("NIFTY") == orc.correlation_group_for("BANKNIFTY")


def test_correlation_group_finnifty_is_separate():
    assert orc.correlation_group_for("FINNIFTY") != orc.correlation_group_for("NIFTY")


def test_gap_and_adx_filter_boundary_gap_exactly_at_threshold_not_blocked():
    blocked, _ = orc.gap_and_adx_filter(gap_pct=0.006, adx=10.0)
    assert blocked is False  # strictly greater-than, not >=


def test_gap_and_adx_filter_blocks_when_both_conditions_true():
    blocked, reason = orc.gap_and_adx_filter(gap_pct=0.008, adx=15.0)
    assert blocked is True
    assert "gap_" in reason


def test_gap_and_adx_filter_does_not_block_gap_alone_if_adx_high():
    blocked, _ = orc.gap_and_adx_filter(gap_pct=0.02, adx=30.0)
    assert blocked is False


def test_can_enter_trade_blocks_at_vix_25():
    ok, reason = orc.can_enter_trade(regime={"gap_pct": 0.0}, vix=26.0)
    assert ok is False
    assert "block_all" in reason


def test_can_enter_trade_boundary_vix_exactly_25_not_blocked():
    ok, _ = orc.can_enter_trade(regime={"gap_pct": 0.0}, vix=25.0, adx=25.0)
    assert ok is True  # strictly greater-than


def test_can_enter_trade_blocks_nondirectional_at_vix_30():
    ok, reason = orc.can_enter_trade(regime={"gap_pct": 0.0}, vix=31.0)
    assert ok is False
    assert "block_nondirectional" in reason


def test_can_enter_trade_blocks_on_high_latency():
    ok, reason = orc.can_enter_trade(regime={"gap_pct": 0.0}, vix=15.0, api_latency_sec=3.0)
    assert ok is False
    assert "latency" in reason


def test_can_enter_trade_blocks_second_undefined_risk_strategy():
    ok, reason = orc.can_enter_trade(
        regime={"gap_pct": 0.0}, vix=15.0, open_undefined_risk_count=1)
    assert ok is False
    assert "undefined_risk" in reason


def test_can_enter_trade_allows_calm_conditions():
    ok, reason = orc.can_enter_trade(
        regime={"gap_pct": 0.001}, vix=13.0, api_latency_sec=0.3,
        open_undefined_risk_count=0, adx=22.0)
    assert ok is True
    assert reason == ""
