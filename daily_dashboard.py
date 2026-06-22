#!/usr/bin/env python3
"""
daily_dashboard.py — one-screen consolidated status (audit gap #11).

The evidence is scattered across signal_log.db + several nightly JSON reports.
This pulls the headline numbers into a single view: signals/labels, last ML
pipeline run, validation scoreboard, modifier ranking, and the edge report.

Read-only and defensive — each section is wrapped so a missing/old file never
crashes the dashboard. Optional --telegram sends a compact version.

Usage:
    python daily_dashboard.py
    python daily_dashboard.py --telegram
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List

SIGNAL_DB = "signal_log.db"


def _load_json(path: str) -> Dict[str, Any]:
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return {}


def _signals_section() -> List[str]:
    out = ["📊 SIGNALS & LABELS"]
    try:
        conn = sqlite3.connect(SIGNAL_DB)
        try:
            total = conn.execute("SELECT COUNT(*) FROM signal_log").fetchone()[0]
            decided = conn.execute(
                "SELECT COUNT(*) FROM signal_log WHERE tb_label IN (1,-1)").fetchone()[0]
            wins = conn.execute(
                "SELECT COUNT(*) FROM signal_log WHERE tb_label = 1").fetchone()[0]
            days = conn.execute(
                "SELECT COUNT(DISTINCT signal_date) FROM signal_log "
                "WHERE tb_label IN (1,0,-1)").fetchone()[0]
            unlabelled = conn.execute(
                "SELECT COUNT(*) FROM signal_log WHERE tb_label IN (-2,-99)").fetchone()[0]
            corrupt = conn.execute(
                "SELECT COUNT(*) FROM signal_log WHERE tb_label IN (1,-1) "
                "AND (entry_price <= 0 OR outcome_price <= 0)").fetchone()[0]
        finally:
            conn.close()
        wr = (100.0 * wins / decided) if decided else 0.0
        cov = (100.0 * decided / total) if total else 0.0
        out += [
            f"  rows={total}  decided={decided} ({cov:.0f}% labelled)  unlabelled={unlabelled}",
            f"  win_rate={wr:.1f}%  labelled_days={days}  bad_price_rows={corrupt}",
        ]
        if days < 20:
            out.append(f"  ⚠ only {days} labelled day(s) — evidence reports are DATA-GATED")
    except Exception as exc:
        out.append(f"  (signal_log unavailable: {exc})")
    return out


def _pipeline_section() -> List[str]:
    d = _load_json("ml_pipeline_last_run.json")
    if not d:
        return ["🤖 LAST ML PIPELINE", "  (no ml_pipeline_last_run.json)"]
    return [
        "🤖 LAST ML PIPELINE",
        f"  run={d.get('timestamp','?')}  signals_used={d.get('signals_used','?')}  "
        f"elapsed={d.get('elapsed_sec','?')}s",
        f"  overall_wr={_pct(d.get('overall_wr'))}  ml_auc={_num(d.get('ml_auc'))}  "
        f"danger_zones={d.get('danger_zones','?')}  per_symbol_models={d.get('per_symbol_models','?')}",
        f"  edge_conclusion: {str(d.get('edge_conclusion','n/a'))[:80]}",
    ]


def _validation_section() -> List[str]:
    d = _load_json("validation_results.json")
    res = d.get("results") if isinstance(d, dict) else None
    if not isinstance(res, dict) or not res:
        return ["✅ VALIDATION SCOREBOARD", "  (no validation_results.json)"]
    verdicts: Dict[str, int] = {}
    best = (None, -1.0, "?", None, None, None)
    for name, r in res.items():
        if not isinstance(r, dict):
            continue
        verdicts[r.get("verdict", "?")] = verdicts.get(r.get("verdict", "?"), 0) + 1
        dsr = r.get("deflated_sharpe")
        if isinstance(dsr, (int, float)) and dsr > best[1]:
            best = (
                name,
                float(dsr),
                r.get("verdict", "?"),
                r.get("holdout_pnl"),
                r.get("holdout_sharpe"),
                r.get("holdout_trades"),
            )
    vs = "  ".join(f"{k}={v}" for k, v in sorted(verdicts.items()))
    holdout = (
        f"holdout_pnl={best[3]} holdout_sharpe={best[4]} trades={best[5]}"
        if best[3] is not None or best[4] is not None or best[5] is not None
        else "no locked-holdout PASS"
    )
    return [
        "✅ VALIDATION SCOREBOARD",
        f"  {len(res)} strategies  |  {vs}",
        f"  best DSR: {best[0]} = {best[1]:.3f}  verdict={best[2]}  {holdout}",
        "  live edge gate: verdict must be PASS with DSR>=0.95 + positive locked holdout",
    ]


def _modifier_section() -> List[str]:
    d = _load_json("modifier_edge_report.json")
    s = d.get("summary") if isinstance(d, dict) else None
    if not isinstance(s, dict):
        return ["🔧 MODIFIER RANKING", "  (no modifier_edge_report.json)"]
    def names(k: str) -> str:
        v = s.get(k, []) or []
        return f"{len(v)} ({', '.join(v[:4])}{'…' if len(v) > 4 else ''})" if v else "0"
    return [
        "🔧 MODIFIER RANKING",
        f"  n_signals={d.get('n_signals','?')}  corrupt_dropped={d.get('corrupt_dropped','?')}",
        f"  HELPS: {names('helps')}",
        f"  HURTS: {names('hurts')}",
        f"  DEAD:  {names('dead')}   NOISE: {names('noise')}",
    ]


def _edge_section() -> List[str]:
    d = _load_json("edge_report.json")
    if not d:
        return ["📈 EDGE REPORT", "  (no edge_report.json)"]
    return [
        "📈 EDGE REPORT",
        f"  generated={d.get('generated','?')}  window={d.get('window_days','?')}d  "
        f"labelled={d.get('total_labelled','?')}",
        f"  W/L/T = {d.get('wins','?')}/{d.get('losses','?')}/{d.get('timeouts','?')}  "
        f"WR={_pct(d.get('overall_win_rate'))} (Wilson low {_pct(d.get('overall_wr_wilson_low'))})",
    ]


def _capital_section() -> List[str]:
    try:
        import capital_simulation as cs
        rets = cs.load_trade_returns(days=90)
        if not rets:
            return ["💰 CAPITAL SIM", "  (no decided trades yet)"]
        r1 = cs.simulate(rets, 100000)
        r5 = cs.simulate(rets, 500000)
        def line(tag, r):
            return (f"  {tag} → ₹{r['final_equity']:.0f} "
                    f"({r['total_return']:+.1f}%, maxDD {r['max_drawdown']:.0f}%)"
                    + ("  RUINED" if r["ruined"] else ""))
        return [
            f"💰 CAPITAL SIM (real signals, risk_frac=0.2, n={len(rets)})",
            line("₹1L", r1), line("₹5L", r5),
        ]
    except Exception as exc:
        return ["💰 CAPITAL SIM", f"  (unavailable: {exc})"]


def _db_health_section() -> List[str]:
    """SQLite integrity across all project DBs (catches corruption early)."""
    try:
        import db_health
        s = db_health.summary()
        out = ["🗄️ DB HEALTH", f"  {s['healthy']}/{s['n_dbs']} healthy"]
        if s["bad"]:
            out.append(f"  ⚠ CORRUPT/UNREADABLE: {', '.join(s['bad'])}")
        return out
    except Exception:
        return ["🗄️ DB HEALTH", "  (unavailable)"]


def _prune_section() -> List[str]:
    """Prune status + analyzer suggestions (gate-off, reversible)."""
    try:
        import pruning
        st = pruning.status()
        out = ["✂️ PRUNING (gate-off, reversible — reduce > add)",
               f"  disabled: {len(st['disabled_strategies'])} strategies, "
               f"{len(st['disabled_modifiers'])} modifiers"]
        sug = _load_json("prune_suggestions.json")
        sm, ss = sug.get("modifiers", []), sug.get("strategies", [])
        if sm or ss:
            out.append(f"  suggested: {len(sm)} modifiers ({', '.join(sm[:4])})"
                       + (f", {len(ss)} strategies" if ss else ""))
            out.append("  (data-gated — promote to pruned.json at ~20 labelled days)")
        else:
            out.append("  (no suggestions yet — analyzers refresh nightly)")
        return out
    except Exception:
        return ["✂️ PRUNING", "  (unavailable)"]


def _condor_paper_section() -> List[str]:
    """PAPER defined-risk iron-condor forward-test track record (no live orders)."""
    try:
        import condor_forward_test as cft
        st = cft._load()
        stats = cft._stats(st.get("closed", []))
        n = stats.get("trades", 0)
        out = ["🦅 PAPER IRON-CONDOR (defined-risk forward-test, NO live orders)",
               f"  closed={n}  open={'yes' if st.get('open') else 'no'}"]
        if n:
            out.append(f"  total_pnl=₹{stats.get('total_pnl')}  win%={stats.get('win_rate_pct')}  PF={stats.get('profit_factor')}")
            out.append("  (paper only — promote to LIVE solely if proven OOS + account funded)")
        else:
            out.append("  (no closed paper trades yet — accrues ~weekly)")
        return out
    except Exception:
        return ["🦅 PAPER IRON-CONDOR", "  (not run yet)"]


def _oi_accrual_section() -> List[str]:
    """Intraday OI snapshot accrual progress (free OI-flow validation dataset)."""
    try:
        conn = sqlite3.connect(SIGNAL_DB)
        try:
            total, days, strikes, last = conn.execute(
                "SELECT COUNT(*), COUNT(DISTINCT substr(timestamp,1,10)), "
                "COUNT(DISTINCT strike), MAX(timestamp) FROM intraday_oi_snapshots"
            ).fetchone()
        finally:
            conn.close()
        if not total:
            return ["🛢️ INTRADAY OI ACCRUAL", "  (no snapshots yet — starts next market open)"]
        out = ["🛢️ INTRADAY OI ACCRUAL (free OI-flow dataset)",
               f"  snapshots={total}  days={days}  strikes={strikes}  last={last}"]
        out.append("  ✅ enough days to validate" if days >= 20
                   else f"  accruing — need ~20 days to validate (have {days})")
        return out
    except Exception:
        return ["🛢️ INTRADAY OI ACCRUAL", "  (table not created yet)"]


def _pct(x: Any) -> str:
    try:
        x = float(x)
        return f"{x*100:.1f}%" if x <= 1.0 else f"{x:.1f}%"
    except (TypeError, ValueError):
        return "n/a"


def _num(x: Any) -> str:
    try:
        return f"{float(x):.3f}"
    except (TypeError, ValueError):
        return "n/a"


def build_report() -> str:
    from datetime import datetime
    blocks = [
        f"===== TRADING_ROBOT DASHBOARD  {datetime.now():%Y-%m-%d %H:%M} =====",
        *_signals_section(), "",
        *_pipeline_section(), "",
        *_validation_section(), "",
        *_modifier_section(), "",
        *_edge_section(), "",
        *_capital_section(), "",
        *_oi_accrual_section(), "",
        *_condor_paper_section(), "",
        *_prune_section(), "",
        *_db_health_section(),
    ]
    return "\n".join(blocks)


def main() -> int:
    ap = argparse.ArgumentParser(description="One-screen status dashboard")
    ap.add_argument("--telegram", action="store_true", help="Also send via Telegram")
    args = ap.parse_args()
    report = build_report()
    print(report)
    if args.telegram:
        try:
            from alerts import AlertManager
            AlertManager().send("<pre>" + report + "</pre>", parse_mode="HTML")
            print("\n(sent to Telegram)")
        except Exception as exc:
            print(f"\n(telegram send failed: {exc})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
