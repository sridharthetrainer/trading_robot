#!/usr/bin/env python3
"""
eod_option_structure_miner.py

End-of-day option structure learning.

This mines completed 5-minute index charts for the best intraday legs, labels
HH/HL and LH/LL structure around those legs, attaches option-chain OI/volume
context available near the entry time, and stores rows that later models can
learn from.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd

from eod_signal_miner import _atr, _ema, _load_cached_candles, _load_symbol_interval, _normalise_ohlcv, _resample, _rsi


DB_PATH = "option_structure_training.db"
REPORT_JSON = "eod_option_structure_report.json"
EDGES_JSON = "option_structure_edges.json"
OPTION_SNAPSHOT_DB = "option_chain_snapshots.db"
DEFAULT_SYMBOLS = ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"]


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def _ts(value: Any) -> float:
    try:
        return pd.Timestamp(value).timestamp()
    except Exception:
        return 0.0


def _connect(path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS option_structure_legs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            session_date TEXT NOT NULL,
            direction TEXT NOT NULL,
            start_ts TEXT NOT NULL,
            end_ts TEXT NOT NULL,
            entry_price REAL DEFAULT 0,
            exit_price REAL DEFAULT 0,
            max_profit_pts REAL DEFAULT 0,
            max_profit_pct REAL DEFAULT 0,
            max_adverse_pct REAL DEFAULT 0,
            bars INTEGER DEFAULT 0,
            structure_start TEXT DEFAULT '',
            structure_end TEXT DEFAULT '',
            leg_type TEXT DEFAULT '',
            hh_count INTEGER DEFAULT 0,
            hl_count INTEGER DEFAULT 0,
            lh_count INTEGER DEFAULT 0,
            ll_count INTEGER DEFAULT 0,
            bos INTEGER DEFAULT 0,
            choch INTEGER DEFAULT 0,
            vwap_state TEXT DEFAULT '',
            vwap_cross INTEGER DEFAULT 0,
            volume_ratio REAL DEFAULT 0,
            rsi14 REAL DEFAULT 0,
            atr_pct REAL DEFAULT 0,
            ema_state TEXT DEFAULT '',
            trend_15m INTEGER DEFAULT 0,
            range_position REAL DEFAULT 0,
            oi_pcr REAL DEFAULT 0,
            oi_pcr_change REAL DEFAULT 0,
            nearest_strike REAL DEFAULT 0,
            top_ce_volume_strike REAL DEFAULT 0,
            top_pe_volume_strike REAL DEFAULT 0,
            top_ce_oi_strike REAL DEFAULT 0,
            top_pe_oi_strike REAL DEFAULT 0,
            score REAL DEFAULT 0,
            features_json TEXT DEFAULT '{}',
            created_at TEXT NOT NULL,
            UNIQUE(symbol, session_date, direction, start_ts, end_ts)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_osl_symbol_date ON option_structure_legs(symbol, session_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_osl_score ON option_structure_legs(score DESC)")
    return conn


def _session_date(index_value: Any) -> str:
    return pd.Timestamp(index_value).strftime("%Y-%m-%d")


def _feature_frame(df5: pd.DataFrame) -> pd.DataFrame:
    out = _normalise_ohlcv(df5)
    if out.empty:
        return out
    close = out["close"]
    out["ema9"] = _ema(close, 9)
    out["ema21"] = _ema(close, 21)
    out["ema50"] = _ema(close, 50)
    out["rsi14"] = _rsi(close, 14)
    out["atr"] = _atr(out, 14)
    out["atr_pct"] = (out["atr"] / close.replace(0, pd.NA) * 100).fillna(0.0)
    vol_ma = out["volume"].rolling(20, min_periods=3).mean()
    out["volume_ratio"] = (out["volume"] / vol_ma.replace(0, pd.NA)).fillna(0.0)
    typical = (out["high"] + out["low"] + out["close"]) / 3.0
    cum_vol = out["volume"].cumsum().replace(0, pd.NA)
    out["vwap"] = (typical * out["volume"]).cumsum() / cum_vol
    out["range_high_20"] = out["high"].rolling(20, min_periods=3).max().shift(1)
    out["range_low_20"] = out["low"].rolling(20, min_periods=3).min().shift(1)
    denom = (out["range_high_20"] - out["range_low_20"]).replace(0, pd.NA)
    out["range_position"] = ((close - out["range_low_20"]) / denom).clip(0, 1).fillna(0.5)
    df15 = (
        out.resample("15min")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna(subset=["open", "high", "low", "close"])
    )
    if not df15.empty:
        df15["ema9"] = _ema(df15["close"], 9)
        df15["ema21"] = _ema(df15["close"], 21)
        trend = pd.Series(0, index=df15.index)
        trend[df15["ema9"] > df15["ema21"]] = 1
        trend[df15["ema9"] < df15["ema21"]] = -1
        out["trend_15m"] = trend.reindex(out.index, method="ffill").fillna(0)
    else:
        out["trend_15m"] = 0
    return out


def _valid_price_rows(df: pd.DataFrame) -> pd.DataFrame:
    clean = _normalise_ohlcv(df)
    if clean.empty:
        return clean
    return clean[(clean["open"] > 0) & (clean["high"] > 0) & (clean["low"] > 0) & (clean["close"] > 0)]


def _load_structure_candles(symbol: str, days: int, allow_fetch: bool = False) -> Tuple[pd.DataFrame, str]:
    cached_5m = _valid_price_rows(_load_cached_candles(symbol, "5m", days))
    if len(cached_5m) >= 30:
        return cached_5m, "candle_cache_5m"

    cached_1m = _valid_price_rows(_load_cached_candles(symbol, "1m", days))
    if len(cached_1m) >= 30:
        resampled = _valid_price_rows(_resample(cached_1m, "5min"))
        if len(resampled) >= 20:
            return resampled, "candle_cache_1m_resampled"

    if allow_fetch:
        fetched, source = _load_symbol_interval(symbol, "5m", days)
        fetched = _valid_price_rows(fetched)
        if len(fetched) >= 20:
            return fetched, source
    if len(cached_5m) >= len(cached_1m):
        return cached_5m, "candle_cache_5m_short_or_zero"
    return cached_1m, "candle_cache_1m_short_or_zero"


def _swings(df: pd.DataFrame, lookback: int = 2) -> List[Dict[str, Any]]:
    swings: List[Dict[str, Any]] = []
    if len(df) < lookback * 2 + 3:
        return swings
    closes = df["close"].tolist()
    highs = df["high"].tolist()
    lows = df["low"].tolist()
    last_high: Optional[float] = None
    last_low: Optional[float] = None
    for i in range(lookback, len(df) - lookback):
        window_c = closes[i - lookback : i + lookback + 1]
        ts = df.index[i]
        if closes[i] == max(window_c) and window_c.count(closes[i]) == 1:
            label = "HH" if last_high is not None and highs[i] > last_high else "LH" if last_high is not None else "H"
            last_high = highs[i]
            swings.append({"idx": i, "ts": ts, "kind": "H", "price": float(highs[i]), "label": label})
        if closes[i] == min(window_c) and window_c.count(closes[i]) == 1:
            label = "HL" if last_low is not None and lows[i] > last_low else "LL" if last_low is not None else "L"
            last_low = lows[i]
            swings.append({"idx": i, "ts": ts, "kind": "L", "price": float(lows[i]), "label": label})
    return sorted(swings, key=lambda x: (x["idx"], 0 if x["kind"] == "L" else 1))


def _structure_counts(swings: List[Dict[str, Any]], start_idx: int, end_idx: int) -> Dict[str, int]:
    labels = [s.get("label", "") for s in swings if start_idx <= int(s.get("idx", 0)) <= end_idx]
    counts = Counter(labels)
    return {
        "hh_count": int(counts.get("HH", 0)),
        "hl_count": int(counts.get("HL", 0)),
        "lh_count": int(counts.get("LH", 0)),
        "ll_count": int(counts.get("LL", 0)),
    }


def _classify_leg(direction: str, counts: Dict[str, int], entry: pd.Series, exit_row: pd.Series) -> Tuple[str, int, int]:
    close = _num(entry.get("close"), 0.0)
    vwap = _num(entry.get("vwap"), close)
    exit_close = _num(exit_row.get("close"), close)
    exit_vwap = _num(exit_row.get("vwap"), exit_close)
    vwap_cross = int((close <= vwap < exit_close) or (close >= vwap > exit_close))
    if direction == "BUY":
        if counts["hh_count"] + counts["hl_count"] >= 2:
            return "trend_continuation_hh_hl", 1, vwap_cross
        if counts["ll_count"] > 0 and exit_close > exit_vwap:
            return "choch_reversal_bull", 0, vwap_cross
        if vwap_cross:
            return "vwap_reclaim", 0, vwap_cross
        return "bull_leg", 0, vwap_cross
    if counts["lh_count"] + counts["ll_count"] >= 2:
        return "trend_continuation_lh_ll", 1, vwap_cross
    if counts["hh_count"] > 0 and exit_close < exit_vwap:
        return "choch_reversal_bear", 0, vwap_cross
    if vwap_cross:
        return "vwap_reject", 0, vwap_cross
    return "bear_leg", 0, vwap_cross


def _snapshot_context(symbol: str, entry_ts: Any, spot: float, snapshot_db: str = OPTION_SNAPSHOT_DB) -> Dict[str, Any]:
    path = Path(snapshot_db)
    empty = {
        "oi_pcr": 0.0,
        "oi_pcr_change": 0.0,
        "nearest_strike": 0.0,
        "top_ce_volume_strike": 0.0,
        "top_pe_volume_strike": 0.0,
        "top_ce_oi_strike": 0.0,
        "top_pe_oi_strike": 0.0,
    }
    if not path.exists():
        return empty
    day = pd.Timestamp(entry_ts).strftime("%Y-%m-%d")
    stamp = _ts(entry_ts)
    try:
        with sqlite3.connect(path) as conn:
            row = conn.execute(
                """
                SELECT pcr_oi, pcr_change_oi, atm_strike, rows_json
                  FROM option_chain_snapshots
                 WHERE upper(underlying)=upper(?)
                   AND ok=1
                   AND substr(snapshot_time, 1, 10)=?
                   AND ts <= ?
                 ORDER BY ts DESC
                 LIMIT 1
                """,
                (symbol, day, stamp),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    """
                    SELECT pcr_oi, pcr_change_oi, atm_strike, rows_json
                      FROM option_chain_snapshots
                     WHERE upper(underlying)=upper(?)
                       AND ok=1
                       AND substr(snapshot_time, 1, 10)=?
                     ORDER BY ts ASC
                     LIMIT 1
                    """,
                    (symbol, day),
                ).fetchone()
    except Exception:
        return empty
    if row is None:
        return empty
    try:
        rows = json.loads(row[3] or "[]")
    except Exception:
        rows = []
    if not isinstance(rows, list):
        rows = []

    def top_strike(key: str) -> float:
        best = None
        for item in rows:
            if not isinstance(item, dict):
                continue
            value = _num(item.get(key), 0.0)
            if best is None or value > best[0]:
                best = (value, _num(item.get("strikePrice"), 0.0))
        return float(best[1]) if best and best[0] > 0 else 0.0

    nearest = _num(row[2], 0.0)
    if nearest <= 0 and rows:
        nearest = min((_num(r.get("strikePrice"), 0.0) for r in rows if isinstance(r, dict)), key=lambda x: abs(x - spot), default=0.0)
    return {
        "oi_pcr": round(_num(row[0], 0.0), 4),
        "oi_pcr_change": round(_num(row[1], 0.0), 4),
        "nearest_strike": nearest,
        "top_ce_volume_strike": top_strike("CE_totalTradedVolume"),
        "top_pe_volume_strike": top_strike("PE_totalTradedVolume"),
        "top_ce_oi_strike": top_strike("CE_openInterest"),
        "top_pe_oi_strike": top_strike("PE_openInterest"),
    }


def _leg_rows(symbol: str, session: pd.DataFrame, top_n: int = 8) -> List[Dict[str, Any]]:
    feat = _feature_frame(session)
    if len(feat) < 20:
        return []
    swings = _swings(feat)
    if len(swings) < 2:
        return []
    rows: List[Dict[str, Any]] = []
    for a, b in zip(swings, swings[1:]):
        if a["kind"] == b["kind"]:
            continue
        start_idx = int(a["idx"])
        end_idx = int(b["idx"])
        if end_idx <= start_idx or end_idx - start_idx < 3:
            continue
        direction = "BUY" if a["kind"] == "L" and b["kind"] == "H" else "SELL"
        entry = feat.iloc[start_idx]
        exit_row = feat.iloc[end_idx]
        entry_price = _num(entry.get("close"), 0.0)
        if entry_price <= 0:
            continue
        path = feat.iloc[start_idx : end_idx + 1]
        if direction == "BUY":
            max_favourable = _num(path["high"].max(), entry_price)
            max_adverse = _num(path["low"].min(), entry_price)
            max_profit_pts = max_favourable - entry_price
            max_adverse_pct = max(0.0, (entry_price - max_adverse) / entry_price * 100.0)
        else:
            max_favourable = _num(path["low"].min(), entry_price)
            max_adverse = _num(path["high"].max(), entry_price)
            max_profit_pts = entry_price - max_favourable
            max_adverse_pct = max(0.0, (max_adverse - entry_price) / entry_price * 100.0)
        max_profit_pct = max_profit_pts / entry_price * 100.0
        if max_profit_pct < 0.08:
            continue
        counts = _structure_counts(swings, max(0, start_idx - 8), end_idx)
        leg_type, bos, vwap_cross = _classify_leg(direction, counts, entry, exit_row)
        close = _num(entry.get("close"), entry_price)
        vwap = _num(entry.get("vwap"), close)
        ema_state = "bull" if _num(entry.get("ema9")) > _num(entry.get("ema21")) > _num(entry.get("ema50")) else (
            "bear" if _num(entry.get("ema9")) < _num(entry.get("ema21")) < _num(entry.get("ema50")) else "mixed"
        )
        opt = _snapshot_context(symbol, feat.index[start_idx], close)
        score = max_profit_pct * 10.0 - max_adverse_pct * 4.0 + min(2.0, _num(entry.get("volume_ratio"))) + (0.75 if bos else 0.0)
        features = {
            "structure_start": a.get("label", a.get("kind", "")),
            "structure_end": b.get("label", b.get("kind", "")),
            "leg_type": leg_type,
            "direction": direction,
            "vwap_state": "above" if close >= vwap else "below",
            "ema_state": ema_state,
            "volume_bucket": "high" if _num(entry.get("volume_ratio")) >= 1.3 else "normal",
            "rsi_bucket": "bull" if _num(entry.get("rsi14"), 50) >= 55 else "bear" if _num(entry.get("rsi14"), 50) <= 45 else "neutral",
            "trend_15m": int(_num(entry.get("trend_15m"), 0)),
            "pcr_bias": "call_heavy" if opt["oi_pcr"] < 0.8 else "put_heavy" if opt["oi_pcr"] > 1.2 else "balanced",
        }
        rows.append({
            "symbol": symbol.upper(),
            "session_date": _session_date(feat.index[start_idx]),
            "direction": direction,
            "start_ts": str(feat.index[start_idx]),
            "end_ts": str(feat.index[end_idx]),
            "entry_price": round(entry_price, 4),
            "exit_price": round(_num(exit_row.get("close"), entry_price), 4),
            "max_profit_pts": round(max_profit_pts, 4),
            "max_profit_pct": round(max_profit_pct, 4),
            "max_adverse_pct": round(max_adverse_pct, 4),
            "bars": end_idx - start_idx + 1,
            "structure_start": str(a.get("label", "")),
            "structure_end": str(b.get("label", "")),
            "leg_type": leg_type,
            "hh_count": counts["hh_count"],
            "hl_count": counts["hl_count"],
            "lh_count": counts["lh_count"],
            "ll_count": counts["ll_count"],
            "bos": int(bos),
            "choch": int("choch" in leg_type),
            "vwap_state": features["vwap_state"],
            "vwap_cross": int(vwap_cross),
            "volume_ratio": round(_num(entry.get("volume_ratio")), 4),
            "rsi14": round(_num(entry.get("rsi14"), 50.0), 4),
            "atr_pct": round(_num(entry.get("atr_pct")), 4),
            "ema_state": ema_state,
            "trend_15m": int(_num(entry.get("trend_15m"), 0)),
            "range_position": round(_num(entry.get("range_position"), 0.5), 4),
            "score": round(score, 4),
            "features_json": json.dumps({**features, **opt}, sort_keys=True),
            **opt,
        })
    rows = sorted(rows, key=lambda x: x["score"], reverse=True)
    return rows[:top_n]


def mine_symbol(symbol: str, days: int = 5, top_n: int = 8, allow_fetch: bool = False) -> Dict[str, Any]:
    df, source = _load_structure_candles(symbol, days, allow_fetch=allow_fetch)
    df = _valid_price_rows(df)
    if df.empty:
        return {"symbol": symbol.upper(), "ok": False, "source": source, "reason": "no_valid_5m_data", "legs": []}
    legs: List[Dict[str, Any]] = []
    for _, session in df.groupby(df.index.date):
        session_legs = _leg_rows(symbol, session, top_n=top_n)
        legs.extend(session_legs)
    legs = sorted(legs, key=lambda x: x["score"], reverse=True)[: max(top_n, 1) * 5]
    return {
        "symbol": symbol.upper(),
        "ok": bool(legs),
        "source": source,
        "bars": int(len(df)),
        "sessions": int(len(set(df.index.date))),
        "reason": "" if legs else "no_structure_legs",
        "legs": legs,
        "top_leg": legs[0] if legs else {},
    }


def _persist(results: List[Dict[str, Any]], db_path: str = DB_PATH) -> int:
    conn = _connect(db_path)
    run_date = datetime.now().strftime("%Y-%m-%d")
    created_at = datetime.now().isoformat(timespec="seconds")
    updated = 0
    columns = [
        "run_date", "symbol", "session_date", "direction", "start_ts", "end_ts",
        "entry_price", "exit_price", "max_profit_pts", "max_profit_pct", "max_adverse_pct",
        "bars", "structure_start", "structure_end", "leg_type", "hh_count", "hl_count",
        "lh_count", "ll_count", "bos", "choch", "vwap_state", "vwap_cross", "volume_ratio",
        "rsi14", "atr_pct", "ema_state", "trend_15m", "range_position", "oi_pcr",
        "oi_pcr_change", "nearest_strike", "top_ce_volume_strike", "top_pe_volume_strike",
        "top_ce_oi_strike", "top_pe_oi_strike", "score", "features_json", "created_at",
    ]
    placeholders = ",".join("?" for _ in columns)
    updates = ",".join(f"{c}=excluded.{c}" for c in columns if c not in {"symbol", "session_date", "direction", "start_ts", "end_ts"})
    sql = (
        f"INSERT INTO option_structure_legs ({','.join(columns)}) VALUES ({placeholders}) "
        "ON CONFLICT(symbol, session_date, direction, start_ts, end_ts) DO UPDATE SET "
        f"{updates}"
    )
    try:
        for result in results:
            for leg in result.get("legs", []) or []:
                values = [run_date]
                for c in columns[1:-1]:
                    values.append(leg.get(c, 0 if c not in {"symbol", "session_date", "direction", "start_ts", "end_ts", "structure_start", "structure_end", "leg_type", "vwap_state", "ema_state", "features_json"} else ""))
                values.append(created_at)
                conn.execute(sql, values)
                updated += 1
        conn.commit()
    finally:
        conn.close()
    return updated


def _edge_summary(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for result in results:
        for leg in result.get("legs", []) or []:
            try:
                features = json.loads(leg.get("features_json") or "{}")
            except Exception:
                features = {}
            for key in ("leg_type", "direction", "vwap_state", "ema_state", "volume_bucket", "rsi_bucket", "pcr_bias"):
                value = features.get(key, leg.get(key, ""))
                if value:
                    buckets[f"{key}:{value}"].append(leg)
    edges = []
    for name, legs in buckets.items():
        if len(legs) < 2:
            continue
        avg_profit = sum(_num(x.get("max_profit_pct")) for x in legs) / len(legs)
        avg_adverse = sum(_num(x.get("max_adverse_pct")) for x in legs) / len(legs)
        high_quality = sum(
            1 for x in legs
            if _num(x.get("max_profit_pct")) >= 0.2 and _num(x.get("max_adverse_pct")) <= max(0.35, _num(x.get("max_profit_pct")))
        )
        edges.append({
            "feature": name,
            "samples": len(legs),
            "avg_profit_pct": round(avg_profit, 4),
            "avg_adverse_pct": round(avg_adverse, 4),
            "quality_rate": round(high_quality / len(legs), 4),
        })
    edges = sorted(edges, key=lambda x: (x["quality_rate"], x["avg_profit_pct"], x["samples"]), reverse=True)
    return {"generated_at": datetime.now().isoformat(timespec="seconds"), "edges": edges[:50]}


def run_structure_miner(
    symbols: Optional[Iterable[str]] = None,
    *,
    days: int = 5,
    top_n: int = 8,
    persist: bool = True,
    allow_fetch: bool = False,
    db_path: str = DB_PATH,
    report_file: str = REPORT_JSON,
    edges_file: str = EDGES_JSON,
) -> Dict[str, Any]:
    selected = [str(s).strip().upper() for s in (symbols or DEFAULT_SYMBOLS) if str(s).strip()]
    started = time.time()
    results = [mine_symbol(symbol, days=days, top_n=top_n, allow_fetch=allow_fetch) for symbol in selected]
    stored = _persist(results, db_path=db_path) if persist else 0
    edge_summary = _edge_summary(results)
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "symbols_seen": len(selected),
        "symbols_ok": sum(1 for r in results if r.get("ok")),
        "legs": sum(len(r.get("legs", []) or []) for r in results),
        "stored": stored,
        "duration_sec": round(time.time() - started, 3),
        "top_edges": edge_summary.get("edges", [])[:10],
        "results": results,
    }
    if persist:
        Path(report_file).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        Path(edges_file).write_text(json.dumps(edge_summary, indent=2, default=str), encoding="utf-8")
    return report


def _symbols_from_env() -> List[str]:
    raw = os.getenv("OPTION_STRUCTURE_SYMBOLS", "")
    if raw:
        return [x.strip().upper() for x in raw.split(",") if x.strip()]
    try:
        import config as cfg

        configured = list(getattr(cfg, "SNAPSHOT_OPTION_UNDERLYINGS", []) or [])
        if configured:
            return [str(x).strip().upper() for x in configured if str(x).strip()]
    except Exception:
        pass
    return DEFAULT_SYMBOLS


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="", help="comma-separated index symbols")
    parser.add_argument("--days", type=int, default=int(os.getenv("OPTION_STRUCTURE_DAYS", "5")))
    parser.add_argument("--top-n", type=int, default=int(os.getenv("OPTION_STRUCTURE_TOP_N", "8")))
    parser.add_argument("--allow-fetch", action="store_true", help="allow broker/data_fetcher fallback when cache is insufficient")
    parser.add_argument("--no-persist", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    symbols = [x.strip().upper() for x in args.symbols.split(",") if x.strip()] if args.symbols else _symbols_from_env()
    report = run_structure_miner(
        symbols=symbols,
        days=args.days,
        top_n=args.top_n,
        persist=not args.no_persist,
        allow_fetch=args.allow_fetch,
    )
    print(
        "OPTION STRUCTURE MINER "
        f"symbols={report.get('symbols_ok')}/{report.get('symbols_seen')} "
        f"legs={report.get('legs')} stored={report.get('stored')} "
        f"top_edges={len(report.get('top_edges', []) or [])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
