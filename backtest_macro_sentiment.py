#!/usr/bin/env python3
"""
Validate macro_global_profit_engine snapshots against NIFTY next-day movement.

Outputs gap accuracy, trend accuracy, false bullish/bearish rates, no-trade
quality, profit-quality bands, component usefulness and threshold suggestions.
"""
from __future__ import annotations

import argparse
import math
import sqlite3
from typing import Dict, List, Tuple

MACRO_DB = "signal_log.db"
CANDLE_DB = "candle_cache.db"
MIN_DAYS = 15


def _safe_float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _label(score: float) -> str:
    if score > 35:
        return "BULLISH"
    if score < -35:
        return "BEARISH"
    if -20 <= score <= 20:
        return "NO_TRADE_ZONE"
    return "NEUTRAL"


def load_macro(db_path: str = MACRO_DB) -> List[Dict[str, object]]:
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                """
                SELECT substr(timestamp,1,10) d,
                       AVG(final_global_score),
                       AVG(gift_nifty_change),
                       MAX(gap_prediction),
                       AVG(gap_probability),
                       MAX(nifty_bias),
                       MAX(market_regime),
                       AVG(profit_quality_score),
                       MAX(allowed_trade_type),
                       MAX(no_trade_reason),
                       AVG(us_futures_score),
                       AVG(asia_score),
                       AVG(europe_score),
                       AVG(commodity_score),
                       AVG(currency_score),
                       AVG(vix_score),
                       AVG(bond_yield_score)
                FROM macro_global_sentiment
                GROUP BY d
                ORDER BY d
                """
            ).fetchall()
    except Exception:
        return []
    out: List[Dict[str, object]] = []
    for r in rows:
        score = _safe_float(r[1])
        out.append({
            "date": str(r[0]),
            "score": score,
            "bias": _label(score),
            "gift": _safe_float(r[2]),
            "gap_prediction": str(r[3] or ""),
            "gap_probability": _safe_float(r[4]),
            "nifty_bias": str(r[5] or ""),
            "market_regime": str(r[6] or ""),
            "profit_quality_score": _safe_float(r[7]),
            "allowed_trade_type": str(r[8] or ""),
            "no_trade_reason": str(r[9] or ""),
            "components": {
                "us_futures": _safe_float(r[10]),
                "asia": _safe_float(r[11]),
                "europe": _safe_float(r[12]),
                "commodity": _safe_float(r[13]),
                "currency": _safe_float(r[14]),
                "vix": _safe_float(r[15]),
                "bond_yield": _safe_float(r[16]),
            },
        })
    return out


def load_nifty_daily(db_path: str = CANDLE_DB) -> Dict[str, Dict[str, float]]:
    """Load daily NIFTY bars from candle_cache.db, with participant_oi fallback."""
    for symbol in ("NIFTY", "NIFTY 50", "^NSEI"):
        try:
            with sqlite3.connect(db_path) as conn:
                rows = conn.execute(
                    """
                    SELECT substr(timestamp,1,10) d,
                           MIN(open), MAX(high), MIN(low), MAX(close)
                    FROM candles
                    WHERE upper(symbol)=? AND interval IN ('1d','1D','DAY')
                    GROUP BY d ORDER BY d
                    """,
                    (symbol.upper(),),
                ).fetchall()
            if rows:
                return {
                    str(d): {"open": _safe_float(o), "high": _safe_float(h),
                             "low": _safe_float(l), "close": _safe_float(c)}
                    for d, o, h, l, c in rows
                }
        except Exception:
            pass
    try:
        with sqlite3.connect("participant_oi.db") as conn:
            rows = conn.execute("SELECT date, close FROM nifty_daily WHERE close>0 ORDER BY date").fetchall()
        return {str(d): {"open": _safe_float(c), "high": _safe_float(c),
                         "low": _safe_float(c), "close": _safe_float(c)}
                for d, c in rows}
    except Exception:
        return {}


def _spearman(xs: List[float], ys: List[float]) -> float:
    n = len(xs)
    if n < 3:
        return 0.0
    order_x = sorted(range(n), key=lambda i: xs[i])
    order_y = sorted(range(n), key=lambda i: ys[i])
    rx = [0.0] * n
    ry = [0.0] * n
    for pos, i in enumerate(order_x):
        rx[i] = pos
    for pos, i in enumerate(order_y):
        ry[i] = pos
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    vy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return cov / (vx * vy) if vx and vy else 0.0


def _accuracy(hits: List[bool]) -> float:
    return round(100.0 * sum(1 for h in hits if h) / len(hits), 1) if hits else 0.0


def _threshold_suggestions(samples: List[Dict[str, object]]) -> Dict[str, object]:
    best = {"threshold": None, "accuracy": 0.0, "trades": 0}
    for th in (20, 25, 30, 35, 40, 45, 50, 55):
        hits = []
        for s in samples:
            score = _safe_float(s["score"])
            ret = _safe_float(s["next_close_return"])
            if abs(score) >= th:
                hits.append((score > 0 and ret > 0) or (score < 0 and ret < 0))
        acc = _accuracy(hits)
        if len(hits) >= 5 and acc > best["accuracy"]:
            best = {"threshold": th, "accuracy": acc, "trades": len(hits)}
    return best


def run(min_days: int = MIN_DAYS) -> Dict[str, object]:
    macro = load_macro()
    bars = load_nifty_daily()
    if not macro:
        return {"status": "INSUFFICIENT_DATA", "reason": "no macro_global_sentiment rows", "days": 0}
    if not bars:
        return {"status": "ERROR", "reason": "no NIFTY daily bars found"}

    dates = sorted(bars)
    next_day = {dates[i]: dates[i + 1] for i in range(len(dates) - 1)}
    samples: List[Dict[str, object]] = []
    for row in macro:
        d = str(row["date"])
        nd = next_day.get(d)
        if not nd:
            continue
        today = bars[d]
        nxt = bars[nd]
        prev_close = _safe_float(today["close"])
        if prev_close <= 0:
            continue
        row = dict(row)
        row["next_date"] = nd
        row["next_gap_return"] = (_safe_float(nxt["open"]) / prev_close - 1.0) if _safe_float(nxt["open"]) else 0.0
        row["next_close_return"] = _safe_float(nxt["close"]) / prev_close - 1.0
        row["next_intraday_return"] = (_safe_float(nxt["close"]) / _safe_float(nxt["open"]) - 1.0) if _safe_float(nxt["open"]) else 0.0
        samples.append(row)

    n = len(samples)
    if n < min_days:
        return {"status": "INSUFFICIENT_DATA", "reason": f"only {n} aligned days; need >= {min_days}", "days": n}

    gap_hits: List[bool] = []
    trend_hits: List[bool] = []
    false_bullish = false_bearish = 0
    bull_n = bear_n = 0
    no_trade_hits: List[bool] = []
    pqs_hi: List[bool] = []
    pqs_low: List[bool] = []
    scores: List[float] = []
    next_returns: List[float] = []
    component_ic: Dict[str, float] = {}

    for s in samples:
        score = _safe_float(s["score"])
        ret = _safe_float(s["next_close_return"])
        gap_ret = _safe_float(s["next_gap_return"])
        scores.append(score)
        next_returns.append(ret)
        if s["gap_prediction"] == "GAP_UP":
            gap_hits.append(gap_ret > 0)
        elif s["gap_prediction"] == "GAP_DOWN":
            gap_hits.append(gap_ret < 0)
        elif s["gap_prediction"] == "FLAT":
            gap_hits.append(abs(gap_ret) <= 0.0025)
        if score > 35:
            bull_n += 1
            hit = ret > 0
            trend_hits.append(hit)
            false_bullish += 0 if hit else 1
        elif score < -35:
            bear_n += 1
            hit = ret < 0
            trend_hits.append(hit)
            false_bearish += 0 if hit else 1
        if str(s.get("allowed_trade_type")) in {"NONE", ""} or str(s.get("no_trade_reason")):
            no_trade_hits.append(abs(ret) <= 0.004)
        if _safe_float(s.get("profit_quality_score")) >= 75:
            pqs_hi.append((score > 0 and ret > 0) or (score < 0 and ret < 0))
        else:
            pqs_low.append((score > 0 and ret > 0) or (score < 0 and ret < 0))

    for name in samples[0]["components"].keys():
        vals = [_safe_float(s["components"].get(name)) for s in samples]  # type: ignore[index]
        component_ic[name] = round(_spearman(vals, next_returns), 3)

    return {
        "status": "OK",
        "days": n,
        "gap_accuracy": _accuracy(gap_hits),
        "intraday_trend_accuracy": _accuracy(trend_hits),
        "false_bullish": {"count": false_bullish, "rate": round(100 * false_bullish / bull_n, 1) if bull_n else 0.0},
        "false_bearish": {"count": false_bearish, "rate": round(100 * false_bearish / bear_n, 1) if bear_n else 0.0},
        "no_trade_success": _accuracy(no_trade_hits),
        "profit_quality_performance": {
            "pqs_ge_75_accuracy": _accuracy(pqs_hi),
            "pqs_lt_75_accuracy": _accuracy(pqs_low),
            "high_quality_count": len(pqs_hi),
        },
        "indicator_usefulness_ic": component_ic,
        "global_score_ic": round(_spearman(scores, next_returns), 3),
        "best_thresholds": _threshold_suggestions(samples),
        "note": "Validation only. Keep thresholds data-gated until enough live/paper samples accrue.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest macro sentiment snapshots")
    parser.add_argument("--min-days", type=int, default=MIN_DAYS)
    args = parser.parse_args()
    res = run(min_days=args.min_days)
    print("\nMACRO SENTIMENT BACKTEST")
    print("-" * 50)
    for k, v in res.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
