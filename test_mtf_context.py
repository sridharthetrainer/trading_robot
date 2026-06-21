import numpy as np
import pandas as pd

from mtf_context import build_mtf_context, score_mtf_alignment


def _trend_df(start=100.0, stop=110.0, rows=80):
    close = np.linspace(start, stop, rows)
    return pd.DataFrame({
        "open": close - 0.1,
        "high": close + 0.4,
        "low": close - 0.4,
        "close": close,
        "volume": np.linspace(1000, 2000, rows),
        "ema_fast": close - 0.2,
        "ema_slow": close - 0.7,
        "ema_trend": close - 1.5,
        "vwap": close - 0.3,
        "supertrend_dir": np.ones(rows),
    })


def test_bullish_mtf_context_scores_buy_positive_sell_negative():
    ctx = build_mtf_context({
        "primary": _trend_df(),
        "htf": _trend_df(96, 112),
        "1h": _trend_df(90, 115),
    })
    assert ctx["bias"] == "BUY"
    assert ctx["aligned_frames"] >= 2

    buy = score_mtf_alignment(ctx, "BUY", "breakout")
    sell = score_mtf_alignment(ctx, "SELL", "breakout")
    assert buy["score_modifier"] > 0
    assert sell["score_modifier"] < 0


def test_mtf_context_handles_missing_frames():
    ctx = build_mtf_context({"primary": _trend_df(rows=8), "htf": None})
    assert ctx["bias"] in {"BUY", "SELL", "NEUTRAL"}
    assert "primary" in ctx["frames"]


if __name__ == "__main__":
    test_bullish_mtf_context_scores_buy_positive_sell_negative()
    test_mtf_context_handles_missing_frames()
    print("MTF context tests passed")
