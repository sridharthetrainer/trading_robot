"""
ensemble_validate.py — OOS backtest of the CONFLUENCE ENSEMBLE (generate_signal)
on real NIFTY daily bars.

This is a STANDALONE harness: it monkeypatches signal_engine's network-coupled
modifiers (news/omnisource, NSE cost-of-carry, OI tracker) to NEUTRAL *in this
process only* — the live signal_engine source is never modified. That makes a
per-bar backtest fast enough to run, but it ALSO means this measures the
chart/OHLC CORE of the ensemble, NOT the full live ensemble (which has live
OI/news/option-chain context). Read the verdict with that caveat.

Split: earliest 60% of bars = in-sample read, latest 40% = OOS holdout. The
ensemble weights are fixed (not fit here), so the holdout is a clean OOS read.

CAVEATS: daily bars (few trades); network context stubbed to neutral; entry at
signal-bar close, ATR stop/target, 12-bar time exit; realistic charges applied.
"""
from __future__ import annotations

import json
import time
import types
import logging
from datetime import date
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.getLogger("ensemble_validate")

# ── Neutralise network-coupled modifiers (process-local; live code untouched) ──
import signal_engine as se
se.get_omnisource_score_modifier = lambda *a, **k: 0.0
se.get_cost_of_carry_signal      = lambda *a, **k: {"signal": "NEUTRAL", "score": 0.0}
try:
    import oi_tracker
    oi_tracker.get_oi_tracker = lambda *a, **k: types.SimpleNamespace(
        get_current_direction=lambda *a, **k: None)
except Exception as e:
    log.info("oi patch skipped: %s", e)

from signal_engine import generate_signal           # noqa: E402
import autonomous_backtest as ab                     # noqa: E402

MIN_SCORE = 3.5
TIME_EXIT_BARS = 12


def _charges_local(entry: float, exit_: float, qty: int):
    """Self-contained NSE futures-proxy charges → (gross, net). Avoids
    autonomous_backtest._charges, which NameErrors on an undefined `symbol`."""
    gross = (exit_ - entry) * qty
    e_tv, x_tv = entry * qty, exit_ * qty
    brok  = 2 * 20.0
    stt   = x_tv * 0.0002            # futures sell-side 0.02%
    exch  = (e_tv + x_tv) * 0.0000345
    sebi  = (e_tv + x_tv) * 0.000001
    gst   = (brok + exch + sebi) * 0.18
    stamp = e_tv * 0.00002
    total = brok + stt + exch + sebi + gst + stamp
    return round(gross, 2), round(gross - total, 2)


def _atr(df: pd.DataFrame, p: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(p, min_periods=1).mean()


def _backtest_segment(df: pd.DataFrame, df_htf: Optional[pd.DataFrame], symbol: str,
                      i0: int, i1: int, stop_mult: float = 1.5, tgt_mult: float = 2.5,
                      label: str = "") -> Dict:
    """Walk bars [i0,i1) of df, trade ensemble signals, return metrics."""
    atr = _atr(df)
    qty = ab.LOT_SIZE
    trades: List[Dict] = []
    open_t = None
    capital = float(ab.CAPITAL)
    t_start = time.time()
    for i in range(i0, i1):
        bar = df.iloc[i]
        close = float(bar["close"]); high = float(bar["high"]); low = float(bar["low"])
        a = float(atr.iloc[i]) if not np.isnan(atr.iloc[i]) else close * 0.005

        if open_t:
            open_t["bars"] += 1
            ep = er = None
            if open_t["side"] == "BUY":
                if low <= open_t["stop"]:  ep, er = open_t["stop"], "stop"
                elif high >= open_t["tgt"]: ep, er = open_t["tgt"], "target"
            else:
                if high >= open_t["stop"]:  ep, er = open_t["stop"], "stop"
                elif low <= open_t["tgt"]:  ep, er = open_t["tgt"], "target"
            if ep is None and open_t["bars"] >= TIME_EXIT_BARS:
                ep, er = close, "time"
            if ep is not None:
                gross, net = _charges_local(open_t["entry"], ep, qty)
                trades.append({"net": net, "reason": er, "side": open_t["side"]})
                capital += net
                open_t = None

        if open_t is None:
            try:
                sig = generate_signal(
                    df=df.iloc[:i + 1],
                    df_htf=df_htf.iloc[:i // 3 + 1] if df_htf is not None else None,
                    symbol=symbol, capital=capital, config=None)
                if sig and sig.get("direction") and float(sig.get("score", 0)) >= MIN_SCORE:
                    d = sig["direction"]
                    stop = close - a * stop_mult if d == "BUY" else close + a * stop_mult
                    tgt  = close + a * tgt_mult  if d == "BUY" else close - a * tgt_mult
                    open_t = {"entry": close, "stop": stop, "tgt": tgt, "side": d, "bars": 0}
            except Exception:
                pass
        if (i - i0) % 25 == 0:
            log.info("[%s] bar %d/%d  trades=%d  (%.0fs)", label, i - i0, i1 - i0,
                     len(trades), time.time() - t_start)

    if not trades:
        return {"label": label, "trades": 0, "verdict": "NO TRADES"}
    pnls = np.array([t["net"] for t in trades], float)
    wins = pnls[pnls > 0]; loss = pnls[pnls <= 0]
    pf = float(wins.sum() / abs(loss.sum())) if loss.size and loss.sum() != 0 else (99.0 if wins.size else 0.0)
    sharpe = float(pnls.mean() / pnls.std() * np.sqrt(min(len(pnls), 252))) if pnls.std() > 0 else 0.0
    total = float(pnls.sum())
    return {
        "label": label, "trades": int(len(pnls)),
        "win_rate_pct": round(float((pnls > 0).mean()) * 100, 1),
        "total_pnl": round(total, 0), "avg_pnl": round(float(pnls.mean()), 0),
        "profit_factor": round(pf, 2), "pseudo_sharpe": round(sharpe, 2),
        "return_on_capital_pct": round(total / float(ab.CAPITAL) * 100, 1),
        "verdict": "POSITIVE" if total > 0 else "NEGATIVE",
    }


def main():
    symbol = "NIFTY"
    df = ab._fetch(symbol, days=300)
    df_htf = ab._fetch_htf(symbol, days=300)
    if df is None or len(df) < 120:
        log.error("insufficient data"); return
    df = df.reset_index(drop=True)
    n = len(df)
    split = int(n * 0.6)
    warmup = 30 if n < 150 else 100
    log.info("bars=%d  in-sample=[%d,%d)  holdout=[%d,%d)", n, warmup, split, split, n)

    dev = _backtest_segment(df, df_htf, symbol, warmup, split, label="in_sample")
    log.info("in_sample: %s", dev)
    oos = _backtest_segment(df, df_htf, symbol, max(split, warmup), n, label="holdout_OOS")
    log.info("holdout_OOS: %s", oos)

    out = {
        "run_date": str(date.today()), "symbol": symbol, "bars": n,
        "min_score": MIN_SCORE, "in_sample": dev, "holdout_OOS": oos,
        "caveats": ("Network modifiers (OI/news/cost-of-carry) STUBBED to neutral, "
                    "so this measures the chart/OHLC core of the ensemble, not the "
                    "full live ensemble. Daily bars; small sample; charges applied."),
    }
    with open("ensemble_validation.json", "w") as fh:
        json.dump(out, fh, indent=2, default=str)
    log.info("saved -> ensemble_validation.json")
    print(json.dumps({"in_sample": dev, "holdout_OOS": oos}, indent=2))


if __name__ == "__main__":
    main()
