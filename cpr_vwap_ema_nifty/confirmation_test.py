"""
Confirmation-candle variant (declared by the user 2026-10-17): 2 pre-declared
tests, applied EVERY day. R001 (approved, frozen) and R002 (proposed) are NOT
changed; a confirmation version that looks better would become a new proposed
rule (R003).

  Signal candle s closes (R001: long, R002: short, their unchanged signals).
  Wait for candle s+1. Enter at the open of candle s+2 ONLY IF candle s+1 also
  closes on the right side of futures VWAP AND the 1m 200 EMA (long: above
  both; short: below both). Otherwise no trade that day.
  Exits unchanged: R001 = first 5m close below the 5m 20 EMA -> next open;
  R002 = first 5m close above the 1m 200 EMA -> next open; forced 15:20.

Splits: R001 uses its own split (OOS from config out_of_sample_start); R002 uses
the exit-test split (IS <= 2023-12-31). Day-level p = bootstrap over sessions
(one trade per session per rule, so trades = days).
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import exits_test as xt

OUT_DIR = Path(__file__).resolve().parent / "results" / "confirmation"
TESTS = ("confirm:R001_long", "confirm:R002_short")


def confirmed(day: pd.DataFrame, sig_i: int, sign: int) -> bool:
    """Candle sig_i+1 closes on the right side of futures VWAP and the 1m 200 EMA."""
    if sig_i + 2 >= len(day):
        return False
    b = day.iloc[sig_i + 1]
    if pd.isna(b.fut_close) or pd.isna(b.fut_vwap) or pd.isna(b.ema200_1m):
        return False
    return bool(sign * (b.fut_close - b.fut_vwap) > 0 and sign * (b.close - b.ema200_1m) > 0)


def r001_signals(prep: Dict, cfg: dict) -> pd.DataFrame:
    """R001's unchanged signal candle per day (engine C001 base + r1_pdh_break, futures VWAP)."""
    from . import engine as en
    cond = {"bias": "LONG", "context": {"above_vwap": True, "above_ema20_5m": True, "above_ema50_5m": True,
                                        "above_ema200_5m": True, "above_ema200_1m": True},
            "exit": {"type": "close_vs_ema", "timeframe": "5m", "period": 20}}
    rows = []
    for d in prep["days"]:
        day = prep["by_day"].get(d)
        if day is None or len(day) < 60 or not prep["fut_ok"].get(d, False):
            continue
        t = en.simulate_day(day, prep["levels"].loc[d], cond, ["r1_pdh_break"], cfg, "futures")
        if t is None:
            continue
        sig_ts = pd.Timestamp(t.entry_time) - pd.Timedelta(minutes=5)
        rows.append({"date": d, "sig_i": int(day.index.get_loc(sig_ts))})
    return pd.DataFrame(rows)


def run_rule(prep: Dict, cfg: dict, signals: pd.DataFrame, sign: int, exit_code: str, is_end: pd.Timestamp,
             mod, real, real_days) -> pd.DataFrame:
    from . import backtest as bt
    t_force = pd.Timestamp(cfg["session"]["force_exit_bar"]).time()
    rows = []
    for _, s in signals.iterrows():
        d, sig = s.date, int(s.sig_i)
        day, lev = prep["by_day"][d], prep["levels"].loc[d]
        for version, si in (("unconfirmed", sig), ("confirmed", sig + 1)):
            if version == "confirmed" and not confirmed(day, sig, sign):
                rows.append({"date": d, "version": version, "traded": False})
                continue
            if si + 1 >= len(day) or day.index[si + 1].time() >= t_force:
                rows.append({"date": d, "version": version, "traded": False})
                continue
            t = xt.simulate_exit(day, lev, si, sign, exit_code, 0.0, cfg)
            r = {k: v for k, v in t.__dict__.items() if k != "extra"}
            r.update(bt._option_layers(t, cfg, mod, real, real_days))
            r.update({"version": version, "traded": True, "year": d.year,
                      "split": "IS" if d <= is_end else "OOS"})
            rows.append(r)
    T = pd.DataFrame(rows)
    if "C_net" not in T:
        T["C_net"] = np.nan
    return T


def block(T: pd.DataFrame) -> Dict:
    from .backtest import metrics
    if T.empty:
        return {"trades": 0}
    out = {"A_points": metrics(T.points.tolist(), T.hold_min.tolist(), T.mfe.tolist(), T.mae.tolist()),
           "B_modelled_rs": metrics(T.B_net.dropna().tolist()) if T.B_net.notna().any() else {"trades": 0},
           "C_real_rs": metrics(T.C_net.dropna().tolist()) if T.C_net.notna().any() else {"trades": 0},
           "C_coverage": f"{int(T.C_net.notna().sum())}/{len(T)}",
           "day_bootstrap": xt.day_bootstrap(T)}
    for per in ("IS", "OOS"):
        P = T[T.split == per]
        out[per] = {"A_points": metrics(P.points.tolist()) if len(P) else {"trades": 0},
                    "B_net_rs": round(float(P.B_net.sum()), 2), "day_bootstrap": xt.day_bootstrap(P)}
    out["by_year"] = {int(y): {"trades": int(len(g)), "pf": metrics(g.points.tolist()).get("profit_factor"),
                               "B": round(float(g.B_net.sum()), 2)} for y, g in T.groupby("year")}
    return out


def run(prep: Dict, cfg: dict, conditions: List[dict]) -> Dict:
    from .options_layers import ModelledLayer, RealLayer
    from .r002_control import r002_days
    mod, real = ModelledLayer(cfg, prep["spot_close"]), RealLayer(cfg)
    real_days = set(real.days())
    res: Dict = {"declared": "2026-10-17 by the user; applied every day; 2 tests", "rules": {}}
    all_trades = []
    specs = (("R001_long", r001_signals(prep, cfg), 1, "REF20", pd.Timestamp(cfg["out_of_sample_start"]) - pd.Timedelta(days=1)),
             ("R002_short", r002_days(prep, cfg, conditions)[["date", "sig_i"]], -1, "E2", xt.IS_END))
    for name, sig, sign, code, is_end in specs:
        T = run_rule(prep, cfg, sig, sign, code, is_end, mod, real, real_days)
        T.insert(0, "rule", name)
        all_trades.append(T)
        U, C = T[(T.version == "unconfirmed") & T.traded], T[(T.version == "confirmed") & T.traded]
        res["rules"][name] = {"signal_days": int(len(sig)), "unconfirmed": block(U), "confirmed": block(C),
                              "removed_by_filter": int(len(sig) - len(C)),
                              "removed_pct": round(100 * (1 - len(C) / len(sig)), 1) if len(sig) else None,
                              "exit": code, "split_is_end": str(is_end.date())}
    res["_trades"] = pd.concat(all_trades, ignore_index=True)
    return res


def update_ledger() -> Dict:
    from .backtest import LEDGER_FILE, _ledger
    led = _ledger()
    new = []
    for k in TESTS:
        if k not in led["tests"]:
            led["tests"][k] = {"first_run": datetime.now().strftime("%Y-%m-%d"), "run_id": "confirmation_test"}
            new.append(k)
    LEDGER_FILE.write_text(json.dumps(led, indent=2) + "\n")
    return {"total": len(led["tests"]), "new_this_run": new}


def save(res: Dict) -> Path:
    trades = res.pop("_trades")
    latest = OUT_DIR / "latest"
    if (latest / "confirmation.json").exists():
        prev = json.loads((latest / "confirmation.json").read_text())
        hist = OUT_DIR / "history" / prev["generated_at"].replace(":", "")
        hist.mkdir(parents=True, exist_ok=True)
        for f in latest.iterdir():
            shutil.copy2(f, hist / f.name)
    latest.mkdir(parents=True, exist_ok=True)
    res["generated_at"] = datetime.now().isoformat(timespec="seconds")
    (latest / "confirmation.json").write_text(json.dumps(res, indent=2, default=str))
    trades.to_csv(latest / "confirmation_trades.csv", index=False)
    return latest
