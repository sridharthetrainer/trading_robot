"""Compact mobile-first Telegram views backed by durable local evidence."""
from __future__ import annotations
import json, os, sqlite3, time
from html import escape
from pathlib import Path


def dashboard() -> str:
    from runtime_telemetry import snapshot
    snap=snapshot(); scan=snap.get("last_scan") or {}; hearts=snap.get("heartbeats") or []
    stale=[h["component"] for h in hearts if float(h.get("age_sec",999999))>300]
    return ("📊 <b>LIVE DASHBOARD</b>\n"
            f"Uptime: {snap['system_uptime_sec']/3600:.1f}h | Crashes: {snap['crash_count']}\n"
            f"Last scan: {scan.get('scanned',0)} scanned · {scan.get('signals',0)} signals · {scan.get('rejected',0)} rejected\n"
            f"Latency: {float(scan.get('latency_ms',0) or 0)/1000:.1f}s | Last: {scan.get('last_scanned_symbol') or '—'}\n"
            f"Components: {len(hearts)-len(stale)}/{len(hearts)} fresh"
            +(f"\n⚠️ Stale: {', '.join(stale)}" if stale else "\n✅ Runtime components healthy"))


def scanner_diagnostics() -> str:
    try:
        con=sqlite3.connect("runtime_telemetry.db"); con.row_factory=sqlite3.Row
        rows=[dict(r) for r in con.execute("SELECT * FROM scan_cycles ORDER BY id DESC LIMIT 5")]; con.close()
    except Exception: rows=[]
    out=["🔎 <b>SCANNER DIAGNOSTICS</b>"]
    for r in rows:
        out.append(f"#{r['id']} {r['status']} · scan {r['scanned']} · signal {r['signals']} · reject {r['rejected']} · {r['duration_ms']/1000:.1f}s · {r['last_scanned_symbol'] or '—'}")
    return "\n".join(out+["No persisted scan cycles yet."] if not rows else out)


def journal(limit: int=10) -> str:
    out=["📓 <b>SIGNAL JOURNAL</b>"]
    try:
        con=sqlite3.connect("signal_log.db")
        rows=con.execute("SELECT signal_date,signal_time,symbol,side,strategy,score,tb_label,rejection_reason FROM signal_log ORDER BY id DESC LIMIT ?",(limit,)).fetchall(); con.close()
        for d,t,s,side,strat,score,label,reason in rows:
            state={1:"✅",-1:"❌",0:"⏱",-99:"…"}.get(label,"…")
            out.append(f"{state} {str(t)[:5]} {s} {side} · {strat} · {float(score or 0):.1f}"+(f" · {str(reason)[:30]}" if reason else ""))
    except Exception as exc: out.append(f"Unavailable: {str(exc)[:80]}")
    return "\n".join(out)


def history(days: int=5) -> str:
    try:
        con=sqlite3.connect("trades.db")
        rows=con.execute("SELECT date(entry_time,'unixepoch','localtime'),COUNT(*),ROUND(SUM(realized_pnl),2),ROUND(SUM(total_charges),2) FROM trades WHERE status='CLOSED' GROUP BY 1 ORDER BY 1 DESC LIMIT ?",(days,)).fetchall(); con.close()
    except Exception: rows=[]
    out=["🗓 <b>TRADE HISTORY</b>"]+[f"{d}: {n} trades · Net ₹{p:+,.0f} · Charges ₹{c:,.0f}" for d,n,p,c in rows]
    return "\n".join(out+["No closed history yet."] if not rows else out)


def strategies() -> str:
    try:
        d=json.loads(Path("edge_report.json").read_text()); groups=d.get("by_strategy") or {}
        ranked=sorted(groups.items(),key=lambda kv:(kv[1].get("avg_mfe_pct",0)-kv[1].get("avg_mae_pct",0),kv[1].get("n",0)),reverse=True)[:10]
    except Exception: ranked=[]
    out=["🏆 <b>STRATEGY RANKING</b>"]
    for name,x in ranked: out.append(f"{name}: n={x.get('n',0)} WR={float(x.get('win_rate') or 0):.0%} MFE/MAE={x.get('avg_mfe_pct',0):.2f}/{x.get('avg_mae_pct',0):.2f}")
    return "\n".join(out+["No ranked evidence yet."] if not ranked else out)


def option_health(symbol: str="NIFTY") -> str:
    symbol=symbol.upper() if symbol.upper() in {"NIFTY","BANKNIFTY","FINNIFTY"} else "NIFTY"
    try:
        from option_chain_fetcher import diagnose_option_data
        d=diagnose_option_data(symbol); return (f"🩺 <b>OPTION HEALTH — {symbol}</b>\n"
            f"Market open: {d.get('market_open')}\nSelected source: {d.get('selected_source') or d.get('final_fetch')}\n"
            f"Cache: {d.get('cache')}\nAngel: {d.get('angel')}\nNSE: {d.get('nse_live')}" )[:4000]
    except Exception as exc: return f"⚠️ Option health unavailable: {str(exc)[:120]}"


def _load_json(path: str) -> dict:
    try:
        p = Path(path)
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _fmt_num(value, digits: int = 2, fallback: str = "—") -> str:
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return fallback


def _fmt_pct(value, digits: int = 1, fallback: str = "—") -> str:
    try:
        return f"{float(value) * 100:.{digits}f}%"
    except Exception:
        return fallback


def _short(text: object, limit: int = 72) -> str:
    value = " ".join(str(text or "").split())
    if len(value) > limit:
        value = value[: max(0, limit - 1)] + "…"
    return escape(value)


def _file_age(path: str) -> str:
    try:
        p = Path(path)
        if not p.exists():
            return "missing"
        age_min = max(0.0, (time.time() - p.stat().st_mtime) / 60.0)
        if age_min < 90:
            return f"{age_min:.0f}m old"
        return f"{age_min / 60.0:.1f}h old"
    except Exception:
        return "unknown age"


def control_room() -> str:
    """Single Telegram card answering: can the bot trade, and why/why not?

    This intentionally reads only local evidence files and environment flags.
    It does not place orders, refresh Angel credentials, or mutate live state.
    """
    readiness = _load_json("system_readiness_report.json")
    pipeline = _load_json("ml_pipeline_last_run.json")
    training = _load_json("ml_training_last_result.json")
    learned = _load_json("learned_filters.json")
    discipline = _load_json("profit_discipline_report.json")
    option_audit = _load_json("option_bot_audit_report.json")

    paper = _env_bool("PAPER_TRADING", True)
    real = _env_bool("ENABLE_REAL_TRADING", False)
    probation = _env_bool("LIVE_PROBATION_ENABLED", False)
    max_lots = os.getenv("LIVE_PROBATION_MAX_LOTS", "—")
    max_trades = os.getenv("LIVE_PROBATION_MAX_TRADES_PER_DAY", "—")
    allowed_statuses = os.getenv("LIVE_PROBATION_ALLOWED_STATUSES", "—")

    mode = readiness.get("mode") or {}
    scores = readiness.get("scores") or {}
    execution = readiness.get("execution") or {}
    learning = readiness.get("learning") or {}

    cross = training.get("cross_symbol") or {}
    utility = cross.get("profit_utility") or {}
    summary = discipline.get("summary") or {}
    option_score = (option_audit.get("score") or {})
    audit_chain = option_audit.get("option_chain_snapshots") or {}
    audit_journal = option_audit.get("decision_journal") or {}

    live_ready_count = int(mode.get("live_ready_count") or 0)
    model_promoted = bool(cross.get("promoted") or learned.get("model_promoted"))
    active_filters = bool(learned.get("active")) and (
        len(learned.get("filters") or []) + len(learned.get("boosts") or [])
    ) > 0
    edge_text = str(
        pipeline.get("edge_conclusion")
        or learning.get("edge_conclusion")
        or learned.get("activation_reason")
        or "not evaluated"
    )

    if real and live_ready_count > 0 and model_promoted and active_filters:
        verdict_icon = "🟢"
        verdict = "LIVE-CAPABLE"
    elif probation and real:
        verdict_icon = "🟡"
        verdict = "PROBATION ONLY"
    elif probation:
        verdict_icon = "🟡"
        verdict = "PAPER + PROBATION RULES CONFIGURED"
    else:
        verdict_icon = "🔴"
        verdict = "PAPER / VALIDATION ONLY"

    lines = [
        f"{verdict_icon} <b>TRADING CONTROL ROOM</b>",
        f"Verdict: <b>{verdict}</b>",
        f"Mode: {'📄 PAPER' if paper else '💰 LIVE flag'} · real orders {'ON' if real else 'OFF'}",
        f"Probation: {'ON' if probation else 'OFF'} · max {escape(str(max_lots))} lot · {escape(str(max_trades))}/day",
        f"Allowed: <code>{escape(str(allowed_statuses))}</code>",
        "",
        "🧠 <b>ML / edge</b>",
        f"AUC {_fmt_num(cross.get('cv_auc_mean') or pipeline.get('ml_auc'), 4)} · champion {_short(cross.get('champion_algorithm'), 22)}",
        f"Model promoted: {'✅ yes' if model_promoted else '❌ no'} · learned filters: {'✅ active' if active_filters else '❌ none'}",
        f"Best net-R slice: {_fmt_num(utility.get('best_avg_net_r'), 4)} @ p≥{_fmt_num(utility.get('best_threshold'), 2)}"
        f" · selected {int(utility.get('best_selected') or 0)}",
        f"Edge: {_short(edge_text, 92)}",
        "",
        "💰 <b>Profit discipline</b>",
        f"Quarantined {int(summary.get('QUARANTINED') or 0)} · validating {int(summary.get('VALIDATING') or 0)} · promising {int(summary.get('PAPER_PROMISING') or 0)}",
        f"Readiness live strategies: {live_ready_count}/{int(mode.get('total_strategies') or 0)}",
        f"Execution net P&amp;L evidence: ₹{float(execution.get('net_pnl') or 0):+,.0f}",
        "",
        "📈 <b>Option bot evidence</b>",
        f"Audit {option_score.get('total', '?')}/100 {escape(str(option_score.get('grade', '')))} · {escape(str(option_score.get('readiness', '')))}",
        f"Chain rows {int(audit_chain.get('rows') or 0):,} · verified strike outcomes {int(audit_chain.get('verified_strike_outcomes') or 0):,}",
        f"Today selected {int(audit_journal.get('today_selected') or 0)} · blocked {int(audit_journal.get('today_blocked') or 0)}",
        "",
        "🗂 <b>Freshness</b>",
        f"ML {_file_age('ml_training_last_result.json')} · readiness {_file_age('system_readiness_report.json')} · filters {_file_age('learned_filters.json')}",
    ]
    if not real:
        lines.append("\n<i>Real trading is disabled. This command is read-only and did not change Angel/session state.</i>")
    return "\n".join(lines)[:4000]
