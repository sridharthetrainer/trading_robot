"""
triple_barrier.py  —  De Prado Triple Barrier Method for ML labels.

PROBLEM WITH SIMPLE WIN/LOSS LABELS:
  'Price went up after signal = WIN' is wrong because:
  1. Price may have dropped 150 points (hitting stop) before recovering
  2. The 'win' happened after holding 3 hours — not the same as 30 min
  3. Lookahead bias: you know the final outcome but not the path

TRIPLE BARRIER LABELS:
  For each signal, three barriers are set:
    Upper barrier:  entry + target_pct  → LABEL +1 (real win)
    Lower barrier:  entry - stop_pct    → LABEL -1 (real loss)
    Time barrier:   entry + max_bars    → LABEL  0 (timeout)

  A trade is a WIN only if upper barrier is hit FIRST.
  A trade is a LOSS only if lower barrier is hit FIRST.
  A trade is a TIMEOUT if neither is hit before max_bars.

  This is the correct way to label training data for financial ML.
"""
from __future__ import annotations
import logging
import pandas as pd

logger = logging.getLogger(__name__)


def label_triple_barrier(
    df:          pd.DataFrame,
    entry_idx:   int,
    entry_price: float,
    target_pct:  float = 0.015,    # 1.5% upside target
    stop_pct:    float = 0.010,    # 1.0% downside stop
    max_bars:    int   = 12,       # 12 × 5min = 1 hour
    side:        str   = "BUY",
) -> int:
    """
    Label a single trade using Triple Barrier method.

    Returns:
        +1  = upper barrier hit first (win)
        -1  = lower barrier hit first (loss)
         0  = time barrier hit (timeout / neutral)
    """
    df_c = df.copy()
    df_c.columns = [c.lower() for c in df_c.columns]
    if "close" not in df_c.columns:
        return 0

    n = len(df_c)
    if entry_idx >= n:
        return 0

    if side.upper() == "BUY":
        upper = entry_price * (1 + target_pct)
        lower = entry_price * (1 - stop_pct)
    else:
        upper = entry_price * (1 - target_pct)   # profit for short
        lower = entry_price * (1 + stop_pct)     # loss for short

    end_idx = min(entry_idx + max_bars, n)

    for i in range(entry_idx + 1, end_idx):
        high  = float(df_c["high"].iloc[i])  if "high" in df_c.columns else float(df_c["close"].iloc[i])
        low   = float(df_c["low"].iloc[i])   if "low"  in df_c.columns else float(df_c["close"].iloc[i])

        if side.upper() == "BUY":
            if high >= upper:  return +1   # target hit
            if low  <= lower:  return -1   # stop hit
        else:
            if low  <= upper:  return +1   # target hit (short)
            if high >= lower:  return -1   # stop hit  (short)

    return 0   # timeout


def label_all_trades(
    df:       pd.DataFrame,
    trades:   list,
    target_pct: float = 0.015,
    stop_pct:   float = 0.010,
    max_bars:   int   = 12,
) -> list:
    """
    Relabel a list of trade dicts using Triple Barrier.

    Each trade dict needs: entry_idx or entry_time, entry_price, side.
    Adds: tb_label (+1/0/-1), tb_target, tb_stop, tb_timeout_bars.
    """
    df_c = df.copy()
    df_c.columns = [c.lower() for c in df_c.columns]
    labelled = []

    for trade in trades:
        try:
            entry_price = float(trade.get("entry_price", trade.get("entry", 0)))
            side        = str(trade.get("side", "BUY")).upper()
            entry_idx   = int(trade.get("entry_idx", 0))

            label = label_triple_barrier(
                df_c, entry_idx, entry_price,
                target_pct, stop_pct, max_bars, side,
            )
            labelled.append({
                **trade,
                "tb_label":    label,
                "tb_target":   round(entry_price * (1 + target_pct if side == "BUY" else 1 - target_pct), 2),
                "tb_stop":     round(entry_price * (1 - stop_pct if side == "BUY" else 1 + stop_pct), 2),
                "tb_max_bars": max_bars,
            })
        except Exception as e:
            logger.debug("Triple barrier label error: %s", e)
            labelled.append({**trade, "tb_label": 0})

    return labelled


def get_dynamic_barriers(
    atr:        float,
    entry_price: float,
    vix:        float = 15.0,
) -> tuple[float, float, int]:
    """
    Dynamic barriers based on ATR and VIX.
    Higher volatility → wider barriers, fewer false hits.
    Returns (target_pct, stop_pct, max_bars).
    """
    # Base: 2× ATR target, 1.5× ATR stop
    target_pct = min(atr * 2.0 / entry_price, 0.03)    # cap at 3%
    stop_pct   = min(atr * 1.5 / entry_price, 0.02)    # cap at 2%

    # VIX adjustment: wider in high-vol regimes
    if vix > 20:
        target_pct *= 1.3
        stop_pct   *= 1.3
        max_bars    = 18   # allow more time in volatile markets
    elif vix < 12:
        target_pct *= 0.8
        stop_pct   *= 0.8
        max_bars    = 10
    else:
        max_bars    = 12

    return round(target_pct, 4), round(stop_pct, 4), max_bars
