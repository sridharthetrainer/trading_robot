"""
advisory_engine.validators — LAYER 3
====================================
Multi-strategy technical validation matrix.

Every archetype evaluates CLOSED candles of the UNDERLYING on its mandated
timeframe. If any sub-condition is False — or its inputs are missing/NaN —
the trade is aborted and the exact failing rule is logged:

    "Trade Aborted: Rule [<name>] failed validation"

Missing data is a FAILURE, never a pass-through: an unverifiable signal is an
invalid signal (capital preservation first).

Direction convention:
  LONG  = BUY equity / BUY call / SELL put
  SHORT = SELL equity / BUY put / SELL call
"""

from __future__ import annotations

import logging
import math
from typing import Optional, List

import pandas as pd

from .models import (Action, Segment, StrategyTag, AdvisorySignal,
                     MarketSnapshot, RuleResult, ValidationReport)

logger = logging.getLogger("advisory.validate")


def trade_direction(sig: AdvisorySignal) -> str:
    """Map (action, segment) onto market exposure of the underlying."""
    if sig.segment is Segment.DERIVATIVE_PUT:
        return "SHORT" if sig.action is Action.BUY else "LONG"
    # equity and calls follow the action directly
    return "LONG" if sig.action is Action.BUY else "SHORT"


def _val(df: pd.DataFrame, col: str, idx: int) -> Optional[float]:
    """df[col].iloc[idx] with NaN/missing -> None."""
    if col not in df.columns or len(df) < abs(idx):
        return None
    v = df[col].iloc[idx]
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)


def _rule(results: List[RuleResult], name: str, passed: Optional[bool],
          detail: str) -> None:
    """passed=None means inputs were missing — treated as a hard FAIL."""
    ok = bool(passed)
    if passed is None:
        detail = f"INPUT MISSING — {detail}"
    results.append(RuleResult(rule=name, passed=ok, detail=detail))
    if not ok:
        logger.warning("Trade Aborted: Rule [%s] failed validation (%s)", name, detail)
    else:
        logger.info("L3 ✓ %s (%s)", name, detail)


def _fmt(*vals) -> str:
    return " ".join("None" if v is None else f"{v:.2f}" for v in vals)


# ── ARCHETYPE A: BOLLINGER_MACD — 1H trend reversal/breakdown ──────────────────

def _validate_bollinger_macd(snap: MarketSnapshot, direction: str,
                             rep: ValidationReport) -> None:
    R = rep.results
    if not snap.has_frame("intraday_1h") or len(snap.intraday_1h) < 36:
        _rule(R, "A0_data_1h", None, "need >=36 closed 1H bars for MACD/BB")
        return
    df = snap.intraday_1h
    close = _val(df, "close", -1)
    basis = _val(df, "BB_BASIS", -1)
    macd1, sig1 = _val(df, "MACD", -1), _val(df, "MACD_SIGNAL", -1)
    macd2, sig2 = _val(df, "MACD", -2), _val(df, "MACD_SIGNAL", -2)
    macd3, sig3 = _val(df, "MACD", -3), _val(df, "MACD_SIGNAL", -3)
    rsi = _val(df, "RSI_14", -1)
    inputs = (close, basis, macd1, sig1, macd2, sig2, rsi)

    if direction == "LONG":
        _rule(R, "A1_close_above_bb_basis",
              None if None in (close, basis) else close > basis,
              f"close={_fmt(close)} basis={_fmt(basis)}")
        # bullish cross on the last closed bar, or one bar earlier with the
        # cross still intact — "occurred within the trailing 2 bars"
        cross = None
        if None not in (macd1, sig1, macd2, sig2):
            cross_now = macd1 > sig1 and macd2 <= sig2
            cross_prev = (None not in (macd3, sig3)
                          and macd2 > sig2 and macd3 <= sig3 and macd1 > sig1)
            cross = cross_now or cross_prev
        _rule(R, "A2_macd_bullish_cross_2bars", cross,
              f"macd[-1]={_fmt(macd1)} sig[-1]={_fmt(sig1)} "
              f"macd[-2]={_fmt(macd2)} sig[-2]={_fmt(sig2)}")
        _rule(R, "A3_rsi_above_50", None if rsi is None else rsi > 50,
              f"rsi={_fmt(rsi)}")
    else:
        _rule(R, "A1_close_below_bb_basis",
              None if None in (close, basis) else close < basis,
              f"close={_fmt(close)} basis={_fmt(basis)}")
        cross = None
        if None not in (macd1, sig1, macd2, sig2):
            cross_now = macd1 < sig1 and macd2 >= sig2
            cross_prev = (None not in (macd3, sig3)
                          and macd2 < sig2 and macd3 >= sig3 and macd1 < sig1)
            cross = cross_now or cross_prev
        _rule(R, "A2_macd_bearish_cross_2bars", cross,
              f"macd[-1]={_fmt(macd1)} sig[-1]={_fmt(sig1)} "
              f"macd[-2]={_fmt(macd2)} sig[-2]={_fmt(sig2)}")
        _rule(R, "A3_rsi_below_50", None if rsi is None else rsi < 50,
              f"rsi={_fmt(rsi)}")
    if None in inputs:
        logger.debug("A archetype had missing inputs: %s", inputs)


# ── ARCHETYPE B: VWAP_OI — 15M intraday options momentum squeeze ───────────────

VWAP_DETACH_PCT = 0.005      # 0.5% clear of VWAP
VOL_SPIKE_MULT = 2.5         # vs 20-bar SMA of volume


def _validate_vwap_oi(snap: MarketSnapshot, direction: str, sig: AdvisorySignal,
                      rep: ValidationReport) -> None:
    R = rep.results
    if not snap.has_frame("intraday_15m") or len(snap.intraday_15m) < 22:
        _rule(R, "B0_data_15m", None, "need >=22 closed 15M bars")
        return
    df = snap.intraday_15m
    close = _val(df, "close", -1)
    vwap = _val(df, "VWAP", -1)
    vol = _val(df, "volume", -1)
    vol_sma20 = df["volume"].iloc[-21:-1].mean() if len(df) >= 21 else None
    if vol_sma20 is not None and math.isnan(vol_sma20):
        vol_sma20 = None

    if None in (close, vwap):
        _rule(R, "B1_vwap_detachment", None, "close/VWAP unavailable")
    elif direction == "LONG":
        _rule(R, "B1_vwap_detachment", close > vwap * (1 + VWAP_DETACH_PCT),
              f"close={close:.2f} needs > vwap*1.005={vwap * 1.005:.2f}")
    else:
        _rule(R, "B1_vwap_detachment", close < vwap * (1 - VWAP_DETACH_PCT),
              f"close={close:.2f} needs < vwap*0.995={vwap * 0.995:.2f}")

    _rule(R, "B2_volume_acceleration",
          None if None in (vol, vol_sma20) or vol_sma20 <= 0
          else vol > vol_sma20 * VOL_SPIKE_MULT,
          f"vol={_fmt(vol)} needs > 2.5x sma20={_fmt(vol_sma20)}")

    if sig.is_derivative:
        oi = snap.oi_series
        if not oi or len(oi) < 4:
            _rule(R, "B3_oi_squeeze", None,
                  f"need >=4 OI points, have {len(oi) if oi else 0} "
                  "(wire an oi_provider, e.g. oi_tracker)")
        else:
            _rule(R, "B3_oi_squeeze", oi[-1] < oi[-4],
                  f"OI now={oi[-1]:.0f} vs 3 bars ago={oi[-4]:.0f} "
                  "(must be unwinding)")


# ── ARCHETYPE C: TRENDLINE_EMA — daily structural breakout ─────────────────────

RSI_BAND = (55.0, 68.0)


def _validate_trendline_ema(snap: MarketSnapshot, direction: str,
                            rep: ValidationReport) -> None:
    R = rep.results
    if direction != "LONG":
        # spec defines C for breakouts only; a short "breakdown" advisory has no
        # validated rule set here — abort rather than invent one
        _rule(R, "C0_direction", False,
              "TRENDLINE_EMA archetype validates LONG breakouts only")
        return
    if not snap.has_frame("daily") or len(snap.daily) < 30:
        _rule(R, "C0_data_daily", None, "need >=30 closed daily bars")
        return
    df = snap.daily
    close = _val(df, "close", -1)
    ema5, ema20 = _val(df, "EMA_5", -1), _val(df, "EMA_20", -1)
    rsi = _val(df, "RSI_14", -1)
    window = df["close"].iloc[-15:-1]          # the 14 sessions before today
    prior_high = float(window.max()) if len(window) == 14 else None

    _rule(R, "C1_consolidation_breakout",
          None if None in (close, prior_high) else close > prior_high,
          f"close={_fmt(close)} needs > max(close[-15:-1])={_fmt(prior_high)}")
    _rule(R, "C2_ema_stack",
          None if None in (close, ema5, ema20) else close > ema5 > ema20,
          f"close={_fmt(close)} ema5={_fmt(ema5)} ema20={_fmt(ema20)}")
    _rule(R, "C3_rsi_band",
          None if rsi is None else RSI_BAND[0] <= rsi <= RSI_BAND[1],
          f"rsi={_fmt(rsi)} needs in [{RSI_BAND[0]}, {RSI_BAND[1]}]")


# ── Matrix entry point ─────────────────────────────────────────────────────────

class ValidationMatrix:
    """LAYER 3 — routes a signal+snapshot to its archetype and runs the rules."""

    def validate(self, sig: AdvisorySignal,
                 snap: MarketSnapshot) -> ValidationReport:
        direction = trade_direction(sig)
        rep = ValidationReport(strategy_tag=sig.strategy_tag, direction=direction)
        logger.info("L3 ▶ validating %s %s via %s on %s",
                    direction, snap.symbol, sig.strategy_tag.value,
                    {"BOLLINGER_MACD": "1H", "VWAP_OI": "15M",
                     "TRENDLINE_EMA": "1D"}.get(sig.strategy_tag.value, "?"))

        if sig.strategy_tag is StrategyTag.BOLLINGER_MACD:
            _validate_bollinger_macd(snap, direction, rep)
        elif sig.strategy_tag is StrategyTag.VWAP_OI:
            _validate_vwap_oi(snap, direction, sig, rep)
        elif sig.strategy_tag is StrategyTag.TRENDLINE_EMA:
            _validate_trendline_ema(snap, direction, rep)
        else:
            _rule(rep.results, "ROUTE_strategy_tag", False,
                  "no archetype matched the advisory rationale — refusing to "
                  "trade an unclassified signal")

        verdict = "PASSED" if rep.passed else (
            f"ABORTED at [{rep.first_failure.rule}]" if rep.first_failure
            else "ABORTED (no rules ran)")
        logger.info("L3 ◀ %s %s: %s", snap.symbol, sig.strategy_tag.value, verdict)
        return rep
