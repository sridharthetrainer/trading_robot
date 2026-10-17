"""
Pre-declared test #43 (user, 2026-10-17): R002 hedged as a BEAR PUT SPREAD.

Fixed before running (no other widths): R002's unchanged entries (first short
signal of the day from C002/C004/C006/C007 version B or H-D) and exit (E2: first
5m close above the 1m 200 EMA, else 15:20). Instead of one ATM put:
  long  1 lot ATM put   (strike = entry spot rounded to 50)
  short 1 lot put 200 points lower (4 strikes), same nearest weekly expiry
Costs: nse_cost_model per leg (OPT_BUY for the long leg, OPT_SELL for the short
leg, trade-date STT on the sell side), 1 point premium slippage per side per leg.
Layer B: each strike priced by DayPricer with its own previous-session IV.
Layer C: real snapshot lastPrice for both strikes at the first snapshot at/after
each fill (both legs must be priced). The naked-put P&L on the same trades is
reported alongside. R002 stays proposed; R001 untouched.
"""
from __future__ import annotations

import json
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import exits_test as xt

WIDTH = 200.0
OUT_DIR = Path(__file__).resolve().parent / "results" / "hedge_test"
TEST_KEY = "hedge:R002_bear_put_spread_200"


def leg_costs(buy_px: float, sell_px: float, d: date, cfg: dict, instrument: str) -> float:
    from nse_cost_model import NseCostModel
    qty = int(cfg["options"]["lot_size"]) * int(cfg["options"].get("lots", 1))
    m = NseCostModel()
    return (m.single_leg_cost(buy_px * qty, instrument, "BUY", include_slippage=False, trade_date=d).total
            + m.single_leg_cost(sell_px * qty, instrument, "SELL", include_slippage=False, trade_date=d).total)


def spread_net(long_in: float, long_out: float, short_in: float, short_out: float, d: date, cfg: dict) -> float:
    """Net rupees for: buy long put, sell short put at entry; reverse at exit."""
    qty = int(cfg["options"]["lot_size"]) * int(cfg["options"].get("lots", 1))
    slip = float(cfg["options"]["slippage_points_per_side"])
    lb, ls = long_in + slip, max(0.0, long_out - slip)                 # long leg: buy then sell
    ss, sb = max(0.0, short_in - slip), short_out + slip               # short leg: sell then buy back
    gross = (ls - lb) * qty + (ss - sb) * qty
    costs = leg_costs(lb, ls, d, cfg, "OPT_BUY") + leg_costs(sb, ss, d, cfg, "OPT_SELL")
    return gross - costs


def naked_net(long_in: float, long_out: float, d: date, cfg: dict) -> float:
    from .options_layers import option_net_pnl
    return float(option_net_pnl(long_in, long_out, d, cfg)[0])


def run(prep: Dict, cfg: dict, conditions: List[dict]) -> Dict:
    from .options_layers import ModelledLayer, RealLayer
    from .r002_control import CachedPricer, r002_days
    pricer = CachedPricer(cfg, prep["spot_close"])
    real = RealLayer(cfg)
    real_days = set(real.days())
    days = r002_days(prep, cfg, conditions)
    step = float(cfg["options"]["strike_step"])
    rows = []
    for _, r in days.iterrows():
        d = r.date
        day, lev = prep["by_day"][d], prep["levels"].loc[d]
        t = xt.simulate_exit(day, lev, int(r.sig_i), -1, "E2", 0.0, cfg)
        ds = t.date
        t_in = datetime.strptime(t.entry_time, "%Y-%m-%d %H:%M")
        t_out = datetime.strptime(t.exit_time, "%Y-%m-%d %H:%M")
        k_long = round(t.entry_px / step) * step
        k_short = k_long - WIDTH
        dd = date.fromisoformat(ds)
        rec = {"date": ds, "entry_time": t.entry_time, "exit_time": t.exit_time, "points": t.points,
               "k_long": k_long, "k_short": k_short, "year": d.year,
               "split": "IS" if d <= xt.IS_END else "OOS"}
        bl_in, bl_out = pricer.price(ds, k_long, "PE", t_in, t.entry_px), pricer.price(ds, k_long, "PE", t_out, t.exit_px)
        bs_in, bs_out = pricer.price(ds, k_short, "PE", t_in, t.entry_px), pricer.price(ds, k_short, "PE", t_out, t.exit_px)
        if bl_in and bl_out is not None:
            rec["B_naked"] = naked_net(bl_in, bl_out, dd, cfg)
            if bs_in and bs_out is not None:
                rec["B_spread"] = spread_net(bl_in, bl_out, bs_in, bs_out, dd, cfg)
                rec["B_debit"] = round((bl_in - bs_in) * int(cfg["options"]["lot_size"]), 2)
        if ds in real_days:
            cl_in, _ = real.price(ds, k_long, "PE", t_in)
            cl_out, _ = real.price(ds, k_long, "PE", t_out)
            cs_in, _ = real.price(ds, k_short, "PE", t_in)
            cs_out, _ = real.price(ds, k_short, "PE", t_out)
            if cl_in and cl_out:
                rec["C_naked"] = naked_net(cl_in, cl_out, dd, cfg)
                if cs_in and cs_out:
                    rec["C_spread"] = spread_net(cl_in, cl_out, cs_in, cs_out, dd, cfg)
        rows.append(rec)
    T = pd.DataFrame(rows)
    for c in ("B_naked", "B_spread", "C_naked", "C_spread", "B_debit"):
        if c not in T:
            T[c] = np.nan
    return {"trades": T}


def summarize(T: pd.DataFrame) -> Dict:
    from .backtest import metrics
    both = T[T.B_spread.notna() & T.B_naked.notna()]
    out = {"width_points": WIDTH, "r002_trades": int(len(T)), "priced_both_ways": int(len(both))}
    for col in ("B_naked", "B_spread"):
        out[col] = metrics(both[col].tolist())
        out[col]["IS_total"] = round(float(both[both.split == "IS"][col].sum()), 2)
        out[col]["OOS_total"] = round(float(both[both.split == "OOS"][col].sum()), 2)
        out[col]["day_bootstrap"] = xt.day_bootstrap(both.assign(points=both[col]))
    diff = (both.B_spread - both.B_naked).to_numpy()
    rng = np.random.default_rng(43)
    means = np.array([diff[rng.integers(0, len(diff), len(diff))].mean() for _ in range(5000)]) if len(diff) else np.array([0.0])
    out["spread_minus_naked_per_trade"] = {"mean": round(float(diff.mean()), 2) if len(diff) else None,
                                           "ci95": np.percentile(means, [2.5, 97.5]).round(2).tolist(),
                                           "p_le_0": round(float((means <= 0).mean()), 4)}
    out["avg_debit_rs"] = round(float(both.B_debit.mean()), 2) if len(both) else None
    out["by_year"] = {int(y): {"n": int(len(g)), "naked": round(float(g.B_naked.sum()), 2),
                               "spread": round(float(g.B_spread.sum()), 2)} for y, g in both.groupby("year")}
    c = T[T.C_spread.notna() & T.C_naked.notna()]
    out["C_real"] = {"trades": int(len(c)), "naked_total": round(float(c.C_naked.sum()), 2),
                     "spread_total": round(float(c.C_spread.sum()), 2),
                     "spread_pf": metrics(c.C_spread.tolist()).get("profit_factor") if len(c) else None}
    return out


def update_ledger() -> Dict:
    from .backtest import LEDGER_FILE, _ledger
    led = _ledger()
    new = []
    if TEST_KEY not in led["tests"]:
        led["tests"][TEST_KEY] = {"first_run": datetime.now().strftime("%Y-%m-%d"), "run_id": "hedge_test"}
        new.append(TEST_KEY)
    LEDGER_FILE.write_text(json.dumps(led, indent=2) + "\n")
    return {"total": len(led["tests"]), "new_this_run": new}


def save(summary: Dict, T: pd.DataFrame) -> Path:
    latest = OUT_DIR / "latest"
    if (latest / "hedge.json").exists():
        prev = json.loads((latest / "hedge.json").read_text())
        hist = OUT_DIR / "history" / prev["generated_at"].replace(":", "")
        hist.mkdir(parents=True, exist_ok=True)
        for f in latest.iterdir():
            shutil.copy2(f, hist / f.name)
    latest.mkdir(parents=True, exist_ok=True)
    summary["generated_at"] = datetime.now().isoformat(timespec="seconds")
    (latest / "hedge.json").write_text(json.dumps(summary, indent=2, default=str))
    T.to_csv(latest / "hedge_trades.csv", index=False)
    return latest
