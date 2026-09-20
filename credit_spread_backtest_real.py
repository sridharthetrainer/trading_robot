"""
credit_spread_backtest_real.py -- real-premia backtest for single-side
credit spreads (Bull Put Spread / Bear Call Spread, catalog IDs B3/B4 in
option_strategy_registry.py's 42-strategy spec), sharing condor_backtest_
real.py's already-validated real-data infrastructure (options_nifty.db,
same weekly-entry loop, same in/out-of-sample split, same tail-dependence
check) rather than re-deriving it. These are the "PAIR" half of the
standalone-vs-pair option-selling audit (2026-09-20): each short leg is
hedged by a protective long leg one wing further out, same defined-risk
shape as the condor/butterfly, just one side instead of two.

Bull Put Spread (B3): sell a put near spot, buy a further-OTM put as
protection -- profits if NIFTY stays above the short strike (bullish/
neutral bet), same premise as "the market won't crash this week."
Bear Call Spread (B4): sell a call near spot, buy a further-OTM call as
protection -- profits if NIFTY stays below the short strike (bearish/
neutral bet).

Real transaction costs (brokerage per leg + slippage on the 2 legs
crossed), same weekly Monday-ish entry / nearest->=min_dte expiry
selection as condor_backtest_real.py.
"""
from __future__ import annotations

import argparse
import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from condor_backtest_real import (
    _LOT, _load, _prep, _nearest, _leg_price, _metrics, _report,
)

logging.basicConfig(level=logging.WARNING)


def _build_credit_spread(chain: pd.DataFrame, spot: float, otm_pct: float,
                          wing_pts: float, side: str) -> Optional[dict]:
    if chain is None or chain.empty:
        return None
    if side == "put":
        pe = chain[chain["opt_type"] == "PE"]["strike"].to_numpy()
        if not len(pe):
            return None
        short_strike = _nearest(pe, spot * (1 - otm_pct), "put_short")
        if short_strike is None:
            return None
        long_strike = _nearest(pe, short_strike - wing_pts, "put_short")
        if long_strike is None or long_strike >= short_strike:
            return None
        p_short = _leg_price(chain, short_strike, "PE")
        p_long = _leg_price(chain, long_strike, "PE")
    else:  # "call"
        ce = chain[chain["opt_type"] == "CE"]["strike"].to_numpy()
        if not len(ce):
            return None
        short_strike = _nearest(ce, spot * (1 + otm_pct), "call_short")
        if short_strike is None:
            return None
        long_strike = _nearest(ce, short_strike + wing_pts, "call_short")
        if long_strike is None or long_strike <= short_strike:
            return None
        p_short = _leg_price(chain, short_strike, "CE")
        p_long = _leg_price(chain, long_strike, "CE")

    if p_short is None or p_long is None:
        return None
    credit = p_short - p_long
    if credit <= 0:
        return None
    wing = abs(long_strike - short_strike)
    return {"side": side, "short": short_strike, "long": long_strike,
            "credit": credit, "legs_premium": p_short + p_long, "wing": wing}


def _settle_spread(c: dict, s1: float) -> float:
    if c["side"] == "put":
        owed = min(max(c["short"] - s1, 0.0), c["wing"])
    else:
        owed = min(max(s1 - c["short"], 0.0), c["wing"])
    return c["credit"] - owed


def backtest_credit_spread(opt: pd.DataFrame, spot: Dict[str, float], side: str,
                           otm_pct: float, wing_pts: float, min_dte: int,
                           brokerage_leg: float, slippage_pct: float, qty: int,
                           prep=None) -> dict:
    dates, groups, exp_by_date, bisect = prep or _prep(opt)

    trades: List[dict] = []
    seen_weeks = set()
    from datetime import datetime
    for d in dates:
        wk = datetime.strptime(d, "%Y-%m-%d").isocalendar()[:2]
        if wk in seen_weeks or d not in spot:
            continue
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
        c = _build_credit_spread(groups.get((d, target_exp)), spot[d], otm_pct, wing_pts, side)
        if c is None:
            continue
        gross = _settle_spread(c, spot[target_exp])
        slip = slippage_pct * c["legs_premium"]
        costs_per_unit = slip + (brokerage_leg * 2) / qty
        net = gross - costs_per_unit
        max_loss = c["wing"] - c["credit"]
        r = net / max_loss if max_loss > 0 else 0.0
        trades.append({"entry": d, "expiry": target_exp, "credit": round(c["credit"], 2),
                       "net_per_unit": round(net, 2), "r": r,
                       "net_rs": round(net * qty, 2), "max_loss_u": round(max_loss, 2)})
        seen_weeks.add(wk)

    return _report(trades, otm_pct, wing_pts, slippage_pct, qty)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="Real-premia NIFTY credit spread backtest.")
    p.add_argument("--side", choices=["put", "call"], required=True)
    p.add_argument("--otm", type=float, default=0.015)
    p.add_argument("--wing", type=float, default=300)
    p.add_argument("--min-dte", type=int, default=3)
    p.add_argument("--brokerage-leg", type=float, default=20.0)
    p.add_argument("--slippage-pct", type=float, default=0.02)
    p.add_argument("--qty", type=int, default=_LOT)
    p.add_argument("--grid", action="store_true")
    a = p.parse_args(argv)

    opt, spot = _load()
    print(f"loaded {len(opt)} option rows across {opt['date'].nunique()} days; "
          f"{len(spot)} spot dates -- side={a.side}\n")
    if a.grid:
        prep = _prep(opt)
        for otm in (0.01, 0.015, 0.02):
            for wing in (200, 300, 500):
                r = backtest_credit_spread(opt, spot, a.side, otm, wing, a.min_dte,
                                            a.brokerage_leg, a.slippage_pct, a.qty, prep=prep)
                m = r.get("all", {})
                print(f"otm={otm:.1%} wing={wing:>3}: n={m.get('n')} "
                      f"exp_R={m.get('expectancy_R')} totR={m.get('total_R')} "
                      f"OOS_expR={r.get('oos',{}).get('expectancy_R')} "
                      f"PF={m.get('profit_factor')} worst={m.get('worst_R')}")
    else:
        r = backtest_credit_spread(opt, spot, a.side, a.otm, a.wing, a.min_dte,
                                    a.brokerage_leg, a.slippage_pct, a.qty)
        print(r)


if __name__ == "__main__":
    main()
