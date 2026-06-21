#!/usr/bin/env python3
"""
diag_scan.py — Run this to find EXACTLY why Scanned: 0
"""
import os, time
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

# Test 4: DataFetcher (same way LiveSignalEngine creates it)
print("\n[5] Testing DataFetcher with Angel...")
try:
    from data_fetcher import DataFetcher
    df_obj = DataFetcher(angel=ang, paper_trade=False)
    print(f"  DataFetcher.angel = {df_obj.angel}")
    print(f"  angel has get_historical: {hasattr(df_obj.angel, 'get_historical_data') if df_obj.angel else 'angel is None'}")
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


# Test 5: LiveSignalEngine DataFetcher
print("\n[6] Testing LiveSignalEngine DataFetcher (the REAL scan path)...")
try:
    from live_signal_engine import LiveSignalEngine
    lse = LiveSignalEngine()
    df_angel = lse.data_fetcher.angel
    method = getattr(lse, "_angel_source_method", "unknown")
    print(f"  LSE DataFetcher.angel = {type(df_angel).__name__ if df_angel else 'None'}")
    print(f"  LSE angel.obj         = {'EXISTS' if df_angel and df_angel.obj else 'None'}")
    print(f"  LSE angel.paper_trade = {df_angel.paper_trade if df_angel else 'N/A'}")
    print(f"  LSE angel source      = {method}")
    if df_angel and df_angel.obj:
        data = lse.data_fetcher.get_market_data("NIFTY", interval="5m", days=5)
        bars = len(data) if data is not None else 0
        if bars >= 50:
            print(f"  ✅ LSE NIFTY: {bars} bars — SCAN WILL WORK")
        elif bars > 0:
            print(f"  ⚠️  LSE NIFTY: {bars} bars — low count, check market hours")
        else:
            print(f"  ❌ LSE NIFTY: 0 bars — angel set but fetch failed")
    else:
        print("  ❌ LSE DataFetcher has NO Angel — THIS is why Scanned: 0")
        print("     Causes: (a) deploy didn't replace live_signal_engine.py")
        print("             (b) all 3 Angel fallback methods failed")
        print("             (c) Angel login failure (check .env credentials)")
except Exception as e:
    import traceback
    print(f"  LSE test: {e}")
    print(f"  Traceback: {traceback.format_exc()[-400:]}")
