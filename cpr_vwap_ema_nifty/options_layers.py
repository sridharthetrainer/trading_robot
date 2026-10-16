"""
Option P&L layers for CPR_VWAP_EMA_Nifty trades (1 lot NIFTY index option,
ATM at entry, nearest weekly expiry; CE for LONG, PE for SHORT).

  B (modelled): Black-Scholes repricing with the PREVIOUS session's implied vol
     (solved from the real EOD settle in options_nifty.db) via the existing
     option_intraday_pricer.DayPricer (read-only call). IV is held constant
     through the day -- intraday IV moves are not modelled.
  C (real):     the option's lastPrice from option_chain_snapshots.db at the
     first snapshot at/after the entry and exit fills (5-min snapshots; only
     2026-06-19 onward).

Costs: 1 point of premium slippage per side, then nse_cost_model.NseCostModel
single_leg_cost (OPT_BUY, include_slippage=False, trade_date-aware STT).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .data import REPO_DIR

OPTIONS_DB = REPO_DIR / "options_nifty.db"
SNAPSHOT_DB = REPO_DIR / "option_chain_snapshots.db"


def _ro(path: Path) -> Optional[sqlite3.Connection]:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True) if Path(path).exists() else None


def option_net_pnl(p_in: float, p_out: float, d: date, cfg: dict) -> Tuple[float, float]:
    """(net rupees, total friction rupees = charges + slippage) for buying at
    p_in and selling at p_out."""
    from nse_cost_model import NseCostModel
    o = cfg["options"]
    qty = int(o["lot_size"]) * int(o.get("lots", 1))
    slip = float(o["slippage_points_per_side"])
    buy, sell = p_in + slip, max(0.0, p_out - slip)
    m = NseCostModel()
    charges = (m.single_leg_cost(buy * qty, "OPT_BUY", "BUY", include_slippage=False, trade_date=d).total
               + m.single_leg_cost(sell * qty, "OPT_BUY", "SELL", include_slippage=False, trade_date=d).total)
    net = (sell - buy) * qty - charges
    gross_no_friction = (p_out - p_in) * qty
    return net, gross_no_friction - net


class ModelledLayer:
    def __init__(self, cfg: dict, spot_close: Optional[Dict[str, float]] = None):
        """spot_close: {'YYYY-MM-DD': session close} from this package's own spot
        data -- used when options_eod.underlying is empty (all of 2020-2023)."""
        self.cfg = cfg
        self.con = _ro(OPTIONS_DB)
        self.spot_close = spot_close or {}

    @lru_cache(maxsize=4096)
    def _prev_date(self, d: str) -> Optional[str]:
        r = self.con.execute("SELECT MAX(date) FROM options_eod WHERE date<?", (d,)).fetchone()
        return r[0] if r and r[0] else None

    @lru_cache(maxsize=4096)
    def expiry_for(self, d: str) -> Optional[str]:
        """Nearest expiry on/after d, as listed on the PREVIOUS session (known in advance)."""
        prev = self._prev_date(d)
        if not prev:
            return None
        r = self.con.execute("SELECT MIN(expiry) FROM options_eod WHERE date=? AND expiry>=?", (prev, d)).fetchone()
        return r[0] if r and r[0] else None

    @lru_cache(maxsize=65536)
    def _settle(self, prev: str, expiry: str, strike: float, opt_type: str) -> Optional[Tuple[float, float]]:
        """(settle-or-close, underlying) on the previous session; underlying falls
        back to this package's own spot close when the column is empty."""
        row = self.con.execute("SELECT settle, close, underlying FROM options_eod WHERE date=? AND expiry=? "
                               "AND strike=? AND opt_type=?", (prev, expiry, float(strike), opt_type)).fetchone()
        if not row:
            return None
        settle, close, und = row
        px = settle if settle and settle > 0 else close
        und = und if und and und > 0 else self.spot_close.get(prev)
        if not px or px <= 0 or not und:
            return None
        return float(px), float(und)

    def price(self, d: str, strike: float, opt_type: str, when: datetime, spot: float,
              expiry: Optional[str] = None) -> Optional[float]:
        from option_intraday_pricer import DayPricer
        if self.con is None:
            return None
        prev = self._prev_date(d)
        expiry = expiry or self.expiry_for(d)
        if not prev or not expiry:
            return None
        q = self._settle(prev, expiry, float(strike), opt_type)
        if q is None:
            return None
        settle, und = q
        pr = DayPricer(und, float(strike), date.fromisoformat(prev), date.fromisoformat(expiry), opt_type, settle)
        return pr.price_at(when, spot) if pr.valid else None


class RealLayer:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.con = _ro(SNAPSHOT_DB)
        self._day_cache: Dict[str, List[Tuple[datetime, str, Dict[float, dict]]]] = {}

    def days(self) -> List[str]:
        if self.con is None:
            return []
        return [r[0] for r in self.con.execute(
            "SELECT DISTINCT substr(snapshot_time,1,10) FROM option_chain_snapshots "
            "WHERE ok=1 AND underlying='NIFTY' ORDER BY 1")]

    def _snaps(self, d: str):
        if d not in self._day_cache:
            out = []
            for st, exp, rows in self.con.execute(
                    "SELECT snapshot_time, expiry, rows_json FROM option_chain_snapshots WHERE ok=1 "
                    "AND underlying='NIFTY' AND substr(snapshot_time,1,10)=? ORDER BY snapshot_time", (d,)):
                try:
                    data = {float(r["strikePrice"]): r for r in json.loads(rows or "[]")}
                except Exception:
                    continue
                out.append((datetime.strptime(st[:19], "%Y-%m-%dT%H:%M:%S"), str(exp), data))
            self._day_cache[d] = out
        return self._day_cache[d]

    def price(self, d: str, strike: float, opt_type: str, when: datetime) -> Tuple[Optional[float], Optional[str]]:
        """lastPrice at the first snapshot at/after `when` (within the max lag)."""
        if self.con is None:
            return None, None
        lag = float(self.cfg["options"].get("real_price_max_lag_minutes", 10)) * 60
        for ts, exp, data in self._snaps(d):
            if ts < when:
                continue
            if (ts - when).total_seconds() > lag:
                return None, None
            row = data.get(float(strike))
            px = float((row or {}).get(f"{opt_type}_lastPrice") or 0)
            return (px if px > 0 else None), exp
        return None, None
