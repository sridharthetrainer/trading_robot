"""
rerun_trend_control_causal.py — PREREG_BREAKOUT_HTF_ALIGNMENT.md step 3.

Re-run the trend control under the EXACT causal_htf.py definition the built
gate implements (not the old exploratory signal_log.htf_bias field). Also
runs breakout for direct comparison on the identical data window.

Void condition (per the pre-registration): if trend shows a benefit here,
the whole HTF-alignment candidate is VOID, not weakened -- it would mean
the effect isn't breakout-specific after all.
"""
from __future__ import annotations

import statistics

import pandas as pd

import sqlite3
from backtest_trend import backtest_trend
from backtest_breakout import backtest_breakout
from causal_htf import resample_to_htf, add_htf_emas, htf_bias_asof


def classify_and_compare(strategy_name: str, backtest_fn, df: pd.DataFrame) -> None:
    result = backtest_fn(strategy_name, df, verbose=False)
    trades = result.get("trades", [])
    if not trades:
        print(f"{strategy_name}: no trades produced, nothing to compare")
        return

    mask = df[["Open", "High", "Low", "Close"]].apply(
        pd.to_numeric, errors="coerce").notna().all(axis=1)
    entry_timestamps = df.index[mask.values]

    htf_ready = add_htf_emas(resample_to_htf(df, "15min"))

    aligned_pnls = []
    unaligned_pnls = []
    for t in trades:
        idx = t.get("entry_idx")
        if idx is None or idx >= len(entry_timestamps):
            continue
        entry_ts = entry_timestamps[idx]
        bias = htf_bias_asof(htf_ready, entry_ts)
        side = t.get("side", "")
        aligned = (side == "BUY" and bias == "BULLISH") or (side == "SELL" and bias == "BEARISH")
        (aligned_pnls if aligned else unaligned_pnls).append(float(t["pnl"]))

    n_a, n_u = len(aligned_pnls), len(unaligned_pnls)
    avg_a = statistics.mean(aligned_pnls) if aligned_pnls else float("nan")
    avg_u = statistics.mean(unaligned_pnls) if unaligned_pnls else float("nan")
    print(f"{strategy_name:10} total_trades={len(trades):4}  "
          f"aligned: n={n_a:3} avg_pnl={avg_a:+.2f}  |  "
          f"unaligned: n={n_u:3} avg_pnl={avg_u:+.2f}")
    if n_a and n_u:
        diff = avg_a - avg_u
        print(f"{'':10} aligned - unaligned = {diff:+.2f}  "
              f"({'aligned better' if diff > 0 else 'unaligned better or no benefit'})")


def load_from_cache(symbol: str = "NIFTY", interval: str = "5m") -> pd.DataFrame:
    con = sqlite3.connect("candle_cache.db")
    df = pd.read_sql_query(
        "SELECT timestamp, open, high, low, close, volume FROM candles "
        "WHERE symbol=? AND interval=? ORDER BY timestamp",
        con, params=(symbol, interval),
    )
    con.close()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.set_index("timestamp")
    df = df.rename(columns={"open": "Open", "high": "High", "low": "Low",
                             "close": "Close", "volume": "Volume"})
    return df


def main() -> None:
    df = load_from_cache("NIFTY", "5m")
    print(f"Data span: {df.index[0]} .. {df.index[-1]}  ({len(df)} bars)\n")

    print("--- trend (CONTROL -- void condition if this shows a benefit) ---")
    classify_and_compare("trend", backtest_trend, df)
    print()
    print("--- breakout (the actual candidate) ---")
    classify_and_compare("breakout", backtest_breakout, df)


if __name__ == "__main__":
    main()
