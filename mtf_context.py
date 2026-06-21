from __future__ import annotations

from typing import Any, Dict, Mapping

import math

import numpy as np
import pandas as pd


def _norm_frame(df: Any) -> pd.DataFrame | None:
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return None
    out = df.copy()
    out.columns = [str(c).lower() for c in out.columns]
    return out


def _num(v: Any, default: float = math.nan) -> float:
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except Exception:
        return default


def _last_series(df: pd.DataFrame, *cols: str) -> float:
    for col in cols:
        if col in df.columns and len(df[col]):
            val = _num(pd.to_numeric(df[col], errors="coerce").iloc[-1])
            if not math.isnan(val):
                return val
    return math.nan


def _ema(close: pd.Series, span: int) -> float:
    if close is None or len(close) < max(3, min(span, 20)):
        return math.nan
    return _num(pd.to_numeric(close, errors="coerce").ewm(span=span, adjust=False).mean().iloc[-1])


def _vwap(df: pd.DataFrame) -> float:
    if "vwap" in df.columns:
        val = _last_series(df, "vwap")
        if not math.isnan(val):
            return val
    if not {"high", "low", "close", "volume"}.issubset(df.columns):
        return math.nan
    volume = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
    if volume.tail(30).sum() <= 0:
        return math.nan
    typical = (
        pd.to_numeric(df["high"], errors="coerce")
        + pd.to_numeric(df["low"], errors="coerce")
        + pd.to_numeric(df["close"], errors="coerce")
    ) / 3.0
    return _num((typical.tail(60) * volume.tail(60)).sum() / max(volume.tail(60).sum(), 1.0))


def _supertrend_dir(df: pd.DataFrame) -> int:
    val = _last_series(df, "supertrend_dir")
    if val in (1.0, -1.0):
        return int(val)
    try:
        from indicators import calculate_supertrend

        _, direction = calculate_supertrend(df)
        last = _num(direction.iloc[-1])
        if last in (1.0, -1.0):
            return int(last)
    except Exception:
        pass
    return 0


def _frame_context(name: str, df: pd.DataFrame) -> Dict[str, Any]:
    close_s = pd.to_numeric(df.get("close", pd.Series(dtype=float)), errors="coerce")
    price = _last_series(df, "close")
    ema20 = _last_series(df, "ema20", "ema_20", "ema_fast")
    ema50 = _last_series(df, "ema50", "ema_50", "ema_slow")
    ema200 = _last_series(df, "ema200", "ema_200", "ema_trend")
    if math.isnan(ema20):
        ema20 = _ema(close_s, 20)
    if math.isnan(ema50):
        ema50 = _ema(close_s, 50)
    if math.isnan(ema200):
        ema200 = _ema(close_s, 200)
    vwap = _vwap(df)
    st_dir = _supertrend_dir(df)

    votes = []
    reasons = []
    if not math.isnan(price) and not math.isnan(ema20):
        votes.append(1 if price >= ema20 else -1)
        reasons.append("price_above_ema20" if price >= ema20 else "price_below_ema20")
    if not math.isnan(ema20) and not math.isnan(ema50):
        votes.append(1 if ema20 >= ema50 else -1)
        reasons.append("ema20_above_ema50" if ema20 >= ema50 else "ema20_below_ema50")
    if not math.isnan(price) and not math.isnan(ema200):
        votes.append(1 if price >= ema200 else -1)
        reasons.append("price_above_ema200" if price >= ema200 else "price_below_ema200")
    if not math.isnan(price) and not math.isnan(vwap):
        votes.append(1 if price >= vwap else -1)
        reasons.append("price_above_vwap" if price >= vwap else "price_below_vwap")
    if st_dir in (1, -1):
        votes.append(st_dir)
        reasons.append("supertrend_bull" if st_dir == 1 else "supertrend_bear")

    if not votes:
        score = 0.0
    else:
        score = float(np.mean(votes))
    bias = "BUY" if score >= 0.25 else "SELL" if score <= -0.25 else "NEUTRAL"
    return {
        "frame": name,
        "bars": int(len(df)),
        "price": None if math.isnan(price) else round(price, 4),
        "ema20": None if math.isnan(ema20) else round(ema20, 4),
        "ema50": None if math.isnan(ema50) else round(ema50, 4),
        "ema200": None if math.isnan(ema200) else round(ema200, 4),
        "vwap": None if math.isnan(vwap) else round(vwap, 4),
        "supertrend_dir": st_dir,
        "score": round(score, 3),
        "bias": bias,
        "reasons": reasons[:6],
    }


def build_mtf_context(frames: Mapping[str, Any] | None, price: float | None = None) -> Dict[str, Any]:
    """Build a compact multi-timeframe indicator context.

    Positive aggregate score is bullish, negative is bearish. The returned
    object is intentionally serialisable so it can travel through signal_meta.
    """
    if not frames:
        return {"bias": "NEUTRAL", "score": 0.0, "frames": {}, "aligned_frames": 0, "conflict_frames": 0}

    ordered = []
    for name in ("primary", "lt_intraday", "5m", "15m", "htf", "1h", "daily"):
        if name in frames:
            ordered.append(name)
    ordered.extend([name for name in frames.keys() if name not in ordered])

    frame_ctx: Dict[str, Dict[str, Any]] = {}
    for name in ordered:
        df = _norm_frame(frames.get(name))
        if df is None or len(df) < 5:
            continue
        frame_ctx[name] = _frame_context(name, df)

    if not frame_ctx:
        return {"bias": "NEUTRAL", "score": 0.0, "frames": {}, "aligned_frames": 0, "conflict_frames": 0}

    weights = {
        "primary": 1.0,
        "lt_intraday": 0.8,
        "5m": 0.9,
        "15m": 1.0,
        "htf": 1.25,
        "1h": 1.35,
        "daily": 1.6,
    }
    total_w = 0.0
    weighted = 0.0
    for name, ctx in frame_ctx.items():
        w = float(weights.get(name, 1.0))
        weighted += float(ctx.get("score", 0.0) or 0.0) * w
        total_w += w
    score = weighted / max(total_w, 1e-9)
    bias = "BUY" if score >= 0.20 else "SELL" if score <= -0.20 else "NEUTRAL"
    aligned = sum(1 for ctx in frame_ctx.values() if ctx.get("bias") == bias and bias != "NEUTRAL")
    conflicts = sum(
        1 for ctx in frame_ctx.values()
        if bias != "NEUTRAL" and ctx.get("bias") not in (bias, "NEUTRAL")
    )
    out = {
        "bias": bias,
        "score": round(float(score), 3),
        "frames": frame_ctx,
        "aligned_frames": int(aligned),
        "conflict_frames": int(conflicts),
    }
    if price is not None:
        out["price"] = _num(price, 0.0)
    return out


def score_mtf_alignment(
    context: Mapping[str, Any] | None,
    direction: str,
    strategy: str = "",
) -> Dict[str, Any]:
    if not context:
        return {"score_modifier": 0.0, "bias": "NEUTRAL", "aligned": 0, "conflicts": 0}
    side = str(direction or "").upper()
    raw = _num(context.get("score"), 0.0)
    aligned_score = raw if side in ("BUY", "LONG", "CALL") else -raw
    st = str(strategy or "").lower()
    if any(x in st for x in ("break", "orb", "trend", "momentum", "supertrend", "ma_cross")):
        weight = 1.15
    elif any(x in st for x in ("mean", "reversion", "vwap", "cpr", "scalp", "rsi")):
        weight = 0.65
    else:
        weight = 0.9
    mod = max(-1.5, min(1.5, aligned_score * weight))
    return {
        "score_modifier": round(float(mod), 3),
        "bias": context.get("bias", "NEUTRAL"),
        "aligned": int(context.get("aligned_frames", 0) or 0),
        "conflicts": int(context.get("conflict_frames", 0) or 0),
        "raw_score": round(float(raw), 3),
        "frame_biases": {
            name: frame.get("bias", "NEUTRAL")
            for name, frame in (context.get("frames") or {}).items()
            if isinstance(frame, Mapping)
        },
    }
