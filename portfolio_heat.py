"""
portfolio_heat.py — Portfolio Heat & Correlation Monitor

Tracks real-time correlation-adjusted exposure.
Prevents taking too many correlated positions simultaneously.

Example: Long NIFTY + Long BANKNIFTY + Long HDFCBANK + Long ICICIBANK
= 4 positions but in a crash ALL fall together.
Portfolio heat = correlation-adjusted = ~2 effective positions of risk.

HEAT FORMULA:
  heat = sum of (position_risk × correlation_weight)
  where correlation_weight = average pairwise correlation with existing

THRESHOLDS:
  heat < 2.0 → Allow new positions freely
  heat 2.0-3.5 → Warn, allow with reduced size
  heat > 3.5 → Block new positions until existing reduce
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

logger = logging.getLogger(__name__)
_CORR_FILE  = Path("correlation_matrix.json")

# Correlation groups (instruments that move together)
_CORRELATION_GROUPS = {
    "NIFTY_INDICES": {"NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY","NIFTYNEXT50"},
    "BANKING":       {"HDFCBANK","ICICIBANK","SBIN","AXISBANK","KOTAKBANK","INDUSINDBK"},
    "IT":            {"TCS","INFY","WIPRO","HCLTECH","TECHM"},
    "AUTO":          {"MARUTI","TATAMOTORS","M&M","BAJAJ-AUTO","HEROMOTOCO","EICHERMOT"},
    "ENERGY":        {"RELIANCE","ONGC","BPCL","IOC","NTPC","POWERGRID"},
    "METALS":        {"TATASTEEL","JSWSTEEL","HINDALCO","COALINDIA"},
    "PHARMA":        {"SUNPHARMA","DRREDDY","CIPLA","DIVISLAB"},
    "FMCG":          {"HINDUNILVR","ITC","NESTLEIND"},
}

def _get_correlation_group(symbol: str) -> str:
    su = symbol.upper()
    for group, members in _CORRELATION_GROUPS.items():
        if su in members:
            return group
    return su  # unique group = no correlation assumed


def _load_correlation_matrix() -> Tuple[List[str], np.ndarray]:
    """Load pre-computed correlation matrix from idle_engine output."""
    try:
        if _CORR_FILE.exists():
            data = json.loads(_CORR_FILE.read_text())
            syms = data.get("symbols", [])
            mat  = np.array(data.get("matrix", []))
            if len(syms) > 0 and mat.shape[0] == len(syms):
                return syms, mat
    except Exception:
        pass
    return [], np.array([])


def compute_portfolio_heat(
    open_positions: List[Dict],
    capital:        float = 100_000,
) -> Dict:
    """
    Compute portfolio heat from open positions.

    Returns:
        heat:           float (0-5+, higher = more correlated risk)
        heat_pct:       float (as % of capital)
        groups:         dict {group: count}
        dominant_group: most concentrated group
        block_new:      bool
        reduce_size:    float (multiplier 0-1)
        message:        str
    """
    if not open_positions:
        return {"heat": 0.0, "block_new": False, "reduce_size": 1.0, "message": "No positions"}

    syms, corr_mat = _load_correlation_matrix()

    # Group positions by correlation group
    groups: Dict[str, float] = {}   # group → total risk
    total_risk = 0.0

    for pos in open_positions:
        sym     = str(pos.get("symbol","")).upper()
        # Estimated risk = notional × stop distance %
        entry   = float(pos.get("entry_price", 0) or 0)
        stop    = float(pos.get("stop_loss",   0) or 0)
        qty     = int(pos.get("qty", 1) or 1)
        risk    = abs(entry - stop) * qty if (entry and stop) else entry * qty * 0.015
        group   = _get_correlation_group(sym)
        groups[group] = groups.get(group, 0) + risk
        total_risk   += risk

    # Compute heat: each group adds risk, but correlated groups add less incremental safety
    heat = 0.0
    prev_groups = []
    for group, risk in sorted(groups.items(), key=lambda x: -x[1]):
        # First position in group = full risk
        # Each additional correlated position adds less diversification
        corr_penalty = 1.0 + 0.5 * len([g for g in prev_groups
                                         if g == group or _are_correlated(g, group)])
        heat += (risk / max(capital, 1) * 100) * corr_penalty
        prev_groups.append(group)

    dominant = max(groups, key=lambda g: groups[g]) if groups else "None"

    # Thresholds
    block_new    = heat > 3.5
    reduce_size  = 1.0 if heat < 2.0 else max(0.3, 1.0 - (heat - 2.0) * 0.3)
    icon         = "🟢" if heat < 2.0 else "🟡" if heat < 3.5 else "🔴"

    return {
        "heat":          round(heat, 2),
        "heat_pct":      round(total_risk / max(capital,1) * 100, 1),
        "groups":        {g: round(r,0) for g,r in groups.items()},
        "dominant":      dominant,
        "block_new":     block_new,
        "reduce_size":   round(reduce_size, 2),
        "icon":          icon,
        "message": (
            f"{icon} Heat: {heat:.1f}  "
            + ("⛔ Block new — too correlated" if block_new
               else f"⚠️ Reduce size ×{reduce_size:.1f}" if heat > 2.0
               else "✅ Normal")
        ),
    }


def _are_correlated(g1: str, g2: str) -> bool:
    """Check if two groups are highly correlated."""
    corr_pairs = {
        frozenset(["NIFTY_INDICES","BANKING"]),
        frozenset(["NIFTY_INDICES","IT"]),
        frozenset(["BANKING","NIFTY_INDICES"]),
    }
    return frozenset([g1, g2]) in corr_pairs


class PortfolioHeatMonitor:
    """Wire into portfolio_risk.py to block correlated positions."""

    def __init__(self, capital: float = 100_000, alerts=None) -> None:
        self.capital = capital
        self.alerts  = alerts

    def check_new_trade(
        self,
        new_symbol:     str,
        open_positions: List[Dict],
    ) -> Tuple[bool, float, str]:
        """
        Returns (allowed, size_multiplier, reason).
        Call this before approving a new trade.
        """
        heat_now = compute_portfolio_heat(open_positions, self.capital)

        if heat_now["block_new"]:
            return False, 0.0, f"Portfolio heat {heat_now['heat']:.1f} — too correlated"

        new_group = _get_correlation_group(new_symbol)
        existing_groups = [_get_correlation_group(str(p.get("symbol","")))
                           for p in open_positions]

        # If adding more to same group → reduce further
        same_group_count = existing_groups.count(new_group)
        extra_reduction  = 1.0 - (same_group_count * 0.2)
        final_mult       = heat_now["reduce_size"] * max(0.3, extra_reduction)

        reason = (f"Heat OK ({heat_now['heat']:.1f})" if final_mult >= 0.9
                  else f"Reducing size {final_mult:.0%} — {same_group_count} correlated")

        return True, round(final_mult, 2), reason

    def telegram_summary(self, open_positions: List[Dict]) -> str:
        h = compute_portfolio_heat(open_positions, self.capital)
        lines = [
            f"{h['icon']} <b>PORTFOLIO HEAT</b>",
            f"  Heat:     {h['heat']:.2f} / 3.5 limit",
            f"  Exposure: {h['heat_pct']:.1f}% of capital",
        ]
        if h["groups"]:
            lines.append("  Groups:")
            for g, r in sorted(h["groups"].items(), key=lambda x: -x[1])[:4]:
                lines.append(f"    {g}: ₹{r:,.0f}")
        lines.append(f"  {h['message']}")
        return "\n".join(lines)
