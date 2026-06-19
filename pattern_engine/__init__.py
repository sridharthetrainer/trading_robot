"""
pattern_engine — mathematical chart-pattern recognition for the trading platform.

ISOLATED by design: this package detects patterns from an OHLCV DataFrame and
returns structured results. It does NOT import the live trading engine, place
orders, or wire itself into anything. Integrate deliberately (see INTEGRATION.md)
only AFTER backtesting a pattern's edge — detection is not an edge.

Public surface:
    from pattern_engine import PatternEngine, load_config
    eng = PatternEngine()                       # uses default config
    results = eng.detect(df, symbol="NIFTY")    # -> list[PatternResult]
"""
from __future__ import annotations

from .base import PatternResult, Direction, PatternDetector
from .config_loader import EngineConfig, load_config
from .engine import PatternEngine
from .visualization import plot_patterns

__all__ = [
    "PatternResult", "Direction", "PatternDetector",
    "EngineConfig", "load_config", "PatternEngine", "plot_patterns",
]
__version__ = "1.0.0"
