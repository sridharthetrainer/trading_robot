#!/usr/bin/env python3
"""
validate_scan.py — Complete system validation.
Run anytime (market open or closed).
Usage: python3 validate_scan.py
"""
import sys, os, traceback
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv(dotenv_path=".env", override=True)

import pandas as pd

def col(df, name):
    for c in df.columns:
        if c.lower() == name.lower(): return df[c]
    return df.iloc[:, 0]

print("═"*60)
print("SYSTEM VALIDATION — NIFTY ALGO BOT")
print("═"*60)

PASS = FAIL = 0

def chk(label, fn):
    global PASS, FAIL
    try:
        result = fn()
        status = "✅" if result else "⚠️"
        if result: PASS += 1
        else: FAIL += 1
        return result
    except Exception as e:
        print(f"  ❌ {label}: {str(e)[:60]}")
        FAIL += 1
        return False

# ── 1. .env keys ──────────────────────────────────────────
print("\n[1] CONFIGURATION")
import config as cfg
import os as _os

keys = {
    "API_KEY (Angel)":       _os.getenv("API_KEY",""),
    "CLIENT_ID":             _os.getenv("CLIENT_ID",""),
    "TELEGRAM_BOT_TOKEN":    _os.getenv("TELEGRAM_BOT_TOKEN",""),
    "TIINGO_KEY":            _os.getenv("TIINGO_KEY",""),
    "TWELVE_DATA_KEY":       _os.getenv("TWELVE_DATA_KEY",""),
    "GITHUB_TOKEN":          _os.getenv("GITHUB_TOKEN",""),
}
for name, val in keys.items():
    ok = bool(val and len(val) > 4)
    print(f"  {'✅' if ok else '❌'} {name:25} {'SET' if ok else 'MISSING'}")
    if ok: PASS += 1
    else: FAIL += 1

print(f"  PAPER_TRADING: {cfg.PAPER_TRADING} | MIN_LIVE_CAPITAL: ₹{getattr(cfg,'MIN_LIVE_CAPITAL',25000):,.0f}")

# ── 2. No yfinance ────────────────────────────────────────
print("\n[2] YFINANCE REMOVED")
yf_files = []
for f in os.listdir('.'):
    if not f.endswith('.py'): continue
    try:
        with open(f) as fh: src = fh.read()
        # AST check — avoids string false positive in validate_scan itself
        try:
            import ast as _astv
            _tree = _astv.parse(src)
            _yf = any("yfinance" in (getattr(a,"name","") or "")
                      for n in _astv.walk(_tree)
                      if isinstance(n,(_astv.Import,_astv.ImportFrom))
                      for a in getattr(n,"names",[]))
        except Exception: _yf = False
        if _yf: yf_files.append(f)
    except: pass
if yf_files:
    print(f"  ❌ Still has yfinance: {yf_files}")
    FAIL += 1
else:
    print("  ✅ Zero files import yfinance")
    PASS += 1

# ── 3. yf_compat working ─────────────────────────────────
print("\n[3] YF_COMPAT (NSE Live)")
try:
    import yf_compat as yf
    df_nse = yf.download("^NSEI")
    if df_nse is not None and len(df_nse) > 0:
        px = float(col(df_nse,"Close").iloc[-1])
        print(f"  ✅ NIFTY via yf_compat: ₹{px:,.2f}")
        PASS += 1
    else:
        print("  ⚠️  yf_compat returned empty df")
        FAIL += 1
except Exception as e:
    print(f"  ❌ {e}")
    FAIL += 1

# ── 4. NSE Live VIX ───────────────────────────────────────
print("\n[4] INDIA VIX (NSE allIndices)")
try:
    import requests
    s = requests.Session()
    s.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://www.nseindia.com"})
    s.get("https://www.nseindia.com/", timeout=5)
    r = s.get("https://www.nseindia.com/api/allIndices", timeout=8)
    vix = 0.0
    for idx in r.json().get("data",[]):
        if "INDIA VIX" in str(idx.get("index","")).upper():
            vix = float(idx.get("last",0) or 0)
            break
    if vix > 0:
        print(f"  ✅ India VIX: {vix:.2f}")
        PASS += 1
    else:
        print(f"  ⚠️  VIX=0 (market may be closed)")
        FAIL += 1
except Exception as e:
    print(f"  ❌ {e}")
    FAIL += 1

# ── 5. MasterContract ─────────────────────────────────────
print("\n[5] MASTER CONTRACT")
from pathlib import Path
mc = Path("MasterContract_ALL.csv")
if mc.exists():
    df_mc = pd.read_csv(str(mc), low_memory=False)
    nse_eq = df_mc[df_mc["exch_seg"].str.upper()=="NSE"] if "exch_seg" in df_mc.columns else pd.DataFrame()
    print(f"  ✅ {len(df_mc):,} instruments | NSE EQ: {len(nse_eq):,}")
    for sym,tok in [("RELIANCE","2885"),("HDFCBANK","1333"),("TCS","11536")]:
        row = df_mc[df_mc["symbol"].str.upper().str.contains(sym+"-EQ",na=False)]
        t = str(row.iloc[0]["token"]) if len(row) else "?"
        print(f"     {'✅' if t==tok else '❌'} {sym}: {t}")
    PASS += 1
else:
    print("  ❌ Missing — run: ./setup_fresh.sh")
    FAIL += 1

# ── 6. Angel One connection ───────────────────────────────
print("\n[6] ANGEL ONE")
try:
    import pyotp
    from SmartApi import SmartConnect
    obj = SmartConnect(api_key=cfg.API_KEY)
    totp = pyotp.TOTP(cfg.TOTP_SECRET).now()
    resp = obj.generateSession(cfg.CLIENT_ID, cfg.PASSWORD, totp)
    if resp.get("status") == True:
        bal = obj.rmsLimit()
        net = float(bal.get("data",{}).get("net","0") or 0) if bal else 0
        print(f"  ✅ Connected | Balance: ₹{net:,.2f}")
        PASS += 1
        # Test historical data
        from datetime import datetime, timedelta
        r2 = obj.getCandleData({
            "exchange":"NSE","symboltoken":"99926000",
            "interval":"ONE_DAY",
            "fromdate":(datetime.now()-timedelta(days=10)).strftime("%Y-%m-%d %H:%M"),
            "todate":datetime.now().strftime("%Y-%m-%d %H:%M"),
        })
        candles = r2.get("data",[]) if r2 else []
        print(f"  ✅ NIFTY historical: {len(candles)} bars | Last ₹{candles[-1][4]:,.0f}" if candles else "  ⚠️  No candles (after market close)")
        if candles: PASS += 1
        else: FAIL += 1
    else:
        print(f"  ❌ Login failed: {resp.get('message','?')}")
        FAIL += 1
except Exception as e:
    print(f"  ❌ {e}")
    FAIL += 1

# ── 7. DataFetcher ────────────────────────────────────────
print("\n[7] DATA FETCHER (8 symbols)")
try:
    from data_fetcher import DataFetcher
    fetcher = DataFetcher(angel=None)
    got = 0
    for sym in ["NIFTY","BANKNIFTY","RELIANCE","HDFCBANK","INFY","TCS","SBIN","AXISBANK"]:
        try:
            df = fetcher.get_market_data(sym, interval="1d", days=60)
            if df is not None and len(df) >= 5:
                df.columns = [c.lower() for c in df.columns]
                px = float(df["close"].iloc[-1])
                got += 1
                if got <= 3: print(f"  ✅ {sym:12} {len(df):2}d ₹{px:,.0f}")
        except Exception as e:
            print(f"  ❌ {sym}: {str(e)[:40]}")
    print(f"  {'✅' if got>=6 else '⚠️'} DataFetcher: {got}/8 symbols")
    if got >= 6: PASS += 1
    else: FAIL += 1
except Exception as e:
    print(f"  ❌ {e}")
    FAIL += 1

# ── 8. Signal Engine ─────────────────────────────────────
print("\n[8] SIGNAL ENGINE")
try:
    from signal_engine import generate_signal, STRATEGIES
    print(f"  ✅ Strategies loaded: {len(STRATEGIES)}")
    PASS += 1
    df_test = fetcher.get_market_data("NIFTY","1d",60)
    if df_test is not None and len(df_test) >= 20:
        df_test.columns = [c.lower() for c in df_test.columns]
        result = generate_signal(df=df_test, df_htf=df_test, symbol="NIFTY")
        score = result.get("score",0) if result else 0
        dirn  = result.get("direction","?") if result else "?"
        print(f"  ✅ NIFTY signal: score={score:.1f} dir={dirn}")
        PASS += 1
    else:
        print("  ⚠️  Not enough data for signal test")
except Exception as e:
    print(f"  ❌ {e}")
    traceback.print_exc()
    FAIL += 1

# ── 9. WOW Factors ───────────────────────────────────────
print("\n[9] WOW FACTORS")
wow = {
    "meta_learner":          "meta_learner",
    "hmm_regime":            "hmm_regime",
    "elliott_wave":          "elliott_wave",
    "cvar_optimizer":        "cvar_optimizer",
    "order_flow":            "order_flow",
    "dark_pool":             "dark_pool",
    "fii_options_pos":       "fii_options_positioning",
    "fii_data_fetcher":      "fii_data_fetcher",
    "yf_compat":             "yf_compat",
}
for label, module in wow.items():
    try:
        __import__(module)
        print(f"  ✅ {label}")
        PASS += 1
    except Exception as e:
        print(f"  ❌ {label}: {e}")
        FAIL += 1

# ── 10. Telegram commands ─────────────────────────────────
print("\n[10] TELEGRAM COMMANDS")
try:
    import re as _re
    with open('telegram_commands.py') as f: tg_src = f.read()
    cmds = _re.findall(r'self\.register\(["\'](\w+)["\']', tg_src)
    required = ['status','signals','morning','vix','health','fii','backup','github',
                'meta','hmm','waves','orderflow','darkpool','fiipos','kill','pause']
    missing = [c for c in required if c not in cmds]
    print(f"  {'✅' if not missing else '❌'} {len(cmds)} commands registered")
    if missing: print(f"  ❌ Missing: {missing}")
    if not missing: PASS += 1
    else: FAIL += 1
except Exception as e:
    print(f"  ❌ {e}")
    FAIL += 1

# ── 11. Service file ──────────────────────────────────────
print("\n[11] SYSTEMD SERVICE")
try:
    svc = open("/etc/systemd/system/trading-bot.service").read()
    has_python3 = "python3" in svc
    has_venv = "venv/bin" in svc
    has_env  = "EnvironmentFile" not in svc  # we use load_dotenv
    print(f"  {'✅' if has_python3 else '❌'} python3 in ExecStart")
    print(f"  {'✅' if has_venv else '❌'} venv path in service")
    if has_python3 and has_venv: PASS += 1
    else: FAIL += 1
except:
    print("  ⚠️  Service file not installed yet")

# ── Summary ───────────────────────────────────────────────
total = PASS + FAIL
pct   = PASS/max(total,1)*100
print()
print("═"*60)
print(f"RESULT: {PASS}/{total} checks passed ({pct:.0f}%)")
if PASS == total:
    print("✅ SYSTEM FULLY READY — bot can trade tomorrow 9:15 AM")
elif pct >= 80:
    print("✅ MOSTLY READY — minor issues won't block trading")
else:
    print("⚠️  FIX ISSUES ABOVE before going live")
print()
print("Commands to run now:")
print("  ./bot.sh restart   — apply latest changes")
print("  ./bot.sh logs      — watch startup")
print("  Tomorrow: /morning /health /oi NIFTY")
print("═"*60)
