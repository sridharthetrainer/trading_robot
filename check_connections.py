#!/usr/bin/env python3
"""
check_connections.py — Run this on YOUR machine to test all connections.
Usage: python3 check_connections.py
"""
import sys, time, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

results = {}

def test(name, fn, category):
    try:
        ok, detail = fn()
        results.setdefault(category, []).append((name, '✅' if ok else '⚠️', detail))
    except Exception as e:
        msg = str(e)
        if 'ConnectionPool' in msg or 'timeout' in msg.lower():
            msg = 'Connection timeout — check internet'
        elif 'No module' in msg:
            mod = msg.split("'")[1] if "'" in msg else msg
            msg = f"pip install {mod}"
        results.setdefault(category, []).append((name, '❌', msg[:60]))

# ── 1. yfinance (price data) ──────────────────────────────────────────────────
def chk_yf_nifty():
    import yf_compat as yf  # yfinance replaced: Yahoo API broken
    df = yf.download('^NSEI', period='2d', interval='1d', progress=False, auto_adjust=True)
    if df is not None and len(df) > 0:
        return True, f"NIFTY={_safe_close(df):,.0f}"
    return False, "No data returned"
test('yfinance NIFTY', chk_yf_nifty, 'Price Data')

def chk_yf_bnk():
    import yf_compat as yf  # yfinance replaced: Yahoo API broken
    df = yf.download('^NSEBANK', period='2d', interval='1d', progress=False, auto_adjust=True)
    if df is not None and len(df) > 0:
        return True, f"BANKNIFTY={_safe_close(df):,.0f}"
    return False, "No data"
test('yfinance BANKNIFTY', chk_yf_bnk, 'Price Data')

def chk_yf_sensex():
    import yf_compat as yf  # yfinance replaced: Yahoo API broken
    df = yf.download('^BSESN', period='2d', interval='1d', progress=False, auto_adjust=True)
    if df is not None and len(df) > 0:
        return True, f"SENSEX={_safe_close(df):,.0f}"
    return False, "No data"
test('yfinance SENSEX', chk_yf_sensex, 'Price Data')

def chk_yf_vix():
    import yf_compat as yf  # yfinance replaced: Yahoo API broken
    df = yf.download('^INDIAVIX', period='2d', interval='1d', progress=False, auto_adjust=True)
    if df is not None and len(df) > 0:
        return True, f"India VIX={_safe_close(df):.2f}"
    return False, "No data"
test('yfinance India VIX', chk_yf_vix, 'Price Data')

def chk_yf_cross():
    import yf_compat as yf  # yfinance replaced: Yahoo API broken
    items = [('USDINR=X','USD/INR'),('CL=F','Brent'),('BTC-USD','Bitcoin'),
             ('^VIX','US VIX'),('^TNX','US 10Y')]
    vals = []
    for ticker, name in items:
        df = yf.download(ticker, period='2d', interval='1d', progress=False, auto_adjust=True)
        if df is not None and len(df) > 0:
            vals.append(f"{name}={_safe_close(df):.2f}")
    return len(vals) > 0, ' | '.join(vals[:3])
test('Cross-asset data', chk_yf_cross, 'Price Data')

# ── 2. NSE APIs ───────────────────────────────────────────────────────────────
def nse_session():
    import requests

def _safe_close(df) -> float:
    """Safe yfinance last close — handles both old Series and new MultiIndex."""
    try:
        if df is None or len(df) == 0: return 0.0
        c = df["Close"]
        if hasattr(c, "columns"): c = c.iloc[:, 0]
        v = c.iloc[-1]
        if hasattr(v, "iloc"): v = v.iloc[0]
        return float(v)
    except Exception: return 0.0
    s = requests.Session()
    s.headers.update({"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                       "Accept":"*/*","Accept-Language":"en-US,en;q=0.9",
                       "Referer":"https://www.nseindia.com/"})
    s.get("https://www.nseindia.com/", timeout=8)
    return s

def chk_nse_nifty_oc():
    s = nse_session()
    r = s.get("https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY", timeout=12)
    if r.status_code == 200:
        d = r.json()
        spot = d.get('records',{}).get('underlyingValue',0)
        strikes = len(d.get('records',{}).get('data',[]))
        return True, f"spot={spot:,.0f} | {strikes} strikes"
    return False, f"HTTP {r.status_code}"
test('NSE NIFTY Option Chain', chk_nse_nifty_oc, 'NSE Data')

def chk_nse_bnk_oc():
    s = nse_session()
    r = s.get("https://www.nseindia.com/api/option-chain-indices?symbol=BANKNIFTY", timeout=12)
    if r.status_code == 200:
        spot = r.json().get('records',{}).get('underlyingValue',0)
        return True, f"spot={spot:,.0f}"
    return False, f"HTTP {r.status_code}"
test('NSE BANKNIFTY Option Chain', chk_nse_bnk_oc, 'NSE Data')

def chk_nse_finn_oc():
    s = nse_session()
    r = s.get("https://www.nseindia.com/api/option-chain-indices?symbol=FINNIFTY", timeout=12)
    if r.status_code == 200:
        spot = r.json().get('records',{}).get('underlyingValue',0)
        return True, f"spot={spot:,.0f}"
    return False, f"HTTP {r.status_code}"
test('NSE FINNIFTY Option Chain', chk_nse_finn_oc, 'NSE Data')

def chk_nse_fii():
    s = nse_session()
    r = s.get("https://www.nseindia.com/api/fiidiiTradeReact", timeout=10)
    if r.status_code == 200:
        d = r.json()
        rows = len(d) if isinstance(d,list) else len(d.get('data',[]))
        return True, f"{rows} rows"
    return False, f"HTTP {r.status_code}"
test('NSE FII/DII Flows', chk_nse_fii, 'NSE Data')

def chk_nse_bulk():
    s = nse_session()
    r = s.get("https://www.nseindia.com/api/historical/bulk-deals", timeout=10)
    if r.status_code == 200:
        d = r.json()
        rows = len(d.get('data', d if isinstance(d,list) else []))
        return True, f"{rows} rows"
    return False, f"HTTP {r.status_code}"
test('NSE Bulk/Block Deals', chk_nse_bulk, 'NSE Data')

def chk_nse_ban():
    s = nse_session()
    r = s.get("https://www.nseindia.com/api/fo-mktlots", timeout=10)
    return r.status_code == 200, f"HTTP {r.status_code}"
test('NSE F&O Lot Sizes', chk_nse_ban, 'NSE Data')

def chk_nse_holidays():
    s = nse_session()
    r = s.get("https://www.nseindia.com/api/holiday-master?type=trading", timeout=10)
    if r.status_code == 200:
        h = len(r.json().get('CM', []))
        return True, f"{h} holidays"
    return False, f"HTTP {r.status_code}"
test('NSE Holiday Calendar', chk_nse_holidays, 'NSE Data')

def chk_nse_participant():
    s = nse_session()
    r = s.get("https://www.nseindia.com/api/participant-wise-open-interest", timeout=10)
    return r.status_code == 200, f"HTTP {r.status_code}"
test('NSE Participant OI', chk_nse_participant, 'NSE Data')

# ── 3. BSE ────────────────────────────────────────────────────────────────────
def chk_bse_sensex():
    import requests
    s = requests.Session()
    s.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://www.bseindia.com/"})
    r = s.get("https://api.bseindia.com/BseIndiaAPI/api/SensitiveIndex/w?strIndexType=S", timeout=10)
    return r.status_code == 200, f"HTTP {r.status_code}"
test('BSE SENSEX API', chk_bse_sensex, 'BSE Data')

def chk_bse_oc():
    import requests
    s = requests.Session()
    s.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://www.bseindia.com/"})
    r = s.get("https://api.bseindia.com/BseIndiaAPI/api/getScripHeaderData/w?Scrip_cd=999901", timeout=10)
    return r.status_code == 200, f"HTTP {r.status_code}"
test('BSE SENSEX Option Chain', chk_bse_oc, 'BSE Data')

# ── 4. Angel One API ──────────────────────────────────────────────────────────
def chk_angel_creds():
    import dotenv
    from pathlib import Path
    env = {}
    for fname in ['.env','env_clean.txt']:
        p = Path(fname)
        if p.exists():
            for line in p.read_text().split('\n'):
                if '=' in line and not line.strip().startswith('#'):
                    k,_,v = line.partition('=')
                    env[k.strip()] = v.strip()
    has = all(k in env and env[k] and env[k] != 'YOUR_'+k
              for k in ['API_KEY','CLIENT_ID','PASSWORD','TOTP_SECRET'])
    return has, 'All credentials set ✅' if has else 'Missing credentials in .env'
test('Angel One .env Credentials', chk_angel_creds, 'Broker')

def chk_angel_api():
    try:
        from SmartApi import SmartConnect
        return True, "SmartApi library installed ✅"
    except ImportError:
        return False, "pip install smartapi-python"
test('Angel One SmartAPI library', chk_angel_api, 'Broker')

# ── 5. Telegram ───────────────────────────────────────────────────────────────
def chk_telegram():
    import requests
    from pathlib import Path
    token = ''
    for fname in ['.env','env_clean.txt']:
        p = Path(fname)
        if p.exists():
            for line in p.read_text().split('\n'):
                if 'TELEGRAM_BOT_TOKEN' in line and '=' in line:
                    token = line.split('=',1)[1].strip()
    if not token:
        return False, "TELEGRAM_BOT_TOKEN not in .env"
    r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=8)
    if r.status_code == 200:
        name = r.json().get('result',{}).get('username','?')
        return True, f"@{name} connected ✅"
    return False, f"HTTP {r.status_code} — check token"
test('Telegram Bot', chk_telegram, 'Infrastructure')

# ── 6. Google Drive ───────────────────────────────────────────────────────────
def chk_gdrive():
    from pathlib import Path
    files = ['gdrive_credentials.json','gdrive_token.json','gdrive_config.json']
    present = [f for f in files if Path(f).exists()]
    return len(present) >= 2, f"{len(present)}/3 files present"
test('Google Drive credentials', chk_gdrive, 'Infrastructure')

# ── 7. Local files ────────────────────────────────────────────────────────────
def chk_nifty200():
    import pandas as pd
    from pathlib import Path
    if not Path('nifty200.csv').exists():
        return False, "nifty200.csv missing!"
    df = pd.read_csv('nifty200.csv')
    return True, f"{len(df)} symbols"
test('nifty200.csv', chk_nifty200, 'Infrastructure')

def chk_master():
    from pathlib import Path
    import pandas as pd
    mc = Path('MasterContract_NFO.csv')
    if not mc.exists():
        return False, "Missing — will auto-download at startup"
    df = pd.read_csv(mc)
    return True, f"{len(df):,} rows"
test('MasterContract_NFO.csv', chk_master, 'Infrastructure')

def chk_trades_db():
    from pathlib import Path
    import sqlite3
    db = Path('trades.db')
    if not db.exists():
        return True, "Will be created on first trade"
    conn = sqlite3.connect(str(db))
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    conn.close()
    return True, f"{len(tables)} tables"
test('trades.db', chk_trades_db, 'Infrastructure')

def chk_signal_log():
    from pathlib import Path
    import sqlite3
    db = Path('signal_log.db')
    if not db.exists():
        return True, "Will be created on first signal"
    conn = sqlite3.connect(str(db))
    count = conn.execute("SELECT COUNT(*) FROM signal_log").fetchone()[0]
    conn.close()
    return True, f"{count:,} signals logged"
test('signal_log.db', chk_signal_log, 'Infrastructure')

# ── 8. NewsAPI ────────────────────────────────────────────────────────────────
def chk_news():
    import requests
    from pathlib import Path
    # Try to get API key from .env
    news_key = ''
    for fname in ['.env','env_clean.txt']:
        p = Path(fname)
        if p.exists():
            for line in p.read_text().split('\n'):
                if 'NEWS_API' in line and '=' in line:
                    news_key = line.split('=',1)[1].strip()
    if not news_key:
        return False, "NEWS_API_KEY not in .env — add it for news signals"
    r = requests.get(f"https://newsapi.org/v2/top-headlines?country=in&pageSize=3&apiKey={news_key}", timeout=8)
    if r.status_code == 200:
        articles = len(r.json().get('articles',[]))
        return True, f"{articles} headlines fetched"
    return False, f"HTTP {r.status_code}"
test('NewsAPI', chk_news, 'External APIs')

# ── PRINT REPORT ──────────────────────────────────────────────────────────────
print()
print("╔══════════════════════════════════════════════════════════════════╗")
print("   CONNECTION & DATA FEED REPORT")
print(f"   {time.strftime('%d-%b-%Y %H:%M')}")
print("╚══════════════════════════════════════════════════════════════════╝")

ok_total = warn_total = fail_total = 0
for cat, items in results.items():
    print(f"\n  ── {cat} ──")
    for name, icon, detail in items:
        print(f"    {icon} {name:<35} {detail}")
        if icon=='✅': ok_total+=1
        elif icon=='⚠️': warn_total+=1
        else: fail_total+=1

print()
print("─"*65)
total = ok_total + warn_total + fail_total
print(f"  ✅ {ok_total} OK    ⚠️ {warn_total} Warning    ❌ {fail_total} Failed    ({total} total)")
print()

if fail_total == 0 and warn_total == 0:
    print("  🎉 ALL SYSTEMS GO — Ready to trade tomorrow!")
elif fail_total == 0:
    print("  ✅ Core systems OK — Warnings are non-critical")
    print("     System will trade normally tomorrow")
else:
    print("  ❌ Fix these before market opens:")
    for cat, items in results.items():
        for name, icon, detail in items:
            if icon == '❌':
                print(f"     • {name}: {detail}")

# Save report
with open('connection_test_report.json', 'w') as f:
    json.dump({
        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
        'summary': {'ok': ok_total, 'warn': warn_total, 'fail': fail_total},
        'results': {cat: [(n,i,d) for n,i,d in items] for cat,items in results.items()}
    }, f, indent=2)
print()
print("  Report saved: connection_test_report.json")
