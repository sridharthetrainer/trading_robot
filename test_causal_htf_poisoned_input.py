"""
test_causal_htf_poisoned_input.py — the poisoned-input assertion required by
PREREG_BREAKOUT_HTF_ALIGNMENT.md step 2, BEFORE trusting any HTF-alignment
result. Not a one-time manual check: run this after any future change to
causal_htf.py or backtest_breakout.py's HTF wiring, and it must stay green.

Method (per the pre-registration's fixed-in-advance pass criteria):
Run the require_htf_alignment=True backtest_breakout twice on identical
underlying data -- once with the real df_htf, once with every HTF-source bar
at or after a cutoff timestamp poisoned to NaN. Pass condition: every trade
whose entry_ts falls strictly before the cutoff is byte-identical between the
two runs (same side, same entry price, same exit, same pnl). Zero tolerance.
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

import upstox_data
from backtest_breakout import backtest_breakout


def poison_after(df: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    poisoned = df.copy()
    mask = poisoned.index >= cutoff
    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col in poisoned.columns:
            poisoned.loc[mask, col] = np.nan
    return poisoned


def main() -> int:
    df = upstox_data.get_candles("NIFTY", interval="5m", days=25)
    if df is None or len(df) < 200:
        print("FAIL: not enough real data fetched to run this test meaningfully")
        return 1
    df = df.rename(columns={"open": "Open", "high": "High", "low": "Low",
                             "close": "Close", "volume": "Volume"})

    # Cutoff at roughly the 70% mark -- leaves a real, sizeable "before" region
    # to check trades against, and a real "after" region to poison.
    cutoff = df.index[int(len(df) * 0.7)]
    print(f"Data span: {df.index[0]} .. {df.index[-1]}  ({len(df)} bars)")
    print(f"Poison cutoff: {cutoff}")

    real_result = backtest_breakout(
        "NIFTY", df, verbose=False,
        require_htf_alignment=True, df_htf=df,
    )
    poisoned_df = poison_after(df, cutoff)
    poisoned_result = backtest_breakout(
        "NIFTY", df, verbose=False,
        require_htf_alignment=True, df_htf=poisoned_df,
    )

    real_trades = real_result.get("trades", [])
    poisoned_trades = poisoned_result.get("trades", [])

    def entry_ts_of(trade: dict):
        # trades store entry_idx; map back via the same bars used above.
        return df.index[trade["entry_idx"]] if "entry_idx" in trade else None

    real_before = [t for t in real_trades if entry_ts_of(t) is not None and entry_ts_of(t) < cutoff]
    poisoned_before = [t for t in poisoned_trades if entry_ts_of(t) is not None and entry_ts_of(t) < cutoff]

    print(f"Trades with entry before cutoff: real={len(real_before)} poisoned={len(poisoned_before)}")

    if len(real_before) == 0:
        print("FAIL: zero pre-cutoff trades in the real run -- test isn't exercising anything, "
              "widen the data window or move the cutoff before trusting a PASS/FAIL either way")
        return 1

    if real_before == poisoned_before:
        print(f"PASS: all {len(real_before)} pre-cutoff trades are byte-identical "
              f"between the real and poisoned runs. No lookahead leakage detected.")
        return 0
    else:
        print("FAIL: pre-cutoff trades differ between real and poisoned runs -- "
              "causality is violated somewhere in the HTF slicing. Do not trust "
              "any HTF-alignment result until this is fixed.")
        for i, (a, b) in enumerate(zip(real_before, poisoned_before)):
            if a != b:
                print(f"  first divergence at trade #{i}: real={a}  poisoned={b}")
                break
        if len(real_before) != len(poisoned_before):
            print(f"  trade COUNT differs: real={len(real_before)} poisoned={len(poisoned_before)}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
