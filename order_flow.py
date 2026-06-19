"""
order_flow.py — Order Flow Analysis (without tick data)

Approximates order flow from OHLCV 1-min data.
When tick data is available (TrueData), plugs in real bid/ask volumes.

Metrics computed:
  1. Volume Delta (buy vol - sell vol per bar)
  2. Cumulative Delta (running delta trend)
  3. Delta Divergence (price up but delta down = reversal signal)
  4. Buying/Selling Pressure Index
  5. Absorption (large sell with price stable = strong support)
"""
from __future__ import annotations
import logging
import numpy as np
import pandas as pd
from typing import Dict, Optional

logger = logging.getLogger(__name__)


def _estimate_buy_sell_volume(df: pd.DataFrame) -> pd.DataFrame:
    """
    Estimate buy/sell volume from OHLCV (Tick Rule approximation).
    
    Assumption:
      Close near High → more buying pressure
      Close near Low  → more selling pressure
      Formula: buy_vol = vol × (close-low)/(high-low+1e-9)
    """
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]
    hl_range = (df["high"] - df["low"]).clip(lower=0.01)
    buy_ratio = (df["close"] - df["low"]) / hl_range
    df["buy_vol"]  = df["volume"] * buy_ratio
    df["sell_vol"] = df["volume"] * (1 - buy_ratio)
    df["delta"]    = df["buy_vol"] - df["sell_vol"]
    df["cum_delta"] = df["delta"].cumsum()
    return df


def compute_order_flow(df: pd.DataFrame, lookback: int = 20) -> Dict:
    """
    Full order flow analysis.
    
    Returns:
      delta:           current bar delta (buy-sell volume)
      cum_delta:       cumulative delta trend
      divergence:      True if price and delta disagree (reversal signal)
      pressure:        "BUYING" / "SELLING" / "NEUTRAL"
      absorption:      True if large selling absorbed (bullish)
      score_modifier:  float to add to signal score
    """
    try:
        df_c = _estimate_buy_sell_volume(df)
        if len(df_c) < lookback:
            return {"pressure":"NEUTRAL","score_modifier":0.0}

        recent = df_c.tail(lookback)

        # Current delta
        cur_delta   = float(recent["delta"].iloc[-1])
        cum_delta   = float(recent["cum_delta"].iloc[-1])
        avg_vol     = float(recent["volume"].mean())

        # Price vs delta divergence
        price_trend = recent["close"].iloc[-1] - recent["close"].iloc[-lookback//2]
        delta_trend = recent["cum_delta"].iloc[-1] - recent["cum_delta"].iloc[-lookback//2]
        divergence  = (price_trend > 0 and delta_trend < 0) or \
                      (price_trend < 0 and delta_trend > 0)

        # Buying/selling pressure
        buy_ratio  = float(recent["buy_vol"].sum()) / max(float(recent["volume"].sum()), 1)
        if buy_ratio > 0.58:   pressure = "BUYING"
        elif buy_ratio < 0.42: pressure = "SELLING"
        else:                  pressure = "NEUTRAL"

        # Absorption: high sell volume but price didn't drop much
        last5 = df_c.tail(5)
        high_sell = float(last5["sell_vol"].mean()) > avg_vol * 0.6
        price_held = abs(float(last5["close"].pct_change().mean())) < 0.001
        absorption  = high_sell and price_held and pressure != "SELLING"

        # Score modifier
        score_mod = 0.0
        if pressure == "BUYING":   score_mod += 0.5
        elif pressure == "SELLING":score_mod -= 0.5
        if divergence:             score_mod -= 0.8  # divergence warns of reversal
        if absorption:             score_mod += 0.7  # absorption = strong support

        return {
            "delta":          round(cur_delta, 0),
            "cum_delta":      round(cum_delta, 0),
            "buy_ratio":      round(buy_ratio, 3),
            "pressure":       pressure,
            "divergence":     divergence,
            "absorption":     absorption,
            "score_modifier": round(score_mod, 2),
            "note": (
                f"{'⚠️ Delta divergence — reversal warning' if divergence else ''}"
                f"{'🛡️ Absorption — strong support' if absorption else ''}"
                f"{'📈 Buying pressure' if pressure=='BUYING' else ''}"
                f"{'📉 Selling pressure' if pressure=='SELLING' else ''}"
            ).strip() or "Neutral order flow",
        }
    except Exception as e:
        logger.debug("order_flow: %s", e)
        return {"pressure":"NEUTRAL","score_modifier":0.0}


def calculate_vpin(df: pd.DataFrame, n_buckets: int = 50) -> float:
    """
    Volume-Synchronized Probability of Informed Trading (VPIN).
    Easley, López de Prado, O'Hara — "The Exchange of Flow Toxicity" (2012).

    Method:
      1. Estimate buy/sell volume per bar (tick-rule approximation).
      2. Divide total period volume into equal-size buckets of size V/n_buckets.
      3. For each bucket: compute |buy_vol - sell_vol| / (buy_vol + sell_vol).
      4. VPIN = rolling mean of last n_buckets imbalance ratios.

    Interpretation:
      VPIN > 0.7 → high informed trading activity → avoid counter-directional trades
      VPIN 0.3–0.7 → normal
      VPIN < 0.3 → mostly uninformed flow → safer for mean-reversion

    Returns float in [0, 1]. Returns 0.5 (neutral) on insufficient data.
    """
    try:
        _df = _estimate_buy_sell_volume(df)
        total_vol = _df["volume"].sum()
        if total_vol <= 0 or len(_df) < 10:
            return 0.5

        bucket_size = max(total_vol / n_buckets, 1.0)
        imbalances: list = []
        cumvol  = 0.0
        cum_buy = 0.0
        cum_sel = 0.0

        for _, row in _df.iterrows():
            cumvol  += row["volume"]
            cum_buy += row["buy_vol"]
            cum_sel += row["sell_vol"]
            while cumvol >= bucket_size:
                bv = cum_buy * (bucket_size / cumvol)
                sv = cum_sel * (bucket_size / cumvol)
                total = bv + sv
                imbalances.append(abs(bv - sv) / total if total > 0 else 0.0)
                cumvol  -= bucket_size
                cum_buy -= bv
                cum_sel -= sv

        if not imbalances:
            return 0.5
        vpin = float(np.mean(imbalances[-n_buckets:]))
        return round(min(max(vpin, 0.0), 1.0), 4)
    except Exception as e:
        logger.debug("calculate_vpin: %s", e)
        return 0.5


def run_order_flow_strategy(df, df_htf=None, symbol="", **kw) -> Dict:
    """Strategy wrapper for signal_engine."""
    empty = {"strategy":"order_flow","score":0.0,"direction":None,"side":None}
    try:
        of = compute_order_flow(df)
        score_mod = of.get("score_modifier", 0.0)
        if abs(score_mod) < 0.3:
            return empty
        direction = "BUY" if score_mod > 0 else "SELL"
        return {
            "strategy":  "order_flow",
            "score":     round(3.0 + abs(score_mod), 2),
            "direction": direction,
            "side":      direction,
            "note":      of.get("note",""),
            "pressure":  of.get("pressure","NEUTRAL"),
            "divergence":of.get("divergence",False),
        }
    except Exception as e:
        logger.debug("order_flow_strategy: %s", e)
        return empty
