"""
option_execution_quality.py

Selected-strike execution quality checks for option entries.

The existing option_quality module validates the chain/ATM context. This module
scores the actual contract selected for execution so a trade is not opened just
because the overall chain looks healthy.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class OptionExecutionQuality:
    ok: bool
    score: float
    reason: str
    hard_blocks: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    oi: float = 0.0
    volume: float = 0.0
    spread_pct: Optional[float] = None
    premium: float = 0.0
    dte: int = 0
    strike_type: str = ""
    trap_score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _i(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except Exception:
        return default


def _first_float(row: Dict[str, Any], *keys: str) -> float:
    for key in keys:
        value = _f(row.get(key), 0.0)
        if value > 0:
            return value
    return 0.0


def _spread(row: Dict[str, Any]) -> Optional[float]:
    spread = row.get("spread_pct")
    if spread is not None:
        try:
            spread_f = float(spread)
            if spread_f >= 0:
                return spread_f
        except Exception:
            pass
    bid = _first_float(row, "bid", "bid_price", "bidPrice", "bestBid", "bidprice")
    ask = _first_float(row, "ask", "ask_price", "askPrice", "bestAsk", "askprice")
    if bid <= 0 or ask <= 0 or ask < bid:
        return None
    mid = (bid + ask) / 2.0
    return None if mid <= 0 else (ask - bid) / mid


def evaluate_selected_option_execution(
    selected: Dict[str, Any],
    *,
    signal: Optional[Dict[str, Any]] = None,
    min_oi: float = 100.0,
    min_volume: float = 100.0,
    max_spread_pct: float = 0.20,
    min_premium: float = 1.0,
    max_premium: float = 5000.0,
    require_liquidity_fields: bool = False,
    trap_option_move_multiple: float = 3.0,
    trap_min_underlying_move_pct: float = 0.08,
) -> OptionExecutionQuality:
    selected = selected if isinstance(selected, dict) else {}
    signal = signal if isinstance(signal, dict) else {}
    premium = _first_float(selected, "premium", "entry_price", "option_premium")
    oi = _first_float(selected, "oi", "openInterest", "open_interest")
    volume = _first_float(selected, "volume", "totalTradedVolume", "traded_volume")
    spread_pct = _spread(selected)
    dte = _i(selected.get("dte"), 0)
    strike_type = str(selected.get("strike_type") or "").upper()

    hard: List[str] = []
    warn: List[str] = []
    score = 100.0

    if premium <= 0:
        hard.append("premium_missing")
    elif premium < min_premium:
        hard.append("premium_too_low")
    elif premium > max_premium:
        hard.append("premium_too_high")

    if oi <= 0:
        (hard if require_liquidity_fields else warn).append("oi_missing")
        score -= 8.0
    elif oi < min_oi:
        hard.append("oi_too_low")
    elif oi < min_oi * 3:
        warn.append("oi_thin")
        score -= 8.0

    if volume <= 0:
        (hard if require_liquidity_fields else warn).append("volume_missing")
        score -= 8.0
    elif volume < min_volume:
        hard.append("volume_too_low")
    elif volume < min_volume * 3:
        warn.append("volume_thin")
        score -= 8.0

    if spread_pct is None:
        warn.append("spread_unknown")
        score -= 6.0
    elif spread_pct > max_spread_pct:
        hard.append("spread_too_wide")
    elif spread_pct > max_spread_pct * 0.60:
        warn.append("spread_elevated")
        score -= 10.0

    if dte <= 0:
        warn.append("expiry_gamma_risk")
        score -= 6.0
    if strike_type and strike_type not in {"ATM", "ITM", "OTM", "SELECTED"} and "OTM" in strike_type:
        score -= 3.0

    option_move = abs(_f(signal.get("option_premium_change_pct"), 0.0))
    underlying_move = abs(_f(signal.get("underlying_move_pct"), _f(signal.get("price_change_pct"), 0.0)))
    trap_score = 0.0
    if option_move > 0 and underlying_move >= 0:
        trap_score = option_move / max(underlying_move, 0.01)
        if trap_score >= trap_option_move_multiple and underlying_move < trap_min_underlying_move_pct:
            hard.append("option_spike_without_underlying_confirmation")
        elif trap_score >= trap_option_move_multiple:
            warn.append("option_spike_risk")
            score -= 12.0

    score = max(0.0, min(100.0, score))
    return OptionExecutionQuality(
        ok=not hard,
        score=round(score, 2),
        reason="ok" if not hard else ",".join(hard),
        hard_blocks=hard,
        warnings=warn,
        oi=oi,
        volume=volume,
        spread_pct=None if spread_pct is None else round(float(spread_pct), 4),
        premium=premium,
        dte=dte,
        strike_type=strike_type,
        trap_score=round(trap_score, 4),
    )
