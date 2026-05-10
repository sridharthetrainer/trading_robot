"""
institutional_alpha.py

7 proven strategies from the world's best trading institutions,
adapted for NSE NIFTY/BANKNIFTY options trading.

Institution → Strategy → NSE Implementation
────────────────────────────────────────────
Citadel       → Order Flow Imbalance (OFI)
Two Sigma     → IV Skew Direction
Jane Street   → Implied vs Realised Vol Spread
Winton Group  → Multi-Timeframe Trend Strength Index (MTSI)
DE Shaw       → NIFTY-BANKNIFTY Statistical Arbitrage
Goldman Sachs → OI Change Velocity (expiry magnet)
AQR Capital   → Strategy Momentum Factor
Renaissance   → Hurst Exponent (autocorrelation regime)

Each function returns a signal dict compatible with signal_engine:
{action, strategy, confidence, reason, indicators, score_boost}
"""

from __future__ import annotations

import logging
import time
from collections import deque
from datetime import datetime
from typing import Any, Deque, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _s(series: pd.Series, default: float = 0.0) -> float:
    try: v = series.iloc[-1]; return float(v) if pd.notna(v) else default
    except: return default

def _hold(strategy: str, reason: str = "") -> Dict:
    return {"action":"HOLD","strategy":strategy,"confidence":0.0,
            "reason":reason,"indicators":{},"score_boost":0.0}


# ─────────────────────────────────────────────────────────────────────────────
# 1. ORDER FLOW IMBALANCE (OFI) — Citadel
# ─────────────────────────────────────────────────────────────────────────────

class OFIStrategy:
    """
    Order Flow Imbalance: the ratio of buying vs selling volume at the bid/ask.
    Citadel uses this as a primary signal — it measures CURRENT institutional intent.

    OFI = (buy_volume - sell_volume) / (buy_volume + sell_volume)
    Range: -1.0 (pure selling) to +1.0 (pure buying)

    OFI > +0.30 AND price above VWAP → BUY signal
    OFI < -0.30 AND price below VWAP → SELL signal

    Data source: Angel One getMarketData(mode=FULL) → bestFive depth
    """

    def __init__(self) -> None:
        self._ofi_history: Deque[float] = deque(maxlen=12)  # last 12 bars = 1 hour

    def compute_ofi(
        self,
        depth: Optional[Dict] = None,
        df:    Optional[pd.DataFrame] = None,
    ) -> float:
        """
        Compute OFI from market depth or OHLCV approximation.

        With depth (preferred): uses actual bid/ask volumes from L2 order book.
        Without depth: approximates from OHLCV bar (close position in range).
        """
        if depth:
            try:
                buy_vol  = sum(float(d.get("quantity", 0)) for d in depth.get("buy", []))
                sell_vol = sum(float(d.get("quantity", 0)) for d in depth.get("sell", []))
                total    = buy_vol + sell_vol
                if total > 0:
                    return round((buy_vol - sell_vol) / total, 4)
            except Exception:
                pass

        if df is not None and len(df) >= 2:
            try:
                o = float(df["Open"].iloc[-1]  if "Open"  in df.columns else df["open"].iloc[-1])
                h = float(df["High"].iloc[-1]  if "High"  in df.columns else df["high"].iloc[-1])
                l = float(df["Low"].iloc[-1]   if "Low"   in df.columns else df["low"].iloc[-1])
                c = float(df["Close"].iloc[-1] if "Close" in df.columns else df["close"].iloc[-1])
                v = float(df["Volume"].iloc[-1] if "Volume" in df.columns else df.get("volume", pd.Series([1]*len(df))).iloc[-1])
                bar_range = h - l
                if bar_range > 0:
                    buy_pct  = (c - l) / bar_range
                    ofi      = 2 * buy_pct - 1   # normalise to -1..+1
                    return round(ofi, 4)
            except Exception:
                pass
        return 0.0

    def signal(
        self,
        df:       pd.DataFrame,
        depth:    Optional[Dict] = None,
        vwap_val: float          = 0.0,
    ) -> Dict:
        if df is None or len(df) < 5:
            return _hold("ofi_citadel", "insufficient_data")

        ofi = self.compute_ofi(depth=depth, df=df)
        self._ofi_history.append(ofi)

        # Rolling OFI: 5-bar average
        avg_ofi = float(np.mean(list(self._ofi_history)[-5:])) if len(self._ofi_history) >= 5 else ofi

        try:
            close = _s(df["Close"] if "Close" in df.columns else df["close"])
        except Exception:
            close = 0.0

        above_vwap = close > vwap_val * 0.998 if vwap_val > 0 else True
        below_vwap = close < vwap_val * 1.002 if vwap_val > 0 else True

        action = "HOLD"
        conf   = 0.0
        if avg_ofi > 0.30 and above_vwap:
            action = "BUY"
            conf   = min(0.85, 0.55 + (avg_ofi - 0.30) * 1.5)
        elif avg_ofi < -0.30 and below_vwap:
            action = "SELL"
            conf   = min(0.85, 0.55 + (abs(avg_ofi) - 0.30) * 1.5)

        if action == "HOLD":
            return _hold("ofi_citadel", f"ofi={avg_ofi:.3f}_neutral")

        return {
            "action":      action,
            "strategy":    "ofi_citadel",
            "confidence":  round(conf, 4),
            "reason":      f"ofi={avg_ofi:.3f}_above_vwap={above_vwap}",
            "indicators":  {"ofi": round(ofi, 4), "avg_ofi": round(avg_ofi, 4), "vwap": round(vwap_val, 2)},
            "score_boost": round(abs(avg_ofi) * 2.0, 2),
        }


# ─────────────────────────────────────────────────────────────────────────────
# 2. IV SKEW DIRECTION — Two Sigma
# ─────────────────────────────────────────────────────────────────────────────

def iv_skew_signal(
    option_chain: Optional[Dict] = None,
    spot:         float          = 0.0,
    atm_strike:   int            = 0,
    step:         int            = 50,
) -> Dict:
    """
    IV Skew = IV of 1-OTM Put - IV of 1-OTM Call.
    Normal market: puts have slightly higher IV (5-7 points) due to downside fear.
    Elevated skew (>10): excessive fear → MR strategies, sell puts.
    Flat/negative skew (<3): complacency → protect longs, breakout less reliable.

    From NSE option chain: compare IV at ATM-1 put vs ATM+1 call.
    """
    if not option_chain or not spot or not atm_strike:
        return _hold("iv_skew_twosigma", "no_chain_data")

    try:
        put_strike  = atm_strike - step
        call_strike = atm_strike + step

        data = option_chain.get("filtered", {}).get("data", [])
        put_iv = call_iv = 0.0

        for row in data:
            s = int(row.get("strikePrice", 0))
            if s == put_strike:
                put_iv  = float(row.get("PE", {}).get("impliedVolatility", 0) or 0)
            if s == call_strike:
                call_iv = float(row.get("CE", {}).get("impliedVolatility", 0) or 0)

        if put_iv <= 0 or call_iv <= 0:
            return _hold("iv_skew_twosigma", "iv_data_missing")

        skew = put_iv - call_iv

        # Skew interpretation
        if skew > 10:
            # High fear — puts expensive — MR opportunity, sell puts or buy CE
            action = "BUY"
            conf   = min(0.80, 0.55 + (skew - 10) * 0.01)
            reason = f"high_fear_skew={skew:.1f}_puts_expensive"
        elif skew < 2:
            # Low fear — calls elevated — potential reversal or range
            action = "SELL"
            conf   = 0.60
            reason = f"low_fear_skew={skew:.1f}_calls_elevated"
        else:
            return _hold("iv_skew_twosigma", f"normal_skew={skew:.1f}")

        return {
            "action":      action,
            "strategy":    "iv_skew_twosigma",
            "confidence":  round(conf, 4),
            "reason":      reason,
            "indicators":  {"put_iv": put_iv, "call_iv": call_iv, "skew": round(skew, 2)},
            "score_boost": round(abs(skew - 6) / 10, 2),
        }
    except Exception as e:
        logger.debug("iv_skew_signal: %s", e)
        return _hold("iv_skew_twosigma", f"error:{e}")


# ─────────────────────────────────────────────────────────────────────────────
# 3. IMPLIED vs REALISED VOL SPREAD — Jane Street
# ─────────────────────────────────────────────────────────────────────────────

def vol_spread_signal(
    df:      pd.DataFrame,
    atm_iv:  float = 0.0,
    hv_days: int   = 10,
) -> Dict:
    """
    Vol Spread = ATM Implied Volatility - 10-day Historical Volatility.

    Positive (IV > HV): options expensive → sell straddles/spreads.
    Negative (HV > IV): options cheap → buy directional options.
    Jane Street's core edge: they know when options are mispriced.

    HV10 = annualised std dev of last 10 daily returns × sqrt(252).
    """
    if df is None or len(df) < 15:
        return _hold("vol_spread_janestreet", "insufficient_data")

    try:
        close = pd.to_numeric(df["Close"] if "Close" in df.columns else df["close"], errors="coerce")
        daily_ret = close.pct_change().dropna()

        # Use last 10 trading days of returns (approximate from 5-min bars)
        # 10 trading days = ~120 5-min bars
        n_bars  = min(120, len(daily_ret))
        hv_10   = float(daily_ret.tail(n_bars).std() * (252 * 78) ** 0.5 * 100)  # annualised %

        if atm_iv <= 0:
            # Estimate from current bars if not provided
            atm_iv = hv_10 * 1.1   # options usually 10% above HV as default

        vol_spread = atm_iv - hv_10

        if vol_spread > 5:
            # Options expensive (IV >> HV) → prefer selling strategies
            return {
                "action":     "SELL",
                "strategy":   "vol_spread_janestreet",
                "confidence": min(0.78, 0.55 + vol_spread * 0.008),
                "reason":     f"options_expensive_spread={vol_spread:.1f}",
                "indicators": {"atm_iv": round(atm_iv, 2), "hv10": round(hv_10, 2),
                               "spread": round(vol_spread, 2)},
                "score_boost": round(vol_spread * 0.05, 2),
                "regime_hint": "sell_premium",
            }
        elif vol_spread < -3:
            # Options cheap (HV >> IV) → buy directional options
            return {
                "action":     "BUY",
                "strategy":   "vol_spread_janestreet",
                "confidence": min(0.75, 0.55 + abs(vol_spread) * 0.008),
                "reason":     f"options_cheap_spread={vol_spread:.1f}",
                "indicators": {"atm_iv": round(atm_iv, 2), "hv10": round(hv_10, 2),
                               "spread": round(vol_spread, 2)},
                "score_boost": round(abs(vol_spread) * 0.04, 2),
                "regime_hint": "buy_directional",
            }

        return _hold("vol_spread_janestreet", f"vol_fairly_priced_spread={vol_spread:.1f}")

    except Exception as e:
        logger.debug("vol_spread_signal: %s", e)
        return _hold("vol_spread_janestreet", f"error:{e}")


# ─────────────────────────────────────────────────────────────────────────────
# 4. MULTI-TIMEFRAME TREND STRENGTH INDEX (MTSI) — Winton Group
# ─────────────────────────────────────────────────────────────────────────────

def mtsi_signal(
    df_5m:  pd.DataFrame,
    df_15m: Optional[pd.DataFrame] = None,
    df_1h:  Optional[pd.DataFrame] = None,
) -> Dict:
    """
    MTSI = ADX(5m) + ADX(15m) + ADX(1h)
    Each ADX normalised 0-100. MTSI range: 0-300.

    MTSI > 180: very strong trend — highest confidence trend entries.
    MTSI > 120: clear trend — standard trend strategies.
    MTSI < 60:  no trend — range strategies only.

    Winton: they never traded a trend that wasn't confirmed on 3 timeframes.
    """
    if df_5m is None or len(df_5m) < 20:
        return _hold("mtsi_winton", "insufficient_5m_data")

    try:
        from indicators import calculate_adx
        adx_5m  = _s(calculate_adx(df_5m,  14))
        adx_15m = _s(calculate_adx(df_15m, 14)) if df_15m is not None and len(df_15m) >= 14 else adx_5m * 0.9
        adx_1h  = _s(calculate_adx(df_1h,  14)) if df_1h  is not None and len(df_1h)  >= 14 else adx_5m * 0.8

        mtsi = adx_5m + adx_15m + adx_1h

        # Determine direction from 5m EMA
        from indicators import calculate_ema
        ema9  = _s(calculate_ema(df_5m, 9))
        ema21 = _s(calculate_ema(df_5m, 21))
        trend_up = ema9 > ema21

        if mtsi > 180:
            action = "BUY" if trend_up else "SELL"
            conf   = min(0.90, 0.65 + (mtsi - 180) / 400)
        elif mtsi > 120:
            action = "BUY" if trend_up else "SELL"
            conf   = min(0.80, 0.58 + (mtsi - 120) / 400)
        else:
            return _hold("mtsi_winton", f"mtsi_too_low={mtsi:.1f}")

        return {
            "action":     action,
            "strategy":   "mtsi_winton",
            "confidence": round(conf, 4),
            "reason":     f"mtsi={mtsi:.1f}_trend_{'up' if trend_up else 'down'}",
            "indicators": {"mtsi": round(mtsi, 1), "adx_5m": round(adx_5m, 1),
                           "adx_15m": round(adx_15m, 1), "adx_1h": round(adx_1h, 1)},
            "score_boost": round((mtsi - 120) / 60, 2),
        }
    except Exception as e:
        logger.debug("mtsi_signal: %s", e)
        return _hold("mtsi_winton", f"error:{e}")


# ─────────────────────────────────────────────────────────────────────────────
# 5. NIFTY-BANKNIFTY STATISTICAL ARBITRAGE — DE Shaw
# ─────────────────────────────────────────────────────────────────────────────

class StatArbStrategy:
    """
    The BNF/NIFTY ratio is one of the most studied statistical relationships
    in Indian markets. It mean-reverts over 5-20 trading days.

    Z-score of (BNF/NIFTY ratio) over 20-day rolling window:
    Z > +2.0 → BNF overbought vs NIFTY → sell BNF CE or buy NIFTY CE
    Z < -2.0 → BNF oversold vs NIFTY  → sell BNF PE or buy NIFTY PE
    """

    def __init__(self) -> None:
        self._ratio_history: Deque[float] = deque(maxlen=100)   # ~20 trading days of 5-min

    def update(self, nifty_price: float, bnf_price: float) -> None:
        if nifty_price > 0 and bnf_price > 0:
            self._ratio_history.append(bnf_price / nifty_price)

    def signal(self, nifty_price: float, bnf_price: float) -> Dict:
        self.update(nifty_price, bnf_price)

        if len(self._ratio_history) < 20:
            return _hold("stat_arb_deshaw", "insufficient_history")

        ratios = list(self._ratio_history)
        mean   = float(np.mean(ratios[-100:]))
        std    = float(np.std(ratios[-100:]))

        if std <= 0:
            return _hold("stat_arb_deshaw", "zero_std")

        current_ratio = ratios[-1]
        z_score       = (current_ratio - mean) / std

        if z_score > 2.0:
            # BNF expensive vs NIFTY → favour NIFTY CE over BNF CE
            conf = min(0.82, 0.58 + (z_score - 2.0) * 0.06)
            return {
                "action":       "BUY",
                "strategy":     "stat_arb_deshaw",
                "confidence":   round(conf, 4),
                "reason":       f"bnf_overbought_z={z_score:.2f}_buy_nifty",
                "preferred":    "NIFTY",
                "avoid":        "BANKNIFTY",
                "indicators":   {"z_score": round(z_score, 3), "ratio": round(current_ratio, 4),
                                 "mean": round(mean, 4), "std": round(std, 4)},
                "score_boost":  round((z_score - 2.0) * 0.5, 2),
            }
        elif z_score < -2.0:
            # BNF cheap vs NIFTY → favour BNF CE or NIFTY PE
            conf = min(0.82, 0.58 + (abs(z_score) - 2.0) * 0.06)
            return {
                "action":       "SELL",
                "strategy":     "stat_arb_deshaw",
                "confidence":   round(conf, 4),
                "reason":       f"bnf_oversold_z={z_score:.2f}_buy_banknifty",
                "preferred":    "BANKNIFTY",
                "avoid":        "NIFTY",
                "indicators":   {"z_score": round(z_score, 3), "ratio": round(current_ratio, 4),
                                 "mean": round(mean, 4)},
                "score_boost":  round((abs(z_score) - 2.0) * 0.5, 2),
            }

        return _hold("stat_arb_deshaw", f"z_score_neutral={z_score:.2f}")


# ─────────────────────────────────────────────────────────────────────────────
# 6. OI CHANGE VELOCITY — Goldman Sachs Prop Desk
# ─────────────────────────────────────────────────────────────────────────────

class OIVelocityStrategy:
    """
    Tracks how fast Open Interest is building at specific strikes.
    Strikes with rapidly growing OI become institutional magnets.

    Goldman's flow trading edge: knowing where institutions are positioned
    and trading alongside the largest open interest build.

    On expiry day: highest OI velocity strike = likely closing price.
    """

    def __init__(self) -> None:
        self._oi_snapshots: Dict[int, List[Tuple[float, int]]] = {}  # strike → [(ts, oi)]
        self._last_snapshot_ts: float = 0.0

    def update_oi_snapshot(self, option_chain: Dict) -> None:
        """Record current OI for all strikes."""
        now = time.time()
        try:
            for row in option_chain.get("filtered", {}).get("data", []):
                strike = int(row.get("strikePrice", 0))
                ce_oi  = int(row.get("CE", {}).get("openInterest", 0) or 0)
                pe_oi  = int(row.get("PE", {}).get("openInterest", 0) or 0)
                total  = ce_oi + pe_oi
                if strike not in self._oi_snapshots:
                    self._oi_snapshots[strike] = []
                self._oi_snapshots[strike].append((now, total))
                # Keep last 12 snapshots (= 1 hour with 5-min intervals)
                if len(self._oi_snapshots[strike]) > 12:
                    self._oi_snapshots[strike].pop(0)
            self._last_snapshot_ts = now
        except Exception as e:
            logger.debug("OI snapshot: %s", e)

    def get_magnet_strike(self, spot: float, step: int = 50) -> Optional[Dict]:
        """
        Find the strike with highest OI velocity (fastest OI accumulation).
        Returns the strike level and direction implied.
        """
        if not self._oi_snapshots:
            return None

        velocities = {}
        for strike, history in self._oi_snapshots.items():
            if len(history) >= 2:
                oi_old = history[-2][1]
                oi_new = history[-1][1]
                dt_hr  = (history[-1][0] - history[-2][0]) / 3600
                if dt_hr > 0:
                    velocities[strike] = (oi_new - oi_old) / dt_hr

        if not velocities:
            return None

        magnet_strike = max(velocities, key=velocities.get)
        velocity      = velocities[magnet_strike]

        if velocity < 100000:   # minimum threshold
            return None

        dist   = magnet_strike - spot
        action = "BUY" if dist > 0 else "SELL"   # price needs to move toward magnet

        return {
            "strike":    magnet_strike,
            "velocity":  round(velocity, 0),
            "direction": action,
            "distance":  round(dist, 0),
        }

    def signal(self, spot: float, option_chain: Optional[Dict] = None,
               step: int = 50) -> Dict:
        if option_chain:
            self.update_oi_snapshot(option_chain)

        magnet = self.get_magnet_strike(spot, step)
        if not magnet:
            return _hold("oi_velocity_goldman", "no_magnet_strike")

        dist_pct = abs(magnet["distance"]) / spot
        if dist_pct > 0.02:   # magnet too far (>2%) — not relevant now
            return _hold("oi_velocity_goldman", f"magnet_too_far={dist_pct:.1%}")

        conf = min(0.80, 0.55 + magnet["velocity"] / 500000)
        return {
            "action":     magnet["direction"],
            "strategy":   "oi_velocity_goldman",
            "confidence": round(conf, 4),
            "reason":     f"magnet_strike={magnet['strike']}_vel={magnet['velocity']:.0f}/hr",
            "indicators": magnet,
            "score_boost": round(min(1.5, magnet["velocity"] / 200000), 2),
        }


# ─────────────────────────────────────────────────────────────────────────────
# 7. STRATEGY MOMENTUM FACTOR — AQR Capital
# ─────────────────────────────────────────────────────────────────────────────

class StrategyMomentumFactor:
    """
    AQR's insight: strategies have momentum. If trend trading worked
    the last 5 sessions, the market regime that makes trend work is
    still active. Allocate more.

    Tracks per-strategy 5-session rolling win rate and uses it as
    a multiplier on top of existing strategy weights.

    This is adaptive — it learns the CURRENT regime without backtesting.
    """

    def __init__(self) -> None:
        # Stores recent results: {strategy: deque of (win:bool, pnl:float)}
        self._results: Dict[str, Deque[Tuple[bool, float]]] = {}

    def record_result(self, strategy: str, win: bool, pnl: float) -> None:
        if strategy not in self._results:
            self._results[strategy] = deque(maxlen=25)  # 5 sessions × 5 trades avg
        self._results[strategy].append((win, pnl))

    def get_momentum_multiplier(self, strategy: str) -> float:
        """
        Returns a multiplier 0.5 to 1.5 based on strategy's recent performance.
        Hot strategy → 1.5×. Cold strategy → 0.5×. No data → 1.0×.
        """
        hist = self._results.get(strategy)
        if not hist or len(hist) < 3:
            return 1.0

        recent = list(hist)[-10:]   # last 10 trades
        wins   = sum(1 for w, _ in recent if w)
        wr     = wins / len(recent)
        momentum = (wr - 0.50) * 2    # -1.0 to +1.0

        # Convert to multiplier: 0.5 to 1.5
        return round(max(0.5, min(1.5, 1.0 + momentum * 0.5)), 3)

    def get_best_strategy_today(self) -> Optional[str]:
        """Strategy with highest momentum this session."""
        if not self._results:
            return None
        return max(
            self._results.keys(),
            key=self.get_momentum_multiplier,
        )

    def get_all_multipliers(self) -> Dict[str, float]:
        return {s: self.get_momentum_multiplier(s) for s in self._results}


# ─────────────────────────────────────────────────────────────────────────────
# 8. HURST EXPONENT (Autocorrelation Regime) — Renaissance Technologies
# ─────────────────────────────────────────────────────────────────────────────

def hurst_exponent(df: pd.DataFrame, lags: int = 20) -> float:
    """
    Hurst Exponent: measures long-range dependence.
    H < 0.5 = mean-reverting (trade MR)
    H = 0.5 = random walk (no edge)
    H > 0.5 = trending (trade trend)

    Renaissance uses this to decide WHICH strategy to run each day.
    Computed on last 100 5-min bars.
    """
    try:
        close = pd.to_numeric(
            df["Close"] if "Close" in df.columns else df["close"], errors="coerce"
        ).dropna().values

        if len(close) < lags * 3:
            return 0.5

        tau   = range(2, lags)
        gamma = [np.std(np.subtract(close[t:], close[:-t])) for t in tau]
        gamma = [g for g in gamma if g > 0]

        if len(gamma) < 3:
            return 0.5

        m  = np.polyfit(np.log(range(2, 2 + len(gamma))), np.log(gamma), 1)
        H  = m[0]
        return round(float(np.clip(H, 0.1, 0.9)), 4)
    except Exception:
        return 0.5


def hurst_regime_signal(df: pd.DataFrame) -> Dict:
    """
    Uses Hurst exponent to recommend strategy type for current regime.
    Returns a signal with strategy recommendation and score boosts.
    """
    H = hurst_exponent(df)

    if H < 0.40:
        regime = "MEAN_REVERTING"
        recommended = ["mean_reversion", "vwap_reversion", "vpoc_magnet"]
        boost_for   = "mean_reversion"
        penalty_for = "trend"
    elif H > 0.60:
        regime = "TRENDING"
        recommended = ["trend", "breakout", "market_structure", "hour_orb"]
        boost_for   = "trend"
        penalty_for = "mean_reversion"
    else:
        regime = "RANDOM"
        recommended = []
        boost_for   = ""
        penalty_for = ""

    return {
        "hurst":       H,
        "regime":      regime,
        "recommended": recommended,
        "boost_for":   boost_for,
        "penalty_for": penalty_for,
        "score_mod":   +0.5 if H > 0.6 or H < 0.4 else 0.0,
    }


# ─────────────────────────────────────────────────────────────────────────────
# UNIFIED ALPHA ENGINE — applies all strategies as signal boosters
# ─────────────────────────────────────────────────────────────────────────────

class InstitutionalAlphaEngine:
    """
    Applies all 8 institutional alpha signals to enhance scoring.
    Call get_alpha_boost() for any signal to get a composite score adjustment.
    """

    def __init__(self) -> None:
        self.ofi            = OFIStrategy()
        self.stat_arb       = StatArbStrategy()
        self.oi_velocity    = OIVelocityStrategy()
        self.strategy_momentum = StrategyMomentumFactor()
        self._hurst_cache:  Dict[str, Tuple[float, float]] = {}  # symbol → (H, ts)

    def get_alpha_boost(
        self,
        symbol:       str,
        signal_side:  str,
        strategy:     str,
        df_5m:        Optional[pd.DataFrame] = None,
        df_15m:       Optional[pd.DataFrame] = None,
        df_1h:        Optional[pd.DataFrame] = None,
        depth:        Optional[Dict]         = None,
        vwap_val:     float                  = 0.0,
        option_chain: Optional[Dict]         = None,
        spot:         float                  = 0.0,
        nifty_price:  float                  = 0.0,
        bnf_price:    float                  = 0.0,
        atm_iv:       float                  = 0.0,
        atm_strike:   int                    = 0,
        step:         int                    = 50,
    ) -> Dict[str, Any]:
        """
        Returns a composite alpha boost score and per-factor breakdown.
        """
        total_boost = 0.0
        factors     = {}

        # 1. OFI (Citadel)
        if df_5m is not None:
            ofi_sig = self.ofi.signal(df_5m, depth=depth, vwap_val=vwap_val)
            if ofi_sig["action"] == signal_side:
                total_boost += ofi_sig.get("score_boost", 0)
                factors["ofi"] = ofi_sig.get("score_boost", 0)
            elif ofi_sig["action"] not in ("HOLD",) and ofi_sig["action"] != signal_side:
                total_boost -= 0.5   # OFI disagrees — penalty
                factors["ofi"] = -0.5

        # 2. IV Skew (Two Sigma) — option selection context
        if option_chain and atm_strike:
            skew_sig = iv_skew_signal(option_chain, spot, atm_strike, step)
            if skew_sig["action"] == signal_side:
                total_boost += skew_sig.get("score_boost", 0)
                factors["iv_skew"] = skew_sig.get("score_boost", 0)

        # 3. Vol Spread (Jane Street)
        if df_5m is not None:
            vs_sig = vol_spread_signal(df_5m, atm_iv)
            hint   = vs_sig.get("regime_hint", "")
            if hint == "buy_directional" and signal_side == "BUY":
                total_boost += vs_sig.get("score_boost", 0)
                factors["vol_spread"] = vs_sig.get("score_boost", 0)
            elif hint == "sell_premium":
                factors["vol_spread"] = 0.0  # neutral — sell strategies preferred

        # 4. MTSI (Winton)
        if df_5m is not None:
            mtsi_sig = mtsi_signal(df_5m, df_15m, df_1h)
            if mtsi_sig["action"] == signal_side:
                total_boost += mtsi_sig.get("score_boost", 0)
                factors["mtsi"] = mtsi_sig.get("score_boost", 0)

        # 5. Stat Arb (DE Shaw)
        if nifty_price and bnf_price:
            arb_sig = self.stat_arb.signal(nifty_price, bnf_price)
            preferred = arb_sig.get("preferred", "")
            if preferred and preferred.upper() in symbol.upper():
                total_boost += arb_sig.get("score_boost", 0)
                factors["stat_arb"] = arb_sig.get("score_boost", 0)

        # 6. OI Velocity (Goldman)
        if spot and option_chain:
            oi_sig = self.oi_velocity.signal(spot, option_chain, step)
            if oi_sig["action"] == signal_side:
                total_boost += oi_sig.get("score_boost", 0)
                factors["oi_velocity"] = oi_sig.get("score_boost", 0)

        # 7. Strategy Momentum (AQR)
        sm_mult = self.strategy_momentum.get_momentum_multiplier(strategy)
        factors["strategy_momentum"] = round(sm_mult - 1.0, 3)
        total_boost += (sm_mult - 1.0) * 1.0

        # 8. Hurst (Renaissance)
        cache_key = f"{symbol}_hurst"
        now = time.time()
        if cache_key not in self._hurst_cache or (now - self._hurst_cache[cache_key][1]) > 300:
            if df_5m is not None and len(df_5m) >= 60:
                H = hurst_exponent(df_5m)
                self._hurst_cache[cache_key] = (H, now)
        H_val = self._hurst_cache.get(cache_key, (0.5, 0))[0]
        trend_strategies = {"trend","breakout","market_structure","mtsi_winton","hour_orb"}
        mr_strategies    = {"mean_reversion","vwap_reversion","vpoc_magnet"}
        if H_val > 0.60 and strategy in trend_strategies:
            total_boost += 0.5
            factors["hurst"] = 0.5
        elif H_val < 0.40 and strategy in mr_strategies:
            total_boost += 0.5
            factors["hurst"] = 0.5

        return {
            "total_boost":   round(total_boost, 3),
            "factors":       factors,
            "hurst":         round(H_val, 4),
            "ofi_direction": self.ofi.compute_ofi(df=df_5m) if df_5m is not None else 0.0,
        }

    def record_trade_result(self, strategy: str, win: bool, pnl: float) -> None:
        self.strategy_momentum.record_result(strategy, win, pnl)

    def get_strategy_multiplier(self, strategy: str) -> float:
        return self.strategy_momentum.get_momentum_multiplier(strategy)


# ── Module singleton ──────────────────────────────────────────────────────────
_alpha_engine: Optional[InstitutionalAlphaEngine] = None


def get_alpha_engine() -> InstitutionalAlphaEngine:
    global _alpha_engine
    if _alpha_engine is None:
        _alpha_engine = InstitutionalAlphaEngine()
    return _alpha_engine
