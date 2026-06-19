#!/usr/bin/env python3
"""Persistent market/volume profile context for live scoring and EOD learning."""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

DB_PATH = "market_profile_snapshots.db"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _norm_df(df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    if df is None or not hasattr(df, "columns") or len(df) < 5:
        return None
    out = df.copy()
    out.columns = [str(c).lower() for c in out.columns]
    if "close" not in out.columns or "volume" not in out.columns:
        return None
    if "high" not in out.columns:
        out["high"] = out["close"]
    if "low" not in out.columns:
        out["low"] = out["close"]
    return out


def _conn(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS market_profile_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date TEXT NOT NULL,
            snapshot_time TEXT NOT NULL,
            ts REAL NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL DEFAULT '5m',
            price REAL DEFAULT 0,
            poc REAL DEFAULT 0,
            vah REAL DEFAULT 0,
            val REAL DEFAULT 0,
            hvn_json TEXT DEFAULT '[]',
            lvn_json TEXT DEFAULT '[]',
            value_width_pct REAL DEFAULT 0,
            poc_distance_pct REAL DEFAULT 0,
            vah_distance_pct REAL DEFAULT 0,
            val_distance_pct REAL DEFAULT 0,
            profile_position TEXT DEFAULT '',
            profile_bias TEXT DEFAULT 'NEUTRAL',
            acceptance_state TEXT DEFAULT '',
            score_modifier REAL DEFAULT 0,
            quality REAL DEFAULT 0,
            created_at TEXT DEFAULT ''
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_profile_symbol_ts "
        "ON market_profile_snapshots(symbol, ts)"
    )
    conn.commit()
    return conn


def _last_snapshot(symbol: str, db_path: str = DB_PATH) -> Dict[str, Any]:
    if not Path(db_path).exists():
        return {}
    try:
        with _conn(db_path) as conn:
            row = conn.execute(
                """
                SELECT * FROM market_profile_snapshots
                 WHERE symbol = ?
                 ORDER BY ts DESC
                 LIMIT 1
                """,
                (symbol.upper(),),
            ).fetchone()
        return dict(row) if row else {}
    except Exception:
        return {}


def _position(price: float, val: float, vah: float) -> str:
    if price <= 0 or val <= 0 or vah <= 0:
        return "UNKNOWN"
    if price < val:
        return "BELOW_VALUE"
    if price > vah:
        return "ABOVE_VALUE"
    mid = (val + vah) / 2.0
    return "UPPER_VALUE" if price >= mid else "LOWER_VALUE"


def _profile_bias(price: float, prev: float, poc: float, val: float, vah: float) -> str:
    if price <= 0 or poc <= 0:
        return "NEUTRAL"
    if price > vah and price > prev:
        return "BULLISH_BREAKOUT"
    if price < val and price < prev:
        return "BEARISH_BREAKDOWN"
    if prev < poc <= price:
        return "BULLISH_POC_CROSS"
    if prev > poc >= price:
        return "BEARISH_POC_CROSS"
    if val <= price <= vah:
        return "BALANCED_VALUE"
    return "NEUTRAL"


def _acceptance_state(price: float, prev_price: float, val: float, vah: float) -> str:
    if price <= 0 or prev_price <= 0 or val <= 0 or vah <= 0:
        return "UNKNOWN"
    was_inside = val <= prev_price <= vah
    now_inside = val <= price <= vah
    if not was_inside and now_inside:
        return "ACCEPTING_VALUE"
    if was_inside and not now_inside:
        return "REJECTING_VALUE"
    if now_inside:
        return "INSIDE_VALUE"
    return "OUTSIDE_VALUE"


def _score_modifier(ctx: Dict[str, Any], side: str) -> float:
    side = str(side or "").upper()
    bias = str(ctx.get("profile_bias", "")).upper()
    position = str(ctx.get("profile_position", "")).upper()
    poc_dist = abs(_safe_float(ctx.get("poc_distance_pct"), 99.0))
    mod = 0.0

    bullish = bias.startswith("BULLISH")
    bearish = bias.startswith("BEARISH")
    if side == "BUY":
        if bullish:
            mod += 0.55
        if bearish:
            mod -= 0.55
        if position in {"LOWER_VALUE", "BELOW_VALUE"}:
            mod += 0.20
        if position == "ABOVE_VALUE":
            mod -= 0.15
    elif side == "SELL":
        if bearish:
            mod += 0.55
        if bullish:
            mod -= 0.55
        if position in {"UPPER_VALUE", "ABOVE_VALUE"}:
            mod += 0.20
        if position == "BELOW_VALUE":
            mod -= 0.15

    if poc_dist <= 0.15:
        mod += 0.15 if side in {"BUY", "SELL"} else 0.0
    return round(max(-0.75, min(0.75, mod)), 3)


def build_market_profile_context(
    df: pd.DataFrame,
    *,
    symbol: str = "",
    side: str = "",
    timeframe: str = "5m",
    persist: bool = True,
    db_path: str = DB_PATH,
) -> Dict[str, Any]:
    """Build POC/VAH/VAL context and optionally store a compact snapshot."""
    out = {
        "available": False,
        "profile_bias": "NEUTRAL",
        "profile_position": "UNKNOWN",
        "score_modifier": 0.0,
        "quality": 0.0,
    }
    df_c = _norm_df(df)
    if df_c is None:
        out["reason"] = "missing_ohlcv"
        return out
    try:
        from volume_profile_advanced import build_volume_profile

        profile_type = "VOLUME_PROFILE"
        volume_sum = float(pd.to_numeric(df_c["volume"], errors="coerce").fillna(0).sum())
        if volume_sum <= 0:
            df_c = df_c.copy()
            df_c["volume"] = 1.0
            profile_type = "TPO_PROFILE"

        vp = build_volume_profile(df_c, n_bins=80)
        if not vp:
            out["reason"] = "profile_empty"
            return out
        price = _safe_float(df_c["close"].iloc[-1])
        prev_price = _safe_float(df_c["close"].iloc[-2], price)
        poc = _safe_float(vp.get("poc"))
        vah = _safe_float(vp.get("vah"))
        val = _safe_float(vp.get("val"))
        if price <= 0 or poc <= 0 or vah <= 0 or val <= 0:
            out["reason"] = "invalid_profile_levels"
            return out

        value_width_pct = (vah - val) / max(price, 1e-9) * 100.0
        ctx = {
            "available": True,
            "symbol": str(symbol or "").upper(),
            "timeframe": timeframe,
            "price": round(price, 4),
            "poc": round(poc, 4),
            "vah": round(vah, 4),
            "val": round(val, 4),
            "hvn": [round(_safe_float(x), 4) for x in list(vp.get("hvn") or [])[:8]],
            "lvn": [round(_safe_float(x), 4) for x in list(vp.get("lvn") or [])[:8]],
            "value_width_pct": round(value_width_pct, 4),
            "poc_distance_pct": round((price - poc) / max(price, 1e-9) * 100.0, 4),
            "vah_distance_pct": round((price - vah) / max(price, 1e-9) * 100.0, 4),
            "val_distance_pct": round((price - val) / max(price, 1e-9) * 100.0, 4),
            "profile_position": _position(price, val, vah),
            "profile_bias": _profile_bias(price, prev_price, poc, val, vah),
            "acceptance_state": _acceptance_state(price, prev_price, val, vah),
            "profile_type": profile_type,
            "quality": round(
                min(1.0, len(df_c) / 60.0)
                * (0.62 if profile_type == "TPO_PROFILE" else 1.0),
                3,
            ),
        }
        ctx["score_modifier"] = _score_modifier(ctx, side)
        if persist and ctx["symbol"]:
            persist_market_profile_snapshot(ctx, db_path=db_path)
        return ctx
    except Exception as exc:
        out["reason"] = str(exc)[:120]
        return out


def persist_market_profile_snapshot(ctx: Dict[str, Any], db_path: str = DB_PATH) -> bool:
    try:
        import json

        now = datetime.now()
        with _conn(db_path) as conn:
            conn.execute(
                """
                INSERT INTO market_profile_snapshots
                (snapshot_date, snapshot_time, ts, symbol, timeframe, price, poc, vah, val,
                 hvn_json, lvn_json, value_width_pct, poc_distance_pct, vah_distance_pct,
                 val_distance_pct, profile_position, profile_bias, acceptance_state,
                 score_modifier, quality, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    now.strftime("%Y-%m-%d"),
                    now.strftime("%H:%M:%S"),
                    time.time(),
                    str(ctx.get("symbol", "")).upper(),
                    str(ctx.get("timeframe", "5m")),
                    _safe_float(ctx.get("price")),
                    _safe_float(ctx.get("poc")),
                    _safe_float(ctx.get("vah")),
                    _safe_float(ctx.get("val")),
                    json.dumps(ctx.get("hvn") or []),
                    json.dumps(ctx.get("lvn") or []),
                    _safe_float(ctx.get("value_width_pct")),
                    _safe_float(ctx.get("poc_distance_pct")),
                    _safe_float(ctx.get("vah_distance_pct")),
                    _safe_float(ctx.get("val_distance_pct")),
                    str(ctx.get("profile_position", "")),
                    str(ctx.get("profile_bias", "NEUTRAL")),
                    str(ctx.get("acceptance_state", "")),
                    _safe_float(ctx.get("score_modifier")),
                    _safe_float(ctx.get("quality")),
                    now.isoformat(timespec="seconds"),
                ),
            )
        return True
    except Exception:
        return False


def get_latest_market_profile(symbol: str, db_path: str = DB_PATH) -> Dict[str, Any]:
    return _last_snapshot(symbol, db_path=db_path)
