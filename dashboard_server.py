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
        allocation_html= allocation_html,
        positions_table= positions_table,
        refresh_sec    = refresh_sec,
        refresh_ms     = refresh_sec * 1000,
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
        print("   Press Ctrl+C to stop")
        httpd.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Live trading dashboard")
    parser.add_argument("--port",    type=int, default=8080)
    parser.add_argument("--refresh", type=int, default=5, help="Refresh interval in seconds")
    args = parser.parse_args()
    run_dashboard(args.port, args.refresh)
