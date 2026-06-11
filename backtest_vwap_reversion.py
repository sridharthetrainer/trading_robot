"""
backtest_vwap_reversion.py

Backtest for the VWAP Deviation Reversion strategy.
Buys when price deviates significantly below VWAP with oversold RSI,
sells when price deviates significantly above VWAP with overbought RSI.

Usage:  python backtest_vwap_reversion.py [--symbol NIFTY] [--days 30]
"""
from __future__ import annotations

# Auto-fix: get DataFetcher with Angel singleton
def _get_angel_data_fetcher():
    try:
        from angel import AngelOne
        import os as _os_adf
        _ang = AngelOne(api_key=_os_adf.getenv("API_KEY",""),
            client_id=_os_adf.getenv("CLIENT_ID",""),
            password=_os_adf.getenv("PASSWORD",""),
            totp_secret=_os_adf.getenv("TOTP_SECRET",""))
    except Exception: _ang = None
    from data_fetcher import DataFetcher
    return DataFetcher(angel=_ang, paper_trade=False)

import argparse, logging, sys
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd

logging.basicConfig(level=logging.WARNING, format="%(asctime)s | %(levelname)s | %(message)s")

DEFAULT_SYMBOL   = "NIFTY"
DEFAULT_DAYS     = 30
DEFAULT_CAPITAL  = 100_000.0
DEFAULT_LOT      = 65
DEFAULT_LOTS     = 1
DEFAULT_BROK     = 40.0
DEFAULT_STT      = 0.0005
DEFAULT_SLIP     = 0.05
DEFAULT_DEV_MIN  = 0.003
DEFAULT_RSI_OS   = 38
DEFAULT_RSI_OB   = 62
DEFAULT_VOL_MIN  = 0.80


def fetch_data(symbol: str, days: int = 30) -> Optional[pd.DataFrame]:
    try:
        from data_fetcher import DataFetcher
        df = _get_angel_data_fetcher().get_market_data(symbol, interval="5m", days=days)
        if df is not None and not df.empty:
            return df
    except Exception:
        pass
    try:
        import yf_compat as yf  # yfinance replaced: Yahoo API broken
        sym = "^NSEI" if symbol == "NIFTY" else "^NSEBANK" if symbol == "BANKNIFTY" else f"{symbol}.NS"
        return yf.download(sym, period=f"{days}d", interval="5m",
                           progress=False, auto_adjust=True, threads=False)
    except Exception:
        return None


def backtest_vwap_reversion(
    symbol: str, data: pd.DataFrame,
    dev_min: float = DEFAULT_DEV_MIN, rsi_os: float = DEFAULT_RSI_OS,
    rsi_ob: float = DEFAULT_RSI_OB, vol_min: float = DEFAULT_VOL_MIN,
    initial_capital: float = DEFAULT_CAPITAL, lot_size: int = DEFAULT_LOT,
    lots: int = DEFAULT_LOTS, brokerage: float = DEFAULT_BROK,
    stt_rate: float = DEFAULT_STT, slippage_pct: float = DEFAULT_SLIP,
    interval_minutes: int = 5, close_at_end: bool = True,
    verbose: bool = True,
    **_: Any,
) -> Dict[str, Any]:
    if data is None or len(data) < 30:
        return _empty(symbol, "no_data")

    from indicators import calculate_rsi, calculate_atr, calculate_vwap_bands, calculate_volume_ratio
    rsi_s    = calculate_rsi(data, 14)
    atr_s    = calculate_atr(data, 14)
    vwap_lower_s, vwap_s, vwap_upper_s = calculate_vwap_bands(data, period=20, std_mult=1.5)
    vol_r_s  = calculate_volume_ratio(data, 20)

    capital  = float(initial_capital)
    qty      = lots * lot_size
    equity   = [capital]
    trades: List[Dict] = []
    position = None

    for i in range(30, len(data)):
        row      = data.iloc[i]
        close    = float(row.get("Close", row.get("close", 0)) or 0)
        vwap_v   = float(vwap_s.iloc[i] if pd.notna(vwap_s.iloc[i]) else 0)
        vwap_l   = float(vwap_lower_s.iloc[i] if pd.notna(vwap_lower_s.iloc[i]) else vwap_v * (1 - dev_min))
        vwap_u   = float(vwap_upper_s.iloc[i] if pd.notna(vwap_upper_s.iloc[i]) else vwap_v * (1 + dev_min))
        rsi_v    = float(rsi_s.iloc[i]  if pd.notna(rsi_s.iloc[i])  else 50)
        atr_v    = float(atr_s.iloc[i]  if pd.notna(atr_s.iloc[i])  else close * 0.005)
        vol_r    = float(vol_r_s.iloc[i] if pd.notna(vol_r_s.iloc[i]) else 1.0)

        if vwap_v <= 0 or close <= 0:
            equity.append(capital); continue

        dev_pct = (close - vwap_v) / vwap_v

        # Manage open position
        if position:
            target = vwap_v   # natural target = VWAP
            stop   = position["stop"]
            side   = position["side"]
            eod    = (hasattr(data.index[i], 'time') and
                      data.index[i].time() >= __import__('datetime').time(15, 10))

            exit_now = eod
            if side == "BUY"  and (close >= target or close <= stop): exit_now = True
            if side == "SELL" and (close <= target or close >= stop): exit_now = True

            if exit_now:
                exit_p = close * (1 - slippage_pct/100 if side=="BUY" else 1 + slippage_pct/100)
                gross  = (exit_p - position["entry"]) * qty if side=="BUY" else (position["entry"] - exit_p) * qty
                costs  = brokerage + exit_p * qty * stt_rate
                pnl    = gross - costs
                capital += pnl
                trades.append({**position, "exit": exit_p, "pnl": pnl})
                position = None
            equity.append(capital); continue

        # Entry
        if vol_r < vol_min: equity.append(capital); continue

        below_band = close <= vwap_l or dev_pct <= -dev_min
        above_band = close >= vwap_u or dev_pct >= dev_min

        if below_band and rsi_v <= rsi_os:
            entry  = close * (1 + slippage_pct / 100)
            stop   = entry - 2 * atr_v
            position = {"side": "BUY", "entry": entry, "stop": stop}
            capital -= brokerage
        elif above_band and rsi_v >= rsi_ob:
            entry  = close * (1 - slippage_pct / 100)
            stop   = entry + 2 * atr_v
            position = {"side": "SELL", "entry": entry, "stop": stop}
            capital -= brokerage

        equity.append(capital)

    if close_at_end and position and len(data):
        last = data.iloc[-1]
        close = float(last.get("Close", last.get("close", 0)) or 0)
        if close > 0:
            side = position["side"]
            exit_p = close * (1 - slippage_pct/100 if side=="BUY" else 1 + slippage_pct/100)
            gross = (exit_p - position["entry"]) * qty if side=="BUY" else (position["entry"] - exit_p) * qty
            costs = brokerage + exit_p * qty * stt_rate
            pnl = gross - costs
            capital += pnl
            trades.append({**position, "exit": exit_p, "pnl": pnl})
            equity.append(capital)

    n        = len(trades)
    wins     = sum(1 for t in trades if t["pnl"] > 0)
    total_pnl= sum(t["pnl"] for t in trades)
    wr       = wins / n if n else 0.0
    eq       = pd.Series(equity)
    dd       = float((eq.cummax() - eq).max())
    ret_s    = eq.pct_change().dropna()
    sharpe   = float(ret_s.mean()/ret_s.std()*(252*75)**0.5) if len(ret_s)>1 and ret_s.std()>0 else 0.0

    if verbose:
        print(f"\n{'='*55}\nVWAP Reversion Backtest — {symbol}\n{'='*55}")
        print(f"Net Profit  : ₹{total_pnl:>10,.2f}")
        print(f"Total Trades: {n}")
        print(f"Win Rate    : {wr:.2%}")
        print(f"Sharpe Ratio: {sharpe:.4f}")
        print(f"Max Drawdown: ₹{dd:>10,.2f}")

    return {"symbol": symbol, "total_pnl": round(total_pnl,2), "num_trades": n,
            "win_rate": round(wr,4), "sharpe": round(sharpe,4),
            "max_drawdown": round(dd,2), "final_capital": round(capital,2)}


def _empty(symbol, reason):
    return {"symbol": symbol, "total_pnl": 0, "num_trades": 0, "win_rate": 0,
            "sharpe": 0, "max_drawdown": 0, "final_capital": 0, "reason": reason}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    args = parser.parse_args()
    data = fetch_data(args.symbol, args.days)
    if data is None: print("No data"); raise SystemExit(1)  # safe in threads
    backtest_vwap_reversion(args.symbol, data)
