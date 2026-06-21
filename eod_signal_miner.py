#!/usr/bin/env python3
"""
eod_signal_miner.py

End-of-day full-chart signal mining.

This is a hindsight research tool, not a live trader. It scans every completed
5-minute bar, builds 1m/5m/multitimeframe features available at that bar, labels
the forward path with triple-barrier outcomes, and reports which setup ingredients
had edge.

Run:
    .venv/bin/python eod_signal_miner.py --symbols NIFTY,BANKNIFTY --days 5
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd


REPORT_JSON = "eod_signal_miner_report.json"
REPORT_MD = "EOD_SIGNAL_MINER_REPORT.md"
CANDLE_CACHE_DB = Path("candle_cache.db")
MIN_5M_BARS = 75


def _get_data_fetcher():
    try:
        from dotenv import load_dotenv

        load_dotenv(".env")
    except Exception:
        pass
    try:
        import os
        from angel import AngelOne

        angel = AngelOne(
            api_key=os.getenv("API_KEY", ""),
            client_id=os.getenv("CLIENT_ID", ""),
            password=os.getenv("PASSWORD", ""),
            totp_secret=os.getenv("TOTP_SECRET", ""),
        )
    except Exception:
        angel = None
    from data_fetcher import DataFetcher

    return DataFetcher(angel=angel, paper_trade=False)


def _col(df: pd.DataFrame, *names: str) -> Optional[str]:
    lookup = {str(c).lower(): c for c in df.columns}
    for name in names:
        if name.lower() in lookup:
            return lookup[name.lower()]
    return None


def _normalise_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or len(df) == 0:
        return pd.DataFrame()
    out = df.copy()
    if not isinstance(out.index, pd.DatetimeIndex):
        date_col = _col(out, "date", "datetime", "timestamp", "time")
        if date_col:
            out.index = pd.to_datetime(out[date_col], errors="coerce")
        else:
            out.index = pd.to_datetime(out.index, errors="coerce")
    out = out[~out.index.isna()].sort_index()
    mapping = {
        "open": _col(out, "open"),
        "high": _col(out, "high"),
        "low": _col(out, "low"),
        "close": _col(out, "close", "adj close", "adj_close"),
        "volume": _col(out, "volume", "vol"),
    }
    required = ["open", "high", "low", "close"]
    if any(mapping[k] is None for k in required):
        return pd.DataFrame()
    clean = pd.DataFrame(index=out.index)
    for target, source in mapping.items():
        if source is None:
            clean[target] = 0.0
        else:
            clean[target] = pd.to_numeric(out[source], errors="coerce").fillna(0.0)
    return clean.dropna(subset=["open", "high", "low", "close"])


def _load_cached_candles(symbol: str, interval: str, days: int) -> pd.DataFrame:
    """
    Read candle_cache.db directly and filter after timestamp parsing.

    The public cache helper filters with string comparison; mixed timestamp
    formats with timezone offsets can hide valid bars. EOD mining needs a more
    forensic loader because cached candles are the fastest post-market source.
    """
    if not CANDLE_CACHE_DB.exists():
        return pd.DataFrame()
    try:
        conn = sqlite3.connect(str(CANDLE_CACHE_DB))
        rows = conn.execute(
            """
            SELECT timestamp, open, high, low, close, volume
              FROM candles
             WHERE symbol = ? AND interval = ?
             ORDER BY timestamp
            """,
            (symbol.upper(), interval),
        ).fetchall()
        conn.close()
    except Exception:
        return pd.DataFrame()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).set_index("date").sort_index()
    if df.empty:
        return df
    cutoff = pd.Timestamp(datetime.now() - timedelta(days=max(days, 1)))
    if df.index.tz is not None:
        cutoff = cutoff.tz_localize(df.index.tz)
    recent = df[df.index >= cutoff]
    return _normalise_ohlcv(recent if len(recent) >= 5 else df.tail(max(days * 78, MIN_5M_BARS)))


def _load_datafetcher_candles(symbol: str, interval: str, days: int) -> pd.DataFrame:
    try:
        fetcher = _get_data_fetcher()
        df = fetcher.get_market_data(symbol, interval=interval, days=days)
        clean = _normalise_ohlcv(df)
        if not clean.empty:
            try:
                from candle_cache import save_candles

                save_candles(symbol, interval, clean)
            except Exception:
                pass
        return clean
    except Exception:
        return pd.DataFrame()


def _load_symbol_interval(symbol: str, interval: str, days: int) -> Tuple[pd.DataFrame, str]:
    cached = _load_cached_candles(symbol, interval, days)
    if len(cached) >= (MIN_5M_BARS if interval == "5m" else 5):
        return cached, "candle_cache"

    fetched = _load_datafetcher_candles(symbol, interval, days)
    if len(fetched) >= (MIN_5M_BARS if interval == "5m" else 5):
        return fetched, "data_fetcher"

    if len(cached) >= len(fetched):
        return cached, "candle_cache_short"
    return fetched, "data_fetcher_short"


def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    df = _normalise_ohlcv(df)
    if df.empty:
        return df
    return (
        df.resample(rule)
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna(subset=["open", "high", "low", "close"])
    )


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=min(period, len(series))).mean()


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, pd.NA)
    return (100 - 100 / (1 + rs)).fillna(50)


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean().fillna(tr.expanding().mean())


def _feature_frame(df5: pd.DataFrame, df1: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    df5 = _normalise_ohlcv(df5)
    if df5.empty:
        return df5

    out = df5.copy()
    close = out["close"]
    out["ema9"] = _ema(close, 9)
    out["ema21"] = _ema(close, 21)
    out["ema50"] = _ema(close, 50)
    out["rsi14"] = _rsi(close, 14)
    ema12 = _ema(close, 12)
    ema26 = _ema(close, 26)
    out["macd"] = ema12 - ema26
    out["macd_signal"] = _ema(out["macd"], 9)
    out["atr"] = _atr(out, 14)
    vol_ma = out["volume"].rolling(20).mean()
    out["volume_ratio"] = (out["volume"] / vol_ma.replace(0, pd.NA)).fillna(0.0)
    out["breakout_high_20"] = out["high"].rolling(20).max().shift(1)
    out["breakdown_low_20"] = out["low"].rolling(20).min().shift(1)
    out["range_pct"] = ((out["high"] - out["low"]) / out["close"].replace(0, pd.NA) * 100).fillna(0.0)

    typical = (out["high"] + out["low"] + out["close"]) / 3.0
    cum_vol = out["volume"].cumsum().replace(0, pd.NA)
    out["vwap"] = (typical * out["volume"]).cumsum() / cum_vol

    df15 = _resample(out, "15min")
    if not df15.empty:
        df15["ema9"] = _ema(df15["close"], 9)
        df15["ema21"] = _ema(df15["close"], 21)
        trend15 = pd.Series(0, index=df15.index)
        trend15[df15["ema9"] > df15["ema21"]] = 1
        trend15[df15["ema9"] < df15["ema21"]] = -1
        out["trend_15m"] = trend15.reindex(out.index, method="ffill").fillna(0)
    else:
        out["trend_15m"] = 0

    df60 = _resample(out, "60min")
    if not df60.empty:
        df60["ema9"] = _ema(df60["close"], 9)
        df60["ema21"] = _ema(df60["close"], 21)
        trend60 = pd.Series(0, index=df60.index)
        trend60[df60["ema9"] > df60["ema21"]] = 1
        trend60[df60["ema9"] < df60["ema21"]] = -1
        out["trend_1h"] = trend60.reindex(out.index, method="ffill").fillna(0)
    else:
        out["trend_1h"] = 0

    if df1 is not None and len(df1) > 0:
        one = _normalise_ohlcv(df1)
        if not one.empty:
            one["ema9"] = _ema(one["close"], 9)
            one["ema21"] = _ema(one["close"], 21)
            micro = pd.Series(0, index=one.index)
            micro[one["ema9"] > one["ema21"]] = 1
            micro[one["ema9"] < one["ema21"]] = -1
            out["micro_1m_trend"] = micro.reindex(out.index, method="ffill").fillna(0)
            out["micro_1m_volume_ratio"] = (
                one["volume"] / one["volume"].rolling(20).mean().replace(0, pd.NA)
            ).reindex(out.index, method="ffill").fillna(0)
        else:
            out["micro_1m_trend"] = 0
            out["micro_1m_volume_ratio"] = 0
    else:
        out["micro_1m_trend"] = 0
        out["micro_1m_volume_ratio"] = 0

    return out


def _candidate_at(row: pd.Series) -> Optional[Dict[str, Any]]:
    close = _num(row.get("close"), 0.0)
    if close <= 0:
        return None

    bullish = []
    bearish = []
    if _num(row.get("ema9"), 0.0) > _num(row.get("ema21"), 0.0):
        bullish.append("ema9_gt_ema21")
    else:
        bearish.append("ema9_lt_ema21")
    if _num(row.get("ema21"), 0.0) > _num(row.get("ema50"), 0.0):
        bullish.append("ema21_gt_ema50")
    else:
        bearish.append("ema21_lt_ema50")
    if _num(row.get("macd"), 0.0) > _num(row.get("macd_signal"), 0.0):
        bullish.append("macd_positive")
    else:
        bearish.append("macd_negative")
    if close > _num(row.get("vwap"), close):
        bullish.append("above_vwap")
    else:
        bearish.append("below_vwap")
    if int(_num(row.get("trend_15m"), 0.0)) > 0:
        bullish.append("trend_15m_up")
    elif int(_num(row.get("trend_15m"), 0.0)) < 0:
        bearish.append("trend_15m_down")
    if int(_num(row.get("trend_1h"), 0.0)) > 0:
        bullish.append("trend_1h_up")
    elif int(_num(row.get("trend_1h"), 0.0)) < 0:
        bearish.append("trend_1h_down")
    if int(_num(row.get("micro_1m_trend"), 0.0)) > 0:
        bullish.append("micro_1m_up")
    elif int(_num(row.get("micro_1m_trend"), 0.0)) < 0:
        bearish.append("micro_1m_down")

    vol_ratio = _num(row.get("volume_ratio"), 0.0)
    micro_vol = _num(row.get("micro_1m_volume_ratio"), 0.0)
    high_break = close > _num(row.get("breakout_high_20"), math.inf)
    low_break = close < _num(row.get("breakdown_low_20"), -math.inf)
    rsi = _num(row.get("rsi14"), 50.0)

    if high_break:
        bullish.append("breakout_20")
    if low_break:
        bearish.append("breakdown_20")
    if vol_ratio >= 1.2:
        bullish.append("volume_expansion")
        bearish.append("volume_expansion")
    if micro_vol >= 1.2:
        bullish.append("micro_volume_expansion")
        bearish.append("micro_volume_expansion")
    if rsi >= 55:
        bullish.append("rsi_above_55")
    elif rsi <= 45:
        bearish.append("rsi_below_45")

    bull_score = len(set(bullish))
    bear_score = len(set(bearish))
    if max(bull_score, bear_score) < 4 or abs(bull_score - bear_score) < 2:
        return None

    side = "BUY" if bull_score > bear_score else "SELL"
    factors = sorted(set(bullish if side == "BUY" else bearish))
    setup = "mtf_momentum"
    if "breakout_20" in factors or "breakdown_20" in factors:
        setup = "volume_breakout" if "volume_expansion" in factors else "range_break"
    elif "above_vwap" in factors or "below_vwap" in factors:
        setup = "vwap_trend"

    return {
        "side": side,
        "setup": setup,
        "score": bull_score if side == "BUY" else bear_score,
        "opposition": bear_score if side == "BUY" else bull_score,
        "factors": factors,
        "entry_price": close,
        "volume_ratio": round(vol_ratio, 3),
        "micro_volume_ratio": round(micro_vol, 3),
        "rsi14": round(rsi, 2),
    }


def _label_candidate(df: pd.DataFrame, idx: int, side: str, entry_price: float) -> Tuple[int, float]:
    from triple_barrier import get_dynamic_barriers, label_triple_barrier

    atr = float(df["atr"].iloc[idx] if "atr" in df.columns else 0.0)
    if atr <= 0:
        atr = float((df["high"] - df["low"]).tail(14).mean() or 0.0)
    target_pct, stop_pct, max_bars = get_dynamic_barriers(atr, entry_price)
    label = label_triple_barrier(df, idx, entry_price, target_pct, stop_pct, max_bars, side)
    end_idx = min(idx + max_bars, len(df) - 1)
    outcome = float(df["close"].iloc[end_idx]) if end_idx > idx else entry_price
    sign = 1.0 if side == "BUY" else -1.0
    ret_pct = sign * (outcome - entry_price) / max(entry_price, 1e-9) * 100.0
    return int(label), round(ret_pct, 4)


def mine_symbol(
    symbol: str,
    df5: pd.DataFrame,
    *,
    df1: Optional[pd.DataFrame] = None,
    warmup: int = 60,
    min_score: int = 4,
) -> Dict[str, Any]:
    feat = _feature_frame(df5, df1=df1)
    if feat.empty or len(feat) <= warmup + 15:
        return {"symbol": symbol, "ok": False, "reason": "insufficient_bars", "candidates": []}

    candidates = []
    last_i = len(feat) - 13
    for i in range(max(warmup, 1), max(last_i, warmup)):
        candidate = _candidate_at(feat.iloc[i])
        if not candidate or int(candidate.get("score", 0) or 0) < min_score:
            continue
        label, ret_pct = _label_candidate(feat, i, candidate["side"], float(candidate["entry_price"]))
        candidates.append({
            "symbol": symbol,
            "time": str(feat.index[i]),
            "label": label,
            "return_pct": ret_pct,
            **candidate,
        })

    return {
        "symbol": symbol,
        "ok": True,
        "bars_5m": len(feat),
        "candidates": candidates,
    }


def _summarise(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(items)
    wins = sum(1 for x in items if int(x.get("label", 0) or 0) == 1)
    losses = sum(1 for x in items if int(x.get("label", 0) or 0) == -1)
    timeouts = sum(1 for x in items if int(x.get("label", 0) or 0) == 0)
    avg_ret = sum(float(x.get("return_pct", 0.0) or 0.0) for x in items) / max(n, 1)
    return {
        "n": n,
        "wins": wins,
        "losses": losses,
        "timeouts": timeouts,
        "target_rate": round(wins / max(n, 1), 4),
        "win_rate_ex_timeout": round(wins / max(wins + losses, 1), 4),
        "avg_return_pct": round(avg_ret, 4),
    }


def _group(items: List[Dict[str, Any]], key_fn, limit: int = 20) -> List[Dict[str, Any]]:
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for item in items:
        buckets[str(key_fn(item) or "blank")].append(item)
    rows = [{"key": key, **_summarise(vals)} for key, vals in buckets.items()]
    rows.sort(key=lambda x: (x["avg_return_pct"], x["target_rate"], x["n"]), reverse=True)
    return rows[:limit]


def build_report(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    candidates = [c for r in results for c in r.get("candidates", [])]
    factor_rows = []
    all_factors = sorted({f for c in candidates for f in c.get("factors", [])})
    for factor in all_factors:
        subset = [c for c in candidates if factor in c.get("factors", [])]
        factor_rows.append({"key": factor, **_summarise(subset)})
    factor_rows.sort(key=lambda x: (x["avg_return_pct"], x["target_rate"], x["n"]), reverse=True)

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "symbols_seen": len(results),
        "symbols_ok": sum(1 for r in results if r.get("ok")),
        "summary": _summarise(candidates),
        "by_setup": _group(candidates, lambda x: x.get("setup")),
        "by_side": _group(candidates, lambda x: x.get("side")),
        "by_symbol": _group(candidates, lambda x: x.get("symbol")),
        "by_factor": factor_rows[:30],
        "top_candidates": sorted(
            candidates,
            key=lambda x: (x.get("label", 0), x.get("return_pct", 0), x.get("score", 0)),
            reverse=True,
        )[:50],
        "symbol_results": [
            {k: v for k, v in r.items() if k != "candidates"} | {"candidate_count": len(r.get("candidates", []))}
            for r in results
        ],
    }


def render_markdown(report: Dict[str, Any]) -> str:
    summary = report.get("summary", {})
    lines = [
        "# EOD Signal Miner Report",
        "",
        f"- Generated: `{report.get('generated_at')}`",
        f"- Symbols ok: `{report.get('symbols_ok', 0)}` / `{report.get('symbols_seen', 0)}`",
        f"- Candidates: `{summary.get('n', 0)}`",
        f"- Target/loss/timeout: `{summary.get('wins', 0)}` / `{summary.get('losses', 0)}` / `{summary.get('timeouts', 0)}`",
        f"- Avg return pct: `{summary.get('avg_return_pct', 0)}`",
        "",
        "## Best Setups",
        "",
    ]
    for row in report.get("by_setup", [])[:10]:
        lines.append(
            f"- `{row.get('key')}` n `{row.get('n')}` target_rate `{row.get('target_rate')}` "
            f"avg_return `{row.get('avg_return_pct')}`"
        )
    if not report.get("by_setup"):
        lines.append("- none")
    lines.extend(["", "## Best Factors", ""])
    for row in report.get("by_factor", [])[:15]:
        lines.append(
            f"- `{row.get('key')}` n `{row.get('n')}` target_rate `{row.get('target_rate')}` "
            f"avg_return `{row.get('avg_return_pct')}`"
        )
    if not report.get("by_factor"):
        lines.append("- none")
    lines.extend(["", "## Top Candidates", ""])
    for row in report.get("top_candidates", [])[:15]:
        lines.append(
            f"- `{row.get('symbol')}` `{row.get('time')}` `{row.get('side')}` "
            f"`{row.get('setup')}` label `{row.get('label')}` return `{row.get('return_pct')}` "
            f"score `{row.get('score')}`"
        )
    if not report.get("top_candidates"):
        lines.append("- none")
    lines.extend(["", "## Data Coverage", ""])
    for row in report.get("symbol_results", [])[:30]:
        data = row.get("data", {}) if isinstance(row.get("data"), dict) else {}
        lines.append(
            f"- `{row.get('symbol')}` ok `{bool(row.get('ok'))}` "
            f"reason `{row.get('reason', '') or 'ok'}` "
            f"5m `{data.get('bars_5m', row.get('bars_5m', 0))}` "
            f"source `{data.get('source_5m', '')}` "
            f"1m `{data.get('bars_1m', 0)}` "
            f"source `{data.get('source_1m', '')}` "
            f"candidates `{row.get('candidate_count', 0)}`"
        )
    if not report.get("symbol_results"):
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


def _fetch_symbol_data(symbol: str, days: int) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
    df5, src5 = _load_symbol_interval(symbol, "5m", days)
    df1, src1 = _load_symbol_interval(symbol, "1m", min(days, 7))
    return df5, df1, {
        "source_5m": src5,
        "bars_5m": int(len(df5)),
        "source_1m": src1,
        "bars_1m": int(len(df1)),
    }


def run_miner(
    *,
    symbols: List[str],
    days: int = 5,
    max_symbols: int = 20,
    write: bool = True,
) -> Dict[str, Any]:
    results = []
    for symbol in symbols[:max_symbols]:
        try:
            df5, df1, data_meta = _fetch_symbol_data(symbol, days)
            result = mine_symbol(symbol, df5, df1=df1)
            result["data"] = data_meta
            if not result.get("ok") and result.get("reason") == "insufficient_bars":
                result["reason"] = f"insufficient_5m_bars:{data_meta.get('bars_5m', 0)}"
            results.append(result)
        except Exception as exc:
            results.append({"symbol": symbol, "ok": False, "reason": str(exc), "candidates": []})
    report = build_report(results)
    if write:
        Path(REPORT_JSON).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        Path(REPORT_MD).write_text(render_markdown(report), encoding="utf-8")
    return report


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="NIFTY,BANKNIFTY,SENSEX")
    parser.add_argument("--days", type=int, default=5)
    parser.add_argument("--max-symbols", type=int, default=20)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    report = run_miner(symbols=symbols, days=args.days, max_symbols=args.max_symbols, write=not args.no_write)
    print(render_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
