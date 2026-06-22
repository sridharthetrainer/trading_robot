"""
hero_zero_strategy.py — 0DTE Hero or Zero Options Strategy

The "Hero or Zero" — buy ultra-cheap deep OTM options on expiry day.
Potential: 50x–200x return on premium if market makes a big move.
Risk:      100% loss of premium (which is small — ₹5 to ₹50 per lot).

═══════════════════════════════════════════════════════
CONCEPT (NSE-specific):
  On expiry day (weekly/monthly), OTM options trading at
  ₹2–₹20 premium can explode to ₹200–₹2,000 if:
  - Market makes a 150–300 point move (NIFTY)
  - VIX spikes (geopolitical event, RBI surprise)
  - Key level breaks (PDH/PDL, CPR breakout)

MATH EXAMPLE (NIFTY, 1 lot = 65):
  Buy NIFTY 22700 CE at ₹5 when NIFTY = 22500
  Cost = ₹5 × 50 = ₹250 (your max loss)
  If NIFTY moves to 22850:
  Option value ≈ ₹150 → P&L = ₹145 × 50 = ₹7,250 (29x)

ENTRY WINDOWS:
  Power Hour:   9:15–10:00 AM (best — max time value + momentum)
  Mid-session:  11:00–12:00 AM (if catalyst/event)
  Avoid:        After 1:30 PM (theta destroys value fast)

ENTRY TRIGGERS:
  1. OI buildup at key strike + CPR breakout
  2. VIX spike > 2% intraday
  3. GIFT Nifty gap up/down > 0.5% + opening momentum
  4. 1-min 200 EMA breakout with volume
  5. Failed auction at PDH/PDL → sharp reversal expected

SELECTION:
  Buy CE:  2-3 strikes OTM (1.5–2% away from current price)
  Buy PE:  2-3 strikes OTM on SELL direction
  Premium: ₹2–₹50 ideal (never > ₹100 — defeats the purpose)
  Target:  5x–20x (take partial at 5x, trail remainder)
  Stop:    100% (options expire worthless if wrong — that's the deal)

INSTRUMENTS:  NIFTY, BANKNIFTY, FINNIFTY, SENSEX, MIDCPNIFTY
EXPIRY DAYS:  NIFTY=Tue (weekly, NSE), SENSEX=Thu (weekly, BSE).
              BANKNIFTY/FINNIFTY/MIDCPNIFTY weeklies discontinued (SEBI Nov-2024)
              — monthly only (last Tue). Verified Jun 2026. Holiday shifts to the
              prior working day are NOT yet handled (caveat).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, date, time as dtime, timedelta
from typing import Dict, List, Optional, Tuple


from option_quality import evaluate_option_buy_quality
from option_decision_journal import record_option_decision
from option_strike_autotune import score_candidate_with_autotune

logger = logging.getLogger(__name__)

# ── Expiry schedule (post SEBI Nov-2024 single-weekly rule + Sept-2025 shift) ──
# Only NIFTY (NSE) and SENSEX (BSE) still have WEEKLY expiries. The rest lost
# their weeklies and now expire only on the MONTHLY (last expiry-weekday).
_EXPIRY_DAY = {
    "NIFTY":      1,   # Tuesday — weekly (NSE)
    "SENSEX":     3,   # Thursday — weekly (BSE)
    "BANKNIFTY":  1,   # Tuesday — MONTHLY only (last Tue)
    "FINNIFTY":   1,   # Tuesday — MONTHLY only (last Tue)
    "MIDCPNIFTY": 1,   # Tuesday — MONTHLY only (last Tue)
}
# Indices whose weekly expiry was discontinued — 0DTE only on the monthly expiry.
_MONTHLY_ONLY = {"BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"}

# ── Strike grid (approx lot sizes) ───────────────────────────────────────────
_LOT_SIZES = {
    "NIFTY": 65, "BANKNIFTY": 30, "FINNIFTY": 60,
    "SENSEX": 20, "MIDCPNIFTY": 120, "BANKEX": 30,
}
_STRIKE_GAP = {
    "NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50,
    "SENSEX": 100, "MIDCPNIFTY": 25,
}


def _is_market_holiday(d: date) -> bool:
    """NSE trading holiday or weekend. Defensive: if the holiday master can't
    load, treat only weekends as closed so we never wrongly suppress an expiry.
    (NSE's list is also a good proxy for BSE/SENSEX.)"""
    if d.weekday() >= 5:
        return True
    try:
        from nse_master import get_nse_master
        return bool(get_nse_master().is_trading_holiday(d))
    except Exception:
        return False


def _prev_working_day(d: date) -> date:
    """Roll back to the previous working day if d is a weekend/holiday."""
    guard = 0
    while _is_market_holiday(d) and guard < 14:
        d -= timedelta(days=1); guard += 1
    return d


def _nominal_expiry_date(today: date, weekday: int, monthly: bool) -> date:
    """The scheduled (pre-holiday) expiry date relevant to `today`.

    Weekly → that weekday in today's Mon–Sun week.
    Monthly → the last occurrence of that weekday in today's month.
    """
    if monthly:
        import calendar
        last = date(today.year, today.month,
                    calendar.monthrange(today.year, today.month)[1])
        while last.weekday() != weekday:
            last -= timedelta(days=1)
        return last
    return today + timedelta(days=(weekday - today.weekday()))


def is_expiry_today(symbol: str = "NIFTY") -> bool:
    """True if today is the effective 0DTE expiry for the given index.

    Honours the SEBI single-weekly rule (NIFTY/SENSEX weekly; the rest
    monthly-only) AND holiday roll-back: if the scheduled expiry weekday is a
    holiday, the effective expiry moves to the previous working day.
    """
    idx = symbol.upper()
    today = date.today()
    expiry_wd = _EXPIRY_DAY.get(idx, 1)
    nominal   = _nominal_expiry_date(today, expiry_wd, idx in _MONTHLY_ONLY)
    return today == _prev_working_day(nominal)


# Hero-zero premium band: cheap enough to be a true lottery ticket, not so cheap
# it's already worthless. Outside this band the "hero or zero" math breaks down.
_HZ_MIN_PREMIUM = float(os.getenv("HERO_ZERO_MIN_PREMIUM", "2.0"))
_HZ_MAX_PREMIUM = float(os.getenv("HERO_ZERO_MAX_PREMIUM", "100.0"))
_HZ_IDEAL_PREMIUM = float(os.getenv("HERO_ZERO_IDEAL_PREMIUM", "20.0"))
_HZ_MIN_OI      = float(os.getenv("HERO_ZERO_MIN_OI", "100"))      # contracts
_HZ_MIN_VOLUME  = float(os.getenv("HERO_ZERO_MIN_VOLUME", "100"))  # contracts
_HZ_MAX_SPREAD_PCT = float(os.getenv("HERO_ZERO_MAX_SPREAD_PCT", "0.25"))
_HZ_MODE = os.getenv("HERO_ZERO_MODE", "shadow").strip().lower()
_HZ_LIVE_MICRO_MAX_PREMIUM = float(os.getenv("HERO_ZERO_LIVE_MICRO_MAX_PREMIUM", "25.0"))
_HZ_STOP_LOSS_PCT = float(os.getenv("HERO_ZERO_STOP_LOSS_PCT", "0.50"))
_HZ_TARGET_1_MULT = float(os.getenv("HERO_ZERO_TARGET_1_MULT", "2.0"))
_HZ_TARGET_2_MULT = float(os.getenv("HERO_ZERO_TARGET_2_MULT", "5.0"))
_HZ_TRAIL_AFTER_MULT = float(os.getenv("HERO_ZERO_TRAIL_AFTER_MULT", "2.0"))
_HZ_TRAIL_LOCK_PCT = float(os.getenv("HERO_ZERO_TRAIL_LOCK_PCT", "0.35"))


def _chain_rows(option_data: Optional[dict]) -> List[Dict]:
    """Best-effort extraction of NSE-style option-chain rows (each with
    'strikePrice' + 'CE'/'PE' dicts) from whatever shape option_data carries."""
    if not isinstance(option_data, dict):
        return []
    for k in ("chain", "option_chain", "rows", "data", "strikes_data"):
        v = option_data.get(k)
        if isinstance(v, list) and v and isinstance(v[0], dict) and "strikePrice" in v[0]:
            return v
    for k in ("records", "filtered"):
        v = option_data.get(k)
        if isinstance(v, dict) and isinstance(v.get("data"), list):
            return v["data"]
    return []


def _leg_quote(rows: List[Dict], strike: float, opt_type: str):
    """Quote fields for a strike/leg, or zeros if not in the chain."""
    for row in rows:
        try:
            if abs(float(row.get("strikePrice", -1) or -1) - strike) < 1e-6:
                leg = row.get(opt_type, {}) or {}
                bid = (
                    leg.get("bidprice")
                    or leg.get("bidPrice")
                    or leg.get("bestBid")
                    or 0
                )
                ask = (
                    leg.get("askPrice")
                    or leg.get("askprice")
                    or leg.get("bestAsk")
                    or 0
                )
                return {
                    "ltp": float(leg.get("lastPrice", 0) or 0),
                    "oi": float(leg.get("openInterest", 0) or 0),
                    "volume": float(leg.get("totalTradedVolume", 0) or 0),
                    "bid": float(bid or 0),
                    "ask": float(ask or 0),
                }
        except Exception:
            continue
    return {"ltp": 0.0, "oi": 0.0, "volume": 0.0, "bid": 0.0, "ask": 0.0}


def _spread_pct(bid: float, ask: float, ltp: float) -> Optional[float]:
    """Return bid/ask spread as % of mid/ltp. None when unavailable."""
    if bid <= 0 or ask <= 0 or ask < bid:
        return None
    mid = (bid + ask) / 2.0
    denom = mid if mid > 0 else ltp
    if denom <= 0:
        return None
    return (ask - bid) / denom


def _hero_zero_quality(row: Dict) -> Tuple[bool, float, str]:
    """
    Liquidity/quality gate for a hero-zero option candidate.

    Returns (tradeable, quality_score, reason). The score intentionally favours
    cheap-but-not-dead contracts near the ideal premium with actual OI/volume.
    """
    premium = row.get("premium")
    if premium is None:
        return False, 0.0, "premium_unknown"
    premium = float(premium or 0)
    if premium < _HZ_MIN_PREMIUM:
        return False, 0.0, "premium_too_low"
    if premium > _HZ_MAX_PREMIUM:
        return False, 0.0, "premium_too_high"

    oi = float(row.get("oi") or 0)
    volume = float(row.get("volume") or 0)
    if oi < _HZ_MIN_OI:
        return False, 0.0, "oi_too_low"
    if volume < _HZ_MIN_VOLUME:
        return False, 0.0, "volume_too_low"

    spread = row.get("spread_pct")
    if spread is not None and float(spread) > _HZ_MAX_SPREAD_PCT:
        return False, 0.0, "spread_too_wide"

    premium_score = max(0.0, 1.0 - abs(premium - _HZ_IDEAL_PREMIUM) / max(_HZ_IDEAL_PREMIUM, 1.0))
    liquidity_score = min(1.0, (oi / max(_HZ_MIN_OI * 5.0, 1.0)) * 0.5)
    volume_score = min(1.0, (volume / max(_HZ_MIN_VOLUME * 5.0, 1.0)) * 0.5)
    spread_score = 0.5
    if spread is not None:
        spread_score = max(0.0, 1.0 - float(spread) / max(_HZ_MAX_SPREAD_PCT, 0.01))

    score = round((premium_score * 0.45) + (liquidity_score * 0.2)
                  + (volume_score * 0.2) + (spread_score * 0.15), 4)
    return True, score, "ok"


def hero_zero_mode() -> str:
    """Configured Hero Zero operating mode."""
    if _HZ_MODE in {"alert", "alert_only", "shadow", "paper", "live_micro"}:
        return _HZ_MODE
    return "shadow"


def build_hero_zero_risk_plan(candidate: Dict, lot_size: Optional[int] = None) -> Dict:
    """
    Premium-defined risk plan for Hero Zero.

    Hero Zero must never use a normal wide option stop. The maximum planned loss
    is the premium, but the live/paper controller should try to exit earlier when
    the lottery thesis fails.
    """
    premium = float(candidate.get("premium") or 0.0)
    lot = int(lot_size or candidate.get("lot_size") or 1)
    if premium <= 0:
        return {
            "mode": hero_zero_mode(),
            "risk_known": False,
            "max_loss": 0.0,
            "stop_price": 0.0,
            "target_1": 0.0,
            "target_2": 0.0,
            "trail_after": 0.0,
            "trail_lock_pct": _HZ_TRAIL_LOCK_PCT,
        }
    return {
        "mode": hero_zero_mode(),
        "risk_known": True,
        "premium": round(premium, 2),
        "lot_size": lot,
        "max_loss": round(premium * lot, 2),
        "stop_price": round(max(0.05, premium * (1.0 - _HZ_STOP_LOSS_PCT)), 2),
        "target_1": round(premium * _HZ_TARGET_1_MULT, 2),
        "target_2": round(premium * _HZ_TARGET_2_MULT, 2),
        "trail_after": round(premium * _HZ_TRAIL_AFTER_MULT, 2),
        "trail_lock_pct": round(_HZ_TRAIL_LOCK_PCT, 4),
        "exit_policy": "partial_at_target_1_trail_after_target_1_exit_if_premium_breaks_stop",
    }


def select_hero_zero_candidate(
    strikes: List[Dict],
    quality: Optional[Dict] = None,
    side: str = "",
) -> Optional[Dict]:
    """Pick the best tradeable hero-zero candidate from enriched strikes."""
    candidates = [s for s in strikes if s.get("tradeable")]
    if not candidates:
        return None
    best = None
    best_score = -1.0
    for candidate in candidates:
        auto = score_candidate_with_autotune(candidate, quality=quality or {}, side=side)
        base = float(candidate.get("quality_score", 0.0) or 0.0)
        tuned = base * float(auto.get("multiplier", 1.0) or 1.0)
        candidate["autotune"] = auto
        candidate["autotune_score"] = round(tuned, 4)
        if tuned > best_score:
            best_score = tuned
            best = candidate
    return best


def get_hero_zero_strikes(
    spot:       float,
    symbol:     str   = "NIFTY",
    direction:  str   = "BUY",  # BUY=CE, SELL=PE
    otm_pct:    float = 1.5,    # % OTM from spot
    n_strikes:  int   = 3,      # how many strikes to return
    option_data: Optional[dict] = None,  # NSE option chain for real premiums
) -> List[Dict]:
    """
    Get 2-4 hero-zero candidate strikes OTM from spot.

    Returns multiple strikes at different OTM levels:
      ₹2-5   → deepest OTM (lottery ticket, low prob, huge payout)
      ₹10-30 → moderate OTM (better probability, still hero-zero)
      ₹30-80 → near OTM (highest probability, smaller multiple)

    When `option_data` carries a live chain, each strike is enriched with the
    REAL premium/OI/volume and a `tradeable` flag (premium within the hero-zero
    band and some OI). Without a chain it degrades to OTM-by-percentage only and
    `premium` is None (the caller must not assume a phantom cheap premium).
    """
    gap = _STRIKE_GAP.get(symbol.upper(), 50)
    lot = _LOT_SIZES.get(symbol.upper(), 50)
    rows = _chain_rows(option_data)

    # Calculate OTM strikes
    strikes = []
    for i in range(1, n_strikes + 1):
        offset = spot * (otm_pct * i / 100)
        if direction == "BUY":  # CE
            raw_strike = spot + offset
            strike     = round(raw_strike / gap) * gap
            option_type = "CE"
        else:  # PE
            raw_strike  = spot - offset
            strike      = round(raw_strike / gap) * gap
            option_type = "PE"

        rec = {
            "strike":      strike,
            "option_type": option_type,
            "otm_pct":     round(i * otm_pct, 1),
            "lot_size":    lot,
            "symbol":      symbol,
            "expiry_type": "0DTE",
            "tier":        f"T{i}",  # T1=deepest OTM, T3=nearest OTM
            "premium":     None,     # real LTP when a chain is available
            "oi":          None,
            "volume":      None,
            "tradeable":   None,     # None=unknown, True/False when chain known
        }
        if rows:
            quote = _leg_quote(rows, strike, option_type)
            ltp = quote["ltp"]
            if ltp > 0:
                spread = _spread_pct(quote["bid"], quote["ask"], ltp)
                rec["premium"]   = round(ltp, 2)
                rec["oi"]        = quote["oi"]
                rec["volume"]    = quote["volume"]
                rec["bid"]       = quote["bid"]
                rec["ask"]       = quote["ask"]
                rec["spread_pct"] = round(spread, 4) if spread is not None else None
                tradeable, quality_score, quality_reason = _hero_zero_quality(rec)
                rec["tradeable"] = tradeable
                rec["quality_score"] = quality_score
                rec["quality_reason"] = quality_reason
            else:
                rec["tradeable"] = False  # not found / no quote
                rec["quality_score"] = 0.0
                rec["quality_reason"] = "quote_missing"
        strikes.append(rec)

    return strikes


def score_hero_zero_entry(
    spot:         float,
    symbol:       str = "NIFTY",
    direction:    str = "BUY",
    vix:          float = 15.0,
    vix_change:   float = 0.0,   # intraday VIX % change
    gift_gap:     float = 0.0,   # GIFT Nifty gap vs prev close %
    cpr_break:    bool  = False,  # Price broken above/below CPR
    ema200_cross: bool  = False,  # 1-min 200 EMA fresh breakout
    pdh_pdl_break:bool  = False,  # Price broken PDH (for CE) or PDL (for PE)
    oi_buildup:   bool  = False,  # OI buildup in same direction
    time_now:     Optional[datetime] = None,
) -> Dict:
    """
    Score whether NOW is a good time to enter a hero-zero position.

    Returns score (0-10), verdict, and reasoning.
    Higher score = better hero-zero setup.
    """
    if time_now is None:
        time_now = datetime.now()

    t = time_now.time()
    score  = 0.0
    notes  = []

    # ── TIME WINDOW ───────────────────────────────────────────────────────────
    if dtime(9, 15) <= t <= dtime(10,  0):
        score += 3.0; notes.append("⚡ Power hour — best time window")
    elif dtime(10, 0) <= t <= dtime(11, 0):
        score += 2.0; notes.append("🟡 Mid-morning — good window")
    elif dtime(11, 0) <= t <= dtime(12, 30):
        score += 1.0; notes.append("🟠 Late morning — moderate")
    elif dtime(12, 30) <= t <= dtime(13, 30):
        score += 0.5; notes.append("⚠️ Lunch — weak time")
    else:
        score -= 1.0; notes.append("❌ Afternoon — theta destroys fast, avoid")

    # ── VIX CONDITIONS ────────────────────────────────────────────────────────
    if vix > 20:
        score += 1.5; notes.append(f"🔥 High VIX {vix:.0f} — premium expensive but big moves expected")
    elif vix > 15:
        score += 1.0; notes.append(f"📊 VIX {vix:.0f} — normal")

    if vix_change > 3:
        score += 1.5; notes.append(f"🚨 VIX spiking +{vix_change:.1f}% — market panic = hero-zero opportunity")
    elif vix_change > 1:
        score += 0.8; notes.append(f"📈 VIX rising +{vix_change:.1f}%")

    # ── CATALYSTS ─────────────────────────────────────────────────────────────
    if gift_gap > 0.5 and direction == "BUY":
        score += 2.0; notes.append(f"🎁 GIFT gap up {gift_gap:.1f}% — CE hero-zero plays")
    elif gift_gap < -0.5 and direction == "SELL":
        score += 2.0; notes.append(f"🎁 GIFT gap down {gift_gap:.1f}% — PE hero-zero plays")

    if ema200_cross:
        score += 1.5; notes.append("📈 1-min 200 EMA fresh breakout — momentum trigger")

    if cpr_break:
        score += 1.5; notes.append("🔲 CPR breakout — strong directional move starting")

    if pdh_pdl_break:
        score += 1.5; notes.append("📊 PDH/PDL break — high momentum day")

    if oi_buildup:
        score += 1.0; notes.append("🐳 OI building in direction — smart money")

    # ── VERDICT ───────────────────────────────────────────────────────────────
    score = round(min(score, 10.0), 2)

    if score >= 7.0:
        verdict = "🟢 STRONG HERO-ZERO SETUP — High conviction"
    elif score >= 5.0:
        verdict = "🟡 MODERATE SETUP — Entry with tight premium (<₹20)"
    elif score >= 3.0:
        verdict = "🟠 WEAK SETUP — Only deepest OTM (lottery tier)"
    else:
        verdict = "🔴 AVOID — Poor timing / no catalyst"

    return {
        "score":      score,
        "verdict":    verdict,
        "direction":  direction,
        "symbol":     symbol,
        "notes":      notes,
        "enter":      score >= 5.0,
    }


def run_hero_zero_strategy(
    df,                                  # 5-min OHLCV
    df_htf=None,
    symbol: str = "NIFTY",
    option_data: Optional[dict] = None,
    **kw,
) -> Dict:
    """
    Full hero-zero signal generator.

    Only fires on EXPIRY DAY during valid time windows.
    Checks all entry conditions and returns CE/PE strike recommendation.
    """
    empty = {"strategy": "hero_zero", "score": 0.0, "direction": None, "side": None}

    # Only on expiry day for index instruments
    _INDICES = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"}
    if symbol.upper() not in _INDICES:
        return empty

    if not is_expiry_today(symbol):
        return empty

    # Block after 1:30 PM — theta too destructive
    now = datetime.now()
    if now.time() > dtime(13, 30):
        return empty

    try:
        df_c = df.copy()
        if hasattr(df_c, 'columns'):
            df_c.columns = [c.lower() for c in df_c.columns]
        closes = df_c["close"].values if hasattr(df_c, '__len__') else [0]
        if len(closes) < 5: return empty

        # Direction from a multi-bar trend (less noisy than one bar). Use the
        # 5-bar move as the primary read; the last bar only breaks ties when the
        # short trend is essentially flat.
        spot      = float(closes[-1])
        lookback  = min(5, len(closes) - 1)
        ref       = float(closes[-1 - lookback]) or spot
        mom_n     = (spot - ref) / ref * 100 if ref else 0.0
        prev      = float(closes[-2]) or spot
        mom_1     = (spot - prev) / prev * 100 if prev else 0.0
        if abs(mom_n) >= 0.05:
            direction = "BUY" if mom_n > 0 else "SELL"
        else:
            direction = "BUY" if mom_1 >= 0 else "SELL"

        # Get VIX
        vix = float(kw.get("vix", 15) or 15)

        # Get GIFT gap
        gift_gap = float(kw.get("gift_gap", 0) or 0)

        # Score the setup
        setup = score_hero_zero_entry(
            spot=spot, symbol=symbol, direction=direction,
            vix=vix, vix_change=float(kw.get("vix_change", 0) or 0),
            gift_gap=gift_gap,
            cpr_break=kw.get("cpr_break", False),
            ema200_cross=kw.get("ema200_cross", False),
            pdh_pdl_break=kw.get("pdh_pdl_break", False),
            oi_buildup=kw.get("oi_buildup", False),
            time_now=now,
        )

        if not setup["enter"]:
            return empty

        quality = evaluate_option_buy_quality(
            option_data=option_data,
            spot=spot,
            df=df_c,
            max_chain_age_sec=int(os.getenv("OPTION_CHAIN_MAX_AGE_SEC", "120")),
            min_expected_move_pct=float(os.getenv("HERO_ZERO_MIN_EXPECTED_MOVE_PCT", "0.35")),
            expected_move_usage_limit=float(os.getenv("HERO_ZERO_EXPECTED_MOVE_USAGE_LIMIT", "0.70")),
        )
        if not quality.ok:
            record_option_decision(
                strategy="hero_zero",
                symbol=symbol,
                decision="blocked_quality",
                reason=quality.reason,
                side=direction,
                spot=spot,
                setup_score=setup["score"],
                quality=quality.to_dict(),
            )
            return {
                **empty,
                "option_quality": quality.to_dict(),
                "live_block_reason": f"option_quality_{quality.reason}",
            }

        # Get candidate strikes (enriched with real premiums when a chain is given)
        strikes = get_hero_zero_strikes(spot, symbol, direction, otm_pct=1.5,
                                        n_strikes=3, option_data=option_data)

        # Pick the primary strike. Prefer a tradeable strike (real premium within
        # the hero-zero band) when the chain is known; else fall back to the
        # balanced T2 tier.
        primary = select_hero_zero_candidate(
            strikes,
            quality=quality.to_dict(),
            side=direction,
        )
        tradeable = [s for s in strikes if s.get("tradeable")]
        if primary is None:
            primary = strikes[1] if len(strikes) > 1 else strikes[0]
        prem = primary.get("premium")

        # If the chain is known and NO strike is actually tradeable (premiums all
        # out of band / illiquid), this isn't a real hero-zero — stand down.
        chain_known = any(s.get("tradeable") is not None for s in strikes)
        if chain_known and not tradeable:
            record_option_decision(
                strategy="hero_zero",
                symbol=symbol,
                decision="blocked_no_tradeable_strike",
                reason="no_tradeable_hero_zero_strike",
                side=direction,
                spot=spot,
                setup_score=setup["score"],
                quality=quality.to_dict(),
                strikes=strikes,
            )
            return empty

        risk_plan = build_hero_zero_risk_plan(primary, primary.get("lot_size"))

        # Score is the hero-zero setup score (capped at 6 — it's always high risk)
        final_score = min(setup["score"] * 0.7, 6.0)

        # CAPITAL-PRESERVATION GATE. The saved backtest (hero_zero_backtest.json)
        # shows 0DTE deep-OTM BUYING is a structural loser (~89-96% expire
        # worthless, profit factor 0.2-0.37, negative ROC) — even with the model
        # understating premiums. So by DEFAULT hero_zero is ALERT-ONLY: it still
        # reports the setup/strikes for information but contributes ZERO confluence
        # score, so the bot never sizes real capital into it. Opt in explicitly
        # with HERO_ZERO_LIVE_VOTE=true only if you accept the measured negative edge.
        mode = hero_zero_mode()
        live_vote = os.getenv("HERO_ZERO_LIVE_VOTE", "false").lower() == "true"
        live_micro_ok = (
            mode == "live_micro"
            and live_vote
            and prem is not None
            and float(prem or 0.0) <= _HZ_LIVE_MICRO_MAX_PREMIUM
        )
        if not live_micro_ok:
            final_score = 0.0

        prem_txt = (f"@ ₹{prem:.1f} premium" if prem else "@ ₹5-₹30 premium (est.)")
        record_option_decision(
            strategy="hero_zero",
            symbol=symbol,
            decision="selected",
            reason="live_micro_enabled" if live_micro_ok else f"{mode}_capital_blocked",
            side=direction,
            spot=spot,
            setup_score=setup["score"],
            quality=quality.to_dict(),
            selected=primary,
            strikes=strikes,
            metadata={"mode": mode, "risk_plan": risk_plan, "live_micro_ok": live_micro_ok},
        )

        return {
            "strategy":      "hero_zero",
            "score":         round(final_score, 2),
            "direction":     direction,
            "side":          direction,
            "spot":          round(spot, 2),
            "verdict":       setup["verdict"],
            "setup_score":   setup["score"],
            "strikes":       strikes,
            "primary_strike":primary,
            "premium":       prem,
            "option_quality": quality.to_dict(),
            "mode":          mode,
            "risk_plan":     risk_plan,
            "live_micro_ok": live_micro_ok,
            "notes":         setup["notes"],
            "max_loss":      "100% of premium (small amount)",
            "target":        "5x–20x on premium",
            "expiry":        "0DTE — today",
            "alert":         (
                f"🎰 HERO-ZERO: Buy {symbol} "
                f"{'CE' if direction=='BUY' else 'PE'} "
                f"{primary['strike']} "
                f"{prem_txt} | Target 5x-20x | Max loss = premium paid"
            ),
        }
    except Exception as e:
        logger.debug("hero_zero: %s", e)
        return empty


def format_hero_zero_alert(result: Dict) -> str:
    """Format hero-zero alert for Telegram."""
    if not result.get("direction"): return ""
    sym    = result.get("symbol","NIFTY")
    dir_   = result.get("direction","BUY")
    opt    = "CE" if dir_ == "BUY" else "PE"
    spot   = result.get("spot", 0)
    strikes= result.get("strikes", [])
    score  = result.get("setup_score", 0)
    notes  = result.get("notes", [])

    lines = [
        f"{'━'*35}",
        f"🎰 <b>HERO OR ZERO — 0DTE PLAY</b>",
        f"  {result.get('verdict','')}",
        f"{'━'*35}",
        f"  Symbol: {sym} | Direction: {dir_} → Buy {opt}",
        f"  Spot:   ₹{spot:,.0f}",
        f"",
        f"  <b>Strike Options:</b>",
    ]
    tiers = ["T1 (Lottery 🎲)", "T2 (Balanced ⚖️)", "T3 (Safer 🛡️)"]
    for i, s in enumerate(strikes[:3]):
        tier = tiers[i] if i < len(tiers) else f"T{i+1}"
        prem = s.get("premium")
        prem_txt = (f" | ₹{prem:.1f}" if prem else "")
        liq = ""
        if s.get("tradeable") is False:
            liq = " ⚠️ illiquid/out-of-band"
        lines.append(
            f"  {tier}: {sym} {s['strike']:.0f} {opt} "
            f"| OTM: {s['otm_pct']:.1f}%{prem_txt}{liq}"
        )

    lines += [
        f"",
        f"  <b>Risk:</b>  Max loss = 100% of premium",
        f"  <b>Target:</b> 5x → 10x → 20x",
        f"  <b>Rule:</b>  Book 50% at 5x. Trail rest.",
        f"  <b>Avoid:</b> After 1:30 PM",
        f"",
        f"  Setup score: {score:.1f}/10",
    ]
    for n in notes[:3]:
        lines.append(f"  {n}")
    lines.append(f"{'━'*35}")
    return "\n".join(lines)


def get_hero_zero_schedule() -> str:
    """Show which index has expiry on which day this week."""
    today = date.today()
    lines = ["📅 <b>HERO-ZERO EXPIRY SCHEDULE</b>", ""]
    days  = ["Mon","Tue","Wed","Thu","Fri"]
    for symbol, wd in sorted(_EXPIRY_DAY.items(), key=lambda x: (x[1], x[0])):
        kind     = "monthly only" if symbol in _MONTHLY_ONLY else "weekly"
        is_today = is_expiry_today(symbol)
        marker   = " ← TODAY 🎰" if is_today else ""
        lines.append(f"  {days[wd]}: {symbol} ({kind}){marker}")
    lines += ["", "Only NIFTY (Tue) & SENSEX (Thu) have weekly 0DTE since",
              "SEBI's Nov-2024 single-weekly rule; the rest are monthly only.",
              "", "Strategy: Buy deep OTM options on expiry day",
              "Window: 9:15–10:00 AM (best) | 10–11 AM (good) | Avoid after 1:30 PM",
              "Risk: 100% of small premium | Target: 5x–20x"]
    return "\n".join(lines)
