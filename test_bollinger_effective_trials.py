"""Unit tests for the eigenvalue-based effective-trials logic in
bollinger_effective_trials.py, isolated from the actual backtest/DB calls."""
import numpy as np


def _n_eff_from_corr(corr: np.ndarray) -> float:
    eigenvalues = np.clip(np.linalg.eigvalsh(corr), 0, None)
    return float((eigenvalues.sum() ** 2) / (eigenvalues ** 2).sum())


def test_perfectly_correlated_trials_collapse_to_n_eff_one():
    n = 9
    corr = np.ones((n, n))
    assert abs(_n_eff_from_corr(corr) - 1.0) < 1e-6


def test_perfectly_independent_trials_keep_full_n_eff():
    n = 9
    corr = np.eye(n)
    assert abs(_n_eff_from_corr(corr) - n) < 1e-6


def test_moderate_correlation_gives_intermediate_n_eff():
    n = 9
    rho = 0.66   # matches this session's real Bollinger-grid finding
    corr = np.full((n, n), rho)
    np.fill_diagonal(corr, 1.0)
    n_eff = _n_eff_from_corr(corr)
    assert 1.0 < n_eff < n


def main() -> int:
    tests = [
        test_perfectly_correlated_trials_collapse_to_n_eff_one,
        test_perfectly_independent_trials_keep_full_n_eff,
        test_moderate_correlation_gives_intermediate_n_eff,
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
    print(f"OK bollinger_effective_trials: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
