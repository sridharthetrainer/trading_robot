"""Tests for walk_forward_backtest.monte_carlo_with_ruin() -- the additive
trade-level Monte Carlo + ruin-probability extension. Does not touch or
re-test the existing monte_carlo_trade_sequence() (unchanged, still tested
implicitly via run_walk_forward's own logging/usage)."""
import numpy as np

from walk_forward_backtest import monte_carlo_with_ruin


def _synthetic_trades(n=200, win_rate=0.45, avg_win=1500.0, avg_loss=-1000.0, seed=7):
    rng = np.random.default_rng(seed)
    wins = rng.random(n) < win_rate
    pnls = np.where(wins,
                     rng.normal(avg_win, avg_win * 0.3, n),
                     rng.normal(avg_loss, abs(avg_loss) * 0.3, n))
    return pnls.tolist()


def test_too_few_trades_returns_error():
    result = monte_carlo_with_ruin([100.0, -50.0, 200.0], initial_capital=100_000)
    assert result["n_sim"] == 0
    assert "error" in result


def test_ruin_probability_monotonic_in_threshold():
    """A LOOSER (higher) drawdown threshold must never have a HIGHER ruin
    probability than a tighter one -- breaching 30% implies you also passed
    through 20% on the way, so P(ruin@20%) >= P(ruin@30%) always."""
    trades = _synthetic_trades()
    result = monte_carlo_with_ruin(trades, initial_capital=50_000,
                                    ruin_thresholds=(0.10, 0.20, 0.30, 0.50))
    probs = result["ruin_probability"]
    assert probs["0.1"] >= probs["0.2"] >= probs["0.3"] >= probs["0.5"]


def test_ruin_probability_bounded_zero_to_one():
    trades = _synthetic_trades()
    result = monte_carlo_with_ruin(trades, initial_capital=50_000)
    for p in result["ruin_probability"].values():
        assert 0.0 <= p <= 1.0


def test_smaller_capital_base_increases_ruin_probability():
    """Same trade sequence, smaller starting capital -> drawdown in % terms is
    larger -> ruin probability at a fixed % threshold must not decrease."""
    trades = _synthetic_trades()
    small_cap = monte_carlo_with_ruin(trades, initial_capital=20_000, ruin_thresholds=(0.3,))
    large_cap = monte_carlo_with_ruin(trades, initial_capital=500_000, ruin_thresholds=(0.3,))
    assert small_cap["ruin_probability"]["0.3"] >= large_cap["ruin_probability"]["0.3"]


def test_trade_level_and_window_level_inputs_both_produce_valid_shape():
    """The function is granularity-agnostic by design -- both a real trade-level
    list (n>=30, tagged 'trade') and a window-level aggregate list (n<30,
    tagged 'window') must return the same well-formed shape."""
    trade_level = monte_carlo_with_ruin(_synthetic_trades(n=200), initial_capital=100_000)
    window_level = monte_carlo_with_ruin([5000.0, -2000.0, 3000.0, -1500.0, 4200.0,
                                           -800.0, 2100.0, -3000.0, 1900.0, -500.0],
                                          initial_capital=100_000)
    for result, expected_granularity in ((trade_level, "trade"), (window_level, "window")):
        assert result["granularity"] == expected_granularity
        assert "ruin_probability" in result
        assert "median_max_dd_pct" in result
        assert "p95_max_dd_pct" in result
        assert result["p95_max_dd_pct"] >= result["median_max_dd_pct"]


def main() -> int:
    tests = [
        test_too_few_trades_returns_error,
        test_ruin_probability_monotonic_in_threshold,
        test_ruin_probability_bounded_zero_to_one,
        test_smaller_capital_base_increases_ruin_probability,
        test_trade_level_and_window_level_inputs_both_produce_valid_shape,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as exc:
            print(f"FAIL {t.__name__}: {exc}")
            failed += 1
    if failed:
        print(f"{failed} test(s) failed")
        return 1
    print(f"OK monte_carlo_with_ruin: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
