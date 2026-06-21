"""
morning_brief.py — Institutional Morning Intelligence Brief

Framework inspired by:
  - Goldman Sachs morning call format
  - JP Morgan "Eye on the Market" — Michael Cembalest
  - SAHI-style India equity research methodology
  - Zerodha Varsity market analysis structure
  - Dalal Street Journal pre-market format

Sends at 8:30 AM every trading day.
        # Macro data (GAP 5)
        try:
            from data_source_resilience import get_macro_data
            _mc = get_macro_data()
            if _mc:
                _mp = []
                if _mc.get('repo_rate'): _mp.append(f"Repo={_mc['repo_rate']:.2f}%")
                if _mc.get('fed_rate'):  _mp.append(f"Fed={_mc['fed_rate']:.2f}%")
                if _mc.get('india_cpi'): _mp.append(f"CPI={_mc['india_cpi']:.1f}%")
                if _mp: lines.append(f"  📊 Macro: {' | '.join(_mp)}")
        except Exception: pass

Covers everything a professional trader needs before 9:15 AM.
"""
from __future__ import annotations
import logging
from datetime import datetime, date
from typing import Optional

logger = logging.getLogger(__name__)


def _fetch_global_snapshot() -> dict:
    """Real global market data from Yahoo Finance JSON API."""
    result = {}
    tickers = {
        "SP500":    "^GSPC",
        "DOW":      "^DJI",
        "NASDAQ":   "^IXIC",
        "SGXNIFTY": "^NSEI",
        "NIKKEI":   "^N225",
        "HANGSENG": "^HSI",
        "DXY":      "DX-Y.NYB",
        "GOLD":     "GC=F",
        "BRENT":    "BZ=F",
        "USDINR":   "USDINR=X",
        "USVIX":    "^VIX",
    }
    try:
        # Stooq → Yahoo → AlphaVantage (via cross_asset._fetch_yahoo_price)
        from cross_asset import _fetch_yahoo_price as _gfetch
        for key, ticker in tickers.items():
            try:
                curr, prev = _gfetch(ticker)
                if curr > 0:
                    chg = (curr - prev) / prev * 100 if prev else 0
                    result[key] = {"price": curr, "chg": round(chg, 2)}
            except Exception:
                pass
    except Exception as e:
        logger.debug("global_snapshot: %s", e)


    # Fallback for missing tickers: use cross_asset (Stooq/Alpha/NSE)
    missing = {k: v for k, v in tickers.items() if k not in result}
    if missing:
        try:
            from cross_asset import get_cross_asset_data
            _ca = get_cross_asset_data()
            # cross_asset uses keys: SP500, USVIX, DXY, BRENT, GOLD, USDINR
            # morning_brief uses: SP500, USVIX, DXY — same keys! Direct lookup.
            for mb_key, ticker in missing.items():
                # Map morning_brief key to cross_asset key
                _mbmap = {
                    "SP500":"SP500", "DOW":"DOW", "NASDAQ":"NASDAQ",
                    "USVIX":"USVIX", "USDINR":"USDINR", "DXY":"DXY",
                    "BRENT":"BRENT", "GOLD":"GOLD", "NIKKEI":"NIKKEI",
                    "SGXNIFTY":"SGXNIFTY",
                }
                ca_key  = _mbmap.get(mb_key, mb_key)
                ca_data = _ca.get(ca_key, {})
                if ca_data:
                    price = float(ca_data.get("price", ca_data.get("last", 0)))
                    chg   = float(ca_data.get("change_pct", 0))
                    if price > 0:
                        result[mb_key] = {"price": price, "chg": round(chg, 2)}
        except Exception: pass
    # NSE for Indian indices still missing
    if "^NSEI" not in [tickers.get(k) for k in result] and "SGXNIFTY" in tickers:
        try:
            from yf_compat import _nse_price
            nifty_px = _nse_price("NIFTY 50")
            if nifty_px > 0:
                result["SGXNIFTY"] = {"price": nifty_px, "chg": 0.0}
        except Exception: pass

    return result


def _fetch_india_vix() -> float:
    try:
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://www.nseindia.com"})
        s.get("https://www.nseindia.com/", timeout=4)
        r = s.get("https://www.nseindia.com/api/allIndices", timeout=7)
        for idx in r.json().get("data", []):
            if "INDIA VIX" in str(idx.get("index", "")).upper():
                return float(idx.get("last", 0) or 0)
    except Exception:
        pass
    return 0.0


def _fetch_nifty_futures_gap() -> str:
    """SGX/GIFT Nifty vs previous NIFTY close = pre-market gap."""
    try:
        import requests
        url = "https://query1.finance.yahoo.com/v8/finance/chart/^NSEI?interval=1d&range=2d"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=6)
        meta = r.json()["chart"]["result"][0]["meta"]
        prev_close = float(meta.get("chartPreviousClose") or 0)
        curr = float(meta.get("regularMarketPrice") or 0)
        if prev_close and curr:
            gap = (curr - prev_close) / prev_close * 100
            gap_pts = curr - prev_close
            return f"{gap:+.2f}% ({gap_pts:+.0f} pts)"
    except Exception:
        pass
    return "N/A"


def _fetch_fii_preliminary() -> str:
    """Quick FII/DII status."""
    try:
        from fii_data_fetcher import fii_dii_telegram_report
        data = fii_dii_telegram_report()
        # Extract just the net line
        for line in data.split("\n"):
            if "FII Net" in line or "5d cumulative" in line:
                return line.strip()
    except Exception:
        pass
    return "FII data updating..."


def _get_key_levels() -> dict:
    """NIFTY key support/resistance from pivot points."""
    try:
        import requests
        url = "https://query1.finance.yahoo.com/v8/finance/chart/^NSEI?interval=1d&range=5d"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=6)
        data = r.json()["chart"]["result"][0]
        quotes = data["indicators"]["quote"][0]
        highs  = [h for h in quotes.get("high", []) if h]
        lows   = [l for l in quotes.get("low", []) if l]
        closes = [c for c in quotes.get("close", []) if c]
        if highs and lows and closes:
            H = highs[-1]; L = lows[-1]; C = closes[-1]
            PP = (H + L + C) / 3
            R1 = 2*PP - L
            S1 = 2*PP - H
            R2 = PP + (H - L)
            S2 = PP - (H - L)
            return {"PP": PP, "R1": R1, "R2": R2, "S1": S1, "S2": S2}
    except Exception:
        pass
    return {}


def generate_morning_brief(alerts=None) -> str:
    """
    Full institutional morning brief.
    Goldman Sachs-style: data first, opinion second.
    """
    now = datetime.now().strftime("%d-%b-%Y %H:%M")
    today = date.today().strftime("%A, %d %B %Y")

    lines = [
        f"🌅 <b>MORNING INTELLIGENCE BRIEF</b>",
        f"  {today}  |  {now}",
        f"  {'─'*35}",
        "",
    ]

    # Global markets
    global_data = _fetch_global_snapshot()
    lines.append("  <b>🌍 GLOBAL MARKETS</b>")

    def _gfmt(key, label, decimals=0):
        d = global_data.get(key, {})
        if not d or not d.get("price"):
            return f"  {label:12} N/A"
        px  = d["price"]
        chg = d.get("chg", 0)
        icon = "🟢" if chg > 0 else "🔴" if chg < 0 else "⚪"
        return f"  {label:12} {px:>10,.{decimals}f}  {icon}{chg:+.2f}%"

    lines += [
        _gfmt("SP500",   "S&P 500",   0),
        _gfmt("NASDAQ",  "NASDAQ",    0),
        _gfmt("NIKKEI",  "Nikkei",    0),
        _gfmt("HANGSENG","Hang Seng", 0),
        _gfmt("DXY",     "Dollar(DXY)",2),
        _gfmt("GOLD",    "Gold",      0),
        _gfmt("BRENT",   "Brent Oil", 1),
        _gfmt("USDINR",  "USD/INR",   2),
        "",
    ]

    # India pre-market
    lines.append("  <b>🇮🇳 INDIA PRE-MARKET</b>")
    vix = _fetch_india_vix()
    nifty_gap = _fetch_nifty_futures_gap()

    vix_icon = "🟢" if vix < 15 else "🟡" if vix < 20 else "🔴"
    vix_comment = "Low fear — good for trading" if vix < 15 else \
                  "Moderate — standard sizing" if vix < 20 else \
                  "Elevated — reduce size by 30%" if vix < 25 else \
                  "HIGH — options buying restricted"

    lines += [
        f"  NIFTY Gap:    {nifty_gap}",
        f"  India VIX:    {vix:.1f}  {vix_icon} {vix_comment}",
        "",
    ]

    # Key levels
    levels = _get_key_levels()
    if levels:
        lines += [
            "  <b>📐 KEY NIFTY LEVELS</b>",
            f"  Resistance 2:  {levels['R2']:>8,.0f}",
            f"  Resistance 1:  {levels['R1']:>8,.0f}",
            f"  Pivot:         {levels['PP']:>8,.0f}",
            f"  Support 1:     {levels['S1']:>8,.0f}",
            f"  Support 2:     {levels['S2']:>8,.0f}",
            "",
        ]

    # Sector rotation
    try:
        from sector_rotation_engine import get_top_sectors, get_avoid_sectors
        top    = get_top_sectors(3)
        avoid  = get_avoid_sectors(2)
        lines += [
            "  <b>🔄 SECTOR ROTATION</b>",
            f"  Overweight:   {', '.join(top) if top else 'Updating...'}",
            f"  Underweight:  {', '.join(avoid) if avoid else 'Updating...'}",
            "",
        ]
    except Exception:
        pass

    # FII snapshot
    fii_line = _fetch_fii_preliminary()
    lines += [
        "  <b>💰 INSTITUTIONAL FLOW</b>",
        f"  {fii_line}",
        "",
    ]

    # Market bias summary
    try:
        from cross_asset import get_cross_asset_data, get_market_bias
        macro = get_cross_asset_data()
        bias  = get_market_bias(macro)
        bias_str = "🟢 BULLISH" if bias > 0.3 else "🔴 BEARISH" if bias < -0.3 else "⚪ NEUTRAL"
        lines += [
            "  <b>🎯 MARKET BIAS</b>",
            f"  Global macro score: {bias:+.2f}",
            f"  Direction: {bias_str}",
            "",
        ]
    except Exception:
        pass

    # Trading plan
    lines += [
        "  <b>📋 TODAY'S PLAN</b>",
        f"  Scan starts:   9:15 AM (196 symbols)",
        f"  🎯 Sentiment:  see /score command",
        f"  Strategy mix:  Trend + ORB + CPR/EMA",
        f"  Max signals:   8 per day (quality filtered)",
        f"  Position sizing: Risk 1% of YOUR capital per trade",
        "",
        f"  ⏰ Next update: EOD summary post 3:30 PM",
        f"  {'─'*35}",
        f"  🤖 Autonomous Bot | LIVE mode",
    ]

    return "\n".join(lines)


def send_morning_brief(alerts=None):
    """Called at 8:30 AM by scheduler. Sends to owner + public channels."""
    try:
        msg = generate_morning_brief(alerts)
        if alerts:
            # Owner gets full detailed brief
            alerts.send(msg, dedup_key="morning_brief", dedup_cooldown_override=43200)
            # Public channels get universal brief (no personal fund data)
            try:
                from public_signal_formatter import format_public_morning_brief
                import os
                free_ch = os.getenv("TELEGRAM_FREE_CHANNEL_ID", "")
                prem_ch = os.getenv("TELEGRAM_PREMIUM_CHANNEL_ID", "")
                # FIX: extract real global data instead of hardcoded {}
                try:
                    _gd = _fetch_global_snapshot()
                    _vix = _fetch_india_vix()
                    _bias = 0.0
                    try:
                        from cross_asset import get_cross_asset_data, get_market_bias
                        _bias = get_market_bias(get_cross_asset_data())
                    except Exception: pass
                except Exception:
                    _gd, _vix, _bias = {}, 0.0, 0.0
                pub_data = {
                    "global":         _gd,
                    "india_vix":      _vix,
                    "bias":           _bias,
                    "levels":         {},
                    "top_sectors":    [],
                    "avoid_sectors":  []
                }
                pub_msg = format_public_morning_brief(pub_data)
                for ch_id in [free_ch, prem_ch]:
                    if ch_id:
                        alerts.send_to_channel(ch_id, pub_msg)
            except Exception: pass
        return msg
    except Exception as e:
        logger.warning("morning_brief: %s", e)
        return ""
