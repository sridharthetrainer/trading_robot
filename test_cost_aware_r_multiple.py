"""Spec for the cost-aware (net-of-cost) R-multiple used to judge signal
worthiness from triple-barrier shadow labels."""

from triple_barrier import cost_aware_r_multiple, r_multiple_for_outcome


def test_net_r_is_below_gross_for_a_winner():
    gross = r_multiple_for_outcome(100.0, 102.0, "BUY", 99.0)   # +2R gross
    net = cost_aware_r_multiple(100.0, 102.0, "BUY", 99.0)
    assert gross == 2.0
    assert net < gross          # costs + slippage always reduce R
    assert net > 0              # a clean 2R winner still nets positive


def test_costs_can_flip_a_marginal_gross_positive_to_net_negative():
    # tiny favourable move, gross slightly positive; heavy slippage eats it
    gross = r_multiple_for_outcome(100.0, 100.1, "BUY", 99.0)
    net = cost_aware_r_multiple(100.0, 100.1, "BUY", 99.0, slippage_pct=0.002)
    assert gross > 0
    assert net < 0


def test_short_side_symmetry():
    # SELL winner: price falls from 100 to 98, stop above at 101 (risk=1)
    net = cost_aware_r_multiple(100.0, 98.0, "SELL", 101.0)
    assert net > 0
    assert net < 2.0


def test_invalid_inputs_return_zero():
    assert cost_aware_r_multiple(100.0, 102.0, "BUY", 100.0) == 0.0   # risk=0
    assert cost_aware_r_multiple(0.0, 102.0, "BUY", 99.0) == 0.0      # no entry
    assert cost_aware_r_multiple(100.0, 0.0, "BUY", 99.0) == 0.0      # no outcome
