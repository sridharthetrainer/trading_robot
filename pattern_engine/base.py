"""
base.py — shared types and the PatternDetector contract.

No magic numbers live here; all thresholds come from EngineConfig (config files).
"""
from __future__ import annotations

import abc
import logging
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger("pattern_engine")

REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume")


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"


@dataclass
class PatternResult:
    """One detected pattern, in the platform's standard shape."""
    symbol: str
    timestamp: str                 # ISO time of the confirming bar
    pattern: str
    direction: Direction
    confidence: float              # 0-100 (final blended score)
    entry: float
    stop_loss: float
    target: float
    breakout_level: float = 0.0
    volume_confirmation: bool = False
    market_structure: str = ""
    breakout_confirmed: bool = False
    # provenance / debugging
    start_index: int = -1
    end_index: int = -1
    sub_scores: Dict[str, float] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def risk_reward(self) -> float:
        risk = abs(self.entry - self.stop_loss)
        reward = abs(self.target - self.entry)
        return round(reward / risk, 2) if risk > 0 else 0.0

    def to_json(self) -> Dict[str, Any]:
        d = asdict(self)
        d["direction"] = self.direction.value
        d["risk_reward"] = self.risk_reward
        d["confidence"] = round(float(self.confidence), 1)
        for k in ("entry", "stop_loss", "target", "breakout_level"):
            d[k] = round(float(d[k]), 2)
        return d


def validate_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Return a clean, lowercase-columned OHLCV frame or raise ValueError."""
    if df is None or len(df) == 0:
        raise ValueError("empty dataframe")
    out = df.copy()
    out.columns = [str(c).lower() for c in out.columns]
    missing = [c for c in REQUIRED_COLUMNS if c not in out.columns]
    if missing:
        raise ValueError(f"OHLCV missing columns: {missing}")
    for c in REQUIRED_COLUMNS:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    # Keep the original index intact. Live candles often arrive with a
    # DatetimeIndex, and detectors use it to report the true confirmation time.
    # Positional access still works through iloc / numeric arrays.
    out = out.dropna(subset=list(REQUIRED_COLUMNS))
    if len(out) == 0:
        raise ValueError("no valid rows after cleaning")
    return out


class PatternDetector(abc.ABC):
    """Contract every pattern detector implements.

    Detectors are pure: given a validated OHLCV frame + precomputed `context`
    (pivots, structure, etc.), return zero or more PatternResult. They never
    place orders or mutate global state.
    """

    name: str = "abstract"

    def __init__(self, config: "EngineConfig") -> None:  # noqa: F821
        self.config = config

    @abc.abstractmethod
    def detect(self, df: pd.DataFrame, context: "DetectionContext",
               symbol: str) -> List[PatternResult]:
        ...


@dataclass
class DetectionContext:
    """Precomputed, shared inputs passed to every detector (computed once)."""
    swing_highs: List[int] = field(default_factory=list)   # bar indices
    swing_lows: List[int] = field(default_factory=list)
    structure: Dict[str, Any] = field(default_factory=dict)
    atr: Optional[pd.Series] = None
    vol_ma: Optional[pd.Series] = None
