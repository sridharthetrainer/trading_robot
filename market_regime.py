"""
market_regime.py — Full Market Regime Engine

Classifies market into 6 regimes EVERY MORNING and adjusts:
  - Strategy weights in confluence engine
  - Position sizes
  - Stop/target distances
  - Which strategy families get priority

REGIMES:
  STRONG_TREND    → ADX>30, price above EMA50, directional momentum
  WEAK_TREND      → ADX 20-30, developing trend
  MEAN_REVERT     → ADX<20, price oscillating around VWAP/EMA
  HIGH_VOL        → VIX>20, wide ATR, sudden moves
  LOW_VOL_CHOP    → VIX<13, narrow ATR, theta strategies dominate
  BREAKOUT        → ADX rising from <20, expanding volatility

BOOKS REFERENCE:
  Stan Weinstein  — 4 stage model (base→breakout→uptrend→top)
  John Murphy     — ADX regime classification
  Kevin Haggerty  — Market regime and strategy rotation
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)
_STATE  = Path("regime_state.json")


REGIME_CONFIG: Dict[str, Dict] = {
    "STRONG_TREND": {
        "strategy_weights": {
            "trend": 2.0, "breakout": 1.8, "orb": 1.6,
            "supertrend_mtf": 1.8, "ma_cross": 1.6,
            "morning_momentum": 1.5,
            "mean_reversion": 0.3, "vwap_reversion": 0.3,
        },
        "size_multiplier":  1.2,
        "stop_atr_mult":    1.8,   # wider stops — let winners run
        "target_atr_mult":  3.5,   # larger targets
        "min_score_adjust": -0.5,  # easier to trigger
        "description":      "Strong directional trend — momentum strategies preferred",
    },
    "WEAK_TREND": {
        "strategy_weights": {
            "trend": 1.3, "breakout": 1.2, "orb": 1.2,
            "vwap_reversion": 1.0, "cpr": 1.1,
        },
        "size_multiplier":  1.0,
        "stop_atr_mult":    1.5,
        "target_atr_mult":  2.5,
        "min_score_adjust": 0.0,
        "description":      "Developing trend — standard sizing",
    },
    "MEAN_REVERT": {
        "strategy_weights": {
            "mean_reversion": 2.0, "vwap_reversion": 1.8, "cpr": 1.5,
            "rsi_divergence": 1.6, "stat_arb": 1.5,
            "trend": 0.4, "breakout": 0.4, "orb": 0.5,
        },
        "size_multiplier":  1.0,
        "stop_atr_mult":    1.0,   # tighter stops — quick fades
        "target_atr_mult":  1.5,   # smaller targets — book fast
        "min_score_adjust": 0.5,   # harder to trigger
        "description":      "Range-bound — fade extremes, sell premium",
    },
    "HIGH_VOL": {
        "strategy_weights": {
            "expiry_scalp": 1.5, "cpr": 1.2, "vwap_reversion": 1.1,
            "breakout": 0.6, "trend": 0.7, "orb": 0.8,
            "scalping": 0.4,
        },
        "size_multiplier":  0.6,   # REDUCE SIZE — unpredictable
        "stop_atr_mult":    2.0,   # wider stops to survive whipsaws
        "target_atr_mult":  3.0,
        "min_score_adjust": 1.0,   # HIGHER bar — only highest conviction
        "description":      "High volatility — reduce size, wider stops, favour defined-risk",
    },
    "LOW_VOL_CHOP": {
        "strategy_weights": {
            "theta_strategy": 2.0, "cpr": 1.5, "stat_arb": 1.4,
            "vwap_reversion": 1.3, "mean_reversion": 1.2,
            "breakout": 0.5, "trend": 0.6,
        },
        "size_multiplier":  0.8,   # smaller directional, larger theta
        "stop_atr_mult":    1.0,
        "target_atr_mult":  1.5,
        "min_score_adjust": 0.5,
        "description":      "Low vol / choppy — theta strategies dominate",
    },
    "BREAKOUT": {
        "strategy_weights": {
            "breakout": 2.2, "orb": 2.0, "vp_breakout": 1.8,
            "price_structure": 1.8, "morning_momentum": 1.6,
            "mean_reversion": 0.2, "vwap_reversion": 0.3,
        },
        "size_multiplier":  1.1,
        "stop_atr_mult":    1.3,   # tight stops — breakout or exit fast
        "target_atr_mult":  3.0,   # big targets — breakouts can run
        "min_score_adjust": -0.3,
        "description":      "Breakout forming — momentum / breakout strategies first",
    },
}


def classify_regime(
    df:       pd.DataFrame,
    vix:      float = 15.0,
    df_daily: Optional[pd.DataFrame] = None,
) -> Tuple[str, float, str]:
    """
    Classify market regime from OHLCV + indicators.

    Returns (regime_name, confidence_0_to_1, description)
    """
    try:
        df_c = df.copy()
        df_c.columns = [c.lower() for c in df_c.columns]
        if len(df_c) < 30:
            return "WEAK_TREND", 0.5, "Insufficient data"

        close  = df_c["close"].values
        high   = df_c["high"].values   if "high"   in df_c.columns else close
        low    = df_c["low"].values    if "low"    in df_c.columns else close
        volume = df_c["volume"].values if "volume" in df_c.columns else np.ones(len(close))

        # ── Indicators ────────────────────────────────────────────────────
        # ATR (14)
        tr     = np.maximum(high[1:]-low[1:],
                 np.maximum(abs(high[1:]-close[:-1]), abs(low[1:]-close[:-1])))
        atr14  = float(np.mean(tr[-14:])) if len(tr) >= 14 else float(np.std(close[-20:]))
        atr_pct = atr14 / close[-1] * 100

        # ADX proxy (trend strength via directional movement)
        up_move   = np.diff(high)
        dn_move   = -np.diff(low)
        plus_dm   = np.where((up_move > dn_move) & (up_move > 0), up_move, 0)
        minus_dm  = np.where((dn_move > up_move) & (dn_move > 0), dn_move, 0)
        tr_sum    = np.sum(tr[-14:]) + 1e-9
        plus_di   = 100 * np.sum(plus_dm[-14:]) / tr_sum
        minus_di  = 100 * np.sum(minus_dm[-14:]) / tr_sum
        dx        = 100 * abs(plus_di - minus_di) / (plus_di + minus_di + 1e-9)
        adx       = float(np.mean([dx] * 3))  # simplified ADX

        # EMA
        def ema(arr, n):
            k = 2/(n+1)
            e = arr[0]
            for v in arr[1:]: e = v*k + e*(1-k)
            return float(e)

        ema20  = ema(close[-25:], 20)
        ema50  = ema(close[-55:], 50) if len(close) >= 55 else ema(close, len(close)//2)
        price  = float(close[-1])

        # R-squared (trend quality)
        x    = np.arange(min(20, len(close)))
        y    = close[-len(x):]
        p    = np.polyfit(x, y, 1)
        yhat = np.polyval(p, x)
        r2   = 1 - np.sum((y-yhat)**2) / max(np.sum((y-np.mean(y))**2), 1e-9)

        # Volume trend
        vol_ratio = float(np.mean(volume[-5:])) / max(float(np.mean(volume[-20:])), 1)

        # ATR trend (expanding = breakout, contracting = chop)
        atr_recent = float(np.mean(tr[-5:]))  if len(tr) >= 5  else atr14
        atr_older  = float(np.mean(tr[-14:])) if len(tr) >= 14 else atr14
        atr_expanding = atr_recent > atr_older * 1.1

        # ── Classification Logic ──────────────────────────────────────────
        # HIGH_VOL: VIX elevated OR ATR% very high
        if vix > 22 or atr_pct > 1.5:
            conf = min(1.0, (vix - 18) / 10) if vix > 22 else (atr_pct - 1.0) / 0.5
            return "HIGH_VOL", round(min(0.95, max(0.5, conf)), 2), REGIME_CONFIG["HIGH_VOL"]["description"]

        # LOW_VOL_CHOP: VIX low AND ADX low AND R² low
        if vix < 13 and adx < 18 and r2 < 0.3:
            return "LOW_VOL_CHOP", 0.75, REGIME_CONFIG["LOW_VOL_CHOP"]["description"]

        # BREAKOUT: ADX rising from low AND volume expanding AND ATR expanding
        if adx < 25 and atr_expanding and vol_ratio > 1.3 and r2 > 0.5:
            return "BREAKOUT", round(min(0.85, r2 * vol_ratio), 2), REGIME_CONFIG["BREAKOUT"]["description"]

        # STRONG_TREND: ADX > 28 AND price above both EMAs AND R² high
        if adx > 28 and r2 > 0.65:
            direction_ok = (price > ema20 > ema50) or (price < ema20 < ema50)
            if direction_ok:
                return "STRONG_TREND", round(min(0.9, adx/40 + r2/2), 2), REGIME_CONFIG["STRONG_TREND"]["description"]

        # MEAN_REVERT: ADX < 20 AND R² low (choppy)
        if adx < 20 and r2 < 0.35:
            return "MEAN_REVERT", round(0.6 + (20-adx)/40, 2), REGIME_CONFIG["MEAN_REVERT"]["description"]

        # WEAK_TREND: default
        return "WEAK_TREND", 0.55, REGIME_CONFIG["WEAK_TREND"]["description"]

    except Exception as e:
        logger.debug("classify_regime: %s", e)
        return "WEAK_TREND", 0.5, "Classification error"


class MarketRegimeEngine:
    """
    Full regime engine — classifies daily, stores state,
    provides adjustments for every other module.
    """

    def __init__(self, alerts=None) -> None:
        self.alerts   = alerts
        self._state   = self._load()
        self.regime   = self._state.get("regime",     "WEAK_TREND")
        self.confidence = self._state.get("confidence", 0.5)
        self.vix      = self._state.get("vix",        15.0)

    def _load(self) -> dict:
        try:
            if _STATE.exists():
                return json.loads(_STATE.read_text())
        except Exception:
            pass
        return {}

    def _save(self) -> None:
        try:
            _STATE.write_text(json.dumps({
                "regime":      self.regime,
                "confidence":  self.confidence,
                "vix":         self.vix,
                "updated":     date.today().isoformat(),
            }))
        except Exception:
            pass

    def update(
        self,
        df:       pd.DataFrame,
        vix:      float = 15.0,
        df_daily: Optional[pd.DataFrame] = None,
        symbol:   str   = "NIFTY",
    ) -> str:
        """
        Classify and store regime. Send Telegram alert if changed.
        Call once per morning at 9:05 AM.
        """
        old_regime  = self.regime
        self.vix    = vix
        regime, conf, desc = classify_regime(df, vix, df_daily)
        self.regime     = regime
        self.confidence = conf
        self._save()

        if old_regime != regime and self.alerts:
            cfg   = REGIME_CONFIG[regime]
            icon  = {"STRONG_TREND":"📈","WEAK_TREND":"〰️","MEAN_REVERT":"↔️",
                     "HIGH_VOL":"🔥","LOW_VOL_CHOP":"😴","BREAKOUT":"💥"}.get(regime,"📊")
            size_pct = int(cfg["size_multiplier"] * 100)
            self.alerts.send(
                f"{icon} <b>REGIME CHANGE: {regime}</b>\n"
                f"  Was: {old_regime}\n"
                f"  {desc}\n"
                f"  Confidence: {conf*100:.0f}%\n"
                f"  Size: {size_pct}% of normal\n"
                f"  Stops: {cfg['stop_atr_mult']}× ATR\n"
                f"  Target: {cfg['target_atr_mult']}× ATR\n"
                f"🕐 {datetime.now().strftime('%H:%M')}",
                dedup_key=f"regime:{regime}:{date.today()}",
                dedup_cooldown_override=3600,
            )

        logger.info("Regime: %s (conf=%.2f, vix=%.1f)", regime, conf, vix)
        return regime

    def get_config(self) -> dict:
        return REGIME_CONFIG.get(self.regime, REGIME_CONFIG["WEAK_TREND"])

    def size_multiplier(self) -> float:
        return self.get_config().get("size_multiplier", 1.0)

    def stop_atr_mult(self) -> float:
        return self.get_config().get("stop_atr_mult", 1.5)

    def target_atr_mult(self) -> float:
        return self.get_config().get("target_atr_mult", 2.5)

    def strategy_weight(self, strategy_name: str) -> float:
        weights = self.get_config().get("strategy_weights", {})
        return weights.get(strategy_name, 1.0)

    def min_score_adjust(self) -> float:
        return self.get_config().get("min_score_adjust", 0.0)

    def telegram_summary(self) -> str:
        cfg = self.get_config()
        icon = {"STRONG_TREND":"📈","WEAK_TREND":"〰️","MEAN_REVERT":"↔️",
                "HIGH_VOL":"🔥","LOW_VOL_CHOP":"😴","BREAKOUT":"💥"}.get(self.regime,"📊")
        return (
            f"{icon} <b>REGIME: {self.regime}</b>\n"
            f"  {cfg['description']}\n"
            f"  Confidence: {self.confidence*100:.0f}%\n"
            f"  VIX: {self.vix:.1f}\n"
            f"  Size: {int(cfg['size_multiplier']*100)}%  "
            f"Stop: {cfg['stop_atr_mult']}×ATR  "
            f"Target: {cfg['target_atr_mult']}×ATR"
        )


_engine: Optional[MarketRegimeEngine] = None
def get_regime_engine(alerts=None) -> MarketRegimeEngine:
    global _engine
    if _engine is None:
        _engine = MarketRegimeEngine(alerts=alerts)
    return _engine
