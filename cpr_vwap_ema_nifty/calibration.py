"""
Modelled (layer B) vs real (layer C) option P&L calibration (2026-10-17).

Every trade in this package's result files that has BOTH a modelled and a
real-price option P&L is collected once (de-duplicated on date, entry time,
exit time, strike and option type) and compared, overall, by option type
(CE / PE) and for winning vs losing trades. Descriptive; it changes nothing in
the model. The forward report prints the one-line summary so the gap is
tracked as real snapshots accumulate.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import pandas as pd

RESULTS = Path(__file__).resolve().parent / "results"
SOURCES = ("latest/trades.csv", "latest/conditions_trades.csv", "latest/exit_test_trades.csv",
           "latest/h_d_short_trades.csv", "confirmation/latest/confirmation_trades.csv",
           "forward/R001_trades.csv")
KEYS = ("date", "entry_time", "exit_time", "strike", "opt_type")


def collect(root: Path = RESULTS) -> pd.DataFrame:
    frames: List[pd.DataFrame] = []
    for rel in SOURCES:
        p = root / rel
        if not p.exists():
            continue
        try:
            df = pd.read_csv(p, low_memory=False)
        except Exception:
            continue
        if not {"B_net", "C_net", "points", *KEYS} <= set(df.columns):
            continue
        df = df[df.B_net.notna() & df.C_net.notna()][list(KEYS) + ["B_net", "C_net", "points"]]
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=list(KEYS) + ["B_net", "C_net", "points"])
    return pd.concat(frames, ignore_index=True).drop_duplicates(subset=list(KEYS))


def _block(df: pd.DataFrame) -> Dict:
    if df.empty:
        return {"trades": 0}
    diff = df.C_net - df.B_net
    return {"trades": int(len(df)), "B_total": round(float(df.B_net.sum()), 2),
            "C_total": round(float(df.C_net.sum()), 2),
            "real_minus_model_per_trade": round(float(diff.mean()), 2),
            "real_over_model_total": round(float(df.C_net.sum() / df.B_net.sum()), 2) if df.B_net.sum() else None,
            "same_sign_pct": round(float(((df.B_net > 0) == (df.C_net > 0)).mean() * 100), 1)}


def report(root: Path = RESULTS) -> Dict:
    df = collect(root)
    out = {"all": _block(df)}
    for opt in ("CE", "PE"):
        out[opt] = _block(df[df.opt_type == opt])
    out["winners"] = _block(df[df.points > 0])
    out["losers"] = _block(df[df.points <= 0])
    return out


def line(rep: Dict) -> str:
    a = rep.get("all", {})
    if not a.get("trades"):
        return "Model calibration: no trades with both modelled and real option prices yet."
    parts = [f"Model calibration on {a['trades']} real-priced trades: real minus model "
             f"{a['real_minus_model_per_trade']:+,.0f} Rs/trade"]
    for opt in ("CE", "PE"):
        b = rep.get(opt, {})
        if b.get("trades"):
            parts.append(f"{opt} {b['real_minus_model_per_trade']:+,.0f} (n={b['trades']})")
    return "; ".join(parts) + "."
