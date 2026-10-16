#!/usr/bin/env python3
"""
participant_oi_chart.py

FII / DII / Pro / Client F&O positioning trend from participant_oi.db
(NSE participant-wise OI, end of day) -- the "FII DII data" view Sensibull,
OI Pulse and Dhan show. Context only, not a trade signal.

Per participant per day:
  * index-futures long %  = long / (long + short) contracts
  * net index futures     = long - short contracts
  * net index options     = (call long - call short) - (put long - put short),
                            a crude directional tilt (+ = bullish exposure)

    python participant_oi_chart.py --days 20
"""

from __future__ import annotations

import argparse
import sqlite3
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

import chart_theme as ct
from option_oi_chart import OIChartResult

DB_PATH = "participant_oi.db"
PARTICIPANTS = ("FII", "DII", "Pro", "Client")


def load_positioning(db_path: str = DB_PATH, days: int = 20) -> Dict[str, object]:
    """{"dates": [...], "nifty": {date: close}, "FII": [{...}, ...], ...} oldest first."""
    if not Path(db_path).exists():
        return {"dates": []}
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        dates = [r[0] for r in con.execute(
            "SELECT DISTINCT date FROM participant_oi WHERE client_type='FII' ORDER BY date DESC LIMIT ?",
            (int(days),))][::-1]
        if not dates:
            return {"dates": []}
        marks = ",".join("?" * len(dates))
        rows = con.execute(f"SELECT * FROM participant_oi WHERE date IN ({marks})", dates).fetchall()
        try:
            nifty = dict(con.execute(f"SELECT date, close FROM nifty_daily WHERE date IN ({marks})", dates).fetchall())
        except sqlite3.OperationalError:
            nifty = {}
    by_key = {(r["date"], r["client_type"]): r for r in rows}
    out: Dict[str, object] = {"dates": dates, "nifty": nifty}
    for p in PARTICIPANTS:
        series = []
        for d in dates:
            r = by_key.get((d, p))
            if r is None:
                series.append(None)
                continue
            fl, fs = float(r["future_index_long"] or 0), float(r["future_index_short"] or 0)
            series.append({
                "long_pct": fl / (fl + fs) * 100 if fl + fs else None,
                "net_fut": fl - fs,
                "net_opt": (float(r["opt_index_call_long"] or 0) - float(r["opt_index_call_short"] or 0))
                           - (float(r["opt_index_put_long"] or 0) - float(r["opt_index_put_short"] or 0)),
            })
        out[p] = series
    return out


def _val(series: List[Optional[dict]], key: str) -> List[float]:
    return [s[key] if s and s.get(key) is not None else float("nan") for s in series]


def _fmt_change(series: List[Optional[dict]], key: str) -> str:
    vals = [s[key] for s in series if s and s.get(key) is not None]
    if len(vals) < 2:
        return "n/a"
    return f"{vals[-1] - vals[-2]:+,.0f}"


def generate_participant_chart(
    *, db_path: str = DB_PATH, days: int = 20, output_dir: Optional[str] = None,
) -> OIChartResult:
    data = load_positioning(db_path, days)
    dates = data["dates"]
    if len(dates) < 2:
        return OIChartResult(False, reason="not_enough_participant_oi_days")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    labels = [d[5:] for d in dates]
    colors = {"FII": ct.PUT, "DII": ct.CALL, "Pro": ct.WARNING, "Client": ct.TEXT_SECONDARY}
    fig, axes = plt.subplots(3, 1, figsize=(12, 9.5), sharex=True, dpi=130,
                             gridspec_kw={"height_ratios": [3, 2, 2]})
    fig.patch.set_facecolor(ct.BG)
    fig.suptitle(f"Participant F&O Positioning | {dates[0]} to {dates[-1]}",
                 color=ct.TEXT_PRIMARY, fontsize=14, fontweight="bold")
    for ax in axes:
        ax.set_facecolor(ct.BG)
        ax.grid(True, color=ct.GRID, alpha=0.55)
        ax.tick_params(colors=ct.TEXT_SECONDARY, labelsize=8)
        for spine in ax.spines.values():
            spine.set_color(ct.GRID)

    for p in PARTICIPANTS:
        axes[0].plot(labels, _val(data[p], "long_pct"), color=colors[p], linewidth=2.2 if p == "FII" else 1.5,
                     marker="o", markersize=3, label=p)
        axes[1].plot(labels, _val(data[p], "net_fut"), color=colors[p], linewidth=1.6, label=p)
        axes[2].plot(labels, _val(data[p], "net_opt"), color=colors[p], linewidth=1.6, label=p)
    axes[0].axhline(50, color=ct.TEXT_MUTED, linewidth=0.8, linestyle="--")
    axes[0].set_ylabel("Index fut long %", color=ct.TEXT_SECONDARY)
    nifty = [data["nifty"].get(d, float("nan")) for d in dates]
    if any(v == v for v in nifty):
        ax_n = axes[0].twinx()
        ax_n.plot(labels, nifty, color=ct.TEXT_PRIMARY, linewidth=1.0, alpha=0.5, linestyle=":", label="NIFTY")
        ax_n.tick_params(colors=ct.TEXT_SECONDARY, labelsize=8)
        h1, l1 = axes[0].get_legend_handles_labels()
        h2, l2 = ax_n.get_legend_handles_labels()
        axes[0].legend(h1 + h2, l1 + l2, loc="upper left", ncol=5, facecolor=ct.PANEL, edgecolor=ct.GRID,
                       labelcolor=ct.TEXT_SECONDARY, fontsize=8)
    else:
        axes[0].legend(loc="upper left", ncol=4, facecolor=ct.PANEL, edgecolor=ct.GRID,
                       labelcolor=ct.TEXT_SECONDARY, fontsize=8)
    for ax, title in ((axes[1], "Net index futures"), (axes[2], "Net index options (calls - puts)")):
        ax.axhline(0, color=ct.TEXT_MUTED, linewidth=0.8)
        ax.set_ylabel(title, color=ct.TEXT_SECONDARY, fontsize=9)
    step = max(1, len(labels) // 10)
    axes[2].set_xticks(range(0, len(labels), step))
    axes[2].set_xticklabels(labels[::step], rotation=35, ha="right")

    plt.tight_layout(rect=[0, 0.02, 1, 0.95])
    out_dir = Path(output_dir or tempfile.gettempdir())
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"participants_{dates[-1]}.png"
    fig.savefig(out_path, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)

    lines = [f"Participant positioning {dates[-1]} (index futures long %, day change in net contracts)"]
    for p in PARTICIPANTS:
        last = data[p][-1]
        if not last or last.get("long_pct") is None:
            lines.append(f"{p}: n/a")
            continue
        lines.append(f"{p}: {last['long_pct']:.1f}% long | fut {_fmt_change(data[p], 'net_fut')} "
                     f"| opt {_fmt_change(data[p], 'net_opt')}")
    lines.append("End-of-day NSE data. Context only, not a trade signal.")
    return OIChartResult(True, path=str(out_path), caption="\n".join(lines), points=len(dates))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--days", type=int, default=20)
    p.add_argument("--output-dir", default=None)
    a = p.parse_args()
    res = generate_participant_chart(days=a.days, output_dir=a.output_dir)
    print(res.path if res.ok else res.reason)
    print(res.caption)
    return 0 if res.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
