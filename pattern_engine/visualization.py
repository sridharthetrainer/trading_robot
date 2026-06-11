"""
visualization.py — optional plotting for detected chart patterns.

This module is intentionally not imported by PatternEngine. Use it from notebooks,
reports, or diagnostics when a saved chart is useful.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

from .base import PatternResult, validate_ohlcv


def plot_patterns(
    df: pd.DataFrame,
    patterns: Iterable[PatternResult],
    *,
    output_path: str | Path,
    title: Optional[str] = None,
) -> Path:
    """
    Save a chart with close price, entries, stops, targets and breakout levels.

    Matplotlib is imported lazily so production scans do not incur import cost.
    """
    import matplotlib.pyplot as plt

    clean = validate_ohlcv(df)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    x = list(range(len(clean)))
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(x, clean["close"].to_numpy(float), label="close", linewidth=1.3)

    for pat in patterns:
        idx = pat.end_index if 0 <= pat.end_index < len(clean) else len(clean) - 1
        ax.scatter([idx], [pat.entry], marker="^" if pat.direction == "LONG" else "v",
                   s=80, label=f"{pat.pattern} entry")
        ax.axhline(pat.stop_loss, linestyle="--", linewidth=0.8, color="red", alpha=0.55)
        ax.axhline(pat.target, linestyle="--", linewidth=0.8, color="green", alpha=0.55)
        if pat.breakout_level:
            ax.axhline(pat.breakout_level, linestyle=":", linewidth=0.8,
                       color="orange", alpha=0.65)
        if pat.start_index >= 0:
            ax.axvspan(pat.start_index, idx, alpha=0.06)

    ax.set_title(title or "Detected Chart Patterns")
    ax.set_xlabel("bar")
    ax.set_ylabel("price")
    ax.grid(True, alpha=0.2)
    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return out
