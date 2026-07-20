from __future__ import annotations

# Load .env so credentials are available
try:
    from dotenv import load_dotenv
    load_dotenv('.env')
except ImportError:
    pass

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
        paper_trade: bool = False,  # Default false — data fetch always needs real connection
        symbols_csv: Optional[str] = None,
    ) -> None:
        self.angel       = angel
        self.paper_trade = paper_trade

        # Cache: {cache_key: {"data": DataFrame, "time": datetime}}
        self.cache: Dict[str, Dict[str, Any]] = {}
        self._MAX_CACHE = 500  # max entries before eviction
        self._api_latency: Dict[str, list] = {}  # source → [ms, ms, ...]

        self.indices    = _INDEX_SYMBOLS
        _raw_symbols = self._load_symbol_list(symbols_csv)
        self.full_symbol_universe = self._dedupe_symbols(list(_raw_symbols))
        try:
            from universe_manager import build_learning_universe, describe_universe
            self.nifty_200 = build_learning_universe(_raw_symbols)
            _ud = describe_universe(_raw_symbols)
            logger.info(
                "Learning universe loaded: mode=%s count=%d probation=%s live=%s",
                _ud.get("learning_mode"),
                _ud.get("learning_count"),
                _ud.get("probation_universe"),
                _ud.get("live_universe"),
            )
        except Exception as exc:
            logger.warning("Universe manager unavailable: %s", exc)
            self.nifty_200 = _raw_symbols
        # GA-8: semaphore limits concurrent historical API calls to avoid 429 errors
        # Angel One allows ~3-5 historical requests/second
        self._hist_semaphore = threading.Semaphore(3)

    @staticmethod
    def _dedupe_symbols(symbols: List[str]) -> List[str]:
        seen = set()
        out: List[str] = []
        for symbol in symbols:
            sym = str(symbol or "").strip().upper()
            if not sym or sym in seen:
                continue
            seen.add(sym)
            out.append(sym)
        return out

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

    def _is_full_universe_scan_slot(self) -> bool:
        try:
            import config as _cfg
            if not bool(getattr(_cfg, "ENABLE_TIERED_FULL_UNIVERSE_SCAN", True)):
                return False
            interval = max(5, int(getattr(_cfg, "FULL_UNIVERSE_SCAN_INTERVAL_MIN", 15) or 15))
            now = datetime.now()
            return (now.minute % interval) < 5
        except Exception:
            return False

    def get_ordered_symbols(self, include_full_universe: Optional[bool] = None) -> List[str]:
        """Return the exact priority-ordered universe used by scan fetches."""
        # NIFTYNEXT50 dropped from the scan universe: it has NO working candle
        # source (not in Angel's scrip master → AB4047; Upstox returns
        # wrong-interval bars) and no liquid options, so every cycle wasted a
        # failed fetch + reject-log spam. The index is still defined elsewhere
        # (expiry_regime, backtests) — this only removes it from live scanning.
        priority_1 = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"]
        priority_2 = [s for s in self.indices if s not in priority_1]
        priority_3 = [s for s in self.nifty_200 if s not in self.indices and s not in priority_1]
        full_scan = self._is_full_universe_scan_slot() if include_full_universe is None else bool(include_full_universe)
        priority_4: List[str] = []
        if full_scan:
            try:
                import config as _cfg
                max_full = max(1, int(getattr(_cfg, "FULL_UNIVERSE_SCAN_MAX_SYMBOLS", 220) or 220))
            except Exception:
                max_full = 220
            priority_4 = [
                s for s in self.full_symbol_universe[:max_full]
                if s not in self.indices and s not in priority_1 and s not in priority_3
            ]
        ordered_symbols = priority_1 + priority_2 + priority_3 + priority_4
        return self._dedupe_symbols(ordered_symbols)

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

        symbols = self.get_ordered_symbols()

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

        # ── Parallel data fetch (10 threads, ~8x faster) ────────────
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import datetime as _dt_par

        def _fetch_one_symbol(_sym):
            try:
                _d = self.get_market_data(_sym)
                if _d is not None and not _d.empty:
                    # ── Post-market aware freshness gate ──
                    # During 9:15 AM - 3:30 PM (market hours): accept 5m bars up to 30 min old
                    # Outside market hours: accept bars up to 24 hours old (yesterday's close data)
                    now = _dt_par.datetime.now()
                    hour = now.hour
                    minute = now.minute
                    
                    # Check if currently in market hours (9:15 AM to 3:30 PM)
                    in_market_hours = (
                        (hour > 9 and hour < 15) or  # 10 AM to 2:59 PM
                        (hour == 9 and minute >= 15) or  # 9:15 AM onwards
                        (hour == 15 and minute <= 30)  # up to 3:30 PM
                    )
                    
                    # During market: stricter (30 min), outside market: lenient (24 hr)
                    _ma = 30 if in_market_hours else 1440
                    
                    if self._check_data_freshness(_d, _sym, _ma):
                        return _sym, _d
                return _sym, None
            except Exception:
                return _sym, None

        with ThreadPoolExecutor(max_workers=10) as _tpe:
            _futs = [_tpe.submit(_fetch_one_symbol, s) for s in symbols]
            for _f in as_completed(_futs, timeout=120):
                try:
                    _s, _d = _f.result(timeout=15)
                    if _d is not None:
                        all_data[_s] = _d
                except Exception:
                    pass

        if not all_data:
            # Replicate the same in_market_hours logic used in _fetch_one_symbol above
            _now_diag = _dt_par.datetime.now()
            _dh, _dm = _now_diag.hour, _now_diag.minute
            _in_mh = (_dh > 9 and _dh < 15) or (_dh == 9 and _dm >= 15) or (_dh == 15 and _dm <= 30)
            _eff_max_age = 30 if _in_mh else 1440
            # Diagnose: is angel set? did the patch fire? what's obj state?
            _ang_state = "None"
            if self.angel is not None:
                _has_ghd = hasattr(self.angel, "get_historical_data")
                _obj_set = getattr(self.angel, "obj", None) is not None
                _paper = getattr(self.angel, "paper_trade", "?")
                _ang_state = f"set(ghd={_has_ghd},obj={_obj_set},paper={_paper})"
            logger.error(
                "SCANNED: 0 — No data fetched for ANY of %d symbols. "
                "angel=%s freshness_gate=%d min. "
                "Possible causes: Angel session expired, NSE rate limit, "
                "or data freshness gate too strict. "
                "Run /diagscan to confirm, /fixangel to reconnect.",
                len(symbols), _ang_state, _eff_max_age,
            )
            # Try force session refresh for next cycle
            try:
                if hasattr(self, 'angel') and hasattr(self.angel, '_auto_refresh_session'):
                    self.angel._auto_refresh_session()
                    logger.info('Angel session force-refreshed after Scanned:0')
            except Exception: pass
        else:
            idx_count   = sum(1 for s in all_data if s in set(self.indices + _INDEX_SYMBOLS))
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

        symbols = self.get_ordered_symbols()

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

        symbols = self.get_ordered_symbols()

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
                # Only bypass the freshness gate when the caller already passed a lenient
                # threshold (outside market hours: 1440 min). During market hours (30 min)
                # stale intraday data must be rejected — signals from old prices are harmful.
                if max_age_minutes >= 1440:
                    logger.debug("EOD/post-market data %s: age=%.0fm (accepted, lenient gate)", symbol, age_min)
                    return True
                logger.warning(
                    "STALE DATA: %s last candle is %.0f min old (max=%d) — skipping",
                    symbol, age_min, max_age_minutes,
                )
                return False
            return True
        except Exception:
            return True   # on error, don't block

    def _persist_intraday_candles(
        self,
        symbol: str,
        interval: str,
        df,
        source: str = "",
    ) -> None:
        """
        Save fetched candles into candle_cache.db for EOD mining/training.

        Some fallback paths return daily bars even when the caller requested 5m.
        Check timestamp spacing before writing so daily data is not stored under
        an intraday interval.
        """
        if df is None or len(df) < 2:
            return
        try:
            df = df.copy()
            df.columns = [str(c).lower() for c in df.columns]
        except Exception:
            return
        interval = str(interval or "").lower()
        expected_minutes = {
            "1m": 1,
            "5m": 5,
            "15m": 15,
            "30m": 30,
            "1h": 60,
            "1d": 1440,
        }.get(interval)
        if expected_minutes is None:
            return
        try:
            idx = pd.to_datetime(df.index, errors="coerce")
            idx = idx[~idx.isna()]
            if len(idx) >= 3:
                diffs = pd.Series(idx).sort_values().diff().dropna().dt.total_seconds() / 60.0
                diffs = diffs[diffs > 0]
                if len(diffs) > 0:
                    median_minutes = float(diffs.median())
                    if interval != "1d" and median_minutes > expected_minutes * 3:
                        logger.debug(
                            "Skip candle cache save %s %s from %s: spacing %.1fm",
                            symbol, interval, source, median_minutes,
                        )
                        return
        except Exception:
            return
        try:
            from candle_cache import save_candles

            saved = save_candles(symbol, interval, df)
            if saved == 0 and len(df) > 0:
                # save_candles() itself logs a warning on a real exception;
                # this covers the OTHER silent-zero path -- every row in df
                # failed _valid_ohlc() or the spacing guard above already
                # returned early. Either way, a fetch that reported success
                # writing zero rows is exactly the kind of gap that let
                # 2026-07-20's multi-day candle staleness go unnoticed.
                logger.warning("candle cache: %s %s fetch returned %d rows but 0 were saved",
                               symbol, interval, len(df))
        except Exception as exc:
            logger.warning("candle cache persist failed %s %s: %s", symbol, interval, exc, exc_info=True)

    def _data_matches_interval(self, interval: str, df, *, min_points: int = 3) -> bool:
        """Return False when an intraday request received daily-shaped bars."""
        interval = str(interval or "").lower()
        expected_minutes = {
            "1m": 1,
            "5m": 5,
            "15m": 15,
            "30m": 30,
            "1h": 60,
            "1d": 1440,
        }.get(interval)
        if expected_minutes is None or df is None or len(df) == 0:
            return False
        if interval == "1d":
            return True
        try:
            idx = pd.to_datetime(df.index, errors="coerce")
            idx = idx[~idx.isna()]
            if len(idx) < min_points:
                return True
            diffs = pd.Series(idx).sort_values().diff().dropna().dt.total_seconds() / 60.0
            diffs = diffs[diffs > 0]
            if len(diffs) == 0:
                return True
            return float(diffs.median()) <= expected_minutes * 3
        except Exception:
            return False

    def _cache_market_data(
        self,
        cache_key: str,
        symbol: str,
        interval: str,
        df,
        source: str = "",
    ):
        self.cache[cache_key] = {"data": df, "time": datetime.now()}
        self._persist_intraday_candles(symbol, interval, df, source=source)
        return df

    def _accept_market_data(
        self,
        cache_key: str,
        symbol: str,
        interval: str,
        df,
        source: str = "",
    ):
        if df is None or getattr(df, "empty", False):
            return None
        if not self._data_matches_interval(interval, df):
            logger.warning(
                "Reject %s %s from %s: returned bars do not match requested interval",
                symbol, interval, source,
            )
            return None
        # Symbol-resolution guard. Some fallback vendors silently resolve an
        # index alias to an ETF (MIDCPNIFTY was returned near 880 while the
        # index traded near 14,300). Such bars have valid OHLC/spacing, so the
        # ordinary shape checks cannot detect them.
        index_floors = {
            "NIFTY": 5_000.0,
            "BANKNIFTY": 10_000.0,
            "FINNIFTY": 5_000.0,
            "MIDCPNIFTY": 3_000.0,
            "NIFTYNEXT50": 10_000.0,
            "SENSEX": 10_000.0,
            "BANKEX": 10_000.0,
        }
        floor = index_floors.get(str(symbol or "").upper())
        if floor is not None:
            try:
                cols = {str(c).lower(): c for c in df.columns}
                close_col = cols.get("close")
                latest = float(df[close_col].dropna().iloc[-1]) if close_col is not None else 0.0
            except Exception:
                latest = 0.0
            if latest < floor:
                logger.error(
                    "Reject %s %s from %s: price %.2f below index floor %.2f "
                    "(probable symbol/ETF mismatch)",
                    symbol, interval, source, latest, floor,
                )
                return None
        return self._cache_market_data(cache_key, symbol, interval, df, source=source)

    # ------------------------------------------------------------------
    def get_market_data(
        self,
        symbol:   str,
        interval: str = "5m",
        days:     int = 5,
    ) -> Optional[pd.DataFrame]:
        interval = str(interval or "5m").lower()
        intraday_requested = interval in {"1m", "5m", "15m", "30m", "1h"}
        cache_key = f"{symbol}_{interval}_{days}"
        expiry    = 60 if self._is_market_hours() else 600

        if cache_key in self.cache:
            cached = self.cache[cache_key]
            age    = (datetime.now() - cached["time"]).total_seconds()
            if age < expiry:
                if self._data_matches_interval(interval, cached["data"]):
                    return cached["data"]
                self.cache.pop(cache_key, None)

        # ── Upstox Historical V2 FIRST — FREE, no auth, no daily token expiry ──
        # Promoted ahead of Angel: apiconnect.angelone.in has been timing out
        # (connect timeout=7) and stalling intraday scans into Scanned:0. Angel +
        # the full fallback chain below stay in place; _accept_market_data
        # validates the interval, so wrong-interval index bars (e.g. some
        # NIFTYNEXT50/SENSEX from Upstox) are rejected and fall through to Angel
        # automatically. Intraday only — daily bars keep the existing order. The
        # cache check above still short-circuits first, so this only fires on a
        # cache miss.
        if intraday_requested:
            try:
                from upstox_data import get_candles as _upstox_get
                _ux = _upstox_get(symbol, interval=interval, days=max(days, 5))
                if _ux is not None and len(_ux) >= 5:
                    _ux.columns = [c.lower() for c in _ux.columns]
                    accepted = self._accept_market_data(
                        cache_key, symbol, interval, _ux, source="upstox_primary")
                    if accepted is not None:
                        logger.info("Upstox-first ✅ %s %s: %d bars",
                                    symbol, interval, len(_ux))
                        return accepted
            except Exception as _ue0:
                logger.debug("Upstox-first %s: %s", symbol, _ue0)

        # Angel One — primary data source (free, NSE-native)
        import time as _rl; _rl.sleep(0.3)  # rate limit Angel
        if self.angel and hasattr(self.angel, "get_historical_data"):
            try:
                with self._hist_semaphore:
                    # Try requested interval first. Do not satisfy intraday
                    # callers with daily bars; that poisons signals/training.
                    df = self._fetch_from_angel(symbol, interval, days)
                    if (df is None or df.empty) and not intraday_requested:
                        df = self._fetch_from_angel(symbol, "1d", max(days,30))
                if df is not None and not df.empty:
                    df = self._clean(df)
                    accepted = self._accept_market_data(cache_key, symbol, interval, df, source="angel")
                    if accepted is not None:
                        try:
                            from data_source_resilience import check_and_alert_failover
                            check_and_alert_failover("Angel One", True)
                        except Exception: pass
                        return accepted
            except Exception as exc:
                logger.debug("Angel failed for %s: %s", symbol, exc)
                try:
                    from data_source_resilience import check_and_alert_failover
                    check_and_alert_failover("Angel One", False,
                                             getattr(self,"_alerts",None))
                except Exception: pass

        # ── Quick yfinance fallback when Angel returns nothing ────────────────
        # Gate: set DISABLE_YFINANCE=true in .env to skip (default: enabled)
        _yf_disabled = getattr(__import__("config"), "DISABLE_YFINANCE", False)
        if not _yf_disabled:
            try:
                import yf_compat as yf  # yfinance removed: Yahoo API broken
                _ysym = symbol.upper()
                if _ysym in {"NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY","SENSEX"}:
                    _ymap = {"NIFTY":"^NSEI","BANKNIFTY":"^NSEBANK","SENSEX":"^BSESN",
                             "FINNIFTY":"NIFTY_FIN_SERVICE.NS","MIDCPNIFTY":"NIFTY_MIDCAP_SELECT.NS"}
                    _yt = _ymap.get(_ysym, f"{_ysym}.NS")
                else:
                    _yt = f"{_ysym}.NS"
                _ydf = yf.download(_yt, period="5d", interval=f"{interval}",
                                    progress=False, auto_adjust=True)
                if _ydf is not None and len(_ydf) >= 10:
                    _ydf.columns = [c.lower() if isinstance(c,str) else c[0].lower()
                                    for c in _ydf.columns]
                    accepted = self._accept_market_data(cache_key, symbol, interval, _ydf, source="yf_compat_quick")
                    if accepted is not None:
                        logger.info("yfinance fallback OK for %s (%d bars)", symbol, len(_ydf))
                        return accepted
            except Exception as _yfe:
                logger.debug("yfinance fallback failed %s: %s", symbol, _yfe)

        # Fallback chain: SmartConnect direct → Angel wrapper → Fyers → TwelveData
        try:
            # SmartConnect direct. Keep intraday requests intraday even after
            # market close so EOD learning can fetch same-day bars.
            _use_iv = interval if intraday_requested else ("5m" if self._is_market_hours() else "1d")
            _use_days = days if intraday_requested or _use_iv == "5m" else max(days, 60)
            df_1d = self._fetch_via_smartconnect(symbol, _use_iv, _use_days)
            # If 5m returns too few bars, add 1d history for indicators
            if df_1d is not None and len(df_1d) < 50 and _use_iv == "5m":
                df_hist = self._fetch_via_smartconnect(symbol, "1d", 60)
                if df_hist is not None and len(df_hist) > 0:
                    import pandas as _pd2
                    df_1d = _pd2.concat([df_hist, df_1d]).tail(200)
                    df_1d = df_1d[~df_1d.index.duplicated(keep="last")]

            # Public Upstox historical endpoint: no auth, useful before weaker
            # delayed/global fallbacks. For stocks it depends on an instrument
            # key in local masters, so failure is silent.
            if df_1d is None or (hasattr(df_1d,"empty") and df_1d.empty):
                try:
                    from upstox_data import get_candles as _upstox_get
                    df_1d = _upstox_get(symbol, interval=_use_iv, days=max(_use_days, 5))
                except Exception:
                    pass

            # Dhan is intentionally optional; no credential setup is attempted here.
            if df_1d is None or (hasattr(df_1d,"empty") and df_1d.empty):
                try:
                    from dhan_client import get_historical_data as _dh, is_configured as _dok
                    if _dok():
                        df_1d = _dh(symbol, interval=interval if intraday_requested else "1d", days=max(days,60))
                except Exception: pass
            # Angel wrapper if above fail
            if df_1d is None or (hasattr(df_1d,"empty") and df_1d.empty):
                if not intraday_requested and hasattr(self.angel,"get_historical_data") and self.angel:
                    df_1d = self._fetch_from_angel(symbol, "1d", max(days, 30))
            # Twelve Data as next backup
            if not intraday_requested and (df_1d is None or (hasattr(df_1d,"empty") and df_1d.empty)):
                df_1d = self._fetch_from_twelvedata(symbol, "1d", max(days, 60))
            # Tiingo last
            if not intraday_requested and (df_1d is None or (hasattr(df_1d,"empty") and df_1d.empty)):
                df_1d = self._fetch_from_tiingo(symbol, "1d", max(days, 60))
            # NSE direct for historical bars
            if not intraday_requested and (df_1d is None or (hasattr(df_1d,"empty") and df_1d.empty)):
                df_1d = self._fetch_from_nse_direct(symbol, "1d", max(days, 60))
            # NSE allIndices live price (indices only — always works)
            if not intraday_requested and (df_1d is None or (hasattr(df_1d,"empty") and df_1d.empty)):
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
                accepted = self._accept_market_data(cache_key, symbol, interval, df, source="fallback_chain")
                if accepted is not None:
                    return accepted
        except Exception as e:
            logger.warning("All data sources failed for %s: %s", symbol, e)

        # ── Dhan API (permanent token, no daily login) ───────────────────────
        try:
            from dhan_client import get_historical_data as _dhan_hist, is_configured as _dhan_ok
            if _dhan_ok():
                _dhan_df = _dhan_hist(symbol, interval, days)
                if _dhan_df is not None and not _dhan_df.empty:
                    _dhan_df = self._clean(_dhan_df)
                    accepted = self._accept_market_data(cache_key, symbol, interval, _dhan_df, source="dhan")
                    if accepted is not None:
                        logger.info('Dhan fallback ✅ %s (%d bars)', symbol, len(_dhan_df))
                        return accepted
        except Exception as _de:
            logger.debug('Dhan fallback %s: %s', symbol, _de)

        # ── Zerodha Kite Connect (if configured — best quality) ────────────────
        try:
            from zerodha_client import get_historical_data as _zd_hist, is_configured as _zd_ok
            if _zd_ok():
                _zd = _zd_hist(symbol, interval, days)
                if _zd is not None and not _zd.empty:
                    _zd = self._clean(_zd)
                    accepted = self._accept_market_data(cache_key, symbol, interval, _zd, source="zerodha")
                    if accepted is not None:
                        logger.info("Zerodha fallback OK: %s (%d bars)", symbol, len(_zd))
                        return accepted
        except Exception as _ze:
            logger.debug("Zerodha fallback %s: %s", symbol, _ze)

        # ── Local candle cache (instant, no API call) ────────────────────
        try:
            from candle_cache import get_cached_candles
            _cached = get_cached_candles(symbol, interval, days=max(days, 5))
            if _cached is not None and len(_cached) >= 5:
                if self._data_matches_interval(interval, _cached):
                    return _cached
        except Exception as _ce:
            logger.debug("Cache miss %s: %s", symbol, _ce)

        # ── Upstox Historical V2 (FREE, no auth, no daily login) ─────────
        try:
            from upstox_data import get_candles as _upstox_get
            _upstox_df = _upstox_get(symbol, interval=interval, days=max(days, 5))
            if _upstox_df is not None and len(_upstox_df) >= 5:
                _upstox_df.columns = [c.lower() for c in _upstox_df.columns]
                accepted = self._accept_market_data(cache_key, symbol, interval, _upstox_df, source="upstox")
                if accepted is not None:
                    logger.info("Upstox ✅ %s %s: %d bars", symbol, interval, len(_upstox_df))
                    return accepted
        except Exception as _ue:
            logger.debug("Upstox %s: %s", symbol, _ue)

        # ── Bhavcopy cache fallback (always available offline) ───────────────
        try:
            from bhavcopy_cache import get_ohlcv as _bhav_ohlcv
            _bhav_df = _bhav_ohlcv(symbol, days=365)  # always get max history
            if not intraday_requested and _bhav_df is not None and len(_bhav_df) >= 5:
                _bhav_df.columns = [c.lower() for c in _bhav_df.columns]
                accepted = self._accept_market_data(cache_key, symbol, interval, _bhav_df, source="bhavcopy")
                if accepted is not None:
                    logger.info("Bhavcopy fallback ✅ %s (%d bars)", symbol, len(_bhav_df))
                    return accepted
        except Exception as _bhe:
            logger.debug("Bhavcopy fallback %s: %s", symbol, _bhe)


        # ── NSE Index Historical API (for NIFTY, BANKNIFTY etc) ──────────
        _IDX_SET = {"NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY","SENSEX","NIFTYNEXT50","NIFTYIT","NIFTYBANK"}
        if symbol.upper().replace(" ","") in _IDX_SET or "NIFTY" in symbol.upper():
            try:
                import requests
                _idx_name_map = {
                    "NIFTY": "NIFTY 50", "BANKNIFTY": "NIFTY BANK",
                    "FINNIFTY": "NIFTY FIN SERVICE", "MIDCPNIFTY": "NIFTY MIDCAP SELECT",
                    "SENSEX": "S&P BSE SENSEX", "NIFTYNEXT50": "NIFTY NEXT 50",
                    "NIFTYIT": "NIFTY IT", "NIFTYBANK": "NIFTY BANK",
                }
                _idx = _idx_name_map.get(symbol.upper(), symbol)
                _s = requests.Session()
                _s.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://www.nseindia.com"})
                _s.get("https://www.nseindia.com/", timeout=5)
                _end = datetime.now().strftime("%d-%m-%Y")
                _start = (datetime.now() - timedelta(days=365)).strftime("%d-%m-%Y")
                _r = _s.get(
                    f"https://www.nseindia.com/api/historical/indicesHistory"
                    f"?indexType={_idx}&from={_start}&to={_end}",
                    timeout=10,
                )
                if _r.status_code == 200:
                    _recs = _r.json().get("data", {}).get("indexCloseOnlineRecords", [])
                    if _recs and len(_recs) >= 5:
                        import pandas as _pd_idx
                        _rows = []
                        for _d in _recs:
                            _rows.append({
                                "date": _pd_idx.Timestamp(_d.get("EOD_TIMESTAMP",""), dayfirst=True),
                                "open": float(_d.get("EOD_OPEN_INDEX_VAL", 0) or 0),
                                "high": float(_d.get("EOD_HIGH_INDEX_VAL", 0) or 0),
                                "low": float(_d.get("EOD_LOW_INDEX_VAL", 0) or 0),
                                "close": float(_d.get("EOD_CLOSE_INDEX_VAL", 0) or 0),
                                "volume": int(_d.get("TRADED_QTY", 0) or 0),
                            })
                        _idx_df = _pd_idx.DataFrame(_rows).set_index("date").sort_index()
                        _idx_df = _idx_df[_idx_df["close"] > 0]
                        if len(_idx_df) >= 5:
                            accepted = self._accept_market_data(cache_key, symbol, interval, _idx_df, source="nse_index")
                            if accepted is not None:
                                logger.info("NSE index API ✅ %s (%d bars)", symbol, len(_idx_df))
                                return accepted
            except Exception as _nse_e:
                logger.debug("NSE index %s: %s", symbol, _nse_e)

        # ── yf_compat fallback (Stooq → Yahoo → cached) ─────────────────────
        try:
            import yf_compat as _yfc
            _yf_map = {"NIFTY":"^NSEI","BANKNIFTY":"^NSEBANK","SENSEX":"^BSESN"}
            _yf_sym = _yf_map.get(symbol.upper(), symbol.upper() + ".NS")
            _yf_df = _yfc.download(_yf_sym, period=f"{days}d", interval=interval)
            if _yf_df is not None and not _yf_df.empty and len(_yf_df) >= 5:
                _yf_df.columns = [c.lower() for c in _yf_df.columns]
                accepted = self._accept_market_data(cache_key, symbol, interval, _yf_df, source="yf_compat")
                if accepted is not None:
                    logger.info("yf_compat fallback ✅ %s (%d bars)", symbol, len(_yf_df))
                    return accepted
        except Exception as _yfe:
            logger.debug("yf_compat fallback %s: %s", symbol, _yfe)

        # ── Upstox / Dhan extended fallback (GAP 3 & 4) ─────────────────────
        try:
            from data_source_resilience import get_intraday_with_fallback
            _ext = get_intraday_with_fallback(symbol, days,
                                              getattr(self,'angel',None))
            if _ext is not None and not _ext.empty:
                _ext = self._clean(_ext)
                accepted = self._accept_market_data(cache_key, symbol, interval, _ext, source="extended_fallback")
                if accepted is not None:
                    return accepted
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
    Full fallback chain: Angel One → Upstox public → local cache → yfinance → bhavcopy.
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

    # Try public Upstox historical candles before delayed/global fallbacks.
    try:
        from upstox_data import get_candles
        df = get_candles(symbol, interval=interval, days=max(days, 5))
        if df is not None and len(df) > 5:
            df.columns = [c.lower() for c in df.columns]
            return df
    except Exception: pass

    # Try local candle cache.
    try:
        from candle_cache import get_cached_candles
        df = get_cached_candles(symbol, interval, days=max(days, 5))
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
