"""Unit tests for minimum_detectable_edge.py's classification logic, using
synthetic trade lists so they run without hitting the option pricer/DB."""
import numpy as np

from minimum_detectable_edge import classify, _per_trade_stats, MIN_N_FOR_ANY_VERDICT


def _synthetic_trades(n, gross_mean, gross_std, cost_per_trade, seed=1):
    rng = np.random.default_rng(seed)
    gross = rng.normal(gross_mean, gross_std, n)
    return [{"gross_pnl": float(g), "pnl": float(g - cost_per_trade)} for g in gross]


def test_below_min_n_is_insufficient_power_regardless_of_result():
    trades = _synthetic_trades(n=10, gross_mean=100000, gross_std=100, cost_per_trade=10)
    stats = _per_trade_stats(trades)
    result = classify(stats)
    assert result["verdict"] == "INSUFFICIENT_POWER"
    assert "minimum" in result["reason"]


def test_large_clean_negative_signal_is_no_edge():
    # large n, big consistent negative mean, small relative std -> clears MDE, is NO_EDGE
    trades = _synthetic_trades(n=200, gross_mean=-500, gross_std=200, cost_per_trade=20)
    stats = _per_trade_stats(trades)
    result = classify(stats)
    assert result["verdict"] == "NO_EDGE"


def test_large_clean_positive_signal_survives_costs_is_edge_detected():
    trades = _synthetic_trades(n=200, gross_mean=1000, gross_std=200, cost_per_trade=20)
    stats = _per_trade_stats(trades)
    result = classify(stats)
    assert result["verdict"] == "EDGE_DETECTED"


def test_positive_gross_but_costs_dominate_is_cost_eroded():
    # gross clearly positive and low-variance (clears MDE easily), but cost is
    # large enough to wipe out the net mean.
    trades = _synthetic_trades(n=200, gross_mean=50, gross_std=10, cost_per_trade=55)
    stats = _per_trade_stats(trades)
    result = classify(stats)
    assert result["verdict"] == "COST_ERODED"


def test_noisy_small_positive_signal_is_insufficient_power():
    # n above the floor, but return is small relative to its own noise ->
    # can't be distinguished from zero, must not be called NO_EDGE or EDGE_DETECTED.
    trades = _synthetic_trades(n=40, gross_mean=50, gross_std=2000, cost_per_trade=10)
    stats = _per_trade_stats(trades)
    result = classify(stats)
    assert result["verdict"] == "INSUFFICIENT_POWER"


def main() -> int:
    tests = [
        test_below_min_n_is_insufficient_power_regardless_of_result,
        test_large_clean_negative_signal_is_no_edge,
        test_large_clean_positive_signal_survives_costs_is_edge_detected,
        test_positive_gross_but_costs_dominate_is_cost_eroded,
        test_noisy_small_positive_signal_is_insufficient_power,
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
    print(f"OK minimum_detectable_edge: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
