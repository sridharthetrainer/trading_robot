"""Compact mobile-first Telegram views backed by durable local evidence."""
from __future__ import annotations
import json, sqlite3, time
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
