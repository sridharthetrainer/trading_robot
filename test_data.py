#!/usr/bin/env python3
"""Quick diagnostic — run: python3 test_data.py"""
import sys, os
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv(dotenv_path=".env", override=True)

import config as cfg
from datetime import datetime, timedelta

print("═"*50)
print("TRADING BOT DATA SOURCE TEST")
print("═"*50)
print(f"PAPER_TRADING : {cfg.PAPER_TRADING}")
print(f"API_KEY       : {(cfg.API_KEY or '')[:4]}****")
print(f"TIINGO_KEY    : {os.getenv('TIINGO_KEY','')[:8]}****")
print(f"TWELVE_KEY    : {os.getenv('TWELVE_DATA_KEY','')[:8]}****")
print(f"FYERS_TOKEN   : {'SET' if os.getenv('FYERS_TOKEN') else 'MISSING'}")
print(f"GITHUB_TOKEN  : {'SET' if os.getenv('GITHUB_TOKEN') else 'MISSING'}")
print()

import requests
s = requests.Session()
s.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://www.nseindia.com"})
s.get("https://www.nseindia.com/", timeout=5)

# 1. NSE Live
print("─── NSE Live (allIndices) ──────────────")
try:
    r = s.get("https://www.nseindia.com/api/allIndices", timeout=8)
    for idx in r.json().get("data",[]):
        if "NIFTY 50" in str(idx.get("index","")):
            print(f"  ✅ NIFTY ₹{idx.get('last')} (change {idx.get('percentChange')}%)")
            break
except Exception as e:
    print(f"  ❌ {e}")

# 2. Stooq
print("─── Stooq (NSE EOD) ─────────────────────")
try:
    end_s   = datetime.now().strftime("%Y%m%d")
    start_s = (datetime.now()-timedelta(days=30)).strftime("%Y%m%d")
    r2 = requests.get(
        f"https://stooq.com/q/d/l/?s=^nsei&d1={start_s}&d2={end_s}&i=d",
        timeout=10, headers={"User-Agent":"Mozilla/5.0"})
    lines = [l for l in r2.text.strip().split("\n") if l]
    if len(lines) > 2:
        last = lines[-1].split(",")
        print(f"  ✅ {len(lines)-1} bars | Last: {last[0]} close=₹{last[4]}")
    else:
        print(f"  ❌ No data (HTTP {r2.status_code})")
except Exception as e:
    print(f"  ❌ {e}")

# 3. Stooq for a stock
print("─── Stooq (RELIANCE stock) ─────────────")
try:
    r3 = requests.get(
        f"https://stooq.com/q/d/l/?s=reliance.in&d1={start_s}&d2={end_s}&i=d",
        timeout=10, headers={"User-Agent":"Mozilla/5.0"})
    lines3 = [l for l in r3.text.strip().split("\n") if l]
    if len(lines3) > 2:
        last3 = lines3[-1].split(",")
        print(f"  ✅ RELIANCE: {len(lines3)-1} bars | Last: {last3[0]} ₹{last3[4]}")
    else:
        print(f"  ❌ {r3.text[:60]}")
except Exception as e:
    print(f"  ❌ {e}")

# 4. Tiingo
print("─── Tiingo (key configured) ────────────")
try:
    key = os.getenv("TIINGO_KEY","43f3cb0bc2a1ea5afd7d8b33c084d584e44ba65b")
    end_s2   = datetime.now().strftime("%Y-%m-%d")
    start_s2 = (datetime.now()-timedelta(days=10)).strftime("%Y-%m-%d")
    r4 = requests.get(
        f"https://api.tiingo.com/tiingo/daily/AAPL/prices",
        params={"startDate":start_s2,"endDate":end_s2,"token":key},
        headers={"Authorization":f"Token {key}"},
        timeout=10)
    if r4.status_code == 200 and r4.json():
        last4 = r4.json()[-1]
        print(f"  ✅ Connected | AAPL last: ${last4.get('close')}")
    else:
        print(f"  ❌ HTTP {r4.status_code}: {r4.text[:60]}")
except Exception as e:
    print(f"  ❌ {e}")

# 5. Twelve Data
print("─── Twelve Data ────────────────────────")
try:
    tk = os.getenv("TWELVE_DATA_KEY","")
    if tk:
        r5 = requests.get(
            "https://api.twelvedata.com/price",
            params={"symbol":"AAPL","apikey":tk},
            timeout=10)
        if "price" in r5.text:
            print(f"  ✅ Connected: {r5.text[:40]}")
        else:
            print(f"  ❌ {r5.text[:60]}")
    else:
        print("  ❌ TWELVE_DATA_KEY not in env")
except Exception as e:
    print(f"  ❌ {e}")


# 6b. Fyers historical data for NSE
print("─── Fyers (NSE historical) ─────────────")
try:
    import time as _t
    token = os.getenv("FYERS_TOKEN","")
    if token:
        end_ts   = int(_t.time())
        start_ts = int(_t.time()) - 30*86400  # 30 days ago
        r6 = requests.get(
            "https://api.fyers.in/data/v3/history",
            params={"symbol":"NSE:NIFTY50-INDEX","resolution":"D",
                    "date_format":"1","range_from":str(start_ts),
                    "range_to":str(end_ts),"cont_flag":"1"},
            headers={"Authorization":f"Bearer {token}"},
            timeout=10)
        if r6.status_code == 200:
            candles = r6.json().get("candles",[])
            if candles:
                last_c = candles[-1]
                print(f"  ✅ NIFTY: {len(candles)} bars | Last close ₹{last_c[4]}")
            else:
                print(f"  ❌ No candles: {r6.json().get('message','')}")
        else:
            print(f"  ❌ HTTP {r6.status_code}: {r6.text[:60]}")
    else:
        print("  ❌ FYERS_TOKEN not set")
except Exception as e:
    print(f"  ❌ {e}")

# 6. Angel One connect + historical
print("─── Angel One ──────────────────────────")
try:
    from angel import AngelOne
    a = AngelOne(cfg.API_KEY, cfg.CLIENT_ID, cfg.PASSWORD,
                 cfg.TOTP_SECRET, paper_trade=False)
    ok = a.connect()
    print(f"  Connect: {'✅' if ok else '❌'}")
    if ok:
        from datetime import datetime as _dt
        h = a.get_historical_data(
            symbol="NIFTY", interval="ONE_DAY",
            from_date=(_dt.now()-timedelta(days=10)).strftime("%Y-%m-%d %H:%M"),
            to_date=_dt.now().strftime("%Y-%m-%d %H:%M"),
            exchange="NSE")
        print(f"  Historical bars: {len(h) if h is not None else 'None'}")
except Exception as e:
    print(f"  ❌ {e}")

# 7. DataFetcher full test
print()
print("─── DataFetcher.get_latest_data ────────")
try:
    from data_fetcher import DataFetcher
    df_obj = DataFetcher(angel=None)
    data = df_obj.get_latest_data()
    print(f"  Symbols with data: {len(data)}")
    for sym in list(data.keys())[:5]:
        d = data[sym]
        print(f"  {sym}: {len(d)} bars | last close ₹{float(d['close'].iloc[-1]):,.0f}")
except Exception as e:
    print(f"  ❌ {e}")

print()
print("═"*50)
