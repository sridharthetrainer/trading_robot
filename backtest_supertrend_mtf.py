"""
backtest_supertrend_mtf.py

Backtest for the Multi-Timeframe Supertrend strategy.
Requires both 5-min and 15-min Supertrend to agree on direction.

Usage:  python backtest_supertrend_mtf.py [--symbol NIFTY] [--days 30]
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
import pandas as pd

logging.basicConfig(level=logging.WARNING)

DEFAULT_SYMBOL  = "NIFTY"
DEFAULT_DAYS    = 30
DEFAULT_CAPITAL = 100_000.0
DEFAULT_LOT     = 75
DEFAULT_LOTS    = 1
DEFAULT_BROK    = 40.0
DEFAULT_STT     = 0.0005
ST_PERIOD       = 10
ST_MULT         = 3.0


def fetch_data(symbol: str, days: int) -> Optional[pd.DataFrame]:
    try:
        from data_fetcher import DataFetcher
        df5  = _get_angel_data_fetcher().get_market_data(symbol, interval="5m",  days=days)
        df15 = _get_angel_data_fetcher().get_market_data(symbol, interval="15m", days=days)
        return df5, df15
    except Exception:
        pass
    try:
        import yf_compat as yf  # yfinance replaced: Yahoo API broken
        sym = "^NSEI" if symbol == "NIFTY" else "^NSEBANK" if symbol == "BANKNIFTY" else f"{symbol}.NS"
        df5  = yf.download(sym, period=f"{days}d", interval="5m",  progress=False, auto_adjust=True)
        df15 = yf.download(sym, period=f"{days}d", interval="15m", progress=False, auto_adjust=True)
        return df5, df15
    except Exception:
        return None, None


def backtest_supertrend_mtf(
    symbol: str, df5: pd.DataFrame, df15: pd.DataFrame,
    st_period: int = ST_PERIOD, st_mult: float = ST_MULT,
    initial_capital: float = DEFAULT_CAPITAL,
    lot_size: int = DEFAULT_LOT, lots: int = DEFAULT_LOTS,
    brokerage: float = DEFAULT_BROK, stt_rate: float = DEFAULT_STT,
    verbose: bool = True,
) -> Dict[str, Any]:
    if df5 is None or df15 is None or len(df5) < 30:
        return _empty(symbol, "no_data")

    from indicators import calculate_supertrend, calculate_atr

    # Compute Supertrend on both timeframes
    _, dir5  = calculate_supertrend(df5,  period=st_period, multiplier=st_mult)
    _, dir15 = calculate_supertrend(df15, period=st_period, multiplier=st_mult)
    atr5     = calculate_atr(df5, 14)

    # Resample 15m directions to 5m index for alignment
    if isinstance(df5.index, pd.DatetimeIndex) and isinstance(df15.index, pd.DatetimeIndex):
        dir15_5m = dir15.reindex(df5.index, method="ffill")
    else:
        dir15_5m = pd.Series(0, index=df5.index)

    capital  = float(initial_capital)
    qty      = lots * lot_size
    equity   = [capital]
    trades: List[Dict] = []
    position = None
    prev_dir5 = 0

    for i in range(max(st_period + 5, 20), len(df5)):
        close   = float(df5.iloc[i].get("Close", df5.iloc[i].get("close", 0)) or 0)
        d5      = int(dir5.iloc[i]    if pd.notna(dir5.iloc[i])    else 0)
        d15     = int(dir15_5m.iloc[i] if pd.notna(dir15_5m.iloc[i]) else 0)
        atr_v   = float(atr5.iloc[i]  if pd.notna(atr5.iloc[i])   else close * 0.005)
        ts      = df5.index[i]
        eod     = (hasattr(ts, 'time') and ts.time() >= __import__('datetime').time(15, 10))

        # Position management
        if position:
            stop = position["stop"]
            side = position["side"]
            exit_now = eod
            if side == "BUY"  and (close <= stop or d5 == -1): exit_now = True
            if side == "SELL" and (close >= stop or d5 ==  1): exit_now = True
            if exit_now:
                pnl = ((close - position["entry"]) if side=="BUY" else
                       (position["entry"] - close)) * qty - brokerage - close * qty * stt_rate
                capital += pnl
                trades.append({**position, "exit": close, "pnl": pnl})
                position = None
            else:
                # Update trailing stop
                if side == "BUY":
                    position["stop"] = max(position["stop"], close - 2 * atr_v)
                else:
                    position["stop"] = min(position["stop"], close + 2 * atr_v)
            prev_dir5 = d5
            equity.append(capital); continue

        # Entry: both TFs agree and 5m just flipped
        just_flipped = (prev_dir5 != d5)
        if d5 != 0 and d5 == d15 and just_flipped:
            if d5 == 1:
                stop     = close - 2 * atr_v
                position = {"side": "BUY",  "entry": close, "stop": stop}
            else:
                stop     = close + 2 * atr_v
                position = {"side": "SELL", "entry": close, "stop": stop}
            capital -= brokerage

        prev_dir5 = d5
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
        print(f"\n{'='*55}\nSupertrend MTF Backtest — {symbol}\n{'='*55}")
        print(f"Net Profit  : ₹{total_pnl:>10,.2f}")
        print(f"Total Trades: {n}")
        print(f"Win Rate    : {wr:.2%}")
        print(f"Sharpe Ratio: {sharpe:.4f}")
        print(f"Max Drawdown: ₹{dd:>10,.2f}")

    return {"symbol": symbol, "total_pnl": round(total_pnl,2), "num_trades": n,
            "win_rate": round(wr,4), "sharpe": round(sharpe,4),
            "max_drawdown": round(dd,2), "final_capital": round(capital,2)}


def _empty(symbol, reason):
    return {"symbol": symbol, "total_pnl": 0, "num_trades": 0,
            "win_rate": 0, "sharpe": 0, "max_drawdown": 0, "reason": reason}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--days",   type=int, default=DEFAULT_DAYS)
    args = parser.parse_args()
    df5, df15 = fetch_data(args.symbol, args.days)
    if df5 is None: print("No data"); raise SystemExit(1)  # safe in threads
    backtest_supertrend_mtf(args.symbol, df5, df15)
