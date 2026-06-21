"""
condor_backtest_real.py — honest NIFTY iron-condor backtest on REAL option premia.

Unlike backtest_iron_condor.py (which invents the credit and the loss-on-breach),
this uses the actual per-strike option settlement prices backfilled by
options_bhavcopy_backfill.py into options_nifty.db, and the real NIFTY expiry
close to settle each condor at intrinsic value.

RULE (all assumptions explicit; reported in the output)
  - Weekly short iron condor on NIFTY. One entry per ISO-week at that day's close.
  - Sell the nearest expiry with >= min_dte trading days remaining.
  - Short strikes ~otm_pct OTM each side; long wings wing_pts beyond (real strikes).
  - Hold to expiry; settle at the real NIFTY close on the expiry date.
  - Credit = (short_call - long_call) + (short_put - long_put), real premia.
  - Costs: brokerage/leg + bid-ask slippage (% of each leg's premium, the real
    killer) + optional settlement STT on ITM legs.
  - P&L in R-multiples (R = max loss per unit = wing_pts - credit). OOS split,
    profit factor, max drawdown, and explicit tail analysis.

This MEASURES; it places no orders. A defined-risk seller's whole question is
whether the rare max-loss weeks erase the many small-credit weeks — so we report
the worst trades and the dependence of the result on them.

USAGE
  python condor_backtest_real.py                 # default grid + base case
  python condor_backtest_real.py --otm 0.015 --wing 300 --slippage-pct 0.02
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger("condor_backtest_real")

_OPT_DB = Path("options_nifty.db")
_NIFTY_DB = Path("participant_oi.db")      # holds nifty_daily(date, close)
_LOT = 75


# ── data ────────────────────────────────────────────────────────────────────--

def _load() -> Tuple[pd.DataFrame, Dict[str, float]]:
    conn = sqlite3.connect(str(_OPT_DB))
    opt = pd.read_sql_query(
        "SELECT date, expiry, strike, opt_type, close, settle, underlying "
        "FROM options_eod", conn)
    conn.close()
    # spot per date: prefer nifty_daily, else median of chain underlying
    spot: Dict[str, float] = {}
    if _NIFTY_DB.exists():
        c2 = sqlite3.connect(str(_NIFTY_DB))
        try:
            for d, cl in c2.execute("SELECT date, close FROM nifty_daily"):
                spot[d] = float(cl)
        except Exception:
            pass
        c2.close()
    chain_spot = (opt.dropna(subset=["underlying"])
                     .groupby("date")["underlying"].median())
    for d, v in chain_spot.items():
        spot.setdefault(d, float(v))
    return opt, spot


def _trading_dates(opt: pd.DataFrame) -> List[str]:
    return sorted(opt["date"].unique())


# ── one condor ──────────────────────────────────────────────────────────────--

def _nearest(strikes: np.ndarray, target: float, side: str) -> Optional[float]:
    if side == "call_short":          # smallest strike >= target
        c = strikes[strikes >= target]
        return float(c.min()) if len(c) else None
    if side == "put_short":           # largest strike <= target
        c = strikes[strikes <= target]
        return float(c.max()) if len(c) else None
    return None


def _leg_price(chain: pd.DataFrame, strike: float, opt_type: str) -> Optional[float]:
    row = chain[(chain["strike"] == strike) & (chain["opt_type"] == opt_type)]
    if not len(row):
        return None
    px = float(row["close"].iloc[0])
    return px if px > 0 else None


def _build_condor(chain: pd.DataFrame, spot: float,
                  otm_pct: float, wing_pts: float) -> Optional[dict]:
    if chain is None or chain.empty:
        return None
    ce = chain[chain["opt_type"] == "CE"]["strike"].to_numpy()
    pe = chain[chain["opt_type"] == "PE"]["strike"].to_numpy()
    if not len(ce) or not len(pe):
        return None
    sc = _nearest(ce, spot * (1 + otm_pct), "call_short")
    sp = _nearest(pe, spot * (1 - otm_pct), "put_short")
    if sc is None or sp is None:
        return None
    lc = _nearest(ce, sc + wing_pts, "call_short")        # long call further OTM
    lp = _nearest(pe, sp - wing_pts, "put_short")         # long put further OTM
    if lc is None or lp is None or lc <= sc or lp >= sp:
        return None
    psc, plc = _leg_price(chain, sc, "CE"), _leg_price(chain, lc, "CE")
    psp, plp = _leg_price(chain, sp, "PE"), _leg_price(chain, lp, "PE")
    if None in (psc, plc, psp, plp):
        return None
    credit = (psc - plc) + (psp - plp)
    if credit <= 0:
        return None
    return {"short_call": sc, "long_call": lc, "short_put": sp, "long_put": lp,
            "credit": credit, "legs_premium": psc + plc + psp + plp,
            "call_wing": lc - sc, "put_wing": sp - lp}


def _settle(c: dict, s1: float) -> float:
    """Per-unit gross P&L at expiry spot s1 (credit minus intrinsic owed)."""
    call_owed = min(max(s1 - c["short_call"], 0.0), c["call_wing"])
    put_owed = min(max(c["short_put"] - s1, 0.0), c["put_wing"])
    return c["credit"] - call_owed - put_owed


# ── backtest ────────────────────────────────────────────────────────────────--

def _prep(opt: pd.DataFrame):
    """One-time indexing so the per-trade loop is O(1) lookups, not full scans."""
    import bisect
    dates = _trading_dates(opt)
    groups = {k: v for k, v in opt.groupby(["date", "expiry"], sort=False)}
    exp_by_date: Dict[str, list] = {}
    for (d, e) in groups:
        exp_by_date.setdefault(d, []).append(e)
    for d in exp_by_date:
        exp_by_date[d].sort()
    return dates, groups, exp_by_date, bisect


def backtest(opt: pd.DataFrame, spot: Dict[str, float], otm_pct: float,
             wing_pts: float, min_dte: int, brokerage_leg: float,
             slippage_pct: float, qty: int, prep=None) -> dict:
    dates, groups, exp_by_date, bisect = prep or _prep(opt)

    trades: List[dict] = []
    seen_weeks = set()
    for d in dates:
        wk = datetime.strptime(d, "%Y-%m-%d").isocalendar()[:2]
        if wk in seen_weeks or d not in spot:
            continue
        # nearest expiry with >= min_dte trading days remaining (bisect, no scan)
        target_exp = None
        di = bisect.bisect_right(dates, d)
        for e in exp_by_date.get(d, ()):
            if e <= d:
                continue
            if bisect.bisect_right(dates, e) - di >= min_dte:
                target_exp = e
                break
        if target_exp is None or target_exp not in spot:
            continue
        c = _build_condor(groups.get((d, target_exp)), spot[d], otm_pct, wing_pts)
        if c is None:
            continue
        gross = _settle(c, spot[target_exp])
        slip = slippage_pct * c["legs_premium"]            # cross spread, 4 legs
        costs_per_unit = slip + (brokerage_leg * 4) / qty
        net = gross - costs_per_unit
        max_loss = max(c["call_wing"], c["put_wing"]) - c["credit"]
        r = net / max_loss if max_loss > 0 else 0.0
        trades.append({"entry": d, "expiry": target_exp, "credit": round(c["credit"], 2),
                       "net_per_unit": round(net, 2), "r": r,
                       "net_rs": round(net * qty, 2), "max_loss_u": round(max_loss, 2)})
        seen_weeks.add(wk)

    return _report(trades, otm_pct, wing_pts, slippage_pct, qty)


def _metrics(rs: np.ndarray) -> dict:
    if len(rs) == 0:
        return {"n": 0}
    wins = rs[rs > 0]; losses = rs[rs < 0]
    pf = float(wins.sum() / -losses.sum()) if len(losses) and losses.sum() < 0 else float("inf")
    eq = np.cumsum(rs)
    peak = np.maximum.accumulate(eq)
    mdd = float((eq - peak).min())
    return {"n": int(len(rs)), "win_rate": round(float(np.mean(rs > 0)), 3),
            "expectancy_R": round(float(np.mean(rs)), 4),
            "total_R": round(float(rs.sum()), 2),
            "profit_factor": round(pf, 2) if np.isfinite(pf) else None,
            "max_dd_R": round(mdd, 2),
            "best_R": round(float(rs.max()), 2), "worst_R": round(float(rs.min()), 2)}


def _report(trades: List[dict], otm, wing, slip, qty) -> dict:
    if not trades:
        return {"error": "no trades — backfill options first / loosen params"}
    rs = np.array([t["r"] for t in trades])
    split = int(len(rs) * 0.7)
    worst = sorted(trades, key=lambda t: t["r"])[:5]
    # tail dependence: total R with the 3 worst trades removed
    rs_sorted = np.sort(rs)
    total_ex_worst3 = float(rs.sum() - rs_sorted[:3].sum())
    return {
        "params": {"otm_pct": otm, "wing_pts": wing, "slippage_pct": slip, "qty": qty},
        "all": _metrics(rs),
        "in_sample": _metrics(rs[:split]),
        "oos": _metrics(rs[split:]),
        "total_R_excl_worst3": round(total_ex_worst3, 2),
        "worst_trades": [{"entry": t["entry"], "R": round(t["r"], 2),
                          "net_rs": t["net_rs"]} for t in worst],
    }


def format_report(r: dict) -> str:
    if "error" in r:
        return "condor_backtest_real: " + r["error"]
    p = r["params"]
    L = [f"── NIFTY weekly iron condor (REAL premia) ──────────────────────────",
         f"otm={p['otm_pct']:.1%}  wing={p['wing_pts']}pt  slippage={p['slippage_pct']:.1%}/leg  qty={p['qty']}",
         f"ALL : {r['all']}",
         f"IN  : {r['in_sample']}",
         f"OOS : {r['oos']}",
         f"total_R = {r['all'].get('total_R')}  | total_R excluding 3 worst weeks = {r['total_R_excl_worst3']}",
         f"worst weeks: {r['worst_trades']}"]
    a = r["all"]
    if a.get("expectancy_R", 0) <= 0:
        L.append("VERDICT: negative expectancy after costs — NO edge")
    elif r["total_R_excl_worst3"] > a.get("total_R", 0) * 3:
        L.append("VERDICT: positive only on paper — a few weeks carry it; fragile tail")
    elif r["oos"].get("expectancy_R", 0) <= 0:
        L.append("VERDICT: positive in-sample but OOS expectancy <= 0 — NOT robust")
    else:
        L.append("VERDICT: positive expectancy net of costs IN and OOS — worth deeper review (not proven)")
    return "\n".join(L)


def main(argv=None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(description="Honest NIFTY iron-condor backtest.")
    p.add_argument("--otm", type=float, default=0.015)
    p.add_argument("--wing", type=float, default=300)
    p.add_argument("--min-dte", type=int, default=3)
    p.add_argument("--brokerage-leg", type=float, default=20.0)
    p.add_argument("--slippage-pct", type=float, default=0.02)
    p.add_argument("--qty", type=int, default=_LOT)
    p.add_argument("--grid", action="store_true", help="run an otm/wing stability grid")
    a = p.parse_args(argv)

    opt, spot = _load()
    print(f"loaded {len(opt)} option rows across {opt['date'].nunique()} days; "
          f"{len(spot)} spot dates\n")
    if a.grid:
        prep = _prep(opt)
        for otm in (0.01, 0.015, 0.02):
            for wing in (200, 300, 500):
                r = backtest(opt, spot, otm, wing, a.min_dte, a.brokerage_leg,
                             a.slippage_pct, a.qty, prep=prep)
                m = r.get("all", {})
                print(f"otm={otm:.1%} wing={wing:>3}: n={m.get('n')} "
                      f"exp_R={m.get('expectancy_R')} totR={m.get('total_R')} "
                      f"OOS_expR={r.get('oos',{}).get('expectancy_R')} "
                      f"PF={m.get('profit_factor')} worst={m.get('worst_R')}")
    else:
        r = backtest(opt, spot, a.otm, a.wing, a.min_dte, a.brokerage_leg,
                     a.slippage_pct, a.qty)
        print(format_report(r))


if __name__ == "__main__":
    main()
