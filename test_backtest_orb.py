#!/usr/bin/env python3
from __future__ import annotations

import pandas as pd

from backtest_orb import _get_orb


def test_get_orb_handles_tz_aware_index() -> None:
    """Regression: pandas 3.0 raises TypeError comparing a tz-aware
    DatetimeIndex against a tz-naive Timestamp instead of the older
    silent/lenient behaviour. _get_orb builds its 9:15-9:30 window bounds
    with a naive pd.Timestamp; against tz-aware cached candle data (the
    normal case -- candle_cache stores +05:30-localized timestamps) every
    call used to raise, get swallowed upstream, and silently produce zero
    trades for every window (found 2026-08-05 debugging validation_harness
    reporting 0 dev windows for the orb strategy)."""
    idx = pd.date_range(
        "2026-06-18 09:15:00", periods=6, freq="5min", tz="Asia/Kolkata"
    )
    day_df = pd.DataFrame(
        {
            "Open":  [100, 101, 102, 103, 104, 105],
            "High":  [101, 102, 103, 104, 105, 106],
            "Low":   [99,  100, 101, 102, 103, 104],
            "Close": [100.5, 101.5, 102.5, 103.5, 104.5, 105.5],
        },
        index=idx,
    )
    result = _get_orb(day_df)
    assert result is not None
    high, low = result
    assert high == 104.0   # max High within 09:15-09:30 inclusive (first 4 bars)
    assert low == 99.0     # min Low within 09:15-09:30 inclusive


def test_get_orb_handles_naive_index() -> None:
    """Naive index (no tz) must keep working exactly as before."""
    idx = pd.date_range("2026-06-18 09:15:00", periods=6, freq="5min")
    day_df = pd.DataFrame(
        {
            "Open":  [100, 101, 102, 103, 104, 105],
            "High":  [101, 102, 103, 104, 105, 106],
            "Low":   [99,  100, 101, 102, 103, 104],
            "Close": [100.5, 101.5, 102.5, 103.5, 104.5, 105.5],
        },
        index=idx,
    )
    result = _get_orb(day_df)
    assert result == (104.0, 99.0)


def main() -> int:
    try:
        test_get_orb_handles_tz_aware_index()
        test_get_orb_handles_naive_index()
    except Exception as exc:
        print(f"FAIL backtest_orb: {exc}")
        return 1
    print("OK backtest_orb")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
