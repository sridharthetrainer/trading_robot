"""
remote_dashboard.py

Lightweight HTTP server for remote monitoring from phone/browser.

Serves live trading data at http://your-ip:8765
Accessible from any device on the same WiFi network.

Pages:
  /           → Full dashboard (portfolio, signals, P&L)
  /status     → JSON status for API access
  /positions  → Open positions only
  /pnl        → Today's P&L summary
  /health     → System health check

Usage:
  # Starts automatically with the bot
  # Or run standalone: python remote_dashboard.py
  # Access from phone: http://192.168.1.x:8765

Security:
  - Only accessible on local network (not internet)
  - For internet access: use ngrok (free)
    ngrok http 8765
    → provides https://abc.ngrok.io URL
"""
from __future__ import annotations
import json, logging, os, sqlite3, threading, time
from datetime import datetime, date
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)
PORT = int(os.getenv("DASHBOARD_PORT", "8765"))


def _get_local_ip() -> str:
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _read_live_status() -> dict:
    try:
        p = Path("live_status.json")
        if p.exists():
            return json.loads(p.read_text())
    except Exception:
        pass
    return {}


def _read_trades_today() -> list:
    try:
        conn = sqlite3.connect("trades.db")
        conn.row_factory = sqlite3.Row
        today = date.today().isoformat()
        rows  = conn.execute("""
            SELECT trade_id, symbol, side, qty, strategy, entry_price,
                   exit_price, realized_pnl, status, exit_reason,
                   entry_time, exit_time, stop_loss, target_price,
                   gross_pnl,total_charges,metadata,signal_metadata
            FROM trades ORDER BY entry_time DESC LIMIT 50
        """).fetchall()
        conn.close()
        result = []
        for r in rows:
            d = dict(r)
            # Tag today's trades
            try:
                et = float(d.get("entry_time") or 0)
                d["today"] = datetime.fromtimestamp(et).date().isoformat() == today if et else False
            except Exception:
                d["today"] = False
            result.append(d)
        return result
    except Exception:
        return []


def _build_html(status: dict, trades: list) -> str:
    from pnl_reporting import today_pnl_breakdown
    split_pnl = today_pnl_breakdown()
    td      = status.get("trading", {}) or {}
    pnl     = td.get("daily_realized_pnl", 0)
    mode    = status.get("mode", "PAPER")
    phase   = status.get("market_phase", "UNKNOWN")
    strat   = status.get("current_strategy", "?")
    ts      = status.get("timestamp", "")

    open_pos = [t for t in trades if t.get("status") == "OPEN"]
    closed_today = [t for t in trades if t.get("status") == "CLOSED" and t.get("today")]
    wins    = sum(1 for t in closed_today if (t.get("realized_pnl") or 0) > 0)
    losses  = len(closed_today) - wins

    mode_color = "#e74c3c" if mode == "LIVE" else "#27ae60"
    pnl_color  = "#27ae60" if pnl >= 0 else "#e74c3c"

    rows_html = ""
    for t in (open_pos + closed_today)[:20]:
        pnl_val  = t.get("realized_pnl") or 0
        status_s = t.get("status","?")
        color    = "#27ae60" if pnl_val > 0 else "#e74c3c" if pnl_val < 0 else "#666"
        rows_html += f"""
        <tr>
            <td>{t.get('symbol','?')}</td>
            <td>{t.get('side','?')}</td>
            <td>{t.get('qty','?')}</td>
            <td>₹{t.get('entry_price',0):.2f}</td>
            <td>₹{t.get('stop_loss',0) or 0:.2f}</td>
            <td style="color:{color};font-weight:bold">₹{pnl_val:.2f}</td>
            <td><span class="badge {'open' if status_s=='OPEN' else 'closed'}">{status_s}</span></td>
            <td>{t.get('strategy','?')}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="30">
<title>Trading Bot Monitor</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, sans-serif; background: #0f0f1a; color: #e0e0e0; padding: 16px; }}
  .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }}
  h1 {{ font-size: 20px; color: #fff; }}
  .mode {{ background: {mode_color}; color: white; padding: 4px 12px; border-radius: 20px; font-size: 13px; font-weight: bold; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 20px; }}
  .card {{ background: #1a1a2e; border-radius: 12px; padding: 14px; border: 1px solid #333; }}
  .card-label {{ font-size: 11px; color: #888; text-transform: uppercase; margin-bottom: 6px; }}
  .card-value {{ font-size: 22px; font-weight: bold; color: #fff; }}
  .pnl {{ color: {pnl_color}; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ background: #1a1a2e; padding: 10px 8px; text-align: left; color: #888; font-weight: 500; }}
  td {{ padding: 10px 8px; border-bottom: 1px solid #1a1a2e; }}
  tr:hover {{ background: #1a1a2e; }}
  .badge {{ padding: 3px 8px; border-radius: 10px; font-size: 11px; font-weight: bold; }}
  .badge.open {{ background: #1a4a1a; color: #4caf50; }}
  .badge.closed {{ background: #1a1a3a; color: #64b5f6; }}
  .ts {{ font-size: 11px; color: #555; }}
  .section-title {{ font-size: 14px; color: #888; margin: 16px 0 10px; text-transform: uppercase; letter-spacing: 1px; }}
</style>
</head>
<body>
<div class="header">
  <h1>🤖 Trading Bot</h1>
  <span class="mode">{mode}</span>
</div>

<div class="cards">
  <div class="card"><div class="card-label">Day P&L</div><div class="card-value pnl">₹{pnl:+.0f}</div></div>
  <div class="card"><div class="card-label">Options P&L</div><div class="card-value" style="color:{'#27ae60' if split_pnl['options']['net'] >= 0 else '#e74c3c'}">₹{split_pnl['options']['net']:+.0f}</div></div>
  <div class="card"><div class="card-label">Normal P&L</div><div class="card-value" style="color:{'#27ae60' if split_pnl['normal']['net'] >= 0 else '#e74c3c'}">₹{split_pnl['normal']['net']:+.0f}</div></div>
  <div class="card"><div class="card-label">Open</div><div class="card-value">{len(open_pos)}</div></div>
  <div class="card"><div class="card-label">Trades</div><div class="card-value">{len(closed_today)}</div></div>
  <div class="card"><div class="card-label">W/L</div><div class="card-value">{wins}/{losses}</div></div>
  <div class="card"><div class="card-label">Phase</div><div class="card-value" style="font-size:14px">{phase}</div></div>
  <div class="card"><div class="card-label">Strategy</div><div class="card-value" style="font-size:14px">{strat}</div></div>
</div>

<div class="section-title">Positions &amp; Trades</div>
<table>
  <tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Entry</th><th>SL</th><th>P&amp;L</th><th>Status</th><th>Strategy</th></tr>
  {rows_html or '<tr><td colspan="8" style="text-align:center;color:#555;padding:20px">No trades today</td></tr>'}
</table>

<p class="ts" style="margin-top:16px">Last updated: {ts}  ·  Auto-refreshes every 30s</p>
</body>
</html>"""


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass   # suppress access logs

    def do_GET(self):
        status = _read_live_status()
        trades = _read_trades_today()

        if self.path == "/status":
            body = json.dumps(status, indent=2).encode()
            ct   = "application/json"
        elif self.path == "/positions":
            open_pos = [t for t in trades if t.get("status") == "OPEN"]
            body = json.dumps(open_pos, indent=2).encode()
            ct   = "application/json"
        elif self.path == "/pnl":
            td   = status.get("trading", {}) or {}
            from pnl_reporting import today_pnl_breakdown
            split = today_pnl_breakdown()
            body = json.dumps({
                "daily_pnl": td.get("daily_realized_pnl", 0),
                "options":   split["options"],
                "normal":    split["normal"],
                "total":     split["total"],
                "open":      td.get("open_positions", 0),
                "mode":      status.get("mode","PAPER"),
            }).encode()
            ct   = "application/json"
        elif self.path == "/health":
            age  = 0
            ts   = status.get("timestamp","")
            if ts:
                try:
                    age = int((datetime.now() - datetime.fromisoformat(ts[:19])).total_seconds())
                except Exception:
                    pass
            body = json.dumps({
                "alive":          age < 300,
                "heartbeat_age":  age,
                "phase":          status.get("market_phase","?"),
            }).encode()
            ct   = "application/json"
        else:
            body = _build_html(status, trades).encode()
            ct   = "text/html"

        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


class RemoteDashboard:
    def __init__(self, port: int = PORT) -> None:
        self._port   = port
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> str:
        try:
            self._server = HTTPServer(("0.0.0.0", self._port), _Handler)
            self._thread = threading.Thread(
                target=self._server.serve_forever, daemon=True
            )
            self._thread.start()
            ip  = _get_local_ip()
            url = f"http://{ip}:{self._port}"
            logger.info("Remote dashboard: %s", url)
            return url
        except Exception as e:
            logger.warning("Remote dashboard failed to start: %s", e)
            return ""

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()


_dashboard: Optional[RemoteDashboard] = None
def start_remote_dashboard(port: int = PORT) -> str:
    # DISABLED by default — the LAN web dashboard is redundant now that monitoring
    # is the (rich) Telegram bot + Claude Code/VS Code. Re-enable with
    # ENABLE_REMOTE_DASHBOARD=true.
    if str(os.getenv("ENABLE_REMOTE_DASHBOARD", "false")).lower() not in ("1", "true", "yes", "on"):
        return ""
    global _dashboard
    if _dashboard is None:
        _dashboard = RemoteDashboard(port)
    return _dashboard.start()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    url = start_remote_dashboard()
    print(f"\n✅ Dashboard running at: {url}")
    print(f"   Open on your phone (same WiFi): {url}")
    print(f"   Press Ctrl+C to stop\n")
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopped.")
