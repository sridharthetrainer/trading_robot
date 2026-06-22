#!/usr/bin/env python3
"""
run_backtest.py

Unified backtest runner for ALL 28 strategies.
Uses the live signal_engine directly — same logic as the live bot.

Usage:
    python run_backtest.py                         # all strategies, NIFTY, 90 days
    python run_backtest.py --symbol BANKNIFTY      # specific symbol
    python run_backtest.py --days 180              # longer history
    python run_backtest.py --strategy holy_grail   # one strategy only
    python run_backtest.py --from 2025-01-01       # specific date range
    python run_backtest.py --save results.csv      # save to CSV

Results printed to terminal + saved to backtest_results.json
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
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import numpy as np

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

HERE = Path(__file__).parent
os.chdir(HERE)

# ── NSE transaction costs for the index-point/futures proxy ──────────────────
BROKERAGE     = 20.0          # ₹20 per leg
STT_RATE      = 0.0002        # 0.02% futures sell side
EXCHANGE_RATE = 0.00053       # 0.053% turnover
SEBI_RATE     = 0.000001      # 0.0001% turnover
GST_RATE      = 0.18          # 18% on brokerage+exchange+sebi
STAMP_RATE    = 0.00003       # 0.003% buy side
LOT_SIZES = {
    "NIFTY": 65,
    "BANKNIFTY": 30,
    "FINNIFTY": 60,
    "MIDCPNIFTY": 120,
    "SENSEX": 20,
    "BANKEX": 30,
}
DEFAULT_LOT_SIZE = 65


def get_lot_size(symbol: str) -> int:
    sym = (symbol or "NIFTY").upper()
    try:
        from nse_master import get_nse_master
        lot_size = int(get_nse_master().get_lot_size(sym))
        if lot_size > 0:
            return lot_size
    except Exception:
        pass
    return LOT_SIZES.get(sym, DEFAULT_LOT_SIZE)


def calc_charges(entry: float, exit_: float, qty: int, side: str = "BUY") -> dict:
    """Round-trip costs for the index-point/futures proxy, not option premia."""
    entry_tv   = entry  * qty
    exit_tv    = exit_  * qty
    brokerage  = 2 * BROKERAGE
    stt        = exit_tv  * STT_RATE           # sell side only
    exchange   = (entry_tv + exit_tv) * EXCHANGE_RATE / 2
    sebi       = (entry_tv + exit_tv) * SEBI_RATE
    gst        = (brokerage + exchange + sebi) * GST_RATE
    stamp      = entry_tv * STAMP_RATE
    total      = brokerage + stt + exchange + sebi + gst + stamp
    gross      = (exit_ - entry) * qty if side == "BUY" else (entry - exit_) * qty
    return {
        "gross":    round(gross, 2),
        "net":      round(gross - total, 2),
        "brokerage": round(brokerage, 2),
        "stt":      round(stt, 2),
        "exchange": round(exchange, 2),
        "gst":      round(gst, 2),
        "stamp":    round(stamp, 2),
        "total_charges": round(total, 2),
    }


# ── Data fetcher ──────────────────────────────────────────────────────────────
def fetch_data(symbol: str, days: int = 90, interval: str = "5m") -> Optional[pd.DataFrame]:
    """Fetch historical OHLCV. Angel One → yfinance fallback."""
    try:
        df = _get_angel_data_fetcher().get_market_data(symbol, interval=interval, days=days)
        if df is not None and len(df) > 50:
            df.columns = [c.lower() for c in df.columns]
            return df
    except Exception as e:
        logger.debug("DataFetcher error: %s", e)

    # yfinance fallback
    try:
        import yf_compat as yf  # yfinance replaced: Yahoo API broken
        ticker_map = {
            "NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK",
            "FINNIFTY": "NIFTY_FIN_SERVICE.NS",
        }
        ticker = ticker_map.get(symbol.upper(), f"{symbol}.NS")
        end   = datetime.now()
        start = end - timedelta(days=days + 10)
        ivl   = "5m" if days <= 60 else "1d"
        df    = yf.download(ticker, start=start, end=end, interval=ivl,
                            progress=False, auto_adjust=True)
        if df is not None and len(df) > 50:
            df.columns = [c.lower() for c in df.columns]
            return df
    except Exception as e:
        logger.debug("yfinance error: %s", e)
    return None


# ── Signal generator using live signal_engine ─────────────────────────────────
def get_signals_for_bar(
    df_slice: pd.DataFrame,
    df_htf:   Optional[pd.DataFrame],
    symbol: str,
    strategy_filter: Optional[str] = None,
    signal_config: Optional[Dict[str, Any]] = None,
) -> List[dict]:
    """Run signal_engine on a data slice. Returns list of signals."""
    try:
        from signal_engine import generate_signal
        result = generate_signal(
            df              = df_slice,
            df_htf          = df_htf,
            symbol          = symbol,
            capital         = 100000,
            config          = signal_config,
        )
        if result and result.get("direction") and result.get("score", 0) >= 3.5:
            if strategy_filter and strategy_filter not in result.get("strategy","").lower():
                return []
            return [result]
    except Exception as e:
        logger.debug("Signal error: %s", e)
    return []


# ── Backtest engine ───────────────────────────────────────────────────────────
class BacktestEngine:
    """
    Event-driven backtest using live signal_engine signals.
    Uses actual NSE charges. ATR-based stops and targets.
    """

    def __init__(
        self,
        capital:    float = 100_000,
        lots:       int   = 1,
        stop_atr:   float = 1.5,    # stop = entry ± 1.5 × ATR
        target_atr: float = 2.5,    # target = entry ± 2.5 × ATR
        max_hold:   int   = 12,     # max bars to hold (12 × 5min = 1 hour)
        score_threshold: float = 3.5,
        strategy_filter: Optional[str] = None,
    ) -> None:
        self.capital    = capital
        self.lots       = lots
        self.qty        = lots * DEFAULT_LOT_SIZE
        self.stop_atr   = stop_atr
        self.target_atr = target_atr
        self.max_hold   = max_hold
        self.score_threshold = score_threshold
        self.strategy_filter = strategy_filter

        self.trades:    List[dict] = []
        self.equity_curve: List[float] = [capital]
        self.current_capital = capital
        self._open_trade: Optional[dict] = None

    def run(
        self,
        df:     pd.DataFrame,
        df_htf: Optional[pd.DataFrame] = None,
        symbol: str = "NIFTY",
    ) -> "BacktestResult":
        """Run backtest bar by bar."""
        self.qty = self.lots * get_lot_size(symbol)
        df = df.copy()
        df.columns = [c.lower() for c in df.columns]

        # Need at least 100 bars of history before generating signals
        WARMUP = 100
        if len(df) < WARMUP + 10:
            return BacktestResult(symbol=symbol, trades=[], capital=self.capital)

        atr_vals = self._calc_atr(df)

        for bar_idx in range(WARMUP, len(df)):
            current_bar = df.iloc[bar_idx]
            close   = float(current_bar.get("close", 0))
            high    = float(current_bar.get("high", close))
            low     = float(current_bar.get("low", close))
            atr     = float(atr_vals.iloc[bar_idx]) if bar_idx < len(atr_vals) else close * 0.005

            # ── Manage open trade ─────────────────────────────────────────────
            if self._open_trade:
                t        = self._open_trade
                t["bars"] += 1
                exit_price = None
                exit_reason = None

                if t["side"] == "BUY":
                    if low <= t["stop"]:
                        exit_price  = t["stop"]
                        exit_reason = "stop_loss"
                    elif high >= t["target"]:
                        exit_price  = t["target"]
                        exit_reason = "target"
                else:  # SELL
                    if high >= t["stop"]:
                        exit_price  = t["stop"]
                        exit_reason = "stop_loss"
                    elif low <= t["target"]:
                        exit_price  = t["target"]
                        exit_reason = "target"

                if exit_price is None and t["bars"] >= self.max_hold:
                    exit_price  = close
                    exit_reason = "time_exit"

                if exit_price is not None:
                    self._close_trade(t, exit_price, exit_reason, bar_idx, df)
                    self._open_trade = None

            # ── Look for new signal ───────────────────────────────────────────
            if self._open_trade is None:
                df_slice = df.iloc[:bar_idx+1]
                htf_slice = df_htf.iloc[:bar_idx//3+1] if df_htf is not None else None
                signal_config = {"post_confluence_min_score": self.score_threshold}
                signals = get_signals_for_bar(
                    df_slice, htf_slice, symbol, self.strategy_filter, signal_config
                )

                for sig in signals:
                    direction = sig.get("direction")
                    if not direction:
                        continue
                    strategy  = sig.get("strategy","unknown")
                    score     = float(sig.get("score", 5.0))

                    stop   = close - atr * self.stop_atr   if direction == "BUY" else close + atr * self.stop_atr
                    target = close + atr * self.target_atr if direction == "BUY" else close - atr * self.target_atr

                    self._open_trade = {
                        "entry":      close,
                        "stop":       stop,
                        "target":     target,
                        "side":       direction,
                        "strategy":   strategy,
                        "score":      score,
                        "bar_entry":  bar_idx,
                        "bars":       0,
                        "bar_time":   str(df.index[bar_idx]) if hasattr(df.index[bar_idx], '__str__') else "",
                    }
                    break   # one trade at a time

            self.equity_curve.append(self.current_capital)

        # Close any open trade at end
        if self._open_trade:
            last_close = float(df["close"].iloc[-1])
            self._close_trade(self._open_trade, last_close, "eod_close", len(df)-1, df)
            self._open_trade = None

        return BacktestResult(
            symbol    = symbol,
            trades    = self.trades,
            capital   = self.capital,
            equity    = self.equity_curve,
            stop_atr  = self.stop_atr,
            target_atr= self.target_atr,
        )

    def _close_trade(self, t: dict, exit_price: float, reason: str,
                     bar_idx: int, df: pd.DataFrame) -> None:
        charges = calc_charges(t["entry"], exit_price, self.qty, t["side"])
        pnl_net = charges["net"]
        self.current_capital += pnl_net

        self.trades.append({
            "strategy":    t["strategy"],
            "side":        t["side"],
            "entry":       t["entry"],
            "exit":        exit_price,
            "stop":        t["stop"],
            "target":      t["target"],
            "qty":         self.qty,
            "bars_held":   t["bars"],
            "exit_reason": reason,
            "score":       t["score"],
            "gross_pnl":   charges["gross"],
            "net_pnl":     pnl_net,
            "brokerage":   charges["brokerage"],
            "stt":         charges["stt"],
            "gst":         charges["gst"],
            "total_charges": charges["total_charges"],
            "capital_after": round(self.current_capital, 2),
            "bar_time":    t.get("bar_time",""),
        })

    @staticmethod
    def _calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        h = df["high"]; l = df["low"]; c = df["close"]
        tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
        return tr.rolling(period).mean().fillna(c * 0.005)


# ── Results ───────────────────────────────────────────────────────────────────
class BacktestResult:
    def __init__(self, symbol, trades, capital,
                 equity=None, stop_atr=1.5, target_atr=2.5):
        self.symbol     = symbol
        self.trades     = trades
        self.capital    = capital
        self.equity     = equity or [capital]
        self.stop_atr   = stop_atr
        self.target_atr = target_atr

    def summary(self) -> dict:
        if not self.trades:
            return {"total_trades": 0, "note": "no_trades_generated"}

        pnls   = [t["net_pnl"] for t in self.trades]
        gross  = [t["gross_pnl"] for t in self.trades]
        wins   = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        total_pnl    = sum(pnls)
        total_gross  = sum(gross)
        total_charges= sum(t["total_charges"] for t in self.trades)
        win_rate     = len(wins) / len(pnls) * 100

        # Drawdown
        peak = self.capital
        max_dd = 0.0
        running = self.capital
        for p in pnls:
            running += p
            peak = max(peak, running)
            dd   = (peak - running) / peak
            max_dd = max(max_dd, dd)

        # Sharpe (annualised daily)
        if len(pnls) >= 5:
            pnl_series  = pd.Series(pnls)
            daily_ret   = pnl_series / self.capital
            sharpe      = float(daily_ret.mean() / daily_ret.std() * np.sqrt(252)) if daily_ret.std() > 0 else 0
        else:
            sharpe = 0

        # Strategy breakdown
        by_strat = {}
        for t in self.trades:
            s = t["strategy"]
            if s not in by_strat:
                by_strat[s] = {"trades":0,"wins":0,"pnl":0.0,"charges":0.0}
            by_strat[s]["trades"]  += 1
            by_strat[s]["wins"]    += 1 if t["net_pnl"] > 0 else 0
            by_strat[s]["pnl"]     += t["net_pnl"]
            by_strat[s]["charges"] += t["total_charges"]

        for s in by_strat:
            d = by_strat[s]
            d["win_rate"] = round(d["wins"]/d["trades"]*100, 1)
            d["avg_pnl"]  = round(d["pnl"]/d["trades"], 2)

        exit_reasons = {}
        for t in self.trades:
            r = t["exit_reason"]
            exit_reasons[r] = exit_reasons.get(r, 0) + 1

        return {
            "symbol":           self.symbol,
            "total_trades":     len(self.trades),
            "wins":             len(wins),
            "losses":           len(losses),
            "win_rate_pct":     round(win_rate, 1),
            "total_gross_pnl":  round(total_gross, 2),
            "total_charges":    round(total_charges, 2),
            "total_net_pnl":    round(total_pnl, 2),
            "avg_net_pnl":      round(total_pnl / len(pnls), 2),
            "avg_win":          round(sum(wins)/len(wins), 2)    if wins   else 0,
            "avg_loss":         round(sum(losses)/len(losses), 2) if losses else 0,
            "profit_factor":    round(sum(wins)/abs(sum(losses)), 2) if losses else 99.0,
            "max_drawdown_pct": round(max_dd * 100, 2),
            "sharpe_ratio":     round(sharpe, 2),
            "final_capital":    round(self.capital + total_pnl, 2),
            "return_pct":       round(total_pnl / self.capital * 100, 2),
            "exit_reasons":     exit_reasons,
            "by_strategy":      dict(sorted(by_strat.items(),
                                key=lambda x: x[1]["pnl"], reverse=True)),
        }

    def print_report(self) -> None:
        s = self.summary()
        print()
        print("=" * 65)
        print(f"  BACKTEST RESULTS — {s.get('symbol','')}")
        print("=" * 65)

        if s.get("total_trades",0) == 0:
            print(f"  No trades generated. {s.get('note','')}")
            print("  Tips: increase --days, check data availability")
            return

        wr   = s["win_rate_pct"]
        pnl  = s["total_net_pnl"]
        icon = "✅" if pnl > 0 else "❌"

        print(f"\n  {icon} Net P&L:        ₹{pnl:+,.0f}  ({s['return_pct']:+.1f}%)")
        print(f"  📊 Gross P&L:       ₹{s['total_gross_pnl']:+,.0f}")
        print(f"  💸 Total Charges:   ₹{s['total_charges']:,.0f}")
        print(f"     Charges/trade:   ₹{s['total_charges']/max(s['total_trades'],1):.0f}")
        print(f"\n  🎯 Win Rate:        {wr:.1f}%  ({s['wins']}W / {s['losses']}L)")
        print(f"  📈 Avg Win:         ₹{s['avg_win']:+,.0f}")
        print(f"  📉 Avg Loss:        ₹{s['avg_loss']:+,.0f}")
        print(f"  ⚡ Profit Factor:   {s['profit_factor']:.2f}")
        print(f"  📉 Max Drawdown:    {s['max_drawdown_pct']:.1f}%")
        print(f"  📐 Sharpe Ratio:    {s['sharpe_ratio']:.2f}")
        print(f"  💰 Final Capital:   ₹{s['final_capital']:,.0f}")
        print(f"  🔢 Total Trades:    {s['total_trades']}")

        print(f"\n  Exit reasons: {s['exit_reasons']}")

        if s.get("by_strategy"):
            print(f"\n  STRATEGY BREAKDOWN:")
            print(f"  {'Strategy':<30} {'Trades':>6} {'Win%':>6} {'Net P&L':>10} {'Charges':>8}")
            print(f"  {'-'*30} {'-'*6} {'-'*6} {'-'*10} {'-'*8}")
            for strat, d in s["by_strategy"].items():
                print(f"  {strat:<30} {d['trades']:>6} "
                      f"{d['win_rate']:>5.1f}% "
                      f"₹{d['pnl']:>+9,.0f} "
                      f"₹{d['charges']:>7,.0f}")

        # Verdict
        print(f"\n  VERDICT:")
        if pnl > 0 and wr >= 50 and s["profit_factor"] >= 1.5:
            print("  ✅ STRATEGY IS PROFITABLE — suitable for live trading")
        elif pnl > 0 and wr >= 45:
            print("  🟡 BORDERLINE — profitable but needs more data")
        elif pnl < 0:
            print("  ❌ LOSING STRATEGY — do not trade live")
            print("     Consider: different symbol, different time period,")
            print("     or tighter parameters")
        print("=" * 65)


# ── CLI ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="NSE Strategy Backtest Runner")
    parser.add_argument("--symbol",   default="NIFTY",   help="Symbol (NIFTY/BANKNIFTY/etc)")
    parser.add_argument("--days",     type=int, default=90, help="Lookback days")
    parser.add_argument("--strategy", default=None,      help="Filter to one strategy name")
    parser.add_argument("--lots",     type=int, default=1,  help="Number of lots")
    parser.add_argument("--capital",  type=float, default=100000, help="Starting capital")
    parser.add_argument("--stop-atr", type=float, default=1.5, help="Stop distance in ATR")
    parser.add_argument("--target-atr", type=float, default=2.5, help="Target distance in ATR")
    parser.add_argument("--max-hold", type=int, default=12, help="Max bars to hold a trade")
    parser.add_argument("--min-score", type=float, default=3.5, help="Minimum confluence score")
    parser.add_argument("--save",     default=None,       help="Save trades to CSV file")
    parser.add_argument("--json",     default="backtest_results.json", help="Save summary to JSON")
    args = parser.parse_args()

    print(f"\n📊 BACKTEST: {args.symbol} | {args.days} days | "
          f"{'all strategies' if not args.strategy else args.strategy}")
    print("  Fetching data...")

    df = fetch_data(args.symbol, days=args.days)
    if df is None or len(df) < 110:
        print(f"❌ Could not fetch data for {args.symbol}")
        print("  During market hours: Angel One data feeds work")
        print("  After hours: yfinance may be rate-limited")
        print("  Try: --days 60 or different --symbol")
        return

    # 15-min HTF
    df_htf = fetch_data(args.symbol, days=args.days, interval="15m")

    print(f"  Data: {len(df)} bars ({df.index[0] if hasattr(df.index[0],'__str__') else ''} to {df.index[-1] if hasattr(df.index[-1],'__str__') else ''})")
    print("  Running backtest...")

    engine = BacktestEngine(
        capital         = args.capital,
        lots            = args.lots,
        stop_atr        = args.stop_atr,
        target_atr      = args.target_atr,
        max_hold        = args.max_hold,
        score_threshold = args.min_score,
        strategy_filter = args.strategy,
    )
    result = engine.run(df, df_htf, symbol=args.symbol)
    result.print_report()

    # Save JSON
    summary = result.summary()
    summary["backtest_date"] = datetime.now().isoformat()
    summary["params"] = {
        "symbol": args.symbol, "days": args.days,
        "lots": args.lots, "capital": args.capital,
        "stop_atr": args.stop_atr, "target_atr": args.target_atr,
        "max_hold": args.max_hold, "min_score": args.min_score,
    }
    Path(args.json).write_text(json.dumps(summary, indent=2, default=str))
    print(f"\n  Results saved to: {args.json}")

    # Save CSV
    if args.save and result.trades:
        import csv
        with open(args.save, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=result.trades[0].keys())
            writer.writeheader()
            writer.writerows(result.trades)
        print(f"  Trades saved to:  {args.save}")


if __name__ == "__main__":
    main()
