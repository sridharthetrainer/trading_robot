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


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def resolve_barrier_levels(
    entry_price: float,
    side: str = "BUY",
    target_pct: float = 0.015,
    stop_pct: float = 0.010,
    target_price: float | None = None,
    stop_price: float | None = None,
) -> tuple[float, float, bool]:
    """
    Return (profit_barrier, stop_barrier, used_custom).

    For BUY, target must be above entry and stop below entry.
    For SELL, target must be below entry and stop above entry.
    Invalid or missing custom levels fall back to percentage barriers.
    """
    entry = _safe_float(entry_price, 0.0)
    if entry <= 0:
        return 0.0, 0.0, False

    side_u = str(side or "BUY").upper()
    target = _safe_float(target_price, 0.0)
    stop = _safe_float(stop_price, 0.0)
    if side_u == "BUY":
        fallback_target = entry * (1 + target_pct)
        fallback_stop = entry * (1 - stop_pct)
        custom_ok = target > entry and 0 < stop < entry
    else:
        fallback_target = entry * (1 - target_pct)
        fallback_stop = entry * (1 + stop_pct)
        custom_ok = 0 < target < entry and stop > entry

    if custom_ok:
        return target, stop, True
    return fallback_target, fallback_stop, False


def risk_reward_from_levels(entry_price: float, target_price: float, stop_price: float) -> float:
    entry = _safe_float(entry_price, 0.0)
    target = _safe_float(target_price, 0.0)
    stop = _safe_float(stop_price, 0.0)
    risk = abs(entry - stop)
    reward = abs(target - entry)
    if entry <= 0 or risk <= 0 or reward <= 0:
        return 0.0
    return reward / risk


def r_multiple_for_outcome(
    entry_price: float,
    outcome_price: float,
    side: str = "BUY",
    stop_price: float | None = None,
) -> float:
    entry = _safe_float(entry_price, 0.0)
    outcome = _safe_float(outcome_price, 0.0)
    stop = _safe_float(stop_price, 0.0)
    risk = abs(entry - stop)
    if entry <= 0 or outcome <= 0 or risk <= 0:
        return 0.0
    pnl = outcome - entry if str(side or "BUY").upper() == "BUY" else entry - outcome
    return pnl / risk


def cost_aware_r_multiple(
    entry_price: float,
    outcome_price: float,
    side: str = "BUY",
    stop_price: float | None = None,
    is_options: bool = False,
    product_type: str = "INTRADAY",
    slippage_pct: float = 0.0005,   # 0.05% per leg (0.1% round-trip) — the real bias-killer
    brokerage_per_leg: float = 20.0,
    representative_qty: float | None = None,
) -> float:
    """
    Net-of-cost R-multiple for a labelled signal.

    Gross `r_multiple_for_outcome` is PRE-COST and, with asymmetric barriers,
    biases average-R positive even with zero edge. This subtracts a realistic
    round-trip cost (taxes/fees from the corrected cost model + slippage on both
    legs, brokerage amortised over a representative position) before normalising
    by risk.

    NOTE: triple-barrier labels are UNDERLYING-DIRECTIONAL (entry/stop are the
    underlying price, not the option premium), so this is the cost of trading the
    underlying directionally — NOT the option-premium P&L. Option-buying bleed
    (premium spread + theta) is larger and measured separately from the option
    premium path. Defaults to the cash intraday model.
    """
    entry = _safe_float(entry_price, 0.0)
    outcome = _safe_float(outcome_price, 0.0)
    stop = _safe_float(stop_price, 0.0)
    risk = abs(entry - stop)
    if entry <= 0 or outcome <= 0 or risk <= 0:
        return 0.0

    gross = outcome - entry if str(side or "BUY").upper() == "BUY" else entry - outcome

    qty = representative_qty if (representative_qty and representative_qty > 0) else max(1.0, 100_000.0 / entry)
    try:
        from capital_compounder import calculate_full_costs
        costs = calculate_full_costs(
            entry, outcome, int(round(qty)), brokerage_per_leg, is_options, product_type
        )
        fee_per_unit = float(costs.total) / qty
    except Exception:
        # conservative fallback: ~0.06% round-trip taxes/fees + amortised brokerage
        fee_per_unit = (entry + outcome) * 0.0003 + (2.0 * brokerage_per_leg) / qty

    slip_per_unit = (entry + outcome) * max(0.0, _safe_float(slippage_pct, 0.0))  # one leg each side
    cost_per_unit = fee_per_unit + slip_per_unit
    net_pnl = gross - cost_per_unit
    return net_pnl / risk


def label_triple_barrier(
    df:          pd.DataFrame,
    entry_idx:   int,
    entry_price: float,
    target_pct:  float = 0.015,    # 1.5% upside target
    stop_pct:    float = 0.010,    # 1.0% downside stop
    max_bars:    int   = 12,       # 12 × 5min = 1 hour
    side:        str   = "BUY",
    target_price: float | None = None,
    stop_price:   float | None = None,
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

    profit_barrier, stop_barrier, _ = resolve_barrier_levels(
        entry_price,
        side=side,
        target_pct=target_pct,
        stop_pct=stop_pct,
        target_price=target_price,
        stop_price=stop_price,
    )

    end_idx = min(entry_idx + max_bars, n)

    for i in range(entry_idx + 1, end_idx):
        high  = float(df_c["high"].iloc[i])  if "high" in df_c.columns else float(df_c["close"].iloc[i])
        low   = float(df_c["low"].iloc[i])   if "low"  in df_c.columns else float(df_c["close"].iloc[i])

        if side.upper() == "BUY":
            if high >= profit_barrier:  return +1   # target hit
            if low  <= stop_barrier:    return -1   # stop hit
        else:
            if low  <= profit_barrier:  return +1   # target hit (short)
            if high >= stop_barrier:    return -1   # stop hit  (short)

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
            target_price = _safe_float(
                trade.get("target", trade.get("target_price", trade.get("take_profit", 0))),
                0.0,
            )
            stop_price = _safe_float(
                trade.get("stop_loss", trade.get("sl", trade.get("stop", 0))),
                0.0,
            )
            tb_target, tb_stop, used_custom = resolve_barrier_levels(
                entry_price,
                side=side,
                target_pct=target_pct,
                stop_pct=stop_pct,
                target_price=target_price,
                stop_price=stop_price,
            )

            label = label_triple_barrier(
                df_c, entry_idx, entry_price,
                target_pct, stop_pct, max_bars, side,
                target_price=target_price,
                stop_price=stop_price,
            )
            labelled.append({
                **trade,
                "tb_label":    label,
                "tb_target":   round(tb_target, 2),
                "tb_stop":     round(tb_stop, 2),
                "tb_rr":       round(risk_reward_from_levels(entry_price, tb_target, tb_stop), 4),
                "tb_used_custom_barrier": 1 if used_custom else 0,
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
