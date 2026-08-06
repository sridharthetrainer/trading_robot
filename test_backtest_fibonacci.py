#!/usr/bin/env python3
from __future__ import annotations

import numpy as np
import pandas as pd

from backtest_fibonacci import backtest_fibonacci


def _make_swing_df(n=120, seed=7):
    """A synthetic rally-then-retracement series with a datetime index, so
    the strategy has a real swing to find and retrace into."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-06-01 09:15:00", periods=n, freq="5min")
    up = np.linspace(100, 140, n // 2) + rng.normal(0, 0.3, n // 2)
    down = np.linspace(140, 115, n - n // 2) + rng.normal(0, 0.3, n - n // 2)
    close = np.concatenate([up, down])
    df = pd.DataFrame({
        "Open": close, "High": close + 0.5, "Low": close - 0.5, "Close": close,
        "Volume": np.full(n, 1000.0),
    }, index=idx)
    return df


def test_backtest_fibonacci_runs_and_returns_expected_shape():
    df = _make_swing_df()
    result = backtest_fibonacci("TEST", df, verbose=False)
    assert set(result.keys()) >= {
        "symbol", "total_pnl", "num_trades", "win_rate", "sharpe",
        "max_drawdown", "final_capital",
    }
    assert result["symbol"] == "TEST"


def test_backtest_fibonacci_insufficient_data_returns_empty_result():
    df = _make_swing_df(n=10)
    result = backtest_fibonacci("TEST", df, verbose=False)
    assert result["num_trades"] == 0
    assert result.get("reason") == "no_data"


def test_backtest_fibonacci_accepts_lowercase_columns():
    df = _make_swing_df().rename(columns=str.lower)
    result = backtest_fibonacci("TEST", df, verbose=False)
    assert "num_trades" in result


def main() -> int:
    try:
        test_backtest_fibonacci_runs_and_returns_expected_shape()
        test_backtest_fibonacci_insufficient_data_returns_empty_result()
        test_backtest_fibonacci_accepts_lowercase_columns()
    except Exception as exc:
        print(f"FAIL backtest_fibonacci: {exc}")
        return 1
    print("OK backtest_fibonacci")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
