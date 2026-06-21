#!/usr/bin/env python3
"""
capital_simulation.py — honest capital-curve simulation (audit gap #10).

Replays the system's OWN decided signals (signal_log.db) at several capital
levels with realistic costs, so you can see the *honest* equity curve BEFORE
risking a rupee. The capital levels genuinely differ because FIXED brokerage is
a much larger %-drag on small capital than on large.

This makes NO edge claim — it replays whatever the signals actually did. Given
the measured negative/absent edge, expect the curves to bleed; that is the point
(capital-preservation evidence), per CLAUDE.md rule 5.

Per-trade model (sequential, compounding on equity):
    deployed   = risk_frac * equity
    gross_pnl  = deployed * r                       # r = realised per-trade return
    prop_cost  = deployed * (cost_bps/1e4)          # slippage+STT+charges on turnover
    fixed_cost = brokerage                          # absolute ₹/round-trip
    equity    += gross_pnl - prop_cost - fixed_cost

Usage:
    python capital_simulation.py
    python capital_simulation.py --capitals 50000 100000 500000 1000000 \
        --risk-frac 0.2 --cost-bps 20 --brokerage 40 --days 90 --gap-pct 0
"""
from __future__ import annotations

import argparse
import sqlite3
from typing import Dict, List, Optional, Tuple

DB_PATH = "signal_log.db"


def load_trade_returns(
    db_path: str = DB_PATH,
    days: Optional[int] = None,
    max_abs_return: float = 3.0,
) -> List[float]:
    """Per-trade realised returns from decided signals. r > 0 = profit.
    Drops scale-corrupt rows (|r| > max_abs_return) and non-positive prices —
    the same class of corruption signal_quality.py guards against."""
    where = ["tb_label IN (1, -1)", "entry_price > 0", "outcome_price > 0"]
    if days:
        where.append(f"signal_date >= date('now', '-{int(days)} day')")
    sql = (
        "SELECT side, entry_price, outcome_price FROM signal_log WHERE "
        + " AND ".join(where)
        + " ORDER BY signal_date, signal_time"
    )
    try:
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(sql).fetchall()
        finally:
            conn.close()
    except Exception:
        return []  # missing / corrupt DB → degrade gracefully (chaos-safe)

    returns: List[float] = []
    for side, entry, out in rows:
        if not entry or entry <= 0:
            continue
        raw = (out - entry) / entry
        if str(side).upper() in ("SELL", "SHORT", "PE"):
            raw = -raw
        if abs(raw) <= max_abs_return:
            returns.append(float(raw))
    return returns


def simulate(
    returns: List[float],
    capital: float,
    risk_frac: float = 0.2,
    cost_bps: float = 20.0,
    brokerage: float = 40.0,
    gap_pct: float = 0.0,
) -> Dict[str, float]:
    """Run one equity curve. gap_pct (>=0) applies an extra adverse haircut to
    every LOSING trade — a crude proxy for gap risk making losses worse."""
    equity = float(capital)
    peak = equity
    max_dd = 0.0
    wins = 0
    curve_min = equity
    for r in returns:
        if gap_pct and r < 0:
            r -= abs(gap_pct)
        deployed = risk_frac * equity
        gross = deployed * r
        prop_cost = deployed * (cost_bps / 1e4)
        equity += gross - prop_cost - brokerage
        if equity <= 0:
            equity = 0.0
            curve_min = 0.0
            break
        wins += 1 if (gross - prop_cost - brokerage) > 0 else 0
        peak = max(peak, equity)
        curve_min = min(curve_min, equity)
        dd = (peak - equity) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
    n = len(returns)
    return {
        "capital":       capital,
        "n_trades":      n,
        "final_equity":  round(equity, 2),
        "total_return":  round((equity / capital - 1.0) * 100, 2) if capital else 0.0,
        "max_drawdown":  round(max_dd * 100, 2),
        "win_rate":      round(100.0 * wins / n, 1) if n else 0.0,
        "ruined":        equity <= 0.0,
    }


def run(
    capitals: List[float],
    db_path: str = DB_PATH,
    days: Optional[int] = None,
    risk_frac: float = 0.2,
    cost_bps: float = 20.0,
    brokerage: float = 40.0,
    gap_pct: float = 0.0,
) -> Tuple[List[Dict[str, float]], int]:
    returns = load_trade_returns(db_path, days=days)
    results = [
        simulate(returns, c, risk_frac, cost_bps, brokerage, gap_pct)
        for c in capitals
    ]
    return results, len(returns)


def _print(results: List[Dict[str, float]], n: int,
           risk_frac: float, cost_bps: float, brokerage: float, gap_pct: float) -> None:
    print(f"\nCapital simulation on {n} decided signals "
          f"(risk_frac={risk_frac}, cost={cost_bps}bps, brokerage=₹{brokerage:g}/trade, "
          f"gap_pct={gap_pct})")
    if n == 0:
        print("  (no decided trades found — labels may not have accrued yet)")
        return
    hdr = f"  {'capital':>12s} {'final_equity':>14s} {'return%':>9s} {'max_dd%':>9s} {'win%':>6s} {'ruin':>5s}"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for r in results:
        print(
            f"  {('₹%.0f' % r['capital']):>12s} {('₹%.0f' % r['final_equity']):>14s} "
            f"{r['total_return']:>9.2f} {r['max_drawdown']:>9.2f} {r['win_rate']:>6.1f} "
            f"{('YES' if r['ruined'] else 'no'):>5s}"
        )
    print("\n  NOTE: replays real signals — NOT a profit projection. Negative is the")
    print("  honest result of the measured edge; plan finances assuming ₹0 profit.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Honest capital-curve simulation")
    ap.add_argument("--capitals", type=float, nargs="+",
                    default=[50000, 100000, 500000, 1000000])
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--risk-frac", type=float, default=0.2)
    ap.add_argument("--cost-bps", type=float, default=20.0)
    ap.add_argument("--brokerage", type=float, default=40.0)
    ap.add_argument("--gap-pct", type=float, default=0.0,
                    help="Extra adverse return applied to losing trades (e.g. 0.02 = 2%%)")
    args = ap.parse_args()

    results, n = run(
        capitals=args.capitals, db_path=args.db, days=args.days,
        risk_frac=args.risk_frac, cost_bps=args.cost_bps,
        brokerage=args.brokerage, gap_pct=args.gap_pct,
    )
    _print(results, n, args.risk_frac, args.cost_bps, args.brokerage, args.gap_pct)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
