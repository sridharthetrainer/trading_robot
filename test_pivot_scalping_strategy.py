import numpy as np
import pandas as pd

from pivot_scalping_strategy import run_pivot_scalping_strategy


def _df(start, stop, rows=90):
    close = np.linspace(start, stop, rows)
    return pd.DataFrame({
        "open": close - 0.05,
        "high": close + 0.25,
        "low": close - 0.25,
        "close": close,
        "volume": np.linspace(1000, 2200, rows),
        "volume_ratio": np.full(rows, 1.3),
    })


def _levels():
    return {
        "daily": {
            "P": 100.0, "TC": 101.0, "BC": 99.0,
            "R1": 104.0, "S1": 96.0, "R2": 106.0, "S2": 94.0,
            "R3": 108.0, "S3": 92.0,
            "H3": 101.2, "H4": 106.0, "H5": 110.0,
            "L3": 100.2, "L4": 94.0, "L5": 90.0,
            "H": 103.0, "L": 97.0, "C": 100.0,
        },
        "weekly": {
            "W_P": 99.0, "W_TC": 100.0, "W_BC": 98.0,
        },
        "monthly": {
            "M_P": 98.5, "M_TC": 99.5, "M_BC": 97.5,
        },
        "yearly": {
            "Y_P": 98.0, "Y_TC": 99.0, "Y_BC": 97.0,
        },
    }


def test_pivot_scalper_buy_breakout():
    df5 = _df(100, 105)
    df1 = _df(99, 105, rows=240)
    out = run_pivot_scalping_strategy(
        df5,
        df5,
        option_data={"symbol": "NIFTY", "ochao_levels": _levels(), "frames": {"1m": df1, "5m": df5}},
        symbol="NIFTY",
    )
    assert out["direction"] == "BUY"
    assert out["style"] == "scalping"
    assert out["score"] >= 4.2
    assert out["cpr_structure"]["golden_bullish_pivot"]
    assert out["level_breakout_confirmed"]


def test_pivot_scalper_sell_breakdown():
    levels = _levels()
    df5 = _df(100, 95)
    df1 = _df(101, 95, rows=240)
    out = run_pivot_scalping_strategy(
        df5,
        df5,
        option_data={"symbol": "BANKNIFTY", "ochao_levels": levels, "frames": {"1m": df1, "5m": df5}},
        symbol="BANKNIFTY",
    )
    assert out["direction"] == "SELL"
    assert out["style"] == "scalping"
    assert out["score"] >= 4.2
    assert out["level_breakout_confirmed"] or out["level_rejection"]


def test_pivot_scalper_ignores_non_enabled_underlying():
    out = run_pivot_scalping_strategy(
        _df(100, 105),
        _df(100, 105),
        option_data={"symbol": "RELIANCE", "ochao_levels": _levels()},
        symbol="RELIANCE",
    )
    assert out["direction"] is None


if __name__ == "__main__":
    test_pivot_scalper_buy_breakout()
    test_pivot_scalper_sell_breakdown()
    test_pivot_scalper_ignores_non_enabled_underlying()
    print("pivot scalping strategy tests passed")
