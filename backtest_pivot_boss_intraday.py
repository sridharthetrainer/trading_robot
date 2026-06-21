"""
backtest_pivot_boss_intraday.py — FAITHFUL Pivot Boss "Algo Version" test.

Fixes the two flaws of the daily first pass:
  1. PULLBACK entry (not buy-the-gap): after an A+ gap day, wait intraday for
     price to pull back to the nearest floor pivot below/above the open, enter
     there with a TIGHT pivot-to-pivot stop and target — as the framework says.
  2. POOLED across indices for a usable sample, and measured in R-MULTIPLES
     (profit / initial risk) so results are comparable across price levels/lots.

A+ setups only (everything else ignored, per the spec):
  LONG : Higher Value AND Narrow CPR AND today's OPEN > yesterday's HIGH
  SHORT: Lower Value  AND Narrow CPR AND today's OPEN < yesterday's LOW

Entry (long): nearest floor pivot strictly BELOW the open = entry level; stop =
pivot one step below; target = pivot one step above. Enter when a 5-min bar
trades down to the entry pivot. Walk bars chronologically (stop checked before
target within a bar = pessimistic). EOD close if neither hit. Short = mirror.

CAVEAT: BS-free futures-points proxy; round-trip slippage charged in points.
Still a proxy for actual option execution, but a FAIR test of the rules.
"""
from __future__ import annotations

import argparse, json, logging
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.getLogger("pb_intraday")

from pivot_boss import calc_cpr, calc_floor_pivots, classify_cpr_width

SLIPPAGE_PCT = 0.0003   # per side, charged in points


def _fetch_5m(symbol: str, days: int) -> Optional[pd.DataFrame]:
    import os
    from angel import AngelOne
    ang = AngelOne(api_key=os.getenv("API_KEY",""), client_id=os.getenv("CLIENT_ID",""),
                   password=os.getenv("PASSWORD",""), totp_secret=os.getenv("TOTP_SECRET",""))
    frm = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
    to  = datetime.now().strftime("%Y-%m-%d %H:%M")
    df = ang.get_historical_data(symbol, interval="FIVE_MINUTE", from_date=frm, to_date=to, exchange="NSE")
    if df is None or df.empty:
        return None
    df = df.rename(columns={c: c.capitalize() for c in df.columns})
    for c in ("Open","High","Low","Close"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["Open","High","Low","Close"])
    # day label
    if isinstance(df.index, pd.DatetimeIndex):
        df = df.assign(_day=[ts.date() for ts in df.index])
    else:
        for col in ("Timestamp","Datetime","Date","Time"):
            if col in df.columns:
                df = df.assign(_day=pd.to_datetime(df[col], errors="coerce").dt.date); break
        else:
            return None
    return df.reset_index(drop=True)


def _daily_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("_day")
    d = pd.DataFrame({
        "Open":  g["Open"].first(), "High": g["High"].max(),
        "Low":   g["Low"].min(),    "Close": g["Close"].last(),
    })
    return d.reset_index()


def _ladder(fp: Dict[str, float]) -> List[float]:
    return sorted(fp[k] for k in ("S3","S2","S1","P","R1","R2","R3"))


def _trades_for_symbol(symbol: str, df: pd.DataFrame) -> List[Dict]:
    daily = _daily_ohlc(df)
    days = list(daily["_day"])
    out: List[Dict] = []
    for di in range(2, len(days)):
        d = days[di]
        ph,pl,pc = daily.loc[di-1,["High","Low","Close"]]
        yh,yl,yc = daily.loc[di-2,["High","Low","Close"]]
        cpr  = calc_cpr(ph,pl,pc); fp = calc_floor_pivots(ph,pl,pc)
        yest = calc_cpr(yh,yl,yc)
        narrow = classify_cpr_width(cpr)["classification"] == "NARROW"
        higher_value = cpr["bc"] > yest["tc"]
        lower_value  = cpr["tc"] < yest["bc"]
        bars = df[df["_day"] == d].reset_index(drop=True)
        if len(bars) < 5:
            continue
        o = float(bars.loc[0, "Open"])
        lad = _ladder(fp)
        side = entry = stop = tgt = None
        if higher_value and narrow and o > ph:
            below = [x for x in lad if x < o]
            if len(below) >= 1:
                e = max(below); idx = lad.index(e)
                if 0 < idx < len(lad) - 1:
                    side, entry, stop, tgt = "BUY", e, lad[idx-1], lad[idx+1]
        elif lower_value and narrow and o < pl:
            above = [x for x in lad if x > o]
            if len(above) >= 1:
                e = min(above); idx = lad.index(e)
                if 0 < idx < len(lad) - 1:
                    side, entry, stop, tgt = "SELL", e, lad[idx+1], lad[idx-1]
        if side is None:
            continue
        # intraday state machine
        in_trade = False; exit_px = None; reason = None
        for j in range(1, len(bars)):
            hi, lo, cl = float(bars.loc[j,"High"]), float(bars.loc[j,"Low"]), float(bars.loc[j,"Close"])
            if not in_trade:
                if side == "BUY" and lo <= entry:  in_trade = True
                elif side == "SELL" and hi >= entry: in_trade = True
                continue
            if side == "BUY":
                if lo <= stop:  exit_px, reason = stop, "stop"; break
                if hi >= tgt:   exit_px, reason = tgt, "target"; break
            else:
                if hi >= stop:  exit_px, reason = stop, "stop"; break
                if lo <= tgt:   exit_px, reason = tgt, "target"; break
        if not in_trade:
            continue   # no pullback to entry → no trade
        if exit_px is None:
            exit_px, reason = float(bars.loc[len(bars)-1,"Close"]), "eod"
        risk = abs(entry - stop)
        if risk <= 0:
            continue
        gross = (exit_px - entry) if side == "BUY" else (entry - exit_px)
        cost  = entry * SLIPPAGE_PCT * 2          # round-trip slippage, in points
        pts   = gross - cost
        out.append({"symbol": symbol, "date": str(d), "side": side,
                    "entry": round(entry,1), "stop": round(stop,1), "target": round(tgt,1),
                    "exit": round(exit_px,1), "points": round(pts,1),
                    "R": round(pts / risk, 2), "reason": reason})
    return out


def _stats(trades: List[Dict], label: str) -> Dict:
    if not trades:
        return {"label": label, "trades": 0}
    R = np.array([t["R"] for t in trades], float)
    wins = R[R > 0]; loss = R[R < 0]
    pf = float(wins.sum()/abs(loss.sum())) if loss.size and loss.sum()!=0 else (99.0 if wins.size else 0.0)
    return {
        "label": label, "trades": int(len(R)),
        "win_rate_pct": round(float((R>0).mean())*100, 1),
        "avg_R": round(float(R.mean()), 3),
        "total_R": round(float(R.sum()), 2),
        "expectancy_R": round(float(R.mean()), 3),
        "profit_factor": round(pf, 2),
        "best_R": round(float(R.max()),2), "worst_R": round(float(R.min()),2),
        "targets": sum(1 for t in trades if t["reason"]=="target"),
        "stops": sum(1 for t in trades if t["reason"]=="stop"),
        "eod": sum(1 for t in trades if t["reason"]=="eod"),
        "verdict": "POSITIVE edge" if R.mean() > 0 else "NEGATIVE edge",
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Faithful Pivot Boss intraday pullback backtest")
    p.add_argument("--symbols", default="NIFTY,BANKNIFTY,FINNIFTY")
    p.add_argument("--days", type=int, default=300)
    args = p.parse_args()

    all_trades: List[Dict] = []
    per_symbol = {}
    for sym in [s.strip() for s in args.symbols.split(",") if s.strip()]:
        df = _fetch_5m(sym, args.days)
        if df is None or len(df) < 200:
            log.warning("%s: insufficient 5m data (%s)", sym, 0 if df is None else len(df)); continue
        t = _trades_for_symbol(sym, df)
        per_symbol[sym] = _stats(t, sym)
        log.info("%s: %d bars → %s", sym, len(df), per_symbol[sym])
        all_trades += t

    all_trades.sort(key=lambda x: x["date"])
    n = len(all_trades); split = int(n*0.6)
    res = {
        "pooled_full": _stats(all_trades, "pooled_full"),
        "in_sample":   _stats(all_trades[:split], "in_sample"),
        "holdout_OOS": _stats(all_trades[split:], "holdout_OOS"),
        "per_symbol":  per_symbol,
    }
    out = {"run_date": str(date.today()), "symbols": args.symbols, "n_trades": n,
           "strategy": "pivot_boss_algo_version_intraday_pullback", "results": res,
           "caveat": "futures-points proxy; round-trip slippage in points; R = profit/initial-risk."}
    json.dump(out, open("pivot_boss_intraday_backtest.json","w"), indent=2, default=str)
    print("\n==== POOLED ===="); print(json.dumps(res["pooled_full"], indent=2))
    print("\n==== OOS HOLDOUT ===="); print(json.dumps(res["holdout_OOS"], indent=2))
    print("\nSaved → pivot_boss_intraday_backtest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
