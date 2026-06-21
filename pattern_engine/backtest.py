"""
backtest.py — measure whether detected patterns actually have edge.

For every pattern the engine emits, simulate the trade forward from its
confirmation bar: entry at the pattern's entry, exit at stop or target (whichever
the price reaches first, chronologically, stop-checked-first within a bar), or at
a max-hold timeout. Reports per-pattern AND overall: trades, win rate, avg/total
R-multiple, expectancy, profit factor, max drawdown (R), pseudo-Sharpe, failure
rate, plus a ranking. R = profit / initial-risk, so results are lot/price-agnostic.

This is the gate the README's INTEGRATION guide insists on: do not let a pattern
inform a real trade until it shows a measurable, OOS-robust edge here.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .base import Direction, validate_ohlcv
from .engine import PatternEngine

logger = logging.getLogger("pattern_engine")


def _simulate(df: pd.DataFrame, entry: float, stop: float, target: float,
              direction: Direction, start: int, max_hold: int) -> Optional[float]:
    """Return realised R-multiple, or None if the bar window is invalid."""
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    high = df["high"].to_numpy(float); low = df["low"].to_numpy(float)
    close = df["close"].to_numpy(float)
    n = len(df)
    end = min(n, start + 1 + max_hold)
    for j in range(start + 1, end):
        if direction == Direction.LONG:
            if low[j] <= stop:
                return (stop - entry) / risk
            if high[j] >= target:
                return (target - entry) / risk
        else:
            if high[j] >= stop:
                return (entry - stop) / risk
            if low[j] <= target:
                return (entry - target) / risk
    # timeout → mark out at last close
    j = end - 1
    if j <= start:
        return None
    out = close[j]
    return (out - entry) / risk if direction == Direction.LONG else (entry - out) / risk


def _agg(rs: List[float]) -> Dict:
    if not rs:
        return {"trades": 0}
    a = np.array(rs, float)
    wins = a[a > 0]; loss = a[a < 0]
    eq = np.cumsum(a); peak = np.maximum.accumulate(eq)
    max_dd = float(np.max(peak - eq)) if len(eq) else 0.0
    pf = float(wins.sum() / abs(loss.sum())) if loss.size and loss.sum() != 0 else (99.0 if wins.size else 0.0)
    sharpe = float(a.mean() / a.std() * np.sqrt(len(a))) if a.std() > 0 else 0.0
    return {
        "trades": int(len(a)),
        "win_rate_pct": round(float((a > 0).mean()) * 100, 1),
        "avg_R": round(float(a.mean()), 3),
        "total_R": round(float(a.sum()), 2),
        "expectancy_R": round(float(a.mean()), 3),
        "profit_factor": round(pf, 2),
        "max_drawdown_R": round(max_dd, 2),
        "pseudo_sharpe": round(sharpe, 2),
        "failure_rate_pct": round(float((a < 0).mean()) * 100, 1),
    }


def backtest(df: pd.DataFrame, symbol: str = "", *, engine: Optional[PatternEngine] = None,
             max_hold_bars: int = 40, oos_split: float = 0.6) -> Dict:
    eng = engine or PatternEngine()
    clean = validate_ohlcv(df)
    results = eng.detect(clean, symbol)
    by_pat: Dict[str, List[float]] = defaultdict(list)
    timeline: List[tuple] = []   # (end_index, R)
    for r in results:
        rr = _simulate(clean, r.entry, r.stop_loss, r.target, r.direction,
                       r.end_index, max_hold_bars)
        if rr is None:
            continue
        by_pat[r.pattern].append(rr)
        timeline.append((r.end_index, rr))

    timeline.sort(key=lambda x: x[0])
    seq = [r for _, r in timeline]
    split = int(len(seq) * oos_split)

    per_pattern = {p: _agg(rs) for p, rs in by_pat.items()}
    ranking = sorted(
        [{"pattern": p, **s} for p, s in per_pattern.items() if s.get("trades", 0) > 0],
        key=lambda d: d.get("expectancy_R", -99), reverse=True)
    return {
        "symbol": symbol, "n_patterns": len(seq), "max_hold_bars": max_hold_bars,
        "overall": _agg(seq),
        "in_sample": _agg(seq[:split]),
        "holdout_OOS": _agg(seq[split:]),
        "per_pattern": per_pattern,
        "ranking": ranking,
    }


def format_report(bt: Dict) -> str:
    L = [f"PATTERN BACKTEST — {bt.get('symbol','')} | {bt['n_patterns']} patterns | "
         f"hold≤{bt['max_hold_bars']} bars", "=" * 64]
    o = bt["overall"]
    if o.get("trades", 0):
        L.append(f"OVERALL : trades={o['trades']} win={o['win_rate_pct']}% "
                 f"expectancy={o['expectancy_R']}R PF={o['profit_factor']} "
                 f"maxDD={o['max_drawdown_R']}R sharpe={o['pseudo_sharpe']}")
        h = bt["holdout_OOS"]
        if h.get("trades", 0):
            L.append(f"OOS HOLD: trades={h['trades']} win={h['win_rate_pct']}% "
                     f"expectancy={h['expectancy_R']}R PF={h['profit_factor']}")
    L.append("\nRANKING (by expectancy R):")
    for r in bt["ranking"]:
        L.append(f"  {r['pattern']:24} n={r['trades']:>3} win={r['win_rate_pct']:>5}% "
                 f"exp={r['expectancy_R']:>6}R PF={r['profit_factor']:>5} "
                 f"fail={r['failure_rate_pct']:>5}%")
    return "\n".join(L)
