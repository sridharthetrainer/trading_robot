import pandas as pd

from pivot_boss import (
    build_ochao_levels,
    calc_cpr,
    calc_floor_pivots,
    calc_ochao_level_pack,
    calculate_mtf_ema_alignment,
)


def test_cpr_matches_ochoa_pine_formula():
    h, l, c = 110.0, 100.0, 108.0
    pivot = (h + l + c) / 3.0
    bc = (h + l) / 2.0
    tc = (2.0 * pivot) - bc
    result = calc_cpr(h, l, c)
    assert result["pivot"] == round(pivot, 2)
    assert result["tc"] == round(max(tc, bc), 2)
    assert result["bc"] == round(min(tc, bc), 2)


def test_floor_pivots_include_r4_s4():
    result = calc_floor_pivots(110.0, 100.0, 108.0)
    assert "R4" in result
    assert "S4" in result
    assert result["R4"] > result["R3"]
    assert result["S4"] < result["S3"]


def test_ochao_pack_contains_cpr_floor_camarilla_hlc():
    result = calc_ochao_level_pack(110.0, 100.0, 108.0)
    for key in ("P", "TC", "BC", "R4", "S4", "H3", "H4", "H5", "L3", "L4", "L5", "H", "L", "C"):
        assert key in result


def test_build_ochao_levels_daily_weekly_monthly():
    idx = pd.date_range("2026-01-01 09:15", periods=160, freq="5min")
    df = pd.DataFrame({
        "open": range(160),
        "high": [100 + i * 0.1 for i in range(160)],
        "low": [99 + i * 0.1 for i in range(160)],
        "close": [99.5 + i * 0.1 for i in range(160)],
        "volume": [1000] * 160,
    }, index=idx)
    daily = df.resample("D").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    levels = build_ochao_levels(df, daily)
    assert "daily" in levels
    assert "weekly" in levels
    assert "monthly" in levels
    assert "TC" in levels["daily"]


def test_mtf_ema_alignment_returns_bias():
    idx = pd.date_range("2026-01-01 09:15", periods=220, freq="1min")
    df = pd.DataFrame({"close": [100 + i * 0.05 for i in range(220)]}, index=idx)
    result = calculate_mtf_ema_alignment({"1m": df, "5m": df, "D": df}, price=112)
    assert result["bias"] in ("BUY", "SELL", "NEUTRAL")
    assert result["total"] > 0
    assert "1m_ema20" in result["values"]


if __name__ == "__main__":
    test_cpr_matches_ochoa_pine_formula()
    test_floor_pivots_include_r4_s4()
    test_ochao_pack_contains_cpr_floor_camarilla_hlc()
    test_build_ochao_levels_daily_weekly_monthly()
    test_mtf_ema_alignment_returns_bias()
    print("ochao level tests passed")
