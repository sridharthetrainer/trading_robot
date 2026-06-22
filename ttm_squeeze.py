"""
ttm_squeeze.py

John Carter — Mastering the Trade
TTM Squeeze: The Coiled Spring Signal

CONCEPT:
  Bollinger Bands (BB) measure price volatility.
  Keltner Channels (KC) measure average volatility.

  When BB contracts INSIDE KC → market is compressing like a coiled spring.
  This is called a SQUEEZE — price cannot stay compressed forever.

  When BB expands OUTSIDE KC → squeeze is RELEASED.
  The first momentum bar after release = entry direction.

  Red dot  = squeeze ON  (coiling, wait)
  Green dot = squeeze OFF (released, trade the direction)

ENTRY RULES:
  Squeeze fires + momentum histogram turns positive → BUY CE
  Squeeze fires + momentum histogram turns negative → BUY PE

NIFTY APPLICATION:
  Fires 2-4 times per week on 5-min chart.
  Move after squeeze: typically 50-150 NIFTY points over 3-6 bars.
  Best combined with: Pivot Boss TC/BC breakout for direction confirmation.
"""
from __future__ import annotations
import logging
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

# TTM Squeeze parameters (Carter's original settings)
BB_PERIOD   = 20
BB_MULT     = 2.0
KC_PERIOD   = 20
KC_MULT     = 1.5   # Keltner multiplier — Carter uses 1.5
MOM_PERIOD  = 12    # Momentum lookback


def calculate_ttm_squeeze(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculate TTM Squeeze indicator.

    Adds columns to df:
      squeeze_on:   bool — BB inside KC (coiling)
      squeeze_off:  bool — BB just expanded outside KC (fire!)
      momentum:     float — momentum value
      mom_rising:   bool — momentum increasing (bullish)
      signal:       'BUY' | 'SELL' | None
      score:        float
    """
    df_c = df.copy()
    df_c.columns = [c.lower() for c in df_c.columns]

    if "close" not in df_c.columns or len(df_c) < BB_PERIOD + 5:
        df_c["squeeze_on"]  = False
        df_c["squeeze_off"] = False
        df_c["momentum"]    = 0.0
        df_c["mom_rising"]  = False
        df_c["sq_signal"]   = None
        df_c["sq_score"]    = 0.0
        return df_c

    close = df_c["close"]
    high  = df_c["high"]  if "high"  in df_c.columns else close
    low   = df_c["low"]   if "low"   in df_c.columns else close

    # ── Bollinger Bands ───────────────────────────────────────────────────────
    bb_mid   = close.rolling(BB_PERIOD).mean()
    bb_std   = close.rolling(BB_PERIOD).std()
    bb_upper = bb_mid + BB_MULT * bb_std
    bb_lower = bb_mid - BB_MULT * bb_std

    # ── Keltner Channels ──────────────────────────────────────────────────────
    tr       = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs()
    ], axis=1).max(axis=1)
    atr      = tr.rolling(KC_PERIOD).mean()
    kc_mid   = close.rolling(KC_PERIOD).mean()
    kc_upper = kc_mid + KC_MULT * atr
    kc_lower = kc_mid - KC_MULT * atr

    # ── Squeeze Detection ─────────────────────────────────────────────────────
    # Squeeze ON = BB is completely inside KC
    squeeze_on = (bb_upper < kc_upper) & (bb_lower > kc_lower)

    # Squeeze OFF = squeeze was ON last bar, now BB expanded outside KC
    squeeze_off = squeeze_on.shift(1).fillna(False) & ~squeeze_on

    # ── Momentum (Carter's linear regression oscillator) ──────────────────────
    # Delta between close and midpoint of (highest high + lowest low) / 2
    highest_high = high.rolling(MOM_PERIOD).max()
    lowest_low   = low.rolling(MOM_PERIOD).min()
    delta        = close - (highest_high + lowest_low) / 2 - bb_mid

    # Linear regression of delta over MOM_PERIOD
    def linreg_val(series, period):
        vals = []
        for i in range(len(series)):
            if i < period - 1:
                vals.append(np.nan)
            else:
                y = series.iloc[i-period+1:i+1].values
                x = np.arange(period)
                try:
                    slope, intercept = np.polyfit(x, y, 1)
                    vals.append(float(slope * (period-1) + intercept))
                except Exception:
                    vals.append(float(y[-1]))
        return pd.Series(vals, index=series.index)

    momentum = linreg_val(delta, MOM_PERIOD)

    # Rising momentum = momentum[t] > momentum[t-1]
    mom_rising = momentum > momentum.shift(1)

    # ── Signal Generation ─────────────────────────────────────────────────────
    signals = []
    scores  = []
    for i in range(len(df_c)):
        sq_off = bool(squeeze_off.iloc[i]) if not pd.isna(squeeze_off.iloc[i]) else False
        mom    = float(momentum.iloc[i]) if not pd.isna(momentum.iloc[i]) else 0
        rising = bool(mom_rising.iloc[i]) if not pd.isna(mom_rising.iloc[i]) else False
        sq_on  = bool(squeeze_on.iloc[i]) if not pd.isna(squeeze_on.iloc[i]) else False

        if sq_off:
            if rising and mom > 0:
                signals.append("BUY")
                # Score boosted by momentum strength
                score = 7.5 + min(abs(mom) / 10, 1.5)
                scores.append(round(score, 2))
            elif not rising and mom < 0:
                signals.append("SELL")
                score = 7.5 + min(abs(mom) / 10, 1.5)
                scores.append(round(score, 2))
            else:
                signals.append(None)
                scores.append(0.0)
        else:
            signals.append(None)
            scores.append(0.0)

    df_c["squeeze_on"]  = squeeze_on.values
    df_c["squeeze_off"] = squeeze_off.values
    df_c["momentum"]    = momentum.values
    df_c["mom_rising"]  = mom_rising.values
    df_c["sq_signal"]   = signals
    df_c["sq_score"]    = scores
    return df_c


def run_ttm_squeeze_strategy(df, df_htf=None, option_data=None) -> dict:
    """Drop-in strategy for signal_engine STRATEGIES list."""
    try:
        if df is None or len(df) < BB_PERIOD + MOM_PERIOD + 5:
            return {"strategy": "ttm_squeeze", "score": 0.0, "direction": None}

        result = calculate_ttm_squeeze(df)
        last   = result.iloc[-1]

        direction = last.get("sq_signal")
        score     = float(last.get("sq_score", 0.0))

        if not direction or score <= 0:
            # Check if squeeze is building (pre-signal awareness)
            recent_squeeze = result["squeeze_on"].tail(5).sum()
            if recent_squeeze >= 4:
                logger.debug("TTM Squeeze building — 4/5 bars in squeeze")
            return {"strategy": "ttm_squeeze", "score": 0.0, "direction": None,
                    "reason": "no_squeeze_fire"}

        # HTF confirmation
        if df_htf is not None and len(df_htf) >= BB_PERIOD:
            htf_result = calculate_ttm_squeeze(df_htf)
            htf_mom    = float(htf_result["momentum"].iloc[-1] or 0)
            htf_rising = bool(htf_result["mom_rising"].iloc[-1])
            if direction == "BUY"  and htf_rising and htf_mom > 0:
                score += 1.0
            elif direction == "SELL" and not htf_rising and htf_mom < 0:
                score += 1.0

        return {
            "strategy":  "ttm_squeeze",
            "score":     round(min(score, 9.5), 2),
            "direction": direction,
            "reason":    f"ttm_squeeze_fired_momentum_{'rising' if direction=='BUY' else 'falling'}",
        }
    except Exception as e:
        logger.debug("TTM Squeeze error: %s", e)
        return {"strategy": "ttm_squeeze", "score": 0.0, "direction": None}
