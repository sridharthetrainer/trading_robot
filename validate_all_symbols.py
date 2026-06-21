#!/usr/bin/env python3
"""
validate_all_symbols.py — Test ALL symbols vs Angel One.
Run: python3 validate_all_symbols.py
"""
import sys, csv, time, traceback
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv(dotenv_path=".env", override=True)

import pandas as pd, pyotp, config as cfg
from SmartApi import SmartConnect
from datetime import datetime, timedelta
from pathlib import Path

print("═"*60)
print("FULL SYMBOL ANGEL ONE VALIDATION")
print("═"*60)

# Load symbols
symbols = []
with open("nifty200.csv") as f:
    for row in csv.DictReader(f):
        sym = row.get("Symbol","").strip()
        sec = row.get("Sector","?")
        if sym: symbols.append((sym, sec))

print(f"Symbols: {len(symbols)}")

# Static index tokens
STATIC = {
    "NIFTY":99926000,"BANKNIFTY":99926009,"FINNIFTY":99926037,
    "MIDCPNIFTY":99926051,"SENSEX":99919000,"NIFTYNEXT50":99926012,
}

# Aliases for renamed/merged symbols
ALIASES = {
    "MCDOWELLS":["MCDOWELLS-EQ","MCDOWELL-N-EQ"],
    "LTIM":     ["LTIM-EQ","LTIMINDTEE-EQ"],
    "HDFCAMC":  ["HDFCAMC-EQ"],
    "AMARA":    ["AMARAJABAT-EQ","AMARA-EQ"],
    "MINDACORP":["MINDAIND-EQ","MINDACORP-EQ"],
    "HPCL":     ["HINDPETRO-EQ","HPCL-EQ"],
    "NALCO":    ["NATIONALUM-EQ","NALCO-EQ"],
    "ZOMATO":   ["ZOMATO-EQ"],
    "UJJIVAN":  ["UJJIVANSFB-EQ","UJJIVAN-EQ"],
    "INDIAGRID":["INDIGRID-EQ","INDIAGRID-EQ"],
    "KNR":      ["KNRCON-EQ","KNR-EQ"],
    "RINFRA":   ["RINFRA-EQ"],
    "LEMONTRE": ["LEMONTREE-EQ","LEMONTRE-EQ"],
    "MAHINDCIE":["MAHINDCIE-EQ"],
}

SKIP = {"BANKEX"}

# Load token map
token_map = {}
if Path("MasterContract_ALL.csv").exists():
    df_mc = pd.read_csv("MasterContract_ALL.csv", low_memory=False,
                        usecols=lambda c: c in ["exch_seg","symbol","token"])
    nse_eq = df_mc[df_mc["exch_seg"].str.upper()=="NSE"] if "exch_seg" in df_mc.columns else pd.DataFrame()
    for _, row in nse_eq.iterrows():
        s = str(row.get("symbol","")).strip().upper()
        t = str(row.get("token","")).strip()
        if s and t and t not in ("nan",""):
            token_map[s.replace("-EQ","")] = t
            token_map[s] = t
    print(f"Tokens: {len(token_map)}")
else:
    print("❌ MasterContract_ALL.csv missing — run ./setup_fresh.sh first")
    sys.exit(1)

# Connect
print("Connecting...")
obj = SmartConnect(api_key=cfg.API_KEY)
totp = pyotp.TOTP(cfg.TOTP_SECRET).now()
resp = obj.generateSession(cfg.CLIENT_ID, cfg.PASSWORD, totp)
if resp.get("status") != True:
    print(f"❌ Login failed: {resp.get('message','?')}")
    sys.exit(1)
print("✅ Connected\n")
print(f"Testing {len(symbols)} symbols (~{len(symbols)*1.5/60:.0f} min)...")
print("─"*60)

from_dt = (datetime.now()-timedelta(days=30)).strftime("%Y-%m-%d %H:%M")
to_dt   = datetime.now().strftime("%Y-%m-%d %H:%M")

passed=[]
failed_token=[]
failed_data=[]
rate_limited=[]

for i,(sym,sec) in enumerate(symbols):
    if sym in SKIP:
        print(f"  ⏭️  SKIP {sym:15} (BSE index)")
        continue

    # Token lookup
    token = str(STATIC.get(sym,"")) or token_map.get(sym,"") or token_map.get(sym+"-EQ","")
    if not token:
        for alias in ALIASES.get(sym,[]):
            t2 = token_map.get(alias.replace("-EQ",""),"") or token_map.get(alias,"")
            if t2: token=t2; break
    if not token:
        failed_token.append((sym,sec))
        if i < 5 or sym in ALIASES:
            print(f"  ❌ NO TOKEN {sym:15} ({sec})")
        continue

    # Rate limit
    time.sleep(1.0)

    try:
        r = obj.getCandleData({
            "exchange":"NSE","symboltoken":str(token),
            "interval":"ONE_DAY","fromdate":from_dt,"todate":to_dt,
        })
        candles = r.get("data",[]) if r else []
        if candles:
            px = float(candles[-1][4])
            passed.append((sym,sec,len(candles),px))
            if (i+1) % 25 == 0:
                print(f"  ✅ {i+1:3}/{len(symbols)} {sym:15} {len(candles)}d ₹{px:>10,.0f}")
        else:
            msg = (r.get("message","") if r else "no response")[:50]
            if "rate" in msg.lower():
                rate_limited.append((sym,sec))
                time.sleep(3)
            else:
                failed_data.append((sym,sec,msg))
            print(f"  ❌ {sym:15} → {msg}")
    except KeyboardInterrupt:
        print("\nStopped by user")
        break
    except Exception as e:
        msg = str(e)[:60]
        failed_data.append((sym,sec,msg))
        print(f"  ❌ {sym:15} → {msg}")
        if i < 2: traceback.print_exc()

# Summary
print("\n" + "═"*60)
print("RESULTS")
print("═"*60)
total = len(passed)+len(failed_token)+len(failed_data)+len(rate_limited)
pct = len(passed)/max(total,1)*100
print(f"✅ Passed:       {len(passed)}/{total} ({pct:.0f}%)")
print(f"❌ No token:     {len(failed_token)}")
print(f"❌ No data:      {len(failed_data)}")
print(f"⚠️  Rate limited: {len(rate_limited)}")

if failed_token:
    print(f"\nNo-token symbols:")
    for s,sec in failed_token: print(f"  {s:15} {sec}")
if failed_data:
    print(f"\nData errors (first 5):")
    for s,sec,msg in failed_data[:5]: print(f"  {s:15} {msg}")

if len(passed) >= 185:
    print(f"\n✅ EXCELLENT — {len(passed)}/{total} ready")
elif len(passed) >= 160:
    print(f"\n✅ GOOD — {len(passed)}/{total} symbols working")
else:
    print(f"\n⚠️  PARTIAL — {len(passed)}/{total}. Angel One may be rate limiting.")
    print("   Wait 5 min and retry")

if passed:
    df_out = pd.DataFrame(passed, columns=["symbol","sector","bars","last_price"])
    df_out.to_csv("symbol_validation.csv", index=False)
    print(f"\nSaved: symbol_validation.csv")
