from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests

from option_chain_intelligence import OptionChainIntelligence

logger = logging.getLogger(__name__)


@dataclass
class OptionChainFetchResult:
    underlying: str
    spot: float
    expiry: str
    atm_strike: float
    raw_json: Dict[str, Any]
    dataframe: pd.DataFrame
    signal: Optional[Dict[str, Any]]
    summary: Optional[Dict[str, Any]]


class NSEOptionChainFetcher:
    BASE_URL = "https://www.nseindia.com"
    OPTION_CHAIN_PAGE = "https://www.nseindia.com/option-chain"
    API_URL = "https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"

    def __init__(
        self,
        underlying: str = "NIFTY",
        timeout: int = 15,
        max_retries: int = 3,
        strike_count_each_side: int = 8,
        include_gamma_approx: bool = True,
        cache_file: str = "option_chain_cache.json",
        use_cache_fallback: bool = True,
    ):
        self.underlying = underlying.upper()
        self.timeout = timeout
        self.max_retries = max_retries
        self.strike_count_each_side = strike_count_each_side
        self.include_gamma_approx = include_gamma_approx
        self.cache_file = cache_file
        self.use_cache_fallback = use_cache_fallback

        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/123.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "Referer": self.OPTION_CHAIN_PAGE,
                "Origin": self.BASE_URL,
                "Connection": "keep-alive",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            }
        )

    @staticmethod
    def _market_open() -> bool:
        from datetime import datetime as _dt, time as _dtime
        if _dt.now().weekday() >= 5: return False
        n = _dt.now().time()
        return _dtime(8,45) <= n <= _dtime(16,30)

    def fetch(self, expiry: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Fetch option chain: NSE → Sensibull → Angel."""
        # Try resilience module first (NSE with retry)
        try:
            from data_source_resilience import fetch_option_chain
            _data = fetch_option_chain(self.symbol)
            if _data:
                return _data
        except Exception: pass
        # Sensibull fallback (when NSE IP is blocked)
        try:
            from sensibull_client import fetch_option_chain as _sb_oc
            _sb_data = _sb_oc(self.symbol)
            if _sb_data:
                return _sb_data
        except Exception: pass
        raw = self._fetch_live()
        if raw:
            self._save_cache(raw)
            if expiry:
                raw = self._filter_by_expiry(raw, expiry)
            return raw

        if self.use_cache_fallback:
            logger.warning("Live NSE option-chain unavailable. Trying cache fallback.")
            cached = self._load_cache()
            if cached:
                if expiry:
                    cached = self._filter_by_expiry(cached, expiry)
                return cached

        return None

    def _fetch_live(self) -> Optional[Dict[str, Any]]:
        for attempt in range(1, self.max_retries + 1):
            try:
                self.session.get(self.BASE_URL, timeout=self.timeout)
                self.session.get(self.OPTION_CHAIN_PAGE, timeout=self.timeout)

                url = self.API_URL.format(symbol=self.underlying)
                resp = self.session.get(url, timeout=self.timeout)

                logger.info(
                    "NSE fetch attempt=%d status=%s content-type=%s",
                    attempt,
                    resp.status_code,
                    resp.headers.get("Content-Type"),
                )

                resp.raise_for_status()

                try:
                    data = resp.json()
                except Exception:
                    logger.warning("JSON parse failed")
                    data = None

                if not data:
                    logger.warning("NSE returned empty payload: %s", resp.text[:200])
                    time.sleep(attempt)
                    continue

                if "records" in data and isinstance(data["records"], dict):
                    return data

                logger.warning("Unexpected NSE payload keys: %s", list(data.keys())[:20])
                time.sleep(attempt)

            except Exception as e:
                logger.warning("Live option-chain fetch failed attempt=%d error=%s", attempt, e)
                time.sleep(attempt)

        return None

    def fetch_and_analyze(self, expiry: Optional[str] = None) -> Optional[OptionChainFetchResult]:
        raw = self.fetch(expiry=expiry)
        if not raw:
            return None

        if expiry is None:
            expiry = self._detect_expiry_from_raw(raw)

        spot = self._extract_spot_from_raw(raw)
        df = self.build_dataframe(raw, expiry=expiry, spot_price=spot)

        if df.empty:
            return None

        if not spot or spot <= 0:
            spot = self._infer_spot_from_df(df)

        atm = min(df["strikePrice"], key=lambda x: abs(float(x) - float(spot)))

        intelligence = OptionChainIntelligence(
            underlying=self.underlying,
            strike_window=self.strike_count_each_side,
        )

        summary_obj = intelligence.analyze(df, spot_price=float(spot))
        signal = intelligence.build_trade_signal(summary_obj)

        summary = {
            "underlying": summary_obj.underlying,
            "spot": summary_obj.spot,
            "atm_strike": summary_obj.atm_strike,
            "pcr_oi": summary_obj.pcr_oi,
            "pcr_change_oi": summary_obj.pcr_change_oi,
            "pcr_volume": summary_obj.pcr_volume,
            "bullish_score": summary_obj.bullish_score,
            "bearish_score": summary_obj.bearish_score,
            "net_bias": summary_obj.net_bias,
            "oi_buildup_signal": summary_obj.oi_buildup_signal,
            "call_wall": summary_obj.call_wall,
            "put_wall": summary_obj.put_wall,
            "gamma_support": summary_obj.gamma_support,
            "gamma_resistance": summary_obj.gamma_resistance,
        }

        return OptionChainFetchResult(
            underlying=self.underlying,
            spot=float(spot),
            expiry=str(expiry),
            atm_strike=float(atm),
            raw_json=raw,
            dataframe=df,
            signal=signal,
            summary=summary,
        )

    def build_dataframe(
        self,
        raw_data: Dict[str, Any],
        expiry: Optional[str] = None,
        spot_price: Optional[float] = None,
    ) -> pd.DataFrame:
        records = raw_data.get("records", {}) or {}
        data_rows = records.get("data", []) or []

        if expiry is None:
            expiry = self._detect_expiry_from_raw(raw_data)

        if spot_price is None:
            spot_price = self._extract_spot_from_raw(raw_data)

        rows: List[Dict[str, Any]] = []

        for item in data_rows:
            if expiry and item.get("expiryDate") != expiry:
                continue

            ce = item.get("CE", {}) or {}
            pe = item.get("PE", {}) or {}

            rows.append(
                {
                    "expiryDate": item.get("expiryDate"),
                    "strikePrice": self._to_float(item.get("strikePrice")),
                    "CE_openInterest": self._to_float(ce.get("openInterest")),
                    "PE_openInterest": self._to_float(pe.get("openInterest")),
                    "CE_changeinOpenInterest": self._to_float(ce.get("changeinOpenInterest")),
                    "PE_changeinOpenInterest": self._to_float(pe.get("changeinOpenInterest")),
                    "CE_totalTradedVolume": self._to_float(ce.get("totalTradedVolume")),
                    "PE_totalTradedVolume": self._to_float(pe.get("totalTradedVolume")),
                    "CE_lastPrice": self._to_float(ce.get("lastPrice")),
                    "PE_lastPrice": self._to_float(pe.get("lastPrice")),
                    "CE_impliedVolatility": self._to_float(ce.get("impliedVolatility")),
                    "PE_impliedVolatility": self._to_float(pe.get("impliedVolatility")),
                }
            )

        df = pd.DataFrame(rows)
        if df.empty:
            return df

        df = df.sort_values("strikePrice").reset_index(drop=True)

        if not spot_price or spot_price <= 0:
            spot_price = self._infer_spot_from_df(df)

        if self.include_gamma_approx and spot_price > 0:
            df["CE_gamma"] = df.apply(
                lambda r: self._approx_gamma(
                    spot=spot_price,
                    strike=r["strikePrice"],
                    iv=max(r["CE_impliedVolatility"], 1.0) / 100.0,
                    premium=max(r["CE_lastPrice"], 0.5),
                ),
                axis=1,
            )
            df["PE_gamma"] = df.apply(
                lambda r: self._approx_gamma(
                    spot=spot_price,
                    strike=r["strikePrice"],
                    iv=max(r["PE_impliedVolatility"], 1.0) / 100.0,
                    premium=max(r["PE_lastPrice"], 0.5),
                ),
                axis=1,
            )
        else:
            df["CE_gamma"] = 0.0
            df["PE_gamma"] = 0.0

        return df

    def _save_cache(self, data: Dict[str, Any]) -> None:
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception as e:
            logger.warning("Could not save option-chain cache: %s", e)

    def _load_cache(self) -> Optional[Dict[str, Any]]:
        if not os.path.exists(self.cache_file):
            logger.warning("Cache file not found: %s", self.cache_file)
            return None

        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data and "records" in data:
                logger.info("Loaded option-chain data from cache")
                return data
        except Exception as e:
            logger.warning("Could not load option-chain cache: %s", e)

        return None

    def _filter_by_expiry(self, raw_data: Dict[str, Any], expiry: str) -> Dict[str, Any]:
        copied = {
            "records": {**(raw_data.get("records", {}) or {})},
            "filtered": raw_data.get("filtered", {}),
        }
        all_rows = raw_data.get("records", {}).get("data", []) or []
        copied["records"]["data"] = [r for r in all_rows if r.get("expiryDate") == expiry]
        copied["records"]["expiryDates"] = [expiry]
        return copied

    def _detect_expiry_from_raw(self, raw_data: Dict[str, Any]) -> Optional[str]:
        expiries = raw_data.get("records", {}).get("expiryDates", []) or []
        if not expiries:
            return None

        today = datetime.now().date()
        parsed = []
        for exp in expiries:
            try:
                parsed.append((datetime.strptime(exp, "%d-%b-%Y").date(), exp))
            except Exception:
                continue

        parsed.sort(key=lambda x: x[0])
        for d, exp in parsed:
            if d >= today:
                return exp

        return parsed[0][1] if parsed else expiries[0]

    def _extract_spot_from_raw(self, raw_data: Dict[str, Any]) -> float:
        records = raw_data.get("records", {}) or {}
        val = records.get("underlyingValue")
        if isinstance(val, (int, float)):
            return float(val)
        return 0.0

    def _infer_spot_from_df(self, df: pd.DataFrame) -> float:
        if df.empty:
            return 0.0
        tmp = df.copy()
        tmp["combined_oi"] = tmp["CE_openInterest"] + tmp["PE_openInterest"]
        idx = tmp["combined_oi"].idxmax()
        return float(tmp.loc[idx, "strikePrice"])

    @staticmethod
    def _to_float(value: Any) -> float:
        try:
            if value is None or value == "-":
                return 0.0
            return float(value)
        except Exception:
            return 0.0

    @staticmethod
    def _approx_gamma(spot: float, strike: float, iv: float, premium: float) -> float:
        if spot <= 0 or strike <= 0 or iv <= 0:
            return 0.0
        moneyness = abs(spot - strike) / max(spot, 1e-6)
        vol_factor = max(iv, 0.05)
        premium_factor = max(premium, 0.5)
        gamma = math.exp(-(moneyness * 18.0)) / (vol_factor * premium_factor)
        return float(max(gamma, 0.0))
