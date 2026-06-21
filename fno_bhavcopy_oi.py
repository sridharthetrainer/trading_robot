"""
fno_bhavcopy_oi.py — Load OI baseline from NSE F&O bhavcopy at startup

NSE publishes complete F&O bhavcopy every day at 6 PM IST.
Contains OI for every strike, every expiry, every contract.
Used to seed the OI tracker at 8:30 AM so:
  - Yesterday's OI is the baseline (not stale session data)
  - OI change = live OI - bhavcopy OI (accurate from 9:15 AM)

This replaces the "18-hour stale OI" problem at startup.
"""
from __future__ import annotations
import logging
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, Optional
import pandas as pd

logger = logging.getLogger(__name__)

_BHAVCOPY_DIR = Path("fno_bhavcopy_cache")
_BHAVCOPY_DIR.mkdir(exist_ok=True)


def download_fno_bhavcopy(target_date: date = None) -> Optional[pd.DataFrame]:
    """
    Download NSE F&O bhavcopy for a given date.
    Published at 6 PM, available until next day's bhavcopy.
    """
    if target_date is None:
        target_date = date.today()
        # Use yesterday if today's not published yet (before 6 PM)
        if datetime.now().hour < 18:
            target_date = target_date - timedelta(days=1)

    # Skip weekends
    while target_date.weekday() >= 5:
        target_date -= timedelta(days=1)

    cache_file = _BHAVCOPY_DIR / f"fo_bhavcopy_{target_date}.csv"
    if cache_file.exists():
        try:
            return pd.read_csv(str(cache_file))
        except Exception:
            pass

    try:
        import requests
        dd  = target_date.strftime("%d")
        mmm = target_date.strftime("%b").upper()
        yyyy = target_date.strftime("%Y")

        # NSE F&O bhavcopy URL
        url = (f"https://archives.nseindia.com/content/historical/DERIVATIVES/"
               f"{yyyy}/{mmm}/fo{dd}{mmm}{yyyy}bhav.csv.zip")

        r = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0",
                     "Referer": "https://www.nseindia.com"},
            timeout=30,
        )
        if r.status_code != 200:
            logger.debug("F&O bhavcopy HTTP %d for %s", r.status_code, target_date)
            return None

        # Unzip
        import zipfile, io
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            fname = [n for n in zf.namelist() if n.endswith(".csv")][0]
            df = pd.read_csv(zf.open(fname))

        df.to_csv(str(cache_file), index=False)
        logger.info("F&O bhavcopy ✅ %s: %d rows", target_date, len(df))
        return df

    except Exception as e:
        logger.debug("F&O bhavcopy download %s: %s", target_date, e)
        return None


def get_oi_baseline(symbol: str, target_date: date = None) -> Dict[float, dict]:
    """
    Get yesterday's OI baseline for all strikes of a symbol.
    Returns: {strike: {ce_oi, pe_oi, ce_chg, pe_chg}}

    This is the OI at market close yesterday.
    Use this as baseline: OI change today = live_OI - baseline_OI
    """
    df = download_fno_bhavcopy(target_date)
    if df is None or df.empty:
        return {}

    try:
        sym = symbol.upper()
        # F&O bhavcopy columns: SYMBOL, EXPIRY_DT, OPTION_TYP, STRIKE_PR,
        #                       OPEN_INT, CHG_IN_OI, SETTLE_PR, ...
        df_sym = df[df["SYMBOL"].str.upper() == sym].copy()
        if df_sym.empty:
            return {}

        # Get nearest expiry
        expiries = pd.to_datetime(df_sym["EXPIRY_DT"], dayfirst=True, errors="coerce")
        df_sym["expiry_dt"] = expiries
        today = pd.Timestamp.now()
        df_sym = df_sym[df_sym["expiry_dt"] >= today]
        if df_sym.empty:
            return {}

        nearest_expiry = df_sym["expiry_dt"].min()
        df_expiry = df_sym[df_sym["expiry_dt"] == nearest_expiry]

        baseline = {}
        for _, row in df_expiry.iterrows():
            strike    = float(row.get("STRIKE_PR", 0) or 0)
            opt_type  = str(row.get("OPTION_TYP", "")).upper()
            oi        = int(row.get("OPEN_INT", 0) or 0)
            chg_oi    = int(row.get("CHG_IN_OI", 0) or 0)

            if strike not in baseline:
                baseline[strike] = {"ce_oi":0,"pe_oi":0,"ce_chg":0,"pe_chg":0}

            if "CE" in opt_type:
                baseline[strike]["ce_oi"]  = oi
                baseline[strike]["ce_chg"] = chg_oi
            elif "PE" in opt_type:
                baseline[strike]["pe_oi"]  = oi
                baseline[strike]["pe_chg"] = chg_oi

        logger.info("OI baseline %s: %d strikes from bhavcopy", symbol, len(baseline))
        return baseline

    except Exception as e:
        logger.debug("OI baseline %s: %s", symbol, e)
        return {}


def get_pcr_from_bhavcopy(symbol: str = "NIFTY") -> float:
    """PCR from yesterday's bhavcopy — accurate baseline."""
    try:
        baseline = get_oi_baseline(symbol)
        if not baseline:
            return 0.0
        total_ce = sum(v["ce_oi"] for v in baseline.values())
        total_pe = sum(v["pe_oi"] for v in baseline.values())
        return round(total_pe / total_ce, 3) if total_ce > 0 else 0.0
    except Exception:
        return 0.0


def seed_oi_tracker_at_startup(symbols=None) -> int:
    """
    Called at 8:30 AM startup to seed OI tracker with yesterday's data.
    Returns number of symbols seeded.
    """
    if symbols is None:
        symbols = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]

    seeded = 0
    for symbol in symbols:
        try:
            baseline = get_oi_baseline(symbol)
            if baseline:
                # Store as reference in a JSON file for live OI comparison
                import json
                oi_file = Path(f"oi_baseline_{symbol}.json")
                oi_file.write_text(json.dumps({
                    "symbol":    symbol,
                    "date":      date.today().isoformat(),
                    "baseline":  {str(k):v for k,v in baseline.items()},
                    "pcr":       get_pcr_from_bhavcopy(symbol),
                }))
                seeded += 1
                logger.info("OI seeded: %s (%d strikes)", symbol, len(baseline))
        except Exception as e:
            logger.debug("OI seed %s: %s", symbol, e)

    return seeded
