"""
dashboard_server.py

Live trading dashboard server.
Reads live_status.json every second and serves a real-time web page.

Usage:
    python dashboard_server.py              # runs on http://localhost:8080
    python dashboard_server.py --port 9090  # custom port

The dashboard auto-refreshes every 3 seconds and shows:
    - Current market phase and strategy
    - Open positions with live unrealised P&L (from WebSocket)
    - Today's P&L vs daily limit (progress bar)
    - Circuit breaker and kill switch status
    - Last 20 trades (wins/losses)
    - System health indicators

No additional dependencies beyond the standard library.
Uses Python's built-in http.server — no FastAPI/uvicorn needed.
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import socketserver
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

LIVE_STATUS_FILE = os.getenv("LIVE_STATUS_FILE", "live_status.json")
TRADES_DB        = os.getenv("TRADES_DB",        "trades.db")

# ── HTML template ─────────────────────────────────────────────────────────────
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>NIFTY Algo Bot — Live Dashboard</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: 'Segoe UI', sans-serif; background: #0f1117; color: #e0e0e0; min-height: 100vh; }}
    .header {{ background: linear-gradient(135deg, #1E3A5F, #2d5a8e); padding: 16px 24px;
               display: flex; justify-content: space-between; align-items: center; }}
    .header h1 {{ font-size: 20px; color: #fff; font-weight: 700; }}
    .header .meta {{ font-size: 12px; color: #adc8e8; }}
    .phase-badge {{ padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: 600;
                    background: {phase_color}; color: #fff; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
             gap: 16px; padding: 16px; }}
    .card {{ background: #1a1d27; border: 1px solid #2a2d3a; border-radius: 12px; padding: 20px; }}
    .card h2 {{ font-size: 13px; color: #7b8cde; text-transform: uppercase;
                letter-spacing: 1px; margin-bottom: 12px; }}
    .big-num {{ font-size: 32px; font-weight: 700; }}
    .green {{ color: #4caf82; }}
    .red   {{ color: #e05252; }}
    .amber {{ color: #f4a734; }}
    .gray  {{ color: #888; }}
    .pbar-bg {{ background: #2a2d3a; border-radius: 6px; height: 10px; margin: 8px 0; overflow: hidden; }}
    .pbar {{ height: 10px; border-radius: 6px; transition: width 0.4s; }}
    .pos-row {{ display: flex; justify-content: space-between; align-items: center;
                padding: 8px 0; border-bottom: 1px solid #2a2d3a; font-size: 13px; }}
    .pos-row:last-child {{ border-bottom: none; }}
    .badge {{ padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }}
    .badge-buy  {{ background: #1a3d28; color: #4caf82; }}
    .badge-sell {{ background: #3d1a1a; color: #e05252; }}
    .stat {{ display: flex; justify-content: space-between; padding: 6px 0;
             border-bottom: 1px solid #2a2d3a; font-size: 13px; }}
    .stat:last-child {{ border-bottom: none; }}
    .alert-red  {{ background: #3d1a1a; border: 1px solid #e05252; border-radius: 8px; padding: 10px 14px; }}
    .alert-grn  {{ background: #1a3d28; border: 1px solid #4caf82; border-radius: 8px; padding: 10px 14px; }}
    .ws-dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%;
               background: {ws_color}; margin-right: 6px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
    th {{ color: #7b8cde; text-align: left; padding: 6px 8px; font-weight: 600;
          border-bottom: 1px solid #2a2d3a; }}
    td {{ padding: 6px 8px; border-bottom: 1px solid #1e212d; }}
    tr:hover td {{ background: #1e212d; }}
    .refresh-bar {{ height: 3px; background: #7b8cde; animation: shrink {refresh_sec}s linear infinite; }}
    @keyframes shrink {{ from {{ width: 100%; }} to {{ width: 0; }} }}
    .sparkline {{ width: 100%; height: 80px; overflow: visible; }}
    .spark-area {{ fill: rgba(75,175,130,0.15); }}
    .spark-line {{ fill: none; stroke: #4caf82; stroke-width: 1.5; }}
    .spark-line-neg {{ fill: none; stroke: #e05252; stroke-width: 1.5; }}
    .dd-line {{ fill: none; stroke: #f4a734; stroke-width: 1; stroke-dasharray: 3,2; opacity: 0.7; }}
  </style>
</head>
<body>
  <div class="refresh-bar"></div>
  <div class="header">
    <div>
      <h1>🤖 NIFTY Algo Bot — Live Dashboard</h1>
      <div class="meta">Updated: {timestamp} &nbsp;|&nbsp; <span class="ws-dot"></span>{ws_status}</div>
    </div>
    <div>
      <span class="phase-badge">{market_phase}</span>
      &nbsp;&nbsp;
      <span style="font-size:13px;color:#adc8e8">Strategy: <b style="color:#fff">{strategy}</b></span>
    </div>
  </div>

  <div class="grid">

    <!-- P&L Card -->
    <div class="card">
      <h2>Today's P&L</h2>
      <div class="big-num {pnl_class}">₹{daily_pnl:,.0f}</div>
      <div style="font-size:12px;color:#888;margin-top:4px">Limit: ₹{daily_limit:,.0f}</div>
      <div class="pbar-bg"><div class="pbar" style="width:{pnl_pct:.0f}%;background:{pbar_color}"></div></div>
      <div style="font-size:12px;color:#888">{pnl_pct:.1f}% of daily limit used</div>
    </div>

    <!-- Positions Card -->
    <div class="card">
      <h2>Open Positions ({open_count})</h2>
      {positions_html}
    </div>

    <!-- Trade Stats -->
    <div class="card">
      <h2>Today's Stats</h2>
      <div class="stat"><span>Closed Trades</span><span><b>{closed_count}</b></span></div>
      <div class="stat"><span>Unrealised P&L</span>
        <span class="{unr_class}"><b>₹{unrealized:,.0f}</b></span></div>
      <div class="stat"><span>Market Phase</span><span>{market_phase}</span></div>
      <div class="stat"><span>Current Strategy</span><span>{strategy}</span></div>
      <div class="stat"><span>Heartbeat #</span><span>{heartbeat}</span></div>
    </div>

    <!-- System Health -->
    <div class="card">
      <h2>System Health</h2>
      {cb_html}
      {ks_html}
      {high_impact_html}
      <div class="stat"><span>Mode</span><span>{mode}</span></div>
      <div class="stat"><span>Consec Failures</span>
        <span class="{fail_class}">{consec_failures}</span></div>
    </div>

    <!-- Allocation Card -->
    <div class="card" style="grid-column: span 2;">
      <h2>Capital Allocation</h2>
      {allocation_html}
    </div>

  </div>

  <!-- Advanced Metrics Bar -->
  <div style="padding:0 16px 8px">
    <div class="card">
      <h2>Performance Metrics (30 days)</h2>
      {advanced_metrics_html}
    </div>
  </div>

  <!-- Data Feed Health Card -->
  <div style="padding:0 16px 8px">
    <div class="card">
      <h2>Data Feed Health</h2>
      <div style="display:flex;flex-wrap:wrap;align-items:center;gap:4px;padding:4px 0">
        {feed_health_html}
      </div>
    </div>
  </div>

  <!-- Equity Curve Card -->
  <div style="padding:0 16px 8px">
    <div class="card">
      <h2>Equity Curve (30 days) &nbsp;<span style="font-size:11px;color:#888">{eq_final_pnl} &nbsp;|&nbsp; Max DD: {eq_max_dd}</span></h2>
      {equity_curve_svg}
    </div>
  </div>

  <div style="padding:0 16px 16px">
    <div class="card">
      <h2>Recent Positions</h2>
      {positions_table}
    </div>
  </div>

  <script>
    setTimeout(() => location.reload(), {refresh_ms});
  </script>
</body>
</html>"""


def _load_status() -> Dict[str, Any]:
    """Load live_status.json. Returns empty dict on failure."""
    try:
        p = Path(LIVE_STATUS_FILE)
        if p.exists():
            return json.loads(p.read_text())
    except Exception:
        pass
    return {}


def _load_equity_curve(days: int = 30) -> list:
    """
    Return daily cumulative P&L and drawdown from trades.db.
    Each entry: {"date": "YYYY-MM-DD", "cum_pnl": float, "drawdown_pct": float}
    """
    try:
        import sqlite3
        conn = sqlite3.connect(TRADES_DB)
        rows = conn.execute(
            """
            SELECT date(exit_time, 'unixepoch') AS d,
                   SUM(realized_pnl)            AS day_pnl
            FROM   trades
            WHERE  status = 'CLOSED'
              AND  exit_time >= strftime('%s', 'now', ?)
            GROUP  BY d
            ORDER  BY d ASC
            """,
            (f"-{days} days",),
        ).fetchall()
        conn.close()

        curve, peak, cum = [], 0.0, 0.0
        for d, pnl in rows:
            pnl = float(pnl or 0)
            cum += pnl
            peak = max(peak, cum)
            dd = (cum - peak) / peak * 100 if peak > 0 else 0.0
            curve.append({"date": d, "cum_pnl": round(cum, 2), "drawdown_pct": round(dd, 2)})
        return curve
    except Exception:
        return []


def _load_advanced_metrics(days: int = 30) -> Dict[str, Any]:
    """
    Compute Profit Factor, Sortino, Calmar, Win Rate and Avg R from trades.db.
    All derived from closed trades in the last `days` days.
    Returns a dict with metric name → (value, label, css_class).
    """
    try:
        import sqlite3, math
        conn = sqlite3.connect(TRADES_DB)
        rows = conn.execute(
            """SELECT date(exit_time,'unixepoch') AS d,
                      realized_pnl, r_multiple
               FROM   trades
               WHERE  status='CLOSED'
                 AND  exit_time >= strftime('%s','now',?)""",
            (f"-{days} days",),
        ).fetchall()
        conn.close()
    except Exception:
        return {}

    if not rows:
        return {}

    # Aggregates
    gross_win = gross_loss = 0.0
    wins = n = 0
    r_vals = []
    daily: Dict[str, float] = {}
    for d, pnl, r in rows:
        pnl = float(pnl or 0)
        n += 1
        if pnl > 0:
            wins += 1
            gross_win += pnl
        else:
            gross_loss += abs(pnl)
        daily[d] = daily.get(d, 0.0) + pnl
        try:
            if r is not None:
                r_vals.append(float(r))
        except (TypeError, ValueError):
            pass

    # Profit Factor
    pf = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)

    # Win rate
    wr = wins / n if n else 0.0

    # Avg R
    avg_r = (sum(r_vals) / len(r_vals)) if r_vals else None

    # Sortino: mean daily return / downside std
    daily_vals = list(daily.values())
    mean_day = sum(daily_vals) / len(daily_vals) if daily_vals else 0.0
    neg_sq = [v * v for v in daily_vals if v < 0]
    down_std = math.sqrt(sum(neg_sq) / len(neg_sq)) if neg_sq else None
    sortino = (mean_day / down_std) if down_std else None

    # Calmar: annualised return / abs(max drawdown)
    cum, peak, max_dd = 0.0, 0.0, 0.0
    for d in sorted(daily):
        cum += daily[d]
        peak = max(peak, cum)
        dd = peak - cum
        max_dd = max(max_dd, dd)
    ann_ret = (cum / days * 252) if days > 0 else 0.0  # 252 trading days
    calmar = (ann_ret / max_dd) if max_dd > 0 else None

    def _cls(v, good, warn):
        if v is None: return "gray"
        return "green" if v >= good else ("amber" if v >= warn else "red")

    out = {
        "profit_factor": {
            "val": f"{pf:.2f}" if pf != float("inf") else "∞",
            "label": "Profit Factor",
            "cls": _cls(pf if pf != float("inf") else 999, 1.5, 1.0),
            "hint": "≥1.5 tradeable",
        },
        "win_rate": {
            "val": f"{wr:.0%}",
            "label": "Win Rate",
            "cls": _cls(wr, 0.55, 0.45),
            "hint": f"{wins}/{n} trades",
        },
        "sortino": {
            "val": f"{sortino:.2f}" if sortino is not None else "—",
            "label": "Sortino",
            "cls": _cls(sortino, 1.0, 0.5),
            "hint": "≥1.0 good",
        },
        "calmar": {
            "val": f"{calmar:.2f}" if calmar is not None else "—",
            "label": "Calmar",
            "cls": _cls(calmar, 0.5, 0.2),
            "hint": "ann.ret/max DD",
        },
        "avg_r": {
            "val": f"{avg_r:+.2f}R" if avg_r is not None else "—",
            "label": "Avg R",
            "cls": _cls(avg_r, 0.3, 0.0),
            "hint": f"n={len(r_vals)}",
        },
    }
    return out


def _build_metrics_bar(metrics: Dict[str, Any]) -> str:
    """Render advanced metrics as a horizontal row of stat chips."""
    if not metrics:
        return "<div style='color:#888;font-size:13px'>No closed trades yet</div>"
    parts = []
    for m in metrics.values():
        val   = m["val"]
        label = m["label"]
        hint  = m["hint"]
        cls   = m["cls"]
        parts.append(
            f"<div style='flex:1;min-width:110px;text-align:center;padding:12px 8px;"
            f"background:#0f1117;border-radius:8px;margin:4px'>"
            f"<div style='font-size:22px;font-weight:700' class='{cls}'>{val}</div>"
            f"<div style='font-size:11px;color:#7b8cde;margin-top:4px'>{label}</div>"
            f"<div style='font-size:10px;color:#555;margin-top:2px'>{hint}</div>"
            f"</div>"
        )
    return f"<div style='display:flex;flex-wrap:wrap;gap:4px'>{''.join(parts)}</div>"


def _load_feed_health() -> Dict[str, Any]:
    """
    Aggregate feed health from live_status.json + rejection_stats.json.
    Returns a dict with connection status, failover state, and pass-rate stats.
    """
    health: Dict[str, Any] = {
        "angel_connected":   False,
        "failover_active":   False,
        "ws_connected":      False,
        "pass_rate":         None,
        "total_scanned":     0,
        "passed":            0,
        "top_rejection":     None,
        "feed_degraded":     False,
        "feed_degraded_reasons": [],
        "last_heartbeat_age": None,
    }
    try:
        status = _load_status()
        health["angel_connected"]  = bool(status.get("angel_connected", False))
        health["ws_connected"]     = str(status.get("ws_status", "")).upper() in ("CONNECTED", "LIVE")
        health["feed_degraded"]    = bool(status.get("feed_degraded", False))
        health["feed_degraded_reasons"] = list(status.get("feed_degraded_reasons") or [])
        if status.get("heartbeat"):
            age = time.time() - float(status["heartbeat"])
            health["last_heartbeat_age"] = round(age)
    except Exception:
        pass

    try:
        rej_path = Path("rejection_stats.json")
        if rej_path.exists():
            rej = json.loads(rej_path.read_text())
            total  = int(rej.get("total_scanned", 0) or 0)
            passed = int(rej.get("total_passed", 0) or 0)
            health["total_scanned"] = total
            health["passed"]        = passed
            health["pass_rate"]     = round(passed / total * 100, 1) if total > 0 else None
            top10 = rej.get("top_rejection_reasons") or {}
            if top10:
                # top_rejection_reasons is {reason: count}
                top_r = sorted(top10.items(), key=lambda x: x[1], reverse=True)
                health["top_rejection"] = f"{top_r[0][0]} ({top_r[0][1]}×)" if top_r else None
                health["failover_active"] = any("fallback" in r[0].lower() or "failover" in r[0].lower() for r in top_r)
    except Exception:
        pass

    return health


def _build_feed_health_card(h: Dict[str, Any]) -> str:
    """Render the data-feed health bar as an inline HTML card body."""
    def dot(ok: bool, label: str) -> str:
        c = "#22c55e" if ok else "#ef4444"
        return (
            f"<span style='display:inline-flex;align-items:center;gap:5px;margin-right:16px'>"
            f"<span style='width:10px;height:10px;border-radius:50%;background:{c};display:inline-block'></span>"
            f"<span style='font-size:13px;color:#ccc'>{label}</span>"
            f"</span>"
        )

    angel_ok = h["angel_connected"]
    ws_ok    = h["ws_connected"]
    deg      = h["feed_degraded"]
    hb_age   = h["last_heartbeat_age"]

    hb_str = f"Heartbeat {hb_age}s ago" if hb_age is not None else "Heartbeat unknown"
    hb_ok  = (hb_age is not None and hb_age < 90)

    pass_rate = h["pass_rate"]
    pr_str = f"{pass_rate:.1f}% pass rate ({h['passed']}/{h['total_scanned']} scanned)" if pass_rate is not None else "No scan data"
    pr_ok  = (pass_rate is not None and pass_rate >= 1.0)

    top_r  = h.get("top_rejection") or "—"
    deg_reasons = ", ".join(h["feed_degraded_reasons"][:3]) if h["feed_degraded_reasons"] else ""
    deg_str = f"⚠ Feed degraded: {deg_reasons}" if deg and deg_reasons else ("⚠ Feed degraded" if deg else "")

    failover = h["failover_active"]

    parts = [
        dot(angel_ok, "Angel API"),
        dot(ws_ok, "WebSocket"),
        dot(hb_ok, hb_str),
        dot(not deg, "Feed OK" if not deg else deg_str),
        dot(pr_ok, pr_str),
    ]
    if failover:
        parts.append(
            "<span style='font-size:12px;color:#f59e0b;margin-left:8px'>⚡ Failover active</span>"
        )
    if top_r and top_r != "—":
        parts.append(
            f"<div style='font-size:11px;color:#666;margin-top:6px'>Top rejection: {top_r}</div>"
        )

    return "".join(parts)


def _load_recent_trades(limit: int = 15) -> list:
    """Load recent closed trades from SQLite."""
    try:
        import sqlite3
        conn = sqlite3.connect(TRADES_DB)
        rows = conn.execute(
            "SELECT symbol, side, strategy, entry_price, exit_price, realized_pnl, exit_time "
            "FROM trades WHERE status='CLOSED' ORDER BY exit_time DESC LIMIT ?",
            (limit,)
        ).fetchall()
        conn.close()
        return rows
    except Exception:
        return []


def _build_equity_svg(curve: list) -> tuple:
    """
    Build an inline SVG sparkline from the equity curve data.
    Returns (svg_html, final_pnl_str, max_dd_str).
    """
    if not curve or len(curve) < 2:
        empty = "<div style='color:#888;font-size:13px;padding:20px;text-align:center'>No trade history yet</div>"
        return empty, "₹0", "0%"

    pnls = [p["cum_pnl"] for p in curve]
    dds  = [p["drawdown_pct"] for p in curve]
    n    = len(pnls)
    w, h = 600, 80

    mn, mx = min(pnls), max(pnls)
    span   = (mx - mn) or 1.0

    def px(i):  return round(i / (n - 1) * w, 1)
    def py(v):  return round(h - (v - mn) / span * (h - 4) - 2, 1)

    pts = " ".join(f"{px(i)},{py(v)}" for i, v in enumerate(pnls))
    # Closed path for area fill: go to baseline (zero or bottom)
    zero_y = py(max(0.0, mn))
    area_pts = f"0,{zero_y} " + pts + f" {w},{zero_y}"

    final = pnls[-1]
    max_dd = min(dds)
    line_cls = "spark-line" if final >= 0 else "spark-line-neg"

    svg = (
        f'<svg class="sparkline" viewBox="0 0 {w} {h}" preserveAspectRatio="none">'
        f'<polygon class="spark-area" points="{area_pts}"/>'
        f'<polyline class="{line_cls}" points="{pts}"/>'
        f'</svg>'
    )
    return svg, f"₹{final:+,.0f}", f"{max_dd:.1f}%"


def build_html(refresh_sec: int = 5) -> str:
    """Build the dashboard HTML from live_status.json."""
    s         = _load_status()
    trading   = s.get("trading", {})
    cb        = s.get("circuit_breaker", {})
    ks        = s.get("kill_switch", {})
    positions = s.get("open_positions", [])
    alloc     = s.get("allocation", {})

    ts         = s.get("timestamp", "—")[:19].replace("T", " ")
    phase      = s.get("market_phase", "UNKNOWN")
    strategy   = s.get("current_strategy", "—")
    mode       = s.get("mode", "—")
    heartbeat  = s.get("heartbeat", {}).get("count", 0)
    ws_conn    = s.get("ws_connected", False)
    unrealized = float(s.get("unrealized_pnl", 0.0))

    daily_pnl  = float(trading.get("daily_realized_pnl", 0.0))
    daily_limit= float(trading.get("daily_loss_limit", 3000.0))
    closed_cnt = int(trading.get("closed_positions", 0))
    open_cnt   = int(trading.get("open_positions", 0))

    # Phase colour
    phase_colors = {
        "LIVE": "#2d7a4f", "LEARNING": "#5a4a2d", "OPENING_WAIT": "#2d4a7a",
        "HOLIDAY": "#4a2d5a", "BOOT": "#2a2d3a", "EOD": "#7a2d2d",
    }
    phase_color = phase_colors.get(phase, "#2a2d3a")

    # P&L
    pnl_class = "green" if daily_pnl >= 0 else "red"
    if daily_limit > 0 and daily_pnl < 0:
        pnl_pct   = abs(daily_pnl) / daily_limit * 100
        pbar_color= "#e05252" if pnl_pct > 80 else "#f4a734" if pnl_pct > 50 else "#4caf82"
    else:
        pnl_pct   = 0.0
        pbar_color= "#4caf82"

    # Positions HTML
    pos_rows = ""
    if positions:
        for p in positions:
            sym  = p.get("symbol", "?")
            side = p.get("side", "?")
            ep   = float(p.get("entry_price", 0) or 0)
            sl   = float(p.get("stop_loss",   0) or 0)
            tgt  = float(p.get("target_price",0) or 0)
            unr  = float(p.get("unrealized", p.get("unrealized_pnl", 0) or 0))
            unr_s= f"<span class='{'green' if unr>=0 else 'red'}'>₹{unr:+.0f}</span>"
            badge= f"<span class='badge badge-{side.lower()}'>{side}</span>"
            pos_rows += (
                f"<div class='pos-row'>"
                f"<span>{badge} {sym}</span>"
                f"<span style='font-size:11px;color:#888'>@{ep:.0f} SL:{sl:.0f}</span>"
                f"<span>{unr_s}</span></div>"
            )
    else:
        pos_rows = "<div style='color:#888;font-size:13px;text-align:center;padding:16px'>No open positions</div>"

    # Circuit breaker
    cb_html = ""
    if cb.get("active"):
        resume = int(cb.get("resume_in_sec", 0))
        cb_html = f"<div class='alert-red' style='margin-bottom:8px'>⚡ Circuit breaker active — resumes in {resume}s</div>"

    # Kill switch
    ks_html = ""
    if ks.get("active"):
        ks_html = "<div class='alert-red' style='margin-bottom:8px'>🛑 KILL SWITCH ACTIVE</div>"

    # High impact day
    hi_html = ""
    if s.get("is_high_impact_day"):
        hi_html = "<div class='alert-red' style='margin-bottom:8px'>📅 High-impact day — reduced sizes</div>"

    # WebSocket
    ws_color  = "#4caf82" if ws_conn else "#f4a734"
    ws_status = "WebSocket live" if ws_conn else "REST polling"

    # Unrealized class
    unr_class = "green" if unrealized >= 0 else "red"

    # Fail class
    fails      = int(cb.get("consecutive_failures", 0))
    fail_class = "red" if fails >= 3 else "amber" if fails >= 1 else "green"

    # Allocation
    if alloc and alloc.get("buckets"):
        rows = ""
        for bname, bdata in alloc["buckets"].items():
            if bname == "reserve": continue
            dep  = float(bdata.get("deployed", 0))
            avl  = float(bdata.get("available", 0))
            tot  = float(bdata.get("allocated", 1))
            pct  = dep / tot * 100 if tot > 0 else 0
            pnl_b= float(bdata.get("pnl_today", 0))
            cls  = "green" if pnl_b >= 0 else "red"
            rows += (
                f"<tr><td><b>{bname.upper()}</b></td>"
                f"<td>₹{tot:,.0f}</td>"
                f"<td>₹{dep:,.0f} ({pct:.0f}%)</td>"
                f"<td>₹{avl:,.0f}</td>"
                f"<td class='{cls}'>₹{pnl_b:+.0f}</td>"
                f"<td>{bdata.get('trades_today',0)} trades</td></tr>"
            )
        allocation_html = (
            "<table><tr>"
            "<th>Bucket</th><th>Allocated</th><th>Deployed</th>"
            "<th>Available</th><th>P&L Today</th><th>Activity</th></tr>"
            + rows + "</table>"
        )
    else:
        allocation_html = "<div style='color:#888;font-size:13px'>Allocation data loading...</div>"

    # Advanced metrics bar (Profit Factor, Win Rate, Sortino, Calmar, Avg R)
    adv_metrics = _load_advanced_metrics(days=30)
    advanced_metrics_html = _build_metrics_bar(adv_metrics)

    # Data feed health card
    feed_health = _load_feed_health()
    feed_health_html = _build_feed_health_card(feed_health)

    # Equity curve
    equity_curve = _load_equity_curve(days=30)
    equity_curve_svg, eq_final_pnl, eq_max_dd = _build_equity_svg(equity_curve)

    # Recent trades table
    recent = _load_recent_trades()
    if recent:
        trows = ""
        for sym, side, strat, ep, xp, pnl, xt in recent:
            pnl   = float(pnl or 0)
            cls   = "green" if pnl > 0 else "red"
            side_b= f"<span class='badge badge-{str(side).lower()}'>{side}</span>"
            exit_t= datetime.fromtimestamp(float(xt)).strftime("%H:%M") if xt else "—"
            trows += (
                f"<tr><td>{sym}</td><td>{side_b}</td><td>{strat}</td>"
                f"<td>₹{float(ep or 0):.0f}</td><td>₹{float(xp or 0):.0f}</td>"
                f"<td class='{cls}'><b>₹{pnl:+.0f}</b></td><td>{exit_t}</td></tr>"
            )
        positions_table = (
            "<table><tr><th>Symbol</th><th>Side</th><th>Strategy</th>"
            "<th>Entry</th><th>Exit</th><th>P&L</th><th>Time</th></tr>"
            + trows + "</table>"
        )
    else:
        positions_table = "<div style='color:#888;font-size:13px;padding:12px'>No closed trades today</div>"

    return HTML_TEMPLATE.format(
        timestamp      = ts,
        market_phase   = phase,
        strategy       = strategy or "—",
        phase_color    = phase_color,
        ws_color       = ws_color,
        ws_status      = ws_status,
        daily_pnl      = daily_pnl,
        daily_limit    = daily_limit,
        pnl_class      = pnl_class,
        pnl_pct        = pnl_pct,
        pbar_color     = pbar_color,
        positions_html = pos_rows,
        closed_count   = closed_cnt,
        unrealized     = unrealized,
        unr_class      = unr_class,
        open_count     = open_cnt,
        market_phase2  = phase,
        cb_html        = cb_html,
        ks_html        = ks_html,
        high_impact_html = hi_html,
        mode           = mode,
        fail_class     = fail_class,
        consec_failures= fails,
        heartbeat      = heartbeat,
        allocation_html    = allocation_html,
        positions_table    = positions_table,
        advanced_metrics_html = advanced_metrics_html,
        feed_health_html   = feed_health_html,
        equity_curve_svg   = equity_curve_svg,
        eq_final_pnl       = eq_final_pnl,
        eq_max_dd          = eq_max_dd,
        refresh_sec        = refresh_sec,
        refresh_ms         = refresh_sec * 1000,
    )


# ── HTTP handler ──────────────────────────────────────────────────────────────
class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    refresh_sec = 5

    def do_GET(self):
        if self.path == "/" or self.path == "/dashboard":
            html = build_html(self.refresh_sec).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
        elif self.path == "/api/status":
            data = json.dumps(_load_status(), default=str).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif self.path.startswith("/api/equity_curve"):
            days = 30
            try:
                from urllib.parse import urlparse, parse_qs
                qs = parse_qs(urlparse(self.path).query)
                days = int(qs.get("days", ["30"])[0])
            except Exception:
                pass
            data = json.dumps(_load_equity_curve(days=days), default=str).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_error(404)

    def log_message(self, fmt, *args):
        pass  # suppress request logs


def run_dashboard(port: int = 8080, refresh_sec: int = 5):
    DashboardHandler.refresh_sec = refresh_sec
    with socketserver.TCPServer(("", port), DashboardHandler) as httpd:
        httpd.allow_reuse_address = True
        print(f"✅ Dashboard running at http://localhost:{port}")
        print(f"   Auto-refreshes every {refresh_sec} seconds")
        print(f"   API: http://localhost:{port}/api/status")
        print(f"   Equity curve: http://localhost:{port}/api/equity_curve?days=30")
        print("   Press Ctrl+C to stop")
        httpd.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Live trading dashboard")
    parser.add_argument("--port",    type=int, default=8080)
    parser.add_argument("--refresh", type=int, default=5, help="Refresh interval in seconds")
    args = parser.parse_args()
    run_dashboard(args.port, args.refresh)
