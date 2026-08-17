"""Unit tests for drift_monitor.py's core split/stat logic, using synthetic
data so they run without touching signal_log.db (mirrors this session's
verification pattern: also run once against real data by hand, see
SYSTEM_INFRASTRUCTURE_AUDIT.md / the drift_monitor.py module docstring)."""
import numpy as np
import pandas as pd

from drift_monitor import (
    _split_reference_recent, _drift_block, MIN_SAMPLES, cusum_check, CUSUM_MIN_REFERENCE,
)


def _make_df(n_days=40, per_day=5, ret_fn=lambda day: 0.0):
    """A synthetic signal_log-shaped frame: `n_days` distinct signal_date
    values, `per_day` rows each, `ret` drawn from N(ret_fn(day), 1)."""
    rng = np.random.default_rng(42)
    rows = []
    for day_idx in range(n_days):
        date_str = f"2026-01-{day_idx + 1:02d}" if day_idx < 31 else f"2026-02-{day_idx - 30:02d}"
        mean = ret_fn(day_idx)
        for _ in range(per_day):
            rows.append({"signal_date": date_str, "ret": rng.normal(mean, 1.0)})
    return pd.DataFrame(rows)


def test_split_reference_recent_splits_by_distinct_day():
    df = _make_df(n_days=40, per_day=5)
    ref, recent = _split_reference_recent(df, recent_days=10)
    assert ref["signal_date"].nunique() == 30
    assert recent["signal_date"].nunique() == 10
    assert len(ref) == 30 * 5
    assert len(recent) == 10 * 5


def test_split_reference_recent_insufficient_history_returns_all_in_recent():
    df = _make_df(n_days=10, per_day=5)
    ref, recent = _split_reference_recent(df, recent_days=30)
    assert len(ref) == 0
    assert len(recent) == len(df)


def test_drift_block_flags_clear_degradation():
    # reference period: mean +0.3%/day; recent period: mean -0.8%/day -- a
    # sharp, sustained worsening, same shape as sma20_atm_option's real
    # full-history-vs-holdout finding this session.
    df = _make_df(n_days=40, per_day=6, ret_fn=lambda d: 0.3 if d < 30 else -0.8)
    ref, recent = _split_reference_recent(df, recent_days=10)
    block = _drift_block(ref["ret"].values, recent["ret"].values, alpha_corrected=0.05)
    assert block["n_reference"] >= MIN_SAMPLES
    assert block["n_recent"] >= MIN_SAMPLES
    assert block["verdict"] == "DRIFT_SUSPECTED"
    assert block["recent_mean"] < block["reference_mean"]


def test_drift_block_stable_when_no_real_shift():
    df = _make_df(n_days=40, per_day=6, ret_fn=lambda d: 0.1)   # same mean throughout
    ref, recent = _split_reference_recent(df, recent_days=10)
    block = _drift_block(ref["ret"].values, recent["ret"].values, alpha_corrected=0.05)
    assert block["verdict"] == "STABLE"


def test_drift_block_insufficient_data_below_min_samples():
    df = _make_df(n_days=40, per_day=1)   # 30 ref rows OR fewer than MIN_SAMPLES per side
    ref, recent = _split_reference_recent(df, recent_days=10)
    block = _drift_block(ref["ret"].values, recent["ret"].values, alpha_corrected=0.05)
    assert block["verdict"] == "INSUFFICIENT_DATA"


def test_drift_block_improvement_is_not_flagged_as_drift():
    # recent period IMPROVES sharply -- out of scope per the module docstring,
    # must not be flagged as DRIFT_SUSPECTED (that verdict is reserved for
    # degradation only).
    df = _make_df(n_days=40, per_day=6, ret_fn=lambda d: -0.5 if d < 30 else 0.9)
    ref, recent = _split_reference_recent(df, recent_days=10)
    block = _drift_block(ref["ret"].values, recent["ret"].values, alpha_corrected=0.05)
    assert block["verdict"] != "DRIFT_SUSPECTED"


def test_cusum_flags_sustained_downward_shift():
    rng = np.random.default_rng(7)
    # stable reference, then a sustained (not one-off) downward shift
    ref = rng.normal(0.5, 1.0, CUSUM_MIN_REFERENCE)
    shifted = rng.normal(-1.5, 1.0, 40)
    series = np.concatenate([ref, shifted])
    result = cusum_check(series)
    assert result["verdict"] == "DRIFT_SUSPECTED"
    assert result["flagged_at_index"] is not None
    assert result["flagged_at_index"] >= CUSUM_MIN_REFERENCE


def test_cusum_stable_when_no_real_shift():
    rng = np.random.default_rng(8)
    series = rng.normal(0.3, 1.0, CUSUM_MIN_REFERENCE + 40)
    result = cusum_check(series)
    assert result["verdict"] == "STABLE"
    assert result["flagged_at_index"] is None


def test_cusum_ignores_a_single_moderate_outlier_not_sustained():
    # A single bad-but-not-extreme trade (~4 std below reference mean --
    # unusual, but not so extreme it alone crosses a 5-std boundary) amid an
    # otherwise stable stream should not, by itself, read as a SUSTAINED
    # shift. (A sufficiently extreme single point legitimately CAN trigger
    # CUSUM alone -- that's correct, not a bug -- so this test deliberately
    # keeps the outlier moderate rather than trying to prove CUSUM is
    # outlier-proof, which it isn't and isn't supposed to be.)
    rng = np.random.default_rng(9)
    ref = rng.normal(0.5, 1.0, CUSUM_MIN_REFERENCE)
    mostly_stable = rng.normal(0.5, 1.0, 30).tolist()
    mostly_stable[5] = -4.0
    series = np.concatenate([ref, mostly_stable])
    result = cusum_check(series)
    assert result["verdict"] == "STABLE"


def test_cusum_insufficient_data_below_min_reference():
    series = np.array([0.1, 0.2, 0.3])   # far fewer than CUSUM_MIN_REFERENCE
    result = cusum_check(series)
    assert result["verdict"] == "INSUFFICIENT_DATA"


def test_cusum_needs_far_fewer_samples_than_calendar_check():
    # CUSUM can produce a real (non-INSUFFICIENT_DATA) verdict well below
    # what _drift_block needs (2*MIN_SAMPLES=60) -- that's the entire point.
    rng = np.random.default_rng(10)
    series = rng.normal(0.2, 1.0, CUSUM_MIN_REFERENCE + 5)
    result = cusum_check(series)
    assert result["verdict"] in ("STABLE", "DRIFT_SUSPECTED")
    assert len(series) < 2 * MIN_SAMPLES


def test_cusum_positive_control_false_alarm_rate_bounded_across_lengths():
    """Positive control (same methodology as pipeline_sensitivity_floor.py):
    under a TRUE NULL (pure noise, no real drift), the false-alarm rate must
    stay roughly bounded as the stream gets longer, not climb toward
    certainty. This is the exact bug found and fixed against real
    signal_log data this session: an unscaled h=5*sigma boundary gave a
    6.5% false-alarm rate at n_evaluated=30 but 75.5% at n_evaluated=3500,
    because a long enough random walk will eventually cross any fixed finite
    boundary. The sqrt(n)-scaled boundary must keep every tested length
    under a generous ceiling (real-world data won't be perfectly i.i.d.
    Gaussian like this synthetic control, so this isn't asserting a tight
    5% -- just that nothing runs away toward ~75% the way the bug did)."""
    rng = np.random.default_rng(123)
    n_trials = 100
    for n_evaluated in (30, 200, 1000, 3000):
        false_alarms = sum(
            1 for _ in range(n_trials)
            if cusum_check(rng.normal(0.0, 1.0, CUSUM_MIN_REFERENCE + n_evaluated))["verdict"] == "DRIFT_SUSPECTED"
        )
        rate = false_alarms / n_trials
        assert rate < 0.20, f"n_evaluated={n_evaluated}: false-alarm rate {rate:.0%} too high"


def main() -> int:
    tests = [
        test_split_reference_recent_splits_by_distinct_day,
        test_split_reference_recent_insufficient_history_returns_all_in_recent,
        test_drift_block_flags_clear_degradation,
        test_drift_block_stable_when_no_real_shift,
        test_drift_block_insufficient_data_below_min_samples,
        test_drift_block_improvement_is_not_flagged_as_drift,
        test_cusum_flags_sustained_downward_shift,
        test_cusum_stable_when_no_real_shift,
        test_cusum_ignores_a_single_moderate_outlier_not_sustained,
        test_cusum_insufficient_data_below_min_reference,
        test_cusum_needs_far_fewer_samples_than_calendar_check,
        test_cusum_positive_control_false_alarm_rate_bounded_across_lengths,
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
    print(f"OK drift_monitor: {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
