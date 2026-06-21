"""
manual_book_risk.py — read-only portfolio risk snapshot for your MANUAL trades.

WHY THIS EXISTS
  manual_trade_tracker.py protects each manual trade individually (stop/target/
  trailing GTT) but imports NONE of the platform's portfolio-level risk modules.
  So the manual book has no aggregate view: no VaR, no daily-loss-limit status,
  no correlated-cluster check, no "this position has no stop" flag. The auto
  engine has all of that (portfolio_risk / value_at_risk / daily_loss_limit);
  this reporter reuses those same modules over your manual positions.

  Strictly READ-ONLY and additive: reads manual_trades.db, computes a snapshot,
  prints it. It places no orders, modifies no live code, writes nothing.

WHAT IT REPORTS
  - open positions, per-position risk (entry→stop), and any WITHOUT a stop
  - total exposure and portfolio risk % vs the platform's configured limits
  - correlated cluster (positions on the same underlying)
  - VaR / CVaR (reuses value_at_risk.ValueAtRisk)
  - today's realized P&L vs the daily-loss limit (reuses daily_loss_limit limits)

USAGE
  python manual_book_risk.py                 # capital from REAL_CAPITAL env
  python manual_book_risk.py --capital 500000
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

_DB = Path("manual_trades.db")
_CLOSED = ("CLOSED", "EXITED", "SQUARED_OFF", "CANCELLED")


# ── position loading ────────────────────────────────────────────────────────--

def _is_option(symbol: str) -> bool:
    # CE/PE must follow a digit (the strike), else 'RELIANCE' would look like an option
    return bool(re.search(r"\d(CE|PE)$", str(symbol).strip().upper()))


def _underlying(symbol: str) -> str:
    """Group key: NIFTY / BANKNIFTY / FINNIFTY / <stock> (strip expiry+strike+CE/PE)."""
    s = str(symbol).strip().upper()
    for idx in ("BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNEXT50", "NIFTY", "SENSEX"):
        if s.startswith(idx):
            return idx
    m = re.match(r"^([A-Z&-]+?)(\d|$)", s)        # stock symbol up to first digit
    return m.group(1) if m else s


def load_open_positions(db: Path = _DB) -> List[Dict]:
    if not db.exists():
        return []
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    cols = {r[1] for r in conn.execute("PRAGMA table_info(manual_trades)")}
    q = ("SELECT * FROM manual_trades WHERE UPPER(COALESCE(status,'')) NOT IN "
         f"({','.join('?' for _ in _CLOSED)})")
    rows = conn.execute(q, _CLOSED).fetchall()
    conn.close()
    out: List[Dict] = []
    for r in rows:
        d = dict(r)
        sym = d.get("symbol", "")
        entry = float(d.get("entry_price") or 0)
        stop = d.get("stop_loss")
        stop = float(stop) if stop not in (None, "", 0, 0.0) else None
        qty = int(d.get("qty") or 0)
        cur = float(d.get("current_price") or entry)
        out.append({
            "symbol": sym, "side": (d.get("side") or "").upper(), "qty": qty,
            "entry_price": entry, "stop_loss": stop, "current_price": cur,
            "is_options": _is_option(sym), "underlying": _underlying(sym),
            "pnl": float(d.get("pnl") or 0),
            "exposure": entry * qty,
            "risk": (abs(entry - stop) * qty) if stop else (entry * qty if _is_option(sym) else entry * 0.05 * qty),
        })
    return out


def today_realized_pnl(db: Path = _DB) -> float:
    if not db.exists():
        return 0.0
    conn = sqlite3.connect(str(db))
    today = date.today().isoformat()
    try:
        rows = conn.execute(
            "SELECT pnl, exit_time, created_at FROM manual_trades "
            "WHERE UPPER(COALESCE(status,''))='CLOSED'").fetchall()
    except Exception:
        conn.close(); return 0.0
    conn.close()
    tot = 0.0
    for pnl, exit_time, created in rows:
        stamp = str(exit_time or created or "")
        if stamp.startswith(today):
            tot += float(pnl or 0)
    return tot


# ── risk views (reusing platform modules where available) ─────────────────────

def _capital(cli: Optional[float]) -> float:
    if cli and cli > 0:
        return cli
    return float(os.getenv("REAL_CAPITAL", "0") or 0)


def build_report(capital: float, positions: List[Dict],
                 realized_today: float) -> dict:
    total_exposure = sum(p["exposure"] for p in positions)
    total_risk = sum(p["risk"] for p in positions)
    unstopped = [p for p in positions if p["stop_loss"] is None]
    # correlated clusters
    clusters: Dict[str, int] = {}
    for p in positions:
        clusters[p["underlying"]] = clusters.get(p["underlying"], 0) + 1
    correlated = {k: v for k, v in clusters.items() if v > 1}

    rep = {
        "capital": capital, "n_positions": len(positions),
        "total_exposure": round(total_exposure, 2),
        "exposure_pct": round(total_exposure / capital, 4) if capital else None,
        "portfolio_risk": round(total_risk, 2),
        "portfolio_risk_pct": round(total_risk / capital, 4) if capital else None,
        "unstopped": [p["symbol"] for p in unstopped],
        "correlated_clusters": correlated,
        "realized_today": round(realized_today, 2),
        "warnings": [], "limits": {}, "var": None, "daily_loss": None,
    }

    # reuse PortfolioRiskManager limits
    try:
        from portfolio_risk import PortfolioRiskManager
        prm = PortfolioRiskManager(capital=capital or 1.0)
        rep["limits"] = {
            "max_open_positions": prm.max_open_positions,
            "max_portfolio_risk_pct": prm.max_portfolio_risk_pct,
            "max_total_exposure_pct": prm.max_total_exposure_pct,
            "max_correlated_positions": prm.max_correlated_positions,
        }
        if rep["exposure_pct"] and rep["exposure_pct"] > prm.max_total_exposure_pct:
            rep["warnings"].append(
                f"total exposure {rep['exposure_pct']:.0%} > limit {prm.max_total_exposure_pct:.0%}")
        if rep["portfolio_risk_pct"] and rep["portfolio_risk_pct"] > prm.max_portfolio_risk_pct:
            rep["warnings"].append(
                f"portfolio risk {rep['portfolio_risk_pct']:.1%} > limit {prm.max_portfolio_risk_pct:.1%}")
        if len(positions) > prm.max_open_positions:
            rep["warnings"].append(
                f"{len(positions)} open > max_open_positions {prm.max_open_positions}")
        for u, c in correlated.items():
            if c > prm.max_correlated_positions:
                rep["warnings"].append(
                    f"{c} correlated positions on {u} > limit {prm.max_correlated_positions}")
    except Exception as e:
        rep["warnings"].append(f"portfolio_risk unavailable: {e}")

    if unstopped:
        rep["warnings"].append(
            f"{len(unstopped)} position(s) WITHOUT a stop: {', '.join(rep['unstopped'])}")

    # reuse ValueAtRisk
    try:
        from value_at_risk import ValueAtRisk
        var_rpt = ValueAtRisk(capital=capital or 100_000).compute(open_trades=positions)
        rep["var"] = {
            "var_95": round(getattr(var_rpt, "var_95", 0), 2),
            "cvar_95": round(getattr(var_rpt, "cvar_95", 0), 2),
            "var_pct": getattr(var_rpt, "var_pct", None),
            "safe_to_trade": getattr(var_rpt, "safe_to_trade", None),
            "note": "VaR uses trades.db daily-P&L history (currently sparse) — treat as indicative",
        }
    except Exception as e:
        rep["warnings"].append(f"value_at_risk unavailable: {e}")

    # reuse DailyLossLimitManager limits
    try:
        from daily_loss_limit import DailyLossLimitManager
        dll = DailyLossLimitManager()
        rep["daily_loss"] = {
            "realized_today": round(realized_today, 2),
            "soft_limit": -dll.soft_limit if dll.soft_limit else None,
            "hard_limit": -dll.hard_limit,
            "soft_breached": dll.soft_limit is not None and realized_today <= -dll.soft_limit,
            "hard_breached": realized_today <= -dll.hard_limit,
        }
        if rep["daily_loss"]["hard_breached"]:
            rep["warnings"].append(
                f"DAILY HARD LOSS LIMIT breached: {realized_today:.0f} <= -{dll.hard_limit:.0f}")
        elif rep["daily_loss"]["soft_breached"]:
            rep["warnings"].append(
                f"daily soft loss limit breached: {realized_today:.0f} <= -{dll.soft_limit:.0f}")
    except Exception as e:
        rep["warnings"].append(f"daily_loss_limit unavailable: {e}")

    return rep


def format_report(r: dict) -> str:
    L = ["═══ MANUAL-BOOK RISK SNAPSHOT (read-only) ═══",
         f"capital: ₹{r['capital']:,.0f}   open positions: {r['n_positions']}"]
    if r["n_positions"] == 0:
        L.append("no open manual positions.")
    else:
        L.append(f"exposure: ₹{r['total_exposure']:,.0f} "
                 f"({(r['exposure_pct'] or 0):.0%} of capital)")
        L.append(f"portfolio risk (sum of per-trade risk): ₹{r['portfolio_risk']:,.0f} "
                 f"({(r['portfolio_risk_pct'] or 0):.1%})")
        if r["correlated_clusters"]:
            L.append(f"correlated clusters: {r['correlated_clusters']}")
    if r.get("var"):
        v = r["var"]
        L.append(f"VaR(95%): ₹{v['var_95']:,.0f}  CVaR: ₹{v['cvar_95']:,.0f}  "
                 f"safe_to_trade={v['safe_to_trade']}")
    if r.get("daily_loss"):
        d = r["daily_loss"]
        L.append(f"today realized P&L: ₹{d['realized_today']:,.0f}  "
                 f"(soft {d['soft_limit']}, hard {d['hard_limit']})")
    if r["limits"]:
        L.append(f"limits in force: {r['limits']}")
    if r["warnings"]:
        L.append("⚠ WARNINGS:")
        for w in r["warnings"]:
            L.append(f"  • {w}")
    else:
        L.append("✓ no risk-limit warnings")
    return "\n".join(L)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="Read-only manual-book risk snapshot.")
    p.add_argument("--capital", type=float, default=None,
                   help="account capital (defaults to REAL_CAPITAL env)")
    p.add_argument("--json", action="store_true", help="emit JSON instead of text")
    a = p.parse_args(argv)
    cap = _capital(a.capital)
    positions = load_open_positions()
    realized = today_realized_pnl()
    rep = build_report(cap, positions, realized)
    if a.json:
        import json
        print(json.dumps(rep, indent=2, default=str))
    else:
        print(format_report(rep))


if __name__ == "__main__":
    main()
