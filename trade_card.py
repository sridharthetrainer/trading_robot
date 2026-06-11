"""
trade_card.py — render a manual-trade status as a clean image, so the trade can
be understood at a glance in Telegram without opening the Angel One app.

Pure rendering (matplotlib, Agg backend) — no broker/network. Shared by the
manual-trade tracker and the Trade Guardian bot.
"""

from __future__ import annotations

import os
import matplotlib
matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch


def _fmt(x: float) -> str:
    try:
        return f"₹{float(x):,.2f}"
    except Exception:
        return "—"


def render_trade_card(
    symbol: str,
    side: str,
    qty: int,
    entry: float,
    ltp: float,
    sl: float,
    target: float,
    pnl: float,
    pnl_pct: float,
    out_path: str = "trade_card.png",
    extra: str = "",
) -> str:
    """
    Render a one-glance status card and return the file path.

    Layout: header (symbol/side/qty), a horizontal price track with SL · Entry ·
    LTP · Target markers placed to scale, and a large P&L readout.
    """
    is_long = str(side).upper() in ("BUY", "LONG")
    win = pnl >= 0
    accent = "#1a9850" if win else "#d73027"      # green / red
    bg = "#0e1117"

    fig, ax = plt.subplots(figsize=(8, 3.4), dpi=130)
    fig.patch.set_facecolor(bg)
    ax.set_facecolor(bg)
    ax.axis("off")

    # ── Header ────────────────────────────────────────────────────────────
    ax.text(0.02, 0.92, str(symbol), color="white", fontsize=17,
            fontweight="bold", transform=ax.transAxes, va="top")
    ax.text(0.02, 0.74, f"{str(side).upper()}  ·  {int(qty)} qty",
            color="#9aa0a6", fontsize=11, transform=ax.transAxes, va="top")

    # ── P&L (top-right) ───────────────────────────────────────────────────
    sign = "+" if win else ""
    ax.text(0.98, 0.92, f"{sign}₹{pnl:,.0f}",
            color=accent, fontsize=20, fontweight="bold",
            transform=ax.transAxes, va="top", ha="right")
    ax.text(0.98, 0.70, f"{sign}{pnl_pct:.1f}%", color=accent, fontsize=12,
            transform=ax.transAxes, va="top", ha="right")

    # ── Price track ───────────────────────────────────────────────────────
    pts = {"SL": sl, "Entry": entry, "LTP": ltp, "Target": target}
    vals = [v for v in pts.values() if v and v > 0]
    lo, hi = (min(vals), max(vals)) if vals else (0, 1)
    span = (hi - lo) or 1.0
    pad = span * 0.12
    lo -= pad
    hi += pad
    span = hi - lo

    def x(v):
        return 0.06 + 0.88 * ((v - lo) / span)

    y = 0.36
    ax.plot([0.06, 0.94], [y, y], color="#3a3f44", lw=3,
            transform=ax.transAxes, solid_capstyle="round")

    colors = {"SL": "#d73027", "Entry": "#9aa0a6",
              "LTP": accent, "Target": "#1a9850"}
    for label, v in pts.items():
        if not v or v <= 0:
            continue
        xp = x(v)
        ax.plot([xp], [y], "o", color=colors[label], markersize=11,
                transform=ax.transAxes, zorder=5)
        ax.text(xp, y + 0.12, label, color=colors[label], fontsize=9,
                ha="center", transform=ax.transAxes, fontweight="bold")
        ax.text(xp, y - 0.16, f"{v:,.1f}", color="white", fontsize=9,
                ha="center", transform=ax.transAxes)

    if extra:
        ax.text(0.06, 0.04, extra, color="#9aa0a6", fontsize=9,
                transform=ax.transAxes, va="bottom")

    out_path = os.path.abspath(out_path)
    fig.savefig(out_path, facecolor=bg, bbox_inches="tight", pad_inches=0.25)
    plt.close(fig)
    return out_path


if __name__ == "__main__":
    p = render_trade_card(
        "NIFTY09JUN2623300CE", "BUY", 65,
        entry=243.42, ltp=202.85, sl=170.39, target=365.13,
        pnl=-2637.0, pnl_pct=-16.7,
        extra="GTT SL+target active · trailing on")
    print("wrote", p)
