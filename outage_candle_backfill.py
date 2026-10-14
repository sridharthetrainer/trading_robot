#!/usr/bin/env python3
"""Backfill an exact historical outage window from Angel One.

The regular recorder prefers Upstox, whose capped recent response can be fresh
but still omit an older outage window. This tool deliberately requests the
operator-supplied range from Angel, validates it, saves 1m bars, and derives
aligned 5m/15m/1h bars from that same data. It never reconstructs signals or
option-chain snapshots that were not observed live.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import date, datetime, time as dt_time
from pathlib import Path

import pandas as pd

from candle_cache import save_candles

REPORT = Path("outage_candle_backfill_report.json")
INTERVAL_RULES = {"5m": "5min", "15m": "15min", "1h": "60min"}
BROKER_SYMBOLS = {
    "NIFTYNEXT50": ("NIFTY NEXT 50", "NSE"),
    "SENSEX": ("SENSEX", "BSE"),
}


def _valid_one_minute(df: pd.DataFrame | None) -> pd.DataFrame | None:
    if df is None or df.empty:
        return None
    out = df.copy()
    out.columns = [str(column).lower() for column in out.columns]
    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(out.columns):
        return None
    out = out[list(("open", "high", "low", "close", "volume"))]
    out.index = pd.to_datetime(out.index, errors="coerce")
    out = out[~out.index.isna()].sort_index()
    good = (
        (out[["open", "high", "low", "close"]] > 0).all(axis=1)
        & (out["high"] >= out[["open", "close"]].max(axis=1))
        & (out["low"] <= out[["open", "close"]].min(axis=1))
    )
    out = out[good]
    return out if not out.empty else None


def _derive(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Resample each exchange session independently, anchored at 09:15 IST."""
    parts = []
    for _, session in df.groupby(df.index.date):
        part = session.resample(
            rule, origin="start_day", offset="15min", label="left", closed="left"
        ).agg({
            "open": "first", "high": "max", "low": "min",
            "close": "last", "volume": "sum",
        }).dropna(subset=["open", "high", "low", "close"])
        parts.append(part)
    return pd.concat(parts).sort_index() if parts else pd.DataFrame()


def _symbols() -> list[str]:
    from data_fetcher import DataFetcher

    fetcher = DataFetcher(symbols_csv="nifty200.csv")
    return fetcher.get_ordered_symbols(include_full_universe=True)


def run(start: date, end: date, symbols: list[str] | None = None) -> dict:
    if end < start:
        raise ValueError("end date precedes start date")
    import config  # loads .env
    from angel import AngelOne

    selected = [value.strip().upper() for value in (symbols or _symbols()) if value.strip()]
    broker = AngelOne(
        api_key=os.getenv("API_KEY", ""), client_id=os.getenv("CLIENT_ID", ""),
        password=os.getenv("PASSWORD", ""), totp_secret=os.getenv("TOTP_SECRET", ""),
        paper_trade=True,
    )
    from_text = datetime.combine(start, dt_time(9, 15)).strftime("%Y-%m-%d %H:%M")
    to_text = datetime.combine(end, dt_time(15, 30)).strftime("%Y-%m-%d %H:%M")
    rows = []
    started = time.time()
    for symbol in selected:
        item = {"symbol": symbol, "source": "Angel One SmartAPI", "ok": False}
        try:
            broker_symbol, exchange = BROKER_SYMBOLS.get(symbol, (symbol, "NSE"))
            raw = broker.get_historical_data(
                broker_symbol, interval="ONE_MINUTE", from_date=from_text,
                to_date=to_text, exchange=exchange,
            )
            one_minute = _valid_one_minute(raw)
            if one_minute is None:
                item["error"] = "no valid one-minute bars"
            else:
                counts = {"1m": save_candles(symbol, "1m", one_minute)}
                for interval, rule in INTERVAL_RULES.items():
                    counts[interval] = save_candles(symbol, interval, _derive(one_minute, rule))
                item.update({
                    "ok": True, "first": str(one_minute.index.min()),
                    "last": str(one_minute.index.max()), "bars": len(one_minute),
                    "sessions": len(set(one_minute.index.date)), "saved": counts,
                    "broker_symbol": broker_symbol, "exchange": exchange,
                })
        except Exception as error:
            item["error"] = f"{type(error).__name__}: {error}"
        rows.append(item)
    previous_results = []
    if REPORT.exists():
        try:
            previous = json.loads(REPORT.read_text(encoding="utf-8"))
            if (previous.get("requested_from"), previous.get("requested_to")) == (from_text, to_text):
                previous_results = previous.get("results", [])
        except (OSError, ValueError):
            pass
    merged = {item["symbol"]: item for item in previous_results}
    merged.update({item["symbol"]: item for item in rows})
    merged_rows = list(merged.values())
    evidence = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "requested_from": from_text, "requested_to": to_text,
        "source": "Angel One SmartAPI historical candle endpoint",
        "symbols_requested": len(merged_rows),
        "symbols_ok": sum(bool(item["ok"]) for item in merged_rows),
        "duration_sec": round(time.time() - started, 3),
        "results": merged_rows,
    }
    canonical = json.dumps(evidence, sort_keys=True, default=str).encode()
    evidence["report_sha256"] = hashlib.sha256(canonical).hexdigest()
    REPORT.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="start", required=True, type=date.fromisoformat)
    parser.add_argument("--to", dest="end", required=True, type=date.fromisoformat)
    parser.add_argument("--symbols", default="")
    args = parser.parse_args()
    symbols = [value for value in args.symbols.split(",") if value.strip()] or None
    result = run(args.start, args.end, symbols)
    print(
        f"Angel outage backfill: {result['symbols_ok']}/{result['symbols_requested']} "
        f"symbols, {result['duration_sec']}s; report={REPORT}"
    )
    return 0 if result["symbols_ok"] == result["symbols_requested"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
