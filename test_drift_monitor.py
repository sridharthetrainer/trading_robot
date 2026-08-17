"""Unit tests for drift_monitor.py's core split/stat logic, using synthetic
data so they run without touching signal_log.db (mirrors this session's
verification pattern: also run once against real data by hand, see
SYSTEM_INFRASTRUCTURE_AUDIT.md / the drift_monitor.py module docstring)."""
import numpy as np
import pandas as pd

from drift_monitor import _split_reference_recent, _drift_block, MIN_SAMPLES


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


def main() -> int:
    tests = [
        test_split_reference_recent_splits_by_distinct_day,
        test_split_reference_recent_insufficient_history_returns_all_in_recent,
        test_drift_block_flags_clear_degradation,
        test_drift_block_stable_when_no_real_shift,
        test_drift_block_insufficient_data_below_min_samples,
        test_drift_block_improvement_is_not_flagged_as_drift,
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
