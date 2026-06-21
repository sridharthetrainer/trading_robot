#!/usr/bin/env python3
"""
seed_cache.py — Download last 60 days of NSE data for backtest.
Run ONCE to seed the local cache, then backtest works offline.
Usage: python3 seed_cache.py
"""
import sys
sys.path.insert(0, '.')

print("═"*55)
print("SEEDING NSE BHAVCOPY CACHE (last 60 trading days)")
print("This takes ~5 minutes. Run only once.")
print("═"*55)

from bhavcopy_cache import cache_status
import time

# Also add today's bhavcopy for completeness
from bhavcopy_cache import download_bhavcopy

print("\nDownloading last 60 trading days...")
total = 0

from datetime import date as _d, timedelta as _td
d = _d.today()
days_ok = 0
days_tried = 0

while days_ok < 60 and days_tried < 100:
    if d.weekday() < 5:  # Monday-Friday
        print(f"  {d} ...", end="", flush=True)
        n = download_bhavcopy(d)
        if n > 0:
            print(f" ✅ {n:,} stocks")
            days_ok += 1
            total += n
        else:
            print(f" ⚠️  no data (holiday or future date)")
        days_tried += 1
        time.sleep(0.5)  # be polite to NSE servers
    d -= _td(days=1)

print(f"\n{'═'*55}")
st = cache_status()
print(f"CACHE COMPLETE:")
print(f"  Records:     {st.get('records',0):,}")
print(f"  Symbols:     {st.get('symbols',0):,}")
print(f"  Latest date: {st.get('latest_date','?')}")
print(f"  File:        nse_cache.db")
print()
if st.get('records',0) > 10000:
    print("✅ Cache seeded successfully")
    print("   Backtest will now use local data (no internet needed)")
    print("   Data updates daily at 6 PM automatically")
else:
    print("⚠️  Limited data downloaded")
    print("   NSE may block bulk requests — retry tomorrow")
print("═"*55)

# ── Optional: Download Dhan instrument master (if configured) ─────────────
try:
    from dhan_client import download_dhan_master, is_configured
    if is_configured():
        print("\nDownloading Dhan instrument master...")
        ok = download_dhan_master()
        print("  ✅ Dhan master downloaded" if ok else "  ⚠️  Dhan master failed (optional)")
    else:
        print("\n  ℹ️  Dhan not configured (optional) — see /dhan for setup")
except Exception:
    pass
