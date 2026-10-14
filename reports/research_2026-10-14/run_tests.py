"""Run the two pre-registered tests in PREREGISTRATION.md (same folder).

Read-only research: reads candle/option data, writes results.json next to
this file, places no orders. Run from the repo root:
    .venv/bin/python reports/research_2026-10-14/run_tests.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import backtest_carver_ewmac as ewmac  # noqa: E402
import condor_backtest_real as cbr  # noqa: E402
import credit_spread_backtest_real as csb  # noqa: E402

OUT = Path(__file__).resolve().parent / "results.json"
OOS_FRACTION = 0.30


def daily_from_minutes(name: str) -> pd.Series:
    m = pd.read_csv(ROOT / "external_data" / "nse_index_upto_2024" / f"{name}.csv",
                    usecols=["date", "close"], parse_dates=["date"])
    return m.set_index("date")["close"].resample("1D").last().dropna()


def nifty_daily(option_spot: dict) -> pd.Series:
    pre = daily_from_minutes("NIFTY_50")
    post = pd.Series(option_spot, dtype=float)
    post.index = pd.to_datetime(post.index)
    pre = pre[pre.index < post.index.min()]
    return pd.concat([pre, post]).sort_index()


def sharpe(net: np.ndarray) -> float:
    sd = net.std(ddof=1)
    return float(net.mean() / sd * np.sqrt(252)) if len(net) > 1 and sd > 0 else 0.0


def ewmac_variant_a(price: pd.Series, symbol: str) -> dict:
    r = ewmac.backtest_carver_ewmac(symbol=symbol, data=price.to_frame("close"),
                                    verbose=False, close_at_end=True)
    net = np.array([d["net_pnl"] for d in r["daily"]])
    split = int(len(net) * (1 - OOS_FRACTION))
    dates = [d["date"] for d in r["daily"]]
    return {"days": len(net), "start": dates[0], "end": dates[-1], "oos_start": dates[split],
            "capital": ewmac.CAPITAL, "net_pnl": round(float(net.sum())),
            "total_cost": r["total_cost"], "sharpe": round(sharpe(net), 3),
            "max_drawdown": r["max_drawdown"],
            "oos_net_pnl": round(float(net[split:].sum())), "oos_sharpe": round(sharpe(net[split:]), 3),
            "in_sample_sharpe": round(sharpe(net[:split]), 3)}


def ewmac_variant_b(price: pd.Series, symbol: str, capital: float = 500_000.0, lot: int = 75) -> dict:
    """Same forecast, ₹5L capital, whole lots only (what a retail account can hold)."""
    forecast, price_vol = ewmac._combined_forecast(price)
    ann_vol = price_vol * np.sqrt(252)
    lots = (ewmac.TARGET_RISK_PCT * capital / (lot * ann_vol.replace(0, np.nan))) * (forecast / 10.0)
    lots = lots.dropna().round()
    cost_model = ewmac.get_cost_model()
    prev_lots, prev_px, rows = 0.0, None, []
    for ts, n in lots.items():
        px = float(price.loc[ts])
        gross = prev_lots * lot * (px - prev_px) if prev_px is not None else 0.0
        delta = (n - prev_lots) * lot
        cost = (cost_model.single_leg_cost(abs(delta) * px, "FUT", "BUY" if delta > 0 else "SELL", symbol).total
                if delta else 0.0)
        rows.append(gross - cost)
        prev_lots, prev_px = n, px
    if prev_lots:
        rows[-1] -= cost_model.single_leg_cost(abs(prev_lots) * lot * prev_px, "FUT",
                                               "SELL" if prev_lots > 0 else "BUY", symbol).total
    net = np.array(rows)
    return {"capital": capital, "lot": lot, "net_pnl": round(float(net.sum())),
            "sharpe": round(sharpe(net), 3),
            "pct_days_in_market": round(float((lots != 0).mean()) * 100, 1),
            "max_abs_lots": int(lots.abs().max())}


def buy_and_hold(price: pd.Series) -> dict:
    ret = price.pct_change().dropna().to_numpy()
    years = (price.index[-1] - price.index[0]).days / 365.25
    return {"sharpe": round(sharpe(ret), 3),
            "cagr_pct": round(((price.iloc[-1] / price.iloc[0]) ** (1 / years) - 1) * 100, 2)}


def ewmac_test(option_spot: dict) -> dict:
    out = {}
    for symbol, price in (("NIFTY", nifty_daily(option_spot)),
                          ("BANKNIFTY", daily_from_minutes("NIFTY_BANK"))):
        a = ewmac_variant_a(price, symbol)
        out[symbol] = {"variant_a": a, "variant_b": ewmac_variant_b(price, symbol),
                       "buy_and_hold": buy_and_hold(price.loc[a["start"]:])}
    n = out["NIFTY"]
    checks = {"full_sharpe_ge_0_5": n["variant_a"]["sharpe"] >= 0.5,
              "oos_sharpe_ge_0_3": n["variant_a"]["oos_sharpe"] >= 0.3,
              "oos_pnl_positive": n["variant_a"]["oos_net_pnl"] > 0,
              "whole_lot_5L_pnl_positive": n["variant_b"]["net_pnl"] > 0}
    out["checks"] = checks
    out["verdict"] = "PASS" if all(checks.values()) else "FAIL"
    return out


def put_spread_test(opt: pd.DataFrame, option_spot: dict) -> dict:
    captured: list = []
    original = csb._report
    csb._report = lambda trades, *a: (captured.extend(trades), original(trades, *a))[1]
    try:
        base = csb.backtest_credit_spread(opt, option_spot, "put", 0.015, 300, 3, 20.0, 0.02, cbr._LOT)
    finally:
        csb._report = original
    sma200 = nifty_daily(option_spot).rolling(200).mean()
    close = nifty_daily(option_spot)
    kept = [t for t in captured
            if pd.Timestamp(t["entry"]) in sma200.index
            and not np.isnan(sma200.loc[pd.Timestamp(t["entry"])])
            and close.loc[pd.Timestamp(t["entry"])] > sma200.loc[pd.Timestamp(t["entry"])]]
    filt = cbr._report(kept, 0.015, 300, 0.02, cbr._LOT)
    rs = np.sort([t["r"] for t in kept])
    ex_best3 = float(rs[:-3].sum()) if len(rs) > 3 else 0.0
    best = sorted(kept, key=lambda t: -t["r"])[:3]
    checks = {"n_ge_60": filt["all"]["n"] >= 60,
              "oos_pf_ge_1_2": (filt["oos"].get("profit_factor") or 0) >= 1.2,
              "oos_expectancy_ge_0_03R": filt["oos"]["expectancy_R"] >= 0.03,
              "total_R_ex_best3_positive": ex_best3 > 0,
              "beats_unfiltered": filt["all"]["expectancy_R"] > base["all"]["expectancy_R"]}
    return {"unfiltered": base, "filtered": filt, "weeks_kept": len(kept), "weeks_total": len(captured),
            "total_R_excl_best3": round(ex_best3, 2),
            "best_trades": [{"entry": t["entry"], "R": round(t["r"], 2), "credit": t["credit"],
                             "max_loss_u": t["max_loss_u"]} for t in best],
            "checks": checks, "verdict": "PASS" if all(checks.values()) else "FAIL"}


def main() -> None:
    opt, option_spot = cbr._load()
    results = {"preregistration": "PREREGISTRATION.md",
               "test1_ewmac": ewmac_test(option_spot),
               "test4_put_spread_above_200dma": put_spread_test(opt, option_spot)}
    OUT.write_text(json.dumps(results, indent=2, default=str))
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
