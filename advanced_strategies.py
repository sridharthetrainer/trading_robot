"""
advanced_strategies.py

High-priority missing strategies — all production-ready.

1. RSI Divergence           — strongest reversal signal in technical analysis
2. Gap Fill Strategy        — 65% of gaps fill same day
3. Relative Strength        — trade leaders, not laggards
4. Ichimoku Cloud           — Asian institutional trend filter
5. Expiry Week Pattern      — NSE expiry cycle predictability
6. Global Macro Context     — S&P500, DXY, Crude vs NIFTY
7. Kelly Position Sizing    — mathematically optimal bet sizing
8. Volume Profile Breakout  — VAH/VAL institutional levels
9. Stock F&O Support        — correct lot sizes + monthly expiry

All strategies return dict compatible with signal_engine.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


def _s(series: pd.Series, default: float = 0.0) -> float:
    try: v = series.iloc[-1]; return float(v) if pd.notna(v) else default
    except: return default

def _close(df: pd.DataFrame) -> float:
    try: c = df["Close"] if "Close" in df.columns else df["close"]; return float(c.iloc[-1])
    except: return 0.0

def _hold(strategy: str, reason: str = "") -> Dict:
    return {"action":"HOLD","strategy":strategy,"confidence":0.0,
            "reason":reason,"indicators":{},"score_boost":0.0}


# ─────────────────────────────────────────────────────────────────────────────
# 1. RSI DIVERGENCE STRATEGY
# ─────────────────────────────────────────────────────────────────────────────

def rsi_divergence_signal(df: pd.DataFrame) -> Dict:
    """
    RSI Divergence — one of the most reliable reversal signals.

    Bullish: Price new low + RSI higher low → BUY (weakening sellers)
    Bearish: Price new high + RSI lower high → SELL (weakening buyers)

    Best in RANGE regime. Confirmed by low volume on the divergence bar.
    Works on ALL symbols: indices, stocks, everything.
    """
    if df is None or len(df) < 22:
        return _hold("rsi_divergence", "insufficient_data")
    try:
        from indicators import calculate_rsi_divergence, calculate_volume_ratio
        div_s = calculate_rsi_divergence(df, rsi_period=14, lookback=20)
        last  = int(_s(div_s, 0))

        if last == 0:
            return _hold("rsi_divergence", "no_divergence")

        vr = _s(calculate_volume_ratio(df, 20))
        # Lower volume on divergence bar = more reliable (exhaustion)
        vol_factor = 0.10 if vr < 0.8 else -0.05 if vr > 1.5 else 0.0

        action = "BUY" if last == 1 else "SELL"
        conf   = min(0.82, 0.65 + vol_factor)

        from indicators import calculate_rsi
        current_rsi = _s(calculate_rsi(df, 14))

        # Extra confidence: extreme RSI on divergence
        if action == "BUY" and current_rsi < 35:
            conf += 0.08
        elif action == "SELL" and current_rsi > 65:
            conf += 0.08

        return {
            "action":     action,
            "strategy":   "rsi_divergence",
            "confidence": round(min(conf, 0.88), 4),
            "reason":     f"{'bullish' if last==1 else 'bearish'}_divergence_rsi={current_rsi:.1f}",
            "indicators": {"divergence": last, "rsi": round(current_rsi, 1), "vol_ratio": round(vr, 2)},
            "score_boost": 1.0,
        }
    except Exception as e:
        return _hold("rsi_divergence", f"error:{e}")


# ─────────────────────────────────────────────────────────────────────────────
# 2. GAP FILL STRATEGY
# ─────────────────────────────────────────────────────────────────────────────

def gap_fill_signal(
    df:       pd.DataFrame,
    df_daily: Optional[pd.DataFrame] = None,
    gap_min:  float = 0.005,   # minimum 0.5% gap to trade
    gap_max:  float = 0.030,   # maximum 3% gap (larger = earnings, avoid)
) -> Dict:
    """
    Gap Fill Strategy: When a stock/index gaps > 0.5% at open,
    it fills back 65% of the time on the same day.

    Rules:
    - Gap up > 0.5% at 9:15 → SELL (fade the gap, expect fill)
    - Gap down > 0.5% at 9:15 → BUY (fade the gap, expect fill)
    - Gap > 3% = likely news/earnings → skip (too risky)
    - Only valid 9:20-9:45 (first 30 minutes)
    - Target = previous close (full gap fill)
    - Stop = 1 ATR beyond the gap
    """
    if df is None or len(df) < 5:
        return _hold("gap_fill", "insufficient_data")

    try:
        now_t = datetime.now().time()
        from datetime import time as dtime
        if not (dtime(9, 20) <= now_t <= dtime(9, 45)):
            return _hold("gap_fill", "outside_gap_fill_window")

        close_col = "Close" if "Close" in df.columns else "close"
        open_col  = "Open"  if "Open"  in df.columns else "open"

        # Current open and previous close
        today_open = float(df[open_col].iloc[0]) if len(df) > 0 else 0.0
        prev_close = float(df[close_col].iloc[-2]) if len(df) >= 2 else 0.0

        if today_open <= 0 or prev_close <= 0:
            return _hold("gap_fill", "no_price_data")

        gap_pct = (today_open - prev_close) / prev_close

        if abs(gap_pct) < gap_min:
            return _hold("gap_fill", f"gap_too_small_{gap_pct:.2%}")
        if abs(gap_pct) > gap_max:
            return _hold("gap_fill", f"gap_too_large_{gap_pct:.2%}_likely_news")

        from indicators import calculate_atr
        atr_v = _s(calculate_atr(df, 14), prev_close * 0.005)

        if gap_pct > 0:
            # Gap UP → fade it → SELL
            action = "SELL"
            stop   = today_open + 1.0 * atr_v
            target = prev_close   # full gap fill
        else:
            # Gap DOWN → fade it → BUY
            action = "BUY"
            stop   = today_open - 1.0 * atr_v
            target = prev_close

        conf = min(0.78, 0.60 + abs(gap_pct) * 5)

        return {
            "action":     action,
            "strategy":   "gap_fill",
            "confidence": round(conf, 4),
            "reason":     f"gap_{gap_pct:+.2%}_fade",
            "indicators": {
                "gap_pct":    round(gap_pct, 4),
                "today_open": round(today_open, 2),
                "prev_close": round(prev_close, 2),
                "target":     round(target, 2),
                "stop":       round(stop, 2),
            },
            "score_boost": round(abs(gap_pct) * 20, 2),
        }
    except Exception as e:
        return _hold("gap_fill", f"error:{e}")


# ─────────────────────────────────────────────────────────────────────────────
# 3. RELATIVE STRENGTH vs NIFTY
# ─────────────────────────────────────────────────────────────────────────────

class RelativeStrengthFilter:
    """
    Relative Strength: is this symbol outperforming NIFTY?

    If RELIANCE rose 2% when NIFTY rose 0.5% → RS = 4.0 (strong leader)
    If HDFCBANK fell 1% when NIFTY rose 0.5% → RS = -2.0 (laggard, avoid)

    Used as a signal router: only trade BUY signals on RS > 1.5.
    Used as a signal penalty: fade SELL signals on RS < 0.5 (already weak).

    Works for all 194 stocks + indices.
    """

    def __init__(self) -> None:
        self._nifty_cache: Optional[pd.DataFrame] = None
        self._nifty_ts: float = 0.0

    def get_rs_score(
        self,
        symbol:     str,
        df_symbol:  pd.DataFrame,
        df_nifty:   Optional[pd.DataFrame] = None,
        period:     int = 10,
    ) -> float:
        """Returns RS score. > 1.5 = strong leader. < 0.5 = weak laggard."""
        if df_symbol is None or len(df_symbol) < period + 2:
            return 1.0   # neutral default

        try:
            from indicators import calculate_relative_strength
            if df_nifty is None or len(df_nifty) < period + 2:
                return 1.0
            rs = calculate_relative_strength(df_symbol, df_nifty, period)
            val = _s(rs, 1.0)
            return round(float(val), 4)
        except Exception:
            return 1.0

    def get_score_boost(
        self,
        symbol:    str,
        action:    str,
        df_symbol: pd.DataFrame,
        df_nifty:  Optional[pd.DataFrame] = None,
    ) -> float:
        """
        Score boost for BUY signals on strong RS stocks.
        Score penalty for BUY signals on weak RS stocks.
        """
        if symbol.upper() in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"):
            return 0.0   # RS doesn't apply to indices vs themselves

        rs = self.get_rs_score(symbol, df_symbol, df_nifty)

        if action == "BUY":
            if rs >= 2.0:   return  0.60   # strong leader — boost
            if rs >= 1.5:   return  0.30
            if rs >= 1.0:   return  0.0
            if rs < 0.7:    return -0.30   # laggard — penalise BUY
            if rs < 0.5:    return -0.60
        elif action == "SELL":
            if rs <= 0.5:   return  0.60   # weak stock — boost SELL
            if rs <= 0.7:   return  0.30
            if rs >= 1.5:   return -0.30   # strong stock — penalise SELL

        return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 4. ICHIMOKU CLOUD STRATEGY
# ─────────────────────────────────────────────────────────────────────────────

def ichimoku_signal(df: pd.DataFrame) -> Dict:
    """
    Ichimoku Cloud — Japan's most widely used institutional trend indicator.

    Strong BUY: price above cloud + TK cross (tenkan > kijun) + chikou above price
    Strong SELL: price below cloud + tenkan < kijun + chikou below price
    Weak: price inside cloud = avoid

    Works best on indices (NIFTY, BANKNIFTY) and large-cap stocks.
    15-min and daily charts give strongest signals.
    """
    if df is None or len(df) < 55:
        return _hold("ichimoku", "need_55_bars")
    try:
        from indicators import calculate_ichimoku
        ichi = calculate_ichimoku(df)

        tenkan = _s(ichi["tenkan_sen"])
        kijun  = _s(ichi["kijun_sen"])
        sA     = _s(ichi["senkou_a"])
        sB     = _s(ichi["senkou_b"])
        close  = _close(df)

        if any(v <= 0 for v in [tenkan, kijun, sA, sB, close]):
            return _hold("ichimoku", "invalid_values")

        cloud_top  = max(sA, sB)
        cloud_bot  = min(sA, sB)
        in_cloud   = cloud_bot <= close <= cloud_top
        above_cloud = close > cloud_top
        below_cloud = close < cloud_bot
        tk_bullish  = tenkan > kijun
        bullish_cloud = sA > sB   # green cloud = bullish

        if in_cloud:
            return _hold("ichimoku", "price_inside_cloud_neutral")

        action = "HOLD"
        conf   = 0.0

        if above_cloud and tk_bullish and bullish_cloud:
            action = "BUY"
            # Distance above cloud = conviction
            dist_pct = (close - cloud_top) / cloud_top
            conf = min(0.85, 0.60 + dist_pct * 10)
        elif below_cloud and not tk_bullish and not bullish_cloud:
            action = "SELL"
            dist_pct = (cloud_bot - close) / cloud_bot
            conf = min(0.85, 0.60 + dist_pct * 10)

        if action == "HOLD":
            return _hold("ichimoku", f"no_clear_signal_above={above_cloud}_tk_bull={tk_bullish}")

        return {
            "action":     action,
            "strategy":   "ichimoku",
            "confidence": round(conf, 4),
            "reason":     f"ichimoku_{'above' if above_cloud else 'below'}_cloud_tk={'bull' if tk_bullish else 'bear'}",
            "indicators": {"tenkan": round(tenkan, 2), "kijun": round(kijun, 2),
                           "cloud_top": round(cloud_top, 2), "cloud_bot": round(cloud_bot, 2)},
            "score_boost": round(conf - 0.55, 2),
        }
    except Exception as e:
        return _hold("ichimoku", f"error:{e}")


# ─────────────────────────────────────────────────────────────────────────────
# 5. EXPIRY WEEK PATTERN
# ─────────────────────────────────────────────────────────────────────────────

def expiry_week_regime() -> Dict[str, Any]:
    """
    NSE expiry week has a predictable pattern:

    Monday:     Range-bound. Market makers hedge positions.
                Best: Iron Condor / Bull Put Spread (theta selling)
    Tuesday:    Still range. FII F&O rollovers.
                Best: Iron Condor
    Wednesday:  Direction emerging. BANKNIFTY expiry creates volatility.
                Best: Breakout strategies post-11 AM
    Thursday:   NIFTY expiry. First hour random, then gravitates to max pain.
                Best: Max pain strategy 12-3 PM, no new delta trades after 2 PM
    Friday:     Post-expiry. New month positions building.
                Best: Trend strategies (fresh positioning)

    Returns a dict used by signal_engine to apply multipliers.
    """
    today   = date.today()
    weekday = today.weekday()  # 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri

    patterns = {
        0: {  # Monday
            "day":          "MONDAY",
            "bias":         "RANGE",
            "best_strategies": ["iron_condor", "mean_reversion", "vwap_reversion", "vpoc_magnet"],
            "avoid":        ["trend", "breakout", "hour_orb"],
            "mults":        {"iron_condor":2.0, "mean_reversion":1.5, "trend":0.6, "breakout":0.5},
            "size_mult":    0.80,   # slightly smaller on Monday
        },
        1: {  # Tuesday
            "day":          "TUESDAY",
            "bias":         "RANGE",
            "best_strategies": ["iron_condor", "bull_put_spread", "mean_reversion"],
            "avoid":        ["trend"],
            "mults":        {"iron_condor":1.8, "mean_reversion":1.4, "trend":0.7},
            "size_mult":    0.85,
        },
        2: {  # Wednesday (BANKNIFTY expiry)
            "day":          "WEDNESDAY_BNF_EXPIRY",
            "bias":         "DIRECTIONAL_AFTER_11",
            "best_strategies": ["breakout", "liquidity_sweep", "trend", "hour_orb"],
            "avoid":        ["iron_condor"],  # BNF expiry creates vol
            "mults":        {"breakout":1.6, "trend":1.4, "iron_condor":0.5},
            "size_mult":    0.90,
        },
        3: {  # Thursday (NIFTY expiry)
            "day":          "THURSDAY_NIFTY_EXPIRY",
            "bias":         "MAX_PAIN_GRAVITY",
            "best_strategies": ["oi_velocity_goldman", "vpoc_magnet", "vwap_reversion"],
            "avoid":        ["swing"],  # never open swing on expiry day
            "mults":        {"oi_velocity_goldman":2.0, "vpoc_magnet":1.8, "trend":0.9},
            "size_mult":    0.75,   # smaller on expiry day
            "no_new_after": "14:00",  # no new trades after 2 PM on expiry
        },
        4: {  # Friday (post-expiry, new positioning)
            "day":          "FRIDAY",
            "bias":         "FRESH_TREND",
            "best_strategies": ["trend", "breakout", "market_structure"],
            "avoid":        [],
            "mults":        {"trend":1.4, "breakout":1.3, "market_structure":1.4},
            "size_mult":    1.0,
        },
    }

    pattern = patterns.get(weekday, patterns[4])

    # Add whether we're in the expiry week (within 7 days of next Thursday)
    days_to_thu = (3 - today.weekday()) % 7
    if days_to_thu == 0: days_to_thu = 7
    next_expiry = today + __import__('datetime').timedelta(days=days_to_thu)
    dte_to_expiry = (next_expiry - today).days

    pattern["dte_to_expiry"] = dte_to_expiry
    pattern["is_expiry_week"] = dte_to_expiry <= 7

    return pattern


# ─────────────────────────────────────────────────────────────────────────────
# 6. GLOBAL MACRO CONTEXT
# ─────────────────────────────────────────────────────────────────────────────

class GlobalMacroFeed:
    """
    Overnight global macro factors that affect NIFTY opening.

    1. S&P 500: If S&P closes up 0.8%, NIFTY likely opens +0.3-0.5%
    2. DXY (Dollar Index): DXY up → FII selling India → NIFTY bearish
    3. Brent Crude: India imports oil → crude up = inflation = bearish for India
    4. US 10Y Bond Yield: High yield = money flows from EM to US = bearish NIFTY

    All fetched via yfinance after 6 PM (US market closes 5:30 AM IST).
    Applied as score modifiers for next day BUY/SELL signals.
    """

    CACHE_FILE = "global_macro.json"
    TICKERS    = {
        "sp500":    "^GSPC",   # S&P 500
        "dxy":      "DX-Y.NYB", # US Dollar Index
        "crude":    "BZ=F",    # Brent Crude futures
        "us10y":    "^TNX",    # US 10-Year Treasury yield
        "vix_us":   "^VIX",    # CBOE VIX
    }

    def __init__(self, cache_dir: str = ".") -> None:
        from pathlib import Path
        self._cache_file = Path(cache_dir) / self.CACHE_FILE
        self._data: Dict = {}
        self._load()

    def _load(self) -> None:
        if self._cache_file.exists():
            try:
                import json
                self._data = json.loads(self._cache_file.read_text())
            except Exception:
                pass

    def _save(self) -> None:
        try:
            import json
            self._cache_file.write_text(json.dumps(self._data, indent=2))
        except Exception:
            pass

    def fetch(self) -> bool:
        """Fetch all global macro data via yfinance."""
        try:
            import yf_compat as yf  # yfinance replaced: Yahoo API broken
            results = {}
            for name, ticker in self.TICKERS.items():
                try:
                    d = yf.download(ticker, period="3d", interval="1d",
                                    progress=False, auto_adjust=True, threads=False)
                    if d is not None and len(d) >= 2:
                        today_close = float(d["Close"].iloc[-1])
                        prev_close  = float(d["Close"].iloc[-2])
                        chg_pct     = (today_close - prev_close) / prev_close
                        results[name] = {
                            "close": round(today_close, 4),
                            "chg_pct": round(chg_pct, 4),
                        }
                except Exception:
                    pass
            if results:
                self._data = {
                    "date":    date.today().isoformat(),
                    "data":    results,
                }
                self._save()
                logger.info("Global macro fetched: %s",
                            {k: f"{v['chg_pct']:+.2%}" for k,v in results.items()})
                return True
        except Exception as e:
            logger.debug("GlobalMacroFeed.fetch: %s", e)
        return False

    def get_nifty_bias(self) -> float:
        """
        Combined macro score modifier for NIFTY BUY/SELL signals.
        Returns range roughly -1.0 to +1.0.

        S&P up → +0.3 (positive correlation)
        DXY up → -0.3 (negative correlation — dollar strength = FII selling)
        Crude up (strong) → -0.2 (import inflation)
        US10Y up > 0.05 → -0.2 (yield differential, capital outflow)
        US VIX > 25 → -0.5 (global risk-off)
        """
        d = self._data.get("data", {})
        if not d:
            return 0.0

        score = 0.0

        # S&P 500
        sp = d.get("sp500", {}).get("chg_pct", 0)
        if sp > 0.01:   score += 0.30
        elif sp > 0.005: score += 0.15
        elif sp < -0.015: score -= 0.40
        elif sp < -0.008: score -= 0.20

        # DXY (inverse correlation)
        dxy = d.get("dxy", {}).get("chg_pct", 0)
        if dxy > 0.005: score -= 0.30
        elif dxy < -0.005: score += 0.20

        # Crude (weak negative for India)
        crude = d.get("crude", {}).get("chg_pct", 0)
        if crude > 0.02: score -= 0.20
        elif crude < -0.02: score += 0.10

        # US 10Y yield
        us10y = d.get("us10y", {}).get("chg_pct", 0)
        if us10y > 0.03: score -= 0.20
        elif us10y < -0.03: score += 0.15

        # US VIX
        vix_us = d.get("vix_us", {}).get("close", 0)
        if vix_us > 30:  score -= 0.50
        elif vix_us > 25: score -= 0.30
        elif vix_us < 15: score += 0.20

        return round(max(-1.0, min(1.0, score)), 3)

    def get_summary(self) -> str:
        d = self._data.get("data", {})
        if not d:
            return "No global macro data"
        parts = []
        if "sp500" in d:
            parts.append(f"S&P:{d['sp500']['chg_pct']:+.1%}")
        if "dxy" in d:
            parts.append(f"DXY:{d['dxy']['chg_pct']:+.1%}")
        if "crude" in d:
            parts.append(f"OIL:{d['crude']['chg_pct']:+.1%}")
        if "vix_us" in d:
            parts.append(f"VIX:{d['vix_us']['close']:.0f}")
        return "  ".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# 7. KELLY CRITERION POSITION SIZER
# ─────────────────────────────────────────────────────────────────────────────

class KellyPositionSizer:
    """
    Kelly Criterion: mathematically optimal position sizing.

    f* = (win_rate × avg_win - loss_rate × avg_loss) / avg_win

    For safety we use HALF-KELLY (f*/2) which reduces drawdown while
    maintaining most of the compound growth benefit.

    Per-strategy Kelly: each strategy has its own win rate and R:R
    so they each get their own optimal size.
    """

    def __init__(self) -> None:
        self._history: Dict[str, List[float]] = {}  # strategy → [pnl, ...]

    def record(self, strategy: str, pnl: float, risk: float) -> None:
        """Record a trade result (pnl as multiple of risk: 1.0 = 1R)."""
        if strategy not in self._history:
            self._history[strategy] = []
        r_multiple = pnl / risk if risk > 0 else 0.0
        self._history[strategy].append(r_multiple)
        if len(self._history[strategy]) > 100:
            self._history[strategy].pop(0)

    def get_fraction(
        self,
        strategy:       str,
        default_wr:     float = 0.55,
        default_rr:     float = 1.5,
    ) -> float:
        """
        Returns the half-Kelly fraction of capital to risk on this trade.
        Range: 0.5% to 15% of available capital.
        """
        hist = self._history.get(strategy, [])

        if len(hist) >= 20:
            wins     = sum(1 for r in hist if r > 0)
            wr       = wins / len(hist)
            avg_win  = float(np.mean([r for r in hist if r > 0])) if wins > 0 else 1.0
            avg_loss = abs(float(np.mean([r for r in hist if r <= 0]))) if len(hist) - wins > 0 else 1.0
        else:
            wr       = default_wr
            avg_win  = default_rr
            avg_loss = 1.0

        from indicators import calculate_kelly_fraction
        full_kelly = calculate_kelly_fraction(wr, avg_win, avg_loss)
        half_kelly = full_kelly / 2

        return round(max(0.005, min(0.15, half_kelly)), 4)


# ─────────────────────────────────────────────────────────────────────────────
# 8. VOLUME PROFILE BREAKOUT (VAH/VAL)
# ─────────────────────────────────────────────────────────────────────────────

def vp_breakout_signal(df: pd.DataFrame) -> Dict:
    """
    Volume Profile breakout at Value Area High (VAH) or Value Area Low (VAL).

    When price breaks ABOVE VAH with volume → institutional breakout confirmed.
    When price breaks BELOW VAL with volume → institutional breakdown confirmed.

    This is more reliable than Donchian because VAH/VAL are volume-weighted
    levels where actual institutional transactions occurred.
    """
    if df is None or len(df) < 40:
        return _hold("vp_breakout", "insufficient_data")
    try:
        from institutional_indicators import get_vpoc_bias, calculate_volume_profile
        from indicators import calculate_volume_ratio

        vp   = calculate_volume_profile(df)
        vah  = vp.get("vah", 0)
        val  = vp.get("val", 0)
        vpoc = vp.get("vpoc", 0)

        if not vah or not val or vah <= val:
            return _hold("vp_breakout", "invalid_vp")

        close = _close(df)
        vr    = _s(calculate_volume_ratio(df, 20))

        action    = "HOLD"
        conf      = 0.0
        level_hit = 0.0

        if close > vah * 1.001 and vr >= 1.3:
            # Breaking above Value Area High = institutional breakout
            action    = "BUY"
            level_hit = vah
            breach    = (close - vah) / vah
            conf      = min(0.82, 0.60 + breach * 20 + (vr - 1.3) * 0.05)

        elif close < val * 0.999 and vr >= 1.3:
            # Breaking below Value Area Low = institutional breakdown
            action    = "SELL"
            level_hit = val
            breach    = (val - close) / val
            conf      = min(0.82, 0.60 + breach * 20 + (vr - 1.3) * 0.05)

        if action == "HOLD":
            return _hold("vp_breakout", f"price_in_value_area_{val:.0f}-{vah:.0f}")

        return {
            "action":     action,
            "strategy":   "vp_breakout",
            "confidence": round(conf, 4),
            "reason":     f"vp_{'above_vah' if action=='BUY' else 'below_val'}@{level_hit:.0f}_vol={vr:.1f}",
            "indicators": {"vah": vah, "val": val, "vpoc": vpoc,
                           "close": close, "vol_ratio": round(vr, 2)},
            "score_boost": round(conf - 0.55, 2),
        }
    except Exception as e:
        return _hold("vp_breakout", f"error:{e}")


# ─────────────────────────────────────────────────────────────────────────────
# 9. STOCK F&O LOT SIZE REGISTRY
# ─────────────────────────────────────────────────────────────────────────────

# Standard NSE F&O lot sizes for top stocks (as of 2025)
# Fetched dynamically from nse_master.py but this is the fallback
STOCK_FO_LOT_SIZES: Dict[str, int] = {
    "RELIANCE":    250,
    "TCS":         150,
    "HDFCBANK":    550,
    "INFY":        300,
    "ICICIBANK":   700,
    "HINDUNILVR":  300,
    "SBIN":        1500,
    "BHARTIARTL":  950,
    "ITC":         1600,
    "KOTAKBANK":   400,
    "LT":          150,
    "AXISBANK":    625,
    "WIPRO":       1500,
    "MARUTI":      100,
    "SUNPHARMA":   700,
    "TATAMOTORS":  950,
    "ULTRACEMCO":  100,
    "TATASTEEL":   2925,
    "BAJFINANCE":  125,
    "ONGC":        1925,
    "NTPC":        2975,
    "POWERGRID":   2700,
    "M&M":         350,
    "ASIANPAINT":  200,
    "DMART":       50,
    "BAJAJFINSV":  125,
    "HCLTECH":     700,
    "ADANIPORTS":  1150,
    "COALINDIA":   4200,
    "JSWSTEEL":    1350,
}

STOCK_STRIKE_INTERVALS: Dict[str, int] = {
    "RELIANCE": 20, "TCS": 25, "HDFCBANK": 5, "INFY": 10,
    "ICICIBANK": 5, "SBIN": 5, "TATAMOTORS": 2, "TATASTEEL": 2,
    # Default for most mid-cap stocks: 10 or 20
}


def get_stock_lot_size(symbol: str) -> int:
    """Get lot size for a stock — tries NSEMaster first, then static table."""
    try:
        from nse_master import get_nse_master
        master = get_nse_master()
        lot = master.get_lot_size(symbol)
        if lot and lot > 0:
            return lot
    except Exception:
        pass
    return STOCK_FO_LOT_SIZES.get(symbol.upper(), 100)


def get_stock_strike_interval(symbol: str, price: float = 0.0) -> int:
    """Get strike interval for stock options."""
    s = symbol.upper()
    if s in STOCK_STRIKE_INTERVALS:
        return STOCK_STRIKE_INTERVALS[s]
    # Infer from price level
    if price > 5000: return 50
    if price > 2000: return 20
    if price > 500:  return 10
    if price > 100:  return 5
    return 2


# ─────────────────────────────────────────────────────────────────────────────
# MODULE SINGLETONS
# ─────────────────────────────────────────────────────────────────────────────
_rs_filter:       Optional[RelativeStrengthFilter] = None
_global_macro:    Optional[GlobalMacroFeed]        = None
_kelly_sizer:     Optional[KellyPositionSizer]     = None


def get_rs_filter() -> RelativeStrengthFilter:
    global _rs_filter
    if _rs_filter is None: _rs_filter = RelativeStrengthFilter()
    return _rs_filter

def get_global_macro(cache_dir: str = ".") -> GlobalMacroFeed:
    global _global_macro
    if _global_macro is None: _global_macro = GlobalMacroFeed(cache_dir)
    return _global_macro

def get_kelly_sizer() -> KellyPositionSizer:
    global _kelly_sizer
    if _kelly_sizer is None: _kelly_sizer = KellyPositionSizer()
    return _kelly_sizer
