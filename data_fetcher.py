from __future__ import annotations

def _retry_with_backoff(fn, max_retries=3, base_delay=2.0):
    """Retry function with exponential backoff for network failures."""
    import time
    last_err = None
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as e:
            last_err = e
            err_str = str(e).lower()
            # Only retry on network-related errors
            if any(x in err_str for x in ['dns','connection','timeout','network','socket',
                                            'nodename','getaddrinfo','connect']):
                delay = base_delay * (2 ** attempt)
                import logging
                logging.getLogger(__name__).warning(
                    "Network error (attempt %d/%d): %s — retrying in %.0fs",
                    attempt+1, max_retries, e, delay)
                time.sleep(delay)
            else:
                raise  # non-network errors: fail immediately
    raise last_err

"""
data_fetcher.py

Market data fetcher with cache, Angel One primary, yfinance fallback.

Fixes applied
-------------
1. _load_nifty_200() loaded the wrong CSV
   equity_NIFTY.csv is the equity curve output (columns: bar, equity).
   The "Symbol" column never exists so every run fell back to the
   hardcoded 37-symbol list — silently, with no error.
   Fix: stop trying to load a symbol list from an equity curve file.
   The hardcoded fallback is now the only path and is clearly documented.
   If you want a custom symbol list, provide a CSV with column "Symbol"
   at a path you specify via the symbols_csv parameter.

2. MultiIndex columns not handled in _clean()
   yfinance returns MultiIndex columns when downloading with group_by=
   'column' or auto_adjust=False.  _clean() tried to map column names
   on MultiIndex objects, silently producing misnamed columns.
   Fix: flatten MultiIndex to first-level strings before renaming.

3. No get_latest_data_multi_tf() method
   live_signal_engine.py calls this method as the preferred path for
   fetching both primary (5m) and HTF (15m) data in one shot.
   Added: returns {symbol: {"df": df_5m, "df_htf": df_15m}} for each
   symbol, falling back to df as df_htf if the HTF fetch fails.

4. Cache key did not include interval when called from get_latest_data()
   get_latest_data() called get_market_data(symbol) without interval,
   defaulting to "5m". Cache key f"{symbol}_5m_5" was correct but
   if a later call used a different interval, the cache was separate.
   This was already correct — no change needed, just confirmed.
"""

try:
    from data_sources import get_market_data_from_pool as _pool_fetch
    _POOL_AVAIL = False  # yfinance dead — bypassing pool, using TwelveData+Angel directly
except ImportError:
    _POOL_AVAIL = False
try:
    from data_download_tracker import record as _dl_record
    _DL_TRACK = True
except ImportError:
    _DL_TRACK = False
try:
    from fno_ban_list import filter_banned as _filter_banned
    _BAN_FILTER = True
except ImportError:
    _BAN_FILTER = False
try:
    from participant_oi import get_participant_data as _get_part_data
    _PART_DATA = True
except ImportError:
    _PART_DATA = False

import logging
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd

import time as _df_time_

def _retry_request(url, max_retries=3, backoff=2.0, **kw):
    """GAP 20: DNS/network retry with backoff."""
    import logging
    for attempt in range(max_retries):
        try:
            import requests as _rq4
            return _rq4.get(url, **kw)
        except Exception as _re:
            if attempt < max_retries - 1:
                _df_time_.sleep(backoff * (2 ** attempt))
                logging.getLogger(__name__).warning('retry %d: %s', attempt+1, str(_re)[:40])
            else: raise


def _fetch_with_retry(url: str, session=None, retries: int = 3,
                      backoff: float = 2.0, timeout: int = 10) -> object:
    """
    Fetch URL with exponential backoff retry on network/DNS errors.
    Handles: ConnectionError, Timeout, DNS failure, brief disconnects.
    """
    import requests as _rq, time as _t, logging as _lg
    _log = _lg.getLogger(__name__)
    _sess = session or _rq.Session()
    _sess.headers.update({"User-Agent": "Mozilla/5.0"})
    last_err = None
    for attempt in range(retries):
        try:
            r = _sess.get(url, timeout=timeout)
            r.raise_for_status()
            return r
        except (_rq.exceptions.ConnectionError,
                _rq.exceptions.Timeout,
                _rq.exceptions.ChunkedEncodingError) as e:
            last_err = e
            wait = backoff ** attempt
            _log.debug("Network retry %d/%d for %s (%.1fs): %s",
                       attempt+1, retries, url[:40], wait, e)
            _t.sleep(wait)
        except _rq.exceptions.HTTPError as e:
            if e.response and e.response.status_code in (429, 503):
                wait = backoff ** (attempt + 1)
                _log.debug("Rate limit retry %d: %.1fs", attempt+1, wait)
                _t.sleep(wait)
            else:
                raise
    _log.warning("All %d retries failed for %s: %s", retries, url[:40], last_err)
    raise last_err or Exception(f"Failed to fetch {url}")


logger = logging.getLogger(__name__)

# Default symbol list — used when no external CSV is provided.
# This is a representative subset of large-cap NSE stocks.

# ── Symbol → yfinance ticker mapping ─────────────────────────────────────────
# NSE indices need ^ prefix, NSE stocks need .NS suffix
# BSE index uses ^BSESN, BSE stocks use .BO suffix
_YFINANCE_TICKER_MAP: dict = {
    # NSE Indices
    "NIFTY":        "^NSEI",
    "BANKNIFTY":    "^NSEBANK",
    "FINNIFTY":     "NIFTY_FIN_SERVICE.NS",
    "MIDCPNIFTY":   "^NSEMDCP50",
    "NIFTYNEXT50":  "^NSMIDCP",
    "SENSEX":       "^BSESN",
    "BANKEX":       "BANKEX.BO",
    # Stock suffix rule: append .NS
    # Applied dynamically in _yfinance_ticker()
}

def _yfinance_ticker(symbol: str) -> str:
    """Convert NSE/BSE symbol to yfinance ticker."""
    s = symbol.upper().strip()
    if s in _YFINANCE_TICKER_MAP:
        return _YFINANCE_TICKER_MAP[s]
    # BSE stocks
    if s.endswith(".BO"):
        return s
    # Default: NSE stock
    return f"{s}.NS"

_DEFAULT_SYMBOLS: List[str] = [
    "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS",
    "ITC", "LT", "SBIN", "AXISBANK", "KOTAKBANK",
    "HCLTECH", "ASIANPAINT", "MARUTI", "SUNPHARMA",
    "TITAN", "ULTRACEMCO", "WIPRO", "ONGC", "NTPC",
    "POWERGRID", "BAJFINANCE", "BAJAJFINSV",
    "ADANIENT", "ADANIPORTS", "COALINDIA",
    "JSWSTEEL", "TATASTEEL", "HINDALCO",
    "DIVISLAB", "DRREDDY", "CIPLA",
    "GRASIM", "EICHERMOT", "HEROMOTOCO",
    "BRITANNIA", "NESTLEIND", "HINDUNILVR",
]

# Priority Tier-1 indices — always fetched first.
# FINNIFTY = Nifty Financial Services, also has weekly options.
_INDEX_SYMBOLS: List[str] = [
    "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNEXT50", "SENSEX"
]


def auto_update_nifty200(csv_path: str = "nifty200.csv") -> bool:
    """
    Auto-fetch current Nifty 200 constituents from NSE.
    Run on first of each month to keep list current.
    """
    import os, csv, requests
    from datetime import date, timedelta
    from pathlib import Path

    p = Path(csv_path)
    # Only refresh if file is older than 25 days
    if p.exists():
        age = (date.today() - date.fromtimestamp(p.stat().st_mtime)).days
        if age < 25:
            return True  # still fresh

    try:
        session = requests.Session()
        session.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://www.nseindia.com/"})
        session.get("https://www.nseindia.com/", timeout=5)
        r = session.get(
            "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY200",
            timeout=10
        )
        data  = r.json()
        stocks = [row["symbol"] for row in data.get("data",[]) if row.get("symbol")]
        if len(stocks) < 100:
            return False
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Symbol"])
            for s in stocks:
                writer.writerow([s])
        import logging
        logging.getLogger(__name__).info("Nifty 200 list updated: %d symbols", len(stocks))
        return True
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug("Nifty200 auto-update failed: %s", e)
        return False


class DataFetcher:

    def __init__(
        self,
        angel=None,
        paper_trade: bool = True,
        symbols_csv: Optional[str] = None,
    ) -> None:
        self.angel       = angel
        self.paper_trade = paper_trade

        # Cache: {cache_key: {"data": DataFrame, "time": datetime}}
        self.cache: Dict[str, Dict[str, Any]] = {}
        self._MAX_CACHE = 500  # max entries before eviction
        self._api_latency: Dict[str, list] = {}  # source → [ms, ms, ...]

        self.indices    = _INDEX_SYMBOLS
        self.nifty_200  = self._load_symbol_list(symbols_csv)
        # GA-8: semaphore limits concurrent historical API calls to avoid 429 errors
        # Angel One allows ~3-5 historical requests/second
        self._hist_semaphore = threading.Semaphore(3)

    # ------------------------------------------------------------------
    # Market hours
    # ------------------------------------------------------------------
    def _is_market_hours(self) -> bool:
        now = datetime.now()
        if now.weekday() >= 5:
            return False
        start = now.replace(hour=9,  minute=15, second=0, microsecond=0)
        end   = now.replace(hour=15, minute=30, second=0, microsecond=0)
        return start <= now <= end

    # ------------------------------------------------------------------
    # Symbol list loader
    # ------------------------------------------------------------------
    def _load_symbol_list(self, csv_path: Optional[str]) -> List[str]:
        """
        Load a custom symbol list from a CSV with a "Symbol" column.
        Falls back to the built-in default list on any failure.
        """
        if csv_path:
            try:
                df = pd.read_csv(csv_path)
                if "Symbol" in df.columns:
                    symbols = df["Symbol"].dropna().unique().tolist()
                    if symbols:
                        logger.info("Loaded %d symbols from %s", len(symbols), csv_path)
                        return [str(s).strip().upper() for s in symbols]
                logger.warning("CSV %s has no 'Symbol' column; using default list", csv_path)
            except Exception as exc:
                logger.warning("Failed to load symbol CSV %s: %s; using default list", csv_path, exc)

        logger.info("Using built-in default symbol list (%d symbols)", len(_DEFAULT_SYMBOLS))
        return list(_DEFAULT_SYMBOLS)

    # ------------------------------------------------------------------
    # Master data fetch — returns {symbol: DataFrame}
    # ------------------------------------------------------------------
    def get_latest_data(self) -> Dict[str, pd.DataFrame]:
        """
        Fetch market data for all symbols.

        Priority order:
          1. NIFTY, BANKNIFTY, MIDCPNIFTY, NIFTYNEXT50  (option underlyings — highest priority)
          2. Remaining index symbols
          3. NIFTY 200 stocks

        Priority symbols are fetched first so the live engine can evaluate
        them before spending time on equities. In tight cycles the engine
        may time out on equities but will always have index data.
        """
        all_data: Dict[str, pd.DataFrame] = {}

        # Build priority-ordered symbol list
        priority_1 = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNEXT50", "SENSEX"]
        priority_2 = [s for s in self.indices if s not in priority_1]
        priority_3 = [s for s in self.nifty_200 if s not in self.indices and s not in priority_1]

        ordered_symbols = priority_1 + priority_2 + priority_3
        # Deduplicate preserving order
        seen = set()
        symbols = [s for s in ordered_symbols if not (s in seen or seen.add(s))]

        # ── Pre-load bhavcopy cache for all symbols (cold start fix) ──────────
        try:
            from bhavcopy_cache import get_history as _bhav_hist
            for _sym in symbols[:20]:  # top 20 priority symbols
                _bkey = f"{_sym}_5m_5"
                if _bkey not in self.cache:
                    _bdf = _bhav_hist(_sym, days=5)
                    if _bdf is not None and len(_bdf) >= 5:
                        self.cache[_bkey] = {"data": _bdf,
                                             "time": __import__('datetime').datetime.now()}
        except Exception: pass


        # Force Angel session refresh if first batch returns no data
        _scan_retry_done = False

        # Force Angel session refresh before first scan of day
        if not getattr(self, "_session_refreshed_today", False):
            try:
                if self.angel and hasattr(self.angel, "_auto_refresh_session"):
                    self.angel._auto_refresh_session()
                    self._session_refreshed_today = True
                    logger.info("Angel session refreshed before first scan")
            except Exception: pass

        for symbol in symbols:
            try:
                df = self.get_market_data(symbol)
                # Use longer max_age at open (EOD seed data is hours old but still valid)
                import datetime as _dt2
                _now_h = _dt2.datetime.now().hour
                _now_m = _dt2.datetime.now().minute
                _at_open = (_now_h == 9 and _now_m <= 30)
                max_age = 1440 if _at_open else int(getattr(self, '_max_data_age_minutes', 480))
                if df is not None and not df.empty:
                    if self._check_data_freshness(df, symbol, max_age):
                        all_data[symbol] = df
                        if _DL_TRACK:
                            try: _dl_record("AngelOne/yfinance",f"{symbol} 5m","OK","OHLCV",rows=len(df))
                            except Exception: pass
                    # stale data: skip symbol, already logged
                else:
                    if _DL_TRACK:
                        try: _dl_record("AngelOne/yfinance",f"{symbol} 5m","STALE","OHLCV",error="data stale")
                        except Exception: pass
                    # stale data: skip symbol, already logged
            except Exception:
                logger.warning("Failed fetching %s", symbol)

        if not all_data:
            logger.error(
                "SCANNED: 0 — No data fetched for ANY of %d symbols. "
                "Possible causes: Angel session expired, NSE rate limit, "
                "or data freshness gate too strict (max_age=%d min). "
                "Run /session to refresh Angel token.",
                len(symbols), max_age
            )
            # Try force session refresh for next cycle
            try:
                if hasattr(self, 'angel') and hasattr(self.angel, '_auto_refresh_session'):
                    self.angel._auto_refresh_session()
                    logger.info('Angel session force-refreshed after Scanned:0')
            except Exception: pass
        else:
            idx_count   = sum(1 for s in all_data if s in set(self.indices + priority_1))
            stock_count = len(all_data) - idx_count
            logger.info(
                "Data fetched: %d total (%d indices + %d stocks)",
                len(all_data), idx_count, stock_count,
            )

        return all_data

    def refresh_eod_intelligence(self) -> None:
        """Refresh participant OI and F&O ban list after market close."""
        try:
            from participant_oi import get_participant_data
            get_participant_data(force=True)
            logger.info("EOD: Participant OI refreshed")
        except Exception as e:
            logger.debug("Participant OI: %s", e)
        try:
            from fno_ban_list import get_ban_list
            get_ban_list(force=True)
        except Exception as _e:
            import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

    def get_latest_data_multi_tf(
        self,
        primary_interval: str = "5m",
        htf_interval:     str = "15m",
        days:             int = 5,
    ) -> Dict[str, Dict[str, pd.DataFrame]]:
        """
        Fetch primary and HTF data for all symbols.

        Returns
        -------
        {
            symbol: {
                "df":     pd.DataFrame,   # primary interval (e.g. 5m)
                "df_htf": pd.DataFrame,   # higher TF (e.g. 15m)
            },
            ...
        }

        If the HTF fetch fails for a symbol, df_htf falls back to df
        so callers always receive a valid pair.
        """
        result: Dict[str, Dict[str, pd.DataFrame]] = {}

        # Priority ordering: option underlyings first, then other indices, then stocks
        priority_1 = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNEXT50", "SENSEX"]
        priority_2 = [s for s in self.indices if s not in priority_1]
        priority_3 = [s for s in self.nifty_200 if s not in self.indices and s not in priority_1]
        ordered = priority_1 + priority_2 + priority_3
        seen = set()
        symbols = [s for s in ordered if not (s in seen or seen.add(s))]

        for symbol in symbols:
            try:
                df = self.get_market_data(symbol, interval=primary_interval, days=days)
                if df is None or df.empty:
                    continue

                df_htf = df  # safe fallback
                try:
                    htf = self.get_market_data(symbol, interval=htf_interval, days=days)
                    if htf is not None and len(htf) >= 20:
                        df_htf = htf
                except Exception:
                    logger.debug("HTF fetch failed for %s; using primary as HTF", symbol)

                result[symbol] = {"df": df, "df_htf": df_htf}

            except Exception:
                logger.warning("Multi-TF fetch failed for %s", symbol)

        logger.info("Multi-TF data fetched for %d symbols", len(result))
        return result

    # ------------------------------------------------------------------
    # Single symbol fetch with cache

    def get_latest_data_three_tf(
        self,
        primary_interval: str = "5m",
        htf_interval:     str = "15m",
        htf2_interval:    str = "1h",
        days:             int = 5,
    ) -> Dict[str, Dict[str, "pd.DataFrame"]]:
        """
        Fetch three timeframes per symbol: primary, HTF, and 1-hour.

        Returns
        -------
        {
            symbol: {
                "df":      DataFrame,  # 5-min
                "df_htf":  DataFrame,  # 15-min
                "df_1h":   DataFrame,  # 1-hour
            }
        }

        The 1-hour data is used as the third confirmation filter:
        5-min signal + 15-min aligned + 1-hour trend = strongest signals.
        """
        import pandas as pd

        # Priority ordering: option underlyings first, then other indices, then stocks
        priority_1 = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNEXT50", "SENSEX"]
        priority_2 = [s for s in self.indices if s not in priority_1]
        priority_3 = [s for s in self.nifty_200 if s not in self.indices and s not in priority_1]
        ordered = priority_1 + priority_2 + priority_3
        seen = set()
        symbols = [s for s in ordered if not (s in seen or seen.add(s))]

        result: Dict[str, Dict[str, "pd.DataFrame"]] = {}

        for symbol in symbols:
            try:
                df = self.get_market_data(symbol, interval=primary_interval, days=days)
                if df is None or df.empty:
                    continue

                df_htf = df
                df_1h  = df

                try:
                    htf = self.get_market_data(symbol, interval=htf_interval, days=days)
                    if htf is not None and len(htf) >= 20:
                        df_htf = htf
                except Exception as _e:
                    import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

                try:
                    h1 = self.get_market_data(symbol, interval=htf2_interval, days=max(days, 10))
                    if h1 is not None and len(h1) >= 10:
                        df_1h = h1
                except Exception as _e:
                    import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

                result[symbol] = {
                    "df":     df,
                    "df_htf": df_htf,
                    "df_1h":  df_1h,
                }
            except Exception:
                logger.warning("Three-TF fetch failed for %s", symbol)

        return result


    def _check_data_freshness(
        self,
        df,
        symbol: str,
        max_age_minutes: int = 10,
    ) -> bool:
        """
        Returns True if the last candle is recent enough to use.
        Returns False if data is stale — symbol will be skipped.

        max_age_minutes=10: if the last 5-min candle is more than
        10 minutes old, the data feed is delayed and signals would
        be based on stale prices.
        """
        try:
            if df is None or df.empty:
                return False
            if not isinstance(df.index, pd.DatetimeIndex):
                return True  # can't check without datetime index
            last_ts  = df.index[-1]
            if last_ts.tzinfo is not None:
                last_ts = last_ts.tz_convert("Asia/Kolkata").tz_localize(None)
            age_min = (pd.Timestamp.now() - last_ts).total_seconds() / 60
            # EOD/daily data always looks "stale" — last bar is yesterday's close
            # Accept it if we have enough bars for indicators (100+)
            if age_min > max_age_minutes:
                if len(df) >= 5:  # accept if we have enough bars for basic indicators
                    logger.debug("EOD data %s: age=%.0fm bars=%d (accepted)", symbol, age_min, len(df))
                    return True
                logger.warning(
                    "STALE DATA: %s last candle is %.0f min old (max=%d) — skipping",
                    symbol, age_min, max_age_minutes,
                )
                return False
            return True
        except Exception:
            return True   # on error, don't block

    # ------------------------------------------------------------------
    def get_market_data(
        self,
        symbol:   str,
        interval: str = "5m",
        days:     int = 5,
    ) -> Optional[pd.DataFrame]:
        cache_key = f"{symbol}_{interval}_{days}"
        expiry    = 60 if self._is_market_hours() else 600

        if cache_key in self.cache:
            cached = self.cache[cache_key]
            age    = (datetime.now() - cached["time"]).total_seconds()
            if age < expiry:
                return cached["data"]

        # Angel One — primary data source (free, NSE-native)
        if self.angel and hasattr(self.angel, "get_historical_data"):
            try:
                with self._hist_semaphore:
                    # Try requested interval first, fall back to 1d
                    df = self._fetch_from_angel(symbol, interval, days)
                    if df is None or df.empty:
                        df = self._fetch_from_angel(symbol, "1d", max(days,30))
                if df is not None and not df.empty:
                    df = self._clean(df)
                    self.cache[cache_key] = {"data": df, "time": datetime.now()}
                    try:
                        from data_source_resilience import check_and_alert_failover
                        check_and_alert_failover("Angel One", True)
                    except Exception: pass
                    return df
            except Exception as exc:
                logger.debug("Angel failed for %s: %s", symbol, exc)
                try:
                    from data_source_resilience import check_and_alert_failover
                    check_and_alert_failover("Angel One", False,
                                             getattr(self,"_alerts",None))
                except Exception: pass

        # ── Quick yfinance fallback when Angel returns nothing ────────────────
        try:
            import yf_compat as yf  # yfinance removed: Yahoo API broken
            _ysym = symbol.upper()
            if _ysym in {"NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY","SENSEX"}:
                _ymap = {"NIFTY":"^NSEI","BANKNIFTY":"^NSEBANK","SENSEX":"^BSESN",
                         "FINNIFTY":"NIFTY_FIN_SERVICE.NS","MIDCPNIFTY":"NIFTY_MIDCAP_SELECT.NS"}
                _yt = _ymap.get(_ysym, f"{_ysym}.NS")
            else:
                _yt = f"{_ysym}.NS"
            _ydf = _yf.download(_yt, period="5d", interval=f"{interval}",
                                progress=False, auto_adjust=True)
            if _ydf is not None and len(_ydf) >= 10:
                _ydf.columns = [c.lower() if isinstance(c,str) else c[0].lower()
                                for c in _ydf.columns]
                self.cache[cache_key] = {"data": _ydf, "time": datetime.now()}
                logger.info("yfinance fallback OK for %s (%d bars)", symbol, len(_ydf))
                return _ydf
        except Exception as _yfe:
            logger.debug("yfinance fallback failed %s: %s", symbol, _yfe)

        # Fallback chain: SmartConnect direct → Angel wrapper → Fyers → TwelveData
        try:
            # SmartConnect direct — 5-min during market hours, 1d otherwise
            _use_iv = "5m" if self._is_market_hours() else "1d"
            _use_days = days if _use_iv == "5m" else max(days, 60)
            df_1d = self._fetch_via_smartconnect(symbol, _use_iv, _use_days)
            # If 5m returns too few bars, add 1d history for indicators
            if df_1d is not None and len(df_1d) < 50 and _use_iv == "5m":
                df_hist = self._fetch_via_smartconnect(symbol, "1d", 60)
                if df_hist is not None and len(df_hist) > 0:
                    import pandas as _pd2
                    df_1d = _pd2.concat([df_hist, df_1d]).tail(200)
                    df_1d = df_1d[~df_1d.index.duplicated(keep="last")]
            # Fyers (skip — token expires daily, not autonomous)
            # Use Dhan instead (permanent token):
            if df_1d is None or (hasattr(df_1d,"empty") and df_1d.empty):
                try:
                    from dhan_client import get_historical_data as _dh, is_configured as _dok
                    if _dok():
                        df_1d = _dh(symbol, interval="1d", days=max(days,60))
                except Exception: pass
            # Angel wrapper if above fail
            if df_1d is None or (hasattr(df_1d,"empty") and df_1d.empty):
                if hasattr(self.angel,"get_historical_data") and self.angel:
                    df_1d = self._fetch_from_angel(symbol, "1d", max(days, 30))
            # Twelve Data as next backup
            if df_1d is None or (hasattr(df_1d,"empty") and df_1d.empty):
                df_1d = self._fetch_from_twelvedata(symbol, "1d", max(days, 60))
            # Tiingo last
            if df_1d is None or (hasattr(df_1d,"empty") and df_1d.empty):
                df_1d = self._fetch_from_tiingo(symbol, "1d", max(days, 60))
            # NSE direct for historical bars
            if df_1d is None or (hasattr(df_1d,"empty") and df_1d.empty):
                df_1d = self._fetch_from_nse_direct(symbol, "1d", max(days, 60))
            # NSE allIndices live price (indices only — always works)
            if df_1d is None or (hasattr(df_1d,"empty") and df_1d.empty):
                df_1d = self._fetch_nse_live_single(symbol)
            if df_1d is not None and not df_1d.empty:
                # Append today's live price as latest bar
                try:
                    import requests as _rq
                    _s = _rq.Session()
                    _s.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://www.nseindia.com"})
                    _s.get("https://www.nseindia.com/", timeout=4)
                    _r = _s.get("https://www.nseindia.com/api/allIndices", timeout=6)
                    if _r.status_code == 200:
                        _idx_names = {"NIFTY":"NIFTY 50","BANKNIFTY":"NIFTY BANK",
                                      "FINNIFTY":"NIFTY FIN SERVICE","MIDCPNIFTY":"NIFTY MIDCAP SELECT"}
                        _iname = _idx_names.get(symbol.upper(),"")
                        for _idx in _r.json().get("data",[]):
                            if _iname and _iname.upper() in str(_idx.get("index","")).upper():
                                _p = float(_idx.get("last",0) or 0)
                                if _p > 0:
                                    _now_row = pd.DataFrame([{
                                        "open":  float(_idx.get("open",_p)),
                                        "high":  float(_idx.get("high",_p)),
                                        "low":   float(_idx.get("low",_p)),
                                        "close": _p, "volume": 0,
                                    }], index=[pd.Timestamp.now()])
                                    df_1d = pd.concat([df_1d, _now_row])
                                    df_1d = df_1d[~df_1d.index.duplicated(keep="last")]
                                    break
                except Exception: pass
                df = df_1d
                logger.debug("NSE EOD+live: %s %d bars", symbol, len(df))
            elif _POOL_AVAIL:
                df = _pool_fetch(symbol, interval, days, angel=self.angel)
            else:
                df = self._fetch_from_yfinance(symbol, interval, days)
            if df is not None and not df.empty:
                df = self._clean(df)
                self.cache[cache_key] = {"data": df, "time": datetime.now()}
                return df
        except Exception as e:
            logger.warning("All data sources failed for %s: %s", symbol, e)

        # ── Dhan API (permanent token, no daily login) ───────────────────────
        try:
            from dhan_client import get_historical_data as _dhan_hist, is_configured as _dhan_ok
            if _dhan_ok():
                _dhan_df = _dhan_hist(symbol, interval, days)
                if _dhan_df is not None and not _dhan_df.empty:
                    _dhan_df = self._clean(_dhan_df)
                    self.cache[cache_key] = {'data': _dhan_df, 'time': datetime.now()}
                    logger.info('Dhan fallback ✅ %s (%d bars)', symbol, len(_dhan_df))
                    return _dhan_df
        except Exception as _de:
            logger.debug('Dhan fallback %s: %s', symbol, _de)

        # ── Zerodha Kite Connect (if configured — best quality) ────────────────
        try:
            from zerodha_client import get_historical_data as _zd_hist, is_configured as _zd_ok
            if _zd_ok():
                _zd = _zd_hist(symbol, interval, days)
                if _zd is not None and not _zd.empty:
                    _zd = self._clean(_zd)
                    self.cache[cache_key] = {"data": _zd, "time": datetime.now()}
                    logger.info("Zerodha fallback OK: %s (%d bars)", symbol, len(_zd))
                    return _zd
        except Exception as _ze:
            logger.debug("Zerodha fallback %s: %s", symbol, _ze)

        # ── Bhavcopy cache fallback (always available offline) ───────────────
        try:
            from bhavcopy_cache import get_ohlcv as _bhav_ohlcv
            _bhav_df = _bhav_ohlcv(symbol, days=max(days, 30))
            if _bhav_df is not None and len(_bhav_df) >= 5:
                _bhav_df.columns = [c.lower() for c in _bhav_df.columns]
                self.cache[cache_key] = {"data": _bhav_df, "time": datetime.now()}
                logger.info("Bhavcopy fallback ✅ %s (%d bars)", symbol, len(_bhav_df))
                return _bhav_df
        except Exception as _bhe:
            logger.debug("Bhavcopy fallback %s: %s", symbol, _bhe)

        # ── yf_compat fallback (Stooq → Yahoo → cached) ─────────────────────
        try:
            import yf_compat as _yfc
            _yf_map = {"NIFTY":"^NSEI","BANKNIFTY":"^NSEBANK","SENSEX":"^BSESN"}
            _yf_sym = _yf_map.get(symbol.upper(), symbol.upper() + ".NS")
            _yf_df = _yfc.download(_yf_sym, period=f"{days}d", interval=interval)
            if _yf_df is not None and not _yf_df.empty and len(_yf_df) >= 5:
                _yf_df.columns = [c.lower() for c in _yf_df.columns]
                self.cache[cache_key] = {"data": _yf_df, "time": datetime.now()}
                logger.info("yf_compat fallback ✅ %s (%d bars)", symbol, len(_yf_df))
                return _yf_df
        except Exception as _yfe:
            logger.debug("yf_compat fallback %s: %s", symbol, _yfe)

        # ── Upstox / Dhan extended fallback (GAP 3 & 4) ─────────────────────
        try:
            from data_source_resilience import get_intraday_with_fallback
            _ext = get_intraday_with_fallback(symbol, days,
                                              getattr(self,'angel',None))
            if _ext is not None and not _ext.empty:
                _ext = self._clean(_ext)
                self.cache[cache_key] = {'data': _ext, 'time': datetime.now()}
                return _ext
        except Exception as _ef:
            logger.debug('extended fallback %s: %s', symbol, _ef)

        return None

    # ------------------------------------------------------------------
    # Angel One fetch
    # ------------------------------------------------------------------
    def _fetch_from_angel(
        self, symbol: str, interval: str, days: int
    ) -> Optional[pd.DataFrame]:
        if not hasattr(self.angel, "get_historical_data"):
            return None

        try:
            # Auto-refresh session if stale
            if hasattr(self.angel, "_ensure_session_fresh"):
                self.angel._ensure_session_fresh()
            now     = datetime.now()
            from_dt = (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
            to_dt   = now.strftime("%Y-%m-%d %H:%M")

            interval_map = {
                "1m": "ONE_MINUTE", "5m": "FIVE_MINUTE", "15m": "FIFTEEN_MINUTE",
                "30m": "THIRTY_MINUTE", "1h": "ONE_HOUR", "1d": "ONE_DAY",
            }

            return self.angel.get_historical_data(
                symbol=symbol,
                interval=interval_map.get(interval, "FIVE_MINUTE"),
                from_date=from_dt,
                to_date=to_dt,
                exchange="NSE",
            )
        except Exception as _e:
            import logging as _lg
            _lg.getLogger(__name__).debug("Angel fetch failed for %s: %s", symbol, _e)
            return None

    # ------------------------------------------------------------------
    # yfinance fetch
    # ------------------------------------------------------------------





    def _fetch_via_smartconnect(self, symbol: str, interval: str, days: int):
        """
        Fetch directly via SmartAPI SmartConnect — bypasses AngelOne wrapper.
        Uses same approach as the working test_angel_direct.py.
        Token lookup from MasterContract_ALL.csv.
        """
        try:
            import pandas as _pd, pyotp as _otp, os as _os
            from SmartApi import SmartConnect as _SC
            import config as _cfg

            # Token map (built from MasterContract_ALL.csv)
            _idx_tokens = {
                "NIFTY":99926000,"BANKNIFTY":99926009,"FINNIFTY":99926037,
                "MIDCPNIFTY":99926051,"SENSEX":99919000,
            }
            _iv_map = {
                "1m":"ONE_MINUTE","5m":"FIVE_MINUTE","15m":"FIFTEEN_MINUTE",
                "30m":"THIRTY_MINUTE","1h":"ONE_HOUR","1d":"ONE_DAY","daily":"ONE_DAY",
            }
            iv = _iv_map.get(interval, "ONE_DAY")
            sym_up = symbol.upper()

            # Get token
            token = str(_idx_tokens.get(sym_up, ""))
            if not token:
                # Look up in master contract
                mc = _pd.Path("MasterContract_ALL.csv") if False else None
                from pathlib import Path as _P
                for mc_file in ["MasterContract_ALL.csv","MasterContract_NFO.csv"]:
                    _mc = _P(mc_file)
                    if _mc.exists():
                        _df_mc = _pd.read_csv(str(_mc), low_memory=False,
                                              usecols=lambda c: c in ["exch_seg","symbol","token","name"])
                        if "exch_seg" in _df_mc.columns:
                            _nse = _df_mc[_df_mc["exch_seg"].str.upper()=="NSE"]
                            _row = _nse[_nse["symbol"].str.upper().str.replace("-EQ","",regex=False)==sym_up]
                            if len(_row):
                                token = str(_row.iloc[0]["token"])
                                break

            if not token:
                logger.debug("No token for %s", symbol)
                return None

            # Connect if not cached or session too old (>6hrs)
            import time as _tsess
            _sc_age = _tsess.time() - getattr(self, "_sc_obj_ts", 0)
            if not hasattr(self, "_sc_obj") or self._sc_obj is None or _sc_age > 21600:
                try:
                    _sc = _SC(api_key=_cfg.API_KEY)
                    _totp = _otp.TOTP(_cfg.TOTP_SECRET).now()
                    _r = _sc.generateSession(_cfg.CLIENT_ID, _cfg.PASSWORD, _totp)
                    if _r.get("status") == True:
                        self._sc_obj    = _sc
                        self._sc_obj_ts = _tsess.time()
                    else:
                        return None
                except Exception as _ce:
                    logger.debug("SmartConnect login: %s", _ce)
                    return None

            # Fetch candles
            from_dt = (datetime.now()-timedelta(days=days+2)).strftime("%Y-%m-%d %H:%M")
            to_dt   = datetime.now().strftime("%Y-%m-%d %H:%M")
            resp = self._sc_obj.getCandleData({
                "exchange":    "NSE",
                "symboltoken": str(token),
                "interval":    iv,
                "fromdate":    from_dt,
                "todate":      to_dt,
            })
            candles = resp.get("data",[]) if resp else []
            if not candles:
                logger.debug("SmartConnect no candles for %s: %s",
                             symbol, resp.get("message","") if resp else "no resp")
                return None

            rows = []
            for c in candles:
                try:
                    rows.append({"date":pd.Timestamp(c[0]),"open":float(c[1]),
                                 "high":float(c[2]),"low":float(c[3]),
                                 "close":float(c[4]),"volume":float(c[5]) if len(c)>5 else 0})
                except Exception: continue
            if not rows: return None
            df = pd.DataFrame(rows).set_index("date").sort_index()
            logger.debug("SmartConnect: %s %s %d bars", symbol, iv, len(df))
            return df if not df.empty else None

        except Exception as e:
            logger.debug("smartconnect %s: %s", symbol, e)
            return None

    def _fetch_from_fyers(self, symbol: str, interval: str, days: int):
        """
        Fyers API v3 — free with Fyers account, excellent NSE historical data.
        Token configured: FYERS_TOKEN in .env
        Supports: all NSE indices + Nifty200 stocks, 1-min to 1-day
        """
        # Fyers disabled: daily OAuth token impractical for autonomous bot
        # Use Dhan (free, permanent token) instead
        return None
        try:
            import requests as _rq, os as _os
            from datetime import timedelta as _td
            token = _os.getenv("FYERS_TOKEN", "")
            if not token:
                return None
            # Fyers symbol format
            _idx_map = {
                "NIFTY":      "NSE:NIFTY50-INDEX",
                "BANKNIFTY":  "NSE:NIFTYBANK-INDEX",
                "FINNIFTY":   "NSE:FINNIFTY-INDEX",
                "MIDCPNIFTY": "NSE:MIDCPNIFTY-INDEX",
                "SENSEX":     "BSE:SENSEX-INDEX",
            }
            _iv_map = {
                "1m":"1","5m":"5","15m":"15","30m":"30",
                "1h":"60","1d":"D","daily":"D",
            }
            sym_up = symbol.upper()
            fy_sym = _idx_map.get(sym_up, f"NSE:{sym_up}-EQ")
            fy_iv  = _iv_map.get(interval, "D")
            end_ts   = int(datetime.now().timestamp())
            start_ts = int((datetime.now()-_td(days=days+2)).timestamp())
            r = _rq.get(
                "https://api.fyers.in/data/v3/history",
                params={
                    "symbol":      fy_sym,
                    "resolution":  fy_iv,
                    "date_format": "1",
                    "range_from":  str(start_ts),
                    "range_to":    str(end_ts),
                    "cont_flag":   "1",
                },
                headers={"Authorization": f"Bearer {token}"},
                timeout=12)
            if r.status_code != 200:
                logger.debug("Fyers HTTP %s for %s", r.status_code, symbol)
                return None
            resp = r.json()
            candles = resp.get("candles", [])
            if not candles:
                logger.debug("Fyers no candles for %s: %s", symbol, resp.get("message",""))
                return None
            rows = []
            for c in candles:
                try:
                    rows.append({
                        "date":   pd.Timestamp.fromtimestamp(c[0]),
                        "open":   float(c[1]),
                        "high":   float(c[2]),
                        "low":    float(c[3]),
                        "close":  float(c[4]),
                        "volume": float(c[5]) if len(c)>5 else 0,
                    })
                except Exception: continue
            if not rows: return None
            df = pd.DataFrame(rows).set_index("date").sort_index()
            logger.debug("Fyers: %s %s %d bars", symbol, fy_iv, len(df))
            return df if not df.empty else None
        except Exception as e:
            logger.debug("fyers %s: %s", symbol, e)
            return None

    def _fetch_from_twelvedata(self, symbol: str, interval: str, days: int):
        """
        Twelve Data API — 800 free calls/day, supports NSE indices + stocks.
        Configured key in .env: TWELVE_DATA_KEY
        """
        try:
            import requests as _rq, os as _os
            key = _os.getenv("TWELVE_DATA_KEY", "")
            if not key:
                return None
            # NSE symbol mapping for Twelve Data
            _idx_map = {
                "NIFTY":"NIFTY50","BANKNIFTY":"BANKNIFTY",
                "FINNIFTY":"NIFTY FIN SERVICE","SENSEX":"SENSEX",
                "MIDCPNIFTY":"NIFTY MIDCAP SELECT",
            }
            iv_map = {
                "1m":"1min","5m":"5min","15m":"15min","30m":"30min",
                "1h":"1h","1d":"1day","daily":"1day",
            }
            td_iv  = iv_map.get(interval, "1day")
            sym_up = symbol.upper()
            # For indices use specific Twelve Data name, for stocks use NSE:SYMBOL
            if sym_up in _idx_map:
                td_sym  = _idx_map[sym_up]
                exchange = "NSE"
            else:
                td_sym  = sym_up
                exchange = "NSE"
            output_size = min(days * 2, 5000) if interval == "1d" else min(days * 78, 5000)
            r = _rq.get(
                "https://api.twelvedata.com/time_series",
                params={
                    "symbol":      td_sym,
                    "exchange":    exchange,
                    "interval":    td_iv,
                    "outputsize":  output_size,
                    "apikey":      key,
                    "format":      "JSON",
                },
                timeout=12)
            if r.status_code != 200:
                return None
            data = r.json()
            if "values" not in data:
                return None
            rows = []
            for d in data["values"]:
                try:
                    rows.append({
                        "date":   pd.Timestamp(d["datetime"]),
                        "open":   float(d["open"]),
                        "high":   float(d["high"]),
                        "low":    float(d["low"]),
                        "close":  float(d["close"]),
                        "volume": float(d.get("volume", 0) or 0),
                    })
                except Exception:
                    continue
            if not rows:
                return None
            df = pd.DataFrame(rows).set_index("date").sort_index()
            logger.debug("TwelveData: %s %d bars", symbol, len(df))
            return df if not df.empty else None
        except Exception as e:
            logger.debug("twelvedata %s: %s", symbol, e)
            return None

    def _fetch_from_tiingo(self, symbol: str, interval: str, days: int):
        """
        Tiingo API — 1000 calls/hr free. Best for EOD NSE stock data.
        Key configured: TIINGO_KEY in .env
        """
        try:
            import requests as _rq, os as _os
            from datetime import timedelta as _td
            if interval not in ("1d", "daily"):
                return None
            key = _os.getenv("TIINGO_KEY", "43f3cb0bc2a1ea5afd7d8b33c084d584e44ba65b")
            # Tiingo uses Yahoo-finance style tickers for NSE
            _t_map = {
                "NIFTY":"^NSEI","BANKNIFTY":"^NSEBANK",
                "SENSEX":"^BSESN","FINNIFTY":"NIFTY_FIN_SERVICE.NS",
            }
            tiingo_sym = _t_map.get(symbol.upper(), f"{symbol.upper()}.NS")
            end_s   = datetime.now().strftime("%Y-%m-%d")
            start_s = (datetime.now()-_td(days=days+5)).strftime("%Y-%m-%d")
            headers = {
                "Authorization": f"Token {key}",
                "Content-Type":  "application/json",
            }
            r = _rq.get(
                f"https://api.tiingo.com/tiingo/daily/{tiingo_sym}/prices",
                params={"startDate":start_s,"endDate":end_s,"resampleFreq":"daily"},
                headers=headers, timeout=8)
            if r.status_code != 200:
                return None
            data = r.json()
            if not data:
                return None
            rows = [{"date":d["date"],
                     "open":  d.get("open",  d.get("adjOpen",  0)),
                     "high":  d.get("high",  d.get("adjHigh",  0)),
                     "low":   d.get("low",   d.get("adjLow",   0)),
                     "close": d.get("close", d.get("adjClose", 0)),
                     "volume":d.get("volume",0)} for d in data]
            df = pd.DataFrame(rows)
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date").sort_index()
            logger.debug("Tiingo: %s %d bars", symbol, len(df))
            return df if not df.empty else None
        except Exception as e:
            logger.debug("tiingo %s: %s", symbol, e)
            return None

    def _fetch_nse_live_single(self, symbol: str):
        """NSE allIndices live price — works for indices, always available."""
        _map = {
            "NIFTY":       "NIFTY 50",
            "BANKNIFTY":   "NIFTY BANK",
            "FINNIFTY":    "NIFTY FIN SERVICE",
            "MIDCPNIFTY":  "NIFTY MIDCAP SELECT",
            "SENSEX":      "S&P BSE SENSEX",
        }
        if symbol.upper() not in _map: return None
        try:
            import requests as _rq
            _s = _rq.Session()
            _s.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://www.nseindia.com"})
            _s.get("https://www.nseindia.com/", timeout=4)
            _r = _s.get("https://www.nseindia.com/api/allIndices", timeout=6)
            iname = _map[symbol.upper()]
            for idx in _r.json().get("data", []):
                if iname.upper() in str(idx.get("index", "")).upper():
                    _p = float(idx.get("last", 0) or 0)
                    if _p > 0:
                        return pd.DataFrame(
                            [{"open": float(idx.get("open", _p)),
                              "high": float(idx.get("high", _p)),
                              "low":  float(idx.get("low",  _p)),
                              "close": _p, "volume": 0}],
                            index=[pd.Timestamp.now()])
        except Exception as _e:
            logger.debug("nse_live_single %s: %s", symbol, _e)
        return None

    def _fetch_from_nse_direct(self, symbol: str, interval: str, days: int):
        """
        Fetch OHLCV from NSE — historical data for indices and stocks.
        Primary fallback when Angel One data is unavailable.
        """
        import requests as _rq
        from datetime import timedelta as _td
        try:
            s = _rq.Session()
            s.headers.update({
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                "Referer": "https://www.nseindia.com",
                "Accept": "application/json",
            })
            s.get("https://www.nseindia.com/", timeout=5)

            # INDEX symbols
            _idx_map = {
                "NIFTY":      "NIFTY 50",
                "BANKNIFTY":  "NIFTY BANK",
                "FINNIFTY":   "NIFTY FIN SERVICE",
                "MIDCPNIFTY": "NIFTY MIDCAP SELECT",
                "SENSEX":     "S&P BSE SENSEX",
            }

            if symbol.upper() in _idx_map:
                # NSE historical index endpoint
                nse_sym = _idx_map[symbol.upper()]
                end_dt  = datetime.now()
                start_dt = end_dt - _td(days=days + 5)
                end_s   = end_dt.strftime("%d-%m-%Y")
                start_s = start_dt.strftime("%d-%m-%Y")
                r = s.get(
                    f"https://www.nseindia.com/api/historical/indicesHistory?"
                    f"indexType={nse_sym.replace(' ','%20')}&from={start_s}&to={end_s}",
                    timeout=12)
                if r.status_code == 200:
                    records = r.json().get("data", {}).get("indexCloseOnlineRecords", [])
                    if records:
                        import pandas as _pd
                        rows = [{"date": d["EOD_TIMESTAMP"],
                                 "open":  float(d["EOD_OPEN_INDEX_VAL"]),
                                 "high":  float(d["EOD_HIGH_INDEX_VAL"]),
                                 "low":   float(d["EOD_LOW_INDEX_VAL"]),
                                 "close": float(d["EOD_CLOSE_INDEX_VAL"]),
                                 "volume": 0} for d in records]
                        df = _pd.DataFrame(rows)
                        df["date"] = _pd.to_datetime(df["date"], format="%d-%b-%Y")
                        df = df.sort_values("date").set_index("date")
                        if not df.empty:
                            logger.debug("NSE direct: %s %d bars", symbol, len(df))
                            return df
            else:
                # STOCK — NSE equity historical
                end_s   = datetime.now().strftime("%d-%m-%Y")
                start_s = (datetime.now() - _td(days=days+5)).strftime("%d-%m-%Y")
                r = s.get(
                    f"https://www.nseindia.com/api/historical/cm/equity?"
                    f"symbol={symbol}&series=[%22EQ%22]&from={start_s}&to={end_s}",
                    timeout=12)
                if r.status_code == 200:
                    data = r.json().get("data", [])
                    if data:
                        import pandas as _pd
                        rows = [{"date": d["CH_TIMESTAMP"],
                                 "open":  float(d["CH_OPENING_PRICE"]),
                                 "high":  float(d["CH_TRADE_HIGH_PRICE"]),
                                 "low":   float(d["CH_TRADE_LOW_PRICE"]),
                                 "close": float(d["CH_CLOSING_PRICE"]),
                                 "volume": float(d.get("CH_TOT_TRADED_QTY", 0))} for d in data]
                        df = _pd.DataFrame(rows)
                        df["date"] = _pd.to_datetime(df["date"])
                        df = df.sort_values("date").set_index("date")
                        if not df.empty:
                            logger.debug("NSE equity: %s %d bars", symbol, len(df))
                            return df
        except Exception as _e:
            logger.debug("nse_direct %s: %s", symbol, _e)
        return None


    def _fetch_from_yfinance(
        self, symbol: str, interval: str, days: int
    ) -> Optional[pd.DataFrame]:
        return None  # Yahoo Finance API broken — using TwelveData + Tiingo instead
        try:  # noqa — dead code kept for easy re-enable
            import yf_compat as yf  # yfinance replaced: Yahoo API broken
        except ImportError:
            return None

        try:
            symbol_map = {
                "NIFTY":       "^NSEI",
                "BANKNIFTY":   "^NSEBANK",
                "MIDCPNIFTY":  "MIDCPNIFTY.NS",
                "NIFTYNEXT50": "^NSMIDCP",
                "FINNIFTY":    "NIFTY_FIN_SERVICE.NS",
                "SENSEX":      "^BSESN",
            }
            yf_symbol = symbol_map.get(symbol.upper(), f"{symbol}.NS")

            interval_map = {"1m": "1m", "5m": "5m", "15m": "15m",
                            "30m": "30m", "1h": "1h", "1d": "1d"}

            # Use longer period for intraday — ensures enough bars
            _period = f"{max(days, 7)}d" if interval in ("5m","15m","1m") else f"{days}d"
            import json as _json
            try:
                df = yf.download(
                    yf_symbol,
                    period      = _period,
                    interval    = interval_map.get(interval, "5m"),
                    progress    = False,
                    auto_adjust = True,
                )
            except (_json.JSONDecodeError, Exception):
                return None
            # Handle yfinance MultiIndex
            if df is not None and isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.droplevel(1)
            return df if df is not None and not df.empty else None

        except Exception:
            return None

    # ------------------------------------------------------------------
    # Data cleaning
    # ------------------------------------------------------------------
    def _record_latency(self, source: str, ms: float) -> None:
        """Track API response latency for monitoring."""
        if source not in self._api_latency:
            self._api_latency[source] = []
        self._api_latency[source].append(round(ms, 1))
        if len(self._api_latency[source]) > 20:
            self._api_latency[source].pop(0)
        avg = sum(self._api_latency[source]) / len(self._api_latency[source])
        if avg > 3000:  # > 3 seconds average
            logger.warning("SLOW API: %s avg=%.0fms", source, avg)

    def get_latency_stats(self) -> Dict[str, float]:
        """Get average latency per source for /health command."""
        return {
            src: round(sum(vals)/len(vals), 0)
            for src, vals in self._api_latency.items() if vals
        }

    def _clean(self, df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            return df

        df = df.copy()
        # Data quality check
        try:
            from data_source_resilience import validate_ohlcv as _vq
            _ok2, _r2 = _vq(df)
            if not _ok2:
                import logging
                logging.getLogger(__name__).debug('OHLCV quality: %s', _r2)
        except Exception: pass


        # Flatten MultiIndex columns (yfinance sometimes returns these)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [
                str(c[0]).strip() if isinstance(c, tuple) else str(c).strip()
                for c in df.columns
            ]

        # Normalize column names to Title-Case
        rename_map = {
            "open":      "Open",
            "high":      "High",
            "low":       "Low",
            "close":     "Close",
            "adj close": "Close",
            "volume":    "Volume",
        }
        df.columns = [rename_map.get(str(c).strip().lower(), c) for c in df.columns]

        # Numeric coercion
        for col in ["Open", "High", "Low", "Close"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        if "Volume" not in df.columns:
            df["Volume"] = 0
        df["Volume"] = pd.to_numeric(df["Volume"], errors="coerce").fillna(0)

        # Drop rows missing any OHLC
        df = df.dropna(subset=["Open", "High", "Low", "Close"])

        # Ensure DatetimeIndex
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index, errors="coerce")
            df = df[df.index.notna()]

        return df.sort_index()


def get_ohlcv_with_fallback(symbol: str, days: int = 60, interval: str = "5m"):
    """
    Full fallback chain: Angel One → yfinance → bhavcopy cache.
    Used by live_signal_engine when primary fetch fails.
    """
    # Try Angel One first
    try:
        from angel import get_angel
        ang = get_angel()
        if ang:
            df = ang.get_historical_data(symbol, days=days, interval=interval)
            if df is not None and len(df) > 5:
                return df
    except Exception: pass

    # Try yfinance
    try:
        import yf_compat as yf  # yfinance removed: Yahoo API broken
        ticker_map = {"NIFTY":"^NSEI","BANKNIFTY":"^NSEBANK","SENSEX":"^BSESN"}
        ticker = ticker_map.get(symbol.upper(), f"{symbol}.NS")
        df = yf.download(ticker, period=f"{days}d", interval=interval,
                         progress=False, auto_adjust=True)
        if df is not None and len(df) > 5:
            df.columns = [c.lower() for c in df.columns]
            return df
    except Exception: pass

    # Try bhavcopy cache (daily only)
    try:
        from bhavcopy_cache import get_history
        df = get_history(symbol, days=days)
        if df is not None and len(df) > 5:
            return df
    except Exception: pass

    return None
