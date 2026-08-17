"""Unit tests for time_to_power.py's futility calculation."""
from time_to_power import compute, FUTILITY_YEARS


def test_tiny_edge_relative_to_noise_is_dead_on_arrival():
    # net_mean tiny vs net_std -> huge n_star -> long time_to_power
    stats = {"n": 50, "net_mean": 5.0, "net_std": 2000.0}
    r = compute(stats, holdout_days=63)
    assert r["verdict"] == "DEAD_ON_ARRIVAL"
    assert r["time_to_power_years"] > FUTILITY_YEARS


def test_edge_close_to_detectable_is_worth_waiting():
    # net_mean close to what a modest sample increase would resolve
    stats = {"n": 50, "net_mean": 1500.0, "net_std": 3000.0}
    r = compute(stats, holdout_days=63)
    assert r["verdict"] == "WORTH_WAITING"
    assert r["time_to_power_years"] <= FUTILITY_YEARS


def test_zero_std_or_zero_n_returns_none():
    assert compute({"n": 0, "net_mean": 100.0, "net_std": 50.0}, holdout_days=63) is None
    assert compute({"n": 50, "net_mean": 100.0, "net_std": 0.0}, holdout_days=63) is None


def test_higher_firing_rate_shortens_time_to_power():
    stats = {"n": 50, "net_mean": 800.0, "net_std": 2500.0}
    slow = compute(stats, holdout_days=126)   # same n over a longer span -> lower rate
    fast = compute(stats, holdout_days=63)
    assert fast["firing_rate_per_year"] > slow["firing_rate_per_year"]
    assert fast["time_to_power_years"] < slow["time_to_power_years"]


def main() -> int:
    tests = [
        test_tiny_edge_relative_to_noise_is_dead_on_arrival,
        test_edge_close_to_detectable_is_worth_waiting,
        test_zero_std_or_zero_n_returns_none,
        test_higher_firing_rate_shortens_time_to_power,
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
    print(f"OK time_to_power: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
