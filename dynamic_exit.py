"""
dynamic_exit.py — dynamic stop-loss / target engine for managed trades.

The broker GTT is the static safety net (survives a crash). THIS layer makes the
levels intelligent while the system runs, blending well-established techniques
(no hand-wavy ML — the adaptive part is regime-driven volatility sizing, which
is exactly what ATR/Chandelier systems and pro tools like Sensibull/Streak do):

  1. Chandelier Exit   — HH(n) − k·ATR  (volatility trailing stop)
  2. Supertrend        — trend-following stop line
  3. Swing structure   — most recent swing low/high
  4. Regime-adaptive k — ADX high (trend) → wider stop (let it run);
                         ADX low (range) → tighter stop (protect)
  5. Profit ratchet    — once in profit, lock a fraction of the peak

The stop only ever TIGHTENS; it is always kept on the protective side of the
current price (never insta-triggers); the target only EXTENDS in strong trends.

Pure computation on an OHLC DataFrame — caller fetches candles and applies the
result to the broker GTT.
"""

from __future__ import annotations

from typing import Dict, Optional
import pandas as pd


def _col(df: pd.DataFrame, *names) -> Optional[str]:
    low = {c.lower(): c for c in df.columns}
    for n in names:
        if n in low:
            return low[n]
    return None


def compute_dynamic_levels(
    df: pd.DataFrame,
    side: str,
    entry: float,
    current_price: float,
    current_sl: float,
    current_target: float,
    hwm: float = 0.0,
    ratchet_frac: float = 0.5,
    be_trigger: float = 0.20,
) -> Dict:
    """
    Return {"sl", "target", "reason"} for the instrument.

    `df` is the OHLC series of the *traded* instrument (e.g. the option premium).
    SL only tightens; target only extends. On any failure the inputs pass through
    unchanged, so this can never loosen protection.
    """
    out = {"sl": current_sl, "target": current_target, "reason": ""}
    try:
        if df is None or len(df) < 20:
            return out
        is_long = str(side).upper() in ("BUY", "LONG")
        hc = _col(df, "high"); lc = _col(df, "low"); cc = _col(df, "close")
        if not (hc and lc and cc):
            return out

        from indicators import (calculate_atr, calculate_supertrend,
                                 detect_swing_highs_lows, calculate_adx)

        atr_s = calculate_atr(df, 14).dropna()
        if atr_s.empty:
            return out
        atr = float(atr_s.iloc[-1])
        if atr <= 0:
            return out

        # Regime-adaptive ATR multiplier (the adaptive / "ML" element):
        # strong trend → wider stop so the trade can breathe; range → tighter.
        try:
            adx = float(calculate_adx(df, 14).dropna().iloc[-1])
        except Exception:
            adx = 20.0
        k = 3.0 if adx >= 25 else 2.2 if adx >= 18 else 1.6

        reasons = []
        cands = []

        # 1. Chandelier Exit
        n = 22
        if is_long:
            hh = float(pd.to_numeric(df[hc], errors="coerce").tail(n).max())
            cands.append(hh - k * atr); reasons.append(f"chandelier(k={k})")
        else:
            ll = float(pd.to_numeric(df[lc], errors="coerce").tail(n).min())
            cands.append(ll + k * atr); reasons.append(f"chandelier(k={k})")

        # 2. Supertrend (only if it agrees with the position direction)
        try:
            st_line, st_dir = calculate_supertrend(df, 10, 3.0)
            st = float(st_line.dropna().iloc[-1])
            d = float(st_dir.dropna().iloc[-1])
            if (is_long and d > 0) or ((not is_long) and d < 0):
                cands.append(st); reasons.append("supertrend")
        except Exception:
            pass

        # 3. Swing structure (last swing low for long / high for short)
        try:
            sh, sl_ = detect_swing_highs_lows(df, 3)
            ser = sl_ if is_long else sh
            sv = ser.dropna()
            if not sv.empty:
                cands.append(float(sv.iloc[-1])); reasons.append("swing")
        except Exception:
            pass

        # 4. Profit ratchet — lock a fraction of peak profit once ahead
        peak = hwm or current_price
        if is_long and peak > entry:
            cands.append(entry + ratchet_frac * (peak - entry)); reasons.append("ratchet")
        elif (not is_long) and peak < entry and peak > 0:
            cands.append(entry - ratchet_frac * (entry - peak)); reasons.append("ratchet")

        # 5. Break-even floor — once the trade has run >= be_trigger in profit,
        # never let the stop fall back below entry. This is the give-back guard:
        # a winner that reverses exits at ~breakeven instead of a full loss.
        if is_long and peak >= entry * (1 + be_trigger):
            cands.append(entry); reasons.append("breakeven")
        elif (not is_long) and 0 < peak <= entry * (1 - be_trigger):
            cands.append(entry); reasons.append("breakeven")

        if not cands:
            return out

        # Keep a real buffer from price so normal noise can't trigger the stop.
        buf = max(0.5 * atr, current_price * 0.01)
        if is_long:
            dyn = max(cands)
            if dyn > current_price - buf:   # too close to price → don't tighten
                return out
            dyn = max(dyn, current_sl)      # only ever tighten
        else:
            dyn = min(cands)
            if dyn < current_price + buf:
                return out
            dyn = min(dyn, current_sl) if current_sl > 0 else dyn

        out["sl"] = round(dyn, 2)

        # Dynamic target — let profit run in strong trends (extend only).
        if adx >= 25 and atr > 0:
            if is_long:
                out["target"] = round(max(current_target, current_price + 2 * k * atr), 2)
            else:
                out["target"] = round(min(current_target or 1e9, current_price - 2 * k * atr), 2)

        out["reason"] = "+".join(reasons) + f" adx={adx:.0f}"
        return out
    except Exception:
        return out
