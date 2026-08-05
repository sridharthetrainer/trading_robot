#!/usr/bin/env python3
"""Direct Angel One SmartAPI test — run: python3 test_angel_direct.py"""
import sys, os
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv(dotenv_path=".env", override=True)

import pyotp
from SmartApi import SmartConnect

API_KEY    = os.getenv("API_KEY")
CLIENT_ID  = os.getenv("CLIENT_ID")
PASSWORD   = os.getenv("PASSWORD")
TOTP_SECRET= os.getenv("TOTP_SECRET")

print(f"API_KEY:   {'SET' if API_KEY else 'MISSING'}")
print(f"CLIENT_ID: {'SET' if CLIENT_ID else 'MISSING'}")
print()

# Step 1: Connect
print("── Step 1: Connect ─────────────────────")
obj = SmartConnect(api_key=API_KEY)
totp = pyotp.TOTP(TOTP_SECRET).now()
try:
    data = obj.generateSession(CLIENT_ID, PASSWORD, totp)
    print(f"  Login status: {data.get('status','?')}")
    print(f"  Message:      {data.get('message','?')}")
    if data.get("data"):
        jwt = data["data"].get("jwtToken","")
        print(f"  JWT:          {jwt[:30]}...")
    else:
        print("  ❌ No data in response")
        sys.exit(1)
except Exception as e:
    print(f"  ❌ Login failed: {e}")
    sys.exit(1)

# Step 2: Test getCandleData directly
print()
print("── Step 2: getCandleData NIFTY 1-day ──")
try:
    hist_params = {
        "exchange":    "NSE",
        "symboltoken": "99926000",  # NIFTY 50 index token
        "interval":    "ONE_DAY",
        "fromdate":    "2026-03-01 09:00",
        "todate":      "2026-04-08 15:30",
    }
    print(f"  Params: {hist_params}")
    resp = obj.getCandleData(hist_params)
    print(f"  Raw response: {str(resp)[:200]}")
    if resp:
        candles = resp.get("data", [])
        print(f"  Candles: {len(candles)}")
        if candles:
            print(f"  Last:   {candles[-1]}")
except Exception as e:
    print(f"  ❌ {e}")

# Step 3: Test with RELIANCE stock
print()
print("── Step 3: getCandleData RELIANCE ─────")
try:
    # First find RELIANCE token
    scrip = obj.searchScrip("NSE", "RELIANCE")
    if scrip and scrip.get("data"):
        rel_token = scrip["data"][0]["symboltoken"]
        print(f"  RELIANCE token: {rel_token}")
        resp2 = obj.getCandleData({
            "exchange":    "NSE",
            "symboltoken": rel_token,
            "interval":    "ONE_DAY",
            "fromdate":    "2026-03-01 09:00",
            "todate":      "2026-04-08 15:30",
        })
        candles2 = resp2.get("data",[]) if resp2 else []
        print(f"  RELIANCE candles: {len(candles2)}")
        if candles2: print(f"  Last: {candles2[-1]}")
    else:
        print(f"  Search result: {scrip}")
except Exception as e:
    print(f"  ❌ {e}")

# Step 4: Get balance
print()
print("── Step 4: Balance ─────────────────────")
try:
    bal = obj.rmsLimit()
    print(f"  {str(bal)[:200]}")
except Exception as e:
    print(f"  ❌ {e}")

# Step 5: Test stock token from master contract
print()
print("── Step 5: Stock token lookup ──────────")
try:
    import pandas as pd, requests as rq
    r = rq.get(
        "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json",
        timeout=20)
    df_mc = pd.DataFrame(r.json())
    print(f"  Total instruments: {len(df_mc)}")
    if "exch_seg" in df_mc.columns:
        nse = df_mc[df_mc["exch_seg"].str.upper() == "NSE"]
        print(f"  NSE EQ instruments: {len(nse)}")
        # Find RELIANCE
        rel = nse[nse["symbol"].str.contains("RELIANCE", na=False)].head(3)
        print(f"  RELIANCE rows: {rel[['symbol','token','name']].to_string()}")
        # Save full file
        df_mc.to_csv("MasterContract_ALL.csv", index=False)
        print(f"  ✅ MasterContract_ALL.csv saved")
        
        # Test getCandleData with RELIANCE token
        if len(rel) > 0:
            rel_token = str(rel.iloc[0]["token"])
            print(f"\n── Step 6: getCandleData RELIANCE ({rel_token}) ─")
            resp3 = obj.getCandleData({
                "exchange":    "NSE",
                "symboltoken": rel_token,
                "interval":    "ONE_DAY",
                "fromdate":    "2026-03-01 09:00",
                "todate":      "2026-04-08 15:30",
            })
            c3 = resp3.get("data",[]) if resp3 else []
            print(f"  RELIANCE candles: {len(c3)}")
            if c3: print(f"  Last: {c3[-1]}")
except Exception as e:
    print(f"  ❌ {e}")

# Step 7: Syntax check all files
print()
print("── Syntax check (top 10 files) ────────")
import ast, os, sys
errors = []
for f in sorted(os.listdir('.')):
    if f.endswith('.py'):
        try: ast.parse(open(f).read())
        except SyntaxError as e: errors.append(f"{f}:{e.lineno}: {e.msg}")
print(f"  Total .py files: {sum(1 for f in os.listdir('.') if f.endswith('.py'))}")
print(f"  Syntax errors: {len(errors)}")
for e in errors[:10]: print(f"  ✗ {e}")
