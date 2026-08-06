"""
angel.py

Angel One SmartAPI broker wrapper.

Features
--------
- Thread-safe singleton per (api_key, client_id)
- Exponential backoff reconnect
- Safe session validation
- Paper mode with deterministic fake prices/orders
- Historical OHLCV fetch
- Market depth simulation in paper mode
- Token caching + master contract lookup
- Hardcoded NSE index token fallback
- get_balance() support
- Safe LTP handling for SmartAPI SDK failures
"""

from __future__ import annotations

# Load .env early so os.getenv() calls work
try:
    from dotenv import load_dotenv
    load_dotenv('.env')
except ImportError:
    pass

import threading
import time
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pandas as pd
import pyotp
from SmartApi import SmartConnect

import logging
from master_contract import MasterContract
from utils import retry

logger = logging.getLogger("angel")


# ── Secret redaction for broker logs ──────────────────────────────────────────
# The smartapi library logs the FULL login request (clientcode/password/totp/
# X-PrivateKey) at ERROR level via logzero whenever a login fails — leaking
# credentials into the systemd journal. This filter scrubs those values from any
# log record before its handlers emit it. It does NOT touch credentials or .env;
# it only redacts what would otherwise be written to logs.
from logging_security import install_secret_redaction

install_secret_redaction()

# NSE/BSE F&O prices must be in multiples of the ₹0.05 tick. round(p, 2) leaves
# values like 28.67 that the exchange rejects ("price in multiples of 5 paise").
def _round_to_tick(price: float, tick: float = 0.05) -> float:
    try:
        p = float(price)
        if p <= 0:
            return 0.0
        return round(round(p / tick) * tick, 2)
    except Exception:
        return float(price or 0.0)

PAPER_SPOT_LTP = 22000.0
PAPER_OPTION_LTP = 120.0
PAPER_SPREAD_PCT = 0.02
MAX_CONNECT_RETRIES = 3
CONNECT_BASE_DELAY = 2
RECONNECT_MIN_INTERVAL = 60   # min seconds between reconnect attempts (anti-storm)
RATELIMIT_COOLDOWN     = int(os.getenv("ANGEL_RATELIMIT_COOLDOWN_SEC", "90"))
API_MIN_INTERVAL_SEC   = float(os.getenv("ANGEL_API_MIN_INTERVAL_SEC", "0.4"))
# 2026-07-28 audit finding: request_governor was only wired to getCandleData;
# every order/GTT/search endpoint could still burst uncoordinated. These are
# low-frequency, event-driven calls (order placement, not a per-symbol scan
# loop), so a shared pacer here is safe and closes that gap. Deliberately NOT
# applied to ltpData/getProfile -- those ARE per-symbol hot paths where a
# blocking pacer would meaningfully slow the live scan loop.
ORDER_API_MIN_INTERVAL_SEC = float(os.getenv("ANGEL_ORDER_API_MIN_INTERVAL_SEC", "0.2"))
TOKEN_MISS_TTL         = 1800 # negative-cache unresolved tokens this long (anti-storm)

# 2026-07-08: smartapi-python 1.5.5 hardcodes GTT create/modify/cancel under a
# "/gtt-service" URL prefix that Angel's gateway no longer routes ("no Route
# matched with those values" on every call — verified against the live API,
# unauthenticated, both forms). The unprefixed path (same one the SDK's own
# gtt.details/gtt.list routes already use) is live. Every GTT stop-loss/target
# placed since this SDK version shipped silently failed — positions ran
# unprotected. Patch the INSTANCE's route dict (never the class dict shared
# across instances) right after each SmartConnect() construction.
_DEAD_GTT_ROUTE_PREFIX = "/gtt-service"


def _patch_dead_gtt_routes(obj) -> None:
    try:
        routes = dict(obj._routes)
        for key in ("api.gtt.create", "api.gtt.modify", "api.gtt.cancel"):
            uri = routes.get(key, "")
            if uri.startswith(_DEAD_GTT_ROUTE_PREFIX):
                routes[key] = uri[len(_DEAD_GTT_ROUTE_PREFIX):]
        obj._routes = routes
    except Exception as e:
        logger.warning("GTT route patch failed (SDK shape changed?): %s", e)


def _is_rate_limited(err) -> bool:
    """True if an Angel error is the account-wide 'exceeding access rate' throttle."""
    s = str(err).lower()
    return any(marker in s for marker in (
        "exceeding access rate",
        "access denied because of exceeding",
        "too many requests",
        "ab1021",
        "status code 429",
    ))


def _sanitize_order_tag(tag: str) -> str:
    """Angel SmartAPI ordertag must be alphanumeric and <= 20 characters.

    Used for the SEBI Apr-2026 algo-order audit trail: every automated order
    carries a tag identifying it as algo-originated and traceable to a strategy.
    Returns "" for empty/invalid input so the field is simply omitted.
    """
    if not tag:
        return ""
    clean = "".join(ch for ch in str(tag) if ch.isalnum())
    return clean[:20]

INDEX_TOKEN_MAP = {
    ("NSE", "Nifty 50"): "99926000",
    ("NSE", "NIFTY"): "99926000",
    ("NSE", "NIFTY 50"): "99926000",
    ("NSE", "Nifty Bank"): "99926009",
    ("NSE", "BANKNIFTY"): "99926009",
    ("NSE", "Nifty Fin Service"): "99926037",
    ("NSE", "FINNIFTY"): "99926037",
    ("NSE", "MIDCPNIFTY"): "99926074",
    ("NSE", "Nifty Midcap Select"): "99926074",
    ("NSE", "NIFTY MID SELECT"): "99926074",
    ("NSE", "SENSEX"): "99919000",
    ("NSE", "India VIX"): "99926017",
    ("NSE", "INDIA VIX"): "99926017",
    ("NSE", "INDIAVIX"): "99926017",
    # BSE indices — spot quotes live on the BSE exchange, not NSE.
    ("BSE", "SENSEX"): "99919000",
    ("BSE", "BANKEX"): "99919012",
}



# ── NSE EQ Token Cache (cash market stocks) ────────────────────────────────
_NSE_EQ_TOKENS: dict = {}  # {symbol: token}
_NSE_EQ_LOADED: bool = False


def _load_nse_eq_tokens() -> dict:
    """
    Load NSE cash market (EQ) stock tokens.
    Priority: MasterContract_ALL.csv → download fresh.
    RELIANCE-EQ → token 2885, etc. (190,304 total instruments).
    """
    global _NSE_EQ_TOKENS, _NSE_EQ_LOADED
    if _NSE_EQ_LOADED:
        return _NSE_EQ_TOKENS

    log = logging.getLogger(__name__)
    try:
        import pandas as _pd, requests as _rq

        # Try MasterContract_ALL.csv first (downloaded by test_angel_direct.py)
        for mc_file in ["MasterContract_ALL.csv", "MasterContract_NFO.csv"]:
            mc_path = Path(mc_file)
            if not mc_path.exists():
                continue
            df = _pd.read_csv(str(mc_path), low_memory=False)
            if "exch_seg" not in df.columns:
                continue
            nse_eq = df[df["exch_seg"].str.upper() == "NSE"]
            if len(nse_eq) < 100:
                continue  # too few — probably NFO-only file
            for _, row in nse_eq.iterrows():
                raw_sym = str(row.get("symbol","")).strip().upper()
                sym = raw_sym.replace("-EQ","")
                tok = str(row.get("token","")).strip()
                nm  = str(row.get("name","")).strip().upper()
                if sym and tok and tok != "nan":
                    # -EQ series always wins; other series (-BE/-D1/...) only
                    # fill gaps. Without this, MOTHERSON-D1 (no candle data)
                    # overwrote MOTHERSON-EQ via the shared company name.
                    is_eq = raw_sym.endswith("-EQ")
                    if is_eq or sym not in _NSE_EQ_TOKENS:
                        _NSE_EQ_TOKENS[sym] = tok
                    if nm and nm != "NAN" and (is_eq or nm not in _NSE_EQ_TOKENS):
                        _NSE_EQ_TOKENS[nm] = tok
            if _NSE_EQ_TOKENS:
                # Common colloquial aliases → official NSE symbols
                for _alias, _official in (("HPCL", "HINDPETRO"),):
                    if _alias not in _NSE_EQ_TOKENS and _official in _NSE_EQ_TOKENS:
                        _NSE_EQ_TOKENS[_alias] = _NSE_EQ_TOKENS[_official]
                log.info("NSE tokens loaded: %d from %s", len(_NSE_EQ_TOKENS), mc_file)
                _NSE_EQ_LOADED = True
                return _NSE_EQ_TOKENS

        # Download if not found locally
        log.info("Downloading full master contract (190k instruments)...")
        r = _rq.get(
            "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json",
            timeout=30)
        if r.status_code == 200:
            df = _pd.DataFrame(r.json())
            df.to_csv("MasterContract_ALL.csv", index=False)
            nse_eq = df[df.get("exch_seg","") == "NSE"] if "exch_seg" in df.columns else _pd.DataFrame()
            for _, row in nse_eq.iterrows():
                sym = str(row.get("symbol","")).replace("-EQ","").strip().upper()
                tok = str(row.get("token","")).strip()
                if sym and tok and tok != "nan":
                    _NSE_EQ_TOKENS[sym] = tok
            log.info("NSE tokens downloaded: %d stocks", len(_NSE_EQ_TOKENS))
    except Exception as e:
        logging.getLogger(__name__).warning("NSE EQ token load: %s", e)

    _NSE_EQ_LOADED = True
    return _NSE_EQ_TOKENS


# ── F&O Token Cache (NFO + BFO options/futures) ────────────────────────────────
_FO_TOKENS: dict = {}
_FO_LOTSIZES: dict = {}
_FO_LOADED: bool = False


def _load_fo_tokens() -> dict:
    """
    Load F&O option/future tradingsymbol → token for NFO and BFO from the master
    contract(s) on disk. Authoritative and local; also cuts searchScrip calls
    (rate-limit relief). Loaded lazily once.

    2026-07-08 incident: this used to stop at the FIRST file that yielded any
    tokens at all. OpenAPIScripMaster.csv (stale, last refreshed 2026-06-04)
    has 79k+ F&O rows so it always "succeeded" and the loop broke before ever
    reading MasterContract_ALL.csv (refreshed 2026-07-06, 2 days old) — which
    is the only one of the two that actually contained that week's NIFTY
    21JUL2026 contract. Every GTT SL/target placement for that contract failed
    with "token not found", leaving a live position unprotected. Now MERGES
    every present file (first file to define a symbol wins — tokens are
    assigned once per contract and don't change, so this is safe) instead of
    stopping at the first non-empty one, so a stale snapshot in any one file
    can no longer shadow a fresher contract listed in another.
    """
    global _FO_TOKENS, _FO_LOTSIZES, _FO_LOADED
    if _FO_LOADED:
        return _FO_TOKENS
    log = logging.getLogger(__name__)
    import csv as _csv, os as _os
    files_used = []
    for mc in ("OpenAPIScripMaster.csv", "MasterContract_ALL.csv",
               "MasterContract_NFO.csv"):
        if not _os.path.exists(mc):
            continue
        try:
            added = 0
            with open(mc, errors="replace") as fh:
                for row in _csv.DictReader(fh):
                    if str(row.get("exch_seg", "")).upper() not in ("NFO", "BFO"):
                        continue
                    sym = str(row.get("symbol", "")).strip().upper()
                    tok = str(row.get("token", "")).strip()
                    if sym and tok and tok.lower() != "nan" and sym not in _FO_TOKENS:
                        _FO_TOKENS[sym] = tok
                        added += 1
                    if sym and sym not in _FO_LOTSIZES:
                        try:
                            lot = int(float(row.get("lotsize", 0) or 0))
                            if lot > 0:
                                _FO_LOTSIZES[sym] = lot
                        except (TypeError, ValueError) as e:
                            log.debug("bad lotsize for %s in %s: %s", sym, mc, e)
            if added:
                files_used.append(f"{mc}(+{added})")
        except Exception as e:
            log.debug("_load_fo_tokens(%s): %s", mc, e)
    if files_used:
        log.info("F&O tokens loaded: %d total from %s", len(_FO_TOKENS), ", ".join(files_used))
    _FO_LOADED = True
    return _FO_TOKENS


def get_fo_lot_size(symbol: str) -> Optional[int]:
    """Exchange lot size for an F&O tradingsymbol, from the same master-contract
    data _load_fo_tokens() already parses. None if the symbol isn't found —
    callers must not guess a lot size, since ordering a non-lot-multiple
    quantity gets rejected (or worse, silently mis-executed) at the broker."""
    _load_fo_tokens()
    return _FO_LOTSIZES.get(symbol.upper())


class AngelOne:
    _instance_lock: threading.Lock = threading.Lock()
    _instances: Dict[Tuple[str, str], "AngelOne"] = {}

    def __new__(cls, api_key: str, client_id: str, *args, **kwargs):
        key = (api_key, client_id)
        with cls._instance_lock:
            if key not in cls._instances:
                inst = super().__new__(cls)
                cls._instances[key] = inst
            return cls._instances[key]

    def __init__(
        self,
        api_key: str,
        client_id: str,
        password: str,
        totp_secret: str,
        paper_trade: bool = False,  # Default false — data always fetched
    ):
        if getattr(self, "_initialised", False):
            return

        self._initialised = True
        self.api_key = api_key
        self.client_id = client_id
        self.password = password
        self.totp_secret = totp_secret
        self.paper_trade = paper_trade
        self.block_real_orders = False  # set by auto_mode for PAPER mode

        self.obj: Optional[SmartConnect] = None
        self.auth_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.feed_token: Optional[str] = None

        self.master = MasterContract("MasterContract_NFO.csv")
        self._token_cache: Dict[str, str] = {}
        # Negative cache: cache_key -> ts of last failed resolution. Stops stale/
        # renamed symbols (e.g. a delisted ticker in the scan universe) from
        # re-hitting searchScrip every cycle and triggering an account-wide
        # Angel rate-limit storm that starves candle fetches for valid symbols.
        self._token_miss: Dict[str, float] = {}
        self._lock = threading.Lock()
        self._paper_order_counter = 0
        self._last_connect_ts: float = 0.0      # anti-storm reconnect throttle
        self._rate_limited_until: float = 0.0   # circuit-breaker after a throttle
        self._api_rate_lock = threading.Lock()
        self._last_api_call_ts: float = 0.0
        self._balance_cache_value: float = 0.0
        self._balance_cache_ts: float = 0.0
        self._balance_cache_ttl: float = float(os.getenv("ANGEL_BALANCE_CACHE_TTL_SEC", "20"))
        self._balance_stale_ok_sec: float = float(os.getenv("ANGEL_BALANCE_STALE_OK_SEC", "900"))
        self._balance_rate_limited_until: float = 0.0
        self._balance_last_rate_log_ts: float = 0.0

        # ALWAYS connect for DATA — paper mode only blocks order placement
        # This was the root cause of Scanned:0 for 2 months:
        # paper_trade=True → never connects → obj=None → all data returns None
        self.connect()

    def refresh_session(self) -> bool:
        """Re-login if Angel One session expired (token valid ~8 hours)."""
        try:
            import time as _t
            if hasattr(self, '_last_login') and (_t.time() - self._last_login) < 25200:
                return True  # less than 7 hours — still valid
            logger.info("Refreshing Angel One session...")
            result = self.connect()
            if result:
                self._last_login = _t.time()
                logger.info("Angel One session refreshed")
            return result
        except Exception as e:
            logger.warning("Session refresh failed: %s", e)
            return False


    def download_master_contract(self) -> bool:
        """Download MasterContract_NFO.csv from Angel One API if missing."""
        from pathlib import Path
        mc_path = Path("MasterContract_NFO.csv")
        if mc_path.exists() and mc_path.stat().st_size > 10000:
            return True  # already exists and non-trivial
        try:
            import requests, os
            # Angel One provides instrument list as JSON
            headers = {"Authorization": f"Bearer {getattr(self, '_jwt_token', '')}",
                       "X-ClientCode": getattr(self, 'client_id', ''),
                       "X-SourceID": "WEB", "X-UserType": "USER",
                       "Accept": "application/json",
                       "Content-Type": "application/json"}
            r = requests.get(
                "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json",
                timeout=30)
            if r.status_code == 200:
                import json, pandas as pd
                data = r.json()
                df = pd.DataFrame(data)
                # Filter NFO instruments
                nfo = df[df['exch_seg'].str.upper() == 'NFO'] if 'exch_seg' in df.columns else df
                nfo.to_csv("MasterContract_NFO.csv", index=False)
                logger.info("MasterContract downloaded: %d NFO instruments", len(nfo))
                return True
        except Exception as e:
            logger.debug("MasterContract download: %s", e)
        return False

    def connect(self) -> bool:
        # Anti-storm guard: if we already have a session and either reconnected
        # very recently or are inside a rate-limit cooldown, keep the existing
        # session instead of hammering generateSession (this was the 11k/day
        # reconnect storm). If obj is None (truly disconnected) we always try.
        now = time.time()
        if self.obj is not None and (
                now < self._rate_limited_until or
                (now - self._last_connect_ts) < RECONNECT_MIN_INTERVAL):
            return True
        self._last_connect_ts = now

        for attempt in range(1, MAX_CONNECT_RETRIES + 1):
            try:
                totp = pyotp.TOTP(self.totp_secret).now()
                obj = SmartConnect(api_key=self.api_key)
                _patch_dead_gtt_routes(obj)
                # RECONNECT_MIN_INTERVAL above is a per-instance (== per-process)
                # guard and only applies when self.obj is already set; a fresh or
                # lost connection always retries immediately with zero awareness
                # of other processes sharing this same Angel account
                # (main_autonomous.py, manual_trade_tracker.py, trade_guardian_bot.py,
                # option_chain_recorder.py all log in independently). Angel's login
                # rate limit is account-wide, not per-process -- found 2026-08-06 via
                # "Angel login throttled" appearing 58+ times across 3 services in
                # one day, which was blocking live option-chain fetches
                # (no_chain_data in option_decision_journal.jsonl). Same class of
                # bug as the historical-candle rate-limit collision fixed earlier
                # this session; same fix -- pace the actual login call cross-process.
                from request_governor import acquire as _acquire_login_slot
                _acquire_login_slot("angel_login", RECONNECT_MIN_INTERVAL)
                data = obj.generateSession(
                    clientCode=self.client_id,
                    password=self.password,
                    totp=totp,
                )

                if not data or "data" not in data:
                    raise RuntimeError(f"Invalid login response: {data}")

                with self._lock:
                    self.obj = obj
                    self.auth_token = data["data"].get("jwtToken")
                    self.refresh_token = data["data"].get("refreshToken")
                    self.feed_token = obj.getfeedToken()

                logger.info("✅ Connected to Angel One")
                return True

            except Exception as e:
                if _is_rate_limited(e):
                    # Account is throttled — retrying now only deepens the limit.
                    # Cool down and keep whatever session we already have.
                    self._rate_limited_until = time.time() + RATELIMIT_COOLDOWN
                    logger.warning(
                        "Angel login throttled (rate limit) — backing off %ss",
                        RATELIMIT_COOLDOWN)
                    return self.obj is not None
                delay = CONNECT_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning(
                    f"Connection attempt {attempt}/{MAX_CONNECT_RETRIES} failed: {e}. "
                    f"Retrying in {delay}s..."
                )
                if attempt < MAX_CONNECT_RETRIES:
                    time.sleep(delay)

        logger.error("❌ All connection attempts failed.")
        with self._lock:
            self.obj = None
        return False

    def _ensure_connected(self) -> bool:
        # Paper mode: STILL check connection (data fetch needs obj)
        # Only skip the session validation (getProfile) in paper mode
        if self.paper_trade:
            if self.obj is not None:
                return True
            # obj is None — need to connect even in paper mode
            logger.info("Paper mode but obj=None — connecting for data...")
            return self.connect()

        if self.obj is None:
            logger.warning("No active connection — attempting reconnect...")
            return self.connect()

        try:
            self.obj.getProfile(self.refresh_token)
            return True
        except Exception as e:
            if _is_rate_limited(e):
                # Throttled, NOT disconnected. Reconnecting here amplified the
                # rate limit into a storm (865 triggers/day). Keep the session;
                # the actual API call will proceed or fail on its own.
                logger.debug("Session check throttled — keeping existing session")
                return True
            logger.warning(f"Session check failed ({e}) — reconnecting...")
            return self.connect()


    def place_gtt_order(
        self,
        symbol:      str,
        qty:         int,
        trigger_price: float,
        limit_price:   float,
        transaction_type: str = "SELL",
        exchange:    str = "NFO",
    ) -> Optional[str]:
        """
        Place GTT (Good Till Triggered) Stop-Loss order at Angel One.

        GTT orders persist at the broker even if the bot crashes or restarts.
        Essential for overnight swing positions.

        Args:
            symbol:           trading symbol
            qty:              quantity
            trigger_price:    SL trigger level
            limit_price:      limit price (set 1% below trigger for options)
            transaction_type: "SELL" (to close a long) or "BUY" (to close a short)
            exchange:         "NFO" for options, "NSE" for equity

        Returns:
            GTT order ID or None
        """
        # Honour the runtime PAPER block (block_real_orders / PAPER_ORDERS_ONLY),
        # not just paper_trade — same guard as place_order. In PAPER runtime the
        # bot runs paper_trade=False (data flows) + block_real_orders=True, so a
        # protective order gated only on paper_trade would hit the real exchange.
        _paper_orders_only = getattr(__import__("config"), "PAPER_ORDERS_ONLY", False)
        if self.paper_trade or _paper_orders_only or getattr(self, "block_real_orders", False):
            logger.info("PAPER: GTT SL placed symbol=%s trigger=%.2f", symbol, trigger_price)
            return f"PAPER_GTT_{symbol}"

        if not self._ensure_connected():
            return None

        try:
            token = self._get_token_no_lock(symbol, exchange)
            if not token:
                logger.error("GTT: token not found for %s", symbol)
                return None

            gtt_params = {
                "tradingsymbol":   symbol,
                "symboltoken":     token,
                "exchange":        exchange,
                "transactiontype": transaction_type,
                "producttype":     "CARRYFORWARD",
                "qty":             str(qty),
                "price":           str(_round_to_tick(limit_price)),
                "triggerprice":    str(_round_to_tick(trigger_price)),
                "disclosedqty":    "0",
                "timeperiod":      "365",
            }
            with self._lock:
                from request_governor import acquire as _acquire_request_slot
                _acquire_request_slot("angel_order_ops", ORDER_API_MIN_INTERVAL_SEC)
                resp = self.obj.gttCreateRule(gtt_params)

            # SmartAPI versions differ: some return the rule id directly
            # (int/str), others a dict {"status":..,"data":{"id":..}}.
            gtt_id = None
            if isinstance(resp, dict):
                if resp.get("status"):
                    gtt_id = (resp.get("data") or {}).get("id")
            elif isinstance(resp, (int, str)) and str(resp).strip().isdigit():
                gtt_id = resp
            if gtt_id:
                logger.info(
                    "GTT placed: %s trigger=%.2f qty=%d id=%s",
                    symbol, trigger_price, qty, gtt_id
                )
                return str(gtt_id)
            logger.warning("GTT placement failed: %s", resp)
            return None
        except Exception as e:
            logger.error("GTT order error: %s", e)
            return None

    def cancel_gtt_order(self, gtt_id: str, symbol: str = "") -> bool:
        """Cancel an existing GTT order (when position is closed normally)."""
        if self.paper_trade or not gtt_id or "PAPER" in str(gtt_id):
            return True
        try:
            if not self._ensure_connected():
                return False
            with self._lock:
                from request_governor import acquire as _acquire_request_slot
                _acquire_request_slot("angel_order_ops", ORDER_API_MIN_INTERVAL_SEC)
                resp = self.obj.gttDeleteRule({"id": gtt_id, "symboltoken": "", "exchange": "NFO"})
            ok = bool(resp and resp.get("status"))
            if ok:
                logger.info("GTT cancelled: id=%s symbol=%s", gtt_id, symbol)
            return ok
        except Exception as e:
            logger.debug("GTT cancel error: %s", e)
            return False


    @retry(max_retries=3, base_delay=1)
    def place_order(
        self,
        symbol: str,
        qty: int,
        buy_sell: str,
        order_type: str = "MARKET",
        price: float = 0.0,
        variety: str = "NORMAL",
        producttype: str = "INTRADAY",
        exchange: Optional[str] = None,
        order_tag: str = "",
    ) -> Optional[Tuple[str, float]]:
        if qty <= 0:
            logger.error(f"place_order: invalid qty={qty}")
            return None

        if price < 0:
            logger.error(f"place_order: invalid price={price}")
            return None

        _paper_orders_only = getattr(__import__("config"), "PAPER_ORDERS_ONLY", False)
        # block_real_orders is set by auto_mode / _apply_order_block for PAPER runtime.
        # Honour it on the PRIMARY order path too (previously only the SL/GTT path in
        # websocket_tracker checked it) so real-order blocking has defense-in-depth and
        # both paths agree — strictly safety-additive (can only block more, never fewer).
        if self.paper_trade or _paper_orders_only or getattr(self, "block_real_orders", False):
            self._paper_order_counter += 1
            fake_id = f"PAPER_{self._paper_order_counter:06d}"

            if price > 0:
                fill_price = price
            elif "CE" in symbol or "PE" in symbol:
                fill_price = PAPER_OPTION_LTP
            else:
                fill_price = PAPER_SPOT_LTP

            _mode = "PAPER_ORDERS_ONLY" if _paper_orders_only and not self.paper_trade else "PAPER"
            logger.info(
                f"📤 {_mode} ORDER #{self._paper_order_counter}: "
                f"{buy_sell} {qty} {symbol} @ {'MARKET' if price == 0 else price}"
            )
            return fake_id, fill_price

        if not self._ensure_connected():
            logger.error("place_order: no connection available")
            return None

        if exchange is None:
            exchange = "NFO" if ("CE" in symbol or "PE" in symbol) else "NSE"

        token = self._get_token_no_lock(symbol, exchange)
        if not token:
            logger.error(f"place_order: token not found for {symbol}")
            return None

        order_params = {
            "variety": variety,
            "tradingsymbol": symbol,
            "symboltoken": token,
            "transactiontype": buy_sell.upper(),
            "exchange": exchange,
            "ordertype": order_type.upper(),
            "producttype": producttype.upper(),
            "duration": "DAY",
            "price": str(_round_to_tick(price)) if order_type.upper() == "LIMIT" else "0",
            "squareoff": "0",
            "stoploss": "0",
            "quantity": str(qty),
        }

        # SEBI Apr-2026 algo-order audit trail: tag automated orders
        _tag = _sanitize_order_tag(order_tag)
        if _tag:
            order_params["ordertag"] = _tag

        with self._lock:
            try:
                from request_governor import acquire as _acquire_request_slot
                _acquire_request_slot("angel_order_ops", ORDER_API_MIN_INTERVAL_SEC)
                order_id = self.obj.placeOrder(order_params)
                logger.info(f"📤 Order placed: {buy_sell} {qty} {symbol} — ID: {order_id}")
                time.sleep(2)
                fill_price = self._get_fill_price(order_id)
                return order_id, fill_price if fill_price is not None else price
            except Exception as e:
                logger.error(f"Order placement failed: {e}")
                return None

    def _get_fill_price(self, order_id: str) -> Optional[float]:
        try:
            orders = self.obj.orderBook()
            if isinstance(orders, dict) and "data" in orders:
                orders = orders["data"]

            if orders and isinstance(orders, list):
                for order in orders:
                    if order.get("orderid") == order_id and str(order.get("status", "")).lower() == "complete":
                        avg = order.get("averageprice") or order.get("averagePrice")
                        if avg is not None:
                            return float(avg)
        except Exception as e:
            logger.error(f"Failed to fetch fill price for {order_id}: {e}")
        return None

    def _auto_refresh_session(self) -> bool:
        """
        Auto-refresh Angel One session if token is expired.
        Called at startup and every 4 hours.
        Returns True if session is valid.
        """
        try:
            import pyotp, os
            totp_secret = os.getenv("TOTP_SECRET","")
            api_key     = os.getenv("API_KEY","")
            client_id   = os.getenv("CLIENT_ID","")
            password    = os.getenv("PASSWORD","")
            if not all([totp_secret, api_key, client_id, password]):
                return False
            totp = pyotp.TOTP(totp_secret).now()
            resp = self.obj.generateSession(client_id, password, totp)
            if resp and resp.get("status"):
                self._token     = resp["data"]["jwtToken"]
                self._feed_token = resp["data"].get("feedToken","")
                self._last_refresh = __import__("time").time()
                import logging
                logging.getLogger(__name__).info(
                    "Angel session refreshed ✅ (client=%s)", client_id)
                return True
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("Session refresh failed: %s", e)
        return False

    def _ensure_session_fresh(self) -> None:
        """Refresh session if older than 4 hours."""
        import time
        last = getattr(self, "_last_refresh", 0)
        if time.time() - last > 14400:  # 4 hours
            self._auto_refresh_session()

    _last_balance_call: float = 0.0

    def _get_real_ltp(self, symbol: str, exchange: str = None) -> Optional[float]:
        """Fetch real LTP even in paper mode (for accurate paper trading)."""
        if not self._ensure_connected() or self.obj is None:
            return None
        if exchange is None:
            exchange = "NFO" if ("CE" in symbol or "PE" in symbol) else "NSE"
        token = self._get_token_no_lock(symbol, exchange)
        if not token:
            return None
        try:
            resp = self.obj.ltpData(exchange, symbol, token)
            if resp and resp.get("data"):
                return float(resp["data"].get("ltp", 0))
        except Exception as exc:
            # Fully silent before -- this feeds paper-fill pricing directly,
            # so a real connectivity failure looked identical to "no data yet".
            try:
                from exception_telemetry import record_exception
                record_exception("angel", "get_real_ltp", exc, context={"symbol": symbol})
            except Exception:
                pass
        return None

    def reconcile_positions(self) -> dict:
        """Compare local tracked positions vs Angel actual positions.
        Returns: {matched, missing_local, missing_angel, mismatched_qty}
        """
        result = {"matched": [], "missing_local": [], "missing_angel": [], "mismatched": []}
        if not self._ensure_connected() or not self.obj:
            return result
        try:
            with self._lock:
                resp = self.obj.position()
            if not resp or not resp.get("data"):
                return result
            angel_positions = {}
            for p in resp["data"]:
                sym = p.get("tradingsymbol","")
                qty = int(p.get("netqty",0) or 0)
                if qty != 0:
                    angel_positions[sym] = {"qty": qty, "avg_price": float(p.get("averageprice",0) or 0)}
            result["angel_positions"] = angel_positions
        except Exception as e:
            logger.debug("reconcile: %s", e)
        return result

    def verify_order_fill(self, order_id: str, max_wait: int = 30) -> dict:
        """Check if order filled, partially filled, or rejected.
        Returns: {status, filled_qty, avg_price, rejection_reason}
        """
        import time as _vt
        if not self._ensure_connected() or not self.obj:
            return {"status": "unknown", "filled_qty": 0, "avg_price": 0.0}
        
        for attempt in range(max_wait // 3):
            try:
                with self._lock:
                    book = self.obj.orderBook()
                if book and book.get("data"):
                    for order in book["data"]:
                        if str(order.get("orderid","")) == str(order_id):
                            status = order.get("orderstatus","").upper()
                            filled = int(order.get("filledshares",0) or 0)
                            price  = float(order.get("averageprice",0) or 0)
                            reject = order.get("text","") or order.get("rejreason","")
                            
                            if status in ("COMPLETE","TRADED"):
                                return {"status":"filled","filled_qty":filled,"avg_price":price,"rejection_reason":""}
                            elif status == "REJECTED":
                                return {"status":"rejected","filled_qty":0,"avg_price":0,"rejection_reason":reject}
                            elif status in ("OPEN","PENDING","TRIGGER PENDING"):
                                _vt.sleep(3)
                                continue
                            elif filled > 0:
                                return {"status":"partial","filled_qty":filled,"avg_price":price,"rejection_reason":""}
            except Exception as e:
                logger.debug("verify_order %s: %s", order_id, e)
                _vt.sleep(2)
        return {"status": "timeout", "filled_qty": 0, "avg_price": 0.0}

    def _remember_balance(self, balance: float) -> float:
        try:
            bal = float(balance or 0.0)
        except Exception:
            bal = 0.0
        if bal > 0:
            self._balance_cache_value = bal
            self._balance_cache_ts = time.time()
        return bal

    def _cached_balance(self, max_age: Optional[float] = None) -> float:
        try:
            if self._balance_cache_value <= 0 or self._balance_cache_ts <= 0:
                return 0.0
            age = time.time() - self._balance_cache_ts
            if age <= float(max_age if max_age is not None else self._balance_cache_ttl):
                return float(self._balance_cache_value)
        except Exception:
            return 0.0
        return 0.0

    def get_balance(self, force_real: bool = False) -> float:
        """Return real Angel One balance.
        In paper mode returns 0.0 so callers know
        no real balance is available.
        Use force_real=True to bypass paper check (used by dual_mode_engine).
        """
        cached = self._cached_balance()
        if cached > 0:
            return cached

        # Paper mode: still try real balance for auto-mode switching
        if self.paper_trade and not force_real:
            try:
                if self.obj:
                    _b_resp = self.obj.rmsLimit()
                    if _b_resp and _b_resp.get("data"):
                        _b_val = float(_b_resp["data"].get("availablecash","0") or
                                       _b_resp["data"].get("net","0") or 0)
                        if _b_val > 0:
                            return self._remember_balance(_b_val)
            except Exception as e:
                if _is_rate_limited(e):
                    stale = self._cached_balance(self._balance_stale_ok_sec)
                    if stale > 0:
                        logger.debug("Balance rate-limited; using cached balance ₹%.0f", stale)
                        return stale
            return 0.0

        if not self._ensure_connected():
            stale = self._cached_balance(self._balance_stale_ok_sec)
            if stale > 0:
                logger.debug("Balance fetch skipped: no connection; using cached ₹%.0f", stale)
                return stale
            logger.error("Balance fetch failed: no connection")
            return 0.0

        now = time.time()
        if self._balance_rate_limited_until > now:
            stale = self._cached_balance(self._balance_stale_ok_sec)
            if stale > 0:
                logger.debug("Balance fetch in rate-limit cooldown; using cached ₹%.0f", stale)
                return stale

        try:
            with self._lock:
                data = self.obj.rmsLimit()

            if not data:
                logger.warning("Balance fetch returned empty response")
                return 0.0

            payload = data.get("data", data)
            if not isinstance(payload, dict):
                logger.warning(f"Unexpected balance payload: {data}")
                return 0.0

            for _bk in ["availablecash","availableCash","net","cash",
                        "available_margin","availableMargin",
                        "netavailablecash","NetAvailableCash",
                        "availableBalance","utilisedMargin"]:
                _bv = payload.get(_bk)
                if _bv is not None:
                    try:
                        _bf = float(_bv)
                        if _bf > 0:
                            return self._remember_balance(_bf)
                    except Exception: continue
            # Try nested
            for _sub in ["equity","commodity","fno","derivatives"]:
                _sp = payload.get(_sub, {})
                if isinstance(_sp, dict):
                    for _bk in ["availablecash","net","cash","available_margin"]:
                        _bv = _sp.get(_bk)
                        if _bv:
                            try:
                                _bf = float(_bv)
                                if _bf > 0:
                                    return self._remember_balance(_bf)
                            except Exception: pass

            logger.warning(f"Known balance keys not found in rmsLimit response: {list(payload.keys())}")
            return 0.0

        except Exception as e:
            if _is_rate_limited(e):
                self._balance_rate_limited_until = time.time() + float(
                    os.getenv("ANGEL_BALANCE_RATELIMIT_COOLDOWN_SEC", "30")
                )
                stale = self._cached_balance(self._balance_stale_ok_sec)
                if stale > 0:
                    if (time.time() - self._balance_last_rate_log_ts) > 60:
                        logger.warning(
                            "Balance fetch rate-limited; using cached balance ₹%.0f",
                            stale,
                        )
                        self._balance_last_rate_log_ts = time.time()
                    return stale
                logger.warning("Balance fetch rate-limited and no cached balance available")
            else:
                logger.error(f"Balance fetch failed: {e}")
            return 0.0

    @retry(max_retries=2, base_delay=1)
    def get_ltp(self, symbol: str, exchange: Optional[str] = None) -> Optional[float]:
        # ALWAYS try real LTP (even in paper mode — needed for accurate paper trades)
        try:
            if self._ensure_connected() and self.obj is not None:
                if exchange is None:
                    exchange = "NFO" if ("CE" in symbol or "PE" in symbol) else "NSE"
                token = self._get_token_no_lock(symbol, exchange)
                if token:
                    with self._lock:
                        resp = self.obj.ltpData(exchange, symbol, token)
                    if resp and resp.get("data"):
                        ltp = float(resp["data"].get("ltp", 0))
                        if ltp > 0:
                            return ltp
        except Exception: pass
        # Fallback for paper mode when real LTP unavailable
        if self.paper_trade:
            if "CE" in symbol or "PE" in symbol:
                return PAPER_OPTION_LTP
            return PAPER_SPOT_LTP
        return None

        if not self._ensure_connected():
            return None

        if exchange is None:
            exchange = "NFO" if ("CE" in symbol or "PE" in symbol) else "NSE"

        token = self._get_token_no_lock(symbol, exchange)
        if not token:
            logger.warning(f"LTP token not found for {symbol} on {exchange}")
            return None

        with self._lock:
            try:
                ltp_data = self.obj.ltpData(exchange, symbol, token)

                if not ltp_data:
                    logger.error(f"LTP fetch failed for {symbol}: empty response")
                    return None

                if not isinstance(ltp_data, dict):
                    logger.error(f"LTP fetch failed for {symbol}: non-dict response {ltp_data}")
                    return None

                if not ltp_data.get("status", False):
                    logger.error(f"LTP fetch failed for {symbol}: {ltp_data}")
                    return None

                data = ltp_data.get("data")
                if not data or "ltp" not in data:
                    logger.error(f"LTP fetch failed for {symbol}: malformed response {ltp_data}")
                    return None

                return float(data["ltp"])

            except KeyError as e:
                logger.error(
                    "SmartAPI SDK bug while fetching LTP for %s on %s (token=%s): %s",
                    symbol, exchange, token, e
                )
                return None

            except Exception as e:
                logger.error(f"LTP fetch failed for {symbol}: {e}")
                return None

    def get_market_depth(self, symbol: str, exchange: str = "NSE",
                          force_live: bool = False) -> Dict[str, float]:
        """
        Return best bid and ask prices.

        Paper mode: simulates spread using PAPER_SPREAD_PCT of LTP.
        Live mode:  calls SmartAPI getMarketData (LTP mode) and reads
                    best bid/ask from the depth response.

        force_live=True bypasses the paper-mode simulation and always calls the
        real SmartAPI depth endpoint, regardless of self.paper_trade -- for
        research/logging callers that want a genuine market reading (e.g.
        capturing real spread at signal time) without affecting any order-
        routing or P&L-simulation caller, whose behavior is unchanged.

        Returns {"bid": float, "ask": float}.
        If the API call fails or returns no depth, returns {"bid": 0.0, "ask": 0.0}
        so callers can detect "unknown" as bid == ask == 0.
        """
        if self.paper_trade and not force_live:
            base_price  = PAPER_OPTION_LTP if ("CE" in symbol or "PE" in symbol) else PAPER_SPOT_LTP
            half_spread = base_price * PAPER_SPREAD_PCT / 100
            return {
                "bid": round(base_price - half_spread, 2),
                "ask": round(base_price + half_spread, 2),
            }

        # ── Live: use SmartAPI getMarketData ─────────────────────────────
        if not self.angel:
            return {"bid": 0.0, "ask": 0.0}

        token = self._get_token_for_symbol(symbol, exchange)
        if not token:
            logger.debug("get_market_depth: no token for %s/%s", symbol, exchange)
            return {"bid": 0.0, "ask": 0.0}

        try:
            # SmartAPI getMarketData with mode=FULL returns Level-2 depth
            resp = self.angel.getMarketData(
                mode="FULL",
                exchangeTokens={exchange: [token]},
            )
            if not isinstance(resp, dict) or not resp.get("status"):
                return {"bid": 0.0, "ask": 0.0}

            data = resp.get("data", {})
            fetched = (
                data.get("fetched")
                or data.get(exchange, {}).get("fetched")
                or []
            )
            if not fetched:
                return {"bid": 0.0, "ask": 0.0}

            item = fetched[0] if isinstance(fetched, list) else fetched

            # Depth fields vary by SmartAPI version
            # Try "bestFiveBuy" / "bestFiveSell" first (newer), then ltp fallback
            bid = ask = 0.0

            buy_depth  = item.get("bestFiveBuy",  item.get("depth", {}).get("buy",  []))
            sell_depth = item.get("bestFiveSell", item.get("depth", {}).get("sell", []))

            if buy_depth and isinstance(buy_depth, list) and buy_depth[0]:
                bid = float(buy_depth[0].get("price", 0.0))
            if sell_depth and isinstance(sell_depth, list) and sell_depth[0]:
                ask = float(sell_depth[0].get("price", 0.0))

            # Fallback: if depth not available but LTP is, estimate spread
            if bid == 0.0 and ask == 0.0:
                ltp = float(item.get("ltp", 0.0))
                if ltp > 0:
                    tick = 0.05 if ltp < 100 else 0.10
                    bid  = round(ltp - tick, 2)
                    ask  = round(ltp + tick, 2)

            return {"bid": bid, "ask": ask}

        except Exception as exc:
            logger.debug("get_market_depth failed for %s: %s", symbol, exc)
            return {"bid": 0.0, "ask": 0.0}


    def place_sl_order(
        self,
        symbol:      str,
        qty:         int,
        buy_sell:    str,
        trigger_price: float,
        exchange:    str = "NFO",
        producttype: str = "INTRADAY",
        order_tag:   str = "",
    ) -> Optional[str]:
        """
        Place a Stop-Loss Market (SL-M) order at the broker.

        When triggered, the order executes at market price.
        Use this as a safety net: if internet drops, the broker will
        still close the position when price touches trigger_price.

        In paper mode returns a fake SL order ID.

        Parameters
        ----------
        trigger_price : The SL trigger level (not the limit price — SL-M)
        buy_sell      : "SELL" for long positions, "BUY" for short positions
        """
        # Honour the runtime PAPER block (block_real_orders / PAPER_ORDERS_ONLY),
        # not just paper_trade — same guard as place_order (see place_gtt_order).
        _paper_orders_only = getattr(__import__("config"), "PAPER_ORDERS_ONLY", False)
        if self.paper_trade or _paper_orders_only or getattr(self, "block_real_orders", False):
            self._paper_order_counter += 1
            fake_id = f"PAPER_SL_{self._paper_order_counter:06d}"
            logger.info(
                "PAPER SL-M order: %s %d %s trigger=%.2f → %s",
                buy_sell, qty, symbol, trigger_price, fake_id,
            )
            return fake_id

        if not self._ensure_connected():
            logger.error("place_sl_order: no connection")
            return None

        token = self._get_token_no_lock(symbol, exchange)
        if not token:
            logger.error("place_sl_order: token not found for %s", symbol)
            return None

        # SL-M = variety STOPLOSS, ordertype STOPLOSS_MARKET
        order_params = {
            "variety":        "STOPLOSS",
            "tradingsymbol":  symbol,
            "symboltoken":    token,
            "transactiontype": buy_sell.upper(),
            "exchange":       exchange,
            "ordertype":      "STOPLOSS_MARKET",
            "producttype":    producttype.upper(),
            "duration":       "DAY",
            "price":          "0",
            "triggerprice":   str(round(trigger_price, 2)),
            "squareoff":      "0",
            "stoploss":       "0",
            "quantity":       str(qty),
        }

        # SEBI Apr-2026 algo-order audit trail: tag automated SL orders
        _sl_tag = _sanitize_order_tag(order_tag)
        if _sl_tag:
            order_params["ordertag"] = _sl_tag

        with self._lock:
            try:
                from request_governor import acquire as _acquire_request_slot
                _acquire_request_slot("angel_order_ops", ORDER_API_MIN_INTERVAL_SEC)
                order_id = self.obj.placeOrder(order_params)
                logger.info(
                    "SL-M order placed: %s %d %s trigger=%.2f → %s",
                    buy_sell, qty, symbol, trigger_price, order_id,
                )
                return order_id
            except Exception as exc:
                logger.error(
                    "place_sl_order failed for %s trigger=%.2f: %s",
                    symbol, trigger_price, exc,
                )
                return None

    def poll_order_fill(
        self,
        order_id: str,
        timeout_sec: float = 10.0,
        poll_interval: float = 1.0,
    ) -> Optional[Dict[str, Any]]:
        """
        Poll getOrderBook until the order is COMPLETE or timeout.

        Returns the order dict when complete, or None if not filled within
        timeout_sec. In paper mode, returns a synthetic fill immediately.

        Parameters
        ----------
        order_id       : The order ID to poll
        timeout_sec    : Give up after this many seconds (default 10s)
        poll_interval  : How often to poll in seconds (default 1s)
        """
        if self.paper_trade:
            return {
                "orderid": order_id, "status": "complete",
                "averageprice": PAPER_OPTION_LTP,
            }

        if not self._ensure_connected():
            return None

        start = time.time()
        while (time.time() - start) < timeout_sec:
            try:
                with self._lock:
                    orders = self.obj.orderBook()

                if isinstance(orders, dict):
                    orders = orders.get("data", [])

                if orders and isinstance(orders, list):
                    for o in orders:
                        if str(o.get("orderid", "")) == str(order_id):
                            status = str(o.get("status", "")).lower()
                            if status in ("complete", "filled", "executed"):
                                logger.info(
                                    "Order confirmed COMPLETE: %s avgPrice=%s",
                                    order_id, o.get("averageprice"),
                                )
                                return o
                            elif status in ("rejected", "cancelled", "canceled", "failed"):
                                logger.warning(
                                    "Order %s in terminal state: %s", order_id, status
                                )
                                return None
            except Exception as exc:
                logger.debug("poll_order_fill error: %s", exc)

            time.sleep(poll_interval)

        logger.warning(
            "poll_order_fill timed out after %.0fs for order %s",
            timeout_sec, order_id,
        )
        return None

    def _get_token_for_symbol(self, symbol: str, exchange: str) -> Optional[str]:
        """
        Look up SmartAPI token for a symbol/exchange pair.
        Checks hardcoded index map first, then master contract.
        """
        # Index tokens
        key = (exchange.upper(), symbol.upper())
        token = INDEX_TOKEN_MAP.get(key)
        if token:
            return token

        # Master contract lookup
        try:
            token = self.master.get_token(symbol, exchange)
            if token:
                return token
        except Exception as _e:
            import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

        return None

    def _get_index_token(self, symbol: str, exchange: str) -> Optional[str]:
        if exchange not in ("NSE", "BSE"):
            return None

        key = (exchange, symbol)
        token = INDEX_TOKEN_MAP.get(key)
        if token:
            return token

        normalized = symbol.strip().upper()
        for (exch, sym), tok in INDEX_TOKEN_MAP.items():
            if exch == exchange and sym.strip().upper() == normalized:
                return tok

        return None

    def get_token(self, symbol: str, exchange: Optional[str] = None) -> Optional[str]:
        if exchange is None:
            exchange = "NFO" if ("CE" in symbol or "PE" in symbol) else "NSE"

        cache_key = f"{exchange}:{symbol}"

        with self._lock:
            if cache_key in self._token_cache:
                return self._token_cache[cache_key]
            # Negative cache: a symbol that didn't resolve recently won't resolve
            # now either — skip the searchScrip API call to avoid a rate-limit storm.
            _miss_ts = self._token_miss.get(cache_key, 0.0)
            if _miss_ts and (time.time() - _miss_ts) < TOKEN_MISS_TTL:
                return None

        token = self._get_index_token(symbol, exchange)

        # BUG FIX 2026-06-12 (same as _get_token_no_lock): on NSE the cash
        # map must be consulted BEFORE the NFO master, which otherwise
        # shadows stocks with derivative tokens (TATAPOWER -> 143493) and
        # getCandleData returns SUCCESS with 0 candles for every stock.
        if not token and exchange == "NSE":
            token = _load_nse_eq_tokens().get(symbol.upper().replace("-EQ", ""))

        if not token:
            token = self.master.get_token(symbol)

        if not token:
            token = self._search_scrip_safe(symbol, exchange)

        if token:
            with self._lock:
                self._token_cache[cache_key] = token
                self._token_miss.pop(cache_key, None)
        else:
            with self._lock:
                self._token_miss[cache_key] = time.time()
            logger.warning(f"Token not found for {symbol} on {exchange}")

        return token

    def _get_token_no_lock(self, symbol: str, exchange: str) -> Optional[str]:
        cache_key = f"{exchange}:{symbol}"
        if cache_key in self._token_cache:
            return self._token_cache[cache_key]

        token = self._get_index_token(symbol, exchange)

        # BUG FIX 2026-06-12: for NSE (cash) requests the NFO master was
        # consulted FIRST, shadowing stock tokens with derivative tokens
        # (e.g. TATAPOWER -> 143493); getCandleData then returns SUCCESS
        # with 0 candles, silently killing intraday data for every stock.
        # Cash-market map must win on NSE; NFO master stays first for NFO/BFO.
        if not token and exchange == "NSE":
            eq_map = _load_nse_eq_tokens()
            token = eq_map.get(symbol.upper().replace("-EQ", ""))

        if not token:
            # Try master contract (NFO tokens)
            try: token = self.master.get_token(symbol)
            except Exception: pass

        if not token:
            # Try NSE EQ cash market tokens (stocks)
            eq_map = _load_nse_eq_tokens()
            token = eq_map.get(symbol.upper().replace("-EQ",""))

        if not token and exchange in ("NFO", "BFO"):
            # F&O option/future tokens (incl. BFO — SENSEX/BANKEX)
            token = _load_fo_tokens().get(symbol.upper())

        if not token:
            logger.debug("No token found for %s on %s", symbol, exchange)

        if token:
            self._token_cache[cache_key] = token

        return token


    def modify_order(self, order_id: str, new_sl: float = None, 
                     new_target: float = None, new_qty: int = None) -> dict:
        """
        Modify an open order — update SL, target, or quantity.
        Used by TrailingStopManager to update SL after entry.
        """
        if not self._obj:
            return {"status": False, "message": "Not connected"}
        try:
            params = {"variety": "NORMAL", "orderid": str(order_id)}
            if new_sl is not None:
                params["triggerprice"] = str(round(new_sl, 2))
                params["price"]        = str(round(new_sl, 2))
            if new_qty is not None:
                params["quantity"] = str(new_qty)
            from request_governor import acquire as _acquire_request_slot
            _acquire_request_slot("angel_order_ops", ORDER_API_MIN_INTERVAL_SEC)
            resp = self._obj.modifyOrder(params)
            if resp and resp.get("status"):
                logger.info("Order modified: %s → SL=%.2f", order_id, new_sl or 0)
            return resp or {"status": False}
        except Exception as e:
            logger.warning("modify_order %s: %s", order_id, e)
            return {"status": False, "message": str(e)}

    def cancel_order(self, order_id: str, variety: str = "NORMAL") -> dict:
        """Cancel an open order."""
        if not self._obj:
            return {"status": False, "message": "Not connected"}
        try:
            from request_governor import acquire as _acquire_request_slot
            _acquire_request_slot("angel_order_ops", ORDER_API_MIN_INTERVAL_SEC)
            resp = self._obj.cancelOrder(order_id, variety)
            return resp or {"status": False}
        except Exception as e:
            logger.warning("cancel_order %s: %s", order_id, e)
            return {"status": False, "message": str(e)}

    def get_order_status(self, order_id: str) -> dict:
        """Get current status of an order."""
        if not self._obj:
            return {}
        try:
            orders = self._obj.orderBook()
            if orders and orders.get("data"):
                for o in orders["data"]:
                    if str(o.get("orderid","")) == str(order_id):
                        return o
        except Exception as e:
            logger.debug("order_status %s: %s", order_id, e)
        return {}

    def get_historical_data_yf(self, symbol: str, days: int = 60,
                               interval: str = "5m"):
        """yfinance fallback when Angel One data unavailable. Disabled by DISABLE_YFINANCE=true."""
        try:
            import os as _os_yf
            if _os_yf.getenv("DISABLE_YFINANCE", "false").lower() == "true":
                return None
            import yf_compat as yf  # yfinance removed: Yahoo API broken
            ticker_map = {
                "NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK",
                "SENSEX": "^BSESN",
            }
            ticker = ticker_map.get(symbol.upper(), f"{symbol}.NS")
            period = f"{days}d" if days <= 60 else "3mo"
            df = yf.download(ticker, period=period, interval=interval,
                             progress=False, auto_adjust=True)
            if df is not None and len(df) > 0:
                df.columns = [c.lower() for c in df.columns]
                return df
        except Exception as e:
            import logging; logging.getLogger(__name__).debug("yf fallback: %s", e)
        return None

    def get_historical_data(
        self,
        symbol:    str,
        interval:  str = "FIVE_MINUTE",
        from_date: str = "",
        to_date:   str = "",
        exchange:  str = "NSE",
    ):
        """Fetch historical OHLCV candles from Angel One SmartAPI."""
        if time.time() < self._rate_limited_until:
            return None
        if not self._ensure_connected():
            return None
        try:
            with self._lock:
                obj = self.obj
            if obj is None:
                return None

            # Map common interval strings
            _iv_map = {
                "1m":"ONE_MINUTE","5m":"FIVE_MINUTE","15m":"FIFTEEN_MINUTE",
                "30m":"THIRTY_MINUTE","1h":"ONE_HOUR","1d":"ONE_DAY",
                "ONE_MINUTE":"ONE_MINUTE","FIVE_MINUTE":"FIVE_MINUTE",
                "FIFTEEN_MINUTE":"FIFTEEN_MINUTE","THIRTY_MINUTE":"THIRTY_MINUTE",
                "ONE_HOUR":"ONE_HOUR","ONE_DAY":"ONE_DAY",
            }
            iv = _iv_map.get(interval, "ONE_DAY")

            # Get symbol token
            token = self.get_token(symbol, exchange)
            if not token:
                logger.debug("No token for %s on %s", symbol, exchange)
                return None

            import pandas as pd

            def _fetch_chunk(_from: str, _to: str):
                """One getCandleData request → DataFrame (or None)."""
                if time.time() < self._rate_limited_until:
                    return None
                # SmartAPI applies the historical-candle limit account-wide.
                # Serialize calls from scanner/backfill threads and pace them.
                with self._api_rate_lock:
                    from request_governor import acquire as _acquire_request_slot
                    _acquire_request_slot("angel_historical_candles", API_MIN_INTERVAL_SEC)
                    try:
                        resp = obj.getCandleData({
                            "exchange":    exchange,
                            "symboltoken": token,
                            "interval":    iv,
                            "fromdate":    _from,
                            "todate":      _to,
                        })
                    finally:
                        self._last_api_call_ts = time.monotonic()
                if not resp or resp.get("status") != True:
                    if _is_rate_limited(resp):
                        self._rate_limited_until = time.time() + RATELIMIT_COOLDOWN
                        logger.warning(
                            "Candle API throttled; pausing broker API calls for %ss",
                            RATELIMIT_COOLDOWN,
                        )
                    logger.debug("Candle data error for %s: %s", symbol, resp)
                    return None
                candles = resp.get("data", [])
                if not candles:
                    return None
                rows = []
                for c in candles:
                    try:
                        rows.append({
                            "date":   pd.Timestamp(c[0]),
                            "open":   float(c[1]),
                            "high":   float(c[2]),
                            "low":    float(c[3]),
                            "close":  float(c[4]),
                            "volume": float(c[5]) if len(c) > 5 else 0,
                        })
                    except Exception:
                        continue
                return pd.DataFrame(rows) if rows else None

            # Angel caps each getCandleData request at a per-interval span. For
            # longer requests we STITCH successive chunks (the data exists — Angel
            # serves older ranges fine, it just limits one request's span). Caps
            # are set conservatively below Angel's documented per-interval limits.
            _chunk_days = {
                "ONE_MINUTE": 25, "THREE_MINUTE": 55, "FIVE_MINUTE": 90,
                "TEN_MINUTE": 90, "FIFTEEN_MINUTE": 180, "THIRTY_MINUTE": 180,
                "ONE_HOUR": 350, "ONE_DAY": 2000,
            }.get(iv, 90)
            _fmt = "%Y-%m-%d %H:%M"

            # Parse the requested span; on any parse issue fall back to a single
            # request (exactly the old behaviour).
            try:
                _start = datetime.strptime(from_date, _fmt)
                _end   = datetime.strptime(to_date,   _fmt)
            except Exception:
                _start = _end = None

            if _start is None or _end is None or (_end - _start).days <= _chunk_days:
                df = _fetch_chunk(from_date, to_date)
            else:
                parts = []
                cur = _start
                while cur < _end:
                    seg_to = min(cur + timedelta(days=_chunk_days), _end)
                    part = _fetch_chunk(cur.strftime(_fmt), seg_to.strftime(_fmt))
                    if part is not None and not part.empty:
                        parts.append(part)
                    cur = seg_to
                    if cur < _end:
                        time.sleep(0.4)  # respect Angel rate limit between chunks
                df = pd.concat(parts) if parts else None

            if df is None or df.empty:
                return None
            df = (df.drop_duplicates(subset="date")
                    .set_index("date").sort_index())
            logger.debug("Angel historical: %s %s %d bars", symbol, iv, len(df))
            return df
        except Exception as e:
            if _is_rate_limited(e):
                self._rate_limited_until = time.time() + RATELIMIT_COOLDOWN
                logger.warning(
                    "Candle API throttled; pausing broker API calls for %ss",
                    RATELIMIT_COOLDOWN,
                )
            logger.debug("get_historical_data %s: %s", symbol, e)
            return None

    def _search_scrip_safe(self, symbol: str, exchange: str) -> Optional[str]:
        if not self._ensure_connected():
            return None

        # Honour the account-wide rate-limit circuit breaker (same one login /
        # balance use). Calling searchScrip while throttled only deepens the
        # limit and starves candle fetches for valid symbols.
        if time.time() < self._rate_limited_until:
            return None

        try:
            from request_governor import acquire as _acquire_request_slot
            _acquire_request_slot("angel_order_ops", ORDER_API_MIN_INTERVAL_SEC)
            result = self.obj.searchScrip(exchange, symbol)
            if result and result.get("data"):
                return result["data"][0].get("symboltoken")
        except KeyError as e:
            logger.error(
                "SmartAPI SDK bug in searchScrip for %s on %s: %s",
                symbol, exchange, e
            )
            return None
        except Exception as e:
            if _is_rate_limited(e):
                self._rate_limited_until = time.time() + RATELIMIT_COOLDOWN
                logger.warning(
                    "searchScrip rate-limited — backing off %ss (was hammering for %s)",
                    RATELIMIT_COOLDOWN, symbol,
                )
            else:
                logger.error(f"searchScrip failed for {symbol}: {e}")

        return None


    def _track_slippage(self, symbol: str, expected: float,
                        filled: float, side: str) -> None:
        """Record slippage between expected and actual fill."""
        try:
            if not expected or not filled:
                return
            slip = abs(filled - expected) / expected * 100
            import json, time
            from pathlib import Path
            sf = Path("slippage_log.json")
            d = json.loads(sf.read_text()) if sf.exists() else []
            d.append({"ts": time.time(), "symbol": symbol, "side": side,
                      "expected": expected, "filled": filled,
                      "slip_pct": round(slip, 4)})
            sf.write_text(json.dumps(d[-500:]))
            import logging
            logging.getLogger("angel").info(
                "Slippage %s %s exp=%.2f fill=%.2f %.3f%%",
                symbol, side, expected, filled, slip)
        except Exception as _e:
            import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)
