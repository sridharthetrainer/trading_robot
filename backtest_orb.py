"""
backtest_orb.py

Backtest for the Opening Range Breakout (ORB) strategy.

Usage
-----
    python backtest_orb.py                     # default NIFTY 30 days
    python backtest_orb.py --symbol BANKNIFTY --days 60

Output
------
    Net Profit:    ₹ XXXX
    Total Trades:  XX
    Win Rate:      XX.X%
    Sharpe Ratio:  X.XX
    Max Drawdown:  ₹ XXXX
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


import argparse
import logging
from datetime import time as dtime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

# ── Default parameters ────────────────────────────────────────────────────────
DEFAULT_SYMBOL          = "NIFTY"
DEFAULT_DAYS            = 30
DEFAULT_INTERVAL        = "5m"
DEFAULT_CAPITAL         = 100_000.0
DEFAULT_LOT_SIZE        = 65
DEFAULT_LOTS            = 1
DEFAULT_BROKERAGE       = 40.0        # round-trip
DEFAULT_SLIPPAGE_PCT    = 0.05
DEFAULT_STT_RATE        = 0.0005

ORB_WINDOW_START        = dtime(9, 15)
ORB_WINDOW_END          = dtime(9, 30)
ORB_VALID_UNTIL         = dtime(10, 30)
DEFAULT_ADX_MIN         = 18.0
DEFAULT_VOLUME_MIN      = 1.3
# NSE index candles carry no real traded volume (indices aren't directly
# traded; only their derivatives are) -- confirmed 2026-08-05, cached NIFTY
# volume is 0.0 for 100% of bars. A volume-ratio filter can structurally
# never pass for these symbols. Exempt them rather than block orb entirely
# on data that will never exist; other confirmation (ADX) still applies.
INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"}
DEFAULT_STOP_RANGE_MULT = 1.0         # stop = opposite side of ORB range
DEFAULT_TARGET_MULT     = 1.5         # target = 1.5 × range width


def _safe(s: pd.Series, default: float = 0.0) -> float:
    try:
        v = s.iloc[-1]; return float(v) if pd.notna(v) else default
    except Exception: return default


def fetch_data(
    symbol: str,
    interval: str = DEFAULT_INTERVAL,
    days: int     = DEFAULT_DAYS,
) -> Optional[pd.DataFrame]:
    """Fetch OHLCV data via DataFetcher → yfinance fallback."""
    try:
        df = _get_angel_data_fetcher().get_market_data(symbol, interval=interval, days=days)
        if df is not None and not df.empty:
            logger.info("Data fetched: %s — %d bars", symbol, len(df))
            return df
    except Exception as exc:
        logger.warning("DataFetcher failed: %s", exc)
    try:
        import yf_compat as yf  # yfinance replaced: Yahoo API broken
        sym = f"^NSEI" if symbol == "NIFTY" else f"^NSEBANK" if symbol == "BANKNIFTY" else f"{symbol}.NS"
        df = yf.download(sym, period=f"{days}d", interval=interval,
                         progress=False, auto_adjust=True, threads=False)
        if df is not None and not df.empty:
            return df
    except Exception as exc:
        logger.warning("yfinance failed: %s", exc)
    return None


def _get_orb(day_df: pd.DataFrame) -> Optional[Tuple[float, float]]:
    """Return (high, low) of opening range bars, or None."""
    if not isinstance(day_df.index, pd.DatetimeIndex):
        return None
    day = day_df.index[0].date()
    start = pd.Timestamp(day).replace(hour=9, minute=15)
    end   = pd.Timestamp(day).replace(hour=9, minute=30)
    if day_df.index.tz is not None:
        start = start.tz_localize(day_df.index.tz)
        end   = end.tz_localize(day_df.index.tz)
    mask  = (day_df.index >= start) & (day_df.index <= end)
    orb   = day_df[mask]
    if len(orb) < 1:
        return None
    h = float(orb["High"].max())
    l = float(orb["Low"].min())
    return (h, l) if h > l else None


def backtest_orb(
    symbol:         str,
    data:           pd.DataFrame,
    adx_min:        float = DEFAULT_ADX_MIN,
    volume_min:     float = DEFAULT_VOLUME_MIN,
    stop_mult:      float = DEFAULT_STOP_RANGE_MULT,
    target_mult:    float = DEFAULT_TARGET_MULT,
    initial_capital:float = DEFAULT_CAPITAL,
    lot_size:       int   = DEFAULT_LOT_SIZE,
    lots:           int   = DEFAULT_LOTS,
    brokerage:      float = DEFAULT_BROKERAGE,
    stt_rate:       float = DEFAULT_STT_RATE,
    slippage_pct:   float = DEFAULT_SLIPPAGE_PCT,
    interval_minutes:int   = 5,
    close_at_end:   bool  = True,
    verbose:        bool  = True,
    **_: Any,
) -> Dict[str, Any]:
    """
    Walk forward through data bar by bar. On each new day, compute the ORB.
    When price breaks out with volume confirmation, enter a trade.
    """
    if data is None or len(data) < 20:
        return _empty_result(symbol, "insufficient_data")

    # Pre-compute ADX and volume ratio
    try:
        from indicators import calculate_adx, calculate_volume_ratio
        adx_series = calculate_adx(data, 14)
        vol_ratio  = calculate_volume_ratio(data, 20)
    except Exception:
        adx_series = pd.Series(25.0, index=data.index)
        vol_ratio  = pd.Series(1.0,  index=data.index)

    capital    = float(initial_capital)
    qty        = lots * lot_size
    equity     = [capital]
    trades: List[Dict] = []
    position   = None
    is_index   = str(symbol).upper() in INDEX_SYMBOLS

    if not isinstance(data.index, pd.DatetimeIndex):
        return _empty_result(symbol, "no_datetime_index")

    work = data.copy()

    # Group by date
    work["_date"] = work.index.date
    for day, day_df in work.groupby("_date"):
        orb = _get_orb(day_df)
        if orb is None:
            continue
        orb_high, orb_low = orb
        range_w = orb_high - orb_low

        for i, (ts, row) in enumerate(day_df.iterrows()):
            bar_t = ts.time()
            close = float(row.get("Close", row.get("close", 0)) or 0)
            vol_r = float(vol_ratio.get(ts, 1.0) or 1.0)
            adx_v = float(adx_series.get(ts, 0.0) or 0.0)

            # ── Position management ───────────────────────────────────────
            if position:
                if position["side"] == "BUY":
                    if close <= position["stop"] or close >= position["target"] \
                            or bar_t >= dtime(15, 15):
                        exit_p = close * (1 - slippage_pct / 100)
                        gross  = (exit_p - position["entry"]) * qty
                        costs  = brokerage + exit_p * qty * stt_rate
                        pnl    = gross - costs
                        capital += pnl
                        trades.append({**position, "exit": exit_p, "pnl": pnl,
                                       "exit_ts": ts})
                        position = None
                elif position["side"] == "SELL":
                    if close >= position["stop"] or close <= position["target"] \
                            or bar_t >= dtime(15, 15):
                        exit_p = close * (1 + slippage_pct / 100)
                        gross  = (position["entry"] - exit_p) * qty
                        costs  = brokerage + exit_p * qty * stt_rate
                        pnl    = gross - costs
                        capital += pnl
                        trades.append({**position, "exit": exit_p, "pnl": pnl,
                                       "exit_ts": ts})
                        position = None
                equity.append(capital)
                continue

            # ── Entry check ───────────────────────────────────────────────
            if not (ORB_WINDOW_END <= bar_t <= ORB_VALID_UNTIL):
                equity.append(capital)
                continue
            if adx_v < adx_min or (not is_index and vol_r < volume_min):
                equity.append(capital)
                continue

            if close > orb_high:
                entry  = close * (1 + slippage_pct / 100)
                stop   = orb_low
                target = entry + range_w * target_mult
                position = {
                    "side": "BUY", "entry": entry, "stop": stop,
                    "target": target, "entry_ts": ts, "day": day,
                }
                capital -= brokerage  # entry brokerage
            elif close < orb_low:
                entry  = close * (1 - slippage_pct / 100)
                stop   = orb_high
                target = entry - range_w * target_mult
                position = {
                    "side": "SELL", "entry": entry, "stop": stop,
                    "target": target, "entry_ts": ts, "day": day,
                }
                capital -= brokerage

            equity.append(capital)

    if close_at_end and position and len(work):
        last_ts = work.index[-1]
        last = work.iloc[-1]
        close = float(last.get("Close", last.get("close", 0)) or 0)
        if close > 0:
            if position["side"] == "BUY":
                exit_p = close * (1 - slippage_pct / 100)
                gross = (exit_p - position["entry"]) * qty
            else:
                exit_p = close * (1 + slippage_pct / 100)
                gross = (position["entry"] - exit_p) * qty
            costs = brokerage + exit_p * qty * stt_rate
            pnl = gross - costs
            capital += pnl
            trades.append({**position, "exit": exit_p, "pnl": pnl, "exit_ts": last_ts})
            equity.append(capital)

    return _compute_metrics(symbol, trades, equity, initial_capital, verbose)


def _compute_metrics(
    symbol: str, trades: list, equity: list,
    initial_capital: float, verbose: bool
) -> Dict[str, Any]:
    n        = len(trades)
    wins     = sum(1 for t in trades if t["pnl"] > 0)
    total_pnl= sum(t["pnl"] for t in trades)
    win_rate = wins / n if n else 0.0
    eq       = pd.Series(equity)
    dd       = (eq.cummax() - eq).max()
    ret_s    = eq.pct_change().dropna()
    sharpe   = float(ret_s.mean() / ret_s.std() * (252 * 75) ** 0.5) \
               if len(ret_s) > 1 and ret_s.std() > 0 else 0.0

    if verbose:
        print(f"\n{'='*55}")
        print(f"ORB Backtest — {symbol}")
        print(f"{'='*55}")
        print(f"Net Profit  : ₹{total_pnl:>10,.2f}")
        print(f"Total Trades: {n}")
        print(f"Win Rate    : {win_rate:.2%}")
        print(f"Sharpe Ratio: {sharpe:.4f}")
        print(f"Max Drawdown: ₹{dd:>10,.2f}")
        print(f"Final Capital:₹{initial_capital + total_pnl:>10,.2f}")

    return {
        "symbol": symbol, "total_pnl": round(total_pnl, 2),
        "num_trades": n, "win_rate": round(win_rate, 4),
        "sharpe": round(sharpe, 4), "max_drawdown": round(float(dd), 2),
        "final_capital": round(initial_capital + total_pnl, 2),
    }


def _empty_result(symbol: str, reason: str) -> Dict[str, Any]:
    print(f"Net Profit: 0\nTotal Trades: 0\nWin Rate: 0.0%\nSharpe Ratio: 0.0\nMax Drawdown: 0")
    return {"symbol": symbol, "total_pnl": 0, "num_trades": 0,
            "win_rate": 0, "sharpe": 0, "max_drawdown": 0,
            "final_capital": 0, "reason": reason}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--days",   type=int, default=DEFAULT_DAYS)
    parser.add_argument("--adx",    type=float, default=DEFAULT_ADX_MIN)
    parser.add_argument("--vol",    type=float, default=DEFAULT_VOLUME_MIN)
    parser.add_argument("--tgt",    type=float, default=DEFAULT_TARGET_MULT)
    args = parser.parse_args()

    data = fetch_data(args.symbol, days=args.days)
    if data is None:
        print("No data — cannot run backtest")
        raise SystemExit(1)  # safe in threads

    backtest_orb(
        symbol=args.symbol, data=data,
        adx_min=args.adx, volume_min=args.vol, target_mult=args.tgt,
    )
