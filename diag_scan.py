#!/usr/bin/env python3
"""
diag_scan.py — Run this to find EXACTLY why Scanned: 0
"""
import os, sys, time
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(".env")

print("=" * 55)
print("SCAN DIAGNOSTIC — finding why Scanned: 0")
print("=" * 55)

# Test 1: Check angel.py has the fix
print("\n[1] Checking angel.py connect logic...")
angel_src = Path("angel.py").read_text()
if "if not paper_trade:" in angel_src and "self.connect()" in angel_src:
    # Check if connect is INSIDE the if block
    lines = angel_src.split("\n")
    for i,l in enumerate(lines):
        if "if not paper_trade:" in l:
            next_line = lines[i+1].strip() if i+1 < len(lines) else ""
            if next_line == "self.connect()":
                print("  ❌ OLD CODE: self.connect() is INSIDE 'if not paper_trade'")
                print("     This blocks ALL data fetch in paper mode!")
                print("     FIX: unzip -o ~/Downloads/trading_robot_FRESH.zip")
                break
    else:
        if "ALWAYS connect for DATA" in angel_src:
            print("  ✅ FIXED: self.connect() is unconditional")
        else:
            print("  ⚠️  Unknown state — check line ~158 in angel.py")
else:
    print("  ⚠️  Cannot determine state")

# Test 2: Try to create Angel connection
print("\n[2] Testing Angel One connection...")
try:
    from angel import AngelOne
    ang = AngelOne(
        api_key=os.getenv("API_KEY",""),
        client_id=os.getenv("CLIENT_ID",""),
        password=os.getenv("PASSWORD",""),
        totp_secret=os.getenv("TOTP_SECRET",""),
    )
    print(f"  Angel obj:       {'✅ EXISTS' if ang.obj else '❌ None'}")
    print(f"  paper_trade:     {ang.paper_trade}")
    print(f"  client_id:       {os.getenv('CLIENT_ID','NOT SET')}")
    
    if ang.obj:
        # Test actual data fetch
        print("\n[3] Testing data fetch (NIFTY 5m, 5 days)...")
        try:
            resp = ang.obj.getCandleData({
                "exchange": "NSE",
                "symboltoken": "99926000",  # NIFTY 50 (correct Angel index token)
                "interval": "FIVE_MINUTE",
                "fromdate": (
                    __import__("datetime").datetime.now() - 
                    __import__("datetime").timedelta(days=5)
                ).strftime("%Y-%m-%d %H:%M"),
                "todate": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M"),
            })
            if resp and resp.get("data"):
                bars = len(resp["data"])
                last = resp["data"][-1]
                print(f"  ✅ Got {bars} bars")
                print(f"  Last bar: {last}")
            else:
                print(f"  ❌ No data returned: {resp}")
                if resp and "message" in str(resp).lower():
                    print(f"     Message: {resp.get('message','?')}")
        except Exception as e:
            print(f"  ❌ Data fetch failed: {e}")
    else:
        print("\n  ❌ Angel obj is None — connection failed")
        print("     This means Scanned: 0 will persist")
        print("     Check: API_KEY, CLIENT_ID, PASSWORD, TOTP_SECRET in .env")
except Exception as e:
    print(f"  ❌ Angel init failed: {e}")

# Test 3: Balance
print("\n[4] Testing balance fetch...")
try:
    bal = ang.get_balance(force_real=True)
    print(f"  Balance: ₹{bal:,.0f}")
    if bal == 0:
        print("  ⚠️  Balance is 0 — Angel may be rate-limiting")
        print("     Wait 10 seconds and retry...")
        time.sleep(10)
        bal2 = ang.get_balance(force_real=True)
        print(f"  Retry balance: ₹{bal2:,.0f}")
except Exception as e:
    print(f"  ❌ Balance failed: {e}")

# Test 4: DataFetcher
print("\n[5] Testing DataFetcher with Angel...")
try:
    from data_fetcher import DataFetcher
    df_obj = DataFetcher(angel=ang, paper_trade=False)
    data = df_obj.get_market_data("NIFTY", interval="5m", days=5)
    if data is not None:
        print(f"  ✅ DataFetcher returned {len(data)} bars for NIFTY")
        print(f"  Last close: {data['close'].iloc[-1] if 'close' in data.columns else '?'}")
    else:
        print("  ❌ DataFetcher returned None")
        print("     This is why Scanned: 0")
except Exception as e:
    print(f"  ❌ DataFetcher failed: {e}")

print("\n" + "=" * 55)
print("Run: python3 diag_scan.py")
print("Share the output and I can fix the exact issue")
