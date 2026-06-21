"""
ml_feature_builder.py  —  Post-market ML feature matrix builder.

Builds a per-signal feature DataFrame from two sources:
  1. signal_log.db  — 40+ features already stored (VIX, volume, regime, FII, …)
  2. Angel API      — optional candle-derived indicators for recent signals
     (1m / 5m / 15m / 1h / daily). Rate-limited; skipped gracefully on error.

Output DataFrame columns (all numeric after encoding):
  signal_log features + candle features + engineered composites
  target column: tb_outcome  (1=win, 0=loss/timeout)

Usage:
    from ml_feature_builder import build_feature_matrix
    df = build_feature_matrix(days=90, include_candles=True, candle_days=30)
"""
from __future__ import annotations

import logging
import math
import os
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

SIGNAL_DB = Path(os.getenv("SIGNAL_LOG_DB", "signal_log.db"))

# ── Categorical encoding maps ─────────────────────────────────────────────────
_REGIME_ENC    = {"TREND": 2, "BREAKOUT": 2, "RANGING": 0, "MEAN_REVERT": 0,
                  "VOLATILE": 1, "UNKNOWN": -1, "": -1}
_HTF_ENC       = {"BULLISH": 1, "BEARISH": -1, "NEUTRAL": 0, "": 0, "UNKNOWN": 0}
_CROSS_ENC     = {"BULLISH": 1, "MILDLY_BULLISH": 0.5, "NEUTRAL": 0,
                  "MILDLY_BEARISH": -0.5, "BEARISH": -1, "": 0}
_EXPIRY_ENC    = {"SAME_DAY": 3, "NEXT_DAY": 2, "THIS_WEEK": 1, "NEXT_WEEK": 0,
                  "": 0}
_CONFL_ENC     = {"STRONG": 3, "MODERATE": 2, "SINGLE": 1, "WEAK": 0, "": 0}
_SIDE_ENC      = {"BUY": 1, "SELL": -1, "": 0}
_PROFILE_BIAS_ENC = {
    "BULLISH_BREAKOUT": 2,
    "BULLISH_POC_CROSS": 1.5,
    "BALANCED_VALUE": 0,
    "NEUTRAL": 0,
    "BEARISH_POC_CROSS": -1.5,
    "BEARISH_BREAKDOWN": -2,
    "": 0,
}
_PROFILE_POS_ENC = {
    "ABOVE_VALUE": 2,
    "UPPER_VALUE": 1,
    "LOWER_VALUE": -1,
    "BELOW_VALUE": -2,
    "UNKNOWN": 0,
    "": 0,
}

# Features pulled directly from signal_log (all numeric / encoded)
_DB_COLS = [
    "score", "raw_score", "n_agree", "n_conflict",
    "bhav_delivery", "cross_asset_mod", "participant_mod", "expiry_mod",
    "sip_boost", "bulk_deal_mod", "theta_mod", "rebal_mod", "news_mod",
    "mtf_pivot_mod", "time_bucket_wt", "ai_score", "rl_bias", "weinstein_mod",
    "market_profile_mod", "profile_poc_distance_pct", "profile_value_width_pct",
    "volume_ratio",
    "india_vix", "iv_percentile", "pcr_atm",
    "fii_net_cash", "fii_fut_ratio", "fii_cum_5d", "client_ce_pct",
    "sensex_nifty_div",
    "above_weekly_pvt", "above_monthly_pvt", "pct_from_w_r1", "pct_from_m_r1",
    "expiry_dte",
    "sector_code", "hour_of_day", "day_of_week", "trade_num_today",
]
_ENCODE_COLS = {
    "regime":        _REGIME_ENC,
    "htf_bias":      _HTF_ENC,
    "cross_asset_bias": _CROSS_ENC,
    "expiry_regime": _EXPIRY_ENC,
    "confluence":    _CONFL_ENC,
    "side":          _SIDE_ENC,
    "profile_bias":  _PROFILE_BIAS_ENC,
    "profile_position": _PROFILE_POS_ENC,
}


def _safe(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except Exception:
        return default


def _load_signals(days: int = 90, executed_only: bool = False) -> List[Dict]:
    """Load labelled signals from signal_log.db."""
    if not SIGNAL_DB.exists():
        logger.error("signal_log.db not found: %s", SIGNAL_DB)
        return []
    try:
        conn = sqlite3.connect(str(SIGNAL_DB))
        conn.row_factory = sqlite3.Row
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        exec_clause = "AND executed = 1" if executed_only else ""
        rows = conn.execute(
            f"""SELECT * FROM signal_log
                WHERE tb_label NOT IN (-99, -2)
                  AND signal_date >= ?
                  {exec_clause}
                ORDER BY signal_date, signal_time""",
            (cutoff,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.error("signal_log load failed: %s", exc)
        return []


def _encode_row(row: Dict) -> Dict[str, float]:
    """Convert one signal_log row into a flat numeric feature dict."""
    feat: Dict[str, float] = {}

    # Direct numeric columns
    for col in _DB_COLS:
        feat[col] = _safe(row.get(col))

    # Encoded categorical columns
    for col, enc_map in _ENCODE_COLS.items():
        val = str(row.get(col) or "").strip().upper()
        feat[f"{col}_enc"] = float(enc_map.get(val, 0))

    # Engineered features
    score       = _safe(row.get("score"))
    raw_score   = _safe(row.get("raw_score"))
    feat["score_boost"]        = score - raw_score            # how much modifiers added
    feat["score_boost_pct"]    = (score - raw_score) / max(abs(raw_score), 1.0)
    feat["htf_aligned"]        = float(
        str(row.get("htf_bias","")).upper() in ("BULLISH","BEARISH")
        and str(row.get("side","")).upper() in ("BUY","SELL")
        and (
            (str(row.get("side","")).upper() == "BUY"  and str(row.get("htf_bias","")).upper() == "BULLISH") or
            (str(row.get("side","")).upper() == "SELL" and str(row.get("htf_bias","")).upper() == "BEARISH")
        )
    )
    feat["vix_high"]           = float(_safe(row.get("india_vix")) > 18)
    feat["vix_extreme"]        = float(_safe(row.get("india_vix")) > 22)
    feat["near_expiry"]        = float(_safe(row.get("expiry_dte")) <= 1)
    feat["open_session"]       = float(int(_safe(row.get("hour_of_day"))) == 9)
    feat["close_session"]      = float(int(_safe(row.get("hour_of_day"))) >= 14)
    feat["monday_trade"]       = float(int(_safe(row.get("day_of_week"))) == 0)
    feat["friday_trade"]       = float(int(_safe(row.get("day_of_week"))) == 4)
    feat["vol_thin"]           = float(_safe(row.get("volume_ratio")) < 0.3)
    feat["vol_confirmed"]      = float(_safe(row.get("volume_ratio")) > 0.8)
    feat["near_profile_poc"]   = float(abs(_safe(row.get("profile_poc_distance_pct"), 99)) <= 0.20)
    feat["profile_confirmed"]  = float(
        (_safe(row.get("market_profile_mod")) > 0 and str(row.get("side", "")).upper() in ("BUY", "SELL"))
    )
    feat["profile_headwind"]   = float(_safe(row.get("market_profile_mod")) < 0)
    feat["n_net_agree"]        = _safe(row.get("n_agree")) - _safe(row.get("n_conflict"))
    feat["fii_bull_confirm"]   = float(
        _safe(row.get("fii_cum_5d")) > 0
        and str(row.get("side","")).upper() == "BUY"
    )

    return feat


# ── Optional candle augmentation ──────────────────────────────────────────────

def _get_candle_features(
    symbol:      str,
    signal_ts:   float,   # unix timestamp of signal
    timeframes:  List[str] = None,
) -> Dict[str, float]:
    """
    Fetch candle data around signal_ts and compute technical indicators.
    Returns empty dict on any failure (candle augmentation is optional).
    """
    if timeframes is None:
        timeframes = ["1m", "5m", "15m", "1h", "1d"]

    feat: Dict[str, float] = {}
    try:
        import importlib
        angel_mod = importlib.import_module("angel")
        angel_obj = getattr(angel_mod, "_get_angel_instance", lambda: None)()
        if angel_obj is None:
            return feat
        get_hist = getattr(angel_mod, "get_historical_data", None)
        if not get_hist:
            return feat

        signal_dt = datetime.fromtimestamp(signal_ts)

        for tf in timeframes:
            try:
                bars = get_hist(
                    symbol=symbol,
                    interval=tf,
                    from_date=(signal_dt - timedelta(days=2)).strftime("%Y-%m-%d %H:%M"),
                    to_date=signal_dt.strftime("%Y-%m-%d %H:%M"),
                )
                if bars is None or len(bars) < 5:
                    continue

                closes = [float(b[4]) for b in bars[-20:]]
                highs  = [float(b[2]) for b in bars[-20:]]
                lows   = [float(b[3]) for b in bars[-20:]]
                vols   = [float(b[5]) if len(b) > 5 else 0 for b in bars[-20:]]

                n  = len(closes)
                c  = closes[-1]
                prefix = tf.replace("m", "m").replace("h", "h").replace("d", "d")

                # RSI-14 (simplified)
                if n >= 14:
                    gains = [max(closes[i]-closes[i-1], 0) for i in range(1, n)]
                    losses= [max(closes[i-1]-closes[i], 0) for i in range(1, n)]
                    ag = sum(gains[-13:]) / 13 if gains else 1
                    al = sum(losses[-13:]) / 13 if losses else 1
                    rs = ag / max(al, 1e-9)
                    feat[f"rsi_{prefix}"] = 100 - 100 / (1 + rs)

                # EMA distance (fast 9, slow 21)
                if n >= 21:
                    def _ema(data, period):
                        k = 2 / (period + 1)
                        e = data[0]
                        for x in data[1:]:
                            e = x * k + e * (1 - k)
                        return e
                    ema9  = _ema(closes, 9)
                    ema21 = _ema(closes, 21)
                    feat[f"ema_gap_{prefix}"]   = (ema9 - ema21) / max(c, 1e-9)
                    feat[f"ema_bull_{prefix}"]  = float(ema9 > ema21)
                    feat[f"price_vs_ema21_{prefix}"] = (c - ema21) / max(c, 1e-9)

                # ATR ratio
                if n >= 5:
                    trs = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]),
                               abs(lows[i]-closes[i-1]))
                           for i in range(1, n)]
                    atr = sum(trs[-5:]) / min(5, len(trs))
                    feat[f"atr_ratio_{prefix}"] = atr / max(c, 1e-9)

                # Volume spike (last bar vs 10-bar avg)
                if len(vols) >= 10 and vols[-1] > 0:
                    avg_vol = sum(vols[-10:-1]) / 9
                    feat[f"vol_spike_{prefix}"] = vols[-1] / max(avg_vol, 1e-9)

                # Bollinger Band position
                if n >= 20:
                    ma20  = sum(closes[-20:]) / 20
                    std20 = math.sqrt(sum((x-ma20)**2 for x in closes[-20:]) / 20)
                    if std20 > 0:
                        feat[f"bb_pos_{prefix}"] = (c - (ma20 - 2*std20)) / (4*std20)

            except Exception:
                continue

        time.sleep(0.25)   # Angel rate-limit courtesy pause between symbols

    except Exception as exc:
        logger.debug("candle augmentation failed (%s): %s", symbol, exc)

    return feat


# ── Public API ────────────────────────────────────────────────────────────────

def build_feature_matrix(
    days:            int  = 90,
    executed_only:   bool = True,
    include_candles: bool = True,
    candle_days:     int  = 30,
) -> "pd.DataFrame":
    """
    Build the full feature matrix for ML training.

    Parameters
    ----------
    days          : lookback window for signal_log
    executed_only : if True, include only executed=1 signals (cleaner labels)
    include_candles: augment with candle-derived indicators for recent signals
    candle_days   : how many recent days to fetch candle data for

    Returns
    -------
    pandas DataFrame with feature columns + `tb_outcome` (1=win, 0=loss/timeout)
    and metadata columns: `symbol`, `signal_date`, `strategy`, `side`,
    `tb_label`, `log_time`
    """
    try:
        import pandas as pd
    except ImportError:
        raise ImportError("pandas is required: pip install pandas")

    rows = _load_signals(days=days, executed_only=executed_only)
    if not rows:
        logger.warning("No labelled signals found in signal_log.db for last %d days", days)
        return pd.DataFrame()

    logger.info("Loaded %d labelled signals from signal_log.db", len(rows))

    # Data-quality guard: drop price-scale-corrupt rows (e.g. the 2026-06-11
    # MIDCPNIFTY rows logged at entry ~868 vs true index ~13,900) so they can't
    # poison ML training or analytics. Rows whose prices are merely MISSING are
    # kept — their encoded features + label are still usable. See signal_quality.py.
    try:
        from signal_quality import price_row_ok
        _n0 = len(rows)
        rows = [r for r in rows
                if not ((r.get("entry_price") or 0) > 0
                        and (r.get("outcome_price") or 0) > 0
                        and not price_row_ok(r.get("entry_price"), r.get("outcome_price"))[0])]
        if _n0 - len(rows):
            logger.warning("Data-quality guard: dropped %d price-corrupt signal rows", _n0 - len(rows))
    except Exception as _qexc:
        logger.debug("signal_quality guard skipped: %s", _qexc)

    candle_cutoff = time.time() - candle_days * 86400

    # Build feature rows
    feature_rows = []
    candle_cache: Dict[str, Dict[str, float]] = {}   # symbol → candle features

    for row in rows:
        feat = _encode_row(row)

        # Candle augmentation for recent signals
        if include_candles:
            sig_ts = _safe(row.get("log_time")) or 0.0
            if sig_ts > candle_cutoff:
                sym = str(row.get("symbol", ""))
                if sym and sym not in candle_cache:
                    logger.debug("Fetching candle features for %s", sym)
                    candle_cache[sym] = _get_candle_features(sym, sig_ts)
                feat.update(candle_cache.get(sym, {}))

        # Label: 1 = win, 0 = loss or timeout
        tb = int(_safe(row.get("tb_label")))
        feat["tb_outcome"] = 1 if tb == 1 else 0
        feat["tb_label"]   = tb

        # Metadata (not used in training, kept for analysis)
        feat["__symbol"]       = str(row.get("symbol", ""))
        feat["__signal_date"]  = str(row.get("signal_date", ""))
        feat["__strategy"]     = str(row.get("strategy", ""))
        feat["__side"]         = str(row.get("side", ""))
        feat["__log_time"]     = _safe(row.get("log_time"))

        feature_rows.append(feat)

    df = pd.DataFrame(feature_rows)

    # Join captured market context (FII/DII cash + sector breadth) by signal date,
    # so the ML/analysis uses them once the history accumulates (eod_market_capture).
    try:
        from eod_market_capture import market_context_by_date
        _ctx = market_context_by_date()
        for _col in ("hist_fii_net", "hist_fii_cum5", "sector_breadth"):
            df[_col] = df["__signal_date"].astype(str).map(
                lambda d, c=_col: _ctx.get(d, {}).get(c, 0.0))
    except Exception as _mce:
        logger.debug("market-context join skipped: %s", _mce)

    # Fill NaN with column medians (robust to missing data)
    num_cols = [c for c in df.columns if not c.startswith("__") and c not in ("tb_outcome", "tb_label")]
    for col in num_cols:
        if df[col].isnull().any():
            df[col] = df[col].fillna(df[col].median())

    logger.info(
        "Feature matrix: %d rows × %d features  (wins=%d  losses=%d  timeouts=%d)",
        len(df), len(num_cols),
        (df["tb_label"] == 1).sum(),
        (df["tb_label"] == -1).sum(),
        (df["tb_label"] == 0).sum(),
    )
    return df


def select_top_features(
    df: "pd.DataFrame",
    feat_cols: List[str],
    n: int = 20,
) -> List[str]:
    """
    Reduce feature set to the top n most informative features.

    Three-step process (all correlation-based — no model needed):
      1. Drop near-constant features (std < 0.01 across all rows).
      2. For highly correlated pairs (|r| > 0.85), keep the one with
         higher absolute correlation to the target (tb_outcome).
      3. Return top n survivors ranked by |corr(feature, target)|.

    Safe to call even when sklearn is unavailable.
    """
    try:
        import pandas as pd
        import numpy as np

        target = "tb_outcome"
        if target not in df.columns:
            return feat_cols[:n]

        available = [c for c in feat_cols if c in df.columns]
        if len(available) <= n:
            return available

        sub = df[available + [target]].copy()

        # Step 1 — drop near-constant
        stds = sub[available].std()
        live = [c for c in available if stds.get(c, 0) >= 0.01]
        if not live:
            return available[:n]

        # Step 2 — deduplicate correlated pairs
        corr_mat = sub[live].corr().abs()
        target_corr = sub[live].corrwith(sub[target]).abs().fillna(0)

        keep: List[str] = list(live)
        for i, ci in enumerate(live):
            if ci not in keep:
                continue
            for cj in live[i + 1:]:
                if cj not in keep:
                    continue
                if corr_mat.loc[ci, cj] > 0.85:
                    # Drop the one with lower target correlation
                    if target_corr.get(ci, 0) >= target_corr.get(cj, 0):
                        keep = [c for c in keep if c != cj]
                    else:
                        keep = [c for c in keep if c != ci]
                        break

        # Step 3 — rank by target correlation, take top n
        ranked = sorted(keep, key=lambda c: target_corr.get(c, 0), reverse=True)
        selected = ranked[:n]
        logger.debug(
            "select_top_features: %d → %d features (top: %s)",
            len(available), len(selected),
            ", ".join(selected[:5]),
        )
        return selected

    except Exception as exc:
        logger.debug("select_top_features failed, returning all: %s", exc)
        return feat_cols[:n]


def build_signal_context(signal: Dict[str, Any]) -> Dict[str, float]:
    """
    Build a feature dict from a LIVE signal dict (not from DB).
    Used by learned_filters.apply_learned_filters() at trade time.
    """
    pseudo_row = {
        "score":           signal.get("score", 0),
        "raw_score":       signal.get("raw_score", 0),
        "regime":          signal.get("regime", ""),
        "htf_bias":        signal.get("htf_bias", ""),
        "side":            signal.get("side", ""),
        "n_agree":         signal.get("n_agree", 1),
        "n_conflict":      signal.get("n_conflict", 0),
        "confluence":      signal.get("confluence", "SINGLE"),
        "india_vix":       (signal.get("signal_meta") or {}).get("india_vix", 15),
        "volume_ratio":    (signal.get("signal_meta") or {}).get("volume_ratio", 0),
        "hour_of_day":     datetime.now().hour,
        "day_of_week":     datetime.now().weekday(),
    }
    return _encode_row(pseudo_row)
