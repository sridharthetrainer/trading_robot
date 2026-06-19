"""
advisory_engine.data_core — LAYER 2
===================================
Multi-timeframe data synchronisation + indicator computation.

Broker interface: the existing AngelOne client (angel.py) is the PRIMARY data
source, matching the project rule "Angel getCandleData is PRIMARY for intraday
candles". Any object exposing the same surface works (the self-test injects a
synthetic provider), so this layer never hard-imports broker credentials:

    get_historical_data(symbol, interval, from_date, to_date, exchange) -> DataFrame
    get_ltp(symbol, exchange) -> float | None

Quality guarantees applied to every frame before validation:
  - strictly sorted DatetimeIndex, duplicates dropped
  - the still-forming (incomplete) last bar is dropped — signals only ever
    evaluate CLOSED candles
  - minimum-bar guards per indicator; NaN tails are surfaced, not hidden
"""

from __future__ import annotations

import difflib
import logging
import re
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Callable

import numpy as np
import pandas as pd

try:
    import pandas_ta as ta
    _HAS_TA = True
except ImportError:                                     # pragma: no cover
    _HAS_TA = False

from .models import AdvisorySignal, MarketSnapshot

logger = logging.getLogger("advisory.data")

_DT_FMT = "%Y-%m-%d %H:%M"

# Curated aliases for names fuzzy matching alone gets wrong. Extend freely.
SYMBOL_ALIASES: Dict[str, str] = {
    "MOTHERSON": "MOTHERSON",
    "SAMVARDHANA MOTHERSON": "MOTHERSON",
    "SAMVARDHANA MOTHERSON INT": "MOTHERSON",
    "SAMVARDHANA MOTHERSON INTERNATIONAL": "MOTHERSON",
    "HDFC LIFE": "HDFCLIFE",
    "HDFC LIFE INS COMPANY": "HDFCLIFE",
    "HDFC LIFE INSURANCE": "HDFCLIFE",
    "VISHAL MEGA MART": "VISHALHSG",
    "M&M": "M&M",
    "MAHINDRA AND MAHINDRA": "M&M",
    "BAJAJ FIN": "BAJFINANCE",
    "BAJAJ FINANCE": "BAJFINANCE",
    "NIFTY": "NIFTY",
    "BANK NIFTY": "BANKNIFTY",
    "BANKNIFTY": "BANKNIFTY",
}

_CORP_SUFFIXES = re.compile(
    r"\b(LTD|LTD\.|LIMITED|INS|INS\.|INSURANCE|COMPANY|CO|CO\.|CORP|CORPORATION|"
    r"INT|INT\.|INTERNATIONAL|INDIA|INDS|INDUSTRIES|SERVICES|ENTERPRISES)\b",
    re.IGNORECASE)


def _normalise_name(name: str) -> str:
    s = _CORP_SUFFIXES.sub(" ", name.upper())
    s = re.sub(r"[^\w&\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


class TokenResolver:
    """
    Fuzzy textual-name -> official NSE trading symbol/token.
    Backed by the master-contract map already maintained by angel.py
    (_load_nse_eq_tokens: 'RELIANCE' / company name -> token).
    """

    def __init__(self, token_map: Optional[Dict[str, str]] = None):
        if token_map is None:
            try:
                from angel import _load_nse_eq_tokens
                token_map = _load_nse_eq_tokens()
            except Exception as e:                      # offline / tests
                logger.warning("TokenResolver: master contract unavailable (%s)", e)
                token_map = {}
        self._map = token_map
        self._keys = list(token_map.keys())

    def resolve(self, raw_name: str) -> tuple[Optional[str], Optional[str], float]:
        """Returns (symbol, token, confidence 0..1)."""
        if not raw_name:
            return None, None, 0.0
        norm = _normalise_name(raw_name)

        alias = SYMBOL_ALIASES.get(norm) or SYMBOL_ALIASES.get(raw_name.upper().strip())
        if alias:
            tok = self._map.get(alias)
            logger.info("L2 token: %r -> %s via alias table (token=%s)",
                        raw_name, alias, tok)
            return alias, tok, 1.0

        if norm in self._map:                            # exact symbol hit
            return norm, self._map[norm], 1.0
        compact = norm.replace(" ", "")
        if compact in self._map:
            return compact, self._map[compact], 1.0

        if self._keys:                                   # fuzzy fallback
            hits = difflib.get_close_matches(norm, self._keys, n=1, cutoff=0.78)
            if not hits:
                hits = difflib.get_close_matches(compact, self._keys, n=1, cutoff=0.78)
            if hits:
                score = difflib.SequenceMatcher(None, norm, hits[0]).ratio()
                logger.info("L2 token: %r ~ %r (fuzzy %.2f)", raw_name, hits[0], score)
                return hits[0], self._map[hits[0]], round(score, 2)

        logger.warning("L2 token: could not resolve %r", raw_name)
        return None, None, 0.0


# ── Indicator computation ──────────────────────────────────────────────────────

DEMA_LENGTHS = (5, 9, 20, 50, 100, 200)


def _dema(close: pd.Series, length: int) -> pd.Series:
    e1 = close.ewm(span=length, adjust=False).mean()
    e2 = e1.ewm(span=length, adjust=False).mean()
    return 2 * e1 - e2


def _rsi_wilder(close: pd.Series, length: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / length, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / length, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _session_vwap(df: pd.DataFrame) -> pd.Series:
    """Intraday VWAP anchored to each trading day (resets at session open)."""
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    day = df.index.normalize()
    pv = (tp * df["volume"]).groupby(day).cumsum()
    vv = df["volume"].groupby(day).cumsum().replace(0, np.nan)
    return pv / vv


def compute_indicators(df: pd.DataFrame, intraday: bool) -> pd.DataFrame:
    """
    Attach the full Layer-2 indicator library. pandas_ta where available,
    hand-rolled equivalents otherwise — same math either way, so validation
    logic never branches on the backend.
    """
    out = df.copy()
    close = out["close"]
    n = len(out)

    for L in DEMA_LENGTHS:
        if n >= 2 * L:                          # DEMA needs ~2L bars to stabilise
            col = ta.dema(close, length=L) if _HAS_TA else _dema(close, L)
            out[f"DEMA_{L}"] = col
        else:
            out[f"DEMA_{L}"] = np.nan

    out["EMA_5"] = close.ewm(span=5, adjust=False).mean()
    out["EMA_20"] = close.ewm(span=20, adjust=False).mean()

    out["RSI_14"] = (ta.rsi(close, length=14) if _HAS_TA else
                     _rsi_wilder(close, 14)) if n >= 15 else np.nan

    if n >= 35:
        if _HAS_TA:
            macd = ta.macd(close, fast=12, slow=26, signal=9)
            out["MACD"] = macd["MACD_12_26_9"]
            out["MACD_SIGNAL"] = macd["MACDs_12_26_9"]
        else:
            fast = close.ewm(span=12, adjust=False).mean()
            slow = close.ewm(span=26, adjust=False).mean()
            out["MACD"] = fast - slow
            out["MACD_SIGNAL"] = out["MACD"].ewm(span=9, adjust=False).mean()
    else:
        out["MACD"] = out["MACD_SIGNAL"] = np.nan

    if n >= 20:
        if _HAS_TA:
            bb = ta.bbands(close, length=20, std=2)
            basis = next(c for c in bb.columns if c.startswith("BBM"))
            lower = next(c for c in bb.columns if c.startswith("BBL"))
            upper = next(c for c in bb.columns if c.startswith("BBU"))
            out["BB_BASIS"], out["BB_LOWER"], out["BB_UPPER"] = (
                bb[basis], bb[lower], bb[upper])
        else:
            mid = close.rolling(20).mean()
            sd = close.rolling(20).std(ddof=0)
            out["BB_BASIS"], out["BB_LOWER"], out["BB_UPPER"] = (
                mid, mid - 2 * sd, mid + 2 * sd)
    else:
        out["BB_BASIS"] = out["BB_LOWER"] = out["BB_UPPER"] = np.nan

    if intraday:
        out["VWAP"] = _session_vwap(out)

    return out


def _clean(df: Optional[pd.DataFrame], bar: timedelta,
           now: Optional[datetime] = None) -> Optional[pd.DataFrame]:
    """Sort, dedupe, and drop the still-forming last candle."""
    if df is None or df.empty:
        return None
    df = df[~df.index.duplicated(keep="last")].sort_index()
    now = now or datetime.now()
    last_open = df.index[-1].to_pydatetime().replace(tzinfo=None)
    if last_open + bar > now:
        df = df.iloc[:-1]
        logger.debug("L2 dropped forming bar opened %s", last_open)
    return df if not df.empty else None


class MarketDataCore:
    """
    LAYER 2 orchestrator — token resolution, MTF fetch, indicator attach.

    intraday_interval: "1h" (default, Archetype A) — Archetype B requests force
    a 15M frame too, so both are fetched when the strategy tag needs them.
    oi_provider: optional callable (symbol, strike, opt_type, expiry) ->
    list of recent OI values per 15M bar, oldest first. Plug oi_tracker here.
    """

    DAILY_LOOKBACK_DAYS = 480       # ~320 trading bars: DEMA_200 needs >= 400 closes
    INTRADAY_LOOKBACK_DAYS = 30

    def __init__(self, broker, token_resolver: Optional[TokenResolver] = None,
                 oi_provider: Optional[Callable[..., Optional[List[float]]]] = None):
        self.broker = broker
        self.resolver = token_resolver or TokenResolver()
        self.oi_provider = oi_provider

    # -- public API -------------------------------------------------------------

    def build_snapshot(self, sig: AdvisorySignal,
                       need_1h: bool = True,
                       need_15m: bool = True) -> Optional[MarketSnapshot]:
        sym, tok, score = self.resolver.resolve(sig.tradable_instrument or "")
        if not sym:
            logger.error("L2 ✖ unresolvable instrument %r — snapshot aborted",
                         sig.tradable_instrument)
            return None
        sig.resolved_symbol, sig.resolved_token, sig.resolve_score = sym, tok, score

        now = datetime.now()
        snap = MarketSnapshot(symbol=sym)

        daily = self._fetch(sym, "1d", now - timedelta(days=self.DAILY_LOOKBACK_DAYS), now)
        daily = _clean(daily, timedelta(days=1), now)
        if daily is not None:
            snap.daily = compute_indicators(daily, intraday=False)

        intraday_from = now - timedelta(days=self.INTRADAY_LOOKBACK_DAYS)
        if need_1h:
            h1 = _clean(self._fetch(sym, "1h", intraday_from, now), timedelta(hours=1), now)
            if h1 is not None:
                snap.intraday_1h = compute_indicators(h1, intraday=True)
        if need_15m:
            m15 = _clean(self._fetch(sym, "15m", intraday_from, now),
                         timedelta(minutes=15), now)
            if m15 is not None:
                snap.intraday_15m = compute_indicators(m15, intraday=True)

        snap.underlying_ltp = self._ltp(sym)
        snap.ltp = (self._ltp(self._derivative_symbol(sig))
                    if sig.is_derivative else snap.underlying_ltp)

        if sig.is_derivative and self.oi_provider is not None:
            try:
                snap.oi_series = self.oi_provider(
                    sym, sig.strike, sig.segment.value, sig.expiry)
            except Exception as e:
                logger.warning("L2 OI provider failed for %s: %s", sym, e)

        logger.info("L2 ◀ snapshot %s: daily=%s 1h=%s 15m=%s ltp=%s oi=%s",
                    sym,
                    len(snap.daily) if snap.has_frame("daily") else 0,
                    len(snap.intraday_1h) if snap.has_frame("intraday_1h") else 0,
                    len(snap.intraday_15m) if snap.has_frame("intraday_15m") else 0,
                    snap.ltp,
                    len(snap.oi_series) if snap.oi_series else "n/a")
        return snap

    # -- internals ----------------------------------------------------------------

    def _fetch(self, symbol: str, interval: str,
               start: datetime, end: datetime) -> Optional[pd.DataFrame]:
        try:
            df = self.broker.get_historical_data(
                symbol=symbol, interval=interval,
                from_date=start.strftime(_DT_FMT), to_date=end.strftime(_DT_FMT),
                exchange="NSE")
            if df is None or df.empty:
                logger.warning("L2 fetch: no %s data for %s", interval, symbol)
                return None
            return df
        except Exception as e:
            logger.error("L2 fetch %s %s failed: %s", symbol, interval, e)
            return None

    def _ltp(self, symbol: Optional[str]) -> Optional[float]:
        if not symbol:
            return None
        try:
            return self.broker.get_ltp(symbol)
        except Exception as e:
            logger.warning("L2 LTP %s failed: %s", symbol, e)
            return None

    @staticmethod
    def _derivative_symbol(sig: AdvisorySignal) -> Optional[str]:
        """Angel NFO style: MOTHERSON26MAY26130CE (best-effort; LTP optional)."""
        if not (sig.resolved_symbol and sig.strike and sig.expiry):
            return None
        opt = "CE" if sig.segment.value == "DERIVATIVE_CALL" else "PE"
        exp = re.sub(r"[\s-]", "", sig.expiry.upper())
        strike = int(sig.strike) if float(sig.strike).is_integer() else sig.strike
        return f"{sig.resolved_symbol}{exp}{strike}{opt}"
