"""
Multi-condition backtest for CPR_VWAP_EMA_Nifty (C001-C007), approved by the
user on 2026-10-16. Separate code path: R001 and the original C001 engine
(engine.simulate_day) are untouched.

For every condition three versions run:
  A  EXACT        sessions whose stored setup fields ALL match (standard fields
                  plus any optional fields the condition stores); the
                  condition's own entry and exit.
  B  PRIMARY      every session; the condition's own entry trigger and exit,
                  plus its position vs the six primary levels (futures VWAP,
                  1m 200 EMA, 5m 200 EMA, CPR, PDH/R1 zone, PDL/S1 zone) taken
                  from its own date's signal candle. CPR width / relations /
                  golden zone dropped. Pre-declared tests 25-30 (C002-C007);
                  C001's B is the base rule (already counted).
  C  FIXED-EXIT   version B with a 5m close beyond the 5m 20 EMA, and again
                  beyond the 5m 50 EMA (descriptive).

Fixed definitions (user-approved, config.json "fixed_definitions"):
  strong red / green: candle red / green with body >= 60% of its high-low range
  "strong close below VWAP": futures close below futures VWAP on a strong red spot candle
  "first green after a run of reds": a green candle right after >= 3 consecutive reds
  "two strong bullish candles": two consecutive strong green candles
  C007 staged exit: three equal parts at the first touch of the 5m 200 EMA, the
  weekly CPR (top edge for a short), the PDL/S1 zone (top edge for a short); a
  part whose level is not beyond the entry price uses the next stage's level;
  anything left exits at 15:20 (user's assumption).

Signals are evaluated on candle closes and filled at the next candle's open.
One trade per session. Long = ATM call, short = ATM put.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from . import engine as en
from .hypothesis_tests import block_bootstrap, primary_positions, zone_position

FIVE = pd.Timedelta(minutes=5)
OPTIONAL_SETUP = ("golden_pivot_zone", "weekly_cpr_vs_monthly", "daily_cpr_inside_weekly")


# ── candle helpers ──────────────────────────────────────────────────────────

def body_frac(b) -> float:
    rng = float(b["high"] - b["low"])
    return abs(float(b["close"] - b["open"])) / rng if rng > 0 else 0.0


def strong_red(b, frac: float) -> bool:
    return b["close"] < b["open"] and body_frac(b) >= frac


def strong_green(b, frac: float) -> bool:
    return b["close"] > b["open"] and body_frac(b) >= frac


def match_all(lrow: pd.Series, setup: Dict) -> bool:
    """Version A: every stored setup field equals the session's value."""
    for k, v in setup.items():
        if k not in lrow.index:
            return False
        if isinstance(v, bool):
            if bool(lrow[k]) != v:
                return False
        elif str(lrow[k]) != str(v):
            return False
    return True


# ── entry triggers ──────────────────────────────────────────────────────────

def find_signal(day: pd.DataFrame, lev: pd.Series, etype: str, cfg: dict) -> Optional[int]:
    """Index of the signal candle (fill = next candle's open), or None."""
    fd = cfg["fixed_definitions"]
    frac = float(fd["strong_body_min_frac"])
    sess = cfg["session"]
    t_first = pd.Timestamp(fd.get("c005_first_signal_bar", "09:20")).time() if etype == "reclaim_all_from_0920" \
        else pd.Timestamp(sess["first_signal_bar"]).time()
    t_last = pd.Timestamp(sess["last_signal_bar"]).time()
    zone_lo = min(lev["PDH"], lev["R1"])
    flag = False
    rejections = 0
    for i, (ts, b) in enumerate(day.iterrows()):
        if i >= len(day) - 1:
            break
        if pd.isna(b.get("fut_vwap")) or pd.isna(b.get("fut_close")):
            continue
        below_vwap, above_vwap = b.fut_close < b.fut_vwap, b.fut_close > b.fut_vwap
        if b.high >= zone_lo and b.close < zone_lo:
            rejections += 1
        if not (t_first <= ts.time() <= t_last):
            continue
        if etype == "vwap_context_long":                        # C001 base rule
            if above_vwap and b.close > b.ema20_5m and b.close > b.ema50_5m and b.close > b.ema200_5m \
                    and b.close > b.ema200_1m:
                return i
        elif etype == "weekly_pivot_break_short":                # C002
            if b.close < lev["WP"] and below_vwap and b.close < b.ema20_5m and b.close < b.ema50_5m \
                    and b.close < b.ema200_5m and b.close < b.ema200_1m:
                return i
        elif etype == "cpr_support_reclaim_long":                # C003
            if b.low <= lev["TC"] and b.close >= lev["BC"]:
                flag = True
            if flag and above_vwap and b.close > b.ema200_1m:
                return i
        elif etype == "strong_vwap_then_strong_emas_short":      # C004
            if strong_red(b, frac) and below_vwap:
                flag = True
            if flag and strong_red(b, frac) and b.close < b.ema200_1m and b.close < b.ema50_5m:
                return i
        elif etype == "reclaim_all_from_0920_long" or etype == "reclaim_all_from_0920":  # C005
            if above_vwap and b.close > b.ema20_5m and b.close > b.ema50_5m and b.close > b.ema200_5m \
                    and b.close > b.ema200_1m:
                return i
        elif etype == "vwap_pdl_ema20_resistance_short":         # C006
            if below_vwap and b.close < lev["PDL"] and b.close < b.ema20_5m and b.close < b.ema200_1m \
                    and b.close < b.ema50_5m and b.close < b.ema200_5m:
                return i
        elif etype == "rejections_then_strong_breakdown_short":  # C007
            if rejections >= int(fd["c007_min_rejections"]) and strong_red(b, frac) and below_vwap \
                    and b.close < b.ema20_5m:
                flag = True
            if flag and b.close < b.ema200_1m and b.close < b.ema50_5m:
                return i
        else:
            raise ValueError(f"unknown entry type {etype}")
    return None


# ── exits ───────────────────────────────────────────────────────────────────

@dataclass
class CTrade:
    date: str
    side: str
    entry_time: str
    entry_px: float
    exit_time: str
    exit_px: float
    exit_reason: str
    points: float
    mfe: float
    mae: float
    hold_min: float
    signal_time: str
    parts: List[Tuple[str, float, str]] = field(default_factory=list)   # staged: (time, px, reason)


def simulate(day: pd.DataFrame, lev: pd.Series, entry_type: str, exit_spec: Dict, sign: int, cfg: dict,
             positions: Optional[Dict[str, str]] = None) -> Optional[CTrade]:
    sig = find_signal(day, lev, entry_type, cfg)
    if sig is None:
        return None
    sb = day.iloc[sig]
    if positions is not None and primary_positions(sb, lev) != positions:
        return None
    entry_i = sig + 1
    idx = list(day.index)
    t_force = pd.Timestamp(cfg["session"]["force_exit_bar"]).time()
    if idx[entry_i].time() >= t_force:
        return None
    fd = cfg["fixed_definitions"]
    frac = float(fd["strong_body_min_frac"])
    entry_px = float(day.iloc[entry_i].open)
    etype = exit_spec["type"]
    mfe = mae = 0.0
    reds = 0
    prev_strong_green = False
    zone_lo = min(lev["PDH"], lev["R1"])
    exit_px = exit_t = reason = None
    parts: List[Tuple[str, float, str]] = []
    if etype == "staged_zones":
        stage_levels = [("ema200_5m", None), ("weekly_cpr", lev["WTC"] if sign < 0 else lev["WBC"]),
                        ("pdl_s1_zone", max(lev["PDL"], lev["S1"]) if sign < 0 else min(lev["PDH"], lev["R1"]))]
        done = [False, False, False]
    for j in range(entry_i, len(idx)):
        b = day.iloc[j]
        ts = idx[j]
        if ts.time() >= t_force:
            exit_px, exit_t, reason = float(b.open), ts, "force_exit"
            break
        mfe = max(mfe, (b.high - entry_px) if sign > 0 else (entry_px - b.low))
        mae = max(mae, (entry_px - b.low) if sign > 0 else (b.high - entry_px))
        nxt = (float(day.iloc[j + 1].open), idx[j + 1]) if j + 1 < len(idx) else (float(b.close), ts + FIVE)
        if etype == "close_vs_ema":
            e = b.get(f"ema{int(exit_spec['period'])}_5m")
            if not pd.isna(e) and sign * (b.close - e) < 0:
                exit_px, exit_t, reason = nxt[0], nxt[1], f"close_beyond_ema{int(exit_spec['period'])}"
                break
        elif etype == "green_after_reds":
            if b.close > b.open and reds >= int(fd["red_run_min"]):
                exit_px, exit_t, reason = nxt[0], nxt[1], "green_after_reds"
                break
            reds = reds + 1 if b.close < b.open else 0
        elif etype == "red_at_pdh_r1_zone":
            if b.close < b.open and b.high >= zone_lo:
                exit_px, exit_t, reason = nxt[0], nxt[1], "red_at_pdh_r1_zone"
                break
        elif etype == "two_strong_bullish":
            sg = strong_green(b, frac)
            if sg and prev_strong_green:
                exit_px, exit_t, reason = nxt[0], nxt[1], "two_strong_bullish"
                break
            prev_strong_green = sg
        elif etype == "staged_zones":
            levels_now = [b.ema200_5m, stage_levels[1][1], stage_levels[2][1]]
            for k in range(3):
                if done[k]:
                    continue
                # the k-th part uses the first stage at/after k whose level is beyond the entry price
                for kk in range(k, 3):
                    lvl = levels_now[kk]
                    # level must lie in the trade's favour: below the entry for a short, above for a long
                    if lvl is not None and not pd.isna(lvl) and sign * (lvl - entry_px) > 0:
                        hit = (b.low <= lvl) if sign < 0 else (b.high >= lvl)
                        if hit:
                            fill = min(float(b.open), float(lvl)) if sign < 0 else max(float(b.open), float(lvl))
                            parts.append((ts.strftime("%Y-%m-%d %H:%M"), fill, f"stage{k + 1}:{stage_levels[kk][0]}"))
                            done[k] = True
                        break
            if all(done):
                exit_px, exit_t, reason = parts[-1][1], pd.Timestamp(parts[-1][0]), "staged_complete"
                break
        else:
            raise ValueError(f"unknown exit type {etype}")
    if exit_px is None:
        exit_px, exit_t, reason = float(day.iloc[-1].close), idx[-1] + FIVE, "data_end"
    if etype == "staged_zones":
        while len(parts) < 3:
            parts.append((pd.Timestamp(exit_t).strftime("%Y-%m-%d %H:%M"), float(exit_px), reason))
        pts = float(np.mean([sign * (p[1] - entry_px) for p in parts]))
        exit_t = max(pd.Timestamp(p[0]) for p in parts)
        exit_px = float(np.mean([p[1] for p in parts]))
        reason = "staged:" + ",".join(p[2] for p in parts)
    else:
        pts = sign * (exit_px - entry_px)
    return CTrade(date=idx[0].strftime("%Y-%m-%d"), side="LONG" if sign > 0 else "SHORT",
                  entry_time=idx[entry_i].strftime("%Y-%m-%d %H:%M"), entry_px=entry_px,
                  exit_time=pd.Timestamp(exit_t).strftime("%Y-%m-%d %H:%M"), exit_px=round(exit_px, 2),
                  exit_reason=reason, points=round(pts, 2), mfe=round(mfe, 2), mae=round(mae, 2),
                  hold_min=(pd.Timestamp(exit_t) - idx[entry_i]).total_seconds() / 60,
                  signal_time=idx[sig].strftime("%H:%M"), parts=parts)


# ── option layers ───────────────────────────────────────────────────────────

def option_layers(t: CTrade, cfg: dict, mod, real, real_days) -> Dict:
    from . import backtest as bt
    if not t.parts:
        return bt._option_layers(t, cfg, mod, real, real_days)
    outs = []
    for tm, px, _r in t.parts:          # staged: one 1-lot round trip per part, averaged (per-lot equivalent)
        sub = en.Trade(date=t.date, side=t.side, entry_time=t.entry_time, entry_px=t.entry_px, exit_time=tm,
                       exit_px=px, exit_reason="part", points=0.0, mfe=0.0, mae=0.0, hold_min=0.0,
                       vwap_source="futures_vwap")
        outs.append(bt._option_layers(sub, cfg, mod, real, real_days))
    o = {"strike": outs[0].get("strike"), "opt_type": outs[0].get("opt_type")}
    for key in ("B_net", "C_net"):
        vals = [x.get(key) for x in outs]
        o[key] = round(float(np.mean(vals)), 2) if all(v is not None for v in vals) else None
    return o


# ── metrics ─────────────────────────────────────────────────────────────────

def stats(T: pd.DataFrame, significance: bool = True) -> Dict:
    from .backtest import metrics
    if T.empty:
        return {"trades": 0}
    out = {"A_points": metrics(T.points.tolist(), T.hold_min.tolist(), T.mfe.tolist(), T.mae.tolist())}
    b = T["B_net"].dropna() if "B_net" in T else pd.Series(dtype=float)
    c = T["C_net"].dropna() if "C_net" in T else pd.Series(dtype=float)
    out["B_modelled_rs"] = metrics(b.tolist()) if len(b) else {"trades": 0}
    out["C_real_rs"] = metrics(c.tolist()) if len(c) else {"trades": 0}
    out["C_coverage"] = f"{len(c)}/{len(T)} trades priced from real snapshots"
    for per in ("IS", "OOS"):
        P = T[T.period == per]
        out[per] = {"A_points": metrics(P.points.tolist()) if len(P) else {"trades": 0},
                    "B_net_rs": round(float(P["B_net"].sum()), 2) if len(P) else 0.0}
    out["by_year"] = {int(y): {"trades": int(len(g)), "pf": metrics(g.points.tolist()).get("profit_factor"),
                               "exp": round(float(g.points.mean()), 2), "B": round(float(g.B_net.sum()), 2)}
                      for y, g in T.groupby("year")}
    out["long_short"] = {s: {"trades": int(len(g)), "pf": metrics(g.points.tolist()).get("profit_factor"),
                             "B": round(float(g.B_net.sum()), 2)} for s, g in T.groupby("side")}
    if significance and len(T) >= 10:
        out["bootstrap_points"] = block_bootstrap(T.sort_values("entry_time").points.tolist())
    elif not significance or len(T) < 10:
        out["bootstrap_points"] = "not computed: fewer than 10 trades (sample too small)"
    return out


# ── runner ──────────────────────────────────────────────────────────────────

def _run_version(conds_days, lev_df, by_day, fut_ok, entry_type, exit_spec, sign, cfg, positions, mod, real,
                 real_days, oos) -> pd.DataFrame:
    rows = []
    for d in conds_days:
        day = by_day.get(d)
        if day is None or len(day) < 60 or not fut_ok.get(d, False):
            continue
        t = simulate(day, lev_df.loc[d], entry_type, exit_spec, sign, cfg, positions)
        if t is None:
            continue
        r = {k: v for k, v in t.__dict__.items() if k != "parts"}
        r["parts"] = "|".join(f"{p[0][11:]}@{p[1]:.2f}:{p[2]}" for p in t.parts) if t.parts else ""
        r.update(option_layers(t, cfg, mod, real, real_days))
        r["period"] = "OOS" if d >= oos else "IS"
        r["year"] = d.year
        rows.append(r)
    return pd.DataFrame(rows)


def run_conditions(prep: Dict, cfg: dict, conditions: List[dict]) -> Dict:
    from .options_layers import ModelledLayer, RealLayer
    mod, real = ModelledLayer(cfg, prep["spot_close"]), RealLayer(cfg)
    real_days = set(real.days())
    oos = pd.Timestamp(cfg["out_of_sample_start"])
    lev = prep["levels"]
    days = prep["days"]
    out: Dict = {"conditions": {}, "trades": []}
    base_entry, base_exit = "vwap_context_long", {"type": "close_vs_ema", "period": 20}
    base = _run_version(days, lev, prep["by_day"], prep["fut_ok"], base_entry, base_exit, 1, cfg, None, mod, real,
                        real_days, oos)
    out["base_rule"] = stats(base)
    for cond in conditions:
        cid = cond["id"]
        sign = 1 if cond["bias"].upper() == "LONG" else -1
        etype = cond["entry"]["type"] if cond["entry"]["type"] != "close_vs_vwap" else base_entry
        own_exit = dict(cond["exit"])
        own_exit.setdefault("period", 20)
        d0 = pd.Timestamp(cond["date"])
        # reproduction on the condition's own date (version A logic, no positions filter)
        t0 = simulate(prep["by_day"][d0], lev.loc[d0], etype, own_exit, sign, cfg) if d0 in prep["by_day"] else None
        repro = None
        positions = None
        if t0 is not None:
            repro = {k: v for k, v in t0.__dict__.items()}
            repro.update(option_layers(t0, cfg, mod, real, real_days))
            sig_bar = prep["by_day"][d0].loc[pd.Timestamp(f"{cond['date']} {t0.signal_time}")]
            positions = primary_positions(sig_bar, lev.loc[d0])
        setup = {k: v for k, v in cond["setup"].items()}
        a_days = [d for d in days if match_all(lev.loc[d], setup)]
        A = _run_version(a_days, lev, prep["by_day"], prep["fut_ok"], etype, own_exit, sign, cfg, None, mod, real,
                         real_days, oos)
        if cid == "C001":
            B = base.copy()
            b_note = "C001 version B = the unfiltered base rule (already counted earlier; not recounted)"
        else:
            B = _run_version(days, lev, prep["by_day"], prep["fut_ok"], etype, own_exit, sign, cfg, positions, mod,
                             real, real_days, oos) if positions else pd.DataFrame()
            b_note = f"pre-declared test: own entry + own exit + primary positions {positions}"
        Cv = {}
        for p in (20, 50):
            Cv[f"ema{p}"] = _run_version(days, lev, prep["by_day"], prep["fut_ok"], etype,
                                         {"type": "close_vs_ema", "period": p}, sign, cfg,
                                         None if cid == "C001" else positions, mod, real, real_days, oos)
        exit_note = cond.get("exit", {}).get("type")
        out["conditions"][cid] = {
            "date": cond["date"], "bias": cond["bias"], "entry": etype, "exit": own_exit,
            "reproduction": repro, "primary_positions_at_own_signal": positions,
            "A": {"match_sessions": len(a_days), "dates": [d.strftime("%Y-%m-%d") for d in a_days],
                  "stats": stats(A, significance=len(a_days) >= 10),
                  "small_sample": len(a_days) < 10},
            "B": {"note": b_note, "stats": stats(B)},
            "C": {"ema20": stats(Cv["ema20"]), "ema50": stats(Cv["ema50"]),
                  "note": f"fixed exits replace the condition's own exit ({exit_note})"},
        }
        for name, T in (("A", A), ("B", B), ("C_ema20", Cv["ema20"]), ("C_ema50", Cv["ema50"])):
            if not T.empty:
                T = T.copy()
                T.insert(0, "version", name)
                T.insert(0, "condition", cid)
                out["trades"].append(T)
    out["trades"] = pd.concat(out["trades"], ignore_index=True) if out["trades"] else pd.DataFrame()
    return out


# ── markdown ────────────────────────────────────────────────────────────────

def _row(s: Dict) -> str:
    a = s.get("A_points", {}) if s else {}
    if not a or not a.get("trades"):
        return "0 trades"
    b, c = s.get("B_modelled_rs", {}), s.get("C_real_rs", {})
    return (f"{a['trades']} tr | win {a['win_rate']}% | avg W {a['avg_win']} / L {a['avg_loss']} | PF {a['profit_factor']} | "
            f"exp {a['expectancy']} | maxDD {a['max_drawdown']} | hold {a['avg_hold_min']}m | "
            f"B {b.get('total', 0):+,.0f} (PF {b.get('profit_factor')}) | C {c.get('total', 0) if c.get('trades') else 0:+,.0f} "
            f"({s.get('C_coverage')}) | IS PF {s['IS']['A_points'].get('profit_factor')} / OOS PF "
            f"{s['OOS']['A_points'].get('profit_factor')} | {s.get('bootstrap_points')}")


def to_markdown(r: Dict, ledger_total: int) -> str:
    thr = 0.05 / ledger_total if ledger_total else 0.05
    L = [f"## All conditions C001-C007 (versions A/B/C) — Bonferroni threshold for {ledger_total} tests: p < {thr:.5f}",
         "Version B for C002-C007 = pre-declared tests 25-30. Versions A and C are descriptive (not counted). "
         "Every result is EXPLORATORY unless a version-B bootstrap p (mean <= 0) is below the threshold.", "",
         f"Unfiltered base rule (C001 B): {_row(r['base_rule'])}", ""]
    for cid, c in r["conditions"].items():
        rp = c["reproduction"]
        L.append(f"### {cid} ({c['date']}, {c['bias']}) — entry `{c['entry']}`, exit `{c['exit'].get('type')}`")
        if rp:
            L.append(f"- Reproduction on {c['date']}: signal {rp['signal_time']}, entry {rp['entry_time'][11:]} @ "
                     f"{rp['entry_px']}, exit {rp['exit_time'][11:]} @ {rp['exit_px']} ({rp['exit_reason']}), "
                     f"{rp['points']:+.2f} pts, B {rp.get('B_net')}, C {rp.get('C_net')}")
        else:
            L.append(f"- Reproduction on {c['date']}: NO signal under the fixed rules")
        L.append(f"- Primary positions at own signal candle (version B filter): {c['primary_positions_at_own_signal']}")
        A = c["A"]
        L.append(f"- **A exact** ({A['match_sessions']} sessions{' — SAMPLE TOO SMALL, no significance' if A['small_sample'] else ''}): "
                 f"{_row(A['stats'])}")
        L.append(f"  - dates: {', '.join(A['dates'][:20])}{' ...' if len(A['dates']) > 20 else ''}")
        L.append(f"- **B primary** ({c['B']['note']}): {_row(c['B']['stats'])}")
        by = c["B"]["stats"].get("by_year", {})
        if by:
            L.append("  - by year: " + "; ".join(f"{y}: n={v['trades']} PF={v['pf']} B={v['B']:+,.0f}" for y, v in by.items()))
        L.append(f"- **C fixed exit 20 EMA**: {_row(c['C']['ema20'])}")
        L.append(f"- **C fixed exit 50 EMA**: {_row(c['C']['ema50'])}  ({c['C']['note']})")
        L.append("")
    return "\n".join(L) + "\n"
