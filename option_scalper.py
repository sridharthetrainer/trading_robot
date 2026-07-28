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
        atr = float(rng.tail(14).mean() or avg_rng or last_rng)
        ema9 = float(close.ewm(span=9, adjust=False).mean().iloc[-1])
        ema21 = float(close.ewm(span=21, adjust=False).mean().iloc[-1])
        delta = close.diff()
        avg_gain = float(delta.clip(lower=0).tail(14).mean() or 0.0)
        avg_loss = float((-delta.clip(upper=0)).tail(14).mean() or 0.0)
        rsi = 100.0 if avg_loss <= 0 and avg_gain > 0 else (
            50.0 if avg_loss <= 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
        )
        volume_ratio = 0.0
        if vol is not None:
            prior_volume = float(vol.iloc[-(lookback + 1):-1].mean() or 0.0)
            volume_ratio = float(vol.iloc[-1]) / prior_volume if prior_volume > 0 else 0.0

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

        # These are UNDERLYING invalidation/target levels. The scanner has not
        # priced an option contract, so presenting them as option-premium stops
        # would be false precision.
        stop = (min(prior_high, last_close - atr)
                if side == "BUY" else max(prior_low, last_close + atr))
        risk = abs(last_close - stop)
        target_1 = last_close + risk if side == "BUY" else last_close - risk
        target_2 = last_close + 1.5 * risk if side == "BUY" else last_close - 1.5 * risk
        lvl = f">{prior_high:.1f}" if side == "BUY" else f"<{prior_low:.1f}"
        return {
            "side": side,
            "option_type": "CE" if side == "BUY" else "PE",
            "score": round(score, 2),
            "reason": f"scalp breakout {lvl} rng_exp={rng_exp:.1f}x vwap_ok",
            "last_close": last_close,
            "trigger": ref,
            "stop": round(stop, 2),
            "target_1": round(target_1, 2),
            "target_2": round(target_2, 2),
            "risk_reward_1": 1.0,
            "risk_reward_2": 1.5,
            "vwap": round(vwap, 2),
            "ema9": round(ema9, 2),
            "ema21": round(ema21, 2),
            "rsi": round(rsi, 1),
            "range_expansion": round(rng_exp, 2),
            "volume_ratio": round(volume_ratio, 2),
            "atr": round(atr, 2),
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
            option_context = {}
            try:
                from option_metrics_cache import get as get_option_metrics
                option_context = get_option_metrics(sym) or {}
            except Exception:
                pass
            selected_contract = {
                "symbol": f"{sym}{strike}{sig['option_type']}",
                "strike": strike,
                "option_type": sig["option_type"],
                "style": "scalping",
            }
            # OPTION_SCALP never fetches per-contract chain data (expiry,
            # premium, oi, volume, spread) -- it's deliberately a fast,
            # lightweight scan, unlike the full option-execution-quality gate
            # used for real candidates. Calling evaluate_option_candidate()
            # against this shape always returned the same GENERATED/
            # not-actionable verdict regardless of the actual signal (a
            # 2026-07-28 audit finding: the check was decorative, never gated
            # anything, and its constant output was misleading in the
            # journal). Recorded honestly instead of pretending to evaluate it.
            lifecycle = {
                "stage": "SIGNAL_ONLY",
                "note": "no per-contract chain data fetched; not evaluated against the execution lifecycle gate",
            }
            payload = record_option_decision(
                strategy="OPTION_SCALP", symbol=sym, decision="selected",
                reason=sig["reason"], side=sig["side"], spot=spot,
                setup_score=sig["score"],
                selected=selected_contract,
                source_id=f"scalp_{sym}_{datetime.now():%Y%m%d%H%M}",
                metadata={
                    "scalp_evidence": sig,
                    "interval": interval,
                    "option_context": option_context,
                    "lifecycle": lifecycle,
                },
            )
            if str(_cfg("OPTION_SCALP_IMAGE_REPORT", "true")).lower() in (
                "1", "true", "yes", "on"
            ):
                try:
                    from option_decision_journal import alert_option_scalp_report
                    alert_option_scalp_report(payload, df)
                except Exception as exc:
                    logger.debug("scalp image report %s: %s", sym, exc)
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
