#!/usr/bin/env python3
"""
option_scalper.py — fast intraday option-scalp SIGNAL for index underlyings.

SIGNAL-ONLY. It emits a scalp setup (direction + ATM strike) when a short-term
momentum breakout fires on an index and journals it via record_option_decision
(strategy="OPTION_SCALP") — which (a) pushes a card to the option Telegram channel
and (b) accrues in the option decision journal for later measurement. It places
NO orders and makes NO edge claim. Like every other strategy here it must be
VALIDATED before any live use; this just generates + records the signal so its
edge can be MEASURED (accrue-then-measure discipline). Disabled by default.

Setup (intraday momentum breakout, ATM):
  - last close breaks the high of the prior LOOKBACK bars  -> BUY  -> ATM CE
    or breaks the low                                       -> SELL -> ATM PE
  - confirmed by a bar-range expansion vs the recent average
  - aligned with VWAP (close above VWAP for BUY, below for SELL)

Tune (env / config): OPTION_SCALPER_ENABLED (default false), OPTION_SCALP_INTERVAL
("5m"), OPTION_SCALP_LOOKBACK (5), OPTION_SCALP_MIN_SCORE (6.0),
OPTION_SCALP_EVERY_MIN (5).

Usage:
    python option_scalper.py            # one scan, journal + print
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, time as dtime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_last_scan_ts: Dict[str, float] = {}
_FETCHER = None  # cached DataFetcher — init is heavy (master contract + universe)


def _get_fetcher():
    """Reuse one DataFetcher instead of rebuilding it (39k-row master contract +
    universe load) on every throttled scan."""
    global _FETCHER
    if _FETCHER is None:
        angel = None
        try:
            from angel import AngelOne
            angel = AngelOne(
                api_key=os.getenv("API_KEY", ""),
                client_id=os.getenv("CLIENT_ID", ""),
                password=os.getenv("PASSWORD", ""),
                totp_secret=os.getenv("TOTP_SECRET", ""),
            )
        except Exception as exc:
            logger.debug("option scalper Angel data client unavailable: %s", exc)
        from data_fetcher import DataFetcher
        _FETCHER = DataFetcher(angel=angel, paper_trade=False)
    return _FETCHER


def _cfg(name: str, default):
    try:
        import config
        return getattr(config, name, os.getenv(name, default))
    except Exception:
        return os.getenv(name, default)


def _strike_step(symbol: str) -> int:
    s = str(symbol or "").upper()
    if any(k in s for k in ("BANKNIFTY", "SENSEX", "BANKEX")):
        return 100
    return 50


def _atm_strike(spot: float, symbol: str) -> int:
    step = _strike_step(symbol)
    return int(round(spot / step) * step)


def scalp_signal(df, lookback: int = 5, min_score: float = 6.0) -> Optional[Dict[str, Any]]:
    """Pure: index OHLCV DataFrame -> scalp signal dict or None. Testable offline."""
    try:
        if df is None or len(df) < lookback + 5:
            return None
        cols = {str(c).lower(): c for c in df.columns}
        if not {"close", "high", "low"}.issubset(cols):
            return None
        close = df[cols["close"]].astype(float)
        high = df[cols["high"]].astype(float)
        low = df[cols["low"]].astype(float)
        vol = df[cols["volume"]].astype(float) if "volume" in cols else None

        last_close = float(close.iloc[-1])
        prior_high = float(high.iloc[-(lookback + 1):-1].max())
        prior_low = float(low.iloc[-(lookback + 1):-1].min())

        rng = (high - low).abs()
        last_rng = float(rng.iloc[-1])
        avg_rng = float(rng.iloc[-(lookback + 1):-1].mean() or 0.0)
        rng_exp = (last_rng / avg_rng) if avg_rng > 0 else 1.0

        try:
            tp = (high + low + close) / 3.0
            if vol is not None and float(vol.sum()) > 0:
                vwap = float((tp * vol).cumsum().iloc[-1] / vol.cumsum().iloc[-1])
            else:
                vwap = float(close.tail(lookback).mean())
        except Exception:
            vwap = float(close.tail(lookback).mean())

        side = None
        if last_close > prior_high and last_close > vwap:
            side = "BUY"
        elif last_close < prior_low and last_close < vwap:
            side = "SELL"
        if side is None:
            return None

        ref = prior_high if side == "BUY" else prior_low
        brk_bps = abs(last_close - ref) / max(ref, 1.0) * 10000.0
        score = min(10.0, 3.0 + brk_bps / 5.0 + (rng_exp - 1.0) * 2.0)
        if score < min_score:
            return None

        lvl = f">{prior_high:.1f}" if side == "BUY" else f"<{prior_low:.1f}"
        return {
            "side": side,
            "option_type": "CE" if side == "BUY" else "PE",
            "score": round(score, 2),
            "reason": f"scalp breakout {lvl} rng_exp={rng_exp:.1f}x vwap_ok",
            "last_close": last_close,
        }
    except Exception as exc:
        logger.debug("scalp_signal: %s", exc)
        return None


def scan_and_journal(symbols: Optional[List[str]] = None) -> int:
    """For each index: fetch recent candles, compute a scalp signal, and journal it
    (strategy=OPTION_SCALP -> alerts the option channel + accrues). Signal-only;
    places no orders. Returns the number of signals emitted."""
    try:
        from option_decision_journal import record_option_decision
    except Exception as exc:
        logger.debug("scalper imports: %s", exc)
        return 0
    if symbols is None:
        try:
            from live_signal_engine import SUPPORTED_OPTION_UNDERLYINGS
            symbols = sorted(SUPPORTED_OPTION_UNDERLYINGS)
        except Exception:
            symbols = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]

    interval = str(_cfg("OPTION_SCALP_INTERVAL", "5m"))
    lookback = int(_cfg("OPTION_SCALP_LOOKBACK", 5))
    min_score = float(_cfg("OPTION_SCALP_MIN_SCORE", 6.0))
    fetcher = _get_fetcher()
    emitted = 0
    for sym in symbols:
        try:
            df = fetcher.get_market_data(sym, interval, days=2)
            sig = scalp_signal(df, lookback=lookback, min_score=min_score)
            if not sig:
                continue
            spot = sig["last_close"]
            strike = _atm_strike(spot, sym)
            record_option_decision(
                strategy="OPTION_SCALP", symbol=sym, decision="selected",
                reason=sig["reason"], side=sig["side"], spot=spot,
                setup_score=sig["score"],
                selected={"symbol": f"{sym}{strike}{sig['option_type']}",
                          "strike": strike, "option_type": sig["option_type"]},
                source_id=f"scalp_{sym}_{datetime.now():%Y%m%d%H%M}",
            )
            emitted += 1
            logger.info("OPTION_SCALP %s %s ATM %s%s score=%.1f",
                        sym, sig["side"], strike, sig["option_type"], sig["score"])
        except Exception as exc:
            logger.debug("scalp %s: %s", sym, exc)
    return emitted


def _market_open_now() -> bool:
    now = datetime.now().time()
    return dtime(9, 15) <= now <= dtime(15, 30)


def maybe_scan() -> int:
    """Throttled, market-hours-gated entry for the live loop. Best-effort.
    Disabled unless OPTION_SCALPER_ENABLED is truthy."""
    if str(_cfg("OPTION_SCALPER_ENABLED", "false")).lower() not in ("1", "true", "yes", "on"):
        return 0
    if not _market_open_now():
        return 0
    every = max(1, int(_cfg("OPTION_SCALP_EVERY_MIN", 5))) * 60
    if time.time() - _last_scan_ts.get("_", 0.0) < every:
        return 0
    _last_scan_ts["_"] = time.time()
    try:
        return scan_and_journal()
    except Exception as exc:
        logger.debug("maybe_scan: %s", exc)
        return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    print("OPTION_SCALP signals emitted:", scan_and_journal())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
