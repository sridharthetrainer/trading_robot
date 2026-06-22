"""
backtest_mean_reversion.py

Mean-reversion strategy backtest entry point.

Fixes applied
-------------
Daily data fallback removed — returns empty result instead.

Original behaviour:
    DataFetcher (5m, 15m) fails → yf.download(interval="1d") daily bars
    → run backtest_mr with RSI 14, BB 20, ADX 25 on daily data.

Problem:
    All strategy parameters are calibrated for 5-minute intraday bars.
    On daily bars:
    - RSI 14 oscillates over weeks, not hours
    - BB 20 × 2 standard deviations spans a multi-month range
    - ADX 25 signals trend strength over months, not intraday sessions
    Daily signals look completely different in character from intraday
    signals. The strategy selector received a valid-looking Sharpe and
    win-rate that was unrelated to how the strategy actually performs
    intraday, potentially selecting a strategy for the wrong reason.

Fix:
    If no intraday data is available from DataFetcher, return an empty
    result with all metrics = 0. The strategy selector treats a zero
    Sharpe score neutrally and simply ranks this strategy lower than
    strategies with real intraday results. This is safer than providing
    a measurement from a different data regime.

    yfinance is still used as a SECONDARY intraday source (5m / 15m)
    before giving up — only the daily fallback is removed.
"""
from __future__ import annotations


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


import traceback
from typing import Any, Dict, Optional

import pandas as pd
import yf_compat as yf  # yfinance replaced: Yahoo API broken

from backtest_mr_enhanced import backtest_mr


# ---------------------------------------------------------------------------
# Public backtest wrapper
# ---------------------------------------------------------------------------

def backtest_mean_reversion(
    symbol: str,
    data: pd.DataFrame,
    rsi_period: int                       = 14,
    bb_period: int                        = 20,
    bb_std: float                         = 2.0,
    oversold: int                         = 30,
    overbought: int                       = 70,
    stop_atr_mult: float                  = 1.5,
    lot_multiplier: int                   = 1,
    initial_capital: float                = 100_000.0,
    verbose: bool                         = True,
    trend_sma_period: Optional[int]       = 200,
    adx_threshold: Optional[float]        = 25,
    min_atr_ratio: float                  = 0.0,
    max_atr_ratio: float                  = 1.0,
    profit_target_atr_mult: Optional[float] = None,
    trail_atr_mult: float                 = 1.0,
    trail_activate: float                 = 0.0,
    brokerage_per_order: float            = 20.0,
    close_at_end: bool                    = True,
) -> Dict[str, Any]:
    return backtest_mr(
        symbol                  = symbol,
        data                    = data,
        rsi_period              = rsi_period,
        bb_period               = bb_period,
        bb_std                  = bb_std,
        oversold                = oversold,
        overbought              = overbought,
        trend_sma_period        = trend_sma_period,
        adx_threshold           = adx_threshold,
        min_atr_ratio           = min_atr_ratio,
        max_atr_ratio           = max_atr_ratio,
        stop_atr_mult           = stop_atr_mult,
        profit_target_atr_mult  = profit_target_atr_mult,
        trail_atr_mult          = trail_atr_mult,
        trail_activate          = trail_activate,
        close_at_end            = close_at_end,
        lot_multiplier          = lot_multiplier,
        initial_capital         = initial_capital,
        brokerage_per_order     = brokerage_per_order,
        verbose                 = verbose,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _empty_result(initial_capital: float = 100_000.0) -> Dict[str, Any]:
    return {
        "total_pnl":              0.0,
        "num_trades":             0,
        "win_rate":               0.0,
        "sharpe":                 0.0,
        "max_drawdown":           0.0,
        "final_capital":          float(initial_capital),
        "buy_signals":            0,
        "sell_signals":           0,
        "total_signals":          0,
        "skipped_due_to_filters": 0,
        "csv_file":               None,
    }


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize column names and index to the standard OHLCV format."""
    if df is None or df.empty:
        raise ValueError("Empty dataframe")

    df = df.copy()

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [str(c[0]) if isinstance(c, tuple) else str(c) for c in df.columns]

    rename_map = {
        "open":      "Open",
        "high":      "High",
        "low":       "Low",
        "close":     "Close",
        "adj close": "Close",
        "volume":    "Volume",
        "date":      "Datetime",
        "datetime":  "Datetime",
        "timestamp": "Datetime",
        "time":      "Datetime",
    }
    df.columns = [rename_map.get(str(c).strip().lower(), str(c)) for c in df.columns]

    if "Datetime" in df.columns:
        df["Datetime"] = pd.to_datetime(df["Datetime"], errors="coerce")
        df = df.dropna(subset=["Datetime"]).set_index("Datetime")

    if not isinstance(df.index, pd.DatetimeIndex):
        try:
            df.index = pd.to_datetime(df.index, errors="coerce")
        except Exception:
            pass

    required = ["Open", "High", "Low", "Close"]
    for col in required:
        if col not in df.columns:
            raise ValueError(f"Missing required column: {col}")
        df[col] = pd.to_numeric(df[col], errors="coerce")

    if "Volume" not in df.columns:
        df["Volume"] = 0
    df["Volume"] = pd.to_numeric(df["Volume"], errors="coerce").fillna(0)

    df = df.dropna(subset=required).sort_index()

    if len(df) == 0:
        raise ValueError("No usable OHLC rows after normalization")

    return df


def _fetch_with_datafetcher(symbol: str) -> Optional[pd.DataFrame]:
    """Try to fetch intraday data via the project's DataFetcher."""
    try:
        fetcher = _get_angel_data_fetcher()
    except Exception as exc:
        print(f"⚠️ DataFetcher init failed: {exc}")
        return None

    for interval, days, min_rows in [("5m", 5, 80), ("15m", 10, 50)]:
        try:
            df = fetcher.get_market_data(symbol, interval=interval, days=days)
            if df is not None and not df.empty:
                df = _normalize_ohlcv(df)
                if len(df) >= min_rows:
                    print(f"✅ DataFetcher: {len(df)} candles ({interval})")
                    return df
        except Exception as exc:
            print(f"⚠️ DataFetcher {symbol} {interval}: {exc}")

    return None


def _fetch_with_yfinance_intraday(symbol: str) -> Optional[pd.DataFrame]:
    """
    Try to fetch intraday data from yfinance as a secondary source.

    Only attempts intraday intervals (5m, 15m) — NOT daily.
    yfinance intraday data for Indian markets can have gaps, so we
    require at least 60 rows to consider the data usable.
    """
    symbol_map = {
        "NIFTY":    "^NSEI",
        "BANKNIFTY": "^NSEBANK",
        "SENSEX":   "^BSESN",
    }
    yf_symbol = symbol_map.get(symbol.upper(), f"{symbol}.NS")

    # INTRADAY only — no daily fallback
    attempts = [
        {"period": "60d",  "interval": "5m",  "min_rows": 80},
        {"period": "60d",  "interval": "15m", "min_rows": 50},
        {"period": "30d",  "interval": "5m",  "min_rows": 50},
    ]

    for params in attempts:
        try:
            df = yf.download(
                yf_symbol,
                period   = params["period"],
                interval = params["interval"],
                progress = False,
                auto_adjust = False,
                group_by = "column",
                threads  = False,
            )
            if df is None or df.empty:
                continue

            df = _normalize_ohlcv(df)
            if len(df) >= params["min_rows"]:
                print(
                    f"✅ yfinance (intraday): {len(df)} candles "
                    f"({yf_symbol}, {params['period']}, {params['interval']})"
                )
                return df
        except Exception as exc:
            print(f"⚠️ yfinance intraday {yf_symbol} {params['interval']}: {exc}")

    return None


def _fetch_data(symbol: str) -> Optional[pd.DataFrame]:
    """
    Fetch intraday data, trying DataFetcher first then yfinance intraday.
    Returns None if no intraday data is available — does NOT fall back
    to daily data.
    """
    df = _fetch_with_datafetcher(symbol)
    if df is not None:
        return df

    df = _fetch_with_yfinance_intraday(symbol)
    if df is not None:
        return df

    print(
        "⚠️ No intraday data available from DataFetcher or yfinance. "
        "Daily data fallback is disabled — parameters are calibrated "
        "for intraday bars and would produce meaningless signals on "
        "daily data. Returning empty result."
    )
    return None


def _print_metrics(result: Dict[str, Any]) -> None:
    print("Net Profit   :", float(result.get("total_pnl",    0.0) or 0.0))
    print("Total Trades :", int(  result.get("num_trades",   0)   or 0))
    print("Win Rate     :", float(result.get("win_rate",     0.0) or 0.0) * 100.0)
    print("Sharpe Ratio :", float(result.get("sharpe",       0.0) or 0.0))
    print("Max Drawdown :", float(result.get("max_drawdown", 0.0) or 0.0))


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    initial_capital = 100_000.0

    try:
        print("Starting Mean-Reversion backtest...")

        symbol = "NIFTY"
        data   = _fetch_data(symbol)

        if data is None or data.empty or len(data) < 20:
            print("No intraday data available — returning zero-metric result.")
            result = _empty_result(initial_capital=initial_capital)
        else:
            print(f"Final dataset: {len(data)} intraday candles")
            result = backtest_mean_reversion(
                symbol                  = symbol,
                data                    = data,
                rsi_period              = 14,
                bb_period               = 20,
                bb_std                  = 2.0,
                oversold                = 30,
                overbought              = 70,
                stop_atr_mult           = 1.5,
                trend_sma_period        = 200,
                adx_threshold           = 25,
                min_atr_ratio           = 0.0,
                max_atr_ratio           = 1.0,
                profit_target_atr_mult  = None,
                trail_atr_mult          = 1.0,
                trail_activate          = 0.0,
                lot_multiplier          = 1,
                initial_capital         = initial_capital,
                brokerage_per_order     = 20.0,
                close_at_end            = True,
                verbose                 = True,
            )

        print("\n" + "=" * 60)
        print(f"MEAN-REVERSION BACKTEST ({symbol})")
        print("=" * 60)
        print(f"Total P&L         : ₹{float(result.get('total_pnl',      0.0)):.2f}")
        print(f"Trades            : {int(  result.get('num_trades',       0))}")
        print(f"Win Rate          : {float(result.get('win_rate',         0.0)) * 100:.2f}%")
        print(f"Sharpe            : {float(result.get('sharpe',           0.0)):.2f}")
        print(f"Max Drawdown      : ₹{float(result.get('max_drawdown',   0.0)):.2f}")
        print(f"Final Capital     : ₹{float(result.get('final_capital',  initial_capital)):.2f}")
        print(f"Buy Signals       : {int(  result.get('buy_signals',      0))}")
        print(f"Sell Signals      : {int(  result.get('sell_signals',     0))}")
        print(f"Total Signals     : {int(  result.get('total_signals',    0))}")
        print(f"Skipped By Filter : {int(  result.get('skipped_due_to_filters', 0))}")
        print(f"CSV File          : {result.get('csv_file')}")
        print("\n" + "-" * 60)
        _print_metrics(result)
        print("-" * 60)

        raise SystemExit(1)  # safe in threads

    except Exception as exc:
        print("Backtest crashed:", str(exc))
        traceback.print_exc()
        result = _empty_result(initial_capital=initial_capital)
        print("\n" + "-" * 60)
        _print_metrics(result)
        print("-" * 60)
        raise SystemExit(1)  # safe in threads
