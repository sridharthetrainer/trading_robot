"""
nse_master.py

Dynamic NSE master data fetcher and cache.

Replaces ALL hardcoded lot sizes and trading holidays with live data
fetched from Angel One and NSE, cached locally so the system works
even when internet is temporarily unavailable.

────────────────────────────────────────────────────────────────────
LOT SIZES
────────────────────────────────────────────────────────────────────
Source 1 (best): Angel One ScripMaster JSON
  URL: https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json
  Format: JSON array — each instrument has name, lotsize, exch_seg, instrumenttype
  Filter: exch_seg="NFO", instrumenttype="OPTIDX", name in (NIFTY, BANKNIFTY, ...)
  Refresh: monthly (NSE changes lot sizes quarterly in March/June/Sep/Dec)

Source 2 (fallback): NSE FO Underlying API
  URL: https://www.nseindia.com/api/underlying-information
  Requires: NSE session cookie first from nseindia.com/

Source 3 (fallback): Hardcoded defaults (always available, may be stale)
  NIFTY=75, BANKNIFTY=30, FINNIFTY=65, MIDCPNIFTY=75

────────────────────────────────────────────────────────────────────
TRADING HOLIDAYS
────────────────────────────────────────────────────────────────────
Source 1 (best): NSE Holiday Master API
  URL: https://www.nseindia.com/api/holiday-master?type=trading
  Returns: all NSE trading holidays for current + next year
  Refresh: yearly (or when NSE announces changes)

Source 2 (fallback): Angel One SmartAPI (if connected)
  Angel One API may return holiday calendar via SDK

Source 3 (fallback): pandas_market_calendars (pip install pandas_market_calendars)
  Library with NSE calendar preloaded

Source 4 (fallback): Hardcoded 2025-2026 list

────────────────────────────────────────────────────────────────────
CACHE BEHAVIOUR
────────────────────────────────────────────────────────────────────
Files:
  lot_sizes.json          — updated monthly
  trading_holidays.json   — updated yearly (every Jan 1)
  nse_master_status.json  — last fetch timestamps

On startup:
  1. Load from cache (immediate, no internet needed)
  2. If cache is stale → background thread fetches fresh data
  3. Fresh data replaces cache + updates in-memory state

On failure:
  Falls back to hardcoded defaults silently
  Logs warning but never crashes the trading system

────────────────────────────────────────────────────────────────────
USAGE
────────────────────────────────────────────────────────────────────
    from nse_master import get_nse_master
    master = get_nse_master()

    # Lot sizes
    lot = master.get_lot_size("NIFTY")         # → 65
    lot = master.get_lot_size("BANKNIFTY")     # → 30
    sizes = master.get_all_lot_sizes()         # → {"NIFTY":  65,  # Updated Apr 2026 "BANKNIFTY":30, ...}

    # Holidays
    is_holiday = master.is_trading_holiday(date.today())  # → True/False
    is_open    = master.is_market_open_today()            # → True/False
    holidays   = master.get_holidays_this_year()          # → [date, ...]
    next_open  = master.next_trading_day()                # → date

    # Force refresh
    master.refresh_lot_sizes()
    master.refresh_holidays()
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# ── Cache files ────────────────────────────────────────────────────────────────
LOT_SIZES_CACHE    = "lot_sizes.json"
HOLIDAYS_CACHE     = "trading_holidays.json"
STATUS_FILE        = "nse_master_status.json"

# ── Refresh intervals ─────────────────────────────────────────────────────────
LOT_SIZE_REFRESH_DAYS  = 30    # refresh lot sizes every 30 days
HOLIDAY_REFRESH_DAYS   = 90    # refresh holidays every 90 days (NSE updates rarely)

# ── Hardcoded fallbacks (always correct unless NSE changes them) ───────────────
DEFAULT_LOT_SIZES: Dict[str, int] = {
    "NIFTY":  65,  # Revised for Jan 2026+ contracts
    "BANKNIFTY":  30,    # SEBI Nov 2024: 15 → 30 (₹15L minimum)
    "FINNIFTY":   60,    # per Angel master contract Jun 2026 (was 65)
    "MIDCPNIFTY":  120,  # Updated Apr 2026    # unchanged since 2023
    "SENSEX":  20,  # Updated Apr 2026
    "BANKEX":  30,  # per Angel master contract Jun 2026 (was 15)
}

# ── Hardcoded holiday fallback 2025-2026 ─────────────────────────────────────
DEFAULT_HOLIDAYS_2025: List[str] = [
    "2025-01-26",  # Republic Day
    "2025-02-26",  # Mahashivratri
    "2025-03-14",  # Holi
    "2025-04-14",  # Dr Ambedkar Jayanti
    "2025-04-18",  # Good Friday
    "2025-05-01",  # Maharashtra Day
    "2025-08-15",  # Independence Day
    "2025-08-27",  # Ganesh Chaturthi
    "2025-10-02",  # Gandhi Jayanti
    "2025-10-24",  # Dussehra
    "2025-11-05",  # Diwali (Laxmi Pujan)
    "2025-11-14",  # Gurunanak Jayanti
    "2025-12-25",  # Christmas
]

DEFAULT_HOLIDAYS_2026: List[str] = [
    "2026-01-26",  # Republic Day
    "2026-03-06",  # Mahashivratri
    "2026-03-25",  # Holi (tentative)
    "2026-04-03",  # Good Friday
    "2026-04-14",  # Dr Ambedkar Jayanti
    "2026-05-01",  # Maharashtra Day
    "2026-08-15",  # Independence Day
    "2026-10-02",  # Gandhi Jayanti
    "2026-12-25",  # Christmas
]


class NSEMaster:
    """
    Manages dynamic lot sizes and trading holidays.
    Thread-safe. Singleton pattern recommended.
    """

    def __init__(
        self,
        cache_dir:    str  = ".",
        auto_refresh: bool = True,
    ) -> None:
        self._cache_dir   = Path(cache_dir)
        self._lock        = threading.RLock()
        self._lot_sizes:  Dict[str, int]  = {}
        self._holidays:   Set[date]       = set()
        self._status:     Dict[str, Any]  = {}

        # Load from cache on startup
        self._load_from_cache()

        # If cache is empty, load fallback defaults
        if not self._lot_sizes:
            self._lot_sizes = dict(DEFAULT_LOT_SIZES)
        if not self._holidays:
            self._load_default_holidays()

        # Refresh in background if stale
        if auto_refresh:
            threading.Thread(
                target=self._background_refresh,
                daemon=True,
                name="nse-master-refresh",
            ).start()

    # ─────────────────────────────────────────────────────────────────────────
    # PUBLIC API
    # ─────────────────────────────────────────────────────────────────────────

    def get_lot_size(self, underlying: str) -> int:
        """
        Return lot size for an underlying.
        Checks live cache first, falls back to hardcoded defaults.
        """
        with self._lock:
            sym  = underlying.upper().strip()
            size = self._lot_sizes.get(sym)
            if size and size > 0:
                return int(size)
            # Try partial match (e.g. "NIFTY50" → "NIFTY")
            for k, v in self._lot_sizes.items():
                if sym.startswith(k) or k.startswith(sym):
                    return int(v)
            # Hardcoded fallback
            return DEFAULT_LOT_SIZES.get(sym, 75)

    def get_all_lot_sizes(self) -> Dict[str, int]:
        """Return all known lot sizes."""
        with self._lock:
            return dict(self._lot_sizes)

    def is_trading_holiday(self, check_date: date) -> bool:
        """True if the given date is an NSE trading holiday or weekend."""
        if check_date.weekday() >= 5:   # Saturday=5, Sunday=6
            return True
        with self._lock:
            return check_date in self._holidays

    def is_market_open_today(self) -> bool:
        """True if today is a valid NSE trading day."""
        return not self.is_trading_holiday(date.today())

    def is_expiry_holiday(self, expiry: date) -> bool:
        """True if this Thursday expiry falls on a holiday (needs to be rolled back)."""
        return self.is_trading_holiday(expiry)

    def get_holidays_this_year(self) -> List[date]:
        """Return all NSE trading holidays for the current calendar year."""
        year = date.today().year
        with self._lock:
            return sorted(h for h in self._holidays if h.year == year)

    def get_holidays_range(self, start: date, end: date) -> List[date]:
        """Return NSE holidays between start and end dates (inclusive)."""
        with self._lock:
            return sorted(h for h in self._holidays if start <= h <= end)

    def next_trading_day(self, from_date: Optional[date] = None) -> date:
        """Return the next NSE trading day from the given date (or today)."""
        d = (from_date or date.today()) + timedelta(days=1)
        for _ in range(10):
            if not self.is_trading_holiday(d):
                return d
            d += timedelta(days=1)
        return d

    def previous_trading_day(self, from_date: Optional[date] = None) -> date:
        """Return the previous NSE trading day from the given date (or today)."""
        d = (from_date or date.today()) - timedelta(days=1)
        for _ in range(10):
            if not self.is_trading_holiday(d):
                return d
            d -= timedelta(days=1)
        return d

    def count_trading_days(self, start: date, end: date) -> int:
        """Count trading days between two dates (inclusive)."""
        count = 0
        d     = start
        while d <= end:
            if not self.is_trading_holiday(d):
                count += 1
            d += timedelta(days=1)
        return count

    def get_status(self) -> Dict[str, Any]:
        """Return data freshness status."""
        with self._lock:
            return {
                "lot_sizes_count":      len(self._lot_sizes),
                "holidays_count":       len(self._holidays),
                "lot_sizes_source":     self._status.get("lot_sizes_source", "fallback"),
                "holidays_source":      self._status.get("holidays_source", "fallback"),
                "last_lot_refresh":     self._status.get("last_lot_refresh", "never"),
                "last_holiday_refresh": self._status.get("last_holiday_refresh", "never"),
                "lot_sizes_stale":      self._is_lot_stale(),
                "holidays_stale":       self._is_holiday_stale(),
                "nifty_lot":            self._lot_sizes.get("NIFTY", "?"),
                "banknifty_lot":        self._lot_sizes.get("BANKNIFTY", "?"),
                "finnifty_lot":         self._lot_sizes.get("FINNIFTY", "?"),
            }

    # ─────────────────────────────────────────────────────────────────────────
    # REFRESH METHODS
    # ─────────────────────────────────────────────────────────────────────────

    def refresh_lot_sizes(self, angel_obj=None) -> bool:
        """
        Refresh lot sizes from Angel One or NSE.
        Returns True if successful.

        Priority:
        1. Angel One SmartAPI ScripMaster (if angel_obj provided)
        2. Angel One public JSON file (no auth needed)
        3. NSE FO underlying API
        4. Keep existing (do not update)
        """
        logger.info("Refreshing lot sizes from live data...")

        # Method 0: local master-contract file (authoritative, on disk, no network)
        result = self._fetch_lots_local_master()
        if result:
            self._update_lot_sizes(result, "local_master")
            return True

        # Method 1: Angel One SmartAPI
        if angel_obj:
            result = self._fetch_lots_angel_smartapi(angel_obj)
            if result:
                self._update_lot_sizes(result, "angel_smartapi")
                return True

        # Method 2: Angel One public ScripMaster file
        result = self._fetch_lots_angel_scrip_master()
        if result:
            self._update_lot_sizes(result, "angel_scrip_master")
            return True

        # Method 3: NSE FO API
        result = self._fetch_lots_nse_fo()
        if result:
            self._update_lot_sizes(result, "nse_fo_api")
            return True

        logger.warning("All lot size refresh methods failed — keeping existing data (using hardcoded fallback: NIFTY=75 BNF=30)")
        # Mark attempt so background thread does not retry for 24 hours
        self._status["last_lot_refresh"] = datetime.now().isoformat()
        self._status["lot_sizes_source"] = "hardcoded_fallback"
        self._save_cache()
        return False

    def refresh_holidays(self) -> bool:
        """
        Refresh trading holidays from NSE.
        Returns True if successful.

        Priority:
        1. NSE Holiday Master API
        2. pandas_market_calendars
        3. Keep existing
        """
        logger.info("Refreshing trading holidays from live data...")

        # Method 1: NSE API
        result = self._fetch_holidays_nse()
        if result:
            self._update_holidays(result, "nse_holiday_api")
            return True

        # Method 2: pandas_market_calendars
        result = self._fetch_holidays_pandas_calendars()
        if result:
            self._update_holidays(result, "pandas_calendars")
            return True

        logger.warning("All holiday refresh methods failed — keeping existing data")
        return False

    # ─────────────────────────────────────────────────────────────────────────
    # FETCHING: LOT SIZES
    # ─────────────────────────────────────────────────────────────────────────

    def _fetch_lots_angel_smartapi(self, angel_obj) -> Optional[Dict[str, int]]:
        """
        Fetch lot sizes from Angel One SmartAPI.
        The SDK's searchScrip or getContractInfo returns lot sizes
        for specific symbols. We call it for each major index.
        """
        try:
            result = {}
            targets = [
                ("NIFTY", "NFO", "NIFTY"),
                ("BANKNIFTY", "NFO", "BANKNIFTY"),
                ("FINNIFTY", "NFO", "FINNIFTY"),
                ("MIDCPNIFTY", "NFO", "MIDCPNIFTY"),
            ]
            for name, exch, query in targets:
                try:
                    resp = angel_obj.searchScrip(exch, query)
                    if resp and isinstance(resp, dict):
                        data = resp.get("data", [])
                        if isinstance(data, list):
                            for item in data:
                                if (isinstance(item, dict)
                                        and item.get("tradingsymbol", "").upper().startswith(name)
                                        and item.get("instrumenttype", "") in ("OPTIDX", "OPTSTK")):
                                    lot = int(item.get("lotsize", 0) or 0)
                                    if lot > 0:
                                        result[name] = lot
                                        logger.debug("SmartAPI lot: %s = %d", name, lot)
                                        break
                except Exception as e:
                    logger.debug("SmartAPI searchScrip %s: %s", name, e)

            if len(result) >= 2:
                logger.info("Angel SmartAPI lot sizes: %s", result)
                return result
        except Exception as e:
            logger.debug("_fetch_lots_angel_smartapi: %s", e)
        return None

    def _fetch_lots_local_master(self) -> Optional[Dict[str, int]]:
        """
        Read lot sizes from the master-contract file already on disk.

        The bot downloads OpenAPIScripMaster.{csv,json} (and MasterContract_*.csv)
        daily for symbol resolution — it is the authoritative record of what the
        broker accepts, needs no network, and is immune to the NSE IP block. This
        is the most reliable lot-size source, so it runs first.
        """
        import csv as _csv
        targets = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"}
        for fname in ("OpenAPIScripMaster.csv", "MasterContract_ALL.csv",
                      "MasterContract_NFO.csv"):
            path = self._cache_dir / fname
            if not path.exists():
                continue
            try:
                result: Dict[str, int] = {}
                with open(path, errors="replace") as fh:
                    reader = _csv.DictReader(fh)
                    for row in reader:
                        itype = str(row.get("instrumenttype", "")).upper()
                        if itype not in ("OPTIDX", "FUTIDX"):
                            continue
                        name = str(row.get("name", "")).upper().strip()
                        if name not in targets or name in result:
                            continue
                        try:
                            lot = int(float(row.get("lotsize", 0) or 0))
                        except (TypeError, ValueError):
                            lot = 0
                        if lot > 0:
                            result[name] = lot
                        if len(result) >= len(targets):
                            break
                if result:
                    logger.info("Local master-contract lot sizes (%s): %s",
                                fname, result)
                    return result
            except Exception as e:
                logger.debug("_fetch_lots_local_master(%s): %s", fname, e)
        return None

    def _fetch_lots_angel_scrip_master(self) -> Optional[Dict[str, int]]:
        """
        Download Angel One's public ScripMaster JSON file.
        Large file (~20MB) but contains ALL F&O instruments with lot sizes.
        """
        import requests
        url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
        try:
            logger.info("Downloading Angel One ScripMaster (may take 10-20s)...")
            resp = requests.get(url, timeout=8, stream=True)
            if resp.status_code != 200:
                return None

            # Parse streaming to avoid loading entire 20MB into memory
            result = {}
            targets = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"}

            # Read in chunks and parse
            content = b""
            for chunk in resp.iter_content(chunk_size=65536):
                content += chunk
                if len(content) > 30 * 1024 * 1024:   # 30MB safety limit
                    break

            instruments = json.loads(content)
            if not isinstance(instruments, list):
                return None

            for item in instruments:
                if not isinstance(item, dict):
                    continue
                if item.get("exch_seg") != "NFO":
                    continue
                if item.get("instrumenttype") not in ("OPTIDX", "FUTIDX"):
                    continue
                name = str(item.get("name", "")).upper()
                for tgt in targets:
                    if name == tgt or name.startswith(tgt):
                        lot = int(item.get("lotsize", 0) or 0)
                        if lot > 0 and tgt not in result:
                            result[tgt] = lot
                            logger.debug("ScripMaster: %s = %d lots", tgt, lot)
                        break

                if len(result) >= len(targets):
                    break

            if result:
                logger.info("Angel ScripMaster lot sizes: %s", result)
                return result

        except Exception as e:
            logger.debug("_fetch_lots_angel_scrip_master: %s", e)
        return None

    def _fetch_lots_nse_fo(self) -> Optional[Dict[str, int]]:
        """
        Fetch lot sizes from NSE FO Underlying API.
        Returns underlying details including lot sizes.
        """
        import requests
        try:
            session = requests.Session()
            session.headers.update({
                "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept":          "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer":         "https://www.nseindia.com/",
                "X-Requested-With": "XMLHttpRequest",
            })
            # Need session cookie first
            session.get("https://www.nseindia.com/", timeout=10)
            time.sleep(0.5)

            resp = session.get(
                "https://www.nseindia.com/api/underlying-information",
                timeout=15,
            )
            if resp.status_code != 200:
                return None

            data = resp.json()
            result = {}

            # The API returns data in different possible formats
            items = data if isinstance(data, list) else data.get("data", [])
            for item in (items if isinstance(items, list) else []):
                sym = str(item.get("symbol", item.get("underlying", ""))).upper()
                lot = int(item.get("lotSize", item.get("lot_size", 0)) or 0)
                if lot > 0 and sym:
                    result[sym] = lot

            if result:
                logger.info("NSE FO lot sizes: %s", {k:v for k,v in result.items() if k in DEFAULT_LOT_SIZES})
                return result

        except Exception as e:
            logger.debug("_fetch_lots_nse_fo: %s", e)
        return None

    # ─────────────────────────────────────────────────────────────────────────
    # FETCHING: HOLIDAYS
    # ─────────────────────────────────────────────────────────────────────────

    def _fetch_holidays_nse(self) -> Optional[List[date]]:
        """
        Fetch trading holidays from NSE's official API.
        Returns list of holiday dates.
        """
        import requests
        try:
            session = requests.Session()
            session.headers.update({
                "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept":          "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer":         "https://www.nseindia.com/",
                "Connection":      "keep-alive",
            })
            # Must get homepage first to get session cookie
            logger.info("Fetching NSE holidays (initialising session)...")
            r0 = session.get("https://www.nseindia.com/", timeout=15)
            if r0.status_code != 200:
                logger.warning("NSE homepage returned %d", r0.status_code)
                return None
            time.sleep(1.0)   # NSE rate-limits aggressive bots

            resp = session.get(
                "https://www.nseindia.com/api/holiday-master?type=trading",
                timeout=20,
            )
            if resp.status_code != 200:
                logger.warning("NSE holiday API returned %d", resp.status_code)
                return None

            data = resp.json()
            holidays: List[date] = []

            # NSE API returns {"FO": [...], "CM": [...]} or similar
            # Each entry has "tradingDate": "26-Jan-2025"
            all_entries = []
            if isinstance(data, dict):
                for key in ("FO", "CM", "EQ", "CD"):
                    if key in data:
                        all_entries.extend(data[key])
            elif isinstance(data, list):
                all_entries = data

            seen = set()
            for entry in all_entries:
                if not isinstance(entry, dict):
                    continue
                raw_date = entry.get("tradingDate", entry.get("trade_date", ""))
                if not raw_date:
                    continue
                # Parse multiple formats: "26-Jan-2025", "2025-01-26", "Jan 26, 2025"
                parsed = _parse_nse_date(raw_date)
                if parsed and parsed not in seen:
                    holidays.append(parsed)
                    seen.add(parsed)

            if len(holidays) >= 5:
                logger.info(
                    "NSE holidays fetched: %d dates "
                    "(first: %s, last: %s)",
                    len(holidays),
                    min(holidays).isoformat() if holidays else "?",
                    max(holidays).isoformat() if holidays else "?",
                )
                return holidays

        except Exception as e:
            logger.warning("_fetch_holidays_nse: %s", e)
        return None

    def _fetch_holidays_pandas_calendars(self) -> Optional[List[date]]:
        """Fetch holidays using pandas_market_calendars (NSE calendar)."""
        try:
            import pandas_market_calendars as mcal
            cal   = mcal.get_calendar("NSE")
            today = date.today()
            # Get 2 years of schedules
            sched = cal.schedule(
                start_date=today.strftime("%Y-01-01"),
                end_date=f"{today.year + 1}-12-31",
            )
            all_days   = mcal.date_range(sched, frequency="1D")
            all_dates  = set(d.date() for d in all_days)
            # Invert: find dates that are NOT in the trading schedule
            start = date(today.year, 1, 1)
            end   = date(today.year + 1, 12, 31)
            holidays: List[date] = []
            d = start
            while d <= end:
                if d.weekday() < 5 and d not in all_dates:
                    holidays.append(d)
                d += timedelta(days=1)
            if holidays:
                logger.info("pandas_market_calendars: %d NSE holidays found", len(holidays))
                return holidays
        except ImportError:
            logger.debug("pandas_market_calendars not installed")
        except Exception as e:
            logger.debug("_fetch_holidays_pandas_calendars: %s", e)
        return None

    # ─────────────────────────────────────────────────────────────────────────
    # CACHE: LOAD / SAVE
    # ─────────────────────────────────────────────────────────────────────────

    def _load_from_cache(self) -> None:
        """Load lot sizes and holidays from local cache files."""
        # Lot sizes
        p = self._cache_dir / LOT_SIZES_CACHE
        if p.exists():
            try:
                data = json.loads(p.read_text())
                lots = data.get("lot_sizes", {})
                if lots:
                    self._lot_sizes = {k.upper(): int(v) for k, v in lots.items()}
                    logger.debug("Lot sizes loaded from cache: %s", self._lot_sizes)
            except Exception as e:
                logger.debug("Lot size cache load failed: %s", e)

        # Holidays
        p = self._cache_dir / HOLIDAYS_CACHE
        if p.exists():
            try:
                data = json.loads(p.read_text())
                hols = data.get("holidays", [])
                if hols:
                    self._holidays = set()
                    for h in hols:
                        try:
                            self._holidays.add(date.fromisoformat(str(h)))
                        except Exception:
                            pass
                    logger.debug("Holidays loaded from cache: %d dates", len(self._holidays))
            except Exception as e:
                logger.debug("Holiday cache load failed: %s", e)

        # Status
        p = self._cache_dir / STATUS_FILE
        if p.exists():
            try:
                self._status = json.loads(p.read_text())
            except Exception:
                self._status = {}

    def _save_cache(self) -> None:
        """Save current data to local cache files."""
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)

            # Lot sizes
            lot_data = {
                "lot_sizes":   {k: v for k, v in self._lot_sizes.items()},
                "updated_at":  datetime.now().isoformat(),
                "source":      self._status.get("lot_sizes_source", "unknown"),
            }
            (self._cache_dir / LOT_SIZES_CACHE).write_text(
                json.dumps(lot_data, indent=2)
            )

            # Holidays
            hol_data = {
                "holidays":   sorted(h.isoformat() for h in self._holidays),
                "count":      len(self._holidays),
                "updated_at": datetime.now().isoformat(),
                "source":     self._status.get("holidays_source", "unknown"),
            }
            (self._cache_dir / HOLIDAYS_CACHE).write_text(
                json.dumps(hol_data, indent=2)
            )

            # Status
            (self._cache_dir / STATUS_FILE).write_text(
                json.dumps(self._status, indent=2)
            )
        except Exception as e:
            logger.warning("Cache save failed: %s", e)

    def _update_lot_sizes(self, new_lots: Dict[str, int], source: str) -> None:
        with self._lock:
            self._lot_sizes.update({k.upper(): int(v) for k, v in new_lots.items()})
            self._status["lot_sizes_source"]  = source
            self._status["last_lot_refresh"]  = datetime.now().isoformat()
            self._save_cache()
            logger.info("Lot sizes updated from %s: %s", source,
                        {k: v for k, v in self._lot_sizes.items() if k in DEFAULT_LOT_SIZES})

    def _update_holidays(self, new_holidays: List[date], source: str) -> None:
        with self._lock:
            self._holidays = set(new_holidays)
            self._status["holidays_source"]       = source
            self._status["last_holiday_refresh"]  = datetime.now().isoformat()
            self._save_cache()
            logger.info(
                "Holidays updated from %s: %d dates", source, len(self._holidays)
            )

    def _load_default_holidays(self) -> None:
        """Load hardcoded holidays as fallback."""
        holidays: Set[date] = set()
        for ds in DEFAULT_HOLIDAYS_2025 + DEFAULT_HOLIDAYS_2026:
            try:
                holidays.add(date.fromisoformat(ds))
            except Exception:
                pass
        self._holidays = holidays
        self._status["holidays_source"] = "hardcoded_fallback"
        logger.info("Loaded %d hardcoded fallback holidays", len(holidays))

    def _is_lot_stale(self) -> bool:
        last = self._status.get("last_lot_refresh")
        if not last:
            return True
        try:
            age = (datetime.now() - datetime.fromisoformat(last)).days
            return age >= LOT_SIZE_REFRESH_DAYS
        except Exception:
            return True

    def _is_holiday_stale(self) -> bool:
        last = self._status.get("last_holiday_refresh")
        if not last:
            return True
        try:
            age = (datetime.now() - datetime.fromisoformat(last)).days
            return age >= HOLIDAY_REFRESH_DAYS
        except Exception:
            return True

    def _background_refresh(self) -> None:
        """Background thread: refresh stale data without blocking startup."""
        try:
            time.sleep(10)   # Wait for system to fully start up
            # Only retry lot sizes if no attempt was made in the last hour
            last = self._status.get("last_lot_refresh", "")
            recently_attempted = False
            if last:
                try:
                    age_min = (datetime.now() - datetime.fromisoformat(last)).total_seconds() / 60
                    recently_attempted = age_min < 60
                except Exception:
                    pass
            if self._is_lot_stale() and not recently_attempted:
                logger.info("Lot sizes cache is stale — refreshing in background")
                self.refresh_lot_sizes()
            elif recently_attempted:
                logger.debug("Lot size refresh attempted recently — skipping background retry")
            if self._is_holiday_stale():
                logger.info("Holiday cache is stale — refreshing in background")
                self.refresh_holidays()
        except Exception as e:
            logger.debug("Background refresh error: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _parse_nse_date(raw: str) -> Optional[date]:
    """
    Parse NSE date strings in various formats to date.
    NSE uses: "26-Jan-2025", "26-Jan-25", "2025-01-26", "Jan 26, 2025"
    """
    formats = [
        "%d-%b-%Y",   # 26-Jan-2025
        "%d-%b-%y",   # 26-Jan-25
        "%Y-%m-%d",   # 2025-01-26
        "%b %d, %Y",  # Jan 26, 2025
        "%d/%m/%Y",   # 26/01/2025
        "%d %b %Y",   # 26 Jan 2025
    ]
    raw = str(raw).strip()
    for fmt in formats:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# MODULE SINGLETON
# ─────────────────────────────────────────────────────────────────────────────
_master: Optional[NSEMaster] = None
_master_lock = threading.Lock()


def get_nse_master(cache_dir: str = ".") -> NSEMaster:
    """Return the module-level NSEMaster singleton."""
    global _master
    if _master is None:
        with _master_lock:
            if _master is None:
                _master = NSEMaster(cache_dir=cache_dir, auto_refresh=True)
    return _master
