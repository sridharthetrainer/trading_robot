"""
autonomous_backtest.py

Fully autonomous after-hours backtest engine.

WHAT IT DOES:
  1. Runs every night at 4:30 PM (after market close)
  2. Tests ALL symbols: 4 indices + top 50 Nifty200 stocks
  3. Fine-tunes stop/target ATR multipliers per symbol
  4. Identifies best strategies per symbol
  5. Saves per-symbol optimal params to symbol_params.json
  6. Sends Telegram report: top/worst performers, insights
  7. Updates strategy_performance_matrix with backtest results

SYMBOLS TESTED:
  Priority: NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY
  Stocks:   Top 50 from Nifty200 (sorted by market cap / liquidity)
  Each symbol: 90 days history, 28 strategies

FINE-TUNING PER SYMBOL:
  Tests stop ATR multipliers: [1.0, 1.5, 2.0, 2.5]
  Tests target ATR multipliers: [1.5, 2.0, 2.5, 3.0, 4.0]
  Picks best combo by: profit factor × win rate
  Saves to symbol_params.json — used by live engine

TELEGRAM REPORT (sent at ~5:30 PM):
  - Top 5 strategies by net P&L
  - Bottom 5 (candidates for disable)
  - Best symbols to trade tomorrow
  - Total charges paid (cross-check vs live)
  - Param changes applied
"""
from __future__ import annotations
try:
    from signal_log import get_signal_logger as _get_sig_log
    _SIG_LOG_AVAIL = True
except ImportError:
    _SIG_LOG_AVAIL = False

import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

HERE = Path(__file__).parent

# ── Constants ─────────────────────────────────────────────────────────────────
INDICES = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY",
           "SENSEX", "NIFTYNEXT50"]  # all indices

def _load_all_symbols() -> list:
    """Load full 198-symbol universe from nifty200.csv — same as live scanner."""
    import pandas as _pd
    
    for _p in ["nifty200.csv", "trading_robot/nifty200.csv"]:
        try:
            _df = _pd.read_csv(_p)
            _col = [c for c in _df.columns if c.lower() in ("symbol","ticker","scrip")][0]
            syms = [str(s).upper().strip() for s in _df[_col].dropna()]
            return syms[:200]
        except Exception:
            pass
    return INDICES + TOP_STOCKS  # fallback


# Top 50 liquid Nifty200 stocks for overnight backtest
TOP_STOCKS = [
    "RELIANCE","TCS","HDFCBANK","ICICIBANK","INFOSYS",
    "HINDUNILVR","ITC","SBIN","BHARTIARTL","KOTAKBANK",
    "LT","AXISBANK","ASIANPAINT","MARUTI","SUNPHARMA",
    "TITAN","ULTRACEMCO","WIPRO","NESTLEIND","BAJFINANCE",
    "HCLTECH","POWERGRID","NTPC","ONGC","COALINDIA",
    "TECHM","INDUSINDBK","DRREDDY","BAJAJFINSV","GRASIM",
    "ADANIENT","CIPLA","DIVISLAB","EICHERMOT","HEROMOTOCO",
    "APOLLOHOSP","TATACONSUM","BRITANNIA","BAJAJ-AUTO","UPL",
    "SHREECEM","HINDALCO","JSWSTEEL","TATASTEEL","ADANIPORTS",
    "SBILIFE","HDFCLIFE","M&M","VEDL","PIDILITIND",
]

# ATR multiplier combinations to test for fine-tuning
STOP_MULTS   = [1.0, 1.5, 2.0, 2.5]
TARGET_MULTS = [1.5, 2.0, 2.5, 3.0, 4.0]

LOT_SIZE = 75    # NIFTY
CAPITAL  = 100_000

# Charges
BROKERAGE  = 20.0
# April 2026 NSE F&O charges (revised)
STT_RATE_FUT = 0.0002   # Futures 0.02% sell side (doubled)
STT_RATE_OPT = 0.001    # Options 0.10% sell side (doubled)
STT_RATE     = 0.0002   # Default — futures
EXCH_RATE  = 0.00053
SEBI_RATE  = 0.000001
GST_RATE   = 0.18
STAMP_RATE = 0.00003


# ── Transaction costs ─────────────────────────────────────────────────────────

def _slippage_model(price: float, symbol: str, side: str) -> float:
    """
    Realistic slippage model based on Indian market microstructure.
    Inspired by "Trading and Exchanges" — Larry Harris.

    Large caps (NIFTY50): 0.05% slippage
    Mid caps (NIFTY200): 0.10% slippage
    Small caps: 0.20% slippage
    Indices (NIFTY/BNF): 0.03% (futures — highly liquid)
    """
    indices = {"NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY","SENSEX"}
    nifty50 = {"RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK","HINDUNILVR",
                "SBIN","BHARTIARTL","KOTAKBANK","LT","AXISBANK","BAJFINANCE"}

    if symbol.upper() in indices:
        slip_pct = 0.0003  # 0.03% for index futures
    elif symbol.upper() in nifty50:
        slip_pct = 0.0005  # 0.05% for Nifty50 stocks
    else:
        slip_pct = 0.0010  # 0.10% for others

    # Buy: pay more. Sell: receive less
    direction = 1 if side == "BUY" else -1
    return price * (1 + direction * slip_pct)


def _realistic_slippage(price: float, symbol: str, signal_type: str = "OPTIONS") -> float:
    """
    Model realistic bid-ask slippage for NSE options/futures.

    Based on NSE market microstructure:
      OTM options (price < 50):  spread = 0.5-1.0 (1-2%)
      ATM options (50-200):      spread = 1-3 (0.5-1.5%)
      ITM options (>200):        spread = 2-5 (0.5-1%)
      Futures (large lot):       spread = 0.1-0.5 points (0.0005%)
      Index (NIFTY spot):        virtual, no slippage

    Returns the one-way slippage cost (add to buy, subtract from sell).
    """
    if symbol.upper() in ("NIFTY","BANKNIFTY","FINNIFTY","SENSEX","MIDCPNIFTY"):
        return 0.0  # index virtual trades
    if "PE" in signal_type or "CE" in signal_type:
        if price < 20:   return min(price * 0.05, 1.0)  # OTM cheap options
        if price < 50:   return 1.0
        if price < 100:  return 2.0
        if price < 200:  return 3.0
        return price * 0.01  # 1% for ITM
    # Futures
    return price * 0.0003

def _charges(entry: float, exit_: float, qty: int) -> Tuple[float, float]:
    """Returns (gross_pnl, net_pnl)."""
    gross     = (exit_ - entry) * qty
    entry_tv  = entry  * qty
    exit_tv   = exit_  * qty
    brok      = 2 * BROKERAGE
    _is_opt = "CE" in symbol or "PE" in symbol
    stt     = exit_tv * (STT_RATE_OPT if _is_opt else STT_RATE_FUT)
    exch      = (entry_tv + exit_tv) * EXCH_RATE / 2
    sebi      = (entry_tv + exit_tv) * SEBI_RATE
    gst       = (brok + exch + sebi) * GST_RATE
    stamp     = entry_tv * STAMP_RATE
    total     = brok + stt + exch + sebi + gst + stamp
    return round(gross, 2), round(gross - total, 2)


# ── Data fetch ────────────────────────────────────────────────────────────────
def _fetch(symbol: str, days: int = 90) -> Optional[pd.DataFrame]:
    """Fetch OHLCV — Angel One → yfinance."""
    # Angel One
    try:
        from data_fetcher import DataFetcher
        # Use 1d bars — works any time (no session required after market close)
        # DATA SOURCE PRIORITY:
        # 1. Bhavcopy cache (SQLite, always works offline)
        # 2. NSE historical API (works until ~6 PM)
        # 3. DataFetcher (fallback)
        import time as _t; _t.sleep(0.2)
        try:
            # Try local Bhavcopy cache first
            from bhavcopy_cache import get_ohlcv as _get_bhav
            df = _get_bhav(symbol, days=max(days, 60))
            if df is not None and len(df) >= 10:
                return df
        except Exception: pass

        # NIFTY/BANKNIFTY etc — not in Bhavcopy (index, not equity)
        # Use a minimal single-bar df so backtest doesn't skip them
        _IDX = {"NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY","SENSEX","NIFTYNEXT50"}
        if symbol.upper() in _IDX and df is None:
            try:
                import requests as _rqx, pandas as _pdx
                _sx = _rqx.Session()
                _sx.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://www.nseindia.com"})
                _sx.get("https://www.nseindia.com/",timeout=4)
                _rx = _sx.get("https://www.nseindia.com/api/allIndices",timeout=6)
                _nm = {"NIFTY":"NIFTY 50","BANKNIFTY":"NIFTY BANK","FINNIFTY":"NIFTY FIN SERVICE",
                       "MIDCPNIFTY":"NIFTY MIDCAP SELECT","SENSEX":"S&P BSE SENSEX"}
                for _ix in _rx.json().get("data",[]):
                    if _nm.get(symbol.upper(),"") in str(_ix.get("index","")).upper():
                        _p = float(_ix.get("last",0) or 0)
                        if _p:
                            df = _pdx.DataFrame([{"open":_p,"high":_p,"low":_p,"close":_p,"volume":0}],
                                                index=[_pdx.Timestamp.now()])
                        break
            except Exception: pass
        try:
            import requests as _rq, pandas as _pd
            from datetime import timedelta as _td
            _s = _rq.Session()
            _s.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://www.nseindia.com"})
            _s.get("https://www.nseindia.com/", timeout=5)
            _idx_map = {"NIFTY":"NIFTY 50","BANKNIFTY":"NIFTY BANK","FINNIFTY":"NIFTY FIN SERVICE",
                        "MIDCPNIFTY":"NIFTY MIDCAP SELECT","SENSEX":"S&P BSE SENSEX"}
            _end   = __import__("datetime").datetime.now().strftime("%d-%m-%Y")
            _start = (__import__("datetime").datetime.now()-_td(days=max(days,90))).strftime("%d-%m-%Y")
            if symbol.upper() in _idx_map:
                _iname = _idx_map[symbol.upper()]
                _r = _s.get(f"https://www.nseindia.com/api/historical/indicesHistory?indexType={_iname.replace(' ','%20')}&from={_start}&to={_end}", timeout=12)
                _recs = _r.json().get("data",{}).get("indexCloseOnlineRecords",[]) if _r.status_code==200 else []
                if _recs:
                    _rows = [{"date":_pd.Timestamp(d["EOD_TIMESTAMP"],format="%d-%b-%Y"),
                              "open":float(d["EOD_OPEN_INDEX_VAL"]),"high":float(d["EOD_HIGH_INDEX_VAL"]),
                              "low":float(d["EOD_LOW_INDEX_VAL"]),"close":float(d["EOD_CLOSE_INDEX_VAL"]),"volume":0}
                             for d in _recs]
                    df = _pd.DataFrame(_rows).set_index("date").sort_index()
                else:
                    df = None
            else:
                _r = _s.get(f"https://www.nseindia.com/api/historical/cm/equity?symbol={symbol}&series=[%22EQ%22]&from={_start}&to={_end}", timeout=12)
                _data = _r.json().get("data",[]) if _r.status_code==200 else []
                if _data:
                    _rows = [{"date":_pd.Timestamp(d["CH_TIMESTAMP"]),
                              "open":float(d["CH_OPENING_PRICE"]),"high":float(d["CH_TRADE_HIGH_PRICE"]),
                              "low":float(d["CH_TRADE_LOW_PRICE"]),"close":float(d["CH_CLOSING_PRICE"]),
                              "volume":float(d.get("CH_TOT_TRADED_QTY",0))} for d in _data]
                    df = _pd.DataFrame(_rows).set_index("date").sort_index()
                else:
                    df = None
        except Exception as _e:
            logger.debug("NSE backtest fetch %s: %s", symbol, _e)
            df = None
        if df is not None and len(df) > 100:
            df.columns = [c.lower() for c in df.columns]
            return df
    except Exception:
        pass
    # yfinance (handles both old Series and new MultiIndex API)
    try:
        import yf_compat as yf  # yfinance replaced: Yahoo API broken
        ticker_map = {
            "NIFTY":"^NSEI","BANKNIFTY":"^NSEBANK",
            "FINNIFTY":"NIFTY_FIN_SERVICE.NS",
            "MIDCPNIFTY":"NIFTY_MIDCAP_SELECT.NS",
        }
        ticker = ticker_map.get(symbol, f"{symbol}.NS")
        end = datetime.now()
        start = end - timedelta(days=days + 5)
        df = yf.download(ticker, start=start, end=end,
                         interval="5m", progress=False, auto_adjust=True)
        # Handle yfinance MultiIndex (>=0.2.18)
        if df is not None and isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)
        if df is not None:
            df = df.rename(columns=str.title)
        if df is not None and len(df) > 100:
            df.columns = [c.lower() for c in df.columns]
            return df
    except Exception:
        pass
    return None


def _fetch_htf(symbol: str, days: int = 90) -> Optional[pd.DataFrame]:
    try:
        from data_fetcher import DataFetcher
        # DATA SOURCE PRIORITY:
        # 1. Bhavcopy cache (SQLite, always works offline)
        # 2. NSE historical API (works until ~6 PM)
        # 3. DataFetcher (fallback)
        import time as _t; _t.sleep(0.2)
        try:
            # Try local Bhavcopy cache first
            from bhavcopy_cache import get_ohlcv as _get_bhav
            df = _get_bhav(symbol, days=max(days, 60))
            if df is not None and len(df) >= 10:
                return df
        except Exception: pass

        # NIFTY/BANKNIFTY etc — not in Bhavcopy (index, not equity)
        # Use a minimal single-bar df so backtest doesn't skip them
        _IDX = {"NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY","SENSEX","NIFTYNEXT50"}
        if symbol.upper() in _IDX and df is None:
            try:
                import requests as _rqx, pandas as _pdx
                _sx = _rqx.Session()
                _sx.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://www.nseindia.com"})
                _sx.get("https://www.nseindia.com/",timeout=4)
                _rx = _sx.get("https://www.nseindia.com/api/allIndices",timeout=6)
                _nm = {"NIFTY":"NIFTY 50","BANKNIFTY":"NIFTY BANK","FINNIFTY":"NIFTY FIN SERVICE",
                       "MIDCPNIFTY":"NIFTY MIDCAP SELECT","SENSEX":"S&P BSE SENSEX"}
                for _ix in _rx.json().get("data",[]):
                    if _nm.get(symbol.upper(),"") in str(_ix.get("index","")).upper():
                        _p = float(_ix.get("last",0) or 0)
                        if _p:
                            df = _pdx.DataFrame([{"open":_p,"high":_p,"low":_p,"close":_p,"volume":0}],
                                                index=[_pdx.Timestamp.now()])
                        break
            except Exception: pass
        try:
            import requests as _rq, pandas as _pd
            from datetime import timedelta as _td
            _s = _rq.Session()
            _s.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://www.nseindia.com"})
            _s.get("https://www.nseindia.com/", timeout=5)
            _idx_map = {"NIFTY":"NIFTY 50","BANKNIFTY":"NIFTY BANK","FINNIFTY":"NIFTY FIN SERVICE",
                        "MIDCPNIFTY":"NIFTY MIDCAP SELECT","SENSEX":"S&P BSE SENSEX"}
            _end   = __import__("datetime").datetime.now().strftime("%d-%m-%Y")
            _start = (__import__("datetime").datetime.now()-_td(days=max(days,90))).strftime("%d-%m-%Y")
            if symbol.upper() in _idx_map:
                _iname = _idx_map[symbol.upper()]
                _r = _s.get(f"https://www.nseindia.com/api/historical/indicesHistory?indexType={_iname.replace(' ','%20')}&from={_start}&to={_end}", timeout=12)
                _recs = _r.json().get("data",{}).get("indexCloseOnlineRecords",[]) if _r.status_code==200 else []
                if _recs:
                    _rows = [{"date":_pd.Timestamp(d["EOD_TIMESTAMP"],format="%d-%b-%Y"),
                              "open":float(d["EOD_OPEN_INDEX_VAL"]),"high":float(d["EOD_HIGH_INDEX_VAL"]),
                              "low":float(d["EOD_LOW_INDEX_VAL"]),"close":float(d["EOD_CLOSE_INDEX_VAL"]),"volume":0}
                             for d in _recs]
                    df = _pd.DataFrame(_rows).set_index("date").sort_index()
                else:
                    df = None
            else:
                _r = _s.get(f"https://www.nseindia.com/api/historical/cm/equity?symbol={symbol}&series=[%22EQ%22]&from={_start}&to={_end}", timeout=12)
                _data = _r.json().get("data",[]) if _r.status_code==200 else []
                if _data:
                    _rows = [{"date":_pd.Timestamp(d["CH_TIMESTAMP"]),
                              "open":float(d["CH_OPENING_PRICE"]),"high":float(d["CH_TRADE_HIGH_PRICE"]),
                              "low":float(d["CH_TRADE_LOW_PRICE"]),"close":float(d["CH_CLOSING_PRICE"]),
                              "volume":float(d.get("CH_TOT_TRADED_QTY",0))} for d in _data]
                    df = _pd.DataFrame(_rows).set_index("date").sort_index()
                else:
                    df = None
        except Exception as _e:
            logger.debug("NSE backtest fetch %s: %s", symbol, _e)
            df = None
        if df is not None and len(df) > 30:
            df.columns = [c.lower() for c in df.columns]
            return df
    except Exception:
        pass
    return None


# ── ATR ───────────────────────────────────────────────────────────────────────
def _atr(df: pd.DataFrame, p: int = 14) -> pd.Series:
    h = df["high"]; l = df["low"]; c = df["close"]
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(p).mean().fillna(c * 0.005)


# ── Mini backtest for one param combo ─────────────────────────────────────────
def _run_single(df: pd.DataFrame, df_htf: Optional[pd.DataFrame],
                symbol: str, stop_mult: float, tgt_mult: float,
                strategy_filter: Optional[str] = None) -> dict:
    """
    Run backtest with specific ATR multipliers.
    Returns metrics dict.
    """
    try:
        from signal_engine import generate_signal
    except ImportError:
        return {}

    WARMUP = 100
    if len(df) < WARMUP + 20:
        return {}

    atr_vals  = _atr(df)
    qty       = LOT_SIZE
    trades    = []
    open_trade = None
    capital   = float(CAPITAL)

    for i in range(WARMUP, len(df)):
        bar    = df.iloc[i]
        close  = float(bar.get("close", 0))
        high   = float(bar.get("high", close))
        low    = float(bar.get("low", close))
        atr    = float(atr_vals.iloc[i]) if i < len(atr_vals) else close * 0.005

        # Manage open trade
        if open_trade:
            t = open_trade
            t["bars"] += 1
            ep = None; er = None

            if t["side"] == "BUY":
                if low  <= t["stop"]:   ep, er = t["stop"], "stop"
                elif high >= t["tgt"]:  ep, er = t["tgt"],  "target"
            else:
                if high >= t["stop"]:   ep, er = t["stop"], "stop"
                elif low  <= t["tgt"]:  ep, er = t["tgt"],  "target"

            if ep is None and t["bars"] >= 12:
                ep, er = close, "time"

            if ep is not None:
                gross, net = _charges(t["entry"], ep, qty)
                trades.append({
                    "strategy": t["strat"], "side": t["side"],
                    "entry": t["entry"], "exit": ep,
                    "gross": gross, "net": net, "reason": er,
                })
                capital += net
                open_trade = None

        # New signal
        if open_trade is None:
            try:
                sig = generate_signal(
                    df=df.iloc[:i+1], df_htf=df_htf.iloc[:i//3+1] if df_htf is not None else None,
                    symbol=symbol, capital=capital, cfg=None
                )
                if sig and sig.get("direction") and float(sig.get("score",0)) >= 3.5:
                    strat = sig.get("strategy","")
                    if strategy_filter and strategy_filter not in strat:
                        continue
                    d = sig["direction"]
                    stop = close - atr*stop_mult if d=="BUY" else close + atr*stop_mult
                    tgt  = close + atr*tgt_mult  if d=="BUY" else close - atr*tgt_mult
                    open_trade = {"entry":close,"stop":stop,"tgt":tgt,
                                  "side":d,"strat":strat,"bars":0}
            except Exception:
                pass

    if not trades:
        return {}

    pnls = [t["net"] for t in trades]
    wins = [p for p in pnls if p > 0]
    loss = [p for p in pnls if p <= 0]
    wr   = len(wins)/len(pnls)*100 if pnls else 0
    pf   = sum(wins)/abs(sum(loss)) if loss and sum(loss) != 0 else (99.0 if wins else 0)

    # Max drawdown
    peak = CAPITAL; running = CAPITAL; mdd = 0.0
    for p in pnls:
        running += p; peak = max(peak, running)
        mdd = max(mdd, (peak-running)/peak)

    return {
        "trades":    len(trades),
        "win_rate":  round(wr, 1),
        "net_pnl":   round(sum(pnls), 2),
        "pf":        round(pf, 2),
        "mdd":       round(mdd*100, 2),
        "score":     round(pf * wr / 100, 3),   # composite score for param selection
        "stop_mult": stop_mult,
        "tgt_mult":  tgt_mult,
    }


# ── Per-symbol fine-tuning ────────────────────────────────────────────────────
def fine_tune_symbol(symbol: str, df: pd.DataFrame,
                     df_htf: Optional[pd.DataFrame]) -> dict:
    """
    Grid search over stop/target ATR multipliers.
    Returns best params and performance metrics.
    """
    logger.info("Fine-tuning %s ...", symbol)
    best = None

    for stop_m in STOP_MULTS:
        for tgt_m in TARGET_MULTS:
            if tgt_m <= stop_m:
                continue   # target must be larger than stop
            result = _run_single(df, df_htf, symbol, stop_m, tgt_m)
            if not result or result.get("trades", 0) < 5:
                continue
            if best is None or result["score"] > best["score"]:
                best = result

    if best is None:
        return {"symbol": symbol, "status": "insufficient_data"}

    return {
        "symbol":      symbol,
        "best_stop":   best["stop_mult"],
        "best_target": best["tgt_mult"],
        "win_rate":    best["win_rate"],
        "net_pnl":     best["net_pnl"],
        "profit_factor": best["pf"],
        "max_drawdown":  best["mdd"],
        "trades":        best["trades"],
        "composite_score": best["score"],
        "status":       "ok",
    }


# ── Strategy breakdown for a symbol ──────────────────────────────────────────
def backtest_strategies_for_symbol(symbol: str, df: pd.DataFrame,
                                    df_htf: Optional[pd.DataFrame],
                                    stop_mult: float, tgt_mult: float) -> dict:
    """Run backtest and capture per-strategy performance."""
    try:
        from signal_engine import generate_signal, STRATEGIES
    except ImportError:
        return {}

    WARMUP = 100
    if len(df) < WARMUP + 20:
        return {}

    atr_vals    = _atr(df)
    by_strat    = {}
    open_trade  = None
    capital     = float(CAPITAL)

    for i in range(WARMUP, len(df)):
        bar   = df.iloc[i]
        close = float(bar.get("close",0))
        high  = float(bar.get("high",close))
        low   = float(bar.get("low",close))
        atr   = float(atr_vals.iloc[i]) if i < len(atr_vals) else close*0.005

        if open_trade:
            t = open_trade; t["bars"] += 1
            ep = er = None
            if t["side"] == "BUY":
                if low  <= t["stop"]: ep,er = t["stop"],"stop"
                elif high >= t["tgt"]: ep,er = t["tgt"],"target"
            else:
                if high >= t["stop"]: ep,er = t["stop"],"stop"
                elif low  <= t["tgt"]: ep,er = t["tgt"],"target"
            if ep is None and t["bars"] >= 12:
                ep,er = close,"time"
            if ep is not None:
                gross, net = _charges(t["entry"], ep, LOT_SIZE)
                s = t["strat"]
                if s not in by_strat:
                    by_strat[s] = {"trades":0,"wins":0,"pnl":0.0,"charges":0.0}
                by_strat[s]["trades"]  += 1
                by_strat[s]["wins"]    += 1 if net > 0 else 0
                by_strat[s]["pnl"]     += net
                by_strat[s]["charges"] += abs(net - gross)
                capital += net
                open_trade = None

        if open_trade is None:
            try:
                sig = generate_signal(
                    df=df.iloc[:i+1],
                    df_htf=df_htf.iloc[:i//3+1] if df_htf is not None else None,
                    symbol=symbol, capital=capital, cfg=None,
                )
                if sig and sig.get("direction") and float(sig.get("score",0)) >= 3.5:
                    d = sig["direction"]
                    stop = close - atr*stop_mult if d=="BUY" else close + atr*stop_mult
                    tgt  = close + atr*tgt_mult  if d=="BUY" else close - atr*tgt_mult
                    open_trade = {"entry":close,"stop":stop,"tgt":tgt,
                                  "side":d,"strat":sig.get("strategy","?"),"bars":0}
            except Exception:
                pass

    # Compute win rates
    for s,d in by_strat.items():
        d["win_rate"] = round(d["wins"]/d["trades"]*100,1) if d["trades"] > 0 else 0
        d["avg_pnl"]  = round(d["pnl"]/d["trades"],2) if d["trades"] > 0 else 0

    return by_strat


# ── Main overnight backtest ───────────────────────────────────────────────────
class AutonomousBacktest:
    """
    Runs full backtest overnight. Called from main_autonomous._after_hours_tasks().
    """

    PARAMS_FILE   = "symbol_params.json"
    RESULTS_FILE  = "backtest_results_full.json"
    RUN_HOUR      = 16    # 4 PM
    RUN_MINUTE    = 30    # 4:30 PM

    def __init__(self, alerts=None):
        self._alerts  = alerts
        self._results: Dict[str, Any] = {}
        self._params:  Dict[str, Any] = self._load_params()

    def _load_params(self) -> dict:
        try:
            p = HERE / self.PARAMS_FILE
            if p.exists():
                return json.loads(p.read_text())
        except Exception:
            pass
        return {}

    def _save_params(self) -> None:
        try:
            (HERE / self.PARAMS_FILE).write_text(
                json.dumps(self._params, indent=2)
            )
        except Exception:
            pass

    def should_run_today(self) -> bool:
        """Run once per day between 4:30-8:00 PM."""
        from datetime import time as _dts
        now = datetime.now()
        # Only run in the post-market window
        if not (_dts(16, 28) <= now.time() <= _dts(20, 0)):
            return False
        today = str(now.date())
        last  = self._results.get("last_run_date","")
        return last != today

    def run(self) -> dict:
        """
        Full overnight backtest.
        Returns summary dict. Sends Telegram report.
        """
        start_ts   = time.time()
        today      = str(datetime.now().date())
        logger.info("Autonomous backtest starting — testing all symbols")

        # Use FULL universe — same 198 symbols as live scanner
        try:
            symbols_to_test = INDICES + [
                s for s in _load_all_symbols() if s not in INDICES
            ]
        except Exception:
            symbols_to_test = INDICES + TOP_STOCKS  # fallback
        all_results      = {}
        params_updated   = []

        total = len(symbols_to_test)
        done  = 0

        # Run in chunks of 20 — yields control to bot between chunks
        import time as _bt_time
        CHUNK = 20
        for symbol in symbols_to_test:
            done += 1
            if done % CHUNK == 0:
                _bt_time.sleep(2)  # breathe — let main loop run
                logger.info("Backtest progress: %d/%d symbols", done, total)
            logger.debug("Backtesting %s (%d/%d)...", symbol, done, total)

            df = _fetch(symbol, days=90)
            _min_bars = 20  # bhavcopy gives ~43 bars (60 calendar days)
            if df is None or len(df) < _min_bars:
                logger.debug("Insufficient data for %s (%d bars) — skipping",
                             symbol, len(df) if df is not None else 0)
                all_results[symbol] = {"status": "no_data"}
                continue

            df_htf = _fetch_htf(symbol, days=90)

            # Fine-tune params
            tune = fine_tune_symbol(symbol, df, df_htf)
            stop_m = tune.get("best_stop",  1.5)
            tgt_m  = tune.get("best_target", 2.5)

            # Check if params changed
            prev = self._params.get(symbol, {})
            if (prev.get("stop_mult") != stop_m or
                    prev.get("target_mult") != tgt_m):
                params_updated.append({
                    "symbol": symbol,
                    "old_stop":   prev.get("stop_mult", "default"),
                    "new_stop":   stop_m,
                    "old_target": prev.get("target_mult", "default"),
                    "new_target": tgt_m,
                })
                self._params[symbol] = {
                    "stop_mult":   stop_m,
                    "target_mult": tgt_m,
                    "updated":     today,
                }

            # Full strategy breakdown with best params
            by_strat = backtest_strategies_for_symbol(
                symbol, df, df_htf, stop_m, tgt_m
            )

            all_results[symbol] = {
                **tune,
                "by_strategy": by_strat,
                "best_stop":   stop_m,
                "best_target": tgt_m,
            }

            time.sleep(1)   # rate limit

        # Save params
        self._save_params()

        # Build full results
        elapsed = round(time.time() - start_ts, 1)
        summary = self._build_summary(all_results, params_updated, elapsed, today)

        # Save to file
        try:
            (HERE / self.RESULTS_FILE).write_text(
                json.dumps(summary, indent=2, default=str)
            )
        except Exception:
            pass

        # Update strategy matrix with backtest data
        self._update_strategy_matrix(all_results)

        # Send Telegram report
        self._send_telegram_report(summary)

        logger.info("Autonomous backtest complete in %.0fs", elapsed)
        return summary

    def _build_summary(self, results: dict, params_updated: list,
                       elapsed: float, today: str) -> dict:
        """Build ranked summary of all results."""

        # Collect all symbols with valid results
        valid = {s: r for s, r in results.items()
                 if r.get("status") == "ok" and r.get("net_pnl") is not None}

        # Sort by composite score
        ranked = sorted(valid.items(),
                        key=lambda x: x[1].get("composite_score", 0),
                        reverse=True)

        # Top strategies across all symbols
        all_strats: Dict[str, dict] = {}
        for sym, res in valid.items():
            for strat, d in res.get("by_strategy", {}).items():
                if strat not in all_strats:
                    all_strats[strat] = {"symbols":0,"trades":0,"wins":0,
                                          "pnl":0.0,"charges":0.0}
                all_strats[strat]["symbols"] += 1
                all_strats[strat]["trades"]  += d.get("trades",0)
                all_strats[strat]["wins"]    += d.get("wins",0)
                all_strats[strat]["pnl"]     += d.get("pnl",0)
                all_strats[strat]["charges"] += d.get("charges",0)

        for s,d in all_strats.items():
            d["win_rate"] = round(d["wins"]/max(d["trades"],1)*100,1)
            d["avg_pnl"]  = round(d["pnl"]/max(d["trades"],1),2)

        strats_ranked = sorted(all_strats.items(),
                               key=lambda x: x[1]["pnl"], reverse=True)

        return {
            "date":              today,
            "elapsed_seconds":   elapsed,
            "symbols_tested":    len(results),
            "symbols_ok":        len(valid),
            "symbols_no_data":   sum(1 for r in results.values() if r.get("status")=="no_data"),
            "params_updated":    params_updated,
            "top_symbols":       [{"symbol":s, **r} for s,r in ranked[:10]],
            "worst_symbols":     [{"symbol":s, **r} for s,r in ranked[-5:]],
            "strategy_ranking":  [{"strategy":s, **d} for s,d in strats_ranked],
            "last_run_date":     today,
        }

    def _update_strategy_matrix(self, results: dict) -> None:
        """Push backtest win rates into the live strategy_performance_matrix."""
        try:
            from strategy_performance_matrix import get_strategy_matrix
            matrix = get_strategy_matrix()
            for sym, res in results.items():
                for strat, d in res.get("by_strategy", {}).items():
                    wins   = d.get("wins", 0)
                    trades = d.get("trades", 0)
                    if trades < 3:
                        continue
                    losses = trades - wins
                    # Record as backtest trades in matrix
                    for _ in range(min(wins, 5)):
                        matrix.record_outcome(strat, "TREND", True,  0.5, 0.5)
                    for _ in range(min(losses, 5)):
                        matrix.record_outcome(strat, "TREND", False, 0.5, 0.5)
        except Exception as e:
            logger.debug("Strategy matrix update: %s", e)

    def _send_telegram_report(self, summary: dict) -> None:
        """Send formatted backtest report to Telegram."""
        if not self._alerts:
            return
        try:
            lines = [
                f"📊 <b>OVERNIGHT BACKTEST REPORT</b>",
                f"{summary['date']} | {summary['elapsed_seconds']:.0f}s",
                f"Symbols: {summary['symbols_ok']}/{summary['symbols_tested']} ok",
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            ]

            # Top 5 symbols
            lines.append("\n🏆 <b>TOP SYMBOLS:</b>")
            for r in summary.get("top_symbols", [])[:5]:
                pnl = r.get("net_pnl", 0)
                wr  = r.get("win_rate", 0)
                pf  = r.get("profit_factor", 0)
                lines.append(
                    f"  {r['symbol']}: ₹{pnl:+,.0f} WR={wr:.0f}% PF={pf:.1f} "
                    f"[stop={r.get('best_stop',1.5)}× tgt={r.get('best_target',2.5)}×]"
                )

            # Top 5 strategies
            lines.append("\n⭐ <b>TOP STRATEGIES:</b>")
            for d in summary.get("strategy_ranking", [])[:5]:
                lines.append(
                    f"  {d['strategy']}: ₹{d['pnl']:+,.0f} "
                    f"WR={d['win_rate']:.0f}% ({d['trades']} trades)"
                )

            # Weak strategies
            strats = summary.get("strategy_ranking", [])
            weak = [d for d in strats if d["win_rate"] < 40 and d["trades"] >= 10]
            if weak:
                lines.append(f"\n⚠️ <b>WEAK STRATEGIES (consider disabling):</b>")
                for d in weak[:3]:
                    lines.append(
                        f"  {d['strategy']}: WR={d['win_rate']:.0f}% "
                        f"₹{d['pnl']:+,.0f}"
                    )

            # Params updated
            if summary.get("params_updated"):
                lines.append(f"\n🔧 <b>PARAMS UPDATED ({len(summary['params_updated'])}):</b>")
                for p in summary["params_updated"][:3]:
                    lines.append(
                        f"  {p['symbol']}: stop {p['old_stop']}→{p['new_stop']}× "
                        f"tgt {p['old_target']}→{p['new_target']}×"
                    )

            lines.append(f"\n💡 symbol_params.json updated for live use")

            msg = "\n".join(lines)
            self._alerts.send(msg, dedup_key=f"backtest_{summary['date']}")
        except Exception as e:
            logger.debug("Telegram backtest report: %s", e)


# ── Singleton ─────────────────────────────────────────────────────────────────
_bt: Optional[AutonomousBacktest] = None
def get_backtest(alerts=None) -> AutonomousBacktest:
    global _bt
    if _bt is None:
        _bt = AutonomousBacktest(alerts)
    if alerts and not _bt._alerts:
        _bt._alerts = alerts
    return _bt
