#!/usr/bin/env python3
"""
edge_report.py — measured-edge analytics over labelled signals.

The analytics layer competitor platforms (Streak/Tradetron dashboards,
AlgoTest result matrices) provide, built on OUR data: every signal logged by
the live engine and labelled by the nightly triple-barrier job (+1 hit target
first / -1 hit stop first / 0 timeout).

Answers, per bucket, with sample sizes shown and small buckets suppressed:
  - which STRATEGIES actually win more than they lose
  - which REGIME / HOUR / VIX band / CONFLUENCE tier the ensemble works in
  - whether high engine scores mean anything (score-decile lift)

Output: console table + edge_report.json (+ optional Telegram via --telegram).
Read-only: never modifies the signal log. Run any time:
    python3 edge_report.py [--days 30] [--min-n 30] [--telegram]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Any

DB = Path(os.getenv("SIGNAL_LOG_DB", "signal_log.db"))
OUT = Path("edge_report.json")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def wilson_low(wins: int, n: int, z: float = 1.96) -> float:
    """Lower bound of the Wilson 95% interval — honest WR for small samples."""
    if n == 0:
        return 0.0
    p = wins / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - margin) / denom


def fetch(days: int) -> List[sqlite3.Row]:
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT side, strategy, regime, score, confluence, hour_of_day,
                  india_vix, max_adverse_move,
                  max_favorable_move, executed, tb_label
           FROM signal_log
           WHERE tb_label IN (-1, 0, 1) AND signal_date >= ?""",
        (cutoff,)).fetchall()
    conn.close()
    return rows


def vix_band(v) -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "unknown"
    if v < 13:  return "<13 (calm)"
    if v < 16:  return "13-16"
    if v < 20:  return "16-20"
    return ">20 (stressed)"


def score_bucket(s) -> str:
    try:
        s = float(s)
    except (TypeError, ValueError):
        return "unknown"
    if s < 7:   return "<7"
    if s < 12:  return "7-12"
    if s < 18:  return "12-18"
    return ">=18"


def bucketize(rows, key_fn) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        k = str(key_fn(r) or "unknown")
        b = out.setdefault(k, {"n": 0, "win": 0, "loss": 0, "timeout": 0,
                               "mae_sum": 0.0, "mfe_sum": 0.0})
        b["n"] += 1
        if r["tb_label"] == 1:    b["win"] += 1
        elif r["tb_label"] == -1: b["loss"] += 1
        else:                     b["timeout"] += 1
        try:
            b["mae_sum"] += float(r["max_adverse_move"] or 0)
        except (TypeError, ValueError):
            pass
        try:
            b["mfe_sum"] += float(r["max_favorable_move"] or 0)
        except (TypeError, ValueError):
            pass
    for b in out.values():
        decided = b["win"] + b["loss"]
        b["win_rate"] = round(b["win"] / decided, 3) if decided else None
        b["wr_wilson_low"] = round(wilson_low(b["win"], decided), 3) if decided else None
        b["avg_mae_pct"] = round(b["mae_sum"] / b["n"], 3) if b["n"] else None
        b["avg_mfe_pct"] = round(b["mfe_sum"] / b["n"], 3) if b["n"] else None
        del b["mae_sum"], b["mfe_sum"]
    return out


def mae_mfe_summary(rows) -> Dict[str, Any]:
    """
    Global MAE/MFE stats — answers 'are we leaving money on the table?'
    MFE > target_pct on losing trades → too-tight stop.
    MAE > stop_pct on winning trades → too-tight stop entry.
    """
    wins   = [r for r in rows if r["tb_label"] == 1]
    losses = [r for r in rows if r["tb_label"] == -1]

    def safe_avg(items, col):
        vals = [float(r[col] or 0) for r in items if r[col] is not None]
        return round(sum(vals) / len(vals), 3) if vals else None

    return {
        "wins_avg_mae":   safe_avg(wins,   "max_adverse_move"),
        "wins_avg_mfe":   safe_avg(wins,   "max_favorable_move"),
        "losses_avg_mae": safe_avg(losses, "max_adverse_move"),
        "losses_avg_mfe": safe_avg(losses, "max_favorable_move"),
        "mfe_over_mae_ratio": None,  # filled below
    }


def fmt_section(title: str, buckets: Dict[str, Dict], min_n: int,
                overall_wr: float) -> List[str]:
    lines = [f"\n── {title} " + "─" * max(0, 46 - len(title))]
    shown = {k: v for k, v in buckets.items() if v["n"] >= min_n}
    hidden = len(buckets) - len(shown)
    for k, v in sorted(shown.items(), key=lambda x: -(x[1]["win_rate"] or 0)):
        wr = v["win_rate"]
        edge = "" if wr is None else (
            " ▲" if v["wr_wilson_low"] > overall_wr else
            (" ▼" if wr < overall_wr - 0.10 else ""))
        lines.append(
            f"  {k[:26]:26s} n={v['n']:<5d} WR={wr if wr is not None else '—'}"
            f" (wilson_low={v['wr_wilson_low']}) timeouts={v['timeout']}{edge}")
    if hidden:
        lines.append(f"  … {hidden} bucket(s) hidden (n < {min_n} — too few to judge)")
    return lines


def fetch_pnl_by_strategy(days: int) -> Dict[str, Dict[str, Any]]:
    """
    Pull realized P&L per strategy from trades.db.
    Returns dict keyed by strategy name with:
      total_pnl, n_trades, win_rate, avg_pnl, best_trade, worst_trade
    """
    trades_db = Path(os.getenv("TRADES_DB", "trades.db"))
    if not trades_db.exists():
        return {}
    cutoff_epoch = (date.today() - timedelta(days=days)).strftime("%s")
    try:
        conn = sqlite3.connect(str(trades_db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT strategy, realized_pnl, r_multiple
               FROM   trades
               WHERE  status = 'CLOSED'
                 AND  exit_time >= ?""",
            (cutoff_epoch,),
        ).fetchall()
        conn.close()
    except Exception:
        return {}

    buckets: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        strat   = str(r["strategy"] or "unknown")
        pnl     = float(r["realized_pnl"] or 0)
        r_mult  = r["r_multiple"]
        b = buckets.setdefault(strat, {
            "n_trades": 0, "total_pnl": 0.0, "wins": 0,
            "gross_wins": 0.0, "gross_losses": 0.0,
            "best_trade": None, "worst_trade": None,
            "r_multiples": [],
        })
        b["n_trades"]    += 1
        b["total_pnl"]   += pnl
        if pnl > 0:
            b["wins"]        += 1
            b["gross_wins"]  += pnl
        else:
            b["gross_losses"] += abs(pnl)
        if b["best_trade"] is None or pnl > b["best_trade"]:
            b["best_trade"] = round(pnl, 2)
        if b["worst_trade"] is None or pnl < b["worst_trade"]:
            b["worst_trade"] = round(pnl, 2)
        try:
            if r_mult is not None:
                b["r_multiples"].append(float(r_mult))
        except (TypeError, ValueError):
            pass

    for b in buckets.values():
        n = b["n_trades"]
        b["total_pnl"]     = round(b["total_pnl"], 2)
        b["avg_pnl"]       = round(b["total_pnl"] / n, 2) if n else 0.0
        b["win_rate"]      = round(b["wins"] / n, 3) if n else 0.0
        gl = b.pop("gross_losses")
        gw = b.pop("gross_wins")
        # Profit Factor = Gross Profit / Gross Loss  (>1.5 is generally tradeable)
        b["profit_factor"] = round(gw / gl, 2) if gl > 0 else (float("inf") if gw > 0 else 0.0)
        rm = b.pop("r_multiples")
        if rm:
            b["avg_r"]     = round(sum(rm) / len(rm), 2)
            b["median_r"]  = round(sorted(rm)[len(rm) // 2], 2)
        else:
            b["avg_r"] = b["median_r"] = None
    return buckets


def fmt_pnl_section(buckets: Dict[str, Dict], min_n: int) -> List[str]:
    """Format the per-strategy P&L and profit factor attribution table."""
    lines = ["\n── BY STRATEGY (Realized P&L · Profit Factor from trades.db) " + "─" * 5]
    shown = {k: v for k, v in buckets.items() if v["n_trades"] >= min_n}
    hidden = len(buckets) - len(shown)
    # Sort by profit_factor descending (primary quality metric)
    for k, v in sorted(shown.items(), key=lambda x: -(x[1]["profit_factor"] or 0)):
        pf   = v["profit_factor"]
        pf_s = f"{pf:.2f}" if pf != float("inf") else "∞"
        pf_flag = " ✓" if isinstance(pf, float) and pf >= 1.5 else (" ✗" if isinstance(pf, float) and pf < 1.0 else "")
        r_s = f"  avgR={v['avg_r']:+.2f}" if v["avg_r"] is not None else ""
        sign = "▲" if v["total_pnl"] > 0 else "▼"
        lines.append(
            f"  {sign} {k[:22]:22s} n={v['n_trades']:<4d} "
            f"PF={pf_s}{pf_flag}  WR={v['win_rate']:.0%}  "
            f"total=₹{v['total_pnl']:+,.0f}  avg=₹{v['avg_pnl']:+,.0f}{r_s}"
        )
    if hidden:
        lines.append(f"  … {hidden} strategy/ies hidden (n < {min_n})")
    if not shown:
        lines.append("  (No closed trades found in trades.db for this window)")
    lines.append("  Note: Profit Factor = Gross Profit / Gross Loss  (≥1.5 is tradeable)")
    return lines


def fetch_trade_duration(days: int) -> Dict[str, Any]:
    """
    Compute hold-time distribution from trades.db (entry_time → exit_time).
    Buckets: <15m, 15m–1h, 1h–4h, >4h.
    Tells us if profits cluster in one hold-time band (AlgoTest 'duration analysis').
    """
    trades_db = Path(os.getenv("TRADES_DB", "trades.db"))
    if not trades_db.exists():
        return {}
    cutoff_epoch = (date.today() - timedelta(days=days)).strftime("%s")
    try:
        conn = sqlite3.connect(str(trades_db))
        rows = conn.execute(
            """SELECT entry_time, exit_time, realized_pnl
               FROM trades WHERE status='CLOSED' AND exit_time >= ?""",
            (cutoff_epoch,),
        ).fetchall()
        conn.close()
    except Exception:
        return {}

    buckets = {"<15m": [], "15m-1h": [], "1h-4h": [], ">4h": []}
    for entry_t, exit_t, pnl in rows:
        try:
            dur_min = (float(exit_t) - float(entry_t)) / 60.0
            pnl = float(pnl or 0)
            if dur_min < 15:        key = "<15m"
            elif dur_min < 60:      key = "15m-1h"
            elif dur_min < 240:     key = "1h-4h"
            else:                   key = ">4h"
            buckets[key].append(pnl)
        except (TypeError, ValueError):
            pass

    result = {}
    for k, pnls in buckets.items():
        n = len(pnls)
        result[k] = {
            "n": n,
            "pnl": round(sum(pnls), 2) if pnls else 0.0,
            "avg_pnl": round(sum(pnls) / n, 2) if n else 0.0,
            "win_rate": round(sum(1 for p in pnls if p > 0) / n, 3) if n else 0.0,
        }
    return result


def fetch_consecutive_losses(days: int) -> Dict[str, Any]:
    """
    Consecutive loss streak analysis from trades.db.
    Reports: max observed streak, expected streak by probability (Bernoulli),
    and probability of N+ consecutive losses.
    """
    trades_db = Path(os.getenv("TRADES_DB", "trades.db"))
    if not trades_db.exists():
        return {}
    cutoff_epoch = (date.today() - timedelta(days=days)).strftime("%s")
    try:
        conn = sqlite3.connect(str(trades_db))
        rows = conn.execute(
            "SELECT realized_pnl FROM trades WHERE status='CLOSED' AND exit_time >= ? ORDER BY exit_time",
            (cutoff_epoch,),
        ).fetchall()
        conn.close()
    except Exception:
        return {}

    pnls = [float(r[0] or 0) for r in rows]
    if not pnls:
        return {}

    n = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    loss_rate = 1.0 - wins / n if n else 0.5

    # Observed max consecutive losses
    max_streak = cur = 0
    streaks = []
    for p in pnls:
        if p <= 0:
            cur += 1
            max_streak = max(max_streak, cur)
        else:
            if cur > 0:
                streaks.append(cur)
            cur = 0
    if cur > 0:
        streaks.append(cur)

    # Expected max consecutive loss run in n trials (geometric distribution)
    # E[max] ≈ log(n) / log(1/loss_rate)  when loss_rate > 0
    import math as _math
    if loss_rate > 0 and loss_rate < 1:
        expected_max = _math.log(n) / _math.log(1.0 / loss_rate)
    else:
        expected_max = 0.0

    # P(streak >= k) = 1 - (1-p^k)^(n-k+1) ≈ simple
    def prob_run(k):
        if loss_rate <= 0 or loss_rate >= 1:
            return 0.0
        return round(1.0 - (1.0 - loss_rate ** k) ** max(1, n - k + 1), 4)

    return {
        "n_trades":       n,
        "loss_rate":      round(loss_rate, 3),
        "max_observed":   max_streak,
        "expected_max":   round(expected_max, 1),
        "prob_3_consec":  prob_run(3),
        "prob_5_consec":  prob_run(5),
        "prob_7_consec":  prob_run(7),
        "streak_counts":  {str(i): streaks.count(i) for i in range(1, max_streak + 1)},
    }


def fmt_duration_section(dur: Dict[str, Any], min_n: int) -> List[str]:
    if not dur:
        return []
    lines = ["\n── TRADE DURATION DISTRIBUTION " + "─" * 30]
    lines.append("  Hold-time band   n     WR      Total P&L    Avg P&L")
    for k, v in dur.items():
        if v["n"] < 1:
            continue
        pnl_s = f"₹{v['pnl']:+,.0f}"
        avg_s = f"₹{v['avg_pnl']:+,.0f}"
        flag = "  ← edge" if v["win_rate"] >= 0.55 and v["n"] >= min_n else ""
        lines.append(f"  {k:12s}    {v['n']:<5} {v['win_rate']:.0%}   {pnl_s:>10s}   {avg_s:>8s}{flag}")
    return lines


def fmt_consec_loss_section(cl: Dict[str, Any]) -> List[str]:
    if not cl or not cl.get("n_trades"):
        return []
    lines = ["\n── CONSECUTIVE LOSS ANALYSIS " + "─" * 32]
    lines.append(
        f"  Trades: {cl['n_trades']}  Loss rate: {cl['loss_rate']:.0%}  "
        f"Max observed streak: {cl['max_observed']}"
    )
    lines.append(f"  Expected max streak in {cl['n_trades']} trades: {cl['expected_max']:.1f}")
    lines.append(f"  P(3+ consec losses): {cl['prob_3_consec']:.1%}  "
                 f"P(5+): {cl['prob_5_consec']:.1%}  P(7+): {cl['prob_7_consec']:.1%}")
    if cl["max_observed"] > cl["expected_max"] * 1.5:
        lines.append("  ⚠ Observed streak significantly exceeds statistical expectation — check for regime clustering")
    return lines


def fetch_r_distribution(days: int) -> Dict[str, Any]:
    """
    Pull r_multiple from trades.db and bucket into a text histogram.
    Buckets: <-2R, -2 to -1R, -1 to 0R, 0 to +1R, +1 to +2R, >+2R
    Returns dict: buckets (label→count), mean_r, median_r, n, expectancy.
    """
    trades_db = Path(os.getenv("TRADES_DB", "trades.db"))
    if not trades_db.exists():
        return {}
    cutoff_epoch = (date.today() - timedelta(days=days)).strftime("%s")
    try:
        conn = sqlite3.connect(str(trades_db))
        rows = conn.execute(
            "SELECT r_multiple FROM trades WHERE status='CLOSED' AND exit_time >= ?",
            (cutoff_epoch,),
        ).fetchall()
        conn.close()
    except Exception:
        return {}

    vals = []
    for (r,) in rows:
        try:
            if r is not None:
                vals.append(float(r))
        except (TypeError, ValueError):
            pass

    if not vals:
        return {}

    buckets = {
        "<-2R": 0, "-2 to -1R": 0, "-1 to 0R": 0,
        "0 to +1R": 0, "+1 to +2R": 0, ">+2R": 0,
    }
    for v in vals:
        if v < -2:          buckets["<-2R"] += 1
        elif v < -1:        buckets["-2 to -1R"] += 1
        elif v < 0:         buckets["-1 to 0R"] += 1
        elif v < 1:         buckets["0 to +1R"] += 1
        elif v < 2:         buckets["+1 to +2R"] += 1
        else:               buckets[">+2R"] += 1

    n = len(vals)
    sorted_v = sorted(vals)
    mean_r = round(sum(vals) / n, 3)
    median_r = round(sorted_v[n // 2], 3)
    expectancy = round(mean_r, 3)  # avg R = expectancy per trade
    return {
        "n": n, "mean_r": mean_r, "median_r": median_r,
        "expectancy": expectancy, "buckets": buckets,
    }


def fmt_r_distribution(rd: Dict[str, Any]) -> List[str]:
    """Render the R-multiple distribution as a text histogram."""
    if not rd or not rd.get("buckets"):
        return []
    n = rd["n"]
    if n == 0:
        return []
    lines = ["\n── R-MULTIPLE DISTRIBUTION " + "─" * 34]
    lines.append(f"  n={n}  mean={rd['mean_r']:+.2f}R  median={rd['median_r']:+.2f}R  "
                 f"expectancy={rd['expectancy']:+.3f}R/trade")
    lines.append("  (positive expectancy = system earns more than 1R risk on average)")
    max_cnt = max(rd["buckets"].values()) or 1
    bar_w = 30
    for label, cnt in rd["buckets"].items():
        pct = cnt / n * 100
        bar = "█" * int(cnt / max_cnt * bar_w)
        sign = "+" if "+" in label or ">" in label else ""
        lines.append(f"  {label:12s} {bar:<{bar_w}} {cnt:4d} ({pct:4.1f}%)")
    neg = sum(v for k, v in rd["buckets"].items() if k.startswith("-") or k.startswith("<"))
    pos = sum(v for k, v in rd["buckets"].items() if "+" in k or k.startswith(">"))
    if neg + pos > 0:
        skew = "positive (more big winners than losers)" if pos > neg else "negative (more big losers than winners)"
        lines.append(f"  Tail skew: {skew}")
    return lines


def build_report(days: int, min_n: int) -> dict:
    rows = fetch(days)
    wins = sum(1 for r in rows if r["tb_label"] == 1)
    losses = sum(1 for r in rows if r["tb_label"] == -1)
    timeouts = len(rows) - wins - losses
    decided = wins + losses
    overall_wr = wins / decided if decided else 0.0

    report = {
        "generated": date.today().isoformat(),
        "window_days": days,
        "total_labelled": len(rows),
        "wins": wins, "losses": losses, "timeouts": timeouts,
        "overall_win_rate": round(overall_wr, 3),
        "overall_wr_wilson_low": round(wilson_low(wins, decided), 3) if decided else None,
        "note": ("win = target hit before stop (triple barrier). Win rate "
                 "ALONE is not profit: net edge also needs target/stop ratio "
                 "and costs. UNVALIDATED for live use."),
        "by_strategy": bucketize(rows, lambda r: r["strategy"]),
        "by_regime": bucketize(rows, lambda r: r["regime"]),
        "by_hour": bucketize(rows, lambda r: f"{int(r['hour_of_day'] or -1):02d}h"),
        "by_vix": bucketize(rows, lambda r: vix_band(r["india_vix"])),
        "by_confluence": bucketize(rows, lambda r: r["confluence"]),
        "by_score": bucketize(rows, lambda r: score_bucket(r["score"])),
        "by_side": bucketize(rows, lambda r: r["side"]),
        "by_strategy_pnl": fetch_pnl_by_strategy(days),
        "mae_mfe": mae_mfe_summary(rows),
        "r_distribution": fetch_r_distribution(days),
        "trade_duration": fetch_trade_duration(days),
        "consecutive_losses": fetch_consecutive_losses(days),
    }
    return report


def render(report: dict, min_n: int) -> str:
    wr = report["overall_win_rate"]
    lines = [
        "═" * 60,
        f"EDGE REPORT — last {report['window_days']}d "
        f"({report['total_labelled']} labelled signals)",
        "═" * 60,
        f"Overall: {report['wins']}W / {report['losses']}L / "
        f"{report['timeouts']}T   WR={wr:.1%} "
        f"(wilson low {report['overall_wr_wilson_low']:.1%})",
        "▲ = bucket's conservative WR beats the overall average",
    ]
    overall = wr or 0.0
    for title, key in (("BY STRATEGY", "by_strategy"), ("BY REGIME", "by_regime"),
                       ("BY HOUR", "by_hour"), ("BY VIX BAND", "by_vix"),
                       ("BY CONFLUENCE", "by_confluence"), ("BY SCORE", "by_score"),
                       ("BY SIDE", "by_side")):
        lines += fmt_section(title, report[key], min_n, overall)
    lines += fmt_pnl_section(report.get("by_strategy_pnl", {}), min_n)

    # MAE / MFE diagnostic
    mm = report.get("mae_mfe", {})
    if mm and any(v is not None for v in mm.values()):
        lines += ["\n── MAE / MFE DIAGNOSTIC " + "─" * 37]
        lines.append("  Interpretation: MFE > stop on losers → stops too tight; MAE > target on winners → targets too tight")
        w_mfe = mm.get("wins_avg_mfe")
        w_mae = mm.get("wins_avg_mae")
        l_mfe = mm.get("losses_avg_mfe")
        l_mae = mm.get("losses_avg_mae")
        if w_mfe is not None:
            lines.append(f"  Winners  — avg MFE: {w_mfe:.2f}%  avg MAE: {w_mae:.2f}%")
        if l_mfe is not None:
            lines.append(f"  Losers   — avg MFE: {l_mfe:.2f}%  avg MAE: {l_mae:.2f}%")
        if l_mfe and l_mfe > 0 and l_mae:
            ratio = l_mfe / l_mae
            flag = "  ⚠ trades often rally before hitting stop — consider wider stops" if ratio > 1.3 else ""
            lines.append(f"  Loser MFE/MAE ratio: {ratio:.2f}{flag}")

    lines += fmt_r_distribution(report.get("r_distribution", {}))
    lines += fmt_duration_section(report.get("trade_duration", {}), min_n)
    lines += fmt_consec_loss_section(report.get("consecutive_losses", {}))

    lines += ["", report["note"]]
    return "\n".join(lines)


def telegram_send(text: str) -> None:
    import requests
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        print("telegram: TELEGRAM_BOT_TOKEN/CHAT_ID not set", file=sys.stderr)
        return
    # Telegram caps messages at 4096 chars — send the head
    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=10,
                  json={"chat_id": chat, "text": text[:4000]})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--min-n", type=int, default=30,
                    help="hide buckets below this sample size (default 30)")
    ap.add_argument("--telegram", action="store_true")
    args = ap.parse_args()

    if not DB.exists():
        print(f"{DB} not found"); return 1
    report = build_report(args.days, args.min_n)
    text = render(report, args.min_n)
    print(text)
    OUT.write_text(json.dumps(report, indent=1))
    print(f"\nsaved → {OUT}")
    if args.telegram:
        telegram_send(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
