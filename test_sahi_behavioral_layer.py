from sahi_strategy import _context_gate, manage_position


def test_market_context_is_mandatory_for_entries():
    assert _context_gate(None, direction="bullish")["reason"] == "missing_market_context"
    assert _context_gate({"sector_strength": "strong"}, direction="bullish")["ok"] is False


def test_sector_direction_and_expiry_transition_are_gated():
    base = {"market_context_ready": True}
    assert _context_gate({**base, "sector_strength": "weak"}, direction="bullish")["reason"] == "weak_sector_for_long"
    assert _context_gate({**base, "sector_strength": "strong"}, direction="bearish")["reason"] == "strong_sector_for_short"
    assert _context_gate({**base, "expiry_transition": True}, direction="bullish")["reason"] == "expiry_transition_liquidity_risk"


def test_option_liquidity_requires_quote_oi_and_volume():
    context = {"market_context_ready": True, "sector_strength": "neutral"}
    liquid = {"bid": 99, "ask": 101, "oi": 1000, "volume": 200}
    assert _context_gate(context, direction="bullish", option=liquid)["ok"] is True
    wide = {**liquid, "bid": 70, "ask": 130}
    assert _context_gate(context, direction="bullish", option=wide)["ok"] is False
    assert _context_gate(context, direction="bullish", option={**liquid, "oi": 0})["reason"] == "option_participation_weak"


def test_position_manager_never_allows_averaging_down():
    result = manage_position(
        {"side": "LONG", "entry": 100, "stop": 95, "original_stop_loss": 95, "target": 110, "qty": 10},
        102,
    )
    assert result["position"]["average_down_allowed"] is False
    assert result["position"]["stop_loss"] >= 95
