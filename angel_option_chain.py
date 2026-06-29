"""
angel_option_chain.py

Angel One broker-based option chain scanner.

Fetches live option contracts from the master contract file,
queries LTP and OI data per contract, and derives directional signals.

Fixes applied
-------------
1. detect_walls() used LTP instead of OI
   Original:
       call_wall = max(valid, key=lambda x: x["CE_LTP"])["strike"]
       put_wall  = max(valid, key=lambda x: x["PE_LTP"])["strike"]

   A "call wall" is the strike where the most CALL option WRITING has
   accumulated — i.e., the strike with maximum CE_OI. Writers sell calls
   at their expected resistance level, creating an OI wall.  Using LTP
   instead always picks the ATM strike (highest time value premium),
   regardless of where writers have positioned.

   Fix: use CE_OI for call wall, PE_OI for put wall, falling back to
   LTP-based ranking only when OI data is unavailable for all strikes.

2. classify_oi_behavior() received LTP as price_change
   Called as: self.classify_oi_behavior(item["CE_LTP"], item["CE_CHG_OI"])
   CE_LTP is always > 0 for options with any time value.
   So "if price_change > 0 and oi_change > 0" became "if oi_change > 0"
   for ALL strikes, always returning LONG_BUILDUP.  SHORT_BUILDUP was
   unreachable.  The analyze_oi_buildup() bias was systematically wrong.

   The underlying intent was to classify OI behavior using both a price
   direction signal and the OI change direction.  Without historical
   per-contract LTP, we use the spot vs ATM relationship as the price
   direction proxy:
   - strike < spot (ITM call / OTM put): underlying has moved toward it
   - strike > spot (OTM call / ITM put): underlying has moved away

   This is passed into classify_oi_behavior as a signed value:
   +1.0 if price has "moved toward" (favorable for this option)
   -1.0 if price has "moved away"

   The classification then behaves correctly:
   CE: strike < spot → underlying up → CE_CHG_OI > 0 = LONG_BUILDUP
   CE: strike < spot → underlying up → CE_CHG_OI < 0 = SHORT_COVERING
   PE: strike > spot → underlying down → PE_CHG_OI > 0 = LONG_BUILDUP
   etc.
"""

from __future__ import annotations



import logging
from datetime import datetime
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ── Lot sizes for all F&O underlyings ────────────────────────────────────────
# Updated after SEBI lot size revision (Jan 2025)
LOT_SIZES: dict = {
    # NSE Indices (NFO exchange)
    "NIFTY":  65,  # Updated Apr 2026
    "BANKNIFTY":  30,  # Updated Apr 2026   # reduced from 25 in Jan 2025
    "FINNIFTY":  65,  # Updated Apr 2026
    "MIDCPNIFTY":  120,  # Updated Apr 2026
    "NIFTYNEXT50": 25,
    # BSE Indices (BFO exchange)
    "SENSEX":  20,  # Updated Apr 2026
    "BANKEX":  15,  # Updated Apr 2026
    # Nifty 50 stocks (NFO) — typical lot sizes
    "RELIANCE":    250,  "HDFCBANK":   550,  "ICICIBANK":  700,
    "INFY":        300,  "TCS":        150,  "ITC":        1600,
    "LT":          150,  "SBIN":       750,  "AXISBANK":   625,
    "KOTAKBANK":   400,  "HCLTECH":    350,  "WIPRO":      1500,
    "BHARTIARTL":  475,  "BAJFINANCE": 125,  "TITAN":      375,
    "MARUTI":       75,  "SUNPHARMA":  700,  "ULTRACEMCO": 100,
    "TATAMOTORS":  900,  "ONGC":      1925,  "NTPC":      2975,
    "TATASTEEL":  1350,  "JSWSTEEL":   675,  "HINDALCO":  1400,
    "ADANIENT":    500,  "M&M":        700,  "BAJAJFINSV": 125,
    "BPCL":       1800,  "COALINDIA": 4200,  "DIVISLAB":   250,
    "DRREDDY":     125,  "CIPLA":      650,  "EICHERMOT":  150,
    "HEROMOTOCO":  150,  "POWERGRID": 2700,  "GRASIM":     375,
}

def get_lot_size(symbol: str) -> int:
    """Return lot size for a symbol. Defaults to 1 lot if unknown."""
    return LOT_SIZES.get(symbol.upper(), 500)  # 500 = safe default for stocks

class AngelOptionChainEngine:
    def __init__(
        self,
        broker,
        symbol:               str = "NIFTY",
        strike_step:          int = 50,
        strikes_each_side:    int = 1,
        master_contract_file: str = "MasterContract_NFO.csv",
    ) -> None:
        self.broker               = broker
        self.symbol               = symbol.upper()
        self.strike_step          = strike_step
        self.strikes_each_side    = strikes_each_side
        self.master_contract_file = master_contract_file
        self.master_df            = self._load_master_contract()

    def _load_master_contract(self) -> Optional[pd.DataFrame]:
        try:
            df = pd.read_csv(self.master_contract_file)
            logger.info(
                "Loaded option master contract from %s | rows=%d",
                self.master_contract_file, len(df),
            )
            return df
        except Exception as exc:
            logger.error("Failed to load master contract %s: %s", self.master_contract_file, exc)
            return None

    def _find_column(self, candidates):
        if self.master_df is None:
            return None
        cols = {str(c).strip().lower(): c for c in self.master_df.columns}
        for cand in candidates:
            key = cand.strip().lower()
            if key in cols:
                return cols[key]
        return None

    def _normalize_underlying_filter(self, s: pd.Series) -> pd.Series:
        values = s.astype(str).str.upper().str.strip()
        if self.symbol in ("NIFTY", "BANKNIFTY", "FINNIFTY"):
            return values.eq(self.symbol)
        return values.str.contains(self.symbol, na=False)

    def _parse_expiry_series(self, s: pd.Series) -> pd.Series:
        raw = s.astype(str).str.strip()
        for fmt in ("%d%b%Y", "%d-%b-%Y"):
            parsed = pd.to_datetime(raw, format=fmt, errors="coerce")
            if parsed.notna().sum() > 0:
                return parsed.dt.normalize()
        return pd.to_datetime(raw, errors="coerce").dt.normalize()

    def _normalize_strikes(self, strike_series: pd.Series) -> pd.Series:
        x = pd.to_numeric(strike_series, errors="coerce")
        if x.dropna().empty:
            return x
        median_val = float(x.dropna().median())
        if median_val > 10_000_000:
            return x / 10_000.0
        if median_val > 100_000:
            return x / 100.0
        return x

    def _derive_option_type_from_symbol(self, s: pd.Series) -> pd.Series:
        sym = s.astype(str).str.upper().str.strip()
        return sym.apply(lambda x: "CE" if x.endswith("CE") else ("PE" if x.endswith("PE") else ""))

    def _prepare_option_universe(self) -> Optional[pd.DataFrame]:
        if self.master_df is None or self.master_df.empty:
            return None

        df = self.master_df.copy()

        symbol_col  = self._find_column(["symbol", "tradingsymbol"])
        name_col    = self._find_column(["name", "underlying", "symbolname"])
        expiry_col  = self._find_column(["expiry", "expirydate"])
        strike_col  = self._find_column(["strike", "strikeprice"])
        opttype_col = self._find_column(["optiontype", "instrumenttype", "instrument_type"])
        exch_col    = self._find_column(["exch_seg", "exchange", "exch"])

        if not symbol_col or not expiry_col or not strike_col:
            logger.error("Master contract missing required columns. Available: %s", list(df.columns))
            return None

        if exch_col:
            df = df[df[exch_col].astype(str).str.upper().eq("NFO")]

        if name_col:
            df = df[self._normalize_underlying_filter(df[name_col])]
        else:
            df = df[df[symbol_col].astype(str).str.upper().str.contains(self.symbol, na=False)]

        logger.info("Rows after underlying filter for %s: %d", self.symbol, len(df))

        if opttype_col:
            raw = df[opttype_col].astype(str).str.upper()
            df  = df[raw.str.contains("OPT", na=False)].copy()
        else:
            df = df[df[symbol_col].astype(str).str.upper().str.contains("CE|PE", na=False)].copy()

        df["option_type"]      = self._derive_option_type_from_symbol(df[symbol_col])
        df                     = df[df["option_type"].isin(["CE", "PE"])].copy()
        df["expiry"]           = self._parse_expiry_series(df[expiry_col])
        df                     = df.dropna(subset=["expiry"]).copy()
        df["strike_normalized"] = self._normalize_strikes(df[strike_col])
        df                     = df.dropna(subset=["strike_normalized"]).copy()
        df["symbol_exact"]     = df[symbol_col].astype(str).str.strip()

        logger.info(
            "Prepared option universe | rows=%d | expiry_min=%s | expiry_max=%s"
            " | strike_min=%.2f | strike_max=%.2f",
            len(df), df["expiry"].min(), df["expiry"].max(),
            df["strike_normalized"].min() if not df.empty else -1,
            df["strike_normalized"].max() if not df.empty else -1,
        )
        return df

    def _get_spot_from_angel(self):
        # Re-enabled 2026-06-12: this was hard-disabled ("SmartAPI index LTP
        # unstable"), which killed the ENTIRE Angel option-chain fallback at
        # the spot step (yfinance fallback is itself dead). AngelOne.get_ltp
        # resolves indices via _get_index_token and has been reliable.
        try:
            if self.broker is not None and hasattr(self.broker, "get_ltp"):
                spot = self.broker.get_ltp(self.symbol, exchange="NSE")
                if spot and float(spot) > 0:
                    return float(spot)
        except Exception as exc:
            logger.warning("Angel spot fetch failed for %s: %s", self.symbol, exc)
        return None

    def _get_spot_from_yfinance(self):
        import os as _os_yfc
        if _os_yfc.getenv("DISABLE_YFINANCE", "false").lower() == "true":
            return None
        try:
            import yf_compat as yf  # yfinance replaced: Yahoo API broken
        except ImportError:
            logger.warning("yfinance not installed; cannot use spot fallback")
            return None

        yf_symbol_map = {
            "NIFTY":    "^NSEI",
            "BANKNIFTY": "^NSEBANK",
            "FINNIFTY": "NIFTY_FIN_SERVICE.NS",
        }
        yf_symbol = yf_symbol_map.get(self.symbol)
        if not yf_symbol:
            return None

        try:
            logger.info("Fetching spot from yfinance for %s (%s)", self.symbol, yf_symbol)
            data = yf.download(
                yf_symbol, period="1d", interval="1m",
                progress=False, auto_adjust=False, threads=False,
            )
            if data is None or data.empty:
                return None
            val = data["Close"].iloc[-1]
            if hasattr(val, "iloc"):
                val = val.iloc[0]
            return float(val)
        except Exception:
            logger.exception("yfinance spot fetch failed for %s", self.symbol)
            return None

    def get_spot_price(self):
        spot = self._get_spot_from_angel()
        if spot and spot > 0:
            return spot
        logger.warning("Angel spot unavailable; falling back to yfinance")
        return self._get_spot_from_yfinance()

    def get_candidate_expiries(self, option_df: pd.DataFrame):
        today = pd.Timestamp(datetime.now().date())
        return sorted([x for x in option_df["expiry"].dropna().unique() if x >= today])

    def get_option_chain_contracts(self):
        option_df = self._prepare_option_universe()
        if option_df is None or option_df.empty:
            return None

        spot = self.get_spot_price()
        if not spot:
            logger.warning("Could not fetch %s spot", self.symbol)
            return None

        atm        = round(float(spot) / self.strike_step) * self.strike_step
        min_strike = atm - self.strikes_each_side * self.strike_step
        max_strike = atm + self.strikes_each_side * self.strike_step

        logger.info("Spot=%.2f ATM=%d strike window=[%d, %d]", spot, atm, min_strike, max_strike)

        expiries = self.get_candidate_expiries(option_df)
        if not expiries:
            logger.warning("No future expiries found in master contract")
            return None

        for expiry in expiries:
            df_exp    = option_df[option_df["expiry"] == expiry].copy()
            df_window = df_exp[
                (df_exp["strike_normalized"] >= min_strike)
                & (df_exp["strike_normalized"] <= max_strike)
            ].copy()

            if not df_window.empty:
                logger.info(
                    "Selected expiry %s | strikes=%s",
                    expiry, sorted(df_window["strike_normalized"].unique().tolist()),
                )
                return {"spot": float(spot), "atm": int(atm), "expiry": expiry, "contracts": df_window}

        logger.warning("No CE/PE contracts found around ATM for any future expiry")
        return None

    @staticmethod
    def _safe_float(value, default: float = 0.0) -> float:
        try:
            return default if (value is None or value == "") else float(value)
        except Exception:
            return default

    def _extract_oi_fields_from_quote(self, quote: dict) -> tuple[float, float]:
        if not isinstance(quote, dict):
            return 0.0, 0.0

        # "opnInterest" is Angel SmartAPI's actual FULL-quote field name
        oi_keys = ["opnInterest", "open_interest", "openInterest", "oi",
                   "OpenInterest"]
        chg_keys = ["changeinopeninterest", "changeinOpenInterest", "change_in_oi",
                    "change_oi", "chg_oi", "ChangeInOpenInterest"]

        oi = chg_oi = 0.0
        for k in oi_keys:
            if k in quote:
                oi = self._safe_float(quote[k])
                break
        for k in chg_keys:
            if k in quote:
                chg_oi = self._safe_float(quote[k])
                break
        return oi, chg_oi

    def _extract_liquidity_fields_from_quote(self, quote: dict) -> dict:
        """Extract volume and top-of-book from one SmartAPI FULL quote."""
        if not isinstance(quote, dict):
            return {"volume": 0.0, "bid": 0.0, "ask": 0.0,
                    "bid_qty": 0.0, "ask_qty": 0.0}

        def first(keys):
            for key in keys:
                value = self._safe_float(quote.get(key))
                if value > 0:
                    return value
            return 0.0

        def top(side_keys):
            depth = None
            for key in side_keys:
                candidate = quote.get(key)
                if candidate:
                    depth = candidate
                    break
            if not depth and isinstance(quote.get("depth"), dict):
                depth = quote["depth"].get("buy" if "Buy" in side_keys[0] else "sell")
            item = depth[0] if isinstance(depth, list) and depth else {}
            return (
                self._safe_float(item.get("price") if isinstance(item, dict) else 0),
                self._safe_float(
                    (item.get("quantity", item.get("qty", 0))) if isinstance(item, dict) else 0
                ),
            )

        bid, bid_qty = top(["bestFiveBuy", "best5Buy"])
        ask, ask_qty = top(["bestFiveSell", "best5Sell"])
        bid = bid or first(["bestBidPrice", "bidPrice", "bidprice"])
        ask = ask or first(["bestAskPrice", "askPrice", "askprice"])
        bid_qty = bid_qty or first(["bestBidQty", "bidQty", "bidQuantity"])
        ask_qty = ask_qty or first(["bestAskQty", "askQty", "askQuantity"])
        return {
            "volume": first(["tradeVolume", "totalTradedVolume", "volume", "totTradedQty"]),
            "bid": bid,
            "ask": ask,
            "bid_qty": bid_qty,
            "ask_qty": ask_qty,
        }

    def _get_quote_data(self, symbol: str) -> dict:
        try:
            if hasattr(self.broker, "get_quote"):
                quote = self.broker.get_quote(symbol, exchange="NFO")
                if isinstance(quote, dict):
                    return quote
        except Exception:
            logger.debug("get_quote failed for %s", symbol, exc_info=True)
        # AngelOne has no get_quote — use SmartAPI FULL market data directly
        # (this path was dead before 2026-06-12: OI always came back 0)
        try:
            tok = self.broker.get_token(symbol, "NFO")
            obj = getattr(self.broker, "obj", None)
            if tok and obj is not None:
                resp = obj.getMarketData("FULL", {"NFO": [tok]})
                fetched = ((resp or {}).get("data") or {}).get("fetched") or []
                if fetched and isinstance(fetched[0], dict):
                    return fetched[0]
        except Exception:
            logger.debug("getMarketData FULL failed for %s", symbol, exc_info=True)
        return {}

    def scan_option_chain(self):
        payload = self.get_option_chain_contracts()
        if not payload:
            return None

        spot   = payload["spot"]
        atm    = payload["atm"]
        expiry = payload["expiry"]
        df     = payload["contracts"]

        strikes = sorted(df["strike_normalized"].unique())
        chain   = []

        for strike in strikes:
            ce_row = df[(df["strike_normalized"] == strike) & (df["option_type"] == "CE")]
            pe_row = df[(df["strike_normalized"] == strike) & (df["option_type"] == "PE")]

            ce_symbol = ce_row.iloc[0]["symbol_exact"] if not ce_row.empty else None
            pe_symbol = pe_row.iloc[0]["symbol_exact"] if not pe_row.empty else None

            ce_ltp = pe_ltp = ce_oi = pe_oi = ce_chg_oi = pe_chg_oi = 0.0
            ce_liq = self._extract_liquidity_fields_from_quote({})
            pe_liq = self._extract_liquidity_fields_from_quote({})

            if ce_symbol:
                try:
                    ce_ltp = float(self.broker.get_ltp(ce_symbol, exchange="NFO") or 0.0)
                except Exception:
                    logger.exception("CE LTP failed for %s", ce_symbol)
                try:
                    ce_quote = self._get_quote_data(ce_symbol)
                    ce_oi, ce_chg_oi = self._extract_oi_fields_from_quote(ce_quote)
                    ce_liq = self._extract_liquidity_fields_from_quote(ce_quote)
                except Exception:
                    logger.debug("CE OI extraction failed for %s", ce_symbol, exc_info=True)

            if pe_symbol:
                try:
                    pe_ltp = float(self.broker.get_ltp(pe_symbol, exchange="NFO") or 0.0)
                except Exception:
                    logger.exception("PE LTP failed for %s", pe_symbol)
                try:
                    pe_quote = self._get_quote_data(pe_symbol)
                    pe_oi, pe_chg_oi = self._extract_oi_fields_from_quote(pe_quote)
                    pe_liq = self._extract_liquidity_fields_from_quote(pe_quote)
                except Exception:
                    logger.debug("PE OI extraction failed for %s", pe_symbol, exc_info=True)

            logger.info(
                "Strike=%s CE=%s CE_LTP=%s CE_OI=%s CE_CHG_OI=%s "
                "PE=%s PE_LTP=%s PE_OI=%s PE_CHG_OI=%s",
                strike, ce_symbol, ce_ltp, ce_oi, ce_chg_oi,
                pe_symbol, pe_ltp, pe_oi, pe_chg_oi,
            )

            chain.append({
                "strike":     float(strike),
                "CE_LTP":     ce_ltp,
                "PE_LTP":     pe_ltp,
                "CE_symbol":  ce_symbol,
                "PE_symbol":  pe_symbol,
                "CE_OI":      ce_oi,
                "PE_OI":      pe_oi,
                "CE_CHG_OI":  ce_chg_oi,
                "PE_CHG_OI":  pe_chg_oi,
                "CE_VOLUME":  ce_liq["volume"],
                "PE_VOLUME":  pe_liq["volume"],
                "CE_bidPrice": ce_liq["bid"],
                "PE_bidPrice": pe_liq["bid"],
                "CE_bidQty": ce_liq["bid_qty"],
                "PE_bidQty": pe_liq["bid_qty"],
                "CE_askPrice": ce_liq["ask"],
                "PE_askPrice": pe_liq["ask"],
                "CE_askQty": ce_liq["ask_qty"],
                "PE_askQty": pe_liq["ask_qty"],
            })

        return spot, atm, expiry, chain

    # ------------------------------------------------------------------
    # Wall detection — uses OI, not LTP
    # ------------------------------------------------------------------
    def detect_walls(self, chain):
        """
        Call wall: strike with maximum CE OI (writers have positioned here as resistance).
        Put wall:  strike with maximum PE OI (writers have positioned here as support).

        Falls back to LTP-based ranking if OI data is zero for all strikes
        (e.g. when the broker's quote API doesn't return OI).
        """
        valid = [x for x in chain if x["CE_LTP"] > 0 or x["PE_LTP"] > 0]
        if not valid:
            return None, None

        has_ce_oi = any(x["CE_OI"] > 0 for x in valid)
        has_pe_oi = any(x["PE_OI"] > 0 for x in valid)

        # Prefer OI-based walls; fall back to LTP if OI unavailable
        call_wall = (
            max(valid, key=lambda x: x["CE_OI"])["strike"] if has_ce_oi
            else max(valid, key=lambda x: x["CE_LTP"])["strike"]
        )
        put_wall = (
            max(valid, key=lambda x: x["PE_OI"])["strike"] if has_pe_oi
            else max(valid, key=lambda x: x["PE_LTP"])["strike"]
        )
        return call_wall, put_wall

    def gamma_zones(self, chain, atm):
        import math
        gamma_support = gamma_resistance = None
        for item in chain:
            strike   = item["strike"]
            distance = abs(strike - atm)
            gamma    = math.exp(-(distance / 100.0))
            if strike < atm:
                if gamma_support is None or gamma > gamma_support[1]:
                    gamma_support = (strike, gamma)
            if strike > atm:
                if gamma_resistance is None or gamma > gamma_resistance[1]:
                    gamma_resistance = (strike, gamma)
        return (gamma_support[0] if gamma_support else None,
                gamma_resistance[0] if gamma_resistance else None)

    def estimate_pcr(self, chain):
        total_call = sum(x["CE_OI"] for x in chain)
        total_put  = sum(x["PE_OI"] for x in chain)
        if total_call > 0:
            return float(total_put / total_call)
        total_call_ltp = sum(x["CE_LTP"] for x in chain)
        total_put_ltp  = sum(x["PE_LTP"] for x in chain)
        if total_call_ltp <= 0:
            return 1.0
        return float(total_put_ltp / total_call_ltp)

    def estimate_pcr_change_oi(self, chain):
        total_call = sum(x["CE_CHG_OI"] for x in chain)
        total_put  = sum(x["PE_CHG_OI"] for x in chain)
        if abs(total_call) <= 0:
            return 1.0
        return float(total_put / total_call)

    # ------------------------------------------------------------------
    # OI behavior classification — uses OI direction, not raw LTP
    # ------------------------------------------------------------------
    def classify_oi_behavior(
        self,
        price_direction: float,
        oi_change: float,
    ) -> str:
        """
        Classify option OI behavior.

        Parameters
        ----------
        price_direction : float
            +1.0 if the underlying has moved "toward" this option
            (e.g. spot moved up for a CE, or down for a PE).
            -1.0 if the underlying has moved "away".
            Pass 0.0 when direction is unknown.
        oi_change : float
            Change in open interest for this contract.

        Classifications
        ---------------
        LONG_BUILDUP     : price up + OI up  (new longs entering)
        SHORT_BUILDUP    : price down + OI up (new shorts entering)
        SHORT_COVERING   : price up + OI down (shorts exiting)
        LONG_UNWINDING   : price down + OI down (longs exiting)
        OI_BUILDUP       : OI up, direction unknown
        OI_UNWIND        : OI down, direction unknown
        NEUTRAL          : no significant change
        """
        if price_direction > 0 and oi_change > 0:
            return "LONG_BUILDUP"
        if price_direction < 0 and oi_change > 0:
            return "SHORT_BUILDUP"
        if price_direction > 0 and oi_change < 0:
            return "SHORT_COVERING"
        if price_direction < 0 and oi_change < 0:
            return "LONG_UNWINDING"
        if oi_change > 0:
            return "OI_BUILDUP"
        if oi_change < 0:
            return "OI_UNWIND"
        return "NEUTRAL"

    def analyze_oi_buildup(self, chain, spot: Optional[float] = None) -> str:
        """
        Derive a BULLISH / BEARISH / NEUTRAL bias from OI behavior.

        Uses spot vs strike relationship as the price-direction proxy
        (required since historical per-contract LTP is not available):
        - CE at strike < spot → underlying has moved toward it (price_direction = +1)
        - PE at strike > spot → underlying has moved toward it (price_direction = +1)
        - CE at strike > spot → underlying has moved away (price_direction = -1)
        - PE at strike < spot → underlying has moved away (price_direction = -1)
        """
        bullish = bearish = 0

        for item in chain:
            strike = float(item["strike"])
            ce_pd  = +1.0 if (spot is not None and strike <= spot) else -1.0
            pe_pd  = +1.0 if (spot is not None and strike >= spot) else -1.0

            call_behavior = self.classify_oi_behavior(ce_pd, item["CE_CHG_OI"])
            put_behavior  = self.classify_oi_behavior(pe_pd, item["PE_CHG_OI"])

            # Put-side buildup / call-side covering = bullish
            if put_behavior in ("LONG_BUILDUP", "SHORT_COVERING", "OI_BUILDUP"):
                bullish += 1
            if call_behavior in ("LONG_BUILDUP", "SHORT_COVERING", "OI_BUILDUP"):
                bearish += 1

        if bullish > bearish:
            return "BULLISH"
        if bearish > bullish:
            return "BEARISH"
        return "NEUTRAL"

    def _compute_directional_score(
        self,
        signal: Optional[str],
        oi_signal: str,
        pcr: float,
        pcr_change_oi: float,
        spot: float,
        call_wall,
        put_wall,
        gamma_support,
        gamma_resistance,
    ) -> tuple[float, str]:
        score   = 0.0
        reasons = []

        if signal == "BUY_CALL":
            if oi_signal == "BULLISH":
                score += 2.0; reasons.append("oi bullish")
            if pcr > 1.0:
                score += 1.0; reasons.append("pcr supportive")
            if pcr_change_oi > 1.0:
                score += 1.0; reasons.append("change_oi supportive")
            if gamma_support is not None and spot > gamma_support:
                score += 0.5; reasons.append("above gamma support")
            if put_wall is not None and spot > put_wall:
                score += 0.5; reasons.append("above put wall")

        elif signal == "BUY_PUT":
            if oi_signal == "BEARISH":
                score += 2.0; reasons.append("oi bearish")
            if pcr < 1.0:
                score += 1.0; reasons.append("pcr supportive")
            if pcr_change_oi < 1.0:
                score += 1.0; reasons.append("change_oi supportive")
            if gamma_resistance is not None and spot < gamma_resistance:
                score += 0.5; reasons.append("below gamma resistance")
            if call_wall is not None and spot < call_wall:
                score += 0.5; reasons.append("below call wall")

        return score, " | ".join(reasons)

    def generate_signal(self):
        data = self.scan_option_chain()
        if not data:
            return None

        spot, atm, expiry, chain = data

        valid_quotes = [x for x in chain if x["CE_LTP"] > 0 or x["PE_LTP"] > 0]
        if not valid_quotes:
            logger.warning("No valid option quotes found from Angel")
            return None

        call_wall, put_wall     = self.detect_walls(chain)
        gamma_support, gamma_resistance = self.gamma_zones(chain, atm)
        pcr           = self.estimate_pcr(chain)
        pcr_change_oi = self.estimate_pcr_change_oi(chain)
        oi_signal     = self.analyze_oi_buildup(chain, spot=spot)

        signal = confidence = None
        confidence = 0.5

        if oi_signal == "BULLISH" and pcr > 1.0 and gamma_support is not None and spot > gamma_support:
            signal     = "BUY_CALL"
            confidence += 0.25
        elif oi_signal == "BEARISH" and pcr < 1.0 and gamma_resistance is not None and spot < gamma_resistance:
            signal     = "BUY_PUT"
            confidence += 0.25

        if signal == "BUY_CALL" and put_wall is not None and spot > put_wall:
            confidence += 0.10
        if signal == "BUY_PUT"  and call_wall is not None and spot < call_wall:
            confidence += 0.10

        directional_score, score_reason = self._compute_directional_score(
            signal=signal, oi_signal=oi_signal,
            pcr=pcr, pcr_change_oi=pcr_change_oi,
            spot=spot, call_wall=call_wall, put_wall=put_wall,
            gamma_support=gamma_support, gamma_resistance=gamma_resistance,
        )

        regime = "SIDEWAYS"
        if signal == "BUY_CALL" and directional_score >= 2.5:
            regime = "BULLISH_TREND"
        elif signal == "BUY_PUT" and directional_score >= 2.5:
            regime = "BEARISH_TREND"
        elif signal is not None:
            regime = "UNCLEAR"

        confidence = min(round(confidence + min(directional_score, 4.0) * 0.05, 2), 0.95)

        return {
            "signal":           signal,
            "confidence":       round(confidence, 2),
            "spot":             float(spot),
            "atm":              int(atm),
            "atm_strike":       int(atm),
            "expiry":           str(expiry),
            "pcr":              round(pcr, 2),
            "pcr_oi":           round(pcr, 2),
            "pcr_change_oi":    round(pcr_change_oi, 2),
            "call_wall":        call_wall,
            "put_wall":         put_wall,
            "gamma_support":    gamma_support,
            "gamma_resistance": gamma_resistance,
            "oi_buildup_signal": oi_signal,
            "regime":           regime,
            "directional_score": round(directional_score, 2),
            "signal_strength":  round(directional_score, 2),
            "reason":           score_reason or "option-chain signal",
            "flow": {
                "call_score": round(max(0.0, 1.0 - pcr), 2),
                "put_score":  round(max(0.0, pcr), 2),
                "imbalance":  round(pcr - 1.0, 2),
            },
            "delta_imbalance": {
                "direction":      oi_signal,
                "intensity":      round(min(abs(directional_score) / 4.0, 1.0), 2),
                "call_aggression": round(sum(x["CE_CHG_OI"] for x in chain), 2),
                "put_aggression":  round(sum(x["PE_CHG_OI"] for x in chain), 2),
                "imbalance_ratio": round(pcr_change_oi, 2),
            },
            "totals": {
                "regime":             regime,
                "directional_score":  round(directional_score, 2),
                "signal_strength":    round(directional_score, 2),
                "oi_buildup_signal":  oi_signal,
                "total_ce_oi":        round(sum(x["CE_OI"]     for x in chain), 2),
                "total_pe_oi":        round(sum(x["PE_OI"]     for x in chain), 2),
                "total_ce_change_oi": round(sum(x["CE_CHG_OI"] for x in chain), 2),
                "total_pe_change_oi": round(sum(x["PE_CHG_OI"] for x in chain), 2),
            },
            "walls": {
                "call_wall":        call_wall,
                "put_wall":         put_wall,
                "gamma_support":    gamma_support,
                "gamma_resistance": gamma_resistance,
            },
            "chain": chain,
        }


def compute_pcr_and_maxpain(option_chain_data: dict) -> dict:
    """
    Compute Put-Call Ratio and Max Pain from NSE option chain.
    
    PCR (Put-Call Ratio):
      < 0.7  = excessive bullishness = contrarian SELL signal
      0.7-1.0 = neutral
      1.0-1.3 = slight bearish bias
      > 1.3  = excessive fear = contrarian BUY signal
    
    Max Pain: strike where option sellers lose the least = 
    market tends to gravitate toward this on expiry day.
    """
    result = {
        "pcr": 1.0, "pcr_signal": "NEUTRAL",
        "max_pain": 0, "total_call_oi": 0, "total_put_oi": 0,
        "iv_surface": {},
    }
    try:
        data = option_chain_data.get("filtered", {}).get("data", [])
        if not data:
            data = option_chain_data.get("data", [])
        if not data:
            return result

        total_call_oi = 0
        total_put_oi  = 0
        pain_by_strike = {}
        iv_surface     = {}

        for row in data:
            strike = int(row.get("strikePrice", 0))
            if strike <= 0:
                continue
            ce = row.get("CE", {}) or {}
            pe = row.get("PE", {}) or {}
            
            c_oi = int(ce.get("openInterest", 0) or 0)
            p_oi = int(pe.get("openInterest", 0) or 0)
            c_iv = float(ce.get("impliedVolatility", 0) or 0)
            p_iv = float(pe.get("impliedVolatility", 0) or 0)

            total_call_oi += c_oi
            total_put_oi  += p_oi

            # Max pain: at expiry, CE writers profit if price < strike
            #           PE writers profit if price > strike
            # Pain at each strike = sum of OI × (strike distance from that price)
            pain_by_strike[strike] = {"c_oi": c_oi, "p_oi": p_oi}
            
            if c_iv > 0: iv_surface[strike] = {"call_iv": round(c_iv, 2)}
            if p_iv > 0: iv_surface.setdefault(strike, {})["put_iv"] = round(p_iv, 2)

        # PCR
        pcr = round(total_put_oi / max(total_call_oi, 1), 3)
        if pcr < 0.70:
            pcr_signal = "EXTREME_BULLISH"  # contrarian SELL
        elif pcr < 0.85:
            pcr_signal = "BULLISH"
        elif pcr < 1.10:
            pcr_signal = "NEUTRAL"
        elif pcr < 1.30:
            pcr_signal = "BEARISH"
        else:
            pcr_signal = "EXTREME_BEARISH"  # contrarian BUY

        # Max pain calculation
        strikes = sorted(pain_by_strike.keys())
        max_pain_strike = 0
        min_pain        = float("inf")
        for test_strike in strikes:
            total_pain = 0
            for s, d in pain_by_strike.items():
                # If spot expires at test_strike:
                # Call writers lose money on calls with strike < test_strike
                if s < test_strike:
                    total_pain += d["c_oi"] * (test_strike - s)
                # Put writers lose money on puts with strike > test_strike
                elif s > test_strike:
                    total_pain += d["p_oi"] * (s - test_strike)
            if total_pain < min_pain:
                min_pain        = total_pain
                max_pain_strike = test_strike

        result.update({
            "pcr":           pcr,
            "pcr_signal":    pcr_signal,
            "max_pain":      max_pain_strike,
            "total_call_oi": total_call_oi,
            "total_put_oi":  total_put_oi,
            "iv_surface":    iv_surface,
        })

    except Exception as e:
        import logging
        logging.getLogger(__name__).debug("PCR calc error: %s", e)

    return result
