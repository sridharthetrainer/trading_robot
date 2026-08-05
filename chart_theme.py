"""
chart_theme.py — shared visual vocabulary for every matplotlib PNG this
system sends to Telegram (2026-07-27, cross-AI-reviewed).

Before this module, ~6 chart-generating files each hardcoded their own dark
theme, picked independently over months: at least 4 different near-black
backgrounds (#0d1117, #0e1117, #08111f + panel variants) and 3 different
bullish/bearish or win/loss color pairs, none but one validated for
contrast/colorblind-safety. A user scrolling either Telegram channel saw
several subtly different "black" backgrounds and several different
conventions for what green/red actually meant.

This is ONE shared vocabulary for presentation only -- no chart logic, no
data, no business rules live here. `#0d1117`/`#161b22` and the
`#0ca30c`/`#d03b3b` bullish/bearish pair are kept as the canonical anchor
because they're this codebase's only combination actually run through an
accessibility validator (CVD-safe adjacent-pair separation, contrast vs.
this exact dark surface) -- not merely because they're newest.

Semantic constants (BULLISH/BEARISH) are named for meaning, not hue, so a
future need to distinguish "bullish" from "profit" doesn't require
repainting every chart that currently conflates them.
"""
from __future__ import annotations

from typing import Optional, Sequence

# ── Surfaces ─────────────────────────────────────────────────────────────────
BG    = "#0d1117"   # figure background
PANEL = "#161b22"   # axes/card background
GRID  = "#30363d"   # gridlines, spines, dividers

# ── Text hierarchy (preserved, not flattened -- 3 tiers were already
# implicit across these modules: a strong title color, a normal label/value
# color, and a fainter metadata color) ───────────────────────────────────────
TEXT_PRIMARY   = "#e6edf3"   # titles, headline numbers
TEXT_SECONDARY = "#c9d1d9"   # normal labels/values
TEXT_MUTED     = "#8b949e"   # timestamps, footnotes, least-emphasis metadata

# ── Semantic accents ─────────────────────────────────────────────────────────
# The only pair in this codebase actually run through a CVD/contrast
# validator (see option_oi_chart.py's history) -- reused everywhere a
# bullish/bearish, buildup/unwinding, win/loss, or selected/blocked meaning
# is being shown, rather than each module picking its own green/red.
BULLISH = "#0ca30c"
BEARISH = "#d03b3b"
WARNING = "#ffd43b"
INFO    = "#4dabf7"

# Fixed order for multi-series charts -- series N always gets the same
# color regardless of how many series are plotted or which get filtered out.
CATEGORICAL = ("#4dabf7", "#ffd43b", "#63e6be", "#da77f2", "#ff922b")

# CE (call) vs PE (put) is an IDENTITY distinction, not a bullish/bearish one --
# CE open interest rising isn't inherently bad news nor PE inherently good (that
# depends on the relative change, which callers compute separately). Give it a
# fixed categorical pair rather than reusing BULLISH/BEARISH, which would imply
# a polarity that isn't actually there. CALL/PUT are the first two CATEGORICAL
# colors, kept as named constants since this pairing recurs across every
# option-chain chart in the codebase. _MUTED variants (same hue, lighter) are
# for a paired sub-chart (e.g. change-in-OI) that needs to visually pair with
# the primary CE/PE lines without competing with them for attention.
# Matches the CE=red/PE=blue convention already on screen in every prior OI
# chart -- centralizing the existing appearance, not changing it.
CALL = "#ff6b6b"
PUT  = CATEGORICAL[0]
CALL_MUTED = "#ffa8a8"
PUT_MUTED  = "#91caff"


def apply_theme(fig, axes=None) -> None:
    """Apply the shared surface/grid/tick styling to a figure and its axes
    in one call. Deliberately does NOT touch data colors, legends, titles'
    text (callers still set their own title strings), or layout -- those
    stay module-local since they vary by chart type. No global
    plt.style.use()/rcParams mutation, so this stays an explicit,
    traceable per-module dependency rather than hidden coupling across a
    470-file codebase.

    axes: a single Axes, a flat iterable of Axes (list/tuple/`axes.flat`),
    the raw 2D ndarray plt.subplots(nrows, ncols) returns directly (each
    "item" is itself a row of Axes, not an Axes -- handled by flattening one
    extra level), or None (defaults to fig.get_axes(), covering subplot
    grids created before this call). Checks for single-Axes-ness by
    duck-typing (hasattr set_facecolor) rather than iterable type, since
    numpy's flatiter/ndarray aren't list/tuple instances."""
    fig.patch.set_facecolor(BG)
    if axes is None:
        axes = fig.get_axes()
    if hasattr(axes, "set_facecolor"):
        target_axes = [axes]
    else:
        target_axes = []
        for item in axes:
            if hasattr(item, "set_facecolor"):
                target_axes.append(item)
            else:
                target_axes.extend(item)   # a 2D array's row -- one more level
    for ax in target_axes:
        ax.set_facecolor(PANEL)
        ax.tick_params(colors=TEXT_MUTED)
        for spine in ax.spines.values():
            spine.set_color(GRID)
        ax.grid(True, color=GRID, alpha=0.4, linewidth=0.7)


def style_title(ax_or_fig, text: Optional[str] = None) -> None:
    """Apply TEXT_PRIMARY to an Axes' title or a Figure's suptitle, without
    dictating font size/weight (callers already vary these deliberately by
    chart prominence)."""
    if hasattr(ax_or_fig, "set_title") and text is not None:
        ax_or_fig.set_title(text, color=TEXT_PRIMARY)
    elif hasattr(ax_or_fig, "suptitle") and text is not None:
        ax_or_fig.suptitle(text, color=TEXT_PRIMARY)


def categorical(n: int) -> Sequence[str]:
    """First n colors of the fixed categorical order, cycling if n exceeds
    the palette (kept simple -- this codebase's charts top out at 5 series)."""
    if n <= len(CATEGORICAL):
        return CATEGORICAL[:n]
    return tuple(CATEGORICAL[i % len(CATEGORICAL)] for i in range(n))
