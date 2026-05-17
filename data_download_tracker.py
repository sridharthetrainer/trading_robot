"""
data_download_tracker.py — Track every download attempt across the system.

Records: what was downloaded, from where, size, success/fail, timestamp.
Produces: daily download report, weekly summary, next-week plan.

HOW IT WORKS:
  Every data fetch (NSE, BSE, Angel One, yfinance, etc.) calls:
      tracker.record(source, item, status, size_kb, error)
  
  End of day: build full report → Telegram
  Weekly: what was never fetched, what failed consistently
"""
from __future__ import annotations

# Auto-fix: get DataFetcher with Angel singleton
def _get_angel_data_fetcher():
    try:
        from angel import AngelOne
        import os as _os_adf
        _ang = AngelOne(api_key=_os_adf.getenv("API_KEY",""),
            client_id=_os_adf.getenv("CLIENT_ID",""),
            password=_os_adf.getenv("PASSWORD",""),
            totp_secret=_os_adf.getenv("TOTP_SECRET",""))
    except Exception: _ang = None
    from data_fetcher import DataFetcher
    return DataFetcher(angel=_ang, paper_trade=False)


import json
import logging
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)
_DB   = Path("download_log.db")

_CREATE = """
CREATE TABLE IF NOT EXISTS downloads (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL    DEFAULT (strftime('%s','now')),
    dl_date    TEXT,
    dl_time    TEXT,
    source     TEXT,   -- NSE, BSE, AngelOne, yfinance, NewsAPI, etc.
    category   TEXT,   -- OHLCV, OptionChain, FII, BhavCopy, Pivots, etc.
    item       TEXT,   -- NIFTY 5m, BANKNIFTY OC, FII_DII, etc.
    status     TEXT,   -- OK, FAILED, PARTIAL, STALE
    size_kb    REAL    DEFAULT 0,
    rows       INTEGER DEFAULT 0,
    latency_ms REAL    DEFAULT 0,
    error      TEXT    DEFAULT '',
    url        TEXT    DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_dl_date ON downloads(dl_date);
CREATE INDEX IF NOT EXISTS idx_status  ON downloads(status);
"""

# ── What SHOULD be downloaded every day ──────────────────────────────────────
DAILY_REQUIRED = {
    # (source, category, item): description
    ("NSE",      "Index",       "NIFTY 5m"):          "NIFTY intraday OHLCV",
    ("NSE",      "Index",       "BANKNIFTY 5m"):       "BANKNIFTY intraday OHLCV",
    ("NSE",      "Index",       "FINNIFTY 5m"):        "FINNIFTY intraday OHLCV",
    ("NSE",      "Index",       "MIDCPNIFTY 5m"):      "MIDCPNIFTY intraday OHLCV",
    ("NSE",      "Index",       "NIFTYNEXT50 5m"):     "NIFTYNEXT50 intraday OHLCV",
    ("BSE",      "Index",       "SENSEX 5m"):          "SENSEX intraday OHLCV",
    ("NSE",      "OptionChain", "NIFTY OC"):           "NIFTY option chain",
    ("NSE",      "OptionChain", "BANKNIFTY OC"):       "BANKNIFTY option chain",
    ("NSE",      "OptionChain", "FINNIFTY OC"):        "FINNIFTY option chain",
    ("BSE",      "OptionChain", "SENSEX OC"):          "SENSEX option chain",
    ("NSE",      "FII",         "FII_DII_Cash"):       "FII/DII cash market flows",
    ("NSE",      "FII",         "Participant_OI"):     "Participant-wise F&O OI",
    ("NSE",      "BhavCopy",    "Equity_BhavCopy"):   "NSE equity BhavCopy delivery%",
    ("NSE",      "Stocks",      "Nifty200_OHLCV"):    "All 194 Nifty200 stocks OHLCV",
    ("NSE",      "Pivots",      "Daily_Pivots"):       "Daily CPR+pivot levels",
    ("NSE",      "Events",      "FnO_BanList"):        "F&O ban list",
    ("NSE",      "Events",      "Corporate_Actions"):  "Corporate actions calendar",
    ("NSE",      "Events",      "Bulk_Deals"):         "Bulk/block deals",
    ("External", "CrossAsset",  "USD_INR"):            "USD/INR rate (yfinance)",
    ("External", "CrossAsset",  "Brent_Crude"):        "Brent crude price (yfinance)",
    ("External", "CrossAsset",  "US_VIX"):             "US VIX (yfinance)",
    ("External", "CrossAsset",  "US_10Y"):             "US 10-year yield (yfinance)",
    ("External", "News",        "Market_News"):        "Market news headlines (NewsAPI)",
    ("NSE",      "Expiry",      "Expiry_Calendar"):    "NSE expiry dates",
}

WEEKLY_REQUIRED = {
    ("NSE",      "Stocks",  "Weekly_OHLCV"):        "Weekly OHLCV all symbols",
    ("NSE",      "Stocks",  "Monthly_OHLCV"):       "Monthly OHLCV all symbols",
    ("NSE",      "Index",   "Index_Rebalancing"):   "Index rebalancing announcements",
    ("NSE",      "FII",     "FII_5d_Cumulative"):   "5-day cumulative FII position",
    ("External", "ML",      "Model_Retrain"):       "AI model weekly retrain",
    ("External", "ML",      "Backtest_Full"):       "Full symbol backtest (199 syms)",
    ("External", "Profile", "Intraday_Profile"):    "Time-bucket profile update",
    ("External", "Feature", "Feature_IC_Report"):   "Feature information coefficient",
}


class DownloadTracker:
    def __init__(self, db_path: str = str(_DB)):
        self.db_path = db_path
        self._init_db()

    def _conn(self):
        c = sqlite3.connect(self.db_path, timeout=10)
        c.row_factory = sqlite3.Row
        return c

    def _init_db(self):
        with self._conn() as c:
            c.executescript(_CREATE)

    def record(
        self,
        source:     str,
        item:       str,
        status:     str   = "OK",
        category:   str   = "Data",
        size_kb:    float = 0,
        rows:       int   = 0,
        latency_ms: float = 0,
        error:      str   = "",
        url:        str   = "",
    ) -> None:
        now = datetime.now()
        try:
            with self._conn() as c:
                c.execute(
                    "INSERT INTO downloads "
                    "(dl_date,dl_time,source,category,item,status,size_kb,rows,latency_ms,error,url) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"),
                     source, category, item, status, size_kb, rows, latency_ms, error[:200], url[:200])
                )
        except Exception as e:
            logger.debug("DownloadTracker.record: %s", e)

    def get_daily_summary(self, for_date: str = "") -> dict:
        """What was downloaded today — success/failure breakdown."""
        dl_date = for_date or date.today().isoformat()
        try:
            with self._conn() as c:
                rows = c.execute(
                    "SELECT * FROM downloads WHERE dl_date=? ORDER BY ts",
                    (dl_date,)
                ).fetchall()
            all_rows = [dict(r) for r in rows]
            ok      = [r for r in all_rows if r["status"] == "OK"]
            failed  = [r for r in all_rows if r["status"] == "FAILED"]
            partial = [r for r in all_rows if r["status"] == "PARTIAL"]

            # What was required but missing?
            downloaded_items = {(r["source"], r["category"], r["item"]) for r in ok}
            missing_required = {
                k: v for k, v in DAILY_REQUIRED.items()
                if k not in downloaded_items
            }

            return {
                "date":     dl_date,
                "total":    len(all_rows),
                "ok":       len(ok),
                "failed":   len(failed),
                "partial":  len(partial),
                "total_kb": sum(r.get("size_kb",0) for r in ok),
                "total_rows":sum(r.get("rows",0) for r in ok),
                "failures": failed,
                "missing_required": missing_required,
                "all": all_rows,
            }
        except Exception as e:
            logger.warning("daily_summary: %s", e)
            return {}

    def get_weekly_summary(self) -> dict:
        """What was downloaded this week — consistency check."""
        cutoff = (date.today() - timedelta(days=7)).isoformat()
        try:
            with self._conn() as c:
                rows = c.execute(
                    "SELECT source,category,item,status,dl_date FROM downloads WHERE dl_date>=?",
                    (cutoff,)
                ).fetchall()
            all_rows = [dict(r) for r in rows]
            # Count by item
            item_stats: Dict[str, dict] = {}
            for r in all_rows:
                key = f"{r['source']}|{r['item']}"
                if key not in item_stats:
                    item_stats[key] = {"ok":0,"failed":0,"days":set()}
                item_stats[key]["days"].add(r["dl_date"])
                if r["status"] == "OK":
                    item_stats[key]["ok"] += 1
                else:
                    item_stats[key]["failed"] += 1

            # Reliability per item
            reliability = {}
            for key, s in item_stats.items():
                total = s["ok"] + s["failed"]
                reliability[key] = {
                    "ok": s["ok"], "failed": s["failed"],
                    "pct": round(s["ok"]/total*100) if total > 0 else 0,
                    "days": len(s["days"]),
                }

            # Items with < 80% reliability
            unreliable = {k:v for k,v in reliability.items() if v["pct"] < 80}
            return {
                "reliability": reliability,
                "unreliable":  unreliable,
                "total_items": len(item_stats),
                "perfect":     sum(1 for v in reliability.values() if v["pct"]==100),
            }
        except Exception as e:
            logger.warning("weekly_summary: %s", e)
            return {}


# Singleton
_tracker: Optional[DownloadTracker] = None
def get_tracker() -> DownloadTracker:
    global _tracker
    if _tracker is None:
        _tracker = DownloadTracker()
    return _tracker


def record(source: str, item: str, status: str = "OK", **kwargs) -> None:
    """Convenience: get_tracker().record(...)"""
    try:
        get_tracker().record(source, item, status, **kwargs)
    except Exception:
        pass


def run_all_downloads() -> dict:
    """Actually download all required items and record results."""
    import logging
    logger = logging.getLogger(__name__)
    tracker = DataDownloadTracker()
    results = {"ok": 0, "fail": 0, "items": []}

    # 1. Index 5m intraday data
    for symbol in ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNEXT50"]:
        try:
            from data_fetcher import DataFetcher
            df = _get_angel_data_fetcher()
            data = df.get_market_data(symbol, interval="5m", days=5)
            if data is not None and not data.empty:
                tracker.record("NSE", "Index", f"{symbol} 5m",
                               True, len(data) * 50)  # rough bytes
                results["ok"] += 1
                results["items"].append(f"✅ {symbol} 5m: {len(data)} bars")
            else:
                tracker.record("NSE", "Index", f"{symbol} 5m", False, 0)
                results["fail"] += 1
                results["items"].append(f"❌ {symbol} 5m: no data")
        except Exception as e:
            tracker.record("NSE", "Index", f"{symbol} 5m", False, 0)
            results["fail"] += 1
            results["items"].append(f"❌ {symbol} 5m: {str(e)[:30]}")

    # 2. BSE SENSEX
    try:
        from data_fetcher import DataFetcher
        df = _get_angel_data_fetcher()
        data = df.get_market_data("SENSEX", interval="5m", days=5)
        if data is not None and not data.empty:
            tracker.record("BSE", "Index", "SENSEX 5m", True, len(data) * 50)
            results["ok"] += 1
        else:
            tracker.record("BSE", "Index", "SENSEX 5m", False, 0)
            results["fail"] += 1
    except Exception:
        tracker.record("BSE", "Index", "SENSEX 5m", False, 0)
        results["fail"] += 1

    # 3. Option chains
    for symbol in ["NIFTY", "BANKNIFTY", "FINNIFTY"]:
        try:
            from data_source_resilience import fetch_option_chain
            oc = fetch_option_chain(symbol)
            if oc and oc.get("records", {}).get("data"):
                strikes = len(oc["records"]["data"])
                tracker.record("NSE", "OptionChain", f"{symbol} OC",
                               True, strikes * 100)
                results["ok"] += 1
                results["items"].append(f"✅ {symbol} OC: {strikes} strikes")
            else:
                tracker.record("NSE", "OptionChain", f"{symbol} OC", False, 0)
                results["fail"] += 1
        except Exception:
            tracker.record("NSE", "OptionChain", f"{symbol} OC", False, 0)
            results["fail"] += 1

    # 4. Bhavcopy
    try:
        from bhavcopy_cache import download_latest
        ok = download_latest()
        tracker.record("NSE", "Bhavcopy", "EOD Bhavcopy",
                       bool(ok), 500000 if ok else 0)
        if ok: results["ok"] += 1
        else: results["fail"] += 1
    except Exception:
        tracker.record("NSE", "Bhavcopy", "EOD Bhavcopy", False, 0)
        results["fail"] += 1

    # 5. FII/DII data
    try:
        from fii_data_fetcher import get_fii_history
        fii = get_fii_history(5)
        if fii is not None and not fii.empty:
            tracker.record("NSE", "Institutional", "FII_DII", True, 1000)
            results["ok"] += 1
        else:
            tracker.record("NSE", "Institutional", "FII_DII", False, 0)
            results["fail"] += 1
    except Exception:
        tracker.record("NSE", "Institutional", "FII_DII", False, 0)
        results["fail"] += 1

    # 6. Global markets
    try:
        from cross_asset import get_cross_asset_data
        gd = get_cross_asset_data()
        if gd and len(gd) >= 3:
            tracker.record("Global", "CrossAsset", "Global Markets", True, 500)
            results["ok"] += 1
        else:
            tracker.record("Global", "CrossAsset", "Global Markets", False, 0)
            results["fail"] += 1
    except Exception:
        tracker.record("Global", "CrossAsset", "Global Markets", False, 0)
        results["fail"] += 1

    # 7. F&O Bhavcopy (OI baseline)
    try:
        from fno_bhavcopy_oi import download_fno_bhavcopy
        fno = download_fno_bhavcopy()
        if fno is not None and not fno.empty:
            tracker.record("NSE", "FnO", "FnO Bhavcopy OI", True, 200000)
            results["ok"] += 1
        else:
            tracker.record("NSE", "FnO", "FnO Bhavcopy OI", False, 0)
            results["fail"] += 1
    except Exception:
        tracker.record("NSE", "FnO", "FnO Bhavcopy OI", False, 0)
        results["fail"] += 1

    # 8. News
    try:
        from omnisource_news_engine import get_omnisource_intelligence
        news = get_omnisource_intelligence()
        if news and news.get("headlines"):
            tracker.record("Multi", "News", "OmniSource News",
                           True, len(str(news)))
            results["ok"] += 1
        else:
            tracker.record("Multi", "News", "OmniSource News", False, 0)
            results["fail"] += 1
    except Exception:
        tracker.record("Multi", "News", "OmniSource News", False, 0)
        results["fail"] += 1

    logger.info("Daily download: %d OK, %d failed", results["ok"], results["fail"])
    return results
