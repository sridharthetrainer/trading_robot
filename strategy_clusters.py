"""
strategy_clusters.py — de-correlate confluence voting.

Counting raw strategy votes double-counts correlated signals: in an uptrend EMA,
Supertrend, Ichimoku, Weinstein and Heikin-Ashi all fire bullish — that looks
like 5 confirmations but is really ONE factor (trend) expressed 5 ways.

We map each strategy to a market FACTOR and count distinct factors that agree.
So 5 trend strategies → 1 effective vote, not 5. (A correlation-matrix /
1-over-correlation weighting is the fuller solution but needs per-strategy output
history; clustering is the robust version available today.)
"""

from __future__ import annotations

from typing import Iterable, List

# Order matters: the FIRST matching factor wins, so put the more specific
# keywords (pattern, breakout, mean-reversion) ahead of the broad ones.
_FACTOR_KEYWORDS = [
    ("PATTERN",        ["chart_pattern", "wedge", "triangle", "head_and_shoulder",
                         "head_shoulder", "double_top", "double_bottom", "cup",
                         "flag", "pennant", "elliott", "harmonic", "candlestick",
                         "candle"]),
    ("BREAKOUT",       ["orb", "breakout", "vp_break", "gap_and_go", "gap_go",
                         "donchian", "range_break", "opening_range", "hero_zero"]),
    ("MEAN_REVERSION", ["mean_rev", "reversion", "vwap_rev", "rsi_div", "fade",
                        "revert", "oversold", "overbought", "gap_fill",
                        "bb_percentb", "ehlers", "fisher", "williams",
                        "oops", "vix_extreme"]),
    ("MOMENTUM",       ["rsi", "stoch", "connors", "macd", "td_seq", "td_sequential",
                        "momentum", "roc", "cci", "awesome", "scalp", "scalping",
                        "canslim", "waddah", "alligator", "kst", "elder", "uo",
                        "aroon"]),
    ("TREND",          ["ema", "supertrend", "ichimoku", "weinstein", "heikin",
                        "holy_grail", "ma_cross", "adx", "trend", "sar", "dema",
                        "tema", "ribbon", "kama"]),
    ("VOLATILITY",     ["bollinger", "keltner", "squeeze", "atr", "volatility",
                        "band", "vix_fix", "vix"]),
    ("FLOW",           ["order_flow", "chaikin", "obv", "mfi", "volume",
                        "accumulation", "delta", "vrvp", "vp_zone", "vsa",
                        "failed_auction"]),
    ("STRUCTURE",      ["price_structure", "structure", "vwap", "cpr", "pivot",
                        "support", "resist", "fibonacci", "fib", "supply", "demand",
                        "order_block", "liquidity_sweep", "vpoc", "smc"]),
    ("RELATIVE_VALUE", ["stat_arb", "pairs"]),
    ("EVENT",          ["event_driven", "event_filter"]),
]


def factor_of(strategy: str) -> str:
    """Return the market factor for a strategy name."""
    s = str(strategy).lower()
    for factor, kws in _FACTOR_KEYWORDS:
        if any(k in s for k in kws):
            return factor
    return "OTHER:" + s  # unknown → its own factor (counts once, never merged)


def effective_confluence(strategies: Iterable[str]) -> int:
    """Number of DISTINCT factors among the agreeing strategies."""
    return len({factor_of(s) for s in strategies if s})


def factors_present(strategies: Iterable[str]) -> List[str]:
    """Sorted distinct factors (for logging)."""
    return sorted({factor_of(s) for s in strategies if s})
