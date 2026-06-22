"""
condor_forward_test.py — PAPER forward-test of a defined-risk IRON CONDOR on
NIFTY, using REAL live market data but placing NO real orders ever.

WHY: the iron condor is the only options structure with a defensible premise
(capped tail + positive calm-market expectancy), but it FAILED strict OOS on the
small synthetic backtest (condor_validation.json). The only honest way to earn
"proven" is to forward-test it on real data over many real expiries. This script
is that apparatus.

WHAT IT DOES (run once per trading day, e.g. via cron/scheduler or by hand):
  • If flat and a new weekly cycle is available → open a paper condor for the
    upcoming NIFTY weekly expiry (Tuesday): short ~1σ strangle + long ~2σ wings
    (defined risk). Entry premiums are Black-Scholes priced off live spot + India
    VIX (same model as options_backtest.py).
  • If a paper position is open and today is on/after its expiry → settle at
    intrinsic, book net P&L (incl. brokerage/slippage/STT), log the trade.
  • Otherwise → just mark-to-market for information.
  • State persists in condor_forward_test.json so a real OOS track record builds
    up over weeks.

SAFETY: standalone. Imports no live-trading engine, places no orders, flips no
flags. Even though the account is live, this cannot touch it.

CAVEATS: BS entry pricing (no real bid/ask/skew — real fills will be a bit
worse); settles at the expiry-day SPOT this script happens to see (run it ON
expiry day for an accurate close). It is a PAPER track record, not live fills.
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("condor_forward_test")

from options_backtest import (
    leg_price, _round_strike, _legs_value, _open_costs, _close_costs,
    Leg, LOT_SIZE, STRIKE_STEP,
)

STATE_FILE   = Path(__file__).resolve().parent / "condor_forward_test.json"
EXPIRY_WD    = 1          # NIFTY weekly expiry = Tuesday (post Sep-2025)
RISK_FREE    = 0.065
LOTS         = 1
MIN_DTE_OPEN = 2          # only open a new condor with >=2 days to expiry


# ── live data ────────────────────────────────────────────────────────────────
def _fetch_spot_vix() -> Optional[Dict[str, float]]:
    """NIFTY 50 spot + India VIX from NSE allIndices (no order, read-only)."""
    try:
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0",
                          "Referer": "https://www.nseindia.com"})
        s.get("https://www.nseindia.com/", timeout=5)
        r = s.get("https://www.nseindia.com/api/allIndices", timeout=8)
        spot = vix = 0.0
        for idx in r.json().get("data", []):
            nm = str(idx.get("index", ""))
            if nm == "NIFTY 50":
                spot = float(idx.get("last", 0) or 0)
            elif "VIX" in nm.upper():
                vix = float(idx.get("last", 0) or 0)
        if spot > 0:
            return {"spot": spot, "vix": vix or 14.0}
    except Exception as e:
        log.warning("NSE fetch failed: %s", e)
    return None


def _next_expiry(today: date) -> date:
    return today + timedelta(days=(EXPIRY_WD - today.weekday()) % 7)


# ── state ────────────────────────────────────────────────────────────────────
def _load() -> Dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"open": None, "closed": [], "created": str(date.today())}


def _save(state: Dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str))


def _legs_from(state_legs: List[Dict]) -> List[Leg]:
    return [Leg(l["strike"], l["opt_type"], l["qty"], l["entry_prem"]) for l in state_legs]


# ── condor construction ──────────────────────────────────────────────────────
def _build_condor(spot: float, vix: float, T: float, lots: int = LOTS):
    atm = _round_strike(spot)
    sd  = spot * (max(vix, 1e-6) / 100.0) * (T ** 0.5)      # 1σ move
    w   = max(STRIKE_STEP, _round_strike(sd))
    sc, sp = atm + w, atm - w           # short strangle (~1σ)
    lc, lp = atm + 2 * w, atm - 2 * w   # long wings (~2σ) → defined risk
    legs = [
        Leg(sc, "call", -lots, leg_price(spot, sc, T, vix, "call")),
        Leg(sp, "put",  -lots, leg_price(spot, sp, T, vix, "put")),
        Leg(lc, "call",  lots, leg_price(spot, lc, T, vix, "call")),
        Leg(lp, "put",   lots, leg_price(spot, lp, T, vix, "put")),
    ]
    credit   = -_legs_value(legs, spot, T, vix)             # cash collected (>0)
    max_loss = w * LOT_SIZE * lots - credit                 # capped (wing width)
    return legs, credit, max_loss, w


# ── stats ────────────────────────────────────────────────────────────────────
def _stats(closed: List[Dict]) -> Dict:
    if not closed:
        return {"trades": 0}
    pnls = [c["pnl_rs"] for c in closed]
    wins = [p for p in pnls if p > 0]
    import statistics as st
    gl = sum(p for p in pnls if p < 0)
    return {
        "trades":        len(pnls),
        "win_rate_pct":  round(len(wins) / len(pnls) * 100, 1),
        "total_pnl":     round(sum(pnls), 0),
        "avg_pnl":       round(st.mean(pnls), 0),
        "best":          round(max(pnls), 0),
        "worst":         round(min(pnls), 0),
        "profit_factor": round(sum(wins) / abs(gl), 2) if gl < 0 else None,
    }


# ── one daily step ───────────────────────────────────────────────────────────
def step() -> None:
    state = _load()
    today = date.today()
    if today.weekday() >= 5:
        log.info("Weekend — no action."); return
    try:                                   # skip NSE holidays (stale data would pollute the record)
        from nse_master import get_nse_master
        if get_nse_master().is_trading_holiday(today):
            log.info("NSE holiday — no action."); return
    except Exception:
        pass

    md = _fetch_spot_vix()
    if not md:
        log.error("No live spot/VIX — cannot run this step (try during/after market hours)."); return
    spot, vix = md["spot"], md["vix"]
    log.info("Live NIFTY=%.0f  India VIX=%.1f", spot, vix)

    op = state.get("open")

    # 1) settle an open condor that has reached expiry
    if op:
        exp = datetime.strptime(op["expiry_date"], "%Y-%m-%d").date()
        if today >= exp:
            legs = _legs_from(op["legs"])
            v_exit  = _legs_value(legs, spot, 0.0, vix)              # intrinsic
            v_entry = _legs_value(legs, op["spot_entry"], op["T_entry"], op["vix_entry"])
            gross   = v_exit - v_entry
            costs   = _open_costs(legs) + _close_costs(legs, spot, 0.0, vix)
            pnl     = round(gross - costs, 0)
            trade = {
                "entry_date": op["entry_date"], "exit_date": str(today),
                "expiry_date": op["expiry_date"],
                "spot_entry": round(op["spot_entry"], 0), "spot_exit": round(spot, 0),
                "credit_rs": round(op["credit_rs"], 0), "max_loss_rs": round(op["max_loss_rs"], 0),
                "pnl_rs": pnl,
                "outcome": "WIN" if pnl > 0 else "LOSS",
            }
            state["closed"].append(trade)
            state["open"] = None
            op = None
            log.info("SETTLED condor → P&L ₹%+.0f (spot %0.f→%0.f)", pnl, trade["spot_entry"], spot)
        else:
            legs = _legs_from(op["legs"])
            dte  = (exp - today).days
            T    = max(dte, 0.5) / 365.0
            mtm  = round(_legs_value(legs, spot, T, vix)
                         - _legs_value(legs, op["spot_entry"], op["T_entry"], op["vix_entry"]), 0)
            log.info("Open condor held | expiry %s (%d DTE) | spot %.0f | credit ₹%.0f | "
                     "max_loss ₹%.0f | unrealised ₹%+.0f",
                     op["expiry_date"], dte, spot, op["credit_rs"], op["max_loss_rs"], mtm)

    # 2) open a new condor if flat and a fresh weekly cycle is available
    if not state.get("open"):
        exp = _next_expiry(today)
        dte = (exp - today).days
        if dte >= MIN_DTE_OPEN:
            T = dte / 365.0
            legs, credit, max_loss, w = _build_condor(spot, vix, T)
            state["open"] = {
                "entry_date": str(today), "expiry_date": str(exp),
                "spot_entry": spot, "vix_entry": vix, "T_entry": T, "width": w,
                "legs": [{"strike": l.strike, "opt_type": l.opt_type,
                          "qty": l.qty, "entry_prem": round(l.entry_prem, 2)} for l in legs],
                "credit_rs": round(credit, 0), "max_loss_rs": round(max_loss, 0),
            }
            log.info("OPENED paper condor | expiry %s (%d DTE) | short ±%d | credit ₹%.0f | max_loss ₹%.0f",
                     exp, dte, w, credit, max_loss)
        else:
            log.info("Flat, but only %d DTE to next expiry (<%d) — waiting for next cycle.",
                     dte, MIN_DTE_OPEN)

    _save(state)
    log.info("Running stats: %s", _stats(state["closed"]))


def main() -> int:
    p = argparse.ArgumentParser(description="Paper iron-condor forward-test (no real orders)")
    p.add_argument("--status", action="store_true", help="print state + stats only")
    p.add_argument("--reset", action="store_true", help="clear all state")
    args = p.parse_args()

    if args.reset:
        if STATE_FILE.exists():
            STATE_FILE.unlink()
        log.info("State cleared."); return 0
    if args.status:
        st = _load()
        print(json.dumps({"open": st.get("open"), "stats": _stats(st.get("closed", [])),
                          "n_closed": len(st.get("closed", []))}, indent=2, default=str))
        return 0
    step()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
