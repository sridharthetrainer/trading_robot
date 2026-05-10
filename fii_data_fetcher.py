"""
fii_data_fetcher.py — Real FII/DII data from NSE + SEBI

Fetches actual FII/DII cash + derivatives data and stores
in fii_history.csv for pattern analysis.

Sources (in priority order):
  1. NSE participant-wise OI JSON (daily, free, real-time)
  2. SEBI FII/DII daily report (EOD, authoritative)
  3. BSE/NSE bhav copy delivery data
  4. Moneycontrol scrape (fallback)

Run automatically: daily at 4 PM after market close.
Telegram: /fii shows today's data + 5-day trend.
"""
from __future__ import annotations
import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)
_FII_CSV = Path("fii_history.csv")
_OI_JSON = Path("participant_oi_history.json")


def fetch_nse_fii_dii_today() -> Dict:
    """
    Fetch today's FII/DII cash market activity from NSE.
    Returns dict with fii_net, dii_net, fii_buy, fii_sell etc.
    """
    try:
        import requests
        s = requests.Session()
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64)",
            "Referer":    "https://www.nseindia.com",
            "Accept":     "application/json",
        })
        s.get("https://www.nseindia.com/", timeout=5)

        # Source 1: NSE FII/DII activity
        today_str = date.today().strftime("%d-%m-%Y")
        r = s.get(
            "https://www.nseindia.com/api/fiidii",
            timeout=10)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and data:
                latest = data[0]
                return {
                    "date":       date.today().isoformat(),
                    "fii_buy":    float(latest.get("fiiBuy",     0) or 0),
                    "fii_sell":   float(latest.get("fiiSell",    0) or 0),
                    "fii_net":    float(latest.get("fiiNet",     0) or 0),
                    "dii_buy":    float(latest.get("diiBuy",     0) or 0),
                    "dii_sell":   float(latest.get("diiSell",    0) or 0),
                    "dii_net":    float(latest.get("diiNet",     0) or 0),
                    "source":     "NSE_FIIDII",
                }
    except Exception as e:
        logger.debug("NSE fiidii: %s", e)

    # Source 2: NSE participant-wise derivatives OI
    try:
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://www.nseindia.com"})
        s.get("https://www.nseindia.com/", timeout=5)
        r2 = s.get(
            "https://www.nseindia.com/api/participant-stats-json?dd=all",
            timeout=10)
        if r2.status_code == 200:
            rows = r2.json()
            if rows:
                latest = rows[-1]  # most recent
                fii = latest.get("FII", {})
                dii = latest.get("DII", {})
                net_fii = float(fii.get("net_cash", 0) or 0)
                net_dii = float(dii.get("net_cash", 0) or 0)
                if net_fii != 0 or net_dii != 0:
                    return {
                        "date":     date.today().isoformat(),
                        "fii_net":  net_fii,
                        "dii_net":  net_dii,
                        "fii_buy":  float(fii.get("buy",0) or 0),
                        "fii_sell": float(fii.get("sell",0) or 0),
                        "dii_buy":  float(dii.get("buy",0) or 0),
                        "dii_sell": float(dii.get("sell",0) or 0),
                        "fii_fut_long":  float(fii.get("fut_long",0) or 0),
                        "fii_fut_short": float(fii.get("fut_short",0) or 0),
                        "fii_call_long": float(fii.get("call_long",0) or 0),
                        "fii_call_short":float(fii.get("call_short",0) or 0),
                        "fii_put_long":  float(fii.get("put_long",0) or 0),
                        "fii_put_short": float(fii.get("put_short",0) or 0),
                        "source":   "NSE_PARTICIPANT",
                    }
    except Exception as e:
        logger.debug("NSE participant: %s", e)

    # Source 3: NSE historical FII/DII (equity segment)
    try:
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://www.nseindia.com"})
        s.get("https://www.nseindia.com/", timeout=5)
        today_str = date.today().strftime("%d-%m-%Y")
        r3 = s.get(
            f"https://www.nseindia.com/api/fii-dii?from={today_str}&to={today_str}",
            timeout=10)
        if r3.status_code == 200:
            data3 = r3.json()
            if data3 and isinstance(data3, list):
                d = data3[-1]
                return {
                    "date":    date.today().isoformat(),
                    "fii_buy": float(d.get("fiiBuy",0) or 0),
                    "fii_sell":float(d.get("fiiSell",0) or 0),
                    "fii_net": float(d.get("fiiNet",0) or 0),
                    "dii_buy": float(d.get("diiBuy",0) or 0),
                    "dii_sell":float(d.get("diiSell",0) or 0),
                    "dii_net": float(d.get("diiNet",0) or 0),
                    "source":  "NSE_FII_DII",
                }
    except Exception as e:
        logger.debug("NSE fii-dii: %s", e)

    return {}


def save_fii_data(data: Dict) -> bool:
    """Append FII/DII data to CSV, avoiding duplicates."""
    if not data or not data.get("fii_net") and not data.get("dii_net"):
        return False
    try:
        df = pd.read_csv(str(_FII_CSV)) if _FII_CSV.exists() else pd.DataFrame()
        today = data.get("date", date.today().isoformat())
        # Remove today's entry if exists (update)
        if len(df) and "date" in df.columns:
            df = df[df["date"] != today]
        new_row = pd.DataFrame([data])
        df = pd.concat([df, new_row], ignore_index=True)
        df = df.sort_values("date").tail(90)  # keep 90 days
        df.to_csv(str(_FII_CSV), index=False)
        return True
    except Exception as e:
        logger.debug("save_fii: %s", e)
        return False


def get_fii_history(days: int = 10) -> pd.DataFrame:
    """Load recent FII/DII history from CSV."""
    try:
        if _FII_CSV.exists():
            df = pd.read_csv(str(_FII_CSV))
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"])
                return df.sort_values("date").tail(days)
    except Exception: pass
    return pd.DataFrame()


def fii_dii_telegram_report() -> str:
    """
    Full institutional money flow report for /fii command.
    Includes 5-day trend, sentiment, derivatives positioning.
    """
    # Fetch fresh data
    today_data = fetch_nse_fii_dii_today()
    if today_data:
        save_fii_data(today_data)

    hist = get_fii_history(10)
    from datetime import datetime as _dt
    lines = [f"💰 <b>FII/DII INSTITUTIONAL FLOW</b> | {_dt.now().strftime('%d-%b %H:%M')}",
             ""]

    # Today's data
    if today_data:
        fii_n = today_data.get("fii_net", 0)
        dii_n = today_data.get("dii_net", 0)
        fii_icon = "🟢" if fii_n > 0 else "🔴"
        dii_icon = "🟢" if dii_n > 0 else "🔴"
        lines += [
            f"  <b>TODAY ({today_data.get('source','NSE')})</b>",
            f"  {fii_icon} FII Net:  ₹{fii_n:>8,.0f} Cr",
            f"  {dii_icon} DII Net:  ₹{dii_n:>8,.0f} Cr",
            "",
        ]
        # Options positioning
        cl = today_data.get("fii_call_long", 0)
        cs = today_data.get("fii_call_short", 0)
        pl = today_data.get("fii_put_long",  0)
        ps = today_data.get("fii_put_short", 0)
        if any([cl, cs, pl, ps]):
            net_put_writing = ps - pl
            net_call_writing = cs - cl
            opt_bias = "🟢 BULLISH" if net_put_writing > net_call_writing else "🔴 BEARISH"
            lines += [
                f"  <b>FII OPTIONS</b> → {opt_bias}",
                f"     Call short: {cs:>8,.0f}  (writing = cap)",
                f"     Put short:  {ps:>8,.0f}  (writing = support)",
                "",
            ]
        # Futures positioning
        fl = today_data.get("fii_fut_long", 0)
        fs = today_data.get("fii_fut_short", 0)
        if fl or fs:
            fut_bias = "🟢 LONG" if fl > fs else "🔴 SHORT"
            lines += [
                f"  <b>FII FUTURES</b> → {fut_bias}",
                f"     Long:  {fl:>8,.0f}  Short: {fs:>8,.0f}",
                "",
            ]
    else:
        lines.append("  ⚠️ No real-time data (market may be closed)")
        lines.append("")

    # 5-day trend
    if len(hist) >= 3 and "fii_net" in hist.columns:
        lines.append("  <b>5-DAY FII TREND</b>")
        for _, row in hist.tail(5).iterrows():
            dt = str(row["date"])[:10] if "date" in row.index else "?"
            fn = float(row.get("fii_net", 0) or 0)
            dn = float(row.get("dii_net", 0) or 0)
            ficon = "▲" if fn > 0 else "▼"
            lines.append(f"   {dt[-5:]}: FII {ficon}₹{abs(fn):,.0f}Cr  DII ₹{dn:+,.0f}Cr")
        # 5-day cumulative
        fii_5d = float(hist.tail(5)["fii_net"].sum()) if "fii_net" in hist.columns else 0
        dii_5d = float(hist.tail(5)["dii_net"].sum()) if "dii_net" in hist.columns else 0
        sentiment = "🟢 NET BUYERS" if fii_5d > 2000 else "🔴 NET SELLERS" if fii_5d < -2000 else "⚪ NEUTRAL"
        lines += [
            "",
            f"  5d cumulative: ₹{fii_5d:+,.0f}Cr → {sentiment}",
        ]
        if fii_5d > 2000:
            lines.append("  📈 Signal boost +0.5 active for large-cap BUY signals")
    else:
        lines.append("  ℹ️ Historical data builds up daily — check back tomorrow")

    return "\n".join(lines)


def refresh_fii_data_eod():
    """Called at 4 PM daily to fetch and store FII/DII data."""
    data = fetch_nse_fii_dii_today()
    if data and save_fii_data(data):
        logger.info("FII/DII data saved: FII=₹%.0f Cr DII=₹%.0f Cr",
                    data.get("fii_net",0), data.get("dii_net",0))
    else:
        logger.warning("FII/DII data not available today")
    return data
