"""
R002 random-entry CONTROL (requested by the user 2026-10-17). DESCRIPTIVE ONLY:
not a hypothesis test, not added to the test count (40). R002 stays "proposed";
R001 is untouched.

On each of R002's trading days (first short signal of the day from C002, C004,
C006, C007 version B or H-D), R002's entry is replaced by a SHORT entry at a
uniformly random 5m candle whose start time is 09:20-14:00 (fill = that
candle's open), one trade per day, ATM put, same costs, and the same exit
rules: E2 (first 5m close above the 1m 200 EMA), and for comparison REF20
(5m 20 EMA), REF50 (5m 50 EMA), E3 (5m 200 EMA); forced exit at the 15:20
candle. 1,000 draws with a fixed seed. R002's real result is placed in each
distribution as a percentile.

Speed without changing the model: for every day and every candidate entry
candle the exit and the modelled option P&L are computed ONCE with the same
exit code (exits_test.simulate_exit), the same DayPricer (option_intraday_pricer,
IV solved from the previous session's settle, cached per day/strike) and the
same cost function (options_layers.option_net_pnl); the draws then sample
from that table.
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

SEED = 20261017
N_DRAWS = 1000
CODES = ("E2", "REF20", "REF50", "E3")
DRAW_FIRST, DRAW_LAST = pd.Timestamp("09:20").time(), pd.Timestamp("14:00").time()
OUT_DIR = Path(__file__).resolve().parent / "results" / "r002_control"


class CachedPricer:
    """DayPricer cached per (prev session, expiry, strike, type) -- identical model, solved once."""

    def __init__(self, cfg: dict, spot_close: Dict[str, float]):
        from .options_layers import ModelledLayer
        self.mod = ModelledLayer(cfg, spot_close)
        self.cache: Dict = {}

    def price(self, d: str, strike: float, opt: str, when: datetime, spot: float) -> Optional[float]:
        from option_intraday_pricer import DayPricer
        prev, expiry = self.mod._prev_date(d), self.mod.expiry_for(d)
        if not prev or not expiry:
            return None
        key = (prev, expiry, float(strike), opt)
        if key not in self.cache:
            q = self.mod._settle(prev, expiry, float(strike), opt)
            pr = DayPricer(q[1], float(strike), date.fromisoformat(prev), date.fromisoformat(expiry), opt, q[0]) if q else None
            self.cache[key] = pr if (pr is not None and pr.valid) else None
        pr = self.cache[key]
        return pr.price_at(when, spot) if pr is not None else None


def modelled_net(t, cfg: dict, pricer: CachedPricer) -> Optional[float]:
    from .options_layers import option_net_pnl
    k = round(t.entry_px / float(cfg["options"]["strike_step"])) * float(cfg["options"]["strike_step"])
    opt = "PE" if t.side == "SHORT" else "CE"
    pin = pricer.price(t.date, k, opt, datetime.strptime(t.entry_time, "%Y-%m-%d %H:%M"), t.entry_px)
    pout = pricer.price(t.date, k, opt, datetime.strptime(t.exit_time, "%Y-%m-%d %H:%M"), t.exit_px)
    if not pin or pout is None:
        return None
    return float(option_net_pnl(pin, pout, date.fromisoformat(t.date), cfg)[0])


def r002_days(prep: Dict, cfg: dict, conditions: List[dict]) -> pd.DataFrame:
    """R002's real entries: earliest short signal per day across its five sources."""
    E = xt.b_entries(prep, cfg, conditions)
    S = E[E.source.isin(["C002", "C004", "C006", "C007", "H-D"])]
    return S.sort_values("sig_i").groupby("date", as_index=False).first()[["date", "sig_i", "source"]]


def build_table(prep: Dict, cfg: dict, days: pd.DataFrame, pricer: CachedPricer) -> pd.DataFrame:
    """One row per (day, entry candle, exit code): points and modelled option net."""
    atr = xt.wilder_atr(prep["frame"])
    t_force = pd.Timestamp(cfg["session"]["force_exit_bar"]).time()
    rows = []
    for _, r in days.iterrows():
        d = r.date
        day, lev = prep["by_day"][d], prep["levels"].loc[d]
        real_entry = int(r.sig_i) + 1
        cands = [i for i, ts in enumerate(day.index) if i >= 1 and ts.time() < t_force
                 and (DRAW_FIRST <= ts.time() <= DRAW_LAST or i == real_entry)]
        for i in cands:
            for code in CODES:
                t = xt.simulate_exit(day, lev, i - 1, -1, code, float(atr.loc[day.index[i - 1]]), cfg)
                rows.append({"date": d, "entry_i": i, "entry_time": day.index[i].strftime("%H:%M"),
                             "in_draw_window": DRAW_FIRST <= day.index[i].time() <= DRAW_LAST,
                             "is_real": i == real_entry, "code": code, "points": t.points,
                             "B_net": modelled_net(t, cfg, pricer)})
    return pd.DataFrame(rows)


def pf(x: np.ndarray) -> float:
    loss = -x[x <= 0].sum()
    return float(x[x > 0].sum() / loss) if loss > 0 else float("inf")


def draws(table: pd.DataFrame, code: str, n: int = N_DRAWS, seed: int = SEED) -> Dict[str, np.ndarray]:
    """n random-entry draws: one uniformly chosen in-window candle per day."""
    T = table[(table.code == code) & table.in_draw_window]
    groups = [g for _, g in T.groupby("date")]
    pts = [g.points.to_numpy() for g in groups]
    bn = [g.B_net.to_numpy(dtype=float) for g in groups]
    rng = np.random.default_rng(seed)
    out_pf, out_b, out_n = np.empty(n), np.empty(n), np.empty(n)
    for k in range(n):
        idx = [rng.integers(0, len(p)) for p in pts]
        p = np.array([pts[j][i] for j, i in enumerate(idx)])
        b = np.array([bn[j][i] for j, i in enumerate(idx)])
        out_pf[k], out_b[k], out_n[k] = pf(p), float(np.nansum(b)), len(p)
    return {"pf": out_pf, "B": out_b, "n": out_n}


def summarize_dist(x: np.ndarray, actual: float) -> Dict:
    return {"actual": round(float(actual), 2), "percentile": round(float((x < actual).mean() * 100), 1),
            "median": round(float(np.median(x)), 2), "p5": round(float(np.percentile(x, 5)), 2),
            "p95": round(float(np.percentile(x, 95)), 2), "min": round(float(x.min()), 2), "max": round(float(x.max()), 2)}


def run(prep: Dict, cfg: dict, conditions: List[dict]) -> Dict:
    days = r002_days(prep, cfg, conditions)
    pricer = CachedPricer(cfg, prep["spot_close"])
    table = build_table(prep, cfg, days, pricer)
    res = {"seed": SEED, "draws": N_DRAWS, "days": int(len(days)),
           "draw_window": "entry candle start 09:20-14:00, uniform", "counted_as_test": False, "exits": {}}
    for code in CODES:
        real = table[(table.code == code) & table.is_real]
        D = draws(table, code)
        res["exits"][code] = {"real_trades": int(len(real)),
                              "pf": summarize_dist(D["pf"], pf(real.points.to_numpy())),
                              "B_net_rs": summarize_dist(D["B"], float(np.nansum(real.B_net.to_numpy(dtype=float))))}
    res["_table"] = table
    return res


def save(res: Dict) -> Path:
    table = res.pop("_table")
    latest = OUT_DIR / "latest"
    if (latest / "control.json").exists():
        prev = json.loads((latest / "control.json").read_text())
        hist = OUT_DIR / "history" / prev["generated_at"].replace(":", "")
        hist.mkdir(parents=True, exist_ok=True)
        for f in latest.iterdir():
            shutil.copy2(f, hist / f.name)
    latest.mkdir(parents=True, exist_ok=True)
    res["generated_at"] = datetime.now().isoformat(timespec="seconds")
    (latest / "control.json").write_text(json.dumps(res, indent=2, default=str))
    table.to_csv(latest / "entry_exit_table.csv", index=False)
    return latest
