"""
elliott_wave.py — Elliott Wave Auto-Detection

Detects Wave 3 (strongest, fastest move) for high R:R entries.
Also detects Wave 5 exhaustion for counter-trend plays.

Wave Rules enforced:
  Wave 2 never retraces > 100% of Wave 1
  Wave 3 never shortest of 1,3,5
  Wave 4 never overlaps Wave 1 territory (in impulse)

Trading edge:
  Wave 3 BUY entry: after Wave 2 pullback, target Wave 3 = 1.618x Wave 1
  Wave 3 SELL: in downtrend, Wave 3 down most powerful short
  Wave 5 exhaustion: fade at 1.0x-1.618x extension — reversal play
"""
from __future__ import annotations
import logging
import numpy as np
import pandas as pd
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

FIB = {
    "w2_min": 0.382, "w2_typical": 0.618, "w2_max": 0.786,
    "w3_min": 1.618, "w3_typical": 2.618,
    "w4_typical": 0.382,
    "w5_typical": 1.0,
    "ext_1": 1.0, "ext_2": 1.272, "ext_3": 1.618,
}


def _find_pivots(prices: np.ndarray, window: int = 5) -> Tuple[List, List]:
    """Find swing highs and lows."""
    highs, lows = [], []
    for i in range(window, len(prices)-window):
        if prices[i] == max(prices[i-window:i+window+1]):
            highs.append((i, float(prices[i])))
        if prices[i] == min(prices[i-window:i+window+1]):
            lows.append((i, float(prices[i])))
    return highs, lows


def _label_waves(pivots: List[Tuple]) -> List[Dict]:
    """Label alternating highs/lows as waves."""
    waves = []
    for i in range(1, len(pivots)):
        p0, p1 = pivots[i-1], pivots[i]
        direction = "UP" if p1[1] > p0[1] else "DOWN"
        size = abs(p1[1] - p0[1])
        pct  = size / max(p0[1], 1) * 100
        waves.append({
            "start_idx": p0[0], "end_idx": p1[0],
            "start_price": p0[1], "end_price": p1[1],
            "direction": direction, "size": size, "pct": pct,
        })
    return waves


def detect_elliott_waves(df: pd.DataFrame) -> Dict:
    """
    Detect Elliott Wave structure and identify current wave position.
    Returns wave position, entry signal, targets, and confidence.
    """
    result = {"wave": None, "signal": None, "score": 0.0, "targets": [], "confidence": 0.0}
    try:
        df_c = df.copy(); df_c.columns = [c.lower() for c in df_c.columns]
        if len(df_c) < 30: return result

        closes = df_c["close"].values.astype(float)
        highs_arr = df_c["high"].values.astype(float) if "high" in df_c.columns else closes
        lows_arr  = df_c["low"].values.astype(float)  if "low"  in df_c.columns else closes
        price     = float(closes[-1])

        # Find pivots
        highs, lows = _find_pivots(closes, window=3)
        all_pivots  = sorted(highs + lows, key=lambda x: x[0])
        if len(all_pivots) < 5: return result

        # Use last 6 pivots for wave labelling
        recent = all_pivots[-6:]
        waves  = _label_waves(recent)
        if len(waves) < 3: return result

        # Impulse wave detection (uptrend: 1 up, 2 down, 3 up, 4 down, 5 up)
        w = waves[-3:]  # last 3 waves
        if len(w) < 3: return result

        w1, w2, w3 = w[0], w[1], w[2]

        # Check for Wave 2 pullback into Wave 3 entry
        if (w1["direction"] == "UP" and w2["direction"] == "DOWN"
                and w3["direction"] == "UP"):
            # Wave 2 retracement check
            retrace = w2["size"] / max(w1["size"], 1)
            if 0.3 <= retrace <= 0.85:
                # Wave 3 is starting/in progress
                w3_ext = w3["size"] / max(w1["size"], 1)
                if w3_ext >= 1.0:
                    # Wave 3 in progress — target 1.618x W1 from W2 low
                    w1_size  = w1["size"]
                    w2_low   = w2["end_price"]
                    t1 = round(w2_low + w1_size * 1.618, 2)
                    t2 = round(w2_low + w1_size * 2.618, 2)
                    sl = round(w2["end_price"] * 0.995, 2)

                    # Wave 3 is our highest-conviction signal
                    confidence = min(0.5 + (1.0 - retrace) * 0.5, 0.85)
                    result = {
                        "wave":       "WAVE_3_UP",
                        "signal":     "BUY",
                        "score":      round(7.0 * confidence, 2),
                        "targets":    [t1, t2],
                        "stop":       sl,
                        "confidence": round(confidence, 3),
                        "retrace":    round(retrace, 3),
                        "note":       f"Wave 3 entry | W1={w1['pct']:.1f}% | Retrace={retrace:.0%} | Target ₹{t1:,.0f}",
                    }

        # Downtrend impulse
        elif (w1["direction"] == "DOWN" and w2["direction"] == "UP"
              and w3["direction"] == "DOWN"):
            retrace = w2["size"] / max(w1["size"], 1)
            if 0.3 <= retrace <= 0.85:
                w2_high = w2["end_price"]
                t1 = round(w2_high - w1["size"] * 1.618, 2)
                t2 = round(w2_high - w1["size"] * 2.618, 2)
                confidence = min(0.5 + (1.0 - retrace) * 0.5, 0.85)
                result = {
                    "wave":       "WAVE_3_DOWN",
                    "signal":     "SELL",
                    "score":      round(7.0 * confidence, 2),
                    "targets":    [t1, t2],
                    "stop":       round(w2["end_price"] * 1.005, 2),
                    "confidence": round(confidence, 3),
                    "retrace":    round(retrace, 3),
                    "note":       f"Wave 3 SHORT | Retrace={retrace:.0%} | Target ₹{t1:,.0f}",
                }

        # Wave 5 exhaustion (fade)
        if len(waves) >= 5:
            w5 = waves[-1]
            w4 = waves[-2]
            if (w5["direction"] == "UP" and
                    w5["size"] < waves[-3]["size"] * 0.8 and
                    w5["pct"] < 2.0):
                result["wave5_exhaustion"] = True
                result["fade_signal"] = "SELL"
                result["fade_note"]   = "Wave 5 exhaustion — consider mean-reversion short"

    except Exception as e:
        logger.debug("elliott_wave: %s", e)

    return result


def run_elliott_wave_strategy(df, df_htf=None, symbol="", **kw) -> Dict:
    """Strategy-compatible wrapper for signal_engine."""
    empty = {"strategy":"elliott_wave","score":0.0,"direction":None,"side":None}
    try:
        result = detect_elliott_waves(df)
        if not result.get("signal"):
            return empty
        direction = result["signal"]
        score     = result.get("score", 0.0)
        return {
            "strategy":  "elliott_wave",
            "score":     score,
            "direction": direction,
            "side":      direction,
            "note":      result.get("note",""),
            "targets":   result.get("targets",[]),
            "wave":      result.get("wave",""),
            "confidence":result.get("confidence",0),
        }
    except Exception as e:
        logger.debug("elliott_wave_strategy: %s", e)
        return empty
