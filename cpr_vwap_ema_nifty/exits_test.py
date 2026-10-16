"""
Pre-declared alternative EXIT test (declared by the user 2026-10-16) on the
UNCHANGED version-B entries of C001-C007 (C001 B = the base rule) plus the H-D
short mirror. Separate code path: R001, its exit and fingerprint are untouched.

Entries: version B of each source (own entry trigger + primary-level positions
from its own date; C001 = base rule without positions), next-bar-open fill,
one trade per source per day, forced exit at the 15:20 bar.

Exits (exactly these; no variants):
  E1  5m close back across futures VWAP against the trade -> next open
  E2  5m close back across the 1m 200 EMA against the trade -> next open
  E3  5m close back across the 5m 200 EMA against the trade -> next open
  E4  trailing stop 3 x ATR(14) (Wilder, continuous 5m spot candles) from the best
      price since entry, ATR fixed at the SIGNAL candle (last completed candle
      before entry); intrabar price stop; each candle is checked against the stop
      from earlier candles, then the best price is updated
  E5  target = nearest primary level beyond entry at entry time (futures VWAP
      converted to spot with that candle's basis, CPR BC/TC, PDH/R1 zone edges,
      PDL/S1 zone edges); intrabar touch fills at the level (or the open if it
      opened beyond); stop = 5m close back across VWAP -> next open; no level
      beyond entry -> stop / 15:20 only
  REF20 / REF50: 5m close across the 5m 20 / 50 EMA (reference, already counted)

Pools: long = base rule + C003 + C005; short = C002 + C004 + C006 + C007 + H-D.
Split for this test: in-sample 2020-07-01..2023-12-31, out-of-sample 2024-01-01..
Screen ("holding up", not proof): PF(points) > 1.15 in both periods, layer B > 0
in both, and the out-of-sample block-bootstrap 95% interval (points) entirely
above zero. Tests 31-40 = E1..E5 x {long, short}; references and per-condition
tables are descriptive.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import conditions_backtest as cb
from . import engine as en
from .hypothesis_tests import block_bootstrap, primary_positions, zone_position

EXITS = ("E1", "E2", "E3", "E4", "E5")
REFS = ("REF20", "REF50")
LONG_SOURCES = ("C001", "C003", "C005")
SHORT_SOURCES = ("C002", "C004", "C006", "C007", "H-D")
IS_END = pd.Timestamp("2023-12-31")


# ── ATR (no lookahead) ──────────────────────────────────────────────────────

def wilder_atr(frame: pd.DataFrame, n: int = 14) -> pd.Series:
    """ATR(n) with Wilder smoothing over continuous 5m candles; value at a candle
    uses that candle and earlier ones only."""
    prev_close = frame["close"].shift(1)
    tr = pd.concat([frame["high"] - frame["low"], (frame["high"] - prev_close).abs(),
                    (frame["low"] - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False).mean()


# ── entries ─────────────────────────────────────────────────────────────────

def _hd_signal(day: pd.DataFrame, lev: pd.Series, cfg: dict) -> Optional[int]:
    """H-D short mirror of R001 (same as engine SHORT + r1_pdh_break + full context)."""
    t_first = pd.Timestamp(cfg["session"]["first_signal_bar"]).time()
    t_last = pd.Timestamp(cfg["session"]["last_signal_bar"]).time()
    lvl = min(lev["S1"], lev["PDL"])
    for i, (ts, b) in enumerate(day.iterrows()):
        if i >= len(day) - 1:
            break
        if not (t_first <= ts.time() <= t_last) or pd.isna(b.fut_close) or pd.isna(b.fut_vwap):
            continue
        if b.fut_close < b.fut_vwap and b.close < b.ema20_5m and b.close < b.ema50_5m and b.close < b.ema200_5m \
                and b.close < b.ema200_1m and b.close < lvl:
            return i
    return None


def b_entries(prep: Dict, cfg: dict, conditions: List[dict]) -> pd.DataFrame:
    lev, days, by_day, fut_ok = prep["levels"], prep["days"], prep["by_day"], prep["fut_ok"]
    t_force = pd.Timestamp(cfg["session"]["force_exit_bar"]).time()
    rows = []
    sources = []
    for c in conditions:
        sign = 1 if c["bias"].upper() == "LONG" else -1
        et = c["entry"]["type"] if c["entry"]["type"] != "close_vs_vwap" else "vwap_context_long"
        positions = None
        if c["id"] != "C001":
            d0 = pd.Timestamp(c["date"])
            s0 = cb.find_signal(by_day[d0], lev.loc[d0], et, cfg)
            positions = primary_positions(by_day[d0].iloc[s0], lev.loc[d0]) if s0 is not None else None
            if positions is None:
                continue
        sources.append((c["id"], sign, et, positions))
    sources.append(("H-D", -1, "hd", None))
    for src, sign, et, positions in sources:
        for d in days:
            day = by_day.get(d)
            if day is None or len(day) < 60 or not fut_ok.get(d, False):
                continue
            L = lev.loc[d]
            sig = _hd_signal(day, L, cfg) if et == "hd" else cb.find_signal(day, L, et, cfg)
            if sig is None:
                continue
            if positions is not None and primary_positions(day.iloc[sig], L) != positions:
                continue
            if day.index[sig + 1].time() >= t_force:
                continue
            rows.append({"source": src, "date": d, "sig_i": sig, "sign": sign})
    return pd.DataFrame(rows)


# ── E5 target ───────────────────────────────────────────────────────────────

def e5_target(sig_bar: pd.Series, lev: pd.Series, entry_px: float, sign: int) -> Optional[float]:
    """Nearest primary level beyond entry (in the trade's direction), at entry time."""
    cands = [sig_bar["fut_vwap"] - (sig_bar["fut_close"] - sig_bar["close"]),
             lev["BC"], lev["TC"], lev["PDH"], lev["R1"], lev["PDL"], lev["S1"]]
    beyond = [float(c) for c in cands if not pd.isna(c) and sign * (float(c) - entry_px) > 0]
    if not beyond:
        return None
    return min(beyond, key=lambda c: abs(c - entry_px))


# ── exit simulation ─────────────────────────────────────────────────────────

def simulate_exit(day: pd.DataFrame, lev: pd.Series, sig_i: int, sign: int, code: str, atr_entry: float,
                  cfg: dict) -> en.Trade:
    idx = list(day.index)
    entry_i = sig_i + 1
    entry_px = float(day.iloc[entry_i].open)
    t_force = pd.Timestamp(cfg["session"]["force_exit_bar"]).time()
    best = entry_px
    stop = entry_px - sign * 3.0 * atr_entry if code == "E4" else None
    target = e5_target(day.iloc[sig_i], lev, entry_px, sign) if code == "E5" else None
    mfe = mae = 0.0
    exit_px = exit_t = reason = None
    for j in range(entry_i, len(idx)):
        b = day.iloc[j]
        ts = idx[j]
        if ts.time() >= t_force:
            exit_px, exit_t, reason = float(b.open), ts, "force_exit"
            break
        nxt = (float(day.iloc[j + 1].open), idx[j + 1]) if j + 1 < len(idx) else (float(b.close), ts)
        if code == "E4":
            hit = (b.low <= stop) if sign > 0 else (b.high >= stop)
            if hit:
                fill = min(float(b.open), stop) if sign > 0 else max(float(b.open), stop)
                mfe = max(mfe, (b.high - entry_px) if sign > 0 else (entry_px - b.low))
                mae = max(mae, (entry_px - b.low) if sign > 0 else (b.high - entry_px))
                exit_px, exit_t, reason = fill, ts, "atr_trail"
                break
        if code == "E5" and target is not None:
            hit = (b.high >= target) if sign > 0 else (b.low <= target)
            if hit:
                fill = max(float(b.open), target) if sign > 0 else min(float(b.open), target)
                mfe = max(mfe, sign * (fill - entry_px))
                mae = max(mae, (entry_px - b.low) if sign > 0 else (b.high - entry_px))
                exit_px, exit_t, reason = fill, ts, "e5_target"
                break
        mfe = max(mfe, (b.high - entry_px) if sign > 0 else (entry_px - b.low))
        mae = max(mae, (entry_px - b.low) if sign > 0 else (b.high - entry_px))
        crossed = False
        if code in ("E1", "E5"):
            crossed = sign * (b.fut_close - b.fut_vwap) < 0
        elif code == "E2":
            crossed = sign * (b.close - b.ema200_1m) < 0
        elif code == "E3":
            crossed = sign * (b.close - b.ema200_5m) < 0
        elif code == "REF20":
            crossed = sign * (b.close - b.ema20_5m) < 0
        elif code == "REF50":
            crossed = sign * (b.close - b.ema50_5m) < 0
        if crossed:
            exit_px, exit_t, reason = nxt[0], nxt[1], f"{code}_close_across"
            break
        if code == "E4":
            best = max(best, float(b.high)) if sign > 0 else min(best, float(b.low))
            stop = best - sign * 3.0 * atr_entry
    if exit_px is None:
        exit_px, exit_t, reason = float(day.iloc[-1].close), idx[-1], "data_end"
    return en.Trade(date=idx[0].strftime("%Y-%m-%d"), side="LONG" if sign > 0 else "SHORT",
                    entry_time=idx[entry_i].strftime("%Y-%m-%d %H:%M"), entry_px=entry_px,
                    exit_time=pd.Timestamp(exit_t).strftime("%Y-%m-%d %H:%M"), exit_px=round(float(exit_px), 2),
                    exit_reason=reason, points=round(sign * (float(exit_px) - entry_px), 2), mfe=round(mfe, 2),
                    mae=round(mae, 2), hold_min=(pd.Timestamp(exit_t) - idx[entry_i]).total_seconds() / 60,
                    vwap_source="futures_vwap")


# ── run ─────────────────────────────────────────────────────────────────────

def day_bootstrap(T: pd.DataFrame, n: int = 5000, seed: int = 3) -> Dict:
    """Resample whole SESSIONS (all trades of a day together): pooled sources often fire on the
    same day, so trades are not independent. Mean points per trade, 95% CI, P(mean <= 0)."""
    if len(T) < 20:
        return {"note": "too few trades"}
    g = T.groupby("date")["points"]
    s, c = g.sum().to_numpy(), g.count().to_numpy()
    rng = np.random.default_rng(seed)
    means = np.array([(lambda ix: s[ix].sum() / c[ix].sum())(rng.integers(0, len(s), len(s))) for _ in range(n)])
    lo, hi = np.percentile(means, [2.5, 97.5])
    return {"days": int(len(s)), "mean": round(float(T.points.mean()), 2),
            "ci95": [round(float(lo), 2), round(float(hi), 2)], "p_mean_le_0": round(float((means <= 0).mean()), 4)}


def _screen(T: pd.DataFrame) -> Dict:
    from .backtest import metrics
    I, O = T[T.split == "IS"], T[T.split == "OOS"]
    pf_i = metrics(I.points.tolist()).get("profit_factor") if len(I) else None
    pf_o = metrics(O.points.tolist()).get("profit_factor") if len(O) else None
    b_i, b_o = float(I.B_net.sum()), float(O.B_net.sum())
    boot_o = block_bootstrap(O.sort_values("entry_time").points.tolist())
    ci = boot_o.get("ci95") if isinstance(boot_o, dict) else None
    passed = bool(pf_i and pf_o and pf_i > 1.15 and pf_o > 1.15 and b_i > 0 and b_o > 0 and ci and ci[0] > 0)
    return {"IS_pf": pf_i, "OOS_pf": pf_o, "IS_B": round(b_i, 2), "OOS_B": round(b_o, 2), "OOS_ci95": ci,
            "holding_up": passed}


def group_report(T: pd.DataFrame) -> Dict:
    from .backtest import metrics
    if T.empty:
        return {"trades": 0}
    out = {"A_points": metrics(T.points.tolist(), T.hold_min.tolist(), T.mfe.tolist(), T.mae.tolist()),
           "B_modelled_rs": metrics(T.B_net.dropna().tolist()) if T.B_net.notna().any() else {"trades": 0},
           "C_real_rs": metrics(T.C_net.dropna().tolist()) if "C_net" in T and T.C_net.notna().any() else {"trades": 0},
           "C_coverage": f"{int(T.C_net.notna().sum()) if 'C_net' in T else 0}/{len(T)} trades priced from real snapshots",
           "by_year": {int(y): {"trades": int(len(g)), "pf": metrics(g.points.tolist()).get("profit_factor"),
                                "B": round(float(g.B_net.sum()), 2)} for y, g in T.groupby("year")},
           "bootstrap_points_full": block_bootstrap(T.sort_values("entry_time").points.tolist()),
           "day_bootstrap_full": day_bootstrap(T),
           "day_bootstrap_oos": day_bootstrap(T[T.split == "OOS"]),
           "screen": _screen(T)}
    return out


def run(prep: Dict, cfg: dict, conditions: List[dict]) -> Dict:
    from . import backtest as bt
    from .options_layers import ModelledLayer, RealLayer
    mod, real = ModelledLayer(cfg, prep["spot_close"]), RealLayer(cfg)
    real_days = set(real.days())
    atr = wilder_atr(prep["frame"])
    E = b_entries(prep, cfg, conditions)
    rows = []
    for _, e in E.iterrows():
        day, L = prep["by_day"][e.date], prep["levels"].loc[e.date]
        atr_entry = float(atr.loc[day.index[int(e.sig_i)]])
        for code in EXITS + REFS:
            t = simulate_exit(day, L, int(e.sig_i), int(e.sign), code, atr_entry, cfg)
            r = {k: v for k, v in t.__dict__.items() if k != "extra"}
            r.update(bt._option_layers(t, cfg, mod, real, real_days))
            r.update({"source": e.source, "exit": code, "year": e.date.year,
                      "split": "IS" if e.date <= IS_END else "OOS", "atr_entry": round(atr_entry, 2)})
            rows.append(r)
    T = pd.DataFrame(rows)
    if "C_net" not in T:
        T["C_net"] = np.nan
    res: Dict = {"entry_counts": E.source.value_counts().to_dict(), "pooled": {}, "per_condition": {}}
    for side, srcs in (("long", LONG_SOURCES), ("short", SHORT_SOURCES)):
        for code in EXITS + REFS:
            S = T[(T.exit == code) & T.source.isin(srcs)]
            res["pooled"][f"{code}|{side}"] = group_report(S)
    for src in LONG_SOURCES + SHORT_SOURCES:
        for code in EXITS + REFS:
            S = T[(T.exit == code) & (T.source == src)]
            if len(S):
                from .backtest import metrics
                m = metrics(S.points.tolist())
                res["per_condition"][f"{src}|{code}"] = {"trades": m.get("trades"), "pf": m.get("profit_factor"),
                                                         "B": round(float(S.B_net.sum()), 2)}
    res["_trades"] = T
    return res


def to_markdown(r: Dict, ledger_total: int) -> str:
    thr = 0.05 / ledger_total if ledger_total else 0.05
    L = [f"## Alternative exits E1-E5 on unchanged version-B entries (tests 31-40; threshold p < {thr:.5f})",
         f"Entries per source: {r['entry_counts']}. Split: IS 2020-07..2023-12, OOS 2024-01..2026-10-16. "
         "Screen = PF>1.15 both periods, layer B > 0 both, OOS bootstrap CI above zero (screen, not proof). "
         "REF20/REF50 and per-condition rows are descriptive. Everything is EXPLORATORY.", "",
         "Pooled sources often fire on the same day, so trades are not independent: the DAY-clustered "
         "p (resampling whole sessions) is the one compared with the threshold.", "",
         "| Exit | Side | Trades | Win% | PF | Exp pts | B total | IS PF / OOS PF | IS B / OOS B | OOS CI (trade blocks) | trade p | day-clustered p | Screen |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for key, g in r["pooled"].items():
        code, side = key.split("|")
        a, b, s = g["A_points"], g["B_modelled_rs"], g["screen"]
        bs = g["bootstrap_points_full"]
        L.append(f"| {code} | {side} | {a['trades']} | {a['win_rate']} | {a['profit_factor']} | {a['expectancy']} | "
                 f"{b.get('total', 0):+,.0f} | {s['IS_pf']} / {s['OOS_pf']} | {s['IS_B']:+,.0f} / {s['OOS_B']:+,.0f} | "
                 f"{s['OOS_ci95']} | {bs.get('p_mean_le_0') if isinstance(bs, dict) else ''} | "
                 f"{g['day_bootstrap_full'].get('p_mean_le_0')} | {'PASS' if s['holding_up'] else 'fail'} |")
    L += ["", "Per pooled group detail (avg win/loss, max DD, hold, layer C coverage, by year):"]
    for key, g in r["pooled"].items():
        a = g["A_points"]
        L.append(f"- {key}: avg W {a['avg_win']} / L {a['avg_loss']} | maxDD {a['max_drawdown']} | hold {a['avg_hold_min']}m | "
                 f"C {g['C_real_rs'].get('total', 0) if g['C_real_rs'].get('trades') else 0:+,.0f} ({g['C_coverage']}) | "
                 "years " + "; ".join(f"{y}: n={v['trades']} PF={v['pf']} B={v['B']:+,.0f}" for y, v in g["by_year"].items()))
    L += ["", "Per-condition (DESCRIPTIVE, small samples, not for choosing an exit):"]
    for key, v in r["per_condition"].items():
        L.append(f"- {key}: n={v['trades']} PF={v['pf']} B={v['B']:+,.0f}")
    return "\n".join(L) + "\n"
