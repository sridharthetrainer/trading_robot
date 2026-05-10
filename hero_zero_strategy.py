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

MATH EXAMPLE (NIFTY, 1 lot = 50):
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
EXPIRY DAYS:  NIFTY=Thu, BANKNIFTY=Wed, FINNIFTY=Tue, SENSEX=Fri
"""
from __future__ import annotations

import logging
from datetime import datetime, date, time as dtime
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ── Expiry schedule ───────────────────────────────────────────────────────────
_EXPIRY_DAY = {
    "NIFTY":      3,   # Thursday (weekday 3)
    "BANKNIFTY":  2,   # Wednesday
    "FINNIFTY":   1,   # Tuesday
    "SENSEX":     4,   # Friday
    "MIDCPNIFTY": 0,   # Monday
}

# ── Strike grid (approx lot sizes) ───────────────────────────────────────────
_LOT_SIZES = {
    "NIFTY": 50, "BANKNIFTY": 30, "FINNIFTY": 65,
    "SENSEX": 20, "MIDCPNIFTY": 120,
}
_STRIKE_GAP = {
    "NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50,
    "SENSEX": 100, "MIDCPNIFTY": 25,
}


def is_expiry_today(symbol: str = "NIFTY") -> bool:
    """True if today is expiry day for the given index."""
    idx = symbol.upper()
    today_wd = date.today().weekday()
    expiry_wd = _EXPIRY_DAY.get(idx, 3)
    return today_wd == expiry_wd


def get_hero_zero_strikes(
    spot:       float,
    symbol:     str   = "NIFTY",
    direction:  str   = "BUY",  # BUY=CE, SELL=PE
    otm_pct:    float = 1.5,    # % OTM from spot
    n_strikes:  int   = 3,      # how many strikes to return
) -> List[Dict]:
    """
    Get 2-4 hero-zero candidate strikes OTM from spot.

    Returns multiple strikes at different OTM levels:
      ₹2-5   → deepest OTM (lottery ticket, low prob, huge payout)
      ₹10-30 → moderate OTM (better probability, still hero-zero)
      ₹30-80 → near OTM (highest probability, smaller multiple)

    Strategy: Buy 1 of each OR concentrate on 1 strike based on setup.
    """
    gap = _STRIKE_GAP.get(symbol.upper(), 50)
    lot = _LOT_SIZES.get(symbol.upper(), 50)

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

        strikes.append({
            "strike":      strike,
            "option_type": option_type,
            "otm_pct":     round(i * otm_pct, 1),
            "lot_size":    lot,
            "symbol":      symbol,
            "expiry_type": "0DTE",
            "tier":        f"T{i}",  # T1=deepest OTM, T3=nearest OTM
        })

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
        import pandas as pd
        df_c = df.copy()
        if hasattr(df_c, 'columns'):
            df_c.columns = [c.lower() for c in df_c.columns]
        closes = df_c["close"].values if hasattr(df_c, '__len__') else [0]
        if len(closes) < 5: return empty

        spot      = float(closes[-1])
        prev      = float(closes[-2])
        momentum  = (spot - prev) / prev * 100
        direction = "BUY" if momentum > 0 else "SELL"

        # Get VIX
        vix = float(kw.get("vix", 15) or 15)

        # Get GIFT gap
        gift_gap = float(kw.get("gift_gap", 0) or 0)

        # Score the setup
        setup = score_hero_zero_entry(
            spot=spot, symbol=symbol, direction=direction,
            vix=vix, vix_change=0, gift_gap=gift_gap,
            cpr_break=kw.get("cpr_break", False),
            ema200_cross=kw.get("ema200_cross", False),
            pdh_pdl_break=kw.get("pdh_pdl_break", False),
            oi_buildup=kw.get("oi_buildup", False),
            time_now=now,
        )

        if not setup["enter"]:
            return empty

        # Get candidate strikes
        strikes = get_hero_zero_strikes(spot, symbol, direction, otm_pct=1.5, n_strikes=3)

        # Score is the hero-zero setup score (capped at 6 — it's always high risk)
        final_score = min(setup["score"] * 0.7, 6.0)

        return {
            "strategy":      "hero_zero",
            "score":         round(final_score, 2),
            "direction":     direction,
            "side":          direction,
            "spot":          round(spot, 2),
            "verdict":       setup["verdict"],
            "setup_score":   setup["score"],
            "strikes":       strikes,
            "primary_strike":strikes[1] if len(strikes) > 1 else strikes[0],  # T2 = balanced
            "notes":         setup["notes"],
            "max_loss":      "100% of premium (small amount)",
            "target":        "5x–20x on premium",
            "expiry":        "0DTE — today",
            "alert":         (
                f"🎰 HERO-ZERO: Buy {symbol} "
                f"{'CE' if direction=='BUY' else 'PE'} "
                f"{strikes[1]['strike'] if len(strikes)>1 else strikes[0]['strike']} "
                f"@ ₹5-₹30 premium | Target 5x-20x | Max loss = premium paid"
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
        lines.append(
            f"  {tier}: {sym} {s['strike']:.0f} {opt} "
            f"| OTM: {s['otm_pct']:.1f}%"
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
    for symbol, wd in sorted(_EXPIRY_DAY.items(), key=lambda x: x[1]):
        is_today = wd == today.weekday()
        marker   = " ← TODAY 🎰" if is_today else ""
        lines.append(f"  {days[wd]}: {symbol}{marker}")
    lines += ["", "Strategy: Buy deep OTM options on expiry day",
              "Window: 9:15–10:00 AM (best) | 10–11 AM (good) | Avoid after 1:30 PM",
              "Risk: 100% of small premium | Target: 5x–20x"]
    return "\n".join(lines)
