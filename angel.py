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

import threading
import time
from typing import Dict, Optional, Tuple

import pandas as pd
import pyotp
from SmartApi import SmartConnect

import logging
from master_contract import MasterContract
from utils import retry

logger = logging.getLogger("angel")

PAPER_SPOT_LTP = 22000.0
PAPER_OPTION_LTP = 120.0
PAPER_SPREAD_PCT = 0.02
MAX_CONNECT_RETRIES = 3
CONNECT_BASE_DELAY = 2

INDEX_TOKEN_MAP = {
    ("NSE", "Nifty 50"): "99926000",
    ("NSE", "NIFTY"): "99926000",
    ("NSE", "NIFTY 50"): "99926000",
    ("NSE", "Nifty Bank"): "99926009",
    ("NSE", "BANKNIFTY"): "99926009",
    ("NSE", "Nifty Fin Service"): "99926037",
    ("NSE", "FINNIFTY"): "99926037",
    ("NSE", "SENSEX"): "99919000",
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
                sym = str(row.get("symbol","")).replace("-EQ","").strip().upper()
                tok = str(row.get("token","")).strip()
                nm  = str(row.get("name","")).strip().upper()
                if sym and tok and tok != "nan":
                    _NSE_EQ_TOKENS[sym] = tok
                    if nm and nm != "NAN": _NSE_EQ_TOKENS[nm] = tok
            if _NSE_EQ_TOKENS:
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
        self._lock = threading.Lock()
        self._paper_order_counter = 0

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
        for attempt in range(1, MAX_CONNECT_RETRIES + 1):
            try:
                totp = pyotp.TOTP(self.totp_secret).now()
                obj = SmartConnect(api_key=self.api_key)
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
        if self.paper_trade:
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
                "price":           str(round(limit_price, 2)),
                "triggerprice":    str(round(trigger_price, 2)),
                "disclosedqty":    "0",
                "timeperiod":      "365",
            }
            with self._lock:
                resp = self.obj.gttCreateRule(gtt_params)

            if resp and resp.get("status"):
                gtt_id = resp.get("data", {}).get("id", "unknown")
                logger.info(
                    "GTT SL placed: %s trigger=%.2f qty=%d id=%s",
                    symbol, trigger_price, qty, gtt_id
                )
                return str(gtt_id)
            else:
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
    ) -> Optional[Tuple[str, float]]:
        if qty <= 0:
            logger.error(f"place_order: invalid qty={qty}")
            return None

        if price < 0:
            logger.error(f"place_order: invalid price={price}")
            return None

        if self.paper_trade:
            self._paper_order_counter += 1
            fake_id = f"PAPER_{self._paper_order_counter:06d}"

            if price > 0:
                fill_price = price
            elif "CE" in symbol or "PE" in symbol:
                fill_price = PAPER_OPTION_LTP
            else:
                fill_price = PAPER_SPOT_LTP

            logger.info(
                f"📤 PAPER ORDER #{self._paper_order_counter}: "
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
            "price": str(price) if order_type.upper() == "LIMIT" else "0",
            "squareoff": "0",
            "stoploss": "0",
            "quantity": str(qty),
        }

        with self._lock:
            try:
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
            orders = self.obj.getOrderBook()
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
        except Exception:
            pass
        return None

    def get_balance(self, force_real: bool = False) -> float:
        """Return real Angel One balance.
        In paper mode returns 0.0 so callers know
        no real balance is available.
        Use force_real=True to bypass paper check (used by dual_mode_engine).
        """
        # Paper mode: still try real balance for auto-mode switching
        if self.paper_trade and not force_real:
            try:
                if self.obj:
                    _b_resp = self.obj.rmsLimit()
                    if _b_resp and _b_resp.get("data"):
                        _b_val = float(_b_resp["data"].get("availablecash","0") or
                                       _b_resp["data"].get("net","0") or 0)
                        if _b_val > 0:
                            return _b_val
            except Exception: pass
            return 0.0

        if not self._ensure_connected():
            logger.error("Balance fetch failed: no connection")
            return 0.0

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
                        if _bf > 0: return _bf
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
                                if _bf > 0: return _bf
                            except Exception: pass

            logger.warning(f"Known balance keys not found in rmsLimit response: {list(payload.keys())}")
            return 0.0

        except Exception as e:
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

    def get_market_depth(self, symbol: str, exchange: str = "NSE") -> Dict[str, float]:
        """
        Return best bid and ask prices.

        Paper mode: simulates spread using PAPER_SPREAD_PCT of LTP.
        Live mode:  calls SmartAPI getMarketData (LTP mode) and reads
                    best bid/ask from the depth response.

        Returns {"bid": float, "ask": float}.
        If the API call fails or returns no depth, returns {"bid": 0.0, "ask": 0.0}
        so callers can detect "unknown" as bid == ask == 0.
        """
        if self.paper_trade:
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
        if self.paper_trade:
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

        with self._lock:
            try:
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
                    orders = self.obj.getOrderBook()

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
        if exchange != "NSE":
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

        token = self._get_index_token(symbol, exchange)

        if not token:
            token = self.master.get_token(symbol)

        if not token:
            token = self._search_scrip_safe(symbol, exchange)

        if token:
            with self._lock:
                self._token_cache[cache_key] = token
        else:
            logger.warning(f"Token not found for {symbol} on {exchange}")

        return token

    def _get_token_no_lock(self, symbol: str, exchange: str) -> Optional[str]:
        cache_key = f"{exchange}:{symbol}"
        if cache_key in self._token_cache:
            return self._token_cache[cache_key]

        token = self._get_index_token(symbol, exchange)

        if not token:
            # Try master contract (NFO tokens)
            try: token = self.master.get_token(symbol)
            except Exception: pass

        if not token:
            # Try NSE EQ cash market tokens (stocks)
            eq_map = _load_nse_eq_tokens()
            token = eq_map.get(symbol.upper().replace("-EQ",""))

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
        """yfinance fallback when Angel One data unavailable."""
        try:
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
            token = self._get_token(symbol, exchange)
            if not token:
                logger.debug("No token for %s on %s", symbol, exchange)
                return None

            params = {
                "exchange":   exchange,
                "symboltoken":token,
                "interval":   iv,
                "fromdate":   from_date,
                "todate":     to_date,
            }
            resp = obj.getCandleData(params)
            if not resp or resp.get("status") != True:
                logger.debug("Candle data error for %s: %s", symbol, resp)
                return None

            candles = resp.get("data", [])
            if not candles:
                return None

            import pandas as pd
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
            if not rows:
                return None
            df = pd.DataFrame(rows).set_index("date").sort_index()
            logger.debug("Angel historical: %s %s %d bars", symbol, iv, len(df))
            return df
        except Exception as e:
            logger.debug("get_historical_data %s: %s", symbol, e)
            return None

    def _search_scrip_safe(self, symbol: str, exchange: str) -> Optional[str]:
        if not self._ensure_connected():
            return None

        try:
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
