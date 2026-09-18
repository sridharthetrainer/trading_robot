"""
test_supertrend_mtf_poisoned_input.py — poisoned-input verification for the
2026-09-18 fix to backtest_supertrend_mtf.py's HTF resampling (was leaking
~10-15 min of future data via pandas' default left-labeled resample; fixed
by reusing causal_htf.resample_to_htf's close-time labeling).

Same method and pass criteria as test_causal_htf_poisoned_input.py: run the
fixed backtest twice on identical data -- once real, once with everything at
or after a cutoff poisoned to NaN. Every trade entered before the cutoff
must be byte-identical between the two runs. Zero tolerance.
"""
from __future__ import annotations

import sqlite3
import sys

import numpy as np
import pandas as pd

from backtest_supertrend_mtf import backtest_supertrend_mtf


def load_nifty_5m() -> pd.DataFrame:
    con = sqlite3.connect("candle_cache.db")
    df = pd.read_sql_query(
        "SELECT timestamp, open, high, low, close, volume FROM candles "
        "WHERE symbol='NIFTY' AND interval='5m' ORDER BY timestamp",
        con,
    )
    con.close()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.set_index("timestamp")
    return df.rename(columns={"open": "Open", "high": "High", "low": "Low",
                               "close": "Close", "volume": "Volume"})


def poison_after(df: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    poisoned = df.copy()
    mask = poisoned.index >= cutoff
    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col in poisoned.columns:
            poisoned.loc[mask, col] = np.nan
    return poisoned


def main() -> int:
    df = load_nifty_5m()
    cutoff = df.index[int(len(df) * 0.7)]
    print(f"Data span: {df.index[0]} .. {df.index[-1]}  ({len(df)} bars)")
    print(f"Poison cutoff: {cutoff}")

    real_result = backtest_supertrend_mtf("NIFTY", df5=df, verbose=False)
    poisoned_result = backtest_supertrend_mtf("NIFTY", df5=poison_after(df, cutoff), verbose=False)

    real_before = [t for t in real_result["trades"] if t["entry_ts"] < cutoff]
    poisoned_before = [t for t in poisoned_result["trades"] if t["entry_ts"] < cutoff]

    print(f"Trades with entry before cutoff: real={len(real_before)} poisoned={len(poisoned_before)}")

    if len(real_before) == 0:
        print("FAIL: zero pre-cutoff trades in the real run -- test isn't exercising anything")
        return 1

    if real_before == poisoned_before:
        print(f"PASS: all {len(real_before)} pre-cutoff trades are byte-identical "
              f"between the real and poisoned runs. No lookahead leakage detected.")
        return 0
    else:
        print("FAIL: pre-cutoff trades differ between real and poisoned runs -- "
              "the resample fix did not fully eliminate lookahead leakage.")
        for i, (a, b) in enumerate(zip(real_before, poisoned_before)):
            if a != b:
                print(f"  first divergence at trade #{i}: real={a}  poisoned={b}")
                break
        if len(real_before) != len(poisoned_before):
            print(f"  trade COUNT differs: real={len(real_before)} poisoned={len(poisoned_before)}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
