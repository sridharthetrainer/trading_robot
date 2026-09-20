"""
svi_smile_relative_value.py -- exploratory SVI (raw stochastic-volatility-
inspired) smile calibration on REAL NIFTY option chains
(RESEARCH_QUEUE_2026-09-13.md, "volatility-surface relative-value idea").

Per the queue's own explicit caution, this borrows the METHODOLOGY cited
(fit a per-expiry SVI smile, measure each option's residual deviation
from it, test whether residuals show repeatable convergence -- BEFORE
ever constructing a trade) from public research, NOT any code -- built
entirely from scratch here using scipy + this project's own existing
option_intraday_pricer.implied_vol() (already-tested Black-Scholes IV
inversion), against options_nifty.db's real EOD settlement chains. None
of the cited external repos were read for their code, only their
documented approach (SVI as a standard, decades-old, publicly-described
parametrization -- Gatheral 2004 -- not something novel to any one repo).

SVI raw parametrization (per expiry, on a given day):
    w(k) = a + b * ( rho*(k-m) + sqrt((k-m)^2 + sigma^2) )
where k = log(K/F) is log-moneyness and w = implied_vol^2 * T is total
variance. Fit via bounded least squares to the day's actual OTM-side IVs
(calls for K>=spot, puts for K<spot -- avoids noisier deep-ITM quotes,
standard practice). This is a SIMPLIFIED fit: no explicit no-arbitrage
(butterfly/calendar) constraints beyond basic parameter bounds (b>=0,
|rho|<1, sigma>0) -- a full production calibration would need those;
flagged here as a real limitation, not hidden.

Exploratory question only, per the queue's framing: do options whose IV
sits unusually far from the fitted smile on day T show their residual
SHRINK (converge toward the smile) by day T+N, on average? This is
diagnostic, not a backtest -- no P&L, no trade construction, no
promotion. A later step (not this one) would need to translate any
found convergence into an actual, cost-adjusted, executable trade
structure before it means anything tradable.
"""
from __future__ import annotations

import sqlite3
from datetime import date as _date
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.optimize import least_squares

from option_intraday_pricer import implied_vol, year_fraction, RISK_FREE_RATE

OPTIONS_DB = "options_nifty.db"


def _load_chain(conn: sqlite3.Connection, d: str, expiry: str) -> Optional[pd.DataFrame]:
    df = pd.read_sql_query(
        "SELECT strike, opt_type, close, settle, underlying FROM options_eod "
        "WHERE date=? AND expiry=?", conn, params=(d, expiry))
    if df.empty:
        return None
    df["px"] = df["settle"].where(df["settle"] > 0, df["close"])
    df = df[(df["px"] > 0) & (df["underlying"] > 0)]
    return df if len(df) >= 10 else None


def _build_smile_points(df: pd.DataFrame, d: str, expiry: str) -> Optional[Tuple[np.ndarray, np.ndarray, float, float]]:
    """Returns (k_array, w_array, forward, T) using OTM-side quotes only."""
    spot = float(df["underlying"].median())
    anchor_date = _date.fromisoformat(d)
    expiry_date = _date.fromisoformat(expiry)
    from datetime import datetime, time as dtime
    T = year_fraction(datetime.combine(anchor_date, dtime(15, 30)), expiry_date)
    if T <= 0:
        return None
    ks, ws = [], []
    for _, row in df.iterrows():
        strike, opt_type, px = float(row["strike"]), row["opt_type"], float(row["px"])
        is_otm_call = opt_type == "CE" and strike >= spot
        is_otm_put = opt_type == "PE" and strike < spot
        if not (is_otm_call or is_otm_put):
            continue
        iv = implied_vol(px, spot, strike, T, RISK_FREE_RATE, opt_type)
        if iv is None or iv <= 0.02 or iv >= 2.0:
            continue
        k = np.log(strike / spot)
        ks.append(k)
        ws.append((iv ** 2) * T)
    if len(ks) < 8:
        return None
    return np.array(ks), np.array(ws), spot, T


def _svi_w(params, k):
    a, b, rho, m, sigma = params
    return a + b * (rho * (k - m) + np.sqrt((k - m) ** 2 + sigma ** 2))


def _fit_svi(k: np.ndarray, w: np.ndarray) -> Optional[np.ndarray]:
    a0 = float(np.min(w)) * 0.9
    b0 = 0.1
    rho0 = -0.3
    m0 = float(np.mean(k))
    sigma0 = max(0.05, float(np.std(k)))
    x0 = [a0, b0, rho0, m0, sigma0]
    lb = [0.0, 0.0, -0.999, -2.0, 0.001]
    ub = [max(w) * 2 + 1e-6, 5.0, 0.999, 2.0, 5.0]

    def resid(p):
        return _svi_w(p, k) - w

    try:
        res = least_squares(resid, x0, bounds=(lb, ub), max_nfev=2000)
    except Exception:
        return None
    return res.x if res.success else None


def calibrate_day(d: str, expiry: str) -> Optional[Dict[str, Any]]:
    conn = sqlite3.connect(OPTIONS_DB)
    try:
        df = _load_chain(conn, d, expiry)
        if df is None:
            return None
        built = _build_smile_points(df, d, expiry)
        if built is None:
            return None
        k, w, spot, T = built
        params = _fit_svi(k, w)
        if params is None:
            return None
        w_fit = _svi_w(params, k)
        rmse = float(np.sqrt(np.mean((w - w_fit) ** 2)))
        # residual per strike, in IV terms (approx: d(iv) ~ d(w)/(2*iv*T))
        iv_actual = np.sqrt(np.maximum(w, 1e-8) / T)
        iv_fit = np.sqrt(np.maximum(w_fit, 1e-8) / T)
        residual_iv = iv_actual - iv_fit
        strikes = df.loc[df.index[:0], :]  # placeholder, replaced below
        return {"date": d, "expiry": expiry, "spot": spot, "T": T,
                "params": params, "k": k, "w": w, "rmse": rmse,
                "residual_iv": residual_iv, "iv_actual": iv_actual}
    finally:
        conn.close()


def main() -> None:
    conn = sqlite3.connect(OPTIONS_DB)
    sample_days = [r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM options_eod ORDER BY date DESC LIMIT 10")]
    conn.close()

    print("=== SVI fit sanity check on 10 recent days (nearest expiry each) ===")
    for d in sample_days:
        conn = sqlite3.connect(OPTIONS_DB)
        exp = conn.execute(
            "SELECT DISTINCT expiry FROM options_eod WHERE date=? AND expiry>=? "
            "ORDER BY expiry LIMIT 1", (d, d)).fetchone()
        conn.close()
        if not exp:
            continue
        rep = calibrate_day(d, exp[0])
        if rep is None:
            print(f"{d} exp={exp[0]}: fit failed / insufficient data")
            continue
        a, b, rho, m, sigma = rep["params"]
        print(f"{d} exp={exp[0]}: n_points={len(rep['k'])} spot={rep['spot']:.1f} "
              f"T={rep['T']:.4f} RMSE(w)={rep['rmse']:.6f} "
              f"params(a,b,rho,m,sigma)=({a:.4f},{b:.4f},{rho:.4f},{m:.4f},{sigma:.4f}) "
              f"max|resid_iv|={np.max(np.abs(rep['residual_iv'])):.4f}")


def residual_convergence_test(n_days: int = 60) -> Dict[str, Any]:
    """For strikes flagged as large-residual (top decile |residual_iv|) on day
    T, check whether that SAME (expiry, strike)'s residual shrinks by day T+1.
    Diagnostic only -- no trade, no P&L, per this module's own scope."""
    conn = sqlite3.connect(OPTIONS_DB)
    days = [r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM options_eod ORDER BY date DESC LIMIT ?", (n_days + 5,))]
    conn.close()
    days = sorted(days)  # oldest -> newest

    fits: Dict[str, Dict[str, Any]] = {}
    for d in days:
        conn = sqlite3.connect(OPTIONS_DB)
        exp = conn.execute(
            "SELECT DISTINCT expiry FROM options_eod WHERE date=? AND expiry>=? "
            "ORDER BY expiry LIMIT 1", (d, d)).fetchone()
        conn.close()
        if not exp:
            continue
        rep = calibrate_day(d, exp[0])
        if rep is None:
            continue
        rep["expiry_id"] = exp[0]
        fits[d] = rep

    fit_days = sorted(fits.keys())
    pairs_shrunk, pairs_grew, deltas = 0, 0, []
    n_flagged_total = 0
    for i in range(len(fit_days) - 1):
        d_t, d_t1 = fit_days[i], fit_days[i + 1]
        f_t, f_t1 = fits[d_t], fits[d_t1]
        if f_t["expiry_id"] != f_t1["expiry_id"]:
            continue  # expiry rolled -- not a same-contract comparison
        resid_t = f_t["residual_iv"]
        thresh = np.quantile(np.abs(resid_t), 0.90)
        flagged_k = f_t["k"][np.abs(resid_t) >= thresh]
        flagged_resid_t = resid_t[np.abs(resid_t) >= thresh]
        # match each flagged k to the closest k in day t+1's smile (same
        # strike/spot should give a very close k, small spot drift aside)
        for k_val, r_t in zip(flagged_k, flagged_resid_t):
            idx = int(np.argmin(np.abs(f_t1["k"] - k_val)))
            if abs(f_t1["k"][idx] - k_val) > 0.01:
                continue  # no close match -- strike likely stopped trading
            r_t1 = f_t1["residual_iv"][idx]
            n_flagged_total += 1
            delta = abs(r_t1) - abs(r_t)
            deltas.append(delta)
            if abs(r_t1) < abs(r_t):
                pairs_shrunk += 1
            else:
                pairs_grew += 1

    n = len(deltas)
    if n == 0:
        return {"error": "no matched pairs found"}
    mean_delta = float(np.mean(deltas))
    return {
        "n_fit_days": len(fit_days), "n_flagged_pairs": n_flagged_total,
        "pct_shrunk": round(pairs_shrunk / n, 4), "pct_grew": round(pairs_grew / n, 4),
        "mean_change_in_abs_residual": round(mean_delta, 5),
        "conclusion": (
            f"{pairs_shrunk}/{n} ({pairs_shrunk/n:.1%}) of large-residual strikes "
            f"had a SMALLER |residual| the next day (mean change: "
            f"{mean_delta:+.5f} IV-points). "
            + ("Consistent with mean-reversion toward the fitted smile."
               if mean_delta < 0 and pairs_shrunk / n > 0.55
               else "NOT consistent with reliable convergence -- residuals "
                    "look closer to a random walk than a mean-reverting process.")
        ),
    }


if __name__ == "__main__":
    main()
    print("\n=== Residual convergence test (diagnostic only, no trade construction) ===")
    conv = residual_convergence_test(n_days=60)
    print(conv)
