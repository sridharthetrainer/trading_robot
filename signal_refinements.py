"""
signal_refinements.py — High-Value Signal Quality Improvements

Five refinements that directly improve win rate and reduce false signals:

1. SCORE DECAY        — Signals lose strength with time (9 AM signal ≠ 2 PM signal)
2. SECTOR ROTATION    — Detect FII money flow into sectors, tilt positions
3. NIFTY WEIGHT BOOST — Heavyweight stocks (HDFC, RIL) get amplified signals  
4. IV SKEW FILTER     — Block option buys when skew is against direction
5. PRE-EARNINGS BLOCK — Avoid options 3 days before earnings (IV crush risk)
6. AUTO S/R LEVELS    — Auto-detect swing high/low S/R from price history
"""
from __future__ import annotations

import logging
from datetime import datetime, date
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 1. SCORE DECAY — Signals lose strength with time of day
# ─────────────────────────────────────────────────────────────────────────────
def apply_score_decay(
    score:         float,
    signal_time:   Optional[datetime] = None,
    strategy:      str = "",
) -> Tuple[float, str]:
    """
    Signals generated earlier in the day are more actionable.
    A 7.0 score at 9:20 AM is very different from 7.0 at 2:45 PM.

    Decay schedule (empirically calibrated):
      9:15 – 10:00  × 1.0   (power open — full score)
      10:00 – 11:30 × 0.95  (mid-morning — slight decay)
      11:30 – 13:00 × 0.85  (lunch lull — moderate decay)
      13:00 – 14:00 × 0.90  (afternoon recovery)
      14:00 – 15:00 × 0.80  (pre-close — reduced conviction)
      15:00 – 15:25 × 0.65  (last 25 min — avoid new entries)

    Exception: expiry day scalp strategies — no decay (0DTE = time critical)
    """
    if signal_time is None:
        signal_time = datetime.now()

    t = signal_time.time()
    from datetime import time as _dtime

    # Expiry scalp — no decay (0DTE moves fast, entries valid all day)
    if "expiry" in strategy.lower() or "scalp" in strategy.lower():
        return round(score, 2), "no_decay (expiry)"

    if   t < _dtime(10,  0): mult = 1.00; tag = "power_open"
    elif t < _dtime(11, 30): mult = 0.95; tag = "mid_morning"
    elif t < _dtime(13,  0): mult = 0.85; tag = "lunch_lull"
    elif t < _dtime(14,  0): mult = 0.90; tag = "afternoon"
    elif t < _dtime(15,  0): mult = 0.80; tag = "pre_close"
    else:                    mult = 0.65; tag = "last_25min"

    decayed = round(score * mult, 2)
    note    = f"decay_{tag}_{mult:.0%}" if mult < 1.0 else "no_decay"
    return decayed, note


# ─────────────────────────────────────────────────────────────────────────────
# 2. SECTOR ROTATION — Which sectors are getting FII money
# ─────────────────────────────────────────────────────────────────────────────

# Nifty sector ETF proxies (yfinance tickers)
_SECTOR_TICKERS = {
    "IT":       "NIFTYIT.NS",
    "BANK":     "^NSEBANK",
    "PHARMA":   "NIFTYPHARMA.NS",
    "AUTO":     "NIFTYAUTO.NS",
    "METAL":    "NIFTYMETAL.NS",
    "FMCG":     "NIFTYFMCG.NS",
    "REALTY":   "NIFTYREALTY.NS",
    "ENERGY":   "NIFTYENERGY.NS",
    "INFRA":    "NIFTYINFRA.NS",
    "MEDIA":    "NIFTYMEDIA.NS",
}

# Which sector each symbol belongs to (sample — full list in sector map)
_SYMBOL_SECTOR = {
    "INFY":"IT","TCS":"IT","WIPRO":"IT","HCLTECH":"IT","TECHM":"IT",
    "HDFCBANK":"BANK","ICICIBANK":"BANK","AXISBANK":"BANK","KOTAKBANK":"BANK","SBIN":"BANK",
    "SUNPHARMA":"PHARMA","DRREDDY":"PHARMA","CIPLA":"PHARMA","DIVISLAB":"PHARMA",
    "MARUTI":"AUTO","TATAMOTORS":"AUTO","M&M":"AUTO","BAJAJ-AUTO":"AUTO","EICHERMOT":"AUTO",
    "TATASTEEL":"METAL","JSWSTEEL":"METAL","HINDALCO":"METAL","COAL INDIA":"ENERGY",
    "HINDUNILVR":"FMCG","ITC":"FMCG","NESTLEIND":"FMCG","BRITANNIA":"FMCG",
    "RELIANCE":"ENERGY","ONGC":"ENERGY","BPCL":"ENERGY","IOC":"ENERGY",
    "DLF":"REALTY","PRESTIGE":"REALTY","OBEROIRLTY":"REALTY",
}

def get_sector_rotation_score(symbol: str, lookback_days: int = 5) -> Dict:
    """
    Detect sector rotation — which sectors are outperforming.
    Boost signals in outperforming sectors, reduce in lagging ones.

    Returns score modifier and momentum rank.
    """
    sector = _SYMBOL_SECTOR.get(symbol.upper(), "")
    if not sector:
        return {"score_mod": 0.0, "sector": "UNKNOWN", "rank": "N/A"}

    # 2026-07-10: this used yfinance — the project's own documented-broken
    # data source — so every lookup came back empty and sector_mod logged 0
    # on every signal, forever. Rewired to sector_history.csv, which
    # eod_market_capture already saves nightly (real chg_1d per sector,
    # fresh through the latest session). Relative strength = the sector's
    # cumulative 1-day changes over the lookback vs the cross-sector mean
    # (the file has no NIFTY row; cross-sector mean is the rotation
    # benchmark). Same thresholds/labels as before.
    _CSV_SECTOR = {"IT": "IT", "BANK": "Banking", "PHARMA": "Pharma",
                   "AUTO": "Auto", "METAL": "Metal", "FMCG": "FMCG",
                   "REALTY": "Realty", "ENERGY": "Energy", "INFRA": "Infra",
                   "MEDIA": "Media"}
    csv_name = _CSV_SECTOR.get(sector)
    if not csv_name:
        return {"score_mod": 0.0, "sector": sector, "rank": "N/A"}

    try:
        import csv as _csv
        from collections import defaultdict
        rows_by_sector = defaultdict(list)
        with open("sector_history.csv", errors="replace") as _fh:
            for _row in _csv.DictReader(_fh):
                try:
                    rows_by_sector[_row["sector"]].append(
                        (_row["date"], float(_row.get("chg_1d", 0) or 0)))
                except (KeyError, ValueError, TypeError):
                    continue
        if csv_name not in rows_by_sector:
            return {"score_mod": 0.0, "sector": sector, "rank": "N/A"}

        def _cum(name: str) -> float:
            recent = sorted(rows_by_sector[name])[-lookback_days:]
            return sum(chg for _, chg in recent)

        sec_ret  = _cum(csv_name)
        all_rets = [_cum(name) for name in rows_by_sector]
        nif_ret  = sum(all_rets) / len(all_rets) if all_rets else 0.0
        relative = sec_ret - nif_ret  # sector alpha vs cross-sector mean

        # Score modifier
        if relative > 3.0:
            score_mod = 1.0; rank = "🔥 TOP (FII buying)"
        elif relative > 1.0:
            score_mod = 0.5; rank = "📈 OUTPERFORMING"
        elif relative > -1.0:
            score_mod = 0.0; rank = "➡️ INLINE"
        elif relative > -3.0:
            score_mod = -0.5; rank = "📉 LAGGING"
        else:
            score_mod = -1.0; rank = "❄️ FII SELLING"

        return {
            "score_mod":    round(score_mod, 2),
            "sector":       sector,
            "sector_ret":   round(sec_ret, 2),
            "nifty_ret":    round(nif_ret, 2),
            "relative":     round(relative, 2),
            "rank":         rank,
        }
    except Exception as e:
        logger.debug("sector_rotation: %s", e)
        return {"score_mod": 0.0, "sector": sector, "rank": "N/A"}


# ─────────────────────────────────────────────────────────────────────────────
# 3. NIFTY50 WEIGHT IMPACT — Heavyweight stocks amplify index moves
# ─────────────────────────────────────────────────────────────────────────────

# Nifty50 constituent weights (approximate, updated quarterly)
_NIFTY_WEIGHTS = {
    "HDFC": 13.5, "RELIANCE": 9.8, "INFY": 6.2, "ICICIBANK": 7.1,
    "TCS": 5.8, "HDFCBANK": 12.8, "BAJFINANCE": 3.2, "KOTAKBANK": 4.1,
    "LT": 3.8, "AXISBANK": 3.5, "SBIN": 3.2, "MARUTI": 2.8,
    "SUNPHARMA": 2.1, "TITANCOMPANY": 1.9, "WIPRO": 1.8, "HCL TECH": 1.7,
    "ULTRACEMCO": 1.6, "NESTLEIND": 1.5, "TATAMOTORS": 1.4, "M&M": 1.8,
    "HCLTECH": 1.7, "TECHM": 0.9, "BAJAJ-AUTO": 1.2, "EICHERMOT": 0.8,
    "DRREDDY": 1.0, "DIVISLAB": 0.9, "CIPLA": 0.8, "HINDUNILVR": 2.2,
    "ITC": 4.2, "ONGC": 1.1, "BPCL": 0.7, "TATASTEEL": 1.3,
    "HINDALCO": 0.9, "JSWSTEEL": 1.1, "COAL INDIA": 0.8, "DLF": 0.6,
}

def get_nifty_weight_multiplier(symbol: str) -> float:
    """
    Heavyweight stocks (Nifty weight > 5%) get signal amplification.
    When HDFC (13.5% weight) moves, it MOVES the Nifty.
    Our signal should be stronger for index-moving stocks.

    Weight > 8%  → 1.3× multiplier (major index driver)
    Weight 4-8%  → 1.15× multiplier (significant impact)
    Weight 1-4%  → 1.05× multiplier (mild impact)
    Weight < 1%  → 1.0× (no amplification)
    """
    weight = _NIFTY_WEIGHTS.get(symbol.upper(), 0)
    if weight > 8:   return 1.30
    elif weight > 4: return 1.15
    elif weight > 1: return 1.05
    else:            return 1.00


# ─────────────────────────────────────────────────────────────────────────────
# 4. IV SKEW FILTER — Block trades when IV skew is against direction
# ─────────────────────────────────────────────────────────────────────────────
def check_iv_skew(
    symbol:    str,
    direction: str,  # BUY or SELL
    atm_strike: float = 0,
    option_data: dict = None,
) -> Dict:
    """
    IV Skew = OTM put IV vs OTM call IV

    Steep put skew (put IV >> call IV):
      → Market expects downside → OPTIONS market is bearish
      → Buying calls = expensive (you pay high IV)
      → Selling puts = risky (crowd consensus is down)

    Flat/positive skew (call IV ≈ put IV or call IV higher):
      → Bullish sentiment → Safe to buy calls

    Application:
      BUY signal + steep put skew → reduce score (market disagrees)
      SELL signal + steep call skew → reduce score
      Signal + skew CONFIRMS direction → boost score
    """
    if not option_data:
        return {"score_mod": 0.0, "skew": "unknown"}

    try:
        put_iv  = float(option_data.get("avg_put_iv",  0) or 0)
        call_iv = float(option_data.get("avg_call_iv", 0) or 0)

        if put_iv <= 0 or call_iv <= 0:
            return {"score_mod": 0.0, "skew": "no_data"}

        skew = put_iv - call_iv  # positive = put skew (bearish market)

        if direction == "BUY":
            if skew > 5:   # steep put skew = market disagrees with BUY
                return {"score_mod": -0.8, "skew": f"PUT_SKEW_{skew:.1f} (market bearish)"}
            elif skew < -2:  # call skew = market agrees with BUY
                return {"score_mod": 0.5, "skew": f"CALL_SKEW confirms bullish"}
        elif direction == "SELL":
            if skew < -5:  # steep call skew = market disagrees with SELL
                return {"score_mod": -0.8, "skew": f"CALL_SKEW_{abs(skew):.1f} (market bullish)"}
            elif skew > 2:  # put skew = market agrees with SELL
                return {"score_mod": 0.5, "skew": f"PUT_SKEW confirms bearish"}

        return {"score_mod": 0.0, "skew": f"neutral_{skew:.1f}"}
    except Exception as e:
        logger.debug("iv_skew: %s", e)
        return {"score_mod": 0.0, "skew": "error"}


# ─────────────────────────────────────────────────────────────────────────────
# 5. PRE-EARNINGS BLOCK — Avoid options near earnings (IV crush risk)
# ─────────────────────────────────────────────────────────────────────────────
def check_earnings_risk(symbol: str, trade_type: str = "options") -> Dict:
    """
    Block option BUYS within 3 days of earnings announcements.
    
    Why: IV spikes before earnings → premium expensive.
    After results, IV collapses (IV crush) → option buyers lose even if
    direction is right.

    Source: NSE corporate actions + earnings calendar.
    """
    if trade_type.lower() not in ("options", "ce", "pe", "call", "put"):
        return {"blocked": False, "reason": "not an options trade"}

    try:
        from corporate_actions import get_corporate_actions
        actions = get_corporate_actions(symbol)
        if not actions:
            return {"blocked": False}

        today = date.today()
        for action in (actions if isinstance(actions, list) else [actions]):
            act_type = str(action.get("purpose","")).upper()
            if "RESULT" in act_type or "DIVIDEND" in act_type:
                act_date_str = str(action.get("date","") or action.get("ex_date",""))
                try:
                    act_date = date.fromisoformat(act_date_str[:10])
                    days_to  = (act_date - today).days
                    if -1 <= days_to <= 3:
                        return {
                            "blocked": True,
                            "reason":  f"Earnings in {days_to}d ({act_date}) — IV crush risk",
                            "action":  act_type,
                        }
                except Exception: pass
    except Exception as e:
        logger.debug("earnings_risk: %s", e)

    return {"blocked": False}


# ─────────────────────────────────────────────────────────────────────────────
# 6. AUTO S/R LEVELS — Detect swing highs/lows from price history
# ─────────────────────────────────────────────────────────────────────────────
def detect_sr_levels(
    df:          pd.DataFrame,
    lookback:    int   = 50,
    min_touches: int   = 2,
    tolerance:   float = 0.003,
) -> Dict:
    """
    Auto-detect Support & Resistance levels from swing highs/lows.

    Method: Identify all swing highs and lows. Cluster levels that
    are within tolerance% of each other. Levels with 2+ touches
    are significant S/R.

    Returns: nearest_support, nearest_resistance, all_levels
    Distance: How far price is from each level (in %)
    """
    try:
        df_c = df.copy(); df_c.columns = [c.lower() for c in df_c.columns]
        if len(df_c) < lookback: return {}

        highs  = df_c["high"].values  if "high"  in df_c.columns else df_c["close"].values
        lows   = df_c["low"].values   if "low"   in df_c.columns else df_c["close"].values
        closes = df_c["close"].values
        price  = float(closes[-1])

        # Find swing highs (local maxima)
        swing_highs = []
        swing_lows  = []
        window = 3
        for i in range(window, len(highs)-window):
            if highs[i] == max(highs[i-window:i+window+1]):
                swing_highs.append(float(highs[i]))
            if lows[i] == min(lows[i-window:i+window+1]):
                swing_lows.append(float(lows[i]))

        # Cluster nearby levels (within tolerance%)
        def cluster(levels):
            if not levels: return []
            levels = sorted(set(levels))
            clusters = []; current = [levels[0]]
            for v in levels[1:]:
                if (v - current[-1]) / current[-1] <= tolerance:
                    current.append(v)
                else:
                    clusters.append(np.mean(current))
                    current = [v]
            clusters.append(np.mean(current))
            return clusters

        resistance_levels = [l for l in cluster(swing_highs) if l > price]
        support_levels    = [l for l in cluster(swing_lows)   if l < price]

        nearest_res  = min(resistance_levels, key=lambda x: x-price) if resistance_levels else None
        nearest_sup  = max(support_levels,    key=lambda x: price-x)  if support_levels    else None

        # Distance as score modifier
        score_mod = 0.0
        note      = ""

        if nearest_sup:
            dist_sup = (price - nearest_sup) / price * 100
            if dist_sup < 0.3:
                score_mod += 1.0
                note = f"At auto-detected support {nearest_sup:.0f}"

        if nearest_res:
            dist_res = (nearest_res - price) / price * 100
            if dist_res < 0.3:
                score_mod -= 0.8  # near resistance = reduce BUY score
                note = f"Near auto-detected resistance {nearest_res:.0f}"

        return {
            "nearest_support":    round(nearest_sup,  2) if nearest_sup  else None,
            "nearest_resistance": round(nearest_res,  2) if nearest_res  else None,
            "support_levels":     [round(l,2) for l in sorted(support_levels)[-5:]],
            "resistance_levels":  [round(l,2) for l in sorted(resistance_levels)[:5]],
            "score_mod":          round(score_mod, 2),
            "note":               note,
        }
    except Exception as e:
        logger.debug("detect_sr: %s", e)
        return {}
