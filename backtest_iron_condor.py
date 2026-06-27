"""
backtest_iron_condor.py

DEPRECATED synthetic backtest for Iron Condor and Bull Put Spread strategies.

This file is intentionally blocked because it invents option credit and loss
severity from underlying prices. Use condor_backtest_real.py instead; it uses
the backfilled real NIFTY option premia in options_nifty.db.

Key insight: Iron Condors profit when the underlying stays within
a range. We model: was NIFTY within ±200pts of entry level at expiry?

Usage:  python condor_backtest_real.py --grid
"""
from __future__ import annotations

import argparse, logging
from typing import Any, Dict, List, Optional

import pandas as pd

logging.basicConfig(level=logging.WARNING)

DEFAULT_SYMBOL   = "NIFTY"
DEFAULT_DAYS     = 120    # need more data for weekly condors
DEFAULT_CAPITAL  = 500_000.0
DEFAULT_LOT      = 65
DEFAULT_LOTS     = 1
DEFAULT_BROK     = 80.0   # 4 legs × ₹20
DEFAULT_STT      = 0.0005
IC_WIDTH         = 200    # ±200 points from ATM
IC_CREDIT_PCT    = 0.004  # ~0.4% of spot as credit (approx ₹90 for NIFTY@22000)
IC_ADJUST_BREACH = 0.5    # if short strike breached, assume 50% loss
ENTRY_DAY        = 0      # Monday (weekday 0)
EXIT_DTE         = 1      # exit when 1 day to expiry (Thursday)


def _blocked() -> None:
    raise RuntimeError(
        "backtest_iron_condor.py is deprecated because it uses synthetic "
        "credits/losses. Run condor_backtest_real.py for real-premia results."
    )


def fetch_daily(symbol: str, days: int) -> Optional[pd.DataFrame]:
    try:
        import yf_compat as yf  # yfinance replaced: Yahoo API broken
        sym = "^NSEI" if symbol == "NIFTY" else "^NSEBANK" if symbol == "BANKNIFTY" else f"{symbol}.NS"
        df = yf.download(sym, period=f"{days}d", interval="1d",
                         progress=False, auto_adjust=True, threads=False)
        return df
    except Exception:
        return None


def backtest_iron_condor(
    symbol: str, data: pd.DataFrame,
    width: int    = IC_WIDTH,
    lots:  int    = DEFAULT_LOTS,
    lot_size: int = DEFAULT_LOT,
    brokerage: float = DEFAULT_BROK,
    stt_rate:  float = DEFAULT_STT,
    initial_capital: float = DEFAULT_CAPITAL,
    verbose: bool = True,
) -> Dict[str, Any]:
    _blocked()
    if data is None or len(data) < 20:
        return _empty(symbol, "no_data")

    capital = float(initial_capital)
    qty     = lots * lot_size
    trades: List[Dict] = []
    equity  = [capital]

    close_col = "Close" if "Close" in data.columns else "close"
    prices    = data[close_col].values
    dates     = data.index

    i = 0
    while i < len(data) - 5:
        entry_date   = dates[i]
        entry_price  = float(prices[i])
        if entry_price <= 0:
            i += 1; continue

        # Approximate credit collected: IC_CREDIT_PCT of spot
        gross_credit_per_unit = entry_price * IC_CREDIT_PCT
        gross_credit = gross_credit_per_unit * qty

        # Short strikes
        call_short = entry_price + width
        put_short  = entry_price - width

        # Find exit (5 trading days later = approximate weekly expiry)
        exit_idx = min(i + 5, len(data) - 1)
        exit_price_u = float(prices[exit_idx])

        # Did underlying breach either short strike?
        window = prices[i:exit_idx+1]
        breached_call = any(p >= call_short for p in window)
        breached_put  = any(p <= put_short  for p in window)
        max_loss_per_unit = width - gross_credit_per_unit
        max_loss = max_loss_per_unit * qty

        if breached_call or breached_put:
            # Assume partial loss: 50% of max loss if breached (can adjust)
            pnl = -max_loss * IC_ADJUST_BREACH
        else:
            # Full credit retained minus costs
            pnl = gross_credit

        costs = brokerage + exit_price_u * qty * stt_rate
        net_pnl = pnl - costs
        capital += net_pnl
        trades.append({
            "entry_date":    str(entry_date.date() if hasattr(entry_date, 'date') else entry_date),
            "entry_price":   round(entry_price, 2),
            "call_short":    round(call_short, 2),
            "put_short":     round(put_short, 2),
            "breached":      breached_call or breached_put,
            "gross_credit":  round(gross_credit, 2),
            "pnl":           round(net_pnl, 2),
        })
        equity.append(capital)
        i += 5  # next weekly cycle

    n         = len(trades)
    wins      = sum(1 for t in trades if t["pnl"] > 0)
    total_pnl = sum(t["pnl"] for t in trades)
    wr        = wins / n if n else 0.0
    eq        = pd.Series(equity)
    dd        = float((eq.cummax() - eq).max())
    ret_s     = eq.pct_change().dropna()
    sharpe    = float(ret_s.mean()/ret_s.std()*(52)**0.5) if len(ret_s)>1 and ret_s.std()>0 else 0.0

    if verbose:
        print(f"\n{'='*60}\nIron Condor Backtest — {symbol}  ±{width}pt width\n{'='*60}")
        print(f"Net Profit   : ₹{total_pnl:>12,.2f}")
        print(f"Total Trades : {n}  (weekly)")
        print(f"Win Rate     : {wr:.2%}")
        print(f"Sharpe Ratio : {sharpe:.4f}")
        print(f"Max Drawdown : ₹{dd:>12,.2f}")
        print(f"Final Capital: ₹{capital:>12,.2f}")
        print(f"Breach Rate  : {sum(1 for t in trades if t['breached'])/n:.1%}" if n else "")

    return {"symbol": symbol, "total_pnl": round(total_pnl,2),
            "num_trades": n, "win_rate": round(wr,4),
            "sharpe": round(sharpe,4), "max_drawdown": round(dd,2),
            "final_capital": round(capital,2),
            "breach_rate": sum(1 for t in trades if t.get("breached",False))/n if n else 0}


def backtest_bull_put_spread(
    symbol: str, data: pd.DataFrame,
    width: int = 100, lots: int = DEFAULT_LOTS,
    lot_size: int = DEFAULT_LOT, brokerage: float = 40.0,
    initial_capital: float = DEFAULT_CAPITAL, verbose: bool = True,
) -> Dict[str, Any]:
    """
    Bull Put Spread backtest. Same synthetic approach as Iron Condor.
    Only the put side — profits when price stays above put_short.
    """
    _blocked()
    if data is None or len(data) < 10:
        return _empty(symbol, "no_data")

    capital  = float(initial_capital)
    qty      = lots * lot_size
    trades: List[Dict] = []
    equity   = [capital]
    close_col = "Close" if "Close" in data.columns else "close"
    prices    = data[close_col].values

    i = 0
    while i < len(data) - 5:
        spot         = float(prices[i])
        put_short    = spot
        put_long     = spot - width
        credit_unit  = spot * 0.0025   # ~₹55 credit per unit for 100pt spread
        credit_total = credit_unit * qty
        max_loss_u   = width - credit_unit
        max_loss     = max_loss_u * qty

        window   = prices[i:min(i+5, len(data))]
        breached = any(p <= put_short for p in window)
        pnl      = (-max_loss * 0.5 if breached else credit_total) - brokerage

        capital += pnl
        trades.append({"pnl": pnl, "breached": breached})
        equity.append(capital)
        i += 5

    n         = len(trades)
    wins      = sum(1 for t in trades if t["pnl"] > 0)
    total_pnl = sum(t["pnl"] for t in trades)
    wr        = wins / n if n else 0.0
    eq        = pd.Series(equity)
    dd        = float((eq.cummax() - eq).max())
    ret_s     = eq.pct_change().dropna()
    sharpe    = float(ret_s.mean()/ret_s.std()*(52)**0.5) if len(ret_s)>1 and ret_s.std()>0 else 0.0

    if verbose:
        print(f"\n{'='*55}\nBull Put Spread Backtest — {symbol}  {width}pt\n{'='*55}")
        print(f"Net Profit  : ₹{total_pnl:>10,.2f}")
        print(f"Total Trades: {n}")
        print(f"Win Rate    : {wr:.2%}")
        print(f"Sharpe Ratio: {sharpe:.4f}")
        print(f"Max Drawdown: ₹{dd:>10,.2f}")

    return {"symbol": symbol, "total_pnl": round(total_pnl,2),
            "num_trades": n, "win_rate": round(wr,4),
            "sharpe": round(sharpe,4), "max_drawdown": round(dd,2)}


def _empty(symbol, reason):
    return {"symbol": symbol, "total_pnl": 0, "num_trades": 0,
            "win_rate": 0, "sharpe": 0, "max_drawdown": 0, "reason": reason}


if __name__ == "__main__":
    print(
        "backtest_iron_condor.py is deprecated and blocked. "
        "Run: python condor_backtest_real.py --grid"
    )
    raise SystemExit(2)

    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--days",   type=int, default=DEFAULT_DAYS)
    parser.add_argument("--width",  type=int, default=IC_WIDTH)
    parser.add_argument("--strategy", choices=["ic","bps"], default="ic")
    args = parser.parse_args()

    data = fetch_daily(args.symbol, args.days)
    if data is None:
        print("No data — cannot run backtest")
        raise SystemExit(1)  # converted from sys.exit — safe in threads

    if args.strategy == "ic":
        backtest_iron_condor(args.symbol, data, width=args.width)
    else:
        backtest_bull_put_spread(args.symbol, data, width=args.width)
