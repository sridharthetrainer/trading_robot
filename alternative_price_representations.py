"""Causal features and shadow strategies from alternative price representations.

Synthetic chart states are signal features only. Orders and labels always use
the source OHLC close, never a Kagi/P&F/Line-Break synthetic level.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np
import pandas as pd


FEATURE_NAMES = (
    "bar_return_1_atr", "line_slope_atr", "line_turn", "step_direction",
    "baseline_distance_atr", "hollow_state", "hollow_run",
    "heikin_direction", "heikin_run", "heikin_reversal",
    "heikin_body_atr", "heikin_upper_wick_atr", "heikin_lower_wick_atr",
    "volume_candle_strength", "line_break_direction", "line_break_run",
    "line_break_event", "kagi_direction", "kagi_reversal", "kagi_distance_atr",
    "pnf_direction", "pnf_boxes", "pnf_reversal", "range_direction",
    "range_run", "range_event", "footprint_delta_proxy", "footprint_available",
    "ichimoku_position", "ichimoku_tk", "representation_coverage",
)


def _frame(df: pd.DataFrame) -> pd.DataFrame:
    if df is None:
        return pd.DataFrame()
    out = df.copy()
    out.columns = [str(col).lower() for col in out.columns]
    for col in ("open", "high", "low", "close", "volume"):
        if col in out:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    if "close" not in out:
        return pd.DataFrame()
    for col in ("open", "high", "low"):
        if col not in out:
            out[col] = out["close"]
    if "volume" not in out:
        out["volume"] = 0.0
    return out.dropna(subset=["close"]).reset_index(drop=True)


def _atr(frame: pd.DataFrame, period: int = 14) -> float:
    previous = frame["close"].shift(1)
    tr = pd.concat([
        frame["high"] - frame["low"],
        (frame["high"] - previous).abs(),
        (frame["low"] - previous).abs(),
    ], axis=1).max(axis=1)
    value = float(tr.tail(period).mean()) if len(tr) else 0.0
    return max(value, float(frame["close"].iloc[-1]) * 0.0005, 1e-9)


def _run(values: list[int]) -> int:
    if not values:
        return 0
    last = values[-1]
    count = 0
    for value in reversed(values):
        if value != last:
            break
        count += 1
    return count


def _line_break(closes: np.ndarray, lines: int = 3) -> Dict[str, float]:
    endpoints = [float(closes[0])]
    directions: list[int] = []
    last_event_index = 0
    event = 0
    for index, value_raw in enumerate(closes[1:], 1):
        value = float(value_raw)
        previous = endpoints[-1]
        direction = directions[-1] if directions else 0
        recent = endpoints[-max(1, lines):]
        add = 0
        if direction >= 0 and value > previous:
            add = 1
        elif direction <= 0 and value < previous:
            add = -1
        elif direction > 0 and value < min(recent):
            add = -1
        elif direction < 0 and value > max(recent):
            add = 1
        if add:
            endpoints.append(value)
            directions.append(add)
            last_event_index = index
            event = add if index == len(closes) - 1 else 0
    direction = directions[-1] if directions else 0
    return {
        "direction": float(direction), "run": float(_run(directions)),
        "event": float(event), "age": float(len(closes) - 1 - last_event_index),
    }


def _kagi(closes: np.ndarray, reversal: float) -> Dict[str, float]:
    extreme = float(closes[0])
    direction = 0
    reversals = 0
    last_reversal = 0
    for index, raw in enumerate(closes[1:], 1):
        value = float(raw)
        if direction == 0:
            if value >= extreme + reversal:
                direction, extreme = 1, value
            elif value <= extreme - reversal:
                direction, extreme = -1, value
        elif direction > 0:
            if value > extreme:
                extreme = value
            elif value <= extreme - reversal:
                direction, extreme = -1, value
                reversals += 1
                last_reversal = index
        else:
            if value < extreme:
                extreme = value
            elif value >= extreme + reversal:
                direction, extreme = 1, value
                reversals += 1
                last_reversal = index
    return {
        "direction": float(direction),
        "reversal": float(direction if last_reversal == len(closes) - 1 else 0),
        "distance": float((closes[-1] - extreme) / max(reversal, 1e-9)),
        "reversals": float(reversals),
    }


def _point_and_figure(closes: np.ndarray, box: float, reversal_boxes: int = 3) -> Dict[str, float]:
    extreme = float(closes[0])
    direction = 0
    boxes = 0
    last_reversal = 0
    for index, raw in enumerate(closes[1:], 1):
        value = float(raw)
        if direction == 0:
            move = value - extreme
            if abs(move) >= box:
                direction = 1 if move > 0 else -1
                boxes = max(1, int(abs(move) // box))
                extreme += direction * boxes * box
        elif direction > 0:
            continuation = int(max(0.0, value - extreme) // box)
            if continuation:
                extreme += continuation * box
                boxes += continuation
            elif value <= extreme - reversal_boxes * box:
                direction = -1
                boxes = max(reversal_boxes, int((extreme - value) // box))
                extreme -= boxes * box
                last_reversal = index
        else:
            continuation = int(max(0.0, extreme - value) // box)
            if continuation:
                extreme -= continuation * box
                boxes += continuation
            elif value >= extreme + reversal_boxes * box:
                direction = 1
                boxes = max(reversal_boxes, int((value - extreme) // box))
                extreme += boxes * box
                last_reversal = index
    return {
        "direction": float(direction), "boxes": float(boxes),
        "reversal": float(direction if last_reversal == len(closes) - 1 else 0),
    }


def _range_state(closes: np.ndarray, box: float) -> Dict[str, float]:
    anchor = float(closes[0])
    directions: list[int] = []
    event = 0
    for index, raw in enumerate(closes[1:], 1):
        value = float(raw)
        move = value - anchor
        while abs(move) >= box:
            direction = 1 if move > 0 else -1
            directions.append(direction)
            anchor += direction * box
            event = direction if index == len(closes) - 1 else 0
            move = value - anchor
    return {
        "direction": float(directions[-1] if directions else 0),
        "run": float(_run(directions)), "event": float(event),
    }


def _heikin_ashi(
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    atr: float,
) -> Dict[str, float]:
    ha_close = (opens + highs + lows + closes) / 4.0
    ha_open = np.zeros_like(ha_close, dtype=float)
    ha_open[0] = (opens[0] + closes[0]) / 2.0
    for index in range(1, len(ha_open)):
        ha_open[index] = (ha_open[index - 1] + ha_close[index - 1]) / 2.0
    ha_high = np.maximum.reduce([highs, ha_open, ha_close])
    ha_low = np.minimum.reduce([lows, ha_open, ha_close])
    directions = [int(np.sign(c - o)) for o, c in zip(ha_open, ha_close)]
    direction = float(directions[-1]) if directions else 0.0
    previous = float(directions[-2]) if len(directions) >= 2 else 0.0
    return {
        "direction": direction,
        "run": float(_run(directions)),
        "reversal": direction if direction and previous and direction != previous else 0.0,
        "body_atr": float(abs(ha_close[-1] - ha_open[-1]) / max(atr, 1e-9)),
        "upper_wick_atr": float((ha_high[-1] - max(ha_open[-1], ha_close[-1])) / max(atr, 1e-9)),
        "lower_wick_atr": float((min(ha_open[-1], ha_close[-1]) - ha_low[-1]) / max(atr, 1e-9)),
    }


def build_representation_features(df: pd.DataFrame) -> Dict[str, float]:
    frame = _frame(df)
    empty = {name: 0.0 for name in FEATURE_NAMES}
    if len(frame) < 55:
        return empty
    close = frame["close"].to_numpy(dtype=float)
    open_ = frame["open"].to_numpy(dtype=float)
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    volume = frame["volume"].fillna(0).to_numpy(dtype=float)
    atr = _atr(frame)
    box = max(atr * 0.5, close[-1] * 0.001)

    prev_direction = np.sign(np.diff(close))
    line_turn = float(np.sign(prev_direction[-1] - prev_direction[-2])) if len(prev_direction) >= 2 else 0.0
    x = np.arange(min(8, len(close)), dtype=float)
    slope = float(np.polyfit(x, close[-len(x):], 1)[0] / atr) if len(x) >= 2 else 0.0
    baseline = float(np.mean(close[-20:]))
    hollow_states = []
    for index in range(1, len(close)):
        close_up = close[index] >= close[index - 1]
        hollow = close[index] >= open_[index]
        hollow_states.append(2 if close_up and hollow else 1 if close_up else -1 if hollow else -2)

    median_volume = max(float(np.median(volume[-20:])), 1.0)
    candle_direction = np.sign(close[-1] - open_[-1])
    volume_strength = float(candle_direction * volume[-1] / median_volume)
    spread = np.maximum(high[-20:] - low[-20:], 1e-9)
    clv = ((2 * close[-20:] - high[-20:] - low[-20:]) / spread)
    total_volume = float(np.sum(volume[-20:]))
    delta_proxy = float(np.sum(clv * volume[-20:]) / total_volume) if total_volume > 0 else 0.0

    line_break = _line_break(close)
    kagi = _kagi(close, max(atr, close[-1] * 0.002))
    pnf = _point_and_figure(close, box)
    range_state = _range_state(close, box)
    heikin = _heikin_ashi(open_, high, low, close, atr)

    try:
        from indicators import calculate_ichimoku
        ichi = calculate_ichimoku(frame)
        span_a = float(ichi["senkou_a"].iloc[-1])
        span_b = float(ichi["senkou_b"].iloc[-1])
        cloud_high, cloud_low = max(span_a, span_b), min(span_a, span_b)
        ichi_pos = 1.0 if close[-1] > cloud_high else -1.0 if close[-1] < cloud_low else 0.0
        ichi_tk = float(np.sign(float(ichi["tenkan_sen"].iloc[-1]) - float(ichi["kijun_sen"].iloc[-1])))
        if not np.isfinite(ichi_pos + ichi_tk):
            ichi_pos = ichi_tk = 0.0
    except Exception:
        ichi_pos = ichi_tk = 0.0

    result = {
        "bar_return_1_atr": float((close[-1] - close[-2]) / atr),
        "line_slope_atr": slope,
        "line_turn": line_turn,
        "step_direction": float(np.sign(close[-1] - baseline)),
        "baseline_distance_atr": float((close[-1] - baseline) / atr),
        "hollow_state": float(hollow_states[-1]),
        "hollow_run": float(_run([int(np.sign(v)) for v in hollow_states])),
        "heikin_direction": heikin["direction"],
        "heikin_run": heikin["run"],
        "heikin_reversal": heikin["reversal"],
        "heikin_body_atr": heikin["body_atr"],
        "heikin_upper_wick_atr": heikin["upper_wick_atr"],
        "heikin_lower_wick_atr": heikin["lower_wick_atr"],
        "volume_candle_strength": volume_strength,
        "line_break_direction": line_break["direction"],
        "line_break_run": line_break["run"],
        "line_break_event": line_break["event"],
        "kagi_direction": kagi["direction"],
        "kagi_reversal": kagi["reversal"],
        "kagi_distance_atr": kagi["distance"],
        "pnf_direction": pnf["direction"],
        "pnf_boxes": pnf["boxes"],
        "pnf_reversal": pnf["reversal"],
        "range_direction": range_state["direction"],
        "range_run": range_state["run"],
        "range_event": range_state["event"],
        # OHLCV close-location proxy, not true bid/ask footprint delta.
        "footprint_delta_proxy": delta_proxy,
        "footprint_available": 0.0,
        "ichimoku_position": ichi_pos,
        "ichimoku_tk": ichi_tk,
        "representation_coverage": 1.0,
    }
    return {name: round(float(result.get(name, 0.0)), 6) for name in FEATURE_NAMES}


def representation_history(df: pd.DataFrame, minimum: int = 55) -> pd.DataFrame:
    """Slow audit helper: each row is computed from that prefix only."""
    rows = []
    for end in range(len(df)):
        rows.append(build_representation_features(df.iloc[: end + 1]) if end + 1 >= minimum else {
            name: 0.0 for name in FEATURE_NAMES
        })
    return pd.DataFrame(rows, index=df.index)


def _signal(df: pd.DataFrame, strategy: str) -> Dict[str, Any]:
    features = build_representation_features(df)
    empty = {"strategy": strategy, "score": 0.0, "direction": None, "side": None,
             "representation_features": features}
    if features["representation_coverage"] <= 0:
        return empty
    real_close = float(_frame(df)["close"].iloc[-1])
    direction = 0
    score = 0.0
    if strategy == "hollow_candle_state":
        direction = int(np.sign(features["hollow_state"]))
        if features["hollow_run"] >= 3 and abs(features["volume_candle_strength"]) >= 0.8:
            score = min(4.5, 2.4 + 0.35 * features["hollow_run"])
    elif strategy == "three_line_break":
        direction = int(features["line_break_event"])
        if direction and features["line_break_run"] >= 2:
            score = min(4.8, 3.2 + 0.3 * features["line_break_run"])
    elif strategy == "kagi_reversal":
        direction = int(features["kagi_reversal"])
        score = 3.8 if direction else 0.0
    elif strategy == "point_and_figure":
        direction = int(features["pnf_reversal"] or features["pnf_direction"])
        if features["pnf_reversal"] and features["pnf_boxes"] >= 3:
            score = min(4.8, 3.4 + 0.15 * features["pnf_boxes"])
    elif strategy == "range_bar_momentum":
        direction = int(features["range_event"])
        if direction and features["range_run"] >= 2:
            score = min(4.5, 2.8 + 0.25 * features["range_run"])
    elif strategy == "ohlcv_footprint_proxy":
        direction = int(np.sign(features["footprint_delta_proxy"]))
        if abs(features["footprint_delta_proxy"]) >= 0.35 and abs(features["volume_candle_strength"]) >= 1.2:
            score = min(4.2, 2.8 + abs(features["footprint_delta_proxy"]))
    if not direction or score <= 0:
        return empty
    side = "BUY" if direction > 0 else "SELL"
    return {
        "strategy": strategy, "score": round(score, 4), "direction": side, "side": side,
        "price": real_close, "entry_price": real_close,
        "reason": f"{strategy}_{side.lower()}",
        "representation_features": features,
        "synthetic_chart_signal": True,
        "execution_price_source": "real_ohlc_close",
    }


def run_hollow_candle_state_strategy(df, df_htf=None, option_data=None, **kwargs):
    return _signal(df, "hollow_candle_state")


def run_three_line_break_strategy(df, df_htf=None, option_data=None, **kwargs):
    return _signal(df, "three_line_break")


def run_kagi_reversal_strategy(df, df_htf=None, option_data=None, **kwargs):
    return _signal(df, "kagi_reversal")


def run_point_and_figure_strategy(df, df_htf=None, option_data=None, **kwargs):
    return _signal(df, "point_and_figure")


def run_range_bar_momentum_strategy(df, df_htf=None, option_data=None, **kwargs):
    return _signal(df, "range_bar_momentum")


def run_ohlcv_footprint_proxy_strategy(df, df_htf=None, option_data=None, **kwargs):
    return _signal(df, "ohlcv_footprint_proxy")
