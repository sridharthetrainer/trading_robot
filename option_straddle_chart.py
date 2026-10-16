#!/usr/bin/env python3
"""
option_straddle_chart.py

Intraday ATM straddle premium + Black-Scholes ATM IV chart from
option_chain_snapshots.db (the "straddle chart" Sensibull / OI Pulse /
Opstra show). Context only -- it is not a trade signal: the 2026-10-14
research found no tradeable options edge (see reports/research_2026-10-14).

Two straddle lines are drawn:
  * rolling ATM  -- CE+PE premium at the strike nearest spot in each snapshot
                    (what the market currently prices as the move to expiry);
  * opening ATM  -- CE+PE premium of the first snapshot's ATM strike all day
                    (pure decay/IV change, no strike hopping).
The dashed line is the running average of the rolling-ATM straddle.

IV is solved from each snapshot's CE and PE lastPrice (Angel quotes carry no
greeks/IV) and averaged; a side that cannot be solved is left out.

    python option_straddle_chart.py --underlying NIFTY --date 2026-10-15
"""

from __future__ import annotations

import argparse
import json
import logging
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import chart_theme as ct
from option_intraday_pricer import RISK_FREE_RATE, implied_vol, year_fraction
from option_oi_chart import DB_PATH, OIChartResult, _load_snapshot_rows, _parse_time, _safe_float

logger = logging.getLogger(__name__)


def _rows_by_strike(snap: Dict[str, Any]) -> Dict[float, Dict[str, Any]]:
    try:
        rows = json.loads(snap.get("rows_json") or "[]")
    except Exception:
        return {}
    out = {}
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        k = _safe_float(r, "strikePrice", "strike")
        if k > 0 and _safe_float(r, "CE_lastPrice") > 0 and _safe_float(r, "PE_lastPrice") > 0:
            out[k] = r
    return out


def _atm_iv(row: Dict[str, Any], spot: float, strike: float, t_years: float) -> Optional[float]:
    ivs = [implied_vol(_safe_float(row, f"{side}_lastPrice"), spot, strike, t_years, RISK_FREE_RATE, side)
           for side in ("CE", "PE")]
    ivs = [v for v in ivs if v]
    return sum(ivs) / len(ivs) if ivs else None


def straddle_series(snapshots: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One point per snapshot: rolling-ATM straddle, opening-ATM straddle, ATM IV."""
    out: List[Dict[str, Any]] = []
    open_strike = None
    for snap in snapshots:
        spot = float(snap.get("spot") or 0)
        rows = _rows_by_strike(snap)
        if spot <= 0 or not rows:
            continue
        atm = min(rows, key=lambda k: abs(k - spot))
        if open_strike is None:
            open_strike = atm
        row = rows[atm]
        ts = _parse_time(str(snap.get("snapshot_time") or ""))
        iv = None
        try:
            expiry = date.fromisoformat(str(row.get("expiryDate") or "")[:10])
            iv = _atm_iv(row, spot, atm, year_fraction(ts, expiry))
        except ValueError:
            logger.debug("straddle IV skipped: bad expiryDate %r", row.get("expiryDate"))
        open_row = rows.get(open_strike)
        out.append({
            "time": ts,
            "label": ts.strftime("%H:%M"),
            "spot": spot,
            "atm": atm,
            "straddle": _safe_float(row, "CE_lastPrice") + _safe_float(row, "PE_lastPrice"),
            "open_straddle": (_safe_float(open_row, "CE_lastPrice") + _safe_float(open_row, "PE_lastPrice"))
                             if open_row else None,
            "iv": iv,
        })
    return out


def generate_straddle_chart(
    *,
    underlying: str = "NIFTY",
    day: Optional[str] = None,
    db_path: str = DB_PATH,
    output_dir: Optional[str] = None,
) -> OIChartResult:
    underlying = str(underlying or "NIFTY").upper()
    day = str(day or datetime.now().strftime("%Y-%m-%d"))
    series = straddle_series(_load_snapshot_rows(db_path=db_path, underlying=underlying, day=day))
    if len(series) < 2:
        return OIChartResult(False, reason=f"not_enough_straddle_points_for_{underlying}_{day}")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    labels = [p["label"] for p in series]
    straddle = [p["straddle"] for p in series]
    running_avg = [sum(straddle[: i + 1]) / (i + 1) for i in range(len(straddle))]
    open_k = series[0]["atm"]
    open_straddle = [p["open_straddle"] if p["open_straddle"] is not None else float("nan") for p in series]
    iv = [p["iv"] * 100 if p["iv"] else float("nan") for p in series]
    spot = [p["spot"] for p in series]

    fig, axes = plt.subplots(2, 1, figsize=(12, 7.5), sharex=True, dpi=130,
                             gridspec_kw={"height_ratios": [3, 2]})
    fig.patch.set_facecolor(ct.BG)
    fig.suptitle(f"{underlying} ATM Straddle | {day}", color=ct.TEXT_PRIMARY, fontsize=14, fontweight="bold")
    for ax in axes:
        ax.set_facecolor(ct.BG)
        ax.grid(True, color=ct.GRID, alpha=0.55)
        ax.tick_params(colors=ct.TEXT_SECONDARY, labelsize=8)
        for spine in ax.spines.values():
            spine.set_color(ct.GRID)

    axes[0].plot(labels, straddle, color=ct.WARNING, linewidth=2.2, label="Rolling ATM straddle")
    axes[0].plot(labels, open_straddle, color=ct.CALL_MUTED, linewidth=1.6, label=f"Opening ATM {int(open_k)} straddle")
    axes[0].plot(labels, running_avg, color=ct.TEXT_MUTED, linewidth=1.2, linestyle="--", label="Running average")
    axes[0].set_ylabel("Premium (CE+PE)", color=ct.TEXT_SECONDARY)
    axes[0].legend(loc="upper right", facecolor=ct.PANEL, edgecolor=ct.GRID, labelcolor=ct.TEXT_SECONDARY)

    axes[1].plot(labels, iv, color=ct.PUT, linewidth=2.0, label="ATM IV % (Black-Scholes)")
    axes[1].set_ylabel("IV %", color=ct.TEXT_SECONDARY)
    ax_spot = axes[1].twinx()
    ax_spot.plot(labels, spot, color=ct.TEXT_SECONDARY, linewidth=1.0, alpha=0.7, label="Spot")
    ax_spot.tick_params(colors=ct.TEXT_SECONDARY, labelsize=8)
    h1, l1 = axes[1].get_legend_handles_labels()
    h2, l2 = ax_spot.get_legend_handles_labels()
    axes[1].legend(h1 + h2, l1 + l2, loc="upper right", facecolor=ct.PANEL, edgecolor=ct.GRID,
                   labelcolor=ct.TEXT_SECONDARY)
    step = max(1, len(labels) // 8)
    axes[1].set_xticks(range(0, len(labels), step))
    axes[1].set_xticklabels(labels[::step], rotation=35, ha="right")

    plt.tight_layout(rect=[0, 0.02, 1, 0.95])
    out_dir = Path(output_dir or tempfile.gettempdir())
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"straddle_{underlying}_{day}.png"
    fig.savefig(out_path, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)

    first, last = series[0], series[-1]
    chg = (last["straddle"] / first["straddle"] - 1) * 100 if first["straddle"] else 0.0
    iv_txt = f"{last['iv'] * 100:.1f}%" if last["iv"] else "n/a"
    iv_open = f"{first['iv'] * 100:.1f}%" if first["iv"] else "n/a"
    caption = (
        f"{underlying} ATM straddle {day} ({last['label']})\n"
        f"Now {last['straddle']:.1f} @ {int(last['atm'])} | open {first['straddle']:.1f} ({chg:+.1f}%)\n"
        f"Avg {running_avg[-1]:.1f} | ATM IV {iv_txt} (open {iv_open})\n"
        f"Breakevens to expiry: {last['atm'] - last['straddle']:.0f} / {last['atm'] + last['straddle']:.0f}\n"
        f"Context only, not a trade signal."
    )
    return OIChartResult(True, path=str(out_path), caption=caption, points=len(series))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--underlying", default="NIFTY")
    p.add_argument("--date", default=None)
    p.add_argument("--output-dir", default=None)
    a = p.parse_args()
    res = generate_straddle_chart(underlying=a.underlying, day=a.date, output_dir=a.output_dir)
    print(res.path if res.ok else res.reason)
    print(res.caption)
    return 0 if res.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
