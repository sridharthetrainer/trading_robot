#!/usr/bin/env python3
"""
Direct diagnostic — run from terminal to debug Scanned: 0
Usage:  python3 diag.py
or:     ./venv/bin/python3 diag.py
"""
import sys
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, '.')

print("=" * 70)
print("DIRECT DIAGNOSTIC — Scanned: 0 Debugging")
print("=" * 70)

# Step 1: Angel connection
print("\n[1] Testing Angel connection...")
try:
    from angel import AngelOne
    ang = AngelOne(
        api_key=os.getenv("API_KEY", ""),
        client_id=os.getenv("CLIENT_ID", ""),
        password=os.getenv("PASSWORD", ""),
        totp_secret=os.getenv("TOTP_SECRET", ""),
        paper_trade=False,
    )
    obj_status = "CONNECTED" if ang.obj else "FAILED"
    print(f"  Angel.obj:        {obj_status}")
    print(f"  paper_trade:      {ang.paper_trade}")
    print(f"  Client ID:        {os.getenv('CLIENT_ID', 'NOT SET')}")
    
    if not ang.obj:
        print("  ✗ Angel object is None — login failed")
        print("  Check: API_KEY, CLIENT_ID, PASSWORD, TOTP_SECRET in .env")
        sys.exit(1)
except Exception as e:
    print(f"  ✗ Angel init failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Step 2: DataFetcher test
print("\n[2] Testing DataFetcher...")
try:
    from data_fetcher import DataFetcher
    df = DataFetcher(symbols_csv="nifty.csv", paper_trade=False)
    df.angel = ang
    print(f"  DataFetcher created, angel assigned")
    
    nifty = df.get_market_data("NIFTY", interval="5m", days=5)
    bars = len(nifty) if nifty is not None else 0
    print(f"  NIFTY bars:       {bars}")
    
    if bars < 5:
        print(f"  ✗ Too few bars (need 5+)")
        sys.exit(1)
    elif bars < 50:
        print(f"  ⚠ Low but OK (>= 5)")
    else:
        print(f"  ✓ Good (>= 50)")
except Exception as e:
    print(f"  ✗ DataFetcher failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Step 3: LiveSignalEngine
print("\n[3] Testing LiveSignalEngine...")
try:
    from live_signal_engine import LiveSignalEngine
    lse = LiveSignalEngine()
    
    angel_exists = lse.data_fetcher.angel is not None
    method = getattr(lse, "_angel_source_method", "unknown")
    
    print(f"  LSE DataFetcher.angel: {type(lse.data_fetcher.angel).__name__ if angel_exists else 'None'}")
    print(f"  Angel source method:   {method}")
    
    if not angel_exists:
        print(f"  ✗ Angel not set in LSE — THIS is why Scanned: 0")
        sys.exit(1)
    
    lse_data = lse.data_fetcher.get_market_data("NIFTY", interval="5m", days=5)
    lse_bars = len(lse_data) if lse_data is not None else 0
    print(f"  NIFTY via LSE:        {lse_bars} bars")
    
    if lse_bars >= 50:
        print(f"  ✓ LSE scan will work")
    elif lse_bars >= 5:
        print(f"  ⚠ Low bars but >= 5 min")
    else:
        print(f"  ✗ LSE fetch returned 0 bars")
        
except Exception as e:
    print(f"  ✗ LSE test failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Step 4: Full scan simulation
print("\n[4] Simulating scan loop...")
try:
    symbols = ["NIFTY", "BANKNIFTY", "FINNIFTY"]
    for sym in symbols:
        data = lse.data_fetcher.get_market_data(sym, interval="5m", days=1)
        bars = len(data) if data is not None else 0
        status = "✓" if bars >= 5 else "✗"
        print(f"  {status} {sym:15} {bars:3} bars")
except Exception as e:
    print(f"  ✗ Scan sim failed: {e}")

print("\n" + "=" * 70)
print("✅ DIAGNOSTIC COMPLETE")
print("=" * 70)
print("\nResult: If all steps show ✓, bot should scan symbols normally.")
print("If any step shows ✗, check the error messages above.")
