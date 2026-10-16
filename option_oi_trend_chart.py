#!/usr/bin/env python3
"""
option_oi_trend_chart.py

"Trending OI" chart (TradingTick / OI Pulse style): one line per strike+side,
y = OI added since the day's first snapshot, from option_chain_snapshots.db.
Measuring from the first snapshot makes it independent of the provider's
change-in-OI field (Angel quotes have none). Context only, not a trade signal.

Strikes are picked automatically (largest OI build/unwind today) or given:
    python option_oi_trend_chart.py --underlying NIFTY --date 2026-10-15
    python option_oi_trend_chart.py --legs 23000CE,22800PE
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import chart_theme as ct
from option_oi_chart import DB_PATH, OIChartResult, _load_snapshot_rows, _parse_time, _row_strike, _safe_float, _snapshot_rows

logger = logging.getLogger(__name__)

Leg = Tuple[float, str]  # (strike, "CE" | "PE")
# 10 distinct colors: chart_theme's categorical palette has 5 and cycles, which
# made two strikes share a color once 6 legs were drawn.
_LEG_COLORS = ("#4dabf7", "#ffd43b", "#ff6b6b", "#69db7c", "#da77f2",
               "#ff922b", "#3bc9db", "#f783ac", "#a9e34b", "#e9ecef")


def parse_legs(text: str) -> List[Leg]:
    """'23000CE,22800pe' / '23000 CE' -> [(23000.0, 'CE'), (22800.0, 'PE')]."""
    return [(float(k), side.upper()) for k, side in re.findall(r"(\d{4,6})\s*(CE|PE)", str(text or ""), re.I)]


def oi_added_series(snapshots: List[Dict]) -> Tuple[List[str], Dict[Leg, List[float]]]:
    """Time labels and, per leg, OI minus that leg's first observed OI today
    (NaN before the leg first appears in a snapshot)."""
    labels: List[str] = []
    base: Dict[Leg, float] = {}
    series: Dict[Leg, List[float]] = {}
    for snap in snapshots:
        rows = _snapshot_rows(snap)
        if not rows:
            continue
        idx = len(labels)
        labels.append(_parse_time(str(snap.get("snapshot_time") or "")).strftime("%H:%M"))
        for row in rows:
            k = _row_strike(row)
            if k <= 0:
                continue
            for side in ("CE", "PE"):
                oi = _safe_float(row, f"{side}_openInterest", f"{side}_OI")
                if oi <= 0:
                    continue
                leg = (k, side)
                base.setdefault(leg, oi)
                s = series.setdefault(leg, [float("nan")] * idx)
                s.extend([float("nan")] * (idx - len(s)))
                s.append(oi - base[leg])
    for s in series.values():
        s.extend([float("nan")] * (len(labels) - len(s)))
    return labels, series


def _last(values: List[float]) -> float:
    for v in reversed(values):
        if v == v:
            return v
    return 0.0


def generate_oi_trend_chart(
    *,
    underlying: str = "NIFTY",
    day: Optional[str] = None,
    legs: Optional[List[Leg]] = None,
    top: int = 5,
    side: Optional[str] = None,
    db_path: str = DB_PATH,
    output_dir: Optional[str] = None,
) -> OIChartResult:
    underlying = str(underlying or "NIFTY").upper()
    day = str(day or datetime.now().strftime("%Y-%m-%d"))
    snapshots = _load_snapshot_rows(db_path=db_path, underlying=underlying, day=day)
    labels, series = oi_added_series(snapshots)
    if len(labels) < 2:
        return OIChartResult(False, reason=f"not_enough_oi_points_for_{underlying}_{day}")

    if legs:
        chosen = [leg for leg in legs if leg in series]
        if not chosen:
            return OIChartResult(False, reason="requested_strikes_not_in_snapshots")
    # Direction read uses the 5 largest CE and 5 largest PE moves, whatever is drawn.
    by_side = {sd: sorted([leg for leg in series if leg[1] == sd],
                          key=lambda leg: abs(_last(series[leg])), reverse=True)[:5] for sd in ("CE", "PE")}
    if not legs:
        # Draw only the `top` (default 5) largest builds/unwinds across both
        # sides, or within one side if asked.
        pool = [leg for leg in series if not side or leg[1] == side.upper()]
        chosen = sorted(pool, key=lambda leg: abs(_last(series[leg])), reverse=True)[:max(1, min(10, top))]

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(12, 7), dpi=130)
    fig.patch.set_facecolor(ct.BG)
    ax.set_facecolor(ct.BG)
    ax.grid(True, color=ct.GRID, alpha=0.55)
    ax.tick_params(colors=ct.TEXT_SECONDARY, labelsize=8)
    for spine in ax.spines.values():
        spine.set_color(ct.GRID)
    spot = float(snapshots[-1].get("spot") or 0)
    fig.suptitle(f"{underlying} Trending OI | {day} | spot {spot:,.0f}",
                 color=ct.TEXT_PRIMARY, fontsize=14, fontweight="bold")

    x = list(range(len(labels)))
    for i, leg in enumerate(chosen):
        # Connect across snapshots where the strike fell outside the stored window.
        pts = [(xi, v) for xi, v in zip(x, series[leg]) if v == v]
        ax.plot([p[0] for p in pts], [p[1] for p in pts], color=_LEG_COLORS[i % len(_LEG_COLORS)],
                linewidth=2.0, marker="o", markersize=3.2,
                markerfacecolor="none", linestyle="-" if leg[1] == "CE" else "--",
                label=f"{leg[1]} {int(leg[0])}")
    ax.axhline(0, color=ct.TEXT_MUTED, linewidth=0.8)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(
        lambda v, _: f"{v / 1e5:.1f}L" if abs(v) >= 1e5 else f"{v / 1e3:.0f}K" if v else "0"))
    ax.set_ylabel(f"OI added since {labels[0]} (shares)", color=ct.TEXT_SECONDARY)
    ax.legend(loc="upper left", ncol=min(len(chosen), 4), fontsize=8, facecolor=ct.PANEL,
              edgecolor=ct.GRID, labelcolor=ct.TEXT_SECONDARY)
    step = max(1, len(labels) // 9)
    ax.set_xticks(x[::step])
    ax.set_xticklabels(labels[::step], rotation=35, ha="right")
    plt.tight_layout(rect=[0, 0.02, 1, 0.95])

    out_dir = Path(output_dir or tempfile.gettempdir())
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"oi_trend_{underlying}_{day}.png"
    fig.savefig(out_path, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)

    ranked = sorted(chosen, key=lambda leg: _last(series[leg]), reverse=True)
    lines = [f"{underlying} trending OI {day} ({labels[0]} to {labels[-1]}), spot {spot:,.0f}"]
    lines += [f"{leg[1]} {int(leg[0])}: {_last(series[leg]) / 1e5:+.1f}L shares" for leg in ranked]
    ce_add = sum(_last(series[leg]) for leg in by_side["CE"])
    pe_add = sum(_last(series[leg]) for leg in by_side["PE"])
    if not legs and not side:
        if pe_add > 0 and pe_add > ce_add * 1.5:
            read = "put writing dominant (support building, bullish lean)"
        elif ce_add > 0 and ce_add > pe_add * 1.5:
            read = "call writing dominant (resistance building, bearish lean)"
        else:
            read = "no clear side"
        lines.append(f"OI read (top 5 each side): PE {pe_add / 1e5:+.1f}L vs CE {ce_add / 1e5:+.1f}L -> {read}")
    lines.append("Solid = CE, dashed = PE. Context only: OI bias was tested (Test 23) "
                 "and showed no trading edge.")
    return OIChartResult(True, path=str(out_path), caption="\n".join(lines), points=len(labels))


_POST_STATE = Path(os.getenv("OPTION_TRENDOI_STATE", "option_trendoi_state.json"))


def _post_slots() -> List[str]:
    raw = os.getenv("OPTION_TRENDOI_TIMES", "10:00,11:00,12:00,13:00,14:00,15:00,15:30")
    return sorted(t.strip() for t in raw.split(",") if re.match(r"^\d{2}:\d{2}$", t.strip()))


def post_trending_oi_if_due(now: Optional[datetime] = None, alerts=None) -> List[str]:
    """Post the trending-OI chart to the option channel at each scheduled slot
    (called after every snapshot round). A slot is posted once per day, and
    only within 25 min of its time so a restart does not replay old slots.
    OPTION_TRENDOI_ENABLED=false turns it off."""
    if os.getenv("OPTION_TRENDOI_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
        return []
    now = now or datetime.now()
    day = now.strftime("%Y-%m-%d")
    due = [t for t in _post_slots()
           if 0 <= (now - datetime.strptime(f"{day} {t}", "%Y-%m-%d %H:%M")).total_seconds() < 25 * 60]
    if not due:
        return []
    slot = due[-1]
    try:
        state = json.loads(_POST_STATE.read_text()) if _POST_STATE.exists() else {}
    except (OSError, ValueError):
        state = {}
    sent = state.get("sent", []) if state.get("day") == day else []
    syms = [x.strip().upper() for x in os.getenv("OPTION_TRENDOI_UNDERLYINGS", "NIFTY,BANKNIFTY").split(",")
            if x.strip()]
    todo = [sym for sym in syms if f"{slot}:{sym}" not in sent]
    if not todo:
        return []
    if alerts is None:
        from option_decision_journal import _option_alerts
        alerts = _option_alerts()
    if alerts is None:
        return []
    posted = []
    for sym in todo:
        try:
            res = generate_oi_trend_chart(underlying=sym, day=day)
            if res.ok and alerts.send_photo(res.path, caption=f"🕒 {slot} " + res.caption):
                posted.append(f"{slot}:{sym}")
            elif not res.ok:
                logger.info("trending OI %s %s skipped: %s", slot, sym, res.reason)
        except Exception as exc:
            logger.warning("trending OI post %s %s failed: %s", slot, sym, exc)
    if posted:
        try:
            _POST_STATE.write_text(json.dumps({"day": day, "sent": sent + posted}))
        except OSError as exc:
            logger.warning("trending OI state write failed: %s", exc)
    return posted


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--underlying", default="NIFTY")
    p.add_argument("--date", default=None)
    p.add_argument("--legs", default="", help="e.g. 23000CE,22800PE")
    p.add_argument("--top", type=int, default=5)
    p.add_argument("--side", choices=["CE", "PE"], default=None)
    p.add_argument("--output-dir", default=None)
    a = p.parse_args()
    res = generate_oi_trend_chart(underlying=a.underlying, day=a.date, legs=parse_legs(a.legs) or None,
                                  top=a.top, side=a.side, output_dir=a.output_dir)
    print(res.path if res.ok else res.reason)
    print(res.caption)
    return 0 if res.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
