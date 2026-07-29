"""
nse_index_history.py — long-horizon NSE index daily-close backfill (2026-07-29,
operator: "download all the available data, we will use for backtest").

NIFTY/BANKNIFTY/etc. are indices, not equities, so they are NOT in bhavcopy
(bhavcopy_cache.py / bhavcopy_backfill.py cover the constituent stocks). This
uses NSE's own historical index API instead, the same endpoint already used
ad hoc in autonomous_backtest.py -- generalized here into a chunked,
resumable, multi-year puller. NSE's historicalOR-family APIs reject very wide
date ranges silently (empty/short results), so this chunks into <=300-day
windows, matching the same "chunk past the provider's per-request cap" pattern
already used for Angel's getCandleData (angel.py's _fetch_chunk).

Writes into candle_cache.db via candle_cache.save_candles(), the SAME table
and API every other daily-candle source in this repo already uses -- so this
data is immediately usable by regime/backtest code without a new DB or a
special-cased reader.
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional

import pandas as pd
import requests

import candle_cache

logger = logging.getLogger(__name__)

_INDEX_NAME_MAP = {
    "NIFTY": "NIFTY 50",
    "BANKNIFTY": "NIFTY BANK",
    "FINNIFTY": "NIFTY FIN SERVICE",
    "MIDCPNIFTY": "NIFTY MIDCAP SELECT",
    "NIFTYNEXT50": "NIFTY NEXT 50",
    "SENSEX": "S&P BSE SENSEX",
}
_CHUNK_DAYS = 300  # conservative; NSE's indicesHistory silently truncates wide ranges
_NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    "Referer": "https://www.nseindia.com/",
}


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(_NSE_HEADERS)
    try:
        s.get("https://www.nseindia.com/", timeout=8)
    except Exception:
        pass
    return s


def _fetch_chunk(session: requests.Session, index_name: str, start: date, end: date,
                  retries: int = 3, backoff_sec: float = 5.0) -> pd.DataFrame:
    url = (
        "https://www.nseindia.com/api/historical/indicesHistory"
        f"?indexType={index_name.replace(' ', '%20')}"
        f"&from={start.strftime('%d-%m-%Y')}&to={end.strftime('%d-%m-%Y')}"
    )
    for attempt in range(1, retries + 1):
        try:
            r = session.get(url, timeout=15)
            if r.status_code == 200:
                recs = r.json().get("data", {}).get("indexCloseOnlineRecords", [])
                if not recs:
                    return pd.DataFrame()
                rows = []
                for d in recs:
                    try:
                        rows.append({
                            # pd.Timestamp() has no dayfirst kwarg (that's a
                            # pd.to_datetime() param) -- NSE's EOD_TIMESTAMP is
                            # "DD-Mon-YYYY" (e.g. "01-Jan-2020"), an explicit
                            # format avoids ambiguity entirely.
                            "date": pd.to_datetime(d["EOD_TIMESTAMP"], format="%d-%b-%Y"),
                            "open": float(d.get("EOD_OPEN_INDEX_VAL", 0)),
                            "high": float(d.get("EOD_HIGH_INDEX_VAL", 0)),
                            "low": float(d.get("EOD_LOW_INDEX_VAL", 0)),
                            "close": float(d.get("EOD_CLOSE_INDEX_VAL", 0)),
                            "volume": int(float(d.get("TRADED_QTY", 0) or 0)),
                        })
                    except Exception:
                        continue
                if not rows:
                    return pd.DataFrame()
                return pd.DataFrame(rows).set_index("date").sort_index()
            logger.warning("indicesHistory HTTP %s for %s %s..%s (attempt %d/%d)",
                           r.status_code, index_name, start, end, attempt, retries)
        except Exception as exc:
            logger.warning("indicesHistory request failed for %s %s..%s: %s",
                           index_name, start, end, exc)
        if attempt < retries:
            time.sleep(backoff_sec * attempt)
    return pd.DataFrame()


def backfill_index(
    symbol: str = "NIFTY",
    years: int = 10,
    end: Optional[date] = None,
    start: Optional[date] = None,
    delay_sec: float = 2.0,
) -> Dict[str, Any]:
    index_name = _INDEX_NAME_MAP.get(symbol.upper())
    if index_name is None:
        return {"error": f"unknown index symbol {symbol!r}; known: {sorted(_INDEX_NAME_MAP)}"}

    end = end or date.today()
    start = start or date(end.year - years, end.month, end.day)

    session = _session()
    total_rows = 0
    chunks_ok = 0
    chunks_empty = 0
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=_CHUNK_DAYS - 1), end)
        df = _fetch_chunk(session, index_name, cursor, chunk_end)
        if not df.empty:
            inserted = candle_cache.save_candles(symbol, "1d", df)
            total_rows += inserted
            chunks_ok += 1
            logger.info("%s %s..%s: %d rows", symbol, cursor, chunk_end, inserted)
        else:
            chunks_empty += 1
            logger.warning("%s %s..%s: no data returned", symbol, cursor, chunk_end)
        cursor = chunk_end + timedelta(days=1)
        if cursor <= end:
            time.sleep(delay_sec)

    return {
        "symbol": symbol, "index_name": index_name,
        "start": start.isoformat(), "end": end.isoformat(),
        "chunks_ok": chunks_ok, "chunks_empty": chunks_empty,
        "rows_stored": total_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="NIFTY,BANKNIFTY,FINNIFTY,MIDCPNIFTY,NIFTYNEXT50,SENSEX")
    parser.add_argument("--years", type=int, default=10)
    parser.add_argument("--delay-sec", type=float, default=2.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s | %(message)s")

    results = []
    for sym in [s.strip() for s in args.symbols.split(",") if s.strip()]:
        result = backfill_index(sym, years=args.years, delay_sec=args.delay_sec)
        print(result)
        results.append(result)
        time.sleep(args.delay_sec)

    import json
    from pathlib import Path
    Path("nse_index_history_report.json").write_text(json.dumps({
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "results": results,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
