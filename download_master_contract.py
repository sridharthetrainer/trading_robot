"""
download_master_contract.py — Download NSE/BSE instrument master files

Downloads two files needed by the trading bot:
  1. OpenAPIScripMaster.csv  — Angel One's full instrument list (token → symbol)
  2. MasterContract_ALL.csv — Combined NSE+BSE instrument tokens

Run once before starting the bot, and re-run monthly to refresh.
Usage:  python3 download_master_contract.py
"""
from __future__ import annotations
import time, logging, json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv('.env', override=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)


def download_angel_scrip_master() -> bool:
    """Download OpenAPIScripMaster from Angel One SmartAPI."""
    try:
        import requests
        url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
        logger.info("Downloading Angel One instrument master...")
        r = requests.get(url, timeout=30)
        if r.status_code != 200:
            logger.warning("Angel master HTTP %d", r.status_code)
            return False

        data = r.json()
        logger.info("Downloaded %d instruments", len(data))

        # Save as JSON
        Path("OpenAPIScripMaster.json").write_text(json.dumps(data, indent=2))

        # Also save as CSV for easier reading
        import csv
        if data and isinstance(data, list):
            keys = list(data[0].keys()) if data else []
            with open("OpenAPIScripMaster.csv", "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=keys, extrasaction='ignore')
                w.writeheader()
                w.writerows(data)
            logger.info("✅ OpenAPIScripMaster.csv — %d rows", len(data))

        # Build a quick token lookup dict
        token_map = {}
        for row in data:
            sym   = str(row.get("symbol","") or row.get("tradingsymbol","")).upper()
            token = str(row.get("token","") or row.get("symboltoken",""))
            exch  = str(row.get("exch_seg","") or row.get("exchange","")).upper()
            if sym and token:
                token_map[f"{exch}:{sym}"] = token
                token_map[sym] = token   # fallback without exchange

        Path("token_map.json").write_text(json.dumps(token_map))
        logger.info("✅ token_map.json — %d entries", len(token_map))
        return True

    except Exception as e:
        logger.error("Angel master download failed: %s", e)
        return False


def download_nse_symbol_list() -> bool:
    """Download NSE equity symbol list for universe validation."""
    try:
        import requests
        logger.info("Downloading NSE equity symbol list...")
        r = requests.get(
            "https://archives.nseindia.com/content/equities/EQUITY_L.csv",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.nseindia.com"},
            timeout=20,
        )
        if r.status_code == 200:
            Path("NSE_EQUITY_LIST.csv").write_text(r.text)
            rows = len(r.text.strip().split('\n')) - 1
            logger.info("✅ NSE_EQUITY_LIST.csv — %d symbols", rows)
            return True
        else:
            logger.warning("NSE equity list HTTP %d", r.status_code)
            return False
    except Exception as e:
        logger.error("NSE equity list failed: %s", e)
        return False


def download_nse_fo_symbol_list() -> bool:
    """Download NSE F&O symbol list."""
    try:
        import requests
        logger.info("Downloading NSE F&O symbol list...")
        r = requests.get(
            "https://archives.nseindia.com/content/fo/fo_mktlots.csv",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.nseindia.com"},
            timeout=20,
        )
        if r.status_code == 200:
            Path("NSE_FO_LIST.csv").write_text(r.text)
            rows = len(r.text.strip().split('\n')) - 1
            logger.info("✅ NSE_FO_LIST.csv — %d F&O symbols", rows)
            return True
        else:
            logger.warning("NSE F&O list HTTP %d", r.status_code)
    except Exception as e:
        logger.error("NSE F&O list failed: %s", e)
    return False


def create_fallback_contract() -> None:
    """
    Create minimal MasterContract_ALL.csv from known NSE indices
    if all download attempts fail.
    """
    known = [
        # symbol, token, exchange, instrument_type, lot_size
        ("NIFTY",     "26000", "NSE", "INDEX",  65),
        ("BANKNIFTY",  "26009", "NSE", "INDEX",  30),
        ("FINNIFTY",  "26037", "NSE", "INDEX",  65),
        ("MIDCPNIFTY","26074", "NSE", "INDEX", 120),
        ("SENSEX",    "1",     "BSE", "INDEX",  20),
        ("BANKEX",    "2",     "BSE", "INDEX",  15),
        ("NIFTYIT",   "26006", "NSE", "INDEX",   1),
        ("NIFTYMETAL","26015", "NSE", "INDEX",   1),
        ("NIFTYPHARMA","26016","NSE", "INDEX",   1),
        ("NIFTYAUTO", "26002", "NSE", "INDEX",   1),
    ]
    header = "symbol,token,exchange,instrumenttype,lot_size\n"
    rows   = "\n".join(f"{s},{t},{e},{i},{l}" for s,t,e,i,l in known)
    Path("MasterContract_ALL.csv").write_text(header + rows)
    logger.info("✅ MasterContract_ALL.csv — fallback (10 known instruments)")


def verify_files() -> None:
    """Show summary of downloaded files."""
    print("\n" + "="*50)
    print("DOWNLOADED FILES SUMMARY")
    print("="*50)
    files_to_check = [
        "OpenAPIScripMaster.json",
        "OpenAPIScripMaster.csv",
        "MasterContract_ALL.csv",
        "NSE_EQUITY_LIST.csv",
        "NSE_FO_LIST.csv",
        "token_map.json",
    ]
    for fname in files_to_check:
        p = Path(fname)
        if p.exists():
            size = p.stat().st_size // 1024
            print(f"  ✅ {fname:35} ({size} KB)")
        else:
            print(f"  ⚠️  {fname:35} (not found)")

    # Verify nifty200.csv
    if Path("nifty200.csv").exists():
        rows = len(Path("nifty200.csv").read_text().strip().split('\n'))
        print(f"  ✅ {'nifty200.csv':35} ({rows} symbols)")
    print()


if __name__ == "__main__":
    print("="*50)
    print("NSE/BSE INSTRUMENT MASTER DOWNLOAD")
    print("="*50)

    results = []

    # 1. Angel One scrip master (most important)
    ok = download_angel_scrip_master()
    results.append(("Angel One ScripMaster", ok))
    if not ok:
        logger.warning("Angel master failed — creating fallback contract file")
        create_fallback_contract()

    time.sleep(1)

    # 2. NSE equity list
    ok = download_nse_symbol_list()
    results.append(("NSE Equity List", ok))

    time.sleep(1)

    # 3. NSE F&O list
    ok = download_nse_fo_symbol_list()
    results.append(("NSE F&O List", ok))

    # 4. Copy OpenAPIScripMaster to MasterContract_ALL if it worked
    if Path("OpenAPIScripMaster.csv").exists() and not Path("MasterContract_ALL.csv").exists():
        import shutil
        shutil.copy("OpenAPIScripMaster.csv", "MasterContract_ALL.csv")
        logger.info("✅ MasterContract_ALL.csv created from Angel master")

    # Summary
    print("\nRESULT:")
    for name, ok in results:
        print(f"  {'✅' if ok else '⚠️ '} {name}")

    verify_files()
    print("Done. You can now run: python3 seed_cache.py")
