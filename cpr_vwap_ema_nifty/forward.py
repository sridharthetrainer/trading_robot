"""
Forward PAPER evaluation of pre-registered rules (rules.json "preregistered").

Nothing is counted unless the rule's status is "approved" AND approved_on is
set; counting starts at the first session AFTER approved_on. The definition
and bar are read from rules.json and never modified here. Paper only: this
module places no orders and is not called by the live system.

Outputs: results/forward/<id>_trades.csv, <id>_status.json, <id>_status_log.jsonl
"""
from __future__ import annotations

import copy
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd

from . import backtest as bt
from . import data as dm
from . import engine as en
from . import rules as rl
from .options_layers import ModelledLayer, RealLayer

FORWARD_DIR = Path(__file__).resolve().parent / "results" / "forward"


def _pf(x: np.ndarray) -> Optional[float]:
    loss = -x[x <= 0].sum()
    return float(x[x > 0].sum() / loss) if loss > 0 else None


def _dd(x: np.ndarray) -> float:
    if len(x) == 0:
        return 0.0
    eq = np.cumsum(x)
    return float((eq - np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:]).min())


def verdict(trades: pd.DataFrame, bar: Dict) -> Dict:
    """Apply the frozen bar to the counted forward trades (in entry order)."""
    n = len(trades)
    pts = trades["points"].to_numpy(dtype=float) if n else np.array([])
    priced = trades[trades["C_net"].notna()] if n and "C_net" in trades else trades.iloc[0:0]
    c = priced["C_net"].to_numpy(dtype=float) if len(priced) else np.array([])
    out = {"trades": n, "real_priced_trades": int(len(c)),
           "real_price_coverage_pct": round(len(c) / n * 100, 1) if n else None,
           "pf_points": _pf(pts) if n else None,
           "t_stat_points": float(pts.mean() / (pts.std(ddof=1) / np.sqrt(n))) if n > 2 and pts.std(ddof=1) > 0 else None,
           "real_option_net_rs": round(float(c.sum()), 2), "real_option_max_dd_rs": round(_dd(c), 2)}
    dd_lim = bar["fail_immediately_if"]["real_option_drawdown_beyond_rs"]
    if out["real_option_max_dd_rs"] < -dd_lim:
        return {**out, "verdict": "FAIL", "reason": f"real-option drawdown beyond Rs {dd_lim}"}
    k = bar["kill_check_at_trades"]
    if n >= k:
        first = trades.iloc[:k]
        fp = first["points"].to_numpy(dtype=float)
        fc = first["C_net"].dropna().to_numpy(dtype=float) if "C_net" in first else np.array([])
        kill = bar["kill_if_any"]
        if (_pf(fp) or 0) < kill["pf_points_below"] or fc.sum() < kill["real_option_net_below_rs"]:
            return {**out, "verdict": "FAIL", "reason": f"kill check at {k} trades"}
    if n < bar["evaluation_trades"]:
        return {**out, "verdict": "IN_PROGRESS", "reason": f"{n}/{bar['evaluation_trades']} trades"}
    final = trades.iloc[:bar["evaluation_trades"]]
    fv = verdict_final(final, bar)
    return {**out, **fv}


def verdict_final(trades: pd.DataFrame, bar: Dict) -> Dict:
    p = bar["pass_if_all_at_150"]
    pts = trades["points"].to_numpy(dtype=float)
    c = trades["C_net"].dropna().to_numpy(dtype=float)
    checks = {
        "pf_points": (_pf(pts) or 0) >= p["pf_points_min"],
        "t_stat_points": pts.mean() / (pts.std(ddof=1) / np.sqrt(len(pts))) >= p["t_stat_points_min"],
        "real_option_net": c.sum() > p["real_option_net_min_rs"],
        "real_option_drawdown": _dd(c) >= -p["real_option_drawdown_max_rs"],
        "real_price_coverage": len(c) / len(pts) * 100 >= p["real_price_coverage_min_pct"],
    }
    return {"verdict": "PASS" if all(checks.values()) else "FAIL", "checks": checks,
            "reason": "all checks met" if all(checks.values()) else
            "failed: " + ", ".join(k for k, v in checks.items() if not v)}


def snapshot_coverage(sessions, cfg: dict, trade_cov_pct: Optional[float], need_pct: float) -> Dict:
    """Option-chain snapshot recorder coverage for counted sessions, so the
    real-price coverage requirement can be seen at risk before it fails."""
    o = cfg["options"]
    expected = int(o.get("snapshot_expected_per_session", 66))
    partial_below = expected * float(o.get("snapshot_partial_below_pct", 50)) / 100
    counts: Dict[str, int] = {}
    con = RealLayer(cfg).con
    if con is not None and sessions:
        days = [d.isoformat() for d in sessions]
        marks = ",".join("?" * len(days))
        counts = dict(con.execute(
            "SELECT substr(snapshot_time,1,10), COUNT(*) FROM option_chain_snapshots WHERE ok=1 AND "
            f"underlying='NIFTY' AND substr(snapshot_time,12,5) BETWEEN '09:15' AND '15:30' "
            f"AND substr(snapshot_time,1,10) IN ({marks}) GROUP BY 1", days).fetchall())
    none = [d.isoformat() for d in sessions if counts.get(d.isoformat(), 0) == 0]
    partial = [d.isoformat() for d in sessions if 0 < counts.get(d.isoformat(), 0) < partial_below]
    n = len(sessions)
    gap_pct = round((len(none) + len(partial)) / n * 100, 1) if n else 0.0
    buffer_pct = need_pct + 5
    at_risk = bool(n) and (gap_pct > 100 - buffer_pct or (trade_cov_pct is not None and trade_cov_pct < buffer_pct))
    line = (f"Snapshot coverage: {n - len(none) - len(partial)}/{n} sessions full, {len(partial)} partial, "
            f"{len(none)} with no snapshots; trade price coverage "
            f"{'n/a' if trade_cov_pct is None else f'{trade_cov_pct}%'} (need >= {need_pct}%). "
            f"{'AT RISK' if at_risk else 'OK'}")
    return {"sessions": n, "full": n - len(none) - len(partial), "partial_days": partial,
            "no_snapshot_days": none, "gap_pct": gap_pct, "at_risk": at_risk, "line": line}


def evaluate(rule_id: str = "R001", fetch: bool = True, today: Optional[date] = None) -> Dict:
    today = today or date.today()
    rule = next((r for r in rl.load().get("preregistered", []) if r["id"] == rule_id), None)
    if rule is None:
        return {"rule": rule_id, "status": "unknown_rule"}
    if rule.get("status") != "approved" or not rule.get("approved_on"):
        return {"rule": rule_id, "status": "not_approved", "counted_trades": 0,
                "note": "Nothing is counted until the user approves the bar (status=approved, approved_on set)."}
    if not rl.frozen_ok(rule):
        return {"rule": rule_id, "status": "frozen_rule_modified", "counted_trades": 0,
                "note": "definition / engine_mapping / forward_bar no longer match the approved fingerprint; "
                        "a changed rule needs a new id and a fresh forward window."}
    bar = rule["forward_bar"]
    approved = date.fromisoformat(rule["approved_on"])
    cfg = copy.deepcopy(bt.load_config())
    if fetch:
        dm.fetch("spot_1m", today - timedelta(days=40), today, cfg)
        tok_exp = date.fromisoformat(cfg["angel"].get("futures_token_expiry", "2026-10-27"))
        if today <= tok_exp:
            dm.fetch("fut_5m", today - timedelta(days=40), today, cfg)
        else:
            dm.fetch_futures_near_month(max(approved, tok_exp + timedelta(days=1)), today, cfg)
    cfg["window"]["end"] = today.isoformat()
    prep = bt.prepare(cfg)
    m = rule["engine_mapping"]
    cond = {"bias": rule["definition"]["bias"], "context": m["context"], "exit": m["exit"]}
    mod, real = ModelledLayer(cfg, prep["spot_close"]), RealLayer(cfg)
    real_days = set(real.days())
    rows = []
    for d in prep["days"]:
        if d.date() <= approved:
            continue
        day = prep["by_day"].get(d)
        if day is None or len(day) < 60 or not prep["fut_ok"].get(d, False):
            continue
        t = en.simulate_day(day, prep["levels"].loc[d], cond, m["filters"], cfg, "futures")
        if t is None:
            continue
        r = {**t.__dict__}
        r.pop("extra", None)
        r.update(bt._option_layers(t, cfg, mod, real, real_days))
        rows.append(r)
    trades = pd.DataFrame(rows)
    if "C_net" not in trades:
        trades["C_net"] = np.nan
    v = verdict(trades.sort_values("entry_time") if len(trades) else trades, bar)
    sessions = [d.date() for d in prep["days"] if d.date() > approved]
    cov = snapshot_coverage(sessions, cfg, v.get("real_price_coverage_pct"),
                            bar["pass_if_all_at_150"]["real_price_coverage_min_pct"])
    status = {"rule": rule_id, "evaluated_at": datetime.now().isoformat(timespec="seconds"),
              "approved_on": rule["approved_on"], **v, "snapshot_coverage": cov,
              "tests_on_backtest_dataset": bt.ledger_total(),
              "note": "Forward paper trades are not backtest tests and do not add to the test count."}
    FORWARD_DIR.mkdir(parents=True, exist_ok=True)
    trades.to_csv(FORWARD_DIR / f"{rule_id}_trades.csv", index=False)
    (FORWARD_DIR / f"{rule_id}_status.json").write_text(json.dumps(status, indent=2, default=str))
    with open(FORWARD_DIR / f"{rule_id}_status_log.jsonl", "a") as fh:
        fh.write(json.dumps(status, default=str) + "\n")
    return status
