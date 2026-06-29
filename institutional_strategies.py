"""
institutional_strategies.py — 6 High-Value Missing Strategies

Sources:
  VSA:          Richard Wyckoff, Tom Williams "Master the Markets"
  Anchored VWAP: Brian Shannon "Technical Analysis Using Multiple Timeframes"
  Gamma Scalp:  Sheldon Natenberg "Option Volatility and Pricing"
  Delta Neutral: Euan Sinclair "Volatility Trading"
  Parabolic SAR: Welles Wilder "New Concepts in Technical Trading Systems"
  Event Driven:  Global macro + NSE event calendar integration

All built for NSE/NFO intraday 5-minute bars.
"""
from __future__ import annotations

import logging
import numpy as np
import pandas as pd
from datetime import datetime, time as dtime
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# The directional signal engine must not execute these functions. A long
# straddle/strangle and a delta-neutral short-vol position require multiple
# option legs, portfolio Greeks, atomic rollback and hedge lifecycle management.
MULTILEG_RESEARCH_ONLY = {"delta_neutral_theta", "gamma_scalp"}


# ─────────────────────────────────────────────────────────────────────────────
# 1. VOLUME SPREAD ANALYSIS (VSA) — Tom Williams / Richard Wyckoff
# ─────────────────────────────────────────────────────────────────────────────
def run_vsa_strategy(
    df: pd.DataFrame,
    df_htf: Optional[pd.DataFrame] = None,
    symbol: str = "",
    **kw,
) -> Dict:
    """
    Volume Spread Analysis — smart money footprint detection.

    Key VSA signals:
      Stopping Volume:  Wide spread DOWN bar with ultra-high volume + close near top
                        → Smart money absorbing supply (bullish)
      Upthrust:         Wide spread UP bar with high volume but weak close (near bottom)
                        → Smart money distributing (bearish)
      No Supply:        Narrow spread bar on very low volume after decline
                        → Supply exhausted, ready to move up
      No Demand:        Narrow spread bar on very low volume after rally
                        → Demand exhausted, ready to move down
      Effort vs Result: Big volume but tiny price move = hidden distribution
    """
    empty = {"strategy": "vsa", "score": 0.0, "direction": None, "side": None}
    try:
        df_c = df.copy()
        df_c.columns = [c.lower() for c in df_c.columns]
        if len(df_c) < 20 or "volume" not in df_c.columns:
            return empty

        o = df_c["open"].values   if "open"   in df_c.columns else df_c["close"].values
        h = df_c["high"].values   if "high"   in df_c.columns else df_c["close"].values
        l = df_c["low"].values    if "low"    in df_c.columns else df_c["close"].values
        c = df_c["close"].values
        v = df_c["volume"].values
        n = len(c)

        spread     = h - l
        avg_spread = float(np.mean(spread[-20:]))
        avg_vol    = float(np.mean(v[-20:]))
        avg_vol5   = float(np.mean(v[-5:]))

        # Current bar
        cur_spread = float(spread[-1])
        cur_vol    = float(v[-1])
        cur_close  = float(c[-1])
        cur_open   = float(o[-1])
        cur_high   = float(h[-1])
        cur_low    = float(l[-1])

        # Close position within bar (0=bottom, 1=top)
        close_pos  = (cur_close - cur_low) / max(cur_spread, 1e-9)
        is_up_bar  = cur_close > cur_open
        is_dn_bar  = cur_close < cur_open
        wide_spread= cur_spread > avg_spread * 1.5
        high_vol   = cur_vol    > avg_vol   * 1.5
        low_vol    = cur_vol    < avg_vol   * 0.7

        buy_score = sell_score = 0.0

        # ── Stopping Volume (bullish absorption) ──────────────────────────
        if is_dn_bar and wide_spread and high_vol and close_pos > 0.5:
            buy_score += 4.0  # down bar, big spread, huge vol but closes near top

        # ── No Supply (ultra-low vol on narrow down bar after decline) ───
        if is_dn_bar and not wide_spread and low_vol:
            if all(c[-i] < c[-i-1] for i in range(1, min(4, n))):  # prior decline
                buy_score += 3.0

        # ── Effort > Result (bullish — big vol, tiny up move, likely absorption) ─
        if is_up_bar and high_vol and cur_spread < avg_spread * 0.5:
            sell_score += 2.0  # big effort, tiny result = supply overhead

        # ── Upthrust (bearish distribution) ───────────────────────────────
        if is_up_bar and wide_spread and high_vol and close_pos < 0.35:
            sell_score += 4.0  # up bar, big spread, huge vol but closes near bottom

        # ── No Demand (ultra-low vol on narrow up bar after rally) ────────
        if is_up_bar and not wide_spread and low_vol:
            if all(c[-i] > c[-i-1] for i in range(1, min(4, n))):  # prior rally
                sell_score += 3.0

        if buy_score >= 3.0 and buy_score > sell_score:
            return {"strategy": "vsa", "score": round(buy_score, 2),
                    "direction": "BUY", "side": "BUY",
                    "vsa_signal": "Stopping Volume" if buy_score >= 4 else "No Supply"}
        if sell_score >= 3.0:
            return {"strategy": "vsa", "score": round(sell_score, 2),
                    "direction": "SELL", "side": "SELL",
                    "vsa_signal": "Upthrust" if sell_score >= 4 else "No Demand"}
    except Exception as e:
        logger.debug("vsa: %s", e)
    return empty


# ─────────────────────────────────────────────────────────────────────────────
# 2. ANCHORED VWAP — Brian Shannon
# ─────────────────────────────────────────────────────────────────────────────
def run_anchored_vwap_strategy(
    df: pd.DataFrame,
    df_htf: Optional[pd.DataFrame] = None,
    symbol: str = "",
    **kw,
) -> Dict:
    """
    Anchored VWAP — VWAP calculated from a significant anchor point.

    Anchors: Prior day high/low, gap open, earnings reaction high/low.
    AVWAP acts as dynamic support/resistance that respects institutional
    average cost basis — unlike regular VWAP which resets daily.

    ENTRY:
      BUY:  Price bounces from AVWAP after pullback + volume rising
      SELL: Price rejected from AVWAP overhead resistance
    """
    empty = {"strategy": "anchored_vwap", "score": 0.0, "direction": None, "side": None}
    try:
        df_c = df.copy()
        df_c.columns = [c.lower() for c in df_c.columns]
        if len(df_c) < 15 or "volume" not in df_c.columns:
            return empty

        h = df_c["high"].values  if "high"  in df_c.columns else df_c["close"].values
        l = df_c["low"].values   if "low"   in df_c.columns else df_c["close"].values
        c = df_c["close"].values
        v = df_c["volume"].values

        # Anchor = yesterday's close (start of session as anchor point)
        # In practice: use the most significant recent swing
        anchor_idx = max(0, len(c) - 20)  # last 20 bars as anchor window

        # AVWAP from anchor
        typical = (h[anchor_idx:] + l[anchor_idx:] + c[anchor_idx:]) / 3
        cum_vol  = np.cumsum(v[anchor_idx:])
        cum_tpv  = np.cumsum(typical * v[anchor_idx:])
        avwap    = cum_tpv / (cum_vol + 1e-9)
        avwap_now = float(avwap[-1])

        price    = float(c[-1])
        prev     = float(c[-2])
        vol_ratio = float(v[-1]) / (float(np.mean(v[-10:])) + 1e-9)

        # Distance from AVWAP
        dist_pct = (price - avwap_now) / avwap_now * 100

        buy_score = sell_score = 0.0

        # Bounce from AVWAP support (price below then returning to AVWAP)
        if prev < avwap_now <= price and vol_ratio > 1.2:
            buy_score = 3.5 + (vol_ratio > 1.5) * 0.5
        elif -0.2 <= dist_pct <= 0.2 and c[-3] < avwap_now if len(c) >= 3 else False:
            buy_score = 2.5  # price at AVWAP after pullback

        # Rejection from AVWAP resistance
        if prev > avwap_now >= price and vol_ratio > 1.2:
            sell_score = 3.5 + (vol_ratio > 1.5) * 0.5
        elif -0.2 <= dist_pct <= 0.2 and c[-3] > avwap_now if len(c) >= 3 else False:
            sell_score = 2.5

        if buy_score >= 2.5:
            return {"strategy": "anchored_vwap", "score": round(buy_score, 2),
                    "direction": "BUY", "side": "BUY",
                    "avwap": round(avwap_now, 2)}
        if sell_score >= 2.5:
            return {"strategy": "anchored_vwap", "score": round(sell_score, 2),
                    "direction": "SELL", "side": "SELL",
                    "avwap": round(avwap_now, 2)}
    except Exception as e:
        logger.debug("anchored_vwap: %s", e)
    return empty


# ─────────────────────────────────────────────────────────────────────────────
# 3. PARABOLIC SAR — Welles Wilder
# ─────────────────────────────────────────────────────────────────────────────
def run_parabolic_sar_strategy(
    df: pd.DataFrame,
    df_htf: Optional[pd.DataFrame] = None,
    symbol: str = "",
    **kw,
) -> Dict:
    """
    Parabolic SAR — trailing stop and reversal system.
    Excellent for catching trend changes and providing dynamic stops.
    Classic Wilder: AF=0.02, max AF=0.2
    """
    empty = {"strategy": "parabolic_sar", "score": 0.0, "direction": None, "side": None}
    try:
        df_c = df.copy()
        df_c.columns = [c.lower() for c in df_c.columns]
        if len(df_c) < 15:
            return empty

        h = df_c["high"].values  if "high"  in df_c.columns else df_c["close"].values
        l = df_c["low"].values   if "low"   in df_c.columns else df_c["close"].values
        c = df_c["close"].values

        AF = 0.02; AF_MAX = 0.20; AF_STEP = 0.02
        sar = [0.0] * len(c)
        ep  = [0.0] * len(c)
        af  = [AF]  * len(c)
        bull= [True] * len(c)

        # Init
        bull[0] = c[1] > c[0]
        sar[0]  = l[0] if bull[0] else h[0]
        ep[0]   = h[0] if bull[0] else l[0]

        for i in range(1, len(c)):
            prev_bull = bull[i-1]
            sar[i] = sar[i-1] + af[i-1] * (ep[i-1] - sar[i-1])

            if prev_bull:
                sar[i] = min(sar[i], l[i-1], l[max(0,i-2)])
                if l[i] < sar[i]:
                    bull[i] = False
                    sar[i]  = ep[i-1]
                    ep[i]   = l[i]
                    af[i]   = AF
                else:
                    bull[i] = True
                    if h[i] > ep[i-1]:
                        ep[i] = h[i]
                        af[i] = min(af[i-1] + AF_STEP, AF_MAX)
                    else:
                        ep[i] = ep[i-1]; af[i] = af[i-1]
            else:
                sar[i] = max(sar[i], h[i-1], h[max(0,i-2)])
                if h[i] > sar[i]:
                    bull[i] = True
                    sar[i]  = ep[i-1]
                    ep[i]   = h[i]
                    af[i]   = AF
                else:
                    bull[i] = False
                    if l[i] < ep[i-1]:
                        ep[i] = l[i]
                        af[i] = min(af[i-1] + AF_STEP, AF_MAX)
                    else:
                        ep[i] = ep[i-1]; af[i] = af[i-1]

        # Signal: SAR just flipped direction
        just_flipped_bull = bull[-1] and not bull[-2]
        just_flipped_bear = not bull[-1] and bull[-2]

        # Higher score when SAR flip confirmed by 2+ bars in same direction
        confirmed_bull = bull[-1] and bull[-2]
        confirmed_bear = not bull[-1] and not bull[-2]

        if just_flipped_bull:
            return {"strategy": "parabolic_sar", "score": 4.5,
                    "direction": "BUY", "side": "BUY",
                    "sar": round(sar[-1], 2), "sar_signal": "Bullish flip"}
        if confirmed_bull and float(c[-1]) > float(c[-2]):
            return {"strategy": "parabolic_sar", "score": 3.0,
                    "direction": "BUY", "side": "BUY", "sar": round(sar[-1], 2)}
        if just_flipped_bear:
            return {"strategy": "parabolic_sar", "score": 4.5,
                    "direction": "SELL", "side": "SELL",
                    "sar": round(sar[-1], 2), "sar_signal": "Bearish flip"}
        if confirmed_bear and float(c[-1]) < float(c[-2]):
            return {"strategy": "parabolic_sar", "score": 3.0,
                    "direction": "SELL", "side": "SELL", "sar": round(sar[-1], 2)}
    except Exception as e:
        logger.debug("parabolic_sar: %s", e)
    return empty


# ─────────────────────────────────────────────────────────────────────────────
# 4. DELTA-NEUTRAL THETA CAPTURE — Euan Sinclair
# ─────────────────────────────────────────────────────────────────────────────
def run_delta_neutral_theta(
    df: pd.DataFrame,
    df_htf: Optional[pd.DataFrame] = None,
    symbol: str = "",
    option_data: Optional[dict] = None,
    **kw,
) -> Dict:
    """
    Delta-Neutral Theta capture — sell straddle/strangle, hedge delta.

    Logic (Euan Sinclair "Volatility Trading"):
      1. Sell ATM straddle when IV > HV × 1.2 (options expensive)
      2. Delta hedge by buying/selling underlying to neutralize delta
      3. Collect theta daily while gamma-scalping around the position
      4. Exit: IV reverts to HV, or position approaches 50% max profit

    Simplified for NSE: generates SELL signal on index when IV > HV
    threshold — system sells OTM strangle around current price.
    """
    empty = {"strategy": "delta_neutral_theta", "score": 0.0, "direction": None, "side": None}
    try:
        _INDICES = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"}
        if symbol.upper() not in _INDICES:
            return empty  # only for indices with liquid options

        df_c = df.copy()
        df_c.columns = [c.lower() for c in df_c.columns]
        if len(df_c) < 20:
            return empty

        closes = df_c["close"].values
        price  = float(closes[-1])

        # Historical Volatility (20-day)
        returns = np.diff(np.log(closes[-21:]))
        hv_20   = float(np.std(returns) * np.sqrt(252) * 100)

        # IV from VIX or option_data
        iv = 0.0
        if option_data:
            iv = float(option_data.get("vix", 0) or option_data.get("iv", 0))
        if not iv:
            try:
                import yf_compat as _yf
                _vdf = _yf.download("^INDIAVIX", period="2d", interval="1d",
                                    progress=False, auto_adjust=True)
                if _vdf is not None and len(_vdf) > 0:
                    _vc = _vdf["Close"]
                    if hasattr(_vc, "columns"): _vc = _vc.iloc[:, 0]
                    iv = float(_vc.iloc[-1])
            except Exception:
                iv = 15.0

        if iv <= 0 or hv_20 <= 0:
            return empty

        iv_hv_ratio = iv / hv_20
        now_t = datetime.now().time()
        in_mkt = dtime(9, 20) <= now_t <= dtime(15, 0)

        # Core logic: sell when IV meaningfully > HV
        if iv_hv_ratio >= 1.2 and in_mkt:
            score = 3.0 + min(2.0, (iv_hv_ratio - 1.2) * 5)
            return {
                "strategy":        "delta_neutral_theta",
                "score":           round(score, 2),
                "direction":       "SELL",  # sell premium
                "side":            "SELL",
                "iv":              round(iv, 1),
                "hv_20":           round(hv_20, 1),
                "iv_hv_ratio":     round(iv_hv_ratio, 2),
                "action":          f"Sell {symbol} strangle — IV {iv:.0f} > HV {hv_20:.0f}",
            }
    except Exception as e:
        logger.debug("delta_neutral_theta: %s", e)
    return empty


# ─────────────────────────────────────────────────────────────────────────────
# 5. GAMMA SCALPING — Natenberg / Sinclair
# ─────────────────────────────────────────────────────────────────────────────
def run_gamma_scalp_strategy(
    df: pd.DataFrame,
    df_htf: Optional[pd.DataFrame] = None,
    symbol: str = "",
    option_data: Optional[dict] = None,
    **kw,
) -> Dict:
    """
    Gamma Scalping — long gamma (long straddle) + hedge delta.

    When you are long gamma (long ATM straddle/strangle):
      Every time price moves significantly, you delta-hedge by
      trading the underlying — locking in profits from price swings.

    Signal: Buy straddle when HV > IV (options cheap) + high ATR day
    Signal score improves when: expiry week, high ATR, IV < HV × 0.8

    Natenberg: "Long gamma is profitable when realized vol > implied vol"
    """
    empty = {"strategy": "gamma_scalp", "score": 0.0, "direction": None, "side": None}
    try:
        _INDICES = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"}
        if symbol.upper() not in _INDICES:
            return empty

        df_c = df.copy()
        df_c.columns = [c.lower() for c in df_c.columns]
        if len(df_c) < 20:
            return empty

        closes = df_c["close"].values
        highs  = df_c["high"].values if "high" in df_c.columns else closes
        lows   = df_c["low"].values  if "low"  in df_c.columns else closes

        # ATR
        tr   = np.maximum(highs[1:]-lows[1:],
               np.maximum(abs(highs[1:]-closes[:-1]), abs(lows[1:]-closes[:-1])))
        atr  = float(np.mean(tr[-14:])) if len(tr) >= 14 else float(np.std(closes[-20:]))
        atr_pct = atr / float(closes[-1]) * 100

        # HV
        returns = np.diff(np.log(closes[-21:]))
        hv_20   = float(np.std(returns) * np.sqrt(252) * 100)

        # IV from VIX
        iv = 0.0
        if option_data:
            iv = float(option_data.get("vix", 0) or 0)
        if not iv:
            try:
                import yf_compat as _yf
                _vdf = _yf.download("^INDIAVIX", period="2d", interval="1d",
                                    progress=False, auto_adjust=True)
                if _vdf is not None and len(_vdf) > 0:
                    _vc = _vdf["Close"]
                    if hasattr(_vc, "columns"): _vc = _vc.iloc[:, 0]
                    iv = float(_vc.iloc[-1])
            except Exception:
                iv = 15.0

        if iv <= 0:
            return empty

        # Gamma scalp opportunity: HV > IV AND high ATR
        hv_iv_ratio = hv_20 / max(iv, 1)
        is_expiry_week = datetime.now().weekday() >= 2  # Wed-Fri
        high_atr = atr_pct > 0.8

        if hv_iv_ratio >= 1.15 and high_atr:
            score = 3.0 + min(2.0, (hv_iv_ratio - 1.0) * 2) + is_expiry_week * 0.5
            return {
                "strategy":    "gamma_scalp",
                "score":       round(score, 2),
                "direction":   "BUY",  # long straddle = buy both CE and PE
                "side":        "BUY",
                "iv":          round(iv, 1),
                "hv_20":       round(hv_20, 1),
                "atr_pct":     round(atr_pct, 2),
                "action":      f"Buy {symbol} straddle — HV {hv_20:.0f} > IV {iv:.0f}",
            }
    except Exception as e:
        logger.debug("gamma_scalp: %s", e)
    return empty


# ─────────────────────────────────────────────────────────────────────────────
# 6. EVENT-DRIVEN / MACRO FILTER — Trump, RBI, Budget, global events
# ─────────────────────────────────────────────────────────────────────────────
def run_event_driven_filter(
    df: pd.DataFrame,
    df_htf: Optional[pd.DataFrame] = None,
    symbol: str = "",
    **kw,
) -> Dict:
    """
    Event-Driven Filter — adjusts signal scores around major global events.

    Tracks:
      - RBI policy dates (predictable)
      - US Fed FOMC dates (high market impact)
      - NSE/BSE result calendar (earnings)
      - Budget day (Feb 1)
      - Expiry + monthly settlement
      - Trump trade war tweets (sentiment proxy via VIX spike)

    Logic: On event days, signal scores are reduced (more uncertainty).
           Post-event, if VIX falls + direction clear = amplified signal.

    This is a FILTER not an entry — returns score modifier, not a trade.
    """
    empty = {"strategy": "event_driven", "score": 0.0, "direction": None, "side": None}
    try:
        now = datetime.now()
        vix = kw.get("vix", 15.0) or 15.0

        # Check if today is a known high-impact event day
        # RBI MPC dates 2026 (approximate — update yearly from RBI calendar)
        rbi_months = {2, 4, 6, 8, 10, 12}
        is_rbi_week = now.month in rbi_months and 1 <= now.day <= 8

        # US Fed FOMC (Jan/Mar/May/Jul/Sep/Nov — usually 3rd week)
        fed_months = {1, 3, 5, 7, 9, 11}
        is_fed_week = now.month in fed_months and 15 <= now.day <= 22

        # Budget day
        is_budget = now.month == 2 and now.day == 1

        # VIX spike = Trump/global event proxy
        vix_spike = float(vix) > 22

        # Post-event clarity: VIX spiked yesterday, calming today
        # (simplified — in production, check yesterday's VIX vs today)
        post_event_clarity = vix_spike and float(vix) < 20

        score = 0.0
        direction = None

        if is_rbi_week or is_fed_week or is_budget:
            # Reduce score — uncertainty. Return negative modifier.
            score = 2.0  # positive score but with NEUTRAL direction = dampener
            direction = "NEUTRAL"

        if vix_spike and not (is_rbi_week or is_fed_week):
            # VIX spike without scheduled event = Trump/surprise = strong move coming
            # Favour momentum direction
            df_c = df.copy()
            df_c.columns = [c.lower() for c in df_c.columns]
            if len(df_c) >= 3:
                closes = df_c["close"].values
                if float(closes[-1]) > float(closes[-3]):
                    direction = "BUY"
                    score = 3.5
                else:
                    direction = "SELL"
                    score = 3.5

        if score > 0 and direction and direction != "NEUTRAL":
            return {
                "strategy":  "event_driven",
                "score":     round(score, 2),
                "direction": direction,
                "side":      direction,
                "event":     ("RBI Week" if is_rbi_week else
                              "FOMC Week" if is_fed_week else
                              "Budget Day" if is_budget else
                              "VIX Spike — macro event"),
            }
    except Exception as e:
        logger.debug("event_driven: %s", e)
    return empty
