"""
backtest_pivot_boss.py — backtest of the PIVOT BOSS "Algo Version" (Frank Ochoa /
user's framework). Daily resolution, real NIFTY data, with an in-sample/OOS split.

ALGO VERSION (the only setups traded — everything else ignored, per the spec):
  A+ LONG : Higher Value  AND Narrow CPR AND today's OPEN > yesterday's HIGH
  A+ SHORT: Lower Value   AND Narrow CPR AND today's OPEN < yesterday's LOW

DEFINITIONS:
  • CPR (today) from YESTERDAY's H/L/C: pivot/TC/BC + floor pivots R1..R3/S1..S3.
  • Narrow CPR: width = |TC-BC|/pivot < 0.25%  (classify_cpr_width threshold).
  • Higher Value: today's CPR fully above yesterday's → today.bc > yest.tc.
  • Lower Value : today's CPR fully below yesterday's → today.tc < yest.bc.
  • Entry at today's OPEN; stop = BC (long)/TC (short); target = the next floor
    pivot ABOVE the open for longs (R1→R2→R3 — handles 'gap above R1'), mirror
    for shorts. Exit target/stop/EOD-close. If both stop & target are inside the
    day's range, assume STOP first (pessimistic).

CAVEATS: daily resolution (intraday entry-pullback nuance not modelled — this is
the breakout-at-open variant); futures-points proxy P&L (an optimistic upper
bound vs real option-buying); costs included. Validate, don't deploy on this alone.
"""
from __future__ import annotations

import argparse, json, logging
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.getLogger("pivot_boss_bt")

from pivot_boss import calc_cpr, calc_floor_pivots, classify_cpr_width

LOT_SIZE      = 65
BROKERAGE_LEG = 20.0
SLIPPAGE_PCT  = 0.0003   # 0.03% per side (index futures proxy)
STT_RATE_FUT  = 0.0002   # 0.02% sell-side


def _fetch_daily(symbol: str, days: int) -> Optional[pd.DataFrame]:
    import os
    from angel import AngelOne
    ang = AngelOne(api_key=os.getenv("API_KEY",""), client_id=os.getenv("CLIENT_ID",""),
                   password=os.getenv("PASSWORD",""), totp_secret=os.getenv("TOTP_SECRET",""))
    frm = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
    to  = datetime.now().strftime("%Y-%m-%d %H:%M")
    df = ang.get_historical_data(symbol, interval="ONE_DAY", from_date=frm, to_date=to, exchange="NSE")
    if df is None or df.empty:
        return None
    df = df.rename(columns={c: c.capitalize() for c in df.columns})
    for c in ("Open","High","Low","Close"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["Open","High","Low","Close"]).reset_index(drop=True)


def _costs(entry: float, exit_: float, qty: int) -> float:
    slip = (entry + exit_) * SLIPPAGE_PCT * qty
    stt  = exit_ * STT_RATE_FUT * qty
    return 2 * BROKERAGE_LEG + slip + stt


def _target_above(open_px: float, fp: Dict[str, float]) -> Optional[float]:
    for lv in ("R1","R2","R3"):
        if fp[lv] > open_px:
            return fp[lv]
    return None


def _target_below(open_px: float, fp: Dict[str, float]) -> Optional[float]:
    for lv in ("S1","S2","S3"):
        if fp[lv] < open_px:
            return fp[lv]
    return None


def _run(df: pd.DataFrame, i0: int, i1: int, lots: int, label: str) -> Dict:
    trades: List[Dict] = []
    O,H,L,C = df["Open"].values, df["High"].values, df["Low"].values, df["Close"].values
    qty = LOT_SIZE * lots
    for i in range(max(i0,2), i1):
        ph,pl,pc = H[i-1], L[i-1], C[i-1]        # yesterday → today's CPR
        yh,yl,yc = H[i-2], L[i-2], C[i-2]        # day-before → yesterday's CPR
        cpr  = calc_cpr(ph,pl,pc); fp = calc_floor_pivots(ph,pl,pc)
        yest = calc_cpr(yh,yl,yc)
        narrow = classify_cpr_width(cpr)["classification"] == "NARROW"
        higher_value = cpr["bc"] > yest["tc"]
        lower_value  = cpr["tc"] < yest["bc"]
        o = O[i]
        side = None
        if higher_value and narrow and o > ph:       # A+ LONG
            side, stop, tgt = "BUY", cpr["bc"], _target_above(o, fp)
        elif lower_value and narrow and o < pl:       # A+ SHORT
            side, stop, tgt = "SELL", cpr["tc"], _target_below(o, fp)
        if side is None or tgt is None:
            continue
        hi, lo, cl = H[i], L[i], C[i]
        if side == "BUY":
            hit_t = hi >= tgt; hit_s = lo <= stop
            exit_px = stop if hit_s else (tgt if hit_t else cl)   # pessimistic: stop first
            pnl = (exit_px - o) * qty - _costs(o, exit_px, qty)
        else:
            hit_t = lo <= tgt; hit_s = hi >= stop
            exit_px = stop if hit_s else (tgt if hit_t else cl)
            pnl = (o - exit_px) * qty - _costs(o, exit_px, qty)
        trades.append({"i": i, "side": side, "entry": round(o,1), "stop": round(stop,1),
                       "tgt": round(tgt,1), "exit": round(exit_px,1), "pnl": round(pnl,0),
                       "reason": "stop" if hit_s else ("target" if hit_t else "eod")})
    if not trades:
        return {"label": label, "trades": 0, "verdict": "NO TRADES"}
    arr = np.array([t["pnl"] for t in trades], float)
    wins = arr[arr>0]; loss = arr[arr<0]
    pf = float(wins.sum()/abs(loss.sum())) if loss.size and loss.sum()!=0 else (99.0 if wins.size else 0.0)
    sharpe = float(arr.mean()/arr.std()*np.sqrt(min(len(arr),252))) if arr.std()>0 else 0.0
    return {
        "label": label, "trades": int(len(arr)),
        "win_rate_pct": round(float((arr>0).mean())*100,1),
        "total_pnl": round(float(arr.sum()),0), "avg_pnl": round(float(arr.mean()),0),
        "profit_factor": round(pf,2), "pseudo_sharpe": round(sharpe,2),
        "max_win": round(float(arr.max()),0), "max_loss": round(float(arr.min()),0),
        "longs": sum(1 for t in trades if t["side"]=="BUY"),
        "shorts": sum(1 for t in trades if t["side"]=="SELL"),
        "verdict": "POSITIVE" if arr.sum()>0 else "NEGATIVE",
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Pivot Boss Algo-Version backtest")
    p.add_argument("--symbol", default="NIFTY")
    p.add_argument("--days", type=int, default=900)
    p.add_argument("--lots", type=int, default=1)
    args = p.parse_args()

    df = _fetch_daily(args.symbol, args.days)
    if df is None or len(df) < 60:
        log.error("insufficient daily data"); return 1
    log.info("daily bars: %d (%d..%d)", len(df), 0, len(df)-1)
    n = len(df); split = int(n*0.6)
    res = {
        "full":       _run(df, 2, n, args.lots, "full"),
        "in_sample":  _run(df, 2, split, args.lots, "in_sample"),
        "holdout_OOS":_run(df, split, n, args.lots, "holdout_OOS"),
    }
    out = {"run_date": str(date.today()), "symbol": args.symbol, "bars": n,
           "strategy": "pivot_boss_algo_version", "results": res,
           "caveats": "daily resolution, breakout-at-open entry, stop-first pessimistic, "
                      "futures-points proxy P&L (optimistic vs option-buying)."}
    json.dump(out, open("pivot_boss_backtest.json","w"), indent=2, default=str)
    for k in ("full","in_sample","holdout_OOS"):
        log.info("%s: %s", k, res[k])
    print("\nSaved → pivot_boss_backtest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
