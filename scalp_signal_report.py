"""Render an evidence-first image for an option scalp signal.

The chart uses the underlying OHLCV bars because OPTION_SCALP currently chooses
an ATM strike without fetching a verified expiry/premium quote. This distinction
is printed on the image so underlying levels are never mistaken for option-price
levels.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

import chart_theme as ct


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def render_scalp_signal_report(
    df,
    payload: Dict[str, Any],
    output_dir: Optional[str] = None,
) -> str:
    """Create a Telegram-ready PNG and return its absolute path."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    if df is None or len(df) < 3:
        raise ValueError("at least 3 OHLC bars are required")
    d = df.copy().tail(60)
    d.columns = [str(c).lower() for c in d.columns]
    required = {"open", "high", "low", "close"}
    if not required.issubset(d.columns):
        raise ValueError(f"missing OHLC columns: {sorted(required - set(d.columns))}")

    evidence = ((payload.get("metadata") or {}).get("scalp_evidence") or {})
    option_context = ((payload.get("metadata") or {}).get("option_context") or {})
    selected = payload.get("selected") or {}
    side = str(payload.get("side") or evidence.get("side") or "").upper()
    symbol = str(payload.get("symbol") or "?").upper()
    option_type = str(selected.get("option_type") or evidence.get("option_type") or "")
    strike = _f(selected.get("strike"))
    accent = ct.BULLISH if side == "BUY" else ct.BEARISH

    close = d["close"].astype(float)
    high = d["high"].astype(float)
    low = d["low"].astype(float)
    opened = d["open"].astype(float)
    volume = d.get("volume", close * 0).astype(float)
    ema9 = close.ewm(span=9, adjust=False).mean()
    ema21 = close.ewm(span=21, adjust=False).mean()
    typical = (high + low + close) / 3.0
    vwap = ((typical * volume).cumsum() / volume.cumsum().replace(0, np.nan)
            if float(volume.sum()) > 0 else close.expanding().mean())
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14, min_periods=3).mean()
    loss = (-delta.clip(upper=0)).rolling(14, min_periods=3).mean()
    rsi = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    rsi = rsi.mask((loss == 0) & (gain > 0), 100.0)
    rsi = rsi.mask((loss == 0) & (gain == 0), 50.0).fillna(50.0)

    fig = plt.figure(figsize=(12, 10), dpi=130)
    grid = fig.add_gridspec(4, 1, height_ratios=[3.2, 1.0, 1.0, 1.45])
    axes = [fig.add_subplot(grid[i]) for i in range(4)]
    ct.apply_theme(fig, axes)
    x = np.arange(len(d))
    candle_colors = np.where(close >= opened, ct.BULLISH, ct.BEARISH)

    axes[0].vlines(x, low, high, color=candle_colors, linewidth=0.8)
    axes[0].bar(x, close - opened, bottom=opened, color=candle_colors, width=0.65)
    axes[0].plot(x, ema9, color=ct.WARNING, label="EMA 9", linewidth=1.2)
    axes[0].plot(x, ema21, color=ct.INFO, label="EMA 21", linewidth=1.2)
    axes[0].plot(x, vwap, color=ct.CATEGORICAL[3], label="VWAP", linewidth=1.1)
    levels = (
        ("Trigger", evidence.get("trigger"), ct.TEXT_PRIMARY, "--"),
        ("Stop", evidence.get("stop"), ct.BEARISH, ":"),
        ("T1", evidence.get("target_1"), ct.BULLISH, "--"),
        ("T2", evidence.get("target_2"), ct.BULLISH, ":"),
    )
    for label, value, color, style in levels:
        value = _f(value)
        if value > 0:
            axes[0].axhline(value, color=color, linestyle=style, linewidth=1,
                            label=f"{label} {value:,.1f}")
    axes[0].legend(loc="upper left", ncol=4, fontsize=8)
    axes[0].set_title(
        f"{symbol} {side} → ATM {strike:.0f}{option_type}  |  UNDERLYING SETUP",
        color=ct.TEXT_PRIMARY, weight="bold",
    )

    axes[1].bar(x, volume, color=candle_colors, alpha=0.8)
    axes[1].set_title(
        f"VOLUME  |  last/prior {_f(evidence.get('volume_ratio')):.2f}×",
        color=ct.TEXT_PRIMARY, fontsize=10,
    )
    axes[2].plot(x, rsi, color=ct.WARNING, linewidth=1.3)
    axes[2].axhline(70, color=ct.BEARISH, linestyle="--", linewidth=0.8)
    axes[2].axhline(50, color=ct.TEXT_MUTED, linestyle=":", linewidth=0.8)
    axes[2].axhline(30, color=ct.BULLISH, linestyle="--", linewidth=0.8)
    axes[2].set_ylim(0, 100)
    axes[2].set_title(
        f"MOMENTUM  |  RSI {_f(evidence.get('rsi')):.1f}  ·  "
        f"range expansion {_f(evidence.get('range_expansion')):.2f}×",
        color=ct.TEXT_PRIMARY, fontsize=10,
    )

    axes[3].axis("off")
    aligned = (
        _f(evidence.get("ema9")) > _f(evidence.get("ema21"))
        if side == "BUY" else
        _f(evidence.get("ema9")) < _f(evidence.get("ema21"))
    )
    evidence_lines = [
        f"SETUP SCORE  {_f(payload.get('setup_score')):.1f}/10",
        f"Breakout trigger  {_f(evidence.get('trigger')):,.2f}    "
        f"Last close  {_f(payload.get('spot')):,.2f}",
        f"Invalidation  {_f(evidence.get('stop')):,.2f}    "
        f"Targets  {_f(evidence.get('target_1')):,.2f} / "
        f"{_f(evidence.get('target_2')):,.2f}    R:R  1.0 / 1.5",
        f"VWAP  {_f(evidence.get('vwap')):,.2f}    "
        f"EMA9/21  {_f(evidence.get('ema9')):,.2f} / "
        f"{_f(evidence.get('ema21')):,.2f}    "
        f"trend alignment  {'YES' if aligned else 'NO'}",
        f"Option context  PCR {_f(option_context.get('pcr')):.2f}    "
        f"Max pain {_f(option_context.get('max_pain')):,.0f}    "
        f"source {str(option_context.get('source') or 'unavailable')[:24]}    "
        f"{'STALE' if option_context.get('stale', True) else 'fresh'}",
    ]
    axes[3].text(0.01, 0.93, "\n".join(evidence_lines), transform=axes[3].transAxes,
                 color=ct.TEXT_SECONDARY, fontsize=11, va="top", linespacing=1.6)
    axes[3].text(
        0.01, 0.08,
        "IMPORTANT: Levels above are for the UNDERLYING index. "
        "Expiry and option premium are not verified on this scanner path; "
        "confirm live spread/liquidity before any trade.",
        transform=axes[3].transAxes, color=ct.WARNING, fontsize=9.5, va="bottom",
        wrap=True,
    )
    fig.suptitle("OPTION SCALP — SUPPORTING EVIDENCE", color=accent,
                 fontsize=16, weight="bold")

    out = Path(output_dir or tempfile.gettempdir())
    out.mkdir(parents=True, exist_ok=True)
    source_id = str(payload.get("source_id") or "latest").replace("/", "_")
    path = (out / f"scalp_signal_{source_id}.png").resolve()
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(path, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    return str(path)
