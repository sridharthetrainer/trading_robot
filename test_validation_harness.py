import pandas as pd

import validation_harness as vh


def _make_df(n=100, index_name="timestamp"):
    idx = pd.date_range("2020-01-01 09:15:00", periods=n, freq="5min")
    idx.name = index_name
    return pd.DataFrame({
        "Open": range(n), "High": range(n), "Low": range(n), "Close": range(n),
        "Volume": [0] * n,
    }, index=idx)


def test_split_holdout_preserves_datetime_index_regardless_of_index_name():
    """Regression for a 2026-07-30 finding: split_holdout used to only
    restore a DatetimeIndex when its name was literally 'date' or the
    pandas-default 'index' -- any other name (e.g. 'timestamp', as produced
    by external_data_loader/run_extended_validation) silently left dev/holdout
    with a plain RangeIndex, breaking every datetime-aware backtest_fn
    (e.g. backtest_supertrend_mtf's 5m/15m alignment) into zero trades."""
    df = _make_df(index_name="timestamp")
    dev, holdout = vh.split_holdout(df, holdout_ratio=0.2)
    assert isinstance(dev.index, pd.DatetimeIndex)
    assert isinstance(holdout.index, pd.DatetimeIndex)
    assert "timestamp" not in dev.columns  # never demoted to a plain column


def test_split_holdout_preserves_datetime_index_named_date():
    df = _make_df(index_name="date")
    dev, holdout = vh.split_holdout(df, holdout_ratio=0.2)
    assert isinstance(dev.index, pd.DatetimeIndex)
    assert isinstance(holdout.index, pd.DatetimeIndex)


def test_split_holdout_preserves_unnamed_datetime_index():
    df = _make_df(index_name=None)
    dev, holdout = vh.split_holdout(df, holdout_ratio=0.2)
    assert isinstance(dev.index, pd.DatetimeIndex)
    assert isinstance(holdout.index, pd.DatetimeIndex)


def test_split_holdout_ratio_and_ordering():
    df = _make_df(n=100)
    dev, holdout = vh.split_holdout(df, holdout_ratio=0.2)
    assert len(dev) == 80
    assert len(holdout) == 20
    assert dev.index.max() < holdout.index.min()


def test_history_gap_guard_allows_weekend_but_rejects_missing_months():
    weekend = pd.date_range("2020-01-03 15:25", periods=2, freq="3D")
    discontinuous = pd.DatetimeIndex([
        pd.Timestamp("2020-01-03 15:25"),
        pd.Timestamp("2021-05-03 09:15"),
    ])
    assert vh._has_disqualifying_history_gap(weekend) is False
    assert vh._has_disqualifying_history_gap(discontinuous) is True


def test_supertrend_mtf_produces_trades_with_preserved_datetime_index():
    """End-to-end: with the datetime index correctly preserved through
    split_holdout, a datetime-aware strategy can actually generate trades
    (was silently zero before the fix, on any non-'date'/'index'-named
    timestamp column)."""
    import numpy as np
    import backtest_supertrend_mtf as bst

    n = 3000
    idx = pd.date_range("2020-01-01 09:15:00", periods=n, freq="5min")
    idx.name = "timestamp"
    rng = np.random.RandomState(7)
    price = 20000 + np.cumsum(rng.normal(0, 8, n))
    df = pd.DataFrame({
        "Open": price, "High": price + 5, "Low": price - 5, "Close": price,
        "Volume": 0,
    }, index=idx)

    dev, _holdout = vh.split_holdout(df, holdout_ratio=0.2)
    result = bst.backtest_supertrend_mtf(symbol="NIFTY", data=dev, verbose=False, interval_minutes=5)
    assert result["num_trades"] > 0
