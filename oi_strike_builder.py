"""
oi_strike_builder.py — OI Builder Style Strike Price Intelligence

Produces an OI Builder-style Telegram alert showing:
  1. Strike-wise OI heatmap (top 10 strikes around ATM)
  2. Fresh buildup (new positions) vs unwinding (exits)
  3. Max pain strike (where most options expire worthless)
  4. Call/Put writing zones (institutional positioning)
  5. Recommended strike for each setup type
  6. Straddle/Strangle pricing

BUILDUP CATEGORIES (like OI Builder app):
  🟢 Long Buildup   = Price ↑ + OI ↑  (bullish fresh buying)
  🔴 Short Buildup  = Price ↓ + OI ↑  (bearish fresh selling)
  🟡 Long Unwinding = Price ↓ + OI ↓  (bulls exiting)
  🟠 Short Covering = Price ↑ + OI ↓  (shorts covering)
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

_STRIKES_AROUND_ATM = 10   # show 10 strikes each side


def _nse_option_chain(symbol: str = "NIFTY") -> Optional[dict]:
    """Fetch raw NSE option chain."""
    try:
        s = requests.Session()
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
            "Referer": "https://www.nseindia.com/",
        })
        try:
            from nse_proxy import apply as _apply_nse_proxy
            _apply_nse_proxy(s)
        except Exception:
            pass
        s.get("https://www.nseindia.com/", timeout=6)
        r = s.get(
            f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}",
            timeout=12,
        )
        return r.json() if r.status_code == 200 else None
    except Exception as e:
        logger.debug("NSE OC fetch: %s", e)
        return None


def _categorize_buildup(
    price_chg: float,
    oi_chg: float,
) -> Tuple[str, str]:
    """
    Classify OI change into buildup category.
    Returns (category_label, emoji)
    """
    if price_chg > 0 and oi_chg > 0:
        return "Long Buildup", "🟢"
    elif price_chg < 0 and oi_chg > 0:
        return "Short Buildup", "🔴"
    elif price_chg < 0 and oi_chg < 0:
        return "Long Unwinding", "🟡"
    elif price_chg > 0 and oi_chg < 0:
        return "Short Covering", "🟠"
    return "No Change", "⚪"


def analyze_strikes(
    symbol:  str   = "NIFTY",
    expiry:  Optional[str] = None,   # None = nearest expiry
) -> Optional[Dict]:
    """
    Full OI Builder analysis for a symbol.
    Returns structured dict for alert formatting.
    """
    raw = _nse_option_chain(symbol)
    if not raw:
        return None

    records = raw.get("records", {})
    all_data = records.get("data", [])
    spot     = float(records.get("underlyingValue", 0))

    if not spot or not all_data:
        return None

    # ── Filter to nearest expiry ──────────────────────────────────────────
    expiry_dates = sorted(set(r.get("expiryDate","") for r in all_data if r.get("expiryDate")))
    target_expiry = expiry or (expiry_dates[0] if expiry_dates else "")

    data = [r for r in all_data if r.get("expiryDate","") == target_expiry]
    if not data:
        data = all_data

    # ── ATM strike ────────────────────────────────────────────────────────
    strikes = sorted(set(float(r.get("strikePrice", 0)) for r in data))
    atm     = min(strikes, key=lambda s: abs(s - spot)) if strikes else spot

    # ── Build strike table ─────────────────────────────────────────────────
    strike_map: Dict[float, Dict] = {}
    for row in data:
        k = float(row.get("strikePrice", 0))
        if k not in strike_map:
            strike_map[k] = {}
        if "CE" in row:
            ce = row["CE"]
            strike_map[k]["ce_oi"]       = float(ce.get("openInterest", 0))
            strike_map[k]["ce_oi_chg"]   = float(ce.get("changeinOpenInterest", 0))
            strike_map[k]["ce_ltp"]      = float(ce.get("lastPrice", 0))
            strike_map[k]["ce_ltp_chg"]  = float(ce.get("change", 0))
            strike_map[k]["ce_iv"]       = float(ce.get("impliedVolatility", 0))
            strike_map[k]["ce_vol"]      = float(ce.get("totalTradedVolume", 0))
        if "PE" in row:
            pe = row["PE"]
            strike_map[k]["pe_oi"]       = float(pe.get("openInterest", 0))
            strike_map[k]["pe_oi_chg"]   = float(pe.get("changeinOpenInterest", 0))
            strike_map[k]["pe_ltp"]      = float(pe.get("lastPrice", 0))
            strike_map[k]["pe_ltp_chg"]  = float(pe.get("change", 0))
            strike_map[k]["pe_iv"]       = float(pe.get("impliedVolatility", 0))
            strike_map[k]["pe_vol"]      = float(pe.get("totalTradedVolume", 0))

    # ── Select strikes around ATM ─────────────────────────────────────────
    n = _STRIKES_AROUND_ATM
    relevant = [s for s in strikes if abs(s - atm) <= n * 50][:n*2+1]

    # ── Max Pain calculation ───────────────────────────────────────────────
    def _max_pain(smap: dict) -> float:
        best_strike, min_loss = 0.0, float("inf")
        for candidate in smap:
            total_loss = 0.0
            for s, d in smap.items():
                ce_loss = max(0, candidate - s) * d.get("ce_oi", 0)
                pe_loss = max(0, s - candidate) * d.get("pe_oi", 0)
                total_loss += ce_loss + pe_loss
            if total_loss < min_loss:
                min_loss, best_strike = total_loss, candidate
        return best_strike

    max_pain = _max_pain(strike_map)

    # ── PCR ───────────────────────────────────────────────────────────────
    total_ce_oi = sum(d.get("ce_oi", 0) for d in strike_map.values())
    total_pe_oi = sum(d.get("pe_oi", 0) for d in strike_map.values())
    pcr         = total_pe_oi / max(total_ce_oi, 1)

    # ── OI Buildup / Unwinding ─────────────────────────────────────────────
    # Top strikes with fresh call writing (CE OI increase, bearish resistance)
    call_buildup   = sorted(
        [(s, d) for s, d in strike_map.items() if d.get("ce_oi_chg", 0) > 0],
        key=lambda x: -x[1].get("ce_oi_chg", 0)
    )[:4]

    # Top strikes with fresh put writing (PE OI increase, bullish support)
    put_buildup    = sorted(
        [(s, d) for s, d in strike_map.items() if d.get("pe_oi_chg", 0) > 0],
        key=lambda x: -x[1].get("pe_oi_chg", 0)
    )[:4]

    # Call wall = strike above spot with most CE OI (ceiling)
    call_wall = 0.0
    above_calls = {s: d.get("ce_oi", 0) for s, d in strike_map.items() if s > spot}
    if above_calls:
        call_wall = max(above_calls, key=above_calls.get)

    # Put wall = strike below spot with most PE OI (floor)
    put_wall = 0.0
    below_puts = {s: d.get("pe_oi", 0) for s, d in strike_map.items() if s < spot}
    if below_puts:
        put_wall = max(below_puts, key=below_puts.get)

    # ── Straddle premium at ATM ───────────────────────────────────────────
    atm_data   = strike_map.get(atm, {})
    straddle   = atm_data.get("ce_ltp", 0) + atm_data.get("pe_ltp", 0)
    upper_bep  = atm + straddle
    lower_bep  = atm - straddle

    # ── Strike recommendations ─────────────────────────────────────────────
    step = 50  # NIFTY step
    rec = {
        "ce_buy":   call_wall - step if call_wall else atm + step*2,
        "pe_buy":   put_wall  + step if put_wall  else atm - step*2,
        "ce_sell":  call_wall if call_wall else atm + step*3,
        "pe_sell":  put_wall  if put_wall  else atm - step*3,
        "ce_buy_rationale":  "Just below call wall — momentum target",
        "pe_buy_rationale":  "Just above put wall — bounce play",
        "ce_sell_rationale": "At call wall — max resistance",
        "pe_sell_rationale": "At put wall — max support",
    }

    # ── OI momentum table (for heatmap rows) ─────────────────────────────
    table_rows = []
    for s in sorted(relevant):
        d = strike_map.get(s, {})
        ce_oi   = d.get("ce_oi", 0)
        pe_oi   = d.get("pe_oi", 0)
        ce_chg  = d.get("ce_oi_chg", 0)
        pe_chg  = d.get("pe_oi_chg", 0)
        ce_ltp  = d.get("ce_ltp", 0)
        pe_ltp  = d.get("pe_ltp", 0)
        is_atm  = s == atm

        # Bar indicator for OI size
        ce_bar  = "█" * min(5, int(ce_oi / max(total_ce_oi / 50, 1)))
        pe_bar  = "█" * min(5, int(pe_oi / max(total_pe_oi / 50, 1)))

        # OI change arrows
        ce_arrow = "↑" if ce_chg > 0 else "↓" if ce_chg < 0 else "─"
        pe_arrow = "↑" if pe_chg > 0 else "↓" if pe_chg < 0 else "─"

        table_rows.append({
            "strike": s,
            "is_atm": is_atm,
            "is_call_wall": s == call_wall,
            "is_put_wall":  s == put_wall,
            "is_max_pain":  s == max_pain,
            "ce_oi": ce_oi, "ce_chg": ce_chg, "ce_ltp": ce_ltp, "ce_bar": ce_bar,
            "pe_oi": pe_oi, "pe_chg": pe_chg, "pe_ltp": pe_ltp, "pe_bar": pe_bar,
            "ce_arrow": ce_arrow, "pe_arrow": pe_arrow,
        })

    # ── Bias ──────────────────────────────────────────────────────────────
    if pcr >= 1.2:   bias = "📈 BULLISH"
    elif pcr >= 0.9: bias = "⚖️ NEUTRAL"
    elif pcr >= 0.7: bias = "🟠 MILD BEAR"
    else:            bias = "📉 BEARISH"

    return {
        "symbol":        symbol,
        "spot":          spot,
        "atm":           atm,
        "expiry":        target_expiry,
        "pcr":           round(pcr, 2),
        "bias":          bias,
        "max_pain":      max_pain,
        "call_wall":     call_wall,
        "put_wall":      put_wall,
        "straddle":      round(straddle, 1),
        "upper_bep":     round(upper_bep, 0),
        "lower_bep":     round(lower_bep, 0),
        "call_buildup":  [(s, d.get("ce_oi_chg",0)) for s,d in call_buildup],
        "put_buildup":   [(s, d.get("pe_oi_chg",0)) for s,d in put_buildup],
        "recommendations": rec,
        "table":         table_rows,
        "total_ce_oi":   total_ce_oi,
        "total_pe_oi":   total_pe_oi,
    }


def format_oi_alert(data: dict) -> str:
    """
    Format OI Builder analysis into a clean Telegram message.
    Designed to be scannable at a glance on mobile.
    """
    if not data:
        return "❌ Could not fetch option chain data"

    spot     = data["spot"]
    atm      = data["atm"]
    pcr      = data["pcr"]
    bias     = data["bias"]
    max_pain = data["max_pain"]
    call_wall= data["call_wall"]
    put_wall = data["put_wall"]
    straddle = data["straddle"]
    expiry   = data.get("expiry","")
    sym      = data["symbol"]
    ts       = datetime.now().strftime("%H:%M")

    def _lakh(v): return f"{v/100000:.1f}L" if v >= 100000 else f"{v:,.0f}"

    lines = [
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"  🏗️ <b>{sym} OI BUILDER</b>",
        f"  Spot: ₹{spot:,.0f}  ATM: {atm:,.0f}",
        f"  Expiry: {expiry}",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        f"<b>📊 OVERVIEW</b>",
        f"  PCR:      {pcr:.2f}  {bias}",
        f"  Max Pain: ₹{max_pain:,.0f}",
        f"  Call Wall: ₹{call_wall:,.0f}  🔴 (ceiling)",
        f"  Put Wall:  ₹{put_wall:,.0f}  🟢 (floor)",
        f"  Straddle:  ₹{straddle:.0f}  →  {data['lower_bep']:,.0f}–{data['upper_bep']:,.0f}",
        "",
    ]

    # ── OI Heatmap ────────────────────────────────────────────────────────
    lines.append(f"<b>🔥 OI HEATMAP</b>  (CE ← ATM → PE)")
    lines.append(f"  {'Strike':>8}  {'CE OI':>8} {'':2}  {'PE OI':>8}")
    lines.append(f"  {'─'*36}")

    for row in data["table"]:
        s       = row["strike"]
        ce_oi   = row["ce_oi"]
        pe_oi   = row["pe_oi"]
        ce_chg  = row["ce_chg"]
        pe_chg  = row["pe_chg"]
        is_atm  = row["is_atm"]
        is_cw   = row["is_call_wall"]
        is_pw   = row["is_put_wall"]
        is_mp   = row["is_max_pain"]

        # Strike label with markers
        markers = ""
        if is_atm:  markers += " ◀ATM"
        if is_cw:   markers += " 🔴CW"
        if is_pw:   markers += " 🟢PW"
        if is_mp:   markers += " 💀MP"

        ce_str  = _lakh(ce_oi)
        pe_str  = _lakh(pe_oi)
        ca      = "↑" if ce_chg > 0 else "↓" if ce_chg < 0 else " "
        pa      = "↑" if pe_chg > 0 else "↓" if pe_chg < 0 else " "

        line    = f"  {s:>8,.0f}  {ce_str:>7}{ca}  {pe_str:>7}{pa}{markers}"
        if is_atm:
            line = f"<b>{line}</b>"
        lines.append(line)

    lines.append("")

    # ── Fresh Buildup ─────────────────────────────────────────────────────
    lines.append(f"<b>🏗️ FRESH BUILDUP (New Positions)</b>")

    if data["call_buildup"]:
        lines.append("  📞 CALL writing (bearish above):")
        for s, chg in data["call_buildup"][:3]:
            rel = "above spot" if s > spot else "below spot (danger!)"
            lines.append(f"    ↑ {s:,.0f}CE  +{_lakh(chg)} OI  {rel}")

    if data["put_buildup"]:
        lines.append("  📟 PUT writing (bullish below):")
        for s, chg in data["put_buildup"][:3]:
            rel = "below spot" if s < spot else "above spot (danger!)"
            lines.append(f"    ↑ {s:,.0f}PE  +{_lakh(chg)} OI  {rel}")

    lines.append("")

    # ── Strike Recommendations ────────────────────────────────────────────
    rec = data["recommendations"]
    lines += [
        f"<b>🎯 STRIKE SELECTION GUIDE</b>",
        f"  Setup → Strike → Why",
        f"  ─────────────────────────────",
        f"  📞 CE Buy  → {rec['ce_buy']:,.0f}  {rec['ce_buy_rationale']}",
        f"  📟 PE Buy  → {rec['pe_buy']:,.0f}  {rec['pe_buy_rationale']}",
        f"  📞 CE Sell → {rec['ce_sell']:,.0f}  {rec['ce_sell_rationale']}",
        f"  📟 PE Sell → {rec['pe_sell']:,.0f}  {rec['pe_sell_rationale']}",
        f"  🔄 Straddle→ {atm:,.0f}  (both CE+PE, cost ₹{straddle:.0f})",
        "",
        f"<b>⚠️ RISK ZONES</b>",
        f"  Above {call_wall:,.0f}: CE sellers at risk — calls get expensive",
        f"  Below {put_wall:,.0f}: PE sellers at risk — puts get expensive",
        f"  Max Pain: {max_pain:,.0f} — expiry gravitates here",
        "",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"  📱 /oi BANKNIFTY  for BN analysis",
        f"🕐 {ts}",
    ]

    return "\n".join(lines)


def send_oi_builder(
    symbol:  str          = "NIFTY",
    alerts                = None,
    return_text: bool     = False,
) -> Optional[str]:
    """Main entry point. Fetch, analyze, format, send."""
    data   = analyze_strikes(symbol)
    text   = format_oi_alert(data)
    if return_text:
        return text
    if alerts:
        alerts.send(text,
                    dedup_key=f"oi_builder:{symbol}:{datetime.now().strftime('%H%M')}",
                    dedup_cooldown_override=300)
    return text
