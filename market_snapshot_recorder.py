#!/usr/bin/env python3
"""Persist market-regime snapshots for later signal learning."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from typing import Any, Dict, Iterable


DB_PATH = "market_snapshots.db"


def _conn(db_path: str = DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_snapshots (
            ts REAL NOT NULL,
            snapshot_time TEXT NOT NULL,
            india_vix REAL DEFAULT 0,
            vix_change REAL DEFAULT 0,
            breadth_ratio REAL DEFAULT 0,
            breadth_signal TEXT DEFAULT '',
            cross_asset_bias TEXT DEFAULT '',
            regime TEXT DEFAULT '',
            top_sectors_json TEXT DEFAULT '[]',
            avoid_sectors_json TEXT DEFAULT '[]',
            raw_json TEXT DEFAULT '{}'
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_market_snap_ts ON market_snapshots(ts)")
    conn.commit()
    return conn


def build_market_snapshot() -> Dict[str, Any]:
    snap: Dict[str, Any] = {}
    try:
        from market_data_feeds import get_market_feeds
        feeds = get_market_feeds()
        snap["india_vix"] = float(feeds.get_vix() or 0)
        snap["vix_change"] = float(feeds.vix.get_change() or 0)
        breadth = feeds.breadth.get() or {}
        snap["breadth_ratio"] = float(breadth.get("ratio", 0) or 0)
        snap["breadth_signal"] = str(breadth.get("signal", "") or "")
    except Exception as exc:
        snap["feeds_error"] = str(exc)
    try:
        from cross_asset import get_market_bias
        bias = get_market_bias() or {}
        snap["cross_asset_bias"] = str(bias.get("bias", "") or "")
        snap["cross_asset"] = bias
    except Exception as exc:
        snap["cross_asset_error"] = str(exc)
    try:
        from market_regime import MarketRegimeEngine
        engine = MarketRegimeEngine()
        snap["regime"] = str(getattr(engine, "regime", "") or "")
        snap["regime_confidence"] = float(getattr(engine, "confidence", 0) or 0)
    except Exception as exc:
        snap["regime_error"] = str(exc)
    try:
        from sector_rotation_engine import get_top_sectors, get_avoid_sectors
        snap["top_sectors"] = get_top_sectors(3)
        snap["avoid_sectors"] = get_avoid_sectors(3)
    except Exception as exc:
        snap["sector_error"] = str(exc)
    return snap


def record_market_snapshot(*, db_path: str = DB_PATH) -> Dict[str, Any]:
    snap = build_market_snapshot()
    conn = _conn(db_path)
    conn.execute(
        """
        INSERT INTO market_snapshots
        (ts, snapshot_time, india_vix, vix_change, breadth_ratio, breadth_signal,
         cross_asset_bias, regime, top_sectors_json, avoid_sectors_json, raw_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            time.time(),
            time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            float(snap.get("india_vix", 0) or 0),
            float(snap.get("vix_change", 0) or 0),
            float(snap.get("breadth_ratio", 0) or 0),
            str(snap.get("breadth_signal", "") or ""),
            str(snap.get("cross_asset_bias", "") or ""),
            str(snap.get("regime", "") or ""),
            json.dumps(snap.get("top_sectors", []), default=str),
            json.dumps(snap.get("avoid_sectors", []), default=str),
            json.dumps(snap, default=str),
        ),
    )
    conn.commit()
    conn.close()
    return {
        "ok": True,
        "india_vix": snap.get("india_vix", 0),
        "breadth_signal": snap.get("breadth_signal", ""),
        "cross_asset_bias": snap.get("cross_asset_bias", ""),
        "regime": snap.get("regime", ""),
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.parse_args(list(argv) if argv is not None else None)
    print(json.dumps(record_market_snapshot(), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
