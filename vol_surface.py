"""
vol_surface.py

Builds a full volatility surface from live NSE option chain data.
Computes IV skew across all strikes, real term structure from two expiries,
skew slope (put/call wing tilt), and PCR — converts to a score modifier.

API:
  get_vol_surface_signal(underlying="NIFTY") → {
    # core fields (unchanged)
    "atm_iv":       float,  # near-expiry ATM implied vol (0-100 scale)
    "skew":         float,  # OTM put IV - OTM call IV  (positive = fear/bearish)
    "skew_pct":     float,  # skew / atm_iv * 100
    "term_ratio":   float,  # near-expiry ATM IV / next-expiry ATM IV (>1.2 = event risk)
    "pcr":          float,  # total put OI / total call OI
    "signal":       str,    # BEARISH_SKEW | BULLISH_SKEW | EVENT_RISK | NORMAL
    "score_mod":    float,  # additive signal score modifier  (-0.30 to +0.30)
    "data_source":  str,
    # full surface fields
    "near_expiry":  str,    # nearest expiry date string used for ATM IV
    "next_expiry":  str,    # second expiry used for term structure (None if unavailable)
    "near_atm_iv":  float,  # same as atm_iv (explicit alias)
    "next_atm_iv":  float,  # ATM IV for next expiry (0 if unavailable)
    "skew_slope":   float,  # linear slope of CE IV vs moneyness_pct (neg = normal skew)
    "wing_spread":  float,  # deep OTM put IV (strikes <95%) minus deep OTM call IV (>105%)
    "iv_surface":   list,   # [{strike, moneyness_pct, ce_iv, pe_iv}] all near-expiry strikes
    "strikes_count": int,   # number of strikes with valid IV data
  }

Usage:
  python3 vol_surface.py
  from vol_surface import get_vol_surface_signal
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

CACHE_FILE        = "vol_surface_cache.json"
CACHE_MAX_AGE_SEC = 300    # 5-minute cache


def _cache_load(underlying: str) -> Optional[Dict[str, Any]]:
    try:
        p = Path(CACHE_FILE)
        if not p.exists():
            return None
        if time.time() - p.stat().st_mtime > CACHE_MAX_AGE_SEC:
            return None
        data = json.loads(p.read_text())
        if data.get("underlying") == underlying:
            return data
    except Exception:
        pass
    return None


def _cache_save(data: Dict[str, Any]) -> None:
    try:
        Path(CACHE_FILE).write_text(json.dumps(data, indent=2))
    except Exception:
        pass


def _fetch_chain(underlying: str, expiry: Optional[str]) -> Optional[Any]:
    """Attempt live fetch via option_chain_fetcher. Silently returns None on failure."""
    try:
        from option_chain_fetcher import OptionChainFetcher
        fetcher = OptionChainFetcher(
            underlying=underlying,
            strike_count_each_side=12,
            include_gamma_approx=False,
        )
        return fetcher.fetch(expiry=expiry)
    except Exception as exc:
        logger.debug("vol_surface: chain fetch failed: %s", exc)
    return None


def _build_from_df(df: Any, spot: float) -> Dict[str, Any]:
    """Compute skew, PCR, full IV surface from a single-expiry strikes DataFrame."""
    if df is None or len(df) == 0:
        return {}
    try:
        import pandas as pd

        df = df.copy()
        for col in ["strikePrice", "CE_impliedVolatility", "PE_impliedVolatility",
                    "CE_openInterest", "PE_openInterest"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

        df = df.sort_values("strikePrice").reset_index(drop=True)

        # ATM
        df["_dist"] = abs(df["strikePrice"] - spot)
        atm_row = df.loc[df["_dist"].idxmin()]
        ce_atm  = float(atm_row.get("CE_impliedVolatility", 0))
        pe_atm  = float(atm_row.get("PE_impliedVolatility", 0))
        atm_iv  = (ce_atm + pe_atm) / 2

        # OTM skew: strikes 3-8% away from spot (keep existing field for compatibility)
        lo_bound = spot * 0.92
        hi_bound = spot * 1.08
        otm_puts  = df[(df["strikePrice"] >= lo_bound) & (df["strikePrice"] < spot * 0.97)]
        otm_calls = df[(df["strikePrice"] >  spot * 1.03) & (df["strikePrice"] <= hi_bound)]
        put_iv  = float(otm_puts["PE_impliedVolatility"].mean()) if len(otm_puts)  else pe_atm
        call_iv = float(otm_calls["CE_impliedVolatility"].mean()) if len(otm_calls) else ce_atm

        skew     = round(put_iv - call_iv, 2)
        skew_pct = round(skew / max(atm_iv, 1e-9) * 100, 2)

        # PCR by open interest (full chain)
        total_pe = float(df["PE_openInterest"].sum())
        total_ce = float(df["CE_openInterest"].sum())
        pcr      = round(total_pe / max(total_ce, 1), 4)

        # Full IV surface — all strikes with valid IV
        df["_moneyness"] = ((df["strikePrice"] - spot) / spot * 100).round(2)
        iv_surface = []
        for _, row in df.iterrows():
            ce_iv = float(row.get("CE_impliedVolatility", 0))
            pe_iv = float(row.get("PE_impliedVolatility", 0))
            if ce_iv > 0 or pe_iv > 0:
                iv_surface.append({
                    "strike":        float(row["strikePrice"]),
                    "moneyness_pct": float(row["_moneyness"]),
                    "ce_iv":         round(ce_iv, 2),
                    "pe_iv":         round(pe_iv, 2),
                })
        strikes_count = len(iv_surface)

        # Skew slope: linear regression of CE_IV vs moneyness_pct
        # Negative slope = higher IV at lower strikes (normal put skew)
        skew_slope = 0.0
        valid_ce = [(r["moneyness_pct"], r["ce_iv"]) for r in iv_surface if r["ce_iv"] > 0]
        if len(valid_ce) >= 3:
            xs = [v[0] for v in valid_ce]
            ys = [v[1] for v in valid_ce]
            n  = len(xs)
            mx, my = sum(xs) / n, sum(ys) / n
            denom = sum((x - mx) ** 2 for x in xs)
            if denom > 0:
                skew_slope = round(sum((xs[i]-mx)*(ys[i]-my) for i in range(n)) / denom, 4)

        # Wing spread: far OTM put IV vs far OTM call IV (>2.5% from spot)
        # 2.5% threshold matches typical chain coverage (12 strikes × 50pt steps for NIFTY)
        deep_puts  = [r["pe_iv"] for r in iv_surface if r["moneyness_pct"] < -2.5 and r["pe_iv"] > 0]
        deep_calls = [r["ce_iv"] for r in iv_surface if r["moneyness_pct"] >  2.5 and r["ce_iv"] > 0]
        wing_spread = round(
            (max(deep_puts) if deep_puts else pe_atm) -
            (max(deep_calls) if deep_calls else ce_atm),
            2,
        )

        return {
            "atm_iv":       round(atm_iv, 2),
            "near_atm_iv":  round(atm_iv, 2),
            "put_iv":       round(put_iv, 2),
            "call_iv":      round(call_iv, 2),
            "skew":         skew,
            "skew_pct":     skew_pct,
            "pcr":          pcr,
            "skew_slope":   skew_slope,
            "wing_spread":  wing_spread,
            "iv_surface":   iv_surface,
            "strikes_count": strikes_count,
        }
    except Exception as exc:
        logger.debug("vol_surface: _build_from_df error: %s", exc)
        return {}


def _parse_chain_result(oc: Any) -> tuple:
    """Extract (spot, DataFrame, sorted_expiries) from option_chain_fetcher result.

    DataFrame includes an 'expiryDate' column so callers can split by expiry.
    sorted_expiries is a list of expiry strings nearest-first (future dates only).
    """
    spot            = 0.0
    df              = None
    sorted_expiries: list = []
    try:
        if hasattr(oc, "spot") and hasattr(oc, "dataframe"):
            # OptionChainFetchResult object — single expiry, no date list available
            return float(oc.spot), oc.dataframe, []

        if isinstance(oc, dict):
            records_block = oc.get("records", {}) or {}
            spot = float(
                records_block.get("underlyingValue")
                or oc.get("spot")
                or oc.get("underlyingValue")
                or 0
            )

            # Sort available expiry dates nearest-first
            raw_expiries = records_block.get("expiryDates", []) or []
            today        = datetime.now().date()
            parsed: list = []
            for exp in raw_expiries:
                for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d"):
                    try:
                        parsed.append((datetime.strptime(exp, fmt).date(), exp))
                        break
                    except ValueError:
                        continue
            parsed.sort(key=lambda x: x[0])
            sorted_expiries = [exp for d, exp in parsed if d >= today]
            if not sorted_expiries and parsed:
                sorted_expiries = [parsed[0][1]]

            # Use records.data (all expiries) → falls back to filtered.data
            raw = (records_block.get("data")
                   or oc.get("filtered", {}).get("data")
                   or oc.get("data")
                   or [])
            if raw and spot > 0:
                import pandas as pd
                rows = []
                for row in raw:
                    ce = row.get("CE", {}) or {}
                    pe = row.get("PE", {}) or {}
                    rows.append({
                        "strikePrice":          float(row.get("strikePrice", 0) or 0),
                        "expiryDate":           str(row.get("expiryDate", "") or ""),
                        "CE_impliedVolatility": float(ce.get("impliedVolatility", 0) or 0),
                        "PE_impliedVolatility": float(pe.get("impliedVolatility", 0) or 0),
                        "CE_openInterest":      float(ce.get("openInterest", 0) or 0),
                        "PE_openInterest":      float(pe.get("openInterest", 0) or 0),
                    })
                df = pd.DataFrame(rows)
    except Exception as exc:
        logger.debug("vol_surface: parse error: %s", exc)
    return spot, df, sorted_expiries


def _get_atm_iv(df: Any, spot: float) -> float:
    """Return ATM IV (CE+PE average) from a single-expiry DataFrame. Returns 0 on failure."""
    try:
        import pandas as pd
        df = df.copy()
        df["strikePrice"] = pd.to_numeric(df["strikePrice"], errors="coerce")
        df["CE_impliedVolatility"] = pd.to_numeric(df["CE_impliedVolatility"], errors="coerce").fillna(0)
        df["PE_impliedVolatility"] = pd.to_numeric(df["PE_impliedVolatility"], errors="coerce").fillna(0)
        df["_dist"] = abs(df["strikePrice"] - spot)
        row = df.loc[df["_dist"].idxmin()]
        return (float(row["CE_impliedVolatility"]) + float(row["PE_impliedVolatility"])) / 2
    except Exception:
        return 0.0


def _interpret(surface: Dict[str, Any]) -> Dict[str, Any]:
    """Add signal label and score_mod to raw surface metrics."""
    atm_iv   = surface.get("atm_iv", 15.0)
    skew     = surface.get("skew", 0.0)
    skew_pct = surface.get("skew_pct", 0.0)
    pcr      = surface.get("pcr", 1.0)
    term     = surface.get("term_ratio", 1.0)

    if abs(skew_pct) > 15:
        signal = "BEARISH_SKEW" if skew > 0 else "BULLISH_SKEW"
    elif term > 1.25:
        signal = "EVENT_RISK"
    else:
        signal = "NORMAL"

    mod = 0.0
    if   skew > 5:  mod -= 0.15
    elif skew > 2:  mod -= 0.07
    elif skew < -3: mod += 0.10
    if pcr  > 1.4:  mod -= 0.10
    if term > 1.3:  mod -= 0.10

    surface["signal"]   = signal
    surface["score_mod"] = round(max(-0.30, min(0.30, mod)), 3)
    return surface


def _empty(underlying: str) -> Dict[str, Any]:
    return {
        "underlying":   underlying,
        "atm_iv":       15.0,
        "near_atm_iv":  15.0,
        "next_atm_iv":  0.0,
        "skew":         0.0,
        "skew_pct":     0.0,
        "pcr":          1.0,
        "term_ratio":   1.0,
        "signal":       "NORMAL",
        "score_mod":    0.0,
        "data_source":  "none",
        "near_expiry":  "",
        "next_expiry":  "",
        "skew_slope":   0.0,
        "wing_spread":  0.0,
        "iv_surface":   [],
        "strikes_count": 0,
        "generated_at": datetime.now().isoformat(),
    }


def get_vol_surface_signal(underlying: str = "NIFTY",
                            expiry: Optional[str] = None) -> Dict[str, Any]:
    """
    Main API for signal_engine. Returns full surface dict with score_mod.

    Fetches ALL expiries in one call, then splits into near + next to compute
    a real term_ratio (previously this was always 1.0).
    Uses a 5-minute file cache. Returns neutral defaults on failure.
    """
    cached = _cache_load(underlying)
    if cached:
        return cached

    result = _empty(underlying)
    # Fetch without expiry filter to get all expiries in one payload
    oc = _fetch_chain(underlying, None)
    if oc is None:
        return result

    spot, df_all, sorted_expiries = _parse_chain_result(oc)
    if spot <= 0 or df_all is None or df_all.empty:
        return result

    # Resolve near and next expiry
    near_exp = expiry or (sorted_expiries[0] if sorted_expiries else "")
    next_exp = sorted_expiries[1] if len(sorted_expiries) >= 2 else ""

    # Filter to near-expiry rows (or use all rows if expiry column is missing)
    if near_exp and "expiryDate" in df_all.columns:
        near_df = df_all[df_all["expiryDate"] == near_exp].copy()
        if near_df.empty:
            near_df = df_all  # fallback: use all rows
    else:
        near_df = df_all

    surface = _build_from_df(near_df, spot)
    if not surface:
        return result

    result.update(surface)
    result["spot"]        = round(spot, 2)
    result["data_source"] = "live_option_chain"
    result["near_expiry"] = near_exp

    # Compute real term_ratio from next expiry ATM IV
    if next_exp and "expiryDate" in df_all.columns:
        next_df = df_all[df_all["expiryDate"] == next_exp].copy()
        if not next_df.empty:
            next_atm_iv = _get_atm_iv(next_df, spot)
            if next_atm_iv > 0:
                near_iv = result.get("near_atm_iv") or result.get("atm_iv") or 0
                result["next_atm_iv"] = round(next_atm_iv, 2)
                result["next_expiry"] = next_exp
                result["term_ratio"]  = round(near_iv / next_atm_iv, 4)

    result = _interpret(result)
    result["underlying"]   = underlying
    result["generated_at"] = datetime.now().isoformat()
    _cache_save(result)
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(message)s")
    for und in ["NIFTY", "BANKNIFTY"]:
        s = get_vol_surface_signal(und)
        print(f"\n{und}:")
        print(f"  Near expiry : {s.get('near_expiry')}  ATM IV={s.get('near_atm_iv'):.1f}")
        print(f"  Next expiry : {s.get('next_expiry')}  ATM IV={s.get('next_atm_iv'):.1f}")
        print(f"  Term ratio  : {s.get('term_ratio'):.3f}  (>1.2 = event risk)")
        print(f"  Skew (3-8%) : {s.get('skew'):.1f}  ({s.get('skew_pct'):.1f}%)")
        print(f"  Skew slope  : {s.get('skew_slope'):.4f}  IV/% moneyness")
        print(f"  Wing spread : {s.get('wing_spread'):.1f}  deep OTM put - call IV")
        print(f"  PCR         : {s.get('pcr'):.2f}")
        print(f"  Strikes     : {s.get('strikes_count')}")
        print(f"  Signal      : {s.get('signal')}")
        print(f"  score_mod   : {s.get('score_mod')}")
        print(f"  Source      : {s.get('data_source')}")
        surf = s.get("iv_surface", [])
        if surf:
            print(f"  IV surface sample (first 5 strikes):")
            for row in surf[:5]:
                print(f"    K={row['strike']:.0f}  ({row['moneyness_pct']:+.1f}%)  "
                      f"CE={row['ce_iv']:.1f}  PE={row['pe_iv']:.1f}")
