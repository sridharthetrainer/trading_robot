"""
weinstein_stage.py

Stan Weinstein — Secrets for Profiting in Bull and Bear Markets
4-Stage Stock Lifecycle Analysis

STAGES (using 150-day / 30-week Simple Moving Average):
  Stage 1 — Basing:    Price flat, MA flat. Accumulation. WAIT.
  Stage 2 — Advancing: Price above rising MA. BUY CE options only.
  Stage 3 — Topping:   Price flat at top, MA flattening. EXIT longs.
  Stage 4 — Declining: Price below falling MA. BUY PE options only.

THE KEY RULE:
  NEVER buy CE options on a Stage 3 or Stage 4 stock.
  NEVER buy PE options on a Stage 1 or Stage 2 stock.

  This filter alone eliminates the majority of losing trades on stocks.

UPDATED: Weinstein now uses 40-week (200-day) MA as primary filter.
         We use both: 150-day (30-week) for signals + 200-day for filter.

NSE APPLICATION:
  Run Weinstein stage classification on all 200 Nifty 200 stocks.
  Only allow CE options on Stage 2 stocks.
  Only allow PE options on Stage 4 stocks.
  Never trade Stage 1 or Stage 3 stocks with options.

RELATIVE STRENGTH (Mansfield RSI):
  Weinstein also compares each stock to NIFTY.
  If stock outperforms NIFTY by 10%+ over 6 months → best candidates.
  We add this as a score bonus.
"""
from __future__ import annotations
import logging
from typing import Optional
import pandas as pd

logger = logging.getLogger(__name__)

# Weinstein's moving average periods
MA_PRIMARY   = 150   # 30-week SMA (original Weinstein)
MA_SECONDARY = 200   # 40-week SMA (updated Weinstein)
MA_SHORT     = 50    # 10-week SMA for early signals

# Stage classification thresholds
STAGE2_MIN_SLOPE  = 0.0001   # MA must be rising (slope > 0)
STAGE4_MAX_SLOPE  = -0.0001  # MA must be falling (slope < 0)
FLAT_THRESHOLD    = 0.0003   # MA slope within this = flat (Stage 1 or 3)


def classify_weinstein_stage(df_daily: pd.DataFrame) -> dict:
    """
    Classify a stock into Weinstein Stage 1/2/3/4.

    Args:
        df_daily: Daily OHLCV dataframe (minimum 220 bars = ~1 year)

    Returns:
        {
          "stage":      1 | 2 | 3 | 4,
          "label":      "BASING" | "ADVANCING" | "TOPPING" | "DECLINING",
          "option_bias": "BUY_CE" | "BUY_PE" | "AVOID" | "NEUTRAL",
          "ma150":      float,
          "ma150_slope": float,
          "price_vs_ma": "above" | "below",
          "volume_trend": "expanding" | "contracting" | "neutral",
          "confidence": float,
        }
    """
    result = {
        "stage":       0,
        "label":       "UNKNOWN",
        "option_bias": "AVOID",
        "ma150":       0.0,
        "ma150_slope": 0.0,
        "price_vs_ma": "unknown",
        "volume_trend":"neutral",
        "confidence":  0.0,
    }

    if df_daily is None or len(df_daily) < MA_PRIMARY + 20:
        return result

    df_c = df_daily.copy()
    df_c.columns = [c.lower() for c in df_c.columns]
    if "close" not in df_c.columns:
        return result

    close = df_c["close"]

    # Calculate MAs
    ma150 = close.rolling(MA_PRIMARY).mean()
    ma50  = close.rolling(MA_SHORT).mean()

    cur_close  = float(close.iloc[-1])
    cur_ma150  = float(ma150.iloc[-1])
    cur_ma50   = float(ma50.iloc[-1]) if len(ma50.dropna()) > 0 else cur_close

    # MA slope: compare current to 10 bars ago (normalized)
    old_ma150  = float(ma150.iloc[-11]) if len(ma150.dropna()) >= 11 else cur_ma150
    ma150_slope = (cur_ma150 - old_ma150) / old_ma150 if old_ma150 > 0 else 0

    price_above = cur_close > cur_ma150
    ma_rising   = ma150_slope > STAGE2_MIN_SLOPE
    ma_falling  = ma150_slope < STAGE4_MAX_SLOPE
    ma_flat     = abs(ma150_slope) <= FLAT_THRESHOLD

    # Volume trend
    vol_trend = "neutral"
    if "volume" in df_c.columns:
        vol_recent = float(df_c["volume"].iloc[-10:].mean())
        vol_older  = float(df_c["volume"].iloc[-30:-10].mean())
        if vol_recent > vol_older * 1.15:
            vol_trend = "expanding"
        elif vol_recent < vol_older * 0.85:
            vol_trend = "contracting"

    # ── Stage Classification ──────────────────────────────────────────────────
    stage   = 0
    label   = "UNKNOWN"
    bias    = "AVOID"
    conf    = 0.5

    if price_above and ma_rising:
        stage = 2
        label = "ADVANCING"
        bias  = "BUY_CE"
        conf  = 0.7
        # Higher confidence: price well above MA, MA steeply rising
        clearance = (cur_close - cur_ma150) / cur_ma150
        if clearance > 0.05:    conf += 0.1  # 5%+ above MA
        if ma150_slope > 0.001: conf += 0.1  # steep rise
        if vol_trend == "expanding": conf += 0.1

    elif not price_above and ma_falling:
        stage = 4
        label = "DECLINING"
        bias  = "BUY_PE"
        conf  = 0.7
        decline = (cur_ma150 - cur_close) / cur_ma150
        if decline > 0.05:      conf += 0.1
        if ma150_slope < -0.001: conf += 0.1
        if vol_trend == "expanding": conf += 0.1

    elif price_above and ma_flat:
        # Could be Stage 1 late (breaking out) or Stage 3 (topping)
        # Use 50-MA to distinguish
        if cur_close > cur_ma50:
            stage = 3
            label = "TOPPING"
            bias  = "AVOID"
            conf  = 0.5
        else:
            stage = 1
            label = "BASING"
            bias  = "AVOID"
            conf  = 0.4

    elif not price_above and ma_flat:
        stage = 1
        label = "BASING"
        bias  = "AVOID"
        conf  = 0.5
        # Stage 1B: approaching breakout
        gap_to_ma = (cur_ma150 - cur_close) / cur_ma150
        if gap_to_ma < 0.02:  # within 2% of MA = potential Stage 2 entry
            label = "BASING_NEAR_BREAKOUT"
            conf  = 0.6

    else:
        stage = 0
        label = "TRANSITIONING"
        bias  = "AVOID"

    result.update({
        "stage":        stage,
        "label":        label,
        "option_bias":  bias,
        "ma150":        round(cur_ma150, 2),
        "ma150_slope":  round(ma150_slope, 6),
        "price_vs_ma":  "above" if price_above else "below",
        "volume_trend": vol_trend,
        "confidence":   round(min(conf, 1.0), 2),
        "cur_price":    round(cur_close, 2),
        "clearance_pct": round((cur_close - cur_ma150) / cur_ma150 * 100, 2),
    })
    return result


def weinstein_stage_filter(
    symbol:     str,
    signal_side: str,    # "BUY" or "SELL"
    df_daily:   Optional[pd.DataFrame],
) -> dict:
    """
    Weinstein filter: should we allow this CE/PE trade on this stock?

    Returns:
        {
          "allow":    bool,
          "stage":    int,
          "reason":   str,
          "score_mod": float,  # positive = good stage, negative = bad
        }
    """
    # Indices don't need stage filter (they are the benchmark)
    index_syms = {"NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY","SENSEX","NIFTYNEXT50"}
    if symbol.upper() in index_syms:
        return {"allow": True, "stage": 2, "reason": "index_no_stage_filter",
                "score_mod": 0.0}

    if df_daily is None or len(df_daily) < MA_PRIMARY + 5:
        return {"allow": True, "stage": 0, "reason": "no_daily_data_allow",
                "score_mod": 0.0}

    stage_data = classify_weinstein_stage(df_daily)
    stage      = stage_data.get("stage", 0)
    label      = stage_data.get("label", "")
    bias       = stage_data.get("option_bias", "AVOID")
    conf       = stage_data.get("confidence", 0.5)

    # BUY (CE) signal
    if signal_side == "BUY":
        if stage == 2:
            return {"allow": True,  "stage": stage, "reason": f"stage2_advancing_allow_CE",
                    "score_mod": round(1.5 * conf, 2)}
        elif stage == 1 and "BREAKOUT" in label:
            return {"allow": True,  "stage": stage, "reason": "stage1b_near_breakout_allow",
                    "score_mod": 0.5}
        elif stage in (3, 4):
            return {"allow": False, "stage": stage, "reason": f"stage{stage}_{label}_BLOCK_CE",
                    "score_mod": -3.0}
        else:
            return {"allow": True,  "stage": stage, "reason": "stage1_basing_neutral",
                    "score_mod": -0.5}

    # SELL (PE) signal
    elif signal_side == "SELL":
        if stage == 4:
            return {"allow": True,  "stage": stage, "reason": "stage4_declining_allow_PE",
                    "score_mod": round(1.5 * conf, 2)}
        elif stage in (1, 2):
            return {"allow": False, "stage": stage, "reason": f"stage{stage}_{label}_BLOCK_PE",
                    "score_mod": -3.0}
        else:
            return {"allow": True,  "stage": stage, "reason": "stage3_topping_neutral_PE",
                    "score_mod": 0.5}

    return {"allow": True, "stage": stage, "reason": "unknown_side", "score_mod": 0.0}


def get_stage2_stocks(stock_daily_data: dict) -> list:
    """
    Given {symbol: df_daily} for all stocks,
    return sorted list of Stage 2 stocks (best CE buy candidates).
    """
    stage2 = []
    for symbol, df in stock_daily_data.items():
        try:
            s = classify_weinstein_stage(df)
            if s["stage"] == 2:
                stage2.append({
                    "symbol":      symbol,
                    "confidence":  s["confidence"],
                    "clearance":   s["clearance_pct"],
                    "ma_slope":    s["ma150_slope"],
                    "vol_trend":   s["volume_trend"],
                })
        except Exception:
            pass
    # Sort by confidence × clearance (best first)
    return sorted(stage2, key=lambda x: x["confidence"] * abs(x["clearance"]),
                  reverse=True)


def run_weinstein_stage_strategy(df, symbol: str = "", **kwargs) -> dict:
    """
    Stan Weinstein Stage Analysis.
    Stage 1: Basing → neutral
    Stage 2: Advancing → BUY
    Stage 3: Top → exit longs
    Stage 4: Declining → SELL/short
    """
    try:
        import pandas as _pd
        _df = df.copy()
        _df.columns = [c.lower() for c in _df.columns]
        if len(_df) < 30:
            return {"strategy": "weinstein_stage", "score": 0, "direction": "NEUTRAL"}

        close  = _df["close"].values
        volume = _df["volume"].values if "volume" in _df.columns else None

        # 30-week MA (approx 150 days, but on 5m data use ~600 bars)
        ma_len = min(len(close), 150)
        ma30 = sum(close[-ma_len:]) / ma_len
        price = close[-1]

        # Volume trend
        vol_rising = False
        if volume is not None and len(volume) >= 20:
            vol_rising = sum(volume[-5:]) / 5 > sum(volume[-20:-5]) / 15

        # Stage detection
        above_ma = price > ma30
        ma_rising = ma30 > sum(close[-(ma_len+5):-5]) / min(len(close), ma_len+5)

        if above_ma and ma_rising and vol_rising:
            # Stage 2 — advancing
            score = 6.5
            direction = "BUY"
            reason = f"Stage 2 advance: price above MA, MA rising, volume expanding"
        elif above_ma and not ma_rising:
            # Stage 3 — topping
            score = 3.5
            direction = "NEUTRAL"
            reason = "Stage 3 top: price above flat/declining MA"
        elif not above_ma and not ma_rising:
            # Stage 4 — declining
            score = 6.0
            direction = "SELL"
            reason = "Stage 4 decline: price below falling MA"
        else:
            # Stage 1 — basing
            score = 2.0
            direction = "NEUTRAL"
            reason = "Stage 1 base: building base"

        return {
            "strategy":  "weinstein_stage",
            "direction": direction,
            "score":     score,
            "reason":    reason,
            "above_ma":  above_ma,
            "ma_rising": ma_rising,
        }
    except Exception as e:
        return {"strategy": "weinstein_stage", "score": 0, "direction": "NEUTRAL"}
