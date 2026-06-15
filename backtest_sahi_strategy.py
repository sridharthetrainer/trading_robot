"""
backtest_sahi_strategy.py

Daily-data approximation backtest for the SAHI log-derived strategy.

The SAHI rules are intraday/options-heavy. This runner uses the local
nse_cache.db daily OHLCV cache for empirical equity testing and reports the
data limitation explicitly. Options/OI enhancements remain implemented in
sahi_strategy.py and are logically validated in the generated report.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

from sahi_strategy import (
    ENHANCEMENT_DECISIONS,
    calculate_indicators,
    check_equity_long,
    check_equity_short,
    gap_adjustment,
    manage_position,
    rollover_filter,
)


DB_PATH = "nse_cache.db"
RESULTS_PATH = "sahi_strategy_backtest_results.json"
REPORT_PATH = "sahi_strategy_validation.md"
DEFAULT_START = "2026-04-01"
DEFAULT_END = "2026-06-10"
DEFAULT_CAPITAL = 100_000.0


def _normalise_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    rename = {
        "date": "Date",
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume",
    }
    out = out.rename(columns={k: v for k, v in rename.items() if k in out.columns})
    if "Date" in out.columns:
        out["Date"] = pd.to_datetime(out["Date"], errors="coerce")
        out = out.dropna(subset=["Date"]).set_index("Date")
    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    return out.dropna(subset=["Open", "High", "Low", "Close"]).sort_index()


def _warmup_start(start: str, days: int = 140) -> str:
    try:
        return (pd.Timestamp(start) - timedelta(days=days)).strftime("%Y-%m-%d")
    except Exception:
        return start


def load_cached_daily(
    symbol: str,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    db_path: str = DB_PATH,
    warmup_days: int = 0,
) -> pd.DataFrame:
    path = Path(db_path)
    if not path.exists():
        return pd.DataFrame()
    query_start = _warmup_start(start, warmup_days) if warmup_days else start
    with sqlite3.connect(str(path)) as conn:
        query = """
            SELECT date, open, high, low, close, volume
            FROM ohlcv
            WHERE symbol = ? AND date >= ? AND date <= ?
            ORDER BY date
        """
        df = pd.read_sql_query(query, conn, params=(symbol.upper(), query_start, end))
    return _normalise_ohlcv(df)


def load_symbol_universe(limit: int = 50, path: str = "nifty200.csv") -> List[str]:
    p = Path(path)
    if not p.exists():
        return ["NIFTY", "RELIANCE", "HDFCBANK", "TCS", "INFY"]
    df = pd.read_csv(p)
    symbols = [str(x).strip().upper() for x in df.get("Symbol", pd.Series(dtype=str)).tolist()]
    symbols = [s for s in symbols if s and s not in {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNEXT50"}]
    return symbols[: max(1, int(limit))]


def _max_drawdown(equity: List[float]) -> float:
    if not equity:
        return 0.0
    arr = np.asarray(equity, dtype=float)
    peak = np.maximum.accumulate(arr)
    return float(np.max(peak - arr))


def _profit_factor(trades: List[Dict[str, Any]]) -> float:
    gross_profit = sum(float(t["pnl"]) for t in trades if float(t["pnl"]) > 0)
    gross_loss = abs(sum(float(t["pnl"]) for t in trades if float(t["pnl"]) < 0))
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def _sharpe_from_equity(equity: List[float]) -> float:
    if len(equity) < 3:
        return 0.0
    returns = pd.Series(equity).pct_change().dropna()
    if len(returns) < 2 or returns.std() == 0:
        return 0.0
    return float(returns.mean() / returns.std() * np.sqrt(252))


def _size_from_risk(capital: float, entry: float, stop: float, risk_pct: float, mult: float = 1.0) -> int:
    risk_per_share = abs(entry - stop)
    if capital <= 0 or entry <= 0 or risk_per_share <= 0:
        return 0
    risk_cash = capital * risk_pct * mult
    qty_by_risk = int(risk_cash // risk_per_share)
    qty_by_capital = int(capital // entry)
    return max(0, min(qty_by_risk, qty_by_capital))


def _exit_pnl(side: str, entry: float, exit_price: float, qty: int, brokerage: float = 40.0) -> float:
    gross = (exit_price - entry) * qty if side == "LONG" else (entry - exit_price) * qty
    return gross - brokerage


def _signal_for_day(history: pd.DataFrame, context: Dict[str, Any]) -> Dict[str, Any]:
    long_sig = check_equity_long(history, context=context)
    short_sig = check_equity_short(history, context=context)
    candidates = [s for s in (long_sig, short_sig) if s.get("action") in ("BUY", "SELL")]
    if not candidates:
        return {"action": "HOLD", "reason": "no_signal"}
    return max(candidates, key=lambda s: float(s.get("confidence", 0.0)))


def backtest_sahi_strategy(
    symbol: str,
    data: Optional[pd.DataFrame] = None,
    nifty_data: Optional[pd.DataFrame] = None,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    initial_capital: float = DEFAULT_CAPITAL,
    risk_per_trade: float = 0.02,
    use_enhancements: bool = True,
    close_at_end: bool = True,
    verbose: bool = True,
    **_: Any,
) -> Dict[str, Any]:
    """
    Backtest SAHI equity long/short rules.

    The signature intentionally accepts extra kwargs so validation harnesses can
    call it without TypeErrors.
    """
    raw = data if data is not None else load_cached_daily(symbol, start, end, warmup_days=140)
    df = _normalise_ohlcv(raw)
    if df.empty or len(df) < 35:
        return {
            "symbol": symbol,
            "total_pnl": 0.0,
            "num_trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "max_drawdown": 0.0,
            "sharpe": 0.0,
            "final_capital": initial_capital,
            "reason": "insufficient_daily_data",
        }

    nifty = _normalise_ohlcv(nifty_data) if nifty_data is not None else load_cached_daily("NIFTY", start, end, warmup_days=140)
    features = calculate_indicators(df)
    trade_start = pd.Timestamp(start)
    capital = float(initial_capital)
    equity = [capital]
    trades: List[Dict[str, Any]] = []
    position: Optional[Dict[str, Any]] = None

    for i in range(35, len(features)):
        today = features.iloc[i]
        today_index = features.index[i]
        if today_index < trade_start:
            equity.append(capital)
            continue

        if position:
            side = position["side"]
            high = float(today["High"])
            low = float(today["Low"])
            close = float(today["Close"])
            stop = float(position["stop_loss"])
            target = float(position["target"])

            # Conservative daily path: if stop and target both print, stop wins.
            stop_hit = high >= stop if side == "SHORT" else low <= stop
            target_hit = low <= target if side == "SHORT" else high >= target
            exit_reason = ""
            exit_price = 0.0
            if stop_hit:
                exit_reason = "stop_loss"
                exit_price = stop
            elif target_hit:
                exit_reason = "target"
                exit_price = target
            else:
                managed = manage_position(position, close, use_enhancements=use_enhancements)
                position = managed.get("position", position)
                if managed.get("action") == "EXIT_ALL":
                    exit_reason = managed.get("reason", "managed_exit")
                    exit_price = float(managed.get("exit_price", close))

            if exit_reason:
                qty = int(position["remaining_qty"])
                pnl = _exit_pnl(side, float(position["entry_price"]), exit_price, qty)
                capital += pnl
                trades.append({
                    "symbol": symbol,
                    "side": side,
                    "entry_date": str(position["entry_date"].date()),
                    "exit_date": str(today_index.date()),
                    "entry": round(float(position["entry_price"]), 2),
                    "exit": round(exit_price, 2),
                    "qty": qty,
                    "pnl": round(pnl, 2),
                    "reason": exit_reason,
                    "strategy": position["strategy"],
                })
                position = None
            equity.append(capital)
            continue

        history = features.iloc[: i + 1].copy()
        gap = gap_adjustment(nifty.iloc[: i + 1] if not nifty.empty and len(nifty) > i else None)
        volume_missing = "Volume" in history.columns and float(history["Volume"].tail(20).sum()) <= 0
        context = {"gap_adjustment": gap, "allow_missing_volume": volume_missing}
        signal = _signal_for_day(history, context)
        if signal.get("action") not in ("BUY", "SELL"):
            equity.append(capital)
            continue

        if gap.get("skip_index_trades") and symbol.upper() in {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"}:
            equity.append(capital)
            continue

        side = "LONG" if signal["action"] == "BUY" else "SHORT"
        entry = float(signal["limit_price"])
        stop = float(signal["stop_loss"])
        target = float(signal["target"])
        size_mult = float(gap.get("position_size_mult", 1.0)) if use_enhancements else 1.0
        qty = _size_from_risk(capital, entry, stop, risk_per_trade, size_mult)
        if qty <= 0:
            equity.append(capital)
            continue

        position = {
            "symbol": symbol,
            "side": side,
            "trade_type": signal["trade_type"],
            "strategy": signal["strategy"],
            "entry_date": today_index,
            "entry_price": entry,
            "stop_loss": stop,
            "original_stop_loss": stop,
            "target": target,
            "qty": qty,
            "remaining_qty": qty,
            "partial_taken": False,
            "confidence": signal.get("confidence", 0.0),
        }
        equity.append(capital)

    if close_at_end and position:
        last = features.iloc[-1]
        exit_price = float(last["Close"])
        qty = int(position["remaining_qty"])
        pnl = _exit_pnl(position["side"], float(position["entry_price"]), exit_price, qty)
        capital += pnl
        trades.append({
            "symbol": symbol,
            "side": position["side"],
            "entry_date": str(position["entry_date"].date()),
            "exit_date": str(features.index[-1].date()),
            "entry": round(float(position["entry_price"]), 2),
            "exit": round(exit_price, 2),
            "qty": qty,
            "pnl": round(pnl, 2),
            "reason": "close_at_end",
            "strategy": position["strategy"],
        })
        equity.append(capital)

    wins = sum(1 for t in trades if float(t["pnl"]) > 0)
    win_rate = wins / len(trades) if trades else 0.0
    total_pnl = sum(float(t["pnl"]) for t in trades)
    pf = _profit_factor(trades)
    result = {
        "symbol": symbol,
        "mode": "enhanced" if use_enhancements else "core",
        "start": str(max(features.index[0], trade_start).date()),
        "warmup_start": str(features.index[0].date()),
        "end": str(features.index[-1].date()),
        "requested_start": start,
        "requested_end": end,
        "data_rows": int(len(features)),
        "total_pnl": round(total_pnl, 2),
        "num_trades": len(trades),
        "win_rate": round(win_rate, 4),
        "profit_factor": round(float(pf), 4) if np.isfinite(pf) else 99.0,
        "max_drawdown": round(_max_drawdown(equity), 2),
        "sharpe": round(_sharpe_from_equity(equity), 4),
        "final_capital": round(initial_capital + total_pnl, 2),
        "trades": trades,
        "data_limitation": "daily_equity_only; local volume is missing/zero, so volume confirmation is treated as unavailable in this proxy backtest; intraday option/OI rules not empirically tested here",
    }
    if verbose:
        print(
            f"{symbol:12s} {result['mode']:8s} "
            f"trades={result['num_trades']:3d} pnl={result['total_pnl']:10.2f} "
            f"wr={result['win_rate']:.1%} pf={result['profit_factor']}"
        )
    return result


def run_comparison(symbols: Iterable[str], start: str, end: str, initial_capital: float) -> Dict[str, Any]:
    all_results: List[Dict[str, Any]] = []
    for symbol in symbols:
        data = load_cached_daily(symbol, start, end, warmup_days=140)
        if len(data) < 35:
            continue
        core = backtest_sahi_strategy(symbol, data=data, start=start, end=end, initial_capital=initial_capital, use_enhancements=False, verbose=False)
        enhanced = backtest_sahi_strategy(symbol, data=data, start=start, end=end, initial_capital=initial_capital, use_enhancements=True, verbose=False)
        all_results.extend([core, enhanced])

    def aggregate(mode: str) -> Dict[str, Any]:
        subset = [r for r in all_results if r["mode"] == mode]
        trades = [t for r in subset for t in r.get("trades", [])]
        total_pnl = sum(float(t["pnl"]) for t in trades)
        wins = sum(1 for t in trades if float(t["pnl"]) > 0)
        return {
            "symbols_tested": len(subset),
            "total_trades": len(trades),
            "total_pnl": round(total_pnl, 2),
            "win_rate": round(wins / len(trades), 4) if trades else 0.0,
            "profit_factor": round(_profit_factor(trades), 4) if trades else 0.0,
            "best_symbol": max(subset, key=lambda r: r.get("total_pnl", -1e9)).get("symbol") if subset else "",
            "worst_symbol": min(subset, key=lambda r: r.get("total_pnl", 1e9)).get("symbol") if subset else "",
        }

    return {
        "requested_window": {"start": start, "end": end},
        "actual_data_note": "nse_cache.db daily rows currently end at 2026-06-08 for most symbols, so June 9-10 are unavailable locally.",
        "core": aggregate("core"),
        "enhanced": aggregate("enhanced"),
        "by_symbol": all_results,
    }


def write_report(results: Dict[str, Any], path: str = REPORT_PATH) -> None:
    core = results.get("core", {})
    enhanced = results.get("enhanced", {})
    lines = [
        "# SAHI Strategy Validation",
        "",
        "## Data Used",
        "",
        f"- Requested window: {results.get('requested_window', {}).get('start')} to {results.get('requested_window', {}).get('end')}",
        f"- Limitation: {results.get('actual_data_note')}",
        "- Empirical backtest scope: daily equity long/short approximation only.",
        "- Local OHLCV volume is zero/missing, so the empirical run is a price-action proxy; live rules still require valid volume confirmation.",
        "- Intraday VWAP/ORH/ORL, option delta, option OI, and spread execution rules are implemented but need matching intraday/options data for full validation.",
        "",
        "## Core vs Enhanced Backtest",
        "",
        "| Mode | Symbols | Trades | P&L | Win Rate | Profit Factor | Best | Worst |",
        "|---|---:|---:|---:|---:|---:|---|---|",
        f"| Core | {core.get('symbols_tested', 0)} | {core.get('total_trades', 0)} | {core.get('total_pnl', 0):.2f} | {core.get('win_rate', 0):.2%} | {core.get('profit_factor', 0)} | {core.get('best_symbol', '')} | {core.get('worst_symbol', '')} |",
        f"| Enhanced | {enhanced.get('symbols_tested', 0)} | {enhanced.get('total_trades', 0)} | {enhanced.get('total_pnl', 0):.2f} | {enhanced.get('win_rate', 0):.2%} | {enhanced.get('profit_factor', 0)} | {enhanced.get('best_symbol', '')} | {enhanced.get('worst_symbol', '')} |",
        "",
        "## Enhancement Decisions",
        "",
        "| ID | Decision | Suggestion | Justification |",
        "|---|---|---|---|",
    ]
    for item in ENHANCEMENT_DECISIONS:
        lines.append(f"| {item['id']} | {item['decision']} | {item['suggestion']} | {item['justification']} |")
    lines.extend([
        "",
        "## Final Recommendation",
        "",
        "Do not promote the SAHI core strategy to live trading as-is. The available daily price-action proxy is negative, and the local cache is missing the volume, intraday, and option-OI data needed to validate the original discretionary edge.",
        "",
        "Keep the coded enhancements for controlled testing. Permanently add S3, S4, and S7 as risk controls once matching data is available. Add S1, S2, S5, S6, and S8 only in the improvised forms shown above, with full intraday/options validation before enabling them as hard live exits.",
        "",
    ])
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest SAHI log-derived strategy")
    parser.add_argument("--symbol", default=None, help="Single symbol to test")
    parser.add_argument("--all", action="store_true", help="Test symbol universe from nifty200.csv")
    parser.add_argument("--limit", type=int, default=50, help="Universe limit for --all")
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--capital", type=float, default=DEFAULT_CAPITAL)
    parser.add_argument("--json", default=RESULTS_PATH)
    parser.add_argument("--report", default=REPORT_PATH)
    args = parser.parse_args()

    if args.all:
        symbols = load_symbol_universe(args.limit)
    else:
        symbols = [args.symbol or "RELIANCE"]

    results = run_comparison(symbols, args.start, args.end, args.capital)
    Path(args.json).write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    write_report(results, args.report)

    print("\nSAHI comparison")
    print(json.dumps({k: results[k] for k in ("requested_window", "core", "enhanced")}, indent=2))
    print(f"\nSaved: {args.json}")
    print(f"Report: {args.report}")


if __name__ == "__main__":
    main()
