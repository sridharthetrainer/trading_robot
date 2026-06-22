from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

from ..base import DetectionContext, Direction, PatternResult
from ..pattern_scoring import blend
from . import structure_score, trend_score, volume_score
from .double_top import _dedup


def ts(df: pd.DataFrame, i: int) -> str:
    if isinstance(df.index, pd.DatetimeIndex):
        return df.index[i].isoformat()
    return str(df.iloc[i].get("timestamp", i))


def regime(context: DetectionContext) -> str:
    return str(context.structure.get("regime", "UNKNOWN"))


def confidence(config, context: DetectionContext, direction: Direction,
               quality: float, breakout: float = 75.0,
               volume: float = 50.0) -> tuple[float, Dict[str, float]]:
    want = "BULL" if direction == Direction.LONG else "BEAR"
    reg = regime(context)
    subs = {
        "pattern_quality": float(np.clip(quality, 0, 100)),
        "volume": float(np.clip(volume, 0, 100)),
        "trend": trend_score(reg, want),
        "market_structure": structure_score(reg),
        "breakout": float(np.clip(breakout, 0, 100)),
    }
    return blend(subs, config.scoring["weights"]), subs


def make_result(config, df: pd.DataFrame, context: DetectionContext, *,
                symbol: str, pattern: str, direction: Direction,
                confidence_score: float, entry: float, stop: float, target: float,
                level: float, start: int, end: int, subs: Dict[str, float],
                volume_confirmed: bool = False, meta: Dict | None = None
                ) -> PatternResult | None:
    if confidence_score < float(config.scoring["min_confidence_to_emit"]):
        return None
    if min(entry, stop, target) <= 0:
        return None
    return PatternResult(
        symbol=symbol,
        timestamp=ts(df, end),
        pattern=pattern,
        direction=direction,
        confidence=confidence_score,
        entry=float(entry),
        stop_loss=float(stop),
        target=float(target),
        breakout_level=float(level),
        volume_confirmation=volume_confirmed,
        market_structure=regime(context),
        breakout_confirmed=True,
        start_index=int(start),
        end_index=int(end),
        sub_scores=subs,
        meta=meta or {},
    )


def vscore(config, df: pd.DataFrame, start: int, end: int) -> tuple[float, bool]:
    return volume_score(
        df, start, end, end,
        int(config.volume["ma_period"]),
        float(config.volume["expansion_ratio"]),
    )


def last_results(results: List[PatternResult]) -> List[PatternResult]:
    return _dedup(results)
