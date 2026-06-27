"""
engine.py — PatternEngine orchestrator.

Builds the shared DetectionContext once (pivots, market structure, ATR, volume
MA), then runs every registered detector and returns a flat, confidence-sorted
list of PatternResult. Detectors are registered here as they're implemented, so
the engine grows additively without touching existing code.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Type

import pandas as pd

from .base import (PatternDetector, PatternResult, DetectionContext,
                   validate_ohlcv)
from .config_loader import EngineConfig, load_config
from .pivot_engine import find_swings, swing_frame
from .market_structure import classify_structure

logger = logging.getLogger("pattern_engine")

# ── detector registry (extend as patterns are added + validated) ──────────────
from .patterns.double_top import DoubleTopDetector
from .patterns.double_bottom import DoubleBottomDetector
from .patterns.head_shoulders import (HeadShouldersDetector,
                                      InverseHeadShouldersDetector)
from .patterns.triangles import TriangleDetector
from .patterns.wedges import WedgeDetector
from .patterns.flags import FlagDetector
from .patterns.triple_top import TripleTopDetector
from .patterns.triple_bottom import TripleBottomDetector
from .patterns.rectangle import RectangleDetector, HorizontalRangeDetector
from .patterns.channels import AscendingChannelDetector, DescendingChannelDetector
from .patterns.cup_handle import CupHandleDetector
from .patterns.rounding import RoundingTopDetector, RoundingBottomDetector
from .patterns.diamond import DiamondTopDetector, DiamondBottomDetector
from .patterns.broadening import BroadeningDetector
from .patterns.smart_money import SmartMoneyDetector

_DETECTORS: List[Type[PatternDetector]] = [
    DoubleTopDetector,
    DoubleBottomDetector,
    TripleTopDetector,
    TripleBottomDetector,
    HeadShouldersDetector,
    InverseHeadShouldersDetector,
    TriangleDetector,
    WedgeDetector,
    FlagDetector,
    RectangleDetector,
    HorizontalRangeDetector,
    AscendingChannelDetector,
    DescendingChannelDetector,
    CupHandleDetector,
    RoundingTopDetector,
    RoundingBottomDetector,
    DiamondTopDetector,
    DiamondBottomDetector,
    BroadeningDetector,
    SmartMoneyDetector,
]


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=1).mean()


class PatternEngine:
    def __init__(self, config: Optional[EngineConfig] = None) -> None:
        self.config = config or load_config()
        self.detectors: List[PatternDetector] = [d(self.config) for d in _DETECTORS]

    def build_context(self, df: pd.DataFrame) -> DetectionContext:
        piv = self.config.pivots
        sh, sl = find_swings(df, int(piv["left_bars"]), int(piv["right_bars"]),
                             int(piv["min_separation_bars"]))
        swings = swing_frame(df, sh, sl)
        structure = classify_structure(
            swings, int(self.config.market_structure["trend_min_swings"]))
        return DetectionContext(
            swing_highs=sh, swing_lows=sl, structure=structure,
            atr=_atr(df, int(self.config.breakout["atr_period"])),
            vol_ma=df["volume"].rolling(int(self.config.volume["ma_period"]),
                                        min_periods=1).mean(),
        )

    def detect(self, df: pd.DataFrame, symbol: str = "") -> List[PatternResult]:
        clean = validate_ohlcv(df)
        ctx = self.build_context(clean)
        out: List[PatternResult] = []
        for det in self.detectors:
            try:
                out.extend(det.detect(clean, ctx, symbol))
            except Exception as exc:
                logger.exception("detector %s failed: %s", det.name, exc)
        return sorted(out, key=lambda r: r.confidence, reverse=True)

    def detect_best(
        self,
        df: pd.DataFrame,
        symbol: str = "",
        min_confidence: float = 55.0,
        min_risk_reward: float = 1.2,
        max_age_bars: int = 8,
        require_confirmed: bool = True,
    ) -> Optional[PatternResult]:
        """
        Return the strongest actionable recent pattern.

        `detect()` intentionally returns all historical detections inside the
        supplied frame. Live signal generation usually wants only a fresh,
        confirmed pattern near the latest candle, with at least acceptable R:R.
        """
        results = self.detect(df, symbol)
        if not results:
            return None

        latest_pos = len(validate_ohlcv(df)) - 1
        eligible: List[PatternResult] = []
        for result in results:
            if require_confirmed and not result.breakout_confirmed:
                continue
            if result.confidence < min_confidence:
                continue
            if result.risk_reward < min_risk_reward:
                continue
            if result.end_index < 0 or latest_pos - result.end_index > max_age_bars:
                continue
            eligible.append(result)

        if not eligible:
            return None
        return max(
            eligible,
            key=lambda r: (
                float(r.confidence),
                float(r.risk_reward),
                -max(0, latest_pos - r.end_index),
            ),
        )

    def detect_json(self, df: pd.DataFrame, symbol: str = "") -> List[Dict]:
        return [r.to_json() for r in self.detect(df, symbol)]
