"""
Backtest runner for CPR_VWAP_EMA_Nifty.

For every condition in rules.json:
  sets      matched (all setup fields equal) / non_matched / all valid days
  variants  base, each optional filter alone, all filters together
  vwap      futures (days without usable futures volume SKIPPED and counted)
            and with_missing (those days kept, using spot TWAP, labelled)
  layers    A index points (no costs) | B modelled ATM option (net Rs)
            | C real option prices (net Rs, 2026-06-19 onward only)
  splits    in-sample / out-of-sample, per-year walk-forward table,
            day type (trending / range) and CPR width breakdowns

Outputs (results/latest, archived to results/history/<run_id>):
  trades.csv, summary.json, summary.md, equity.png
"""
from __future__ import annotations

import json
import logging
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import data as dm
from . import engine as en
from . import rules as rl
from .options_layers import ModelledLayer, RealLayer, option_net_pnl

PKG_DIR = Path(__file__).resolve().parent
CONFIG_FILE = PKG_DIR / "config.json"
RESULTS_DIR = PKG_DIR / "results"
LOG_DIR = PKG_DIR / "logs"
logger = logging.getLogger("cpr_vwap_ema_nifty.backtest")


LEDGER_FILE = PKG_DIR / "test_ledger.json"


def _ledger() -> Dict:
    if LEDGER_FILE.exists():
        return json.loads(LEDGER_FILE.read_text())
    return {"dataset": "NIFTY spot 1m + futures 5m (Angel), sessions 2020-07-01..2026-10-15",
            "counting": "one entry per distinct test looked at on this dataset (variant, sensitivity row, "
                        "field test); re-runs of the same test do not add. Hypotheses and forward paper "
                        "trades are not counted.",
            "tests": {}}


def ledger_total() -> int:
    return len(_ledger()["tests"])


def update_ledger(summary: Dict, run_id: str) -> Dict:
    led = _ledger()
    new = []
    for cid, c in summary.get("conditions", {}).items():
        keys = [f"{cid}|variant:{k.split('|')[0]}" for k in c.get("variants", {}) if k.endswith("|futures")]
        keys += [f"{cid}|sensitivity:{k}" for k in c.get("setup_sensitivity", {})]
        ft = c.get("field_tests", {})
        keys += [f"{cid}|field:{k}" for k in ft if k not in ("base_variant", "tests_run", "reference_no_field_filter")]
        for k in keys:
            if k not in led["tests"]:
                led["tests"][k] = {"first_run": run_id[:10], "run_id": run_id}
                new.append(k)
    pr = summary.get("priority_run", {})
    for name, t in pr.get("tests", {}).items():
        if t.get("status") == "awaiting_definition":
            continue
        k = f"predeclared:{name}"
        if k not in led["tests"]:
            led["tests"][k] = {"first_run": run_id[:10], "run_id": run_id}
            new.append(k)
    for cid in summary.get("conditions_run", {}).get("conditions", {}):
        if cid == "C001":
            continue                         # C001's B is the base rule, already counted
        k = f"conditionB:{cid}"
        if k not in led["tests"]:
            led["tests"][k] = {"first_run": run_id[:10], "run_id": run_id}
            new.append(k)
    looks = led.setdefault("descriptive_looks", {})
    for cid in summary.get("conditions_run", {}).get("conditions", {}):
        for v in ("A", "C_ema20", "C_ema50"):
            looks.setdefault(f"condition{v}:{cid}", {"first_run": run_id[:10], "note": "descriptive version"})
    for lvl in pr.get("priority_focus", {}):
        looks.setdefault(f"priority_focus:{lvl}", {"first_run": run_id[:10],
                                                     "note": "descriptive split, not a pre-declared test"})
    LEDGER_FILE.write_text(json.dumps(led, indent=2) + "\n")
    return {"total": len(led["tests"]), "new_this_run": new, "descriptive_looks": len(looks)}


def load_config(path: Path = CONFIG_FILE) -> dict:
    return json.loads(Path(path).read_text())


def is_enabled_live(cfg: Optional[dict] = None) -> bool:
    cfg = cfg or load_config()
    return bool(cfg.get("enabled_live", False)) and not bool(cfg.get("research_only", True))


# ── metrics ─────────────────────────────────────────────────────────────────

def metrics(pnl: List[float], hold: List[float] = (), mfe: List[float] = (), mae: List[float] = ()) -> Dict:
    p = np.array([x for x in pnl if x is not None and not pd.isna(x)], dtype=float)
    if len(p) == 0:
        return {"trades": 0}
    wins, losses = p[p > 0], p[p <= 0]
    eq = np.cumsum(p)
    dd = float((eq - np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:]).min())
    gl = float(-losses.sum())
    return {
        "trades": int(len(p)),
        "win_rate": round(float(len(wins) / len(p) * 100), 1),
        "avg_win": round(float(wins.mean()), 2) if len(wins) else 0.0,
        "avg_loss": round(float(losses.mean()), 2) if len(losses) else 0.0,
        "profit_factor": round(float(wins.sum() / gl), 2) if gl > 0 else None,
        "expectancy": round(float(p.mean()), 2),
        "total": round(float(p.sum()), 2),
        "max_drawdown": round(dd, 2),
        "avg_hold_min": round(float(np.mean(hold)), 1) if len(hold) else None,
        "avg_mfe_pts": round(float(np.mean(mfe)), 1) if len(mfe) else None,
        "avg_mae_pts": round(float(np.mean(mae)), 1) if len(mae) else None,
    }


# ── run ─────────────────────────────────────────────────────────────────────

def prepare(cfg: dict) -> Dict:
    w, s = cfg["window"], cfg["session"]
    spot, source = dm.load_spot_1m(w["warmup_start"], w["end"], int(s["min_spot_1m_bars"]))
    fut = dm.load_fut_5m(w["warmup_start"], w["end"])
    frame = en.build_frame(spot, fut, cfg)
    lev = en.day_levels(spot, cfg)
    fut_ok = en.futures_day_ok(fut, int(s["min_fut_5m_bars"]))
    start = pd.Timestamp(w["start"])
    days = [d for d in lev.index if d >= start and d <= pd.Timestamp(w["end"])]
    by_day = {d: g for d, g in frame.groupby("date")}
    daily = en.lv.session_ohlc(spot)
    spot_close = {d.strftime("%Y-%m-%d"): float(c) for d, c in daily["close"].items()}
    return {"frame": frame, "levels": lev, "fut_ok": fut_ok, "days": days, "by_day": by_day,
            "spot_source": source, "spot_close": spot_close}


def _option_layers(t: en.Trade, cfg: dict, mod: ModelledLayer, real: RealLayer, real_days: set) -> Dict:
    o = cfg["options"]
    step = float(o["strike_step"])
    opt = "CE" if t.side == "LONG" else "PE"
    k = round(t.entry_px / step) * step
    t_in = datetime.strptime(t.entry_time, "%Y-%m-%d %H:%M")
    t_out = datetime.strptime(t.exit_time, "%Y-%m-%d %H:%M")
    spot_out = t.exit_px
    d = date.fromisoformat(t.date)
    out = {"strike": k, "opt_type": opt}
    expiry = mod.expiry_for(t.date) if mod.con else None
    out["expiry_B"] = expiry
    pin = mod.price(t.date, k, opt, t_in, t.entry_px, expiry)
    pout = mod.price(t.date, k, opt, t_out, spot_out, expiry)
    if pin and pout is not None:
        net, fr = option_net_pnl(pin, pout, d, cfg)
        out.update(B_entry=round(pin, 2), B_exit=round(pout, 2), B_net=round(net, 2), B_friction=round(fr, 2))
    if t.date in real_days:
        cin, exp_c = real.price(t.date, k, opt, t_in)
        cout, _ = real.price(t.date, k, opt, t_out)
        if cin and cout:
            net, fr = option_net_pnl(cin, cout, d, cfg)
            out.update(C_entry=cin, C_exit=cout, C_net=round(net, 2), C_friction=round(fr, 2), expiry_C=exp_c)
            # B priced on the SAME expiry as the real snapshot, for the B-vs-C comparison
            if exp_c and exp_c != expiry:
                pin2 = mod.price(t.date, k, opt, t_in, t.entry_px, exp_c)
                pout2 = mod.price(t.date, k, opt, t_out, spot_out, exp_c)
                if pin2 and pout2 is not None:
                    out["B_net_sameexp"] = round(option_net_pnl(pin2, pout2, d, cfg)[0], 2)
            else:
                out["B_net_sameexp"] = out.get("B_net")
    return out


def run(cfg: Optional[dict] = None, conditions: Optional[List[dict]] = None, prep: Optional[Dict] = None,
        with_options: bool = True) -> pd.DataFrame:
    cfg = cfg or load_config()
    conditions = conditions if conditions is not None else rl.load()["conditions"]
    prep = prep or prepare(cfg)
    lev, by_day, fut_ok = prep["levels"], prep["by_day"], prep["fut_ok"]
    mod, real = (ModelledLayer(cfg, prep.get("spot_close")), RealLayer(cfg)) if with_options else (None, None)
    real_days = set(real.days()) if real else set()
    ratio = float(cfg["day_type_trend_ratio"])
    oos = pd.Timestamp(cfg["out_of_sample_start"])
    rows = []
    for cond in conditions:
        variants = {"base": []}
        for f in cond.get("optional_filters", []):
            variants[f"+{f}"] = [f]
        if cond.get("optional_filters"):
            variants["+all_filters"] = list(cond["optional_filters"])
        for d in prep["days"]:
            day = by_day.get(d)
            if day is None or len(day) < 60:
                continue
            lrow = lev.loc[d]
            matched = en.matches_setup(lrow, cond["setup"])
            has_fut = fut_ok.get(d, False)
            dtype = en.day_type(day, ratio)
            for vname, filters in variants.items():
                for vmode in ("futures", "with_missing"):
                    if vmode == "futures" and not has_fut:
                        continue
                    if vmode == "with_missing" and has_fut:
                        mode = "futures"
                    else:
                        mode = "futures" if vmode == "futures" else "twap"
                    t = en.simulate_day(day, lrow, cond, filters, cfg, mode)
                    if t is None:
                        continue
                    r = {"condition": cond["id"], "variant": vname, "vwap_set": vmode,
                         "matched": matched, "period": "OOS" if d >= oos else "IS", "year": d.year,
                         "day_type": dtype, **{k: lrow[k] for k in en.SETUP_KEYS}, **t.__dict__}
                    r.pop("extra", None)
                    if with_options and vmode == "futures":
                        r.update(_option_layers(t, cfg, mod, real, real_days))
                    rows.append(r)
    return pd.DataFrame(rows)


# ── summaries ───────────────────────────────────────────────────────────────

def _m(df: pd.DataFrame, col: str) -> Dict:
    if df.empty or col not in df:
        return {"trades": 0}
    sub = df[df[col].notna()] if col != "points" else df
    return metrics(sub[col].tolist(), sub["hold_min"].tolist(), sub["mfe"].tolist(), sub["mae"].tolist())


def summarize(trades: pd.DataFrame, prep: Dict, cfg: dict, conditions: List[dict]) -> Dict:
    out = {"generated_at": datetime.now().isoformat(timespec="seconds"),
           "window": cfg["window"], "out_of_sample_start": cfg["out_of_sample_start"],
           "data": {}, "conditions": {}}
    days = prep["days"]
    no_fut = [d for d in days if not prep["fut_ok"].get(d, False)]
    src = pd.Series(prep["spot_source"])
    out["data"] = {
        "sessions_in_window": len(days),
        "sessions_missing_futures_volume": len(no_fut),
        "missing_futures_days": [d.strftime("%Y-%m-%d") for d in no_fut][:50],
        "spot_source_counts": src.value_counts().to_dict(),
    }
    lev = prep["levels"].loc[days]
    for cond in conditions:
        cid = cond["id"]
        T = trades[trades.condition == cid] if not trades.empty else trades
        mask = lev.apply(lambda r: en.matches_setup(r, cond["setup"]), axis=1)
        c = {"setup_matched_sessions": int(mask.sum()),
             "setup_matched_dates": [d.strftime("%Y-%m-%d") for d in lev.index[mask]],
             "variants": {}, "options": {}, "splits": {}, "by_day_type": {}, "by_cpr_width": {},
             "walk_forward": {}}
        for v in sorted(T.variant.unique()) if not T.empty else []:
            for vs in ("futures", "with_missing"):
                S = T[(T.variant == v) & (T.vwap_set == vs)]
                c["variants"][f"{v}|{vs}"] = {
                    "matched": _m(S[S.matched], "points"),
                    "non_matched": _m(S[~S.matched], "points"),
                    "all": _m(S, "points"),
                }
                if vs == "futures":
                    for per in ("IS", "OOS"):
                        P = S[S.period == per]
                        c["variants"][f"{v}|{vs}"][f"{per}_all_points"] = _m(P, "points")
                        c["variants"][f"{v}|{vs}"][f"{per}_all_B_rs"] = _m(P, "B_net")
                    c["variants"][f"{v}|{vs}"]["all_B_rs"] = _m(S, "B_net")
                    c["variants"][f"{v}|{vs}"]["by_year"] = {
                        int(y): {"points": _m(S[S.year == y], "points"), "B_rs": _m(S[S.year == y], "B_net")}
                        for y in sorted(S.year.unique())}
                    c["variants"][f"{v}|{vs}"]["bootstrap_all_points"] = _bootstrap(S["points"].tolist())
                    c["variants"][f"{v}|{vs}"]["all_C_rs"] = _m(S, "C_net") if "C_net" in S else {"trades": 0}
        B = T[(T.variant == "base") & (T.vwap_set == "futures")] if not T.empty else T
        if not B.empty:
            for name, S in (("matched", B[B.matched]), ("non_matched", B[~B.matched]), ("all", B)):
                c["options"][name] = {"A_points": _m(S, "points"), "B_modelled_rs": _m(S, "B_net"),
                                      "C_real_rs": _m(S, "C_net") if "C_net" in S else {"trades": 0}}
            if "C_net" in B:
                both = B[B["C_net"].notna() & B["B_net_sameexp"].notna()]
                c["options"]["B_vs_C_on_real_price_days"] = {
                    "trades": int(len(both)),
                    "B_total": round(float(both["B_net_sameexp"].sum()), 2),
                    "C_total": round(float(both["C_net"].sum()), 2),
                    "mean_abs_diff_per_trade": round(float((both["B_net_sameexp"] - both["C_net"]).abs().mean()), 2)
                    if len(both) else None,
                    "sign_agreement_pct": round(float(((both["B_net_sameexp"] > 0) == (both["C_net"] > 0)).mean() * 100), 1)
                    if len(both) else None,
                }
            for per in ("IS", "OOS"):
                S = B[B.period == per]
                c["splits"][per] = {"matched": _m(S[S.matched], "points"),
                                    "non_matched": _m(S[~S.matched], "points"),
                                    "matched_B_rs": _m(S[S.matched], "B_net"),
                                    "non_matched_B_rs": _m(S[~S.matched], "B_net")}
            for key, col in (("by_day_type", "day_type"), ("by_cpr_width", "cpr_width")):
                for val in sorted(B[col].unique()):
                    S = B[B[col] == val]
                    c[key][val] = {"matched": _m(S[S.matched], "points"), "non_matched": _m(S[~S.matched], "points")}
            for yr in sorted(B.year.unique()):
                S = B[B.year == yr]
                c["walk_forward"][int(yr)] = {"matched": _m(S[S.matched], "points"),
                                              "non_matched": _m(S[~S.matched], "points")}
        c["setup_sensitivity"] = _sensitivity(B, lev, cond) if not B.empty else {}
        c["field_tests"] = _field_tests(T, cond, cfg) if not T.empty else {}
        out["conditions"][cid] = c
    return out


def _field_tests(T: pd.DataFrame, cond: dict, cfg: dict) -> Dict:
    """Pre-declared one-at-a-time field filters on a base variant (config
    'field_tests'). Each test keeps the base variant's trades on sessions whose
    field(s) equal the condition's setup value(s). No combinations."""
    ft = cfg.get("field_tests") or {}
    base = ft.get("base_variant")
    tests = ft.get("tests") or {}
    V = T[(T.variant == base) & (T.vwap_set == "futures")]
    if V.empty or not tests:
        return {}
    out = {"base_variant": base, "tests_run": len(tests),
           "reference_no_field_filter": _block(V)}
    for name, fields in tests.items():
        want = {f: cond["setup"][f] for f in fields}
        mask = np.ones(len(V), dtype=bool)
        for f, v in want.items():
            mask &= (V[f].astype(str) == str(v)).to_numpy()
        out[name] = {"filter": want, **_block(V[mask])}
    return out


def _block(S: pd.DataFrame) -> Dict:
    return {
        "A_points": _m(S, "points"), "B_modelled_rs": _m(S, "B_net"),
        "C_real_rs": _m(S, "C_net") if "C_net" in S else {"trades": 0},
        "IS_A_points": _m(S[S.period == "IS"], "points"), "OOS_A_points": _m(S[S.period == "OOS"], "points"),
        "IS_B_rs": _m(S[S.period == "IS"], "B_net"), "OOS_B_rs": _m(S[S.period == "OOS"], "B_net"),
        "by_year": {int(y): {"points": _m(S[S.year == y], "points"), "B_rs": _m(S[S.year == y], "B_net")}
                    for y in sorted(S.year.unique())},
        "bootstrap_points": _bootstrap(S["points"].tolist()),
    }


def _bootstrap(values: List[float], n: int = 5000, block: int = 5, seed: int = 7) -> Dict:
    """Block bootstrap (blocks of consecutive trades) 95% CI of mean points/trade."""
    x = np.array([v for v in values if not pd.isna(v)], dtype=float)
    if len(x) < 20:
        return {"n": int(len(x)), "note": "too few trades"}
    rng = np.random.default_rng(seed)
    nb = int(np.ceil(len(x) / block))
    means = []
    for _ in range(n):
        starts = rng.integers(0, len(x) - block + 1, nb)
        means.append(np.concatenate([x[s:s + block] for s in starts])[:len(x)].mean())
    lo, hi = np.percentile(means, [2.5, 97.5])
    return {"n": int(len(x)), "mean": round(float(x.mean()), 2), "ci95": [round(float(lo), 2), round(float(hi), 2)],
            "p_mean_le_0": round(float(np.mean(np.array(means) <= 0)), 4)}


def _sensitivity(B: pd.DataFrame, lev: pd.DataFrame, cond: dict) -> Dict:
    """EXPLORATORY, not rules: how the base trade performs when the setup is
    loosened one field at a time, and on the CPR-only / width-only subsets.
    Many looks at the same data -> treat any single good row with suspicion."""
    setup = cond["setup"]
    ladders = {f"drop {k}": {kk: vv for kk, vv in setup.items() if kk != k} for k in setup}
    cpr_only = {k: v for k, v in setup.items() if k.startswith("cpr_")}
    ladders["CPR fields only"] = cpr_only
    ladders["CPR width only"] = {"cpr_width": setup.get("cpr_width")}
    out = {}
    dates = pd.to_datetime(B["date"])
    for name, sub_setup in ladders.items():
        ok_days = {d for d in lev.index if en.matches_setup(lev.loc[d], sub_setup)}
        S = B[dates.isin(ok_days).to_numpy()]
        out[name] = {"sessions": len(ok_days), "A_points": _m(S, "points"),
                     "B_modelled_rs": _m(S, "B_net"),
                     "OOS_A_points": _m(S[S.period == "OOS"], "points")}
    return out


def _fmt(m: Dict, money: bool = False) -> str:
    if not m or not m.get("trades"):
        return "0 trades"
    u = "Rs" if money else "pts"
    pf = m.get("profit_factor")
    return (f"{m['trades']} trades | win {m['win_rate']}% | avg win {m['avg_win']} / loss {m['avg_loss']} {u} | "
            f"PF {pf if pf is not None else 'n/a'} | exp {m['expectancy']} {u} | total {m['total']} | "
            f"maxDD {m['max_drawdown']} | hold {m.get('avg_hold_min')}m | MFE {m.get('avg_mfe_pts')} / "
            f"MAE {m.get('avg_mae_pts')} pts")


def to_markdown(s: Dict, prev: Optional[Dict]) -> str:
    L = [f"# CPR_VWAP_EMA_Nifty backtest - {s['generated_at']}", "",
         f"Window {s['window']['start']} to {s['window']['end']}; out-of-sample from {s['out_of_sample_start']}.",
         f"Sessions: {s['data']['sessions_in_window']}; missing futures volume (skipped in 'futures' set, "
         f"spot-TWAP in 'with_missing' set): {s['data']['sessions_missing_futures_volume']}.",
         f"Spot data source per session: {s['data']['spot_source_counts']}.", "",
         f"**Tests run on this dataset so far: {s.get('test_ledger', {}).get('total', '?')}** "
         f"(new this run: {len(s.get('test_ledger', {}).get('new_this_run', []))}; ledger: test_ledger.json). "
         "Hypotheses and forward paper trades are not counted.",
         f"Conditions logged (observations): {s.get('registry', {}).get('conditions_logged')}",
         f"Hypotheses (observation only, not tested, not counted): {s.get('registry', {}).get('hypotheses_not_counted')}",
         f"Pre-registered rules: {s.get('registry', {}).get('preregistered')} "
         "(forward status: results/forward/<id>_status.json)", "",
         "Layer A = NIFTY index points, no costs. B = modelled 1-lot ATM option, net of costs and 1pt/side "
         "slippage (prior-session IV held constant). C = real option prices from 5-min chain snapshots "
         "(2026-06-19 onward only).", ""]
    for cid, c in s["conditions"].items():
        L += [f"## {cid}", f"Setup-matched sessions: {c['setup_matched_sessions']} "
              f"({', '.join(c['setup_matched_dates'][-15:])}{' ...' if c['setup_matched_sessions'] > 15 else ''})", ""]
        L.append("### Variants (layer A, points)")
        for k, v in c["variants"].items():
            L.append(f"- **{k}**")
            for setname in ("matched", "non_matched", "all"):
                L.append(f"  - {setname}: {_fmt(v[setname])}")
            for extra in ("all_B_rs", "all_C_rs", "IS_all_points", "OOS_all_points", "IS_all_B_rs", "OOS_all_B_rs"):
                if extra in v:
                    L.append(f"  - {extra}: {_fmt(v[extra], extra.endswith('_rs'))}")
            if "bootstrap_all_points" in v:
                L.append(f"  - block bootstrap, mean pts/trade (all days): {v['bootstrap_all_points']}")
            if "by_year" in v:
                yrs = "; ".join(f"{y}: n={r['points'].get('trades', 0)} exp={r['points'].get('expectancy')} "
                                f"PF={r['points'].get('profit_factor')} B={r['B_rs'].get('total')}"
                                for y, r in v["by_year"].items())
                L.append(f"  - by year (all days): {yrs}")
        L += ["", "### Option layers (base, futures VWAP)"]
        for setname in ("matched", "non_matched", "all"):
            o = c["options"].get(setname, {})
            L.append(f"- **{setname}**")
            L.append(f"  - A: {_fmt(o.get('A_points', {}))}")
            L.append(f"  - B: {_fmt(o.get('B_modelled_rs', {}), True)}")
            L.append(f"  - C: {_fmt(o.get('C_real_rs', {}), True)}")
        bc = c["options"].get("B_vs_C_on_real_price_days")
        if bc:
            L.append(f"- **B vs C on real-price days**: {bc}")
        L += ["", "### In-sample vs out-of-sample (base)"]
        for per, v in c["splits"].items():
            L.append(f"- {per} matched: {_fmt(v['matched'])}")
            L.append(f"- {per} non-matched: {_fmt(v['non_matched'])}")
            L.append(f"- {per} matched B: {_fmt(v['matched_B_rs'], True)}")
            L.append(f"- {per} non-matched B: {_fmt(v['non_matched_B_rs'], True)}")
        for key in ("by_day_type", "by_cpr_width"):
            L += ["", f"### {key.replace('_', ' ')} (base, points)"]
            for val, v in c[key].items():
                L.append(f"- {val}: matched {_fmt(v['matched'])} || non-matched {_fmt(v['non_matched'])}")
        if c.get("field_tests"):
            ft = c["field_tests"]
            L += ["", f"### Field tests on {ft['base_variant']} (pre-declared, one field at a time, "
                  f"{ft['tests_run']} tests, no combinations)"]
            for name in ["reference_no_field_filter"] + [k for k in ft if k not in
                                                         ("base_variant", "tests_run", "reference_no_field_filter")]:
                v = ft[name]
                L.append(f"- **{name}** {v.get('filter', '')}")
                for key in ("A_points", "B_modelled_rs", "C_real_rs", "IS_A_points", "OOS_A_points", "IS_B_rs", "OOS_B_rs"):
                    L.append(f"  - {key}: {_fmt(v[key], key.endswith('_rs'))}")
                L.append(f"  - bootstrap pts/trade: {v['bootstrap_points']}")
                yrs = "; ".join(f"{y}: n={r['points'].get('trades', 0)} exp={r['points'].get('expectancy')} "
                                f"PF={r['points'].get('profit_factor')} B={r['B_rs'].get('total')}"
                                for y, r in v["by_year"].items())
                L.append(f"  - by year: {yrs}")
        if c.get("setup_sensitivity"):
            L += ["", "### Setup sensitivity - EXPLORATORY, not rules (base, futures VWAP)",
                  "Each row loosens the setup; many looks at one dataset, so a single good row is weak evidence."]
            for name, v in c["setup_sensitivity"].items():
                L.append(f"- **{name}** ({v['sessions']} sessions): A {_fmt(v['A_points'])}")
                L.append(f"  - B: {_fmt(v['B_modelled_rs'], True)}")
                L.append(f"  - OOS A: {_fmt(v['OOS_A_points'])}")
        L += ["", "### Walk-forward by year (base, points)"]
        for yr, v in c["walk_forward"].items():
            L.append(f"- {yr}: matched {_fmt(v['matched'])} || non-matched {_fmt(v['non_matched'])}")
        L.append("")
    pr = s.get("priority_run")
    if pr:
        from . import hypothesis_tests as ht
        tl = s.get("test_ledger", {})
        total = int(tl.get("total") or 0)
        L.append(ht.to_markdown(pr, total, 0.05 / total if total else 0.05))
        L.append(f"Descriptive looks (priority-focus splits, not counted as tests): {tl.get('descriptive_looks')}. "
                 "Everything in this report is EXPLORATORY; only R001's forward paper result is confirmatory.")
    if s.get("conditions_run"):
        from . import conditions_backtest as cb
        L.append(cb.to_markdown(s["conditions_run"], int(s.get("test_ledger", {}).get("total") or 0)))
    fwd = RESULTS_DIR / "forward" / "R001_status.json"
    if fwd.exists():
        st = json.loads(fwd.read_text())
        L += ["", "## R001 forward status (paper)",
              f"- {st.get('verdict', st.get('status'))}: {st.get('reason', '')} (evaluated {st.get('evaluated_at')})",
              f"- {st.get('snapshot_coverage', {}).get('line', 'Snapshot coverage: n/a')}", ""]
    if prev:
        L += ["## Change vs previous run", f"Previous run: {prev.get('generated_at')}"]
        if pr and not prev.get("priority_run"):
            L.append("- NEW this run: golden_pivot_zone field, C001 re-verification, priority-focus splits, "
                     "pre-declared tests H-A, H-B, H-D (H-C awaiting definition)")
        ptl = prev.get("test_ledger", {})
        L.append(f"- tests on dataset: {ptl.get('total')} -> {s.get('test_ledger', {}).get('total')}")
        for cid, c in s["conditions"].items():
            pc = prev.get("conditions", {}).get(cid)
            if not pc:
                L.append(f"- {cid}: NEW condition (no previous result)")
                continue
            for k in ("base|futures",):
                now, was = c["variants"].get(k, {}), pc["variants"].get(k, {})
                for setname in ("matched", "non_matched"):
                    a, b = now.get(setname, {}), was.get(setname, {})
                    L.append(f"- {cid} {k} {setname}: trades {b.get('trades', 0)} -> {a.get('trades', 0)}, "
                             f"expectancy {b.get('expectancy')} -> {a.get('expectancy')}, "
                             f"PF {b.get('profit_factor')} -> {a.get('profit_factor')}")
        for cid in prev.get("conditions", {}):
            if cid not in s["conditions"]:
                L.append(f"- {cid}: REMOVED since previous run")
    return "\n".join(L) + "\n"


def _equity_png(trades: pd.DataFrame, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    F = trades[trades.vwap_set == "futures"].sort_values("entry_time")
    fig, axes = plt.subplots(2, 1, figsize=(12, 8), dpi=110)
    for cid in sorted(F.condition.unique()):
        for v in sorted(F.variant.unique()):
            sub = F[(F.condition == cid) & (F.variant == v)]
            if not len(sub):
                continue
            lbl = f"{cid} {v} (all days, n={len(sub)}; setup-matched {int(sub.matched.sum())})"
            style = "-" if v == "base" else "--"
            axes[0].plot(pd.to_datetime(sub.entry_time), sub.points.cumsum(), style, lw=1.2, label=lbl)
            if "B_net" in sub:
                sb = sub[sub.B_net.notna()]
                axes[1].plot(pd.to_datetime(sb.entry_time), sb.B_net.cumsum(), style, lw=1.2, label=lbl)
    axes[0].set_title("Layer A: cumulative NIFTY points, all days (no costs)")
    axes[1].set_title("Layer B: cumulative modelled 1-lot ATM option P&L, net (Rs), all days")
    for ax in axes:
        ax.axhline(0, color="grey", lw=0.8)
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3)
    plt.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def run_and_save(cfg: Optional[dict] = None, with_options: bool = True) -> Dict:
    cfg = cfg or load_config()
    LOG_DIR.mkdir(exist_ok=True)
    fh = logging.FileHandler(LOG_DIR / "backtest.log")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(fh)
    logger.setLevel(logging.INFO)
    try:
        all_conditions = rl.load()["conditions"]
        # The original C001 engine (variants, field tests, setup sensitivity) runs only for
        # close_vs_vwap conditions; C002-C007 use conditions_backtest (separate path).
        conditions = [c for c in all_conditions if c["entry"]["type"] == "close_vs_vwap"]
        prep = prepare(cfg)
        trades = run(cfg, conditions, prep, with_options=with_options)
        summary = summarize(trades, prep, cfg, conditions)
        from . import hypothesis_tests as ht
        priority = ht.run_all(trades, prep, cfg, conditions)
        hd_trades = priority.pop("_hd_trades", None)
        summary["priority_run"] = priority
        from . import conditions_backtest as cb
        multi = cb.run_conditions(prep, cfg, all_conditions)
        multi_trades = multi.pop("trades")
        summary["conditions_run"] = multi
        summary["test_ledger"] = update_ledger(summary, summary["generated_at"])
        doc = rl.load()
        summary["registry"] = {
            "hypotheses_not_counted": [{"id": h["id"], "date": h["date"], "status": h.get("status")}
                                       for h in doc.get("hypotheses", [])],
            "preregistered": [{"id": r["id"], "status": r.get("status"), "approved_on": r.get("approved_on"),
                               "frozen_ok": rl.frozen_ok(r)} for r in doc.get("preregistered", [])],
            "conditions_logged": [{"id": c["id"], "date": c["date"], "role": c.get("role")}
                                  for c in doc.get("conditions", [])],
        }
        latest = RESULTS_DIR / "latest"
        prev = None
        if (latest / "summary.json").exists():
            prev = json.loads((latest / "summary.json").read_text())
            hist = RESULTS_DIR / "history" / prev["generated_at"].replace(":", "")
            hist.mkdir(parents=True, exist_ok=True)
            for f in latest.iterdir():
                shutil.copy2(f, hist / f.name)
        latest.mkdir(parents=True, exist_ok=True)
        trades.to_csv(latest / "trades.csv", index=False)
        if hd_trades is not None:
            hd_trades.to_csv(latest / "h_d_short_trades.csv", index=False)
        if multi_trades is not None and not multi_trades.empty:
            multi_trades.to_csv(latest / "conditions_trades.csv", index=False)
        (latest / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
        (latest / "summary.md").write_text(to_markdown(summary, prev))
        if not trades.empty:
            _equity_png(trades, latest / "equity.png")
        logger.info("backtest done: %d trade rows, %d conditions", len(trades), len(conditions))
        return summary
    finally:
        logger.removeHandler(fh)
        fh.close()
