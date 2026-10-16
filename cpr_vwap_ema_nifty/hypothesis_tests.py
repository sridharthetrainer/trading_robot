"""
Priority-focus report and pre-declared hypothesis tests (H-A .. H-D) for
CPR_VWAP_EMA_Nifty, declared by the user on 2026-10-16 (config.json
"predeclared_tests"). Separate code path: nothing here changes R001's frozen
definition, engine_mapping or bar, or C001's stored fields.

Primary levels (organizes the report): futures VWAP, 1m 200 EMA, 5m 200 EMA,
daily CPR, the PDH/R1 ZONE (band between PDH and R1) and the PDL/S1 ZONE (band
between PDL and S1). Positions are taken at the SIGNAL bar's close (the bar
whose close triggers the entry; the fill is the next bar's open).

Secondary hypotheses: golden pivot zone (H-A), Camarilla H3-H4 / L3-L4 zones
(H-B), 30m 200 EMA (earlier filter variant). H-C waits for the user's exact
definition. H-D is a short mirror of R001 (ATM put).
"""
from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import engine as en

FIVE = pd.Timedelta(minutes=5)


# ── zone helpers ────────────────────────────────────────────────────────────

def zone_position(px: float, a: float, b: float) -> str:
    """Position of px relative to the band [min(a, b), max(a, b)]."""
    lo, hi = min(a, b), max(a, b)
    if px > hi:
        return "above"
    if px < lo:
        return "below"
    return "inside"


def line_position(px: float, level: float) -> str:
    return "above" if px > level else "below"


def in_camarilla_sideways_zone(px: float, lev: pd.Series) -> Dict[str, bool]:
    h = zone_position(px, lev["H3"], lev["H4"]) == "inside"
    l_ = zone_position(px, lev["L3"], lev["L4"]) == "inside"
    return {"inside_h3_h4": h, "inside_l3_l4": l_, "inside_either": h or l_}


def signal_bar(day: pd.DataFrame, entry_time: str) -> Optional[pd.Series]:
    ts = pd.Timestamp(entry_time) - FIVE
    return day.loc[ts] if ts in day.index else None


def primary_positions(bar: pd.Series, lev: pd.Series) -> Dict[str, str]:
    return {
        "vwap": line_position(bar["fut_close"], bar["fut_vwap"]),
        "ema200_1m": line_position(bar["close"], bar["ema200_1m"]),
        "ema200_5m": line_position(bar["close"], bar["ema200_5m"]),
        "cpr": zone_position(bar["close"], lev["BC"], lev["TC"]),
        "pdh_r1_zone": zone_position(bar["close"], lev["PDH"], lev["R1"]),
        "pdl_s1_zone": zone_position(bar["close"], lev["PDL"], lev["S1"]),
    }


# ── stats ───────────────────────────────────────────────────────────────────

def _pf(x: np.ndarray) -> Optional[float]:
    loss = -x[x <= 0].sum()
    return round(float(x[x > 0].sum() / loss), 2) if loss > 0 else None


def block_bootstrap(values, n: int = 5000, block: int = 5, seed: int = 7) -> Dict:
    x = np.array([v for v in values if v is not None and not pd.isna(v)], dtype=float)
    if len(x) < 20:
        return {"n": int(len(x)), "note": "too few trades"}
    rng = np.random.default_rng(seed)
    nb = int(np.ceil(len(x) / block))
    means = np.array([np.concatenate([x[s:s + block] for s in rng.integers(0, len(x) - block + 1, nb)])[:len(x)].mean()
                      for _ in range(n)])
    lo, hi = np.percentile(means, [2.5, 97.5])
    return {"n": int(len(x)), "mean": round(float(x.mean()), 2), "ci95": [round(float(lo), 2), round(float(hi), 2)],
            "p_mean_le_0": round(float(np.mean(means <= 0)), 4)}


def diff_bootstrap(a, b, n: int = 5000, seed: int = 11) -> Dict:
    """Bootstrap of mean(a) - mean(b) (independent resampling of each group)."""
    a = np.array([v for v in a if not pd.isna(v)], dtype=float)
    b = np.array([v for v in b if not pd.isna(v)], dtype=float)
    if len(a) < 10 or len(b) < 10:
        return {"note": "too few trades"}
    rng = np.random.default_rng(seed)
    d = np.array([rng.choice(a, len(a)).mean() - rng.choice(b, len(b)).mean() for _ in range(n)])
    lo, hi = np.percentile(d, [2.5, 97.5])
    return {"diff_mean": round(float(a.mean() - b.mean()), 2), "ci95": [round(float(lo), 2), round(float(hi), 2)],
            "p_diff_ge_0": round(float(np.mean(d >= 0)), 4)}


def group_stats(S: pd.DataFrame) -> Dict:
    if S.empty:
        return {"trades": 0}
    pts = S["points"].to_numpy(dtype=float)
    b = S["B_net"].dropna().to_numpy(dtype=float) if "B_net" in S else np.array([])
    out = {"trades": int(len(S)), "win_rate": round(float((pts > 0).mean() * 100), 1), "pf_points": _pf(pts),
           "exp_points": round(float(pts.mean()), 2), "B_net_rs": round(float(b.sum()), 2), "B_trades": int(len(b))}
    for per in ("IS", "OOS"):
        P = S[S["period"] == per]
        pp = P["points"].to_numpy(dtype=float)
        pb = P["B_net"].dropna().to_numpy(dtype=float) if "B_net" in P else np.array([])
        out[per] = {"trades": int(len(P)), "win_rate": round(float((pp > 0).mean() * 100), 1) if len(pp) else None,
                    "pf_points": _pf(pp) if len(pp) else None, "B_net_rs": round(float(pb.sum()), 2)}
    return out


# ── 1. C001 duplicate check, 2. field verification, 3. reproduction ─────────

def verify_c001(cond: dict, prep: Dict, trades: pd.DataFrame, existing: List[dict]) -> Dict:
    from . import rules as rl
    d = pd.Timestamp(cond["date"])
    lev = prep["levels"].loc[d]
    day = prep["by_day"][d]
    dup = rl.check_new({**cond, "id": cond["id"] + "_recheck"}, existing)
    fields = {k: {"stored": cond["setup"].get(k), "data": str(lev[k]),
                  "match": str(lev[k]) == str(cond["setup"].get(k))} for k in en.SETUP_KEYS}
    fields["golden_pivot_zone"] = {"stored": "(not a stored C001 field)", "data": lev["golden_pivot_zone"], "match": None}
    T = trades[(trades.condition == cond["id"]) & (trades.variant == "base") & (trades.vwap_set == "futures")
               & (trades.date == cond["date"])]
    t = T.iloc[0] if len(T) else None
    ctx = {}
    if t is not None:
        sb = signal_bar(day, t.entry_time)
        ctx = {"signal_bar": str(pd.Timestamp(t.entry_time) - FIVE)[11:16],
               "above_vwap": bool(sb.fut_close > sb.fut_vwap), "above_ema20_5m": bool(sb.close > sb.ema20_5m),
               "above_ema50_5m": bool(sb.close > sb.ema50_5m), "above_ema200_5m": bool(sb.close > sb.ema200_5m),
               "above_ema200_1m": bool(sb.close > sb.ema200_1m)}
    above30 = (day["close_30m"] > day["ema200_30m"])
    first_above = above30[above30].index.min()
    ctx["30m_200ema_at_open_bar"] = "above" if bool(above30.iloc[0]) else "below"
    ctx["30m_200ema_first_above_bar_close"] = (str(first_above + FIVE)[11:16] if first_above is not pd.NaT else None)
    ctx["30m_close_minus_ema_at_first_above"] = (round(float(day.loc[first_above, "close_30m"]
                                                             - day.loc[first_above, "ema200_30m"]), 2)
                                                 if first_above is not pd.NaT else None)
    repro = None
    if t is not None:
        repro = {k: (None if pd.isna(t.get(k)) else t.get(k)) for k in
                 ("entry_time", "entry_px", "exit_time", "exit_px", "exit_reason", "points", "strike",
                  "B_entry", "B_exit", "B_net", "C_entry", "C_exit", "C_net")}
    return {"duplicate_check": dup, "fields": fields, "context": ctx, "reproduction": repro}


# ── 4. priority focus ───────────────────────────────────────────────────────

def annotate_base(trades: pd.DataFrame, prep: Dict, cid: str) -> pd.DataFrame:
    B = trades[(trades.condition == cid) & (trades.variant == "base") & (trades.vwap_set == "futures")].copy()
    rows = []
    for _, t in B.iterrows():
        d = pd.Timestamp(t.date)
        sb = signal_bar(prep["by_day"][d], t.entry_time)
        lev = prep["levels"].loc[d]
        pos = primary_positions(sb, lev) if sb is not None else {}
        cam = in_camarilla_sideways_zone(sb["close"], lev) if sb is not None else {}
        rows.append({**pos, **cam, "golden_pivot_zone": lev["golden_pivot_zone"]})
    return pd.concat([B.reset_index(drop=True), pd.DataFrame(rows)], axis=1)


PRIMARY = ("vwap", "ema200_1m", "ema200_5m", "cpr", "pdh_r1_zone", "pdl_s1_zone")


def priority_focus(A: pd.DataFrame, cond: dict) -> Dict:
    out = {}
    entry_conditions = {k.replace("above_", "").replace("ema200_", "ema200_") for k, v in cond.get("context", {}).items() if v}
    for lvl in PRIMARY:
        out[lvl] = {pos: group_stats(A[A[lvl] == pos]) for pos in ("above", "inside", "below") if (A[lvl] == pos).any()}
        if {"vwap": "vwap", "ema200_1m": "ema200_1m", "ema200_5m": "ema200_5m"}.get(lvl) in entry_conditions:
            out[lvl]["note"] = "one-sided by construction: being above this level is an entry condition of the base rule"
    return out


# ── 6. C001 match counts ────────────────────────────────────────────────────

def c001_counts(cond: dict, levels: pd.DataFrame) -> Dict:
    setup = cond["setup"]
    exact = int(levels.apply(lambda r: en.matches_setup(r, setup), axis=1).sum())
    table = {}
    for k, v in setup.items():
        alone = int((levels[k].astype(str) == str(v)).sum())
        rest = {kk: vv for kk, vv in setup.items() if kk != k}
        without = int(levels.apply(lambda r: en.matches_setup(r, rest), axis=1).sum())
        table[k] = {"value": v, "sessions_matching_this_field_alone": alone,
                    "sessions_matching_all_other_fields": without}
    return {"sessions": int(len(levels)), "exact_match_sessions": exact, "one_field_at_a_time": table}


# ── 5. hypothesis tests ─────────────────────────────────────────────────────

def h_a(A: pd.DataFrame, prep: Dict) -> Dict:
    """Golden pivot zone groups: day direction (all sessions) + base-rule result."""
    lev = prep["levels"].loc[prep["days"]]
    daily = en.lv.session_ohlc(pd.concat([g for g in prep["by_day"].values()]))
    out = {"definition": "bullish: daily Camarilla L3 inside daily CPR; bearish: H3 inside; none otherwise. "
                         "Day direction = session close vs session open (and vs prior close).", "groups": {}}
    prev_close = daily["close"].shift(1)
    for g in ("bullish", "bearish", "none"):
        days = lev.index[lev["golden_pivot_zone"] == g]
        dd = daily.loc[daily.index.intersection(days)]
        pc = prev_close.loc[dd.index]
        out["groups"][g] = {
            "sessions": int(len(dd)),
            "close_above_open_pct": round(float((dd.close > dd.open).mean() * 100), 1) if len(dd) else None,
            "avg_close_minus_open_pts": round(float((dd.close - dd.open).mean()), 2) if len(dd) else None,
            "close_above_prior_close_pct": round(float((dd.close > pc).mean() * 100), 1) if len(dd) else None,
            "base_rule": group_stats(A[A.golden_pivot_zone == g]),
            "bootstrap_points": block_bootstrap(A[A.golden_pivot_zone == g]["points"].tolist()),
        }
    return out


def h_b(A: pd.DataFrame) -> Dict:
    inside, outside = A[A.inside_either], A[~A.inside_either]
    return {"definition": "signal-bar close inside daily Camarilla [H3,H4] or [L4,L3] = inside; otherwise outside.",
            "inside_either": group_stats(inside), "outside": group_stats(outside),
            "inside_h3_h4_only_count": int(A.inside_h3_h4.sum()), "inside_l3_l4_only_count": int(A.inside_l3_l4.sum()),
            "inside_minus_outside_points": diff_bootstrap(inside["points"], outside["points"]),
            "answer": None}


def _binom_p(hits: int, n: int, p0: float) -> Optional[float]:
    """One-sided exact binomial p-value P(X >= hits | n, p0)."""
    if n == 0:
        return None
    from scipy.stats import binomtest
    return round(float(binomtest(hits, n, p0, alternative="greater").pvalue), 5)


def late_close_pairs(prep: Dict, oos_start: str) -> pd.DataFrame:
    """One row per (session, next session) pair. final_close = the session's last
    1m close (= close of the 15:25 5m bar), ema = the 1m 200 EMA at that minute.
    A pair counts only when the next session is the very next session seen in the
    data (no pair spans a missing / insufficient-data session)."""
    all_sessions = sorted(pd.Timestamp(d) for d in prep["spot_source"])
    usable = set(prep["by_day"])
    rows = []
    for d, nxt in zip(all_sessions[:-1], all_sessions[1:]):
        if d not in usable or nxt not in usable or d not in set(prep["days"]):
            continue
        day, nday = prep["by_day"][d], prep["by_day"][nxt]
        last = day.iloc[-1]
        if pd.isna(last["ema200_1m"]):
            continue
        final_close, nxt_open = float(last["close"]), float(nday.iloc[0]["open"])
        rows.append({"date": d, "next": nxt, "final_close": final_close, "ema200_1m": float(last["ema200_1m"]),
                     "above": final_close > float(last["ema200_1m"]), "below": final_close < float(last["ema200_1m"]),
                     "gap_up": nxt_open > final_close, "gap_down": nxt_open < final_close,
                     "period": "OOS" if d >= pd.Timestamp(oos_start) else "IS"})
    return pd.DataFrame(rows)


def h_c(prep: Dict, cfg: dict) -> Dict:
    """Run once (user definition 2026-10-16): signal = session's final 1m close above the 1m 200 EMA;
    hit = next session's open strictly above that close (gap up, no threshold). Mirror: final close
    below the EMA, hit = gap down. Baselines: share of all pairs that gap up / gap down."""
    P = late_close_pairs(prep, cfg["out_of_sample_start"])

    def block(S: pd.DataFrame) -> Dict:
        n_all = len(S)
        base_up = S.gap_up.mean() if n_all else None
        base_dn = S.gap_down.mean() if n_all else None
        up, dn = S[S.above], S[S.below]
        return {
            "pairs": int(n_all),
            "baseline_gap_up_pct": round(float(base_up * 100), 1) if n_all else None,
            "baseline_gap_down_pct": round(float(base_dn * 100), 1) if n_all else None,
            "final_close_above_ema": {"sessions": int(len(up)), "gap_up": int(up.gap_up.sum()),
                                      "hit_rate_pct": round(float(up.gap_up.mean() * 100), 1) if len(up) else None,
                                      "p_vs_baseline": _binom_p(int(up.gap_up.sum()), len(up), float(base_up)) if len(up) else None},
            "final_close_below_ema": {"sessions": int(len(dn)), "gap_down": int(dn.gap_down.sum()),
                                      "hit_rate_pct": round(float(dn.gap_down.mean() * 100), 1) if len(dn) else None,
                                      "p_vs_baseline": _binom_p(int(dn.gap_down.sum()), len(dn), float(base_dn)) if len(dn) else None},
        }
    return {"definition": h_c.__doc__.strip(), "all": block(P),
            "IS": block(P[P.period == "IS"]), "OOS": block(P[P.period == "OOS"]),
            "equal_open_pairs": int((~P.gap_up & ~P.gap_down).sum()) if len(P) else 0}


def h_d(prep: Dict, cfg: dict) -> Dict:
    """Short mirror of R001, run once: close < min(PDL, S1), below futures VWAP and below
    5m 20/50/200 + 1m 200 EMAs; next-open entry; exit on a 5m close above the 5m 20 EMA;
    force 15:20; one trade per day; ATM put."""
    from . import backtest as bt
    from .options_layers import ModelledLayer, RealLayer
    cond = {"bias": "SHORT",
            "context": {"above_vwap": True, "above_ema20_5m": True, "above_ema50_5m": True,
                        "above_ema200_5m": True, "above_ema200_1m": True},  # SHORT side = 'below' each
            "exit": {"type": "close_vs_ema", "timeframe": "5m", "period": 20}}
    mod, real = ModelledLayer(cfg, prep["spot_close"]), RealLayer(cfg)
    real_days = set(real.days())
    oos = pd.Timestamp(cfg["out_of_sample_start"])
    rows = []
    for d in prep["days"]:
        day = prep["by_day"].get(d)
        if day is None or len(day) < 60 or not prep["fut_ok"].get(d, False):
            continue
        t = en.simulate_day(day, prep["levels"].loc[d], cond, ["r1_pdh_break"], cfg, "futures")
        if t is None:
            continue
        r = {**t.__dict__}
        r.pop("extra", None)
        r.update(bt._option_layers(t, cfg, mod, real, real_days))
        r["period"] = "OOS" if d >= oos else "IS"
        r["year"] = d.year
        rows.append(r)
    T = pd.DataFrame(rows)
    if T.empty:
        return {"trades": 0}
    if "C_net" not in T:
        T["C_net"] = np.nan
    out = {"definition": h_d.__doc__.strip(), "option": "PE",
           "A_points": group_stats(T),
           "B_modelled_rs": {"trades": int(T.B_net.notna().sum()), "total": round(float(T.B_net.sum()), 2),
                             "pf": _pf(T.B_net.dropna().to_numpy())},
           "C_real_rs": {"trades": int(T.C_net.notna().sum()), "total": round(float(T.C_net.sum()), 2),
                         "pf": _pf(T.C_net.dropna().to_numpy()) if T.C_net.notna().any() else None},
           "by_year": {int(y): {"trades": int(len(g)), "pf_points": _pf(g.points.to_numpy(dtype=float)),
                                "exp_points": round(float(g.points.mean()), 2), "B_net_rs": round(float(g.B_net.sum()), 2)}
                       for y, g in T.groupby("year")},
           "bootstrap_points": block_bootstrap(T.sort_values("entry_time")["points"].tolist()),
           "bootstrap_B_rs": block_bootstrap(T.sort_values("entry_time")["B_net"].tolist())}
    out["_trades"] = T
    return out


def run_all(trades: pd.DataFrame, prep: Dict, cfg: dict, conditions: List[dict]) -> Dict:
    cond = next(c for c in conditions if c["id"] == "C001")
    A = annotate_base(trades, prep, "C001")
    hd = h_d(prep, cfg)
    hd_trades = hd.pop("_trades", None)
    hb = h_b(A)
    diff = hb["inside_minus_outside_points"]
    hb["answer"] = ("inside worse than outside" if diff.get("diff_mean") is not None and diff["diff_mean"] < 0
                    else "inside NOT worse than outside" if diff.get("diff_mean") is not None else "too few trades")
    ptests = cfg.get("predeclared_tests", {})
    return {
        "c001": verify_c001(cond, prep, trades, [c for c in conditions]),
        "c001_counts": c001_counts(cond, prep["levels"].loc[prep["days"]]),
        "priority_focus": priority_focus(A, cond),
        "tests": {"H-A": h_a(A, prep), "H-B": hb,
                  "H-C": (h_c(prep, cfg) if ptests.get("H-C", {}).get("status") == "run" else
                          {"status": "awaiting_definition",
                           "note": "Not run until the user defines it; counted then."}),
                  "H-D": hd},
        "_hd_trades": hd_trades,
    }


# ── markdown ────────────────────────────────────────────────────────────────

def _g(s: Dict) -> str:
    if not s or not s.get("trades"):
        return "0 trades"
    return (f"{s['trades']} tr | win {s['win_rate']}% | PF {s['pf_points']} | exp {s['exp_points']} pts | "
            f"B {s['B_net_rs']:+,.0f} | IS PF {s['IS']['pf_points']} (n={s['IS']['trades']}, B {s['IS']['B_net_rs']:+,.0f}) | "
            f"OOS PF {s['OOS']['pf_points']} (n={s['OOS']['trades']}, B {s['OOS']['B_net_rs']:+,.0f})")


def to_markdown(r: Dict, ledger_total: int, threshold: float) -> str:
    L = ["## C001 re-verification (priority focus run)", ""]
    v = r["c001"]
    L.append(f"Duplicate/conflict check vs existing rules: {v['duplicate_check']}")
    L += ["", "| Field | Stored | Data (2026-10-15) | Match |", "|---|---|---|---|"]
    for k, f in v["fields"].items():
        L.append(f"| {k} | {f['stored']} | {f['data']} | {'' if f['match'] is None else ('yes' if f['match'] else 'NO')} |")
    L += ["", f"Context at signal bar: {v['context']}", "", f"Reproduction: {v['reproduction']}", ""]
    c = r["c001_counts"]
    L += [f"C001 exact-match sessions: {c['exact_match_sessions']} of {c['sessions']}", "",
          "| Field (value) | Sessions matching this field alone | Sessions matching all OTHER fields |", "|---|---|---|"]
    for k, f in c["one_field_at_a_time"].items():
        L.append(f"| {k} ({f['value']}) | {f['sessions_matching_this_field_alone']} | {f['sessions_matching_all_other_fields']} |")
    L += ["", "## Priority focus: base rule by position at the signal bar (descriptive, not tuned)", ""]
    for lvl, groups in r["priority_focus"].items():
        L.append(f"- **{lvl}**" + (f" ({groups['note']})" if "note" in groups else ""))
        for pos, s in groups.items():
            if pos != "note":
                L.append(f"  - {pos}: {_g(s)}")
    t = r["tests"]
    L += ["", f"## Pre-declared tests (exploratory; Bonferroni threshold for {ledger_total} tests: p < {threshold:.5f})", ""]
    L.append(f"### H-A golden pivot zone — {t['H-A']['definition']}")
    for g, s in t["H-A"]["groups"].items():
        L.append(f"- {g}: {s['sessions']} sessions | close>open {s['close_above_open_pct']}% | avg C-O "
                 f"{s['avg_close_minus_open_pts']} pts | close>prior close {s['close_above_prior_close_pct']}%")
        L.append(f"  - base rule: {_g(s['base_rule'])}; bootstrap {s['bootstrap_points']}")
    hb = t["H-B"]
    L += ["", f"### H-B Camarilla sideways zones — {hb['definition']}",
          f"- inside: {_g(hb['inside_either'])} (H3-H4: {hb['inside_h3_h4_only_count']}, L3-L4: {hb['inside_l3_l4_only_count']})",
          f"- outside: {_g(hb['outside'])}",
          f"- inside minus outside (pts/trade): {hb['inside_minus_outside_points']} -> {hb['answer']}", "",
          ]
    hc = t["H-C"]
    if "all" in hc:
        L.append(f"### H-C late close vs 1m 200 EMA -> next open (run once) — {hc['definition']}")
        for per in ("all", "IS", "OOS"):
            b = hc[per]
            L.append(f"- {per}: {b['pairs']} session pairs | baseline gap up {b['baseline_gap_up_pct']}% / "
                     f"gap down {b['baseline_gap_down_pct']}%")
            L.append(f"  - final close ABOVE EMA: {b['final_close_above_ema']}")
            L.append(f"  - final close BELOW EMA: {b['final_close_below_ema']}")
        L.append(f"- pairs with next open exactly equal to the close (neither): {hc['equal_open_pairs']}")
    else:
        L.append(f"### H-C — {hc['status']}: {hc['note']}")
    L.append("")
    hd = t["H-D"]
    L += [f"### H-D short mirror of R001 (ATM put) — run once", f"- A: {_g(hd['A_points'])}",
          f"- B modelled: {hd['B_modelled_rs']}", f"- C real: {hd['C_real_rs']}",
          f"- bootstrap points: {hd['bootstrap_points']}", f"- bootstrap B: {hd['bootstrap_B_rs']}",
          "- by year: " + "; ".join(f"{y}: n={s['trades']} PF={s['pf_points']} exp={s['exp_points']} B={s['B_net_rs']:+,.0f}"
                                    for y, s in hd["by_year"].items()), ""]
    return "\n".join(L) + "\n"
