"""
quant_models.py — Professional Quantitative Finance Models

Implements the key models from institutional finance:
  1. Markowitz Mean-Variance Portfolio Optimization
  2. Black-Scholes Option Pricing (with Greeks)
  3. CAPM — Risk-Return model
  4. Kelly Criterion (with fractional Kelly)
  5. VaR & CVaR (Historical + Parametric)
  6. Geometric Brownian Motion (GBM price simulation)

Used internally by:
  - adaptive_position_sizer.py (Kelly)
  - value_at_risk.py (VaR/CVaR)
  - greeks_sizer.py (Black-Scholes Greeks)
  - portfolio_heat.py (correlation/Markowitz)
"""
from __future__ import annotations
import numpy as np
from typing import Dict


# ─────────────────────────────────────────────────────────────────────────────
# 1. BLACK-SCHOLES — Option Pricing + Greeks
# ─────────────────────────────────────────────────────────────────────────────
def black_scholes(
    S: float,    # Spot price
    K: float,    # Strike price
    T: float,    # Time to expiry (years)
    r: float,    # Risk-free rate (e.g. 0.065 for 6.5%)
    sigma: float,# Implied volatility (e.g. 0.25 for 25%)
    option: str = "call",
) -> Dict:
    """
    Black-Scholes option pricing with full Greeks.
    Returns: price, delta, gamma, theta, vega, rho
    """
    from math import log, sqrt, exp
    try:
        from scipy.stats import norm
    except ImportError:
        # Fallback: approximate norm CDF
        def norm_cdf(x):
            return 0.5 * (1 + float(np.tanh(x * 0.7978845608)))
        class norm:
            @staticmethod
            def cdf(x): return norm_cdf(x)
            @staticmethod
            def pdf(x): return float(np.exp(-0.5*x*x) / np.sqrt(2*np.pi))

    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return {"price": 0, "delta": 0, "gamma": 0, "theta": 0, "vega": 0, "rho": 0}

    d1 = (log(S/K) + (r + 0.5*sigma**2)*T) / (sigma*sqrt(T))
    d2 = d1 - sigma*sqrt(T)

    if option.lower() == "call":
        price = S*norm.cdf(d1) - K*exp(-r*T)*norm.cdf(d2)
        delta = norm.cdf(d1)
        rho   = K*T*exp(-r*T)*norm.cdf(d2) / 100
    else:
        price = K*exp(-r*T)*norm.cdf(-d2) - S*norm.cdf(-d1)
        delta = norm.cdf(d1) - 1
        rho   = -K*T*exp(-r*T)*norm.cdf(-d2) / 100

    gamma = norm.pdf(d1) / (S*sigma*sqrt(T))
    vega  = S*norm.pdf(d1)*sqrt(T) / 100  # per 1% vol move
    theta = (-(S*norm.pdf(d1)*sigma)/(2*sqrt(T)) - r*K*exp(-r*T)*norm.cdf(d2)) / 365

    return {
        "price": round(price, 2),
        "delta": round(delta, 4),
        "gamma": round(gamma, 6),
        "theta": round(theta, 2),  # daily theta decay in ₹
        "vega":  round(vega, 2),
        "rho":   round(rho, 4),
        "d1": round(d1, 4), "d2": round(d2, 4),
    }


def implied_volatility(
    market_price: float, S: float, K: float, T: float,
    r: float = 0.065, option: str = "call",
    tol: float = 0.0001, max_iter: int = 100,
) -> float:
    """Compute implied volatility via Newton-Raphson."""
    sigma = 0.3  # initial guess
    for _ in range(max_iter):
        bs = black_scholes(S, K, T, r, sigma, option)
        price_diff = bs["price"] - market_price
        vega = bs["vega"] * 100  # convert back from per 1%
        if abs(price_diff) < tol: break
        if vega < 1e-6: break
        sigma -= price_diff / vega
        sigma = max(0.001, min(sigma, 5.0))
    return round(sigma, 4)


# ─────────────────────────────────────────────────────────────────────────────
# 2. MARKOWITZ MEAN-VARIANCE OPTIMIZATION
# ─────────────────────────────────────────────────────────────────────────────
def markowitz_optimize(
    returns: np.ndarray,     # (n_days, n_assets) return matrix
    risk_free: float = 0.065,
    target: str = "sharpe",  # "sharpe" | "min_var" | "equal"
) -> Dict:
    """
    Markowitz portfolio optimization.
    Returns optimal weights that maximize Sharpe or minimize variance.
    """
    try:
        n = returns.shape[1]
        mu  = returns.mean(axis=0) * 252      # annualized returns
        cov = np.cov(returns.T) * 252          # annualized covariance

        if target == "equal":
            w = np.ones(n) / n
        elif target == "min_var":
            # Minimum variance via analytical formula
            inv_cov = np.linalg.pinv(cov)
            ones = np.ones(n)
            w = inv_cov @ ones / (ones @ inv_cov @ ones)
        else:  # max Sharpe
            inv_cov = np.linalg.pinv(cov)
            excess  = mu - risk_free
            w_raw   = inv_cov @ excess
            w = w_raw / w_raw.sum() if w_raw.sum() > 0 else np.ones(n)/n

        w = np.clip(w, 0, 1); w /= w.sum()   # no shorting, sum=1

        port_return = float(w @ mu)
        port_vol    = float(np.sqrt(w @ cov @ w))
        sharpe      = (port_return - risk_free) / max(port_vol, 0.001)

        return {
            "weights":       [round(float(x), 4) for x in w],
            "return_annual": round(port_return * 100, 2),
            "vol_annual":    round(port_vol * 100, 2),
            "sharpe":        round(sharpe, 3),
        }
    except Exception as e:
        return {"error": str(e), "weights": [1.0]}


# ─────────────────────────────────────────────────────────────────────────────
# 3. CAPM — Capital Asset Pricing Model
# ─────────────────────────────────────────────────────────────────────────────
def capm(
    asset_returns: np.ndarray,   # daily returns of asset
    market_returns: np.ndarray,  # daily returns of NIFTY
    risk_free: float = 0.065,    # annual risk-free rate
) -> Dict:
    """
    CAPM: Expected Return = Rf + β × (Rm - Rf)
    Also computes Alpha (Jensen's alpha) — excess return vs model.
    """
    try:
        rf_daily = risk_free / 252
        excess_a = asset_returns  - rf_daily
        excess_m = market_returns - rf_daily

        cov = np.cov(excess_a, excess_m)
        beta = cov[0, 1] / max(cov[1, 1], 1e-9)

        market_premium  = float(excess_m.mean() * 252)
        expected_return = risk_free + beta * market_premium
        actual_return   = float(asset_returns.mean() * 252)
        alpha           = actual_return - expected_return

        corr = float(np.corrcoef(asset_returns, market_returns)[0, 1])

        return {
            "beta":            round(beta, 3),
            "alpha_annual":    round(alpha * 100, 2),
            "expected_return": round(expected_return * 100, 2),
            "actual_return":   round(actual_return * 100, 2),
            "correlation":     round(corr, 3),
            "r_squared":       round(corr**2, 3),
        }
    except Exception as e:
        return {"error": str(e), "beta": 1.0}


# ─────────────────────────────────────────────────────────────────────────────
# 4. GEOMETRIC BROWNIAN MOTION — Price Simulation
# ─────────────────────────────────────────────────────────────────────────────
def gbm_simulate(
    S0: float,       # current price
    mu: float,       # annual drift (e.g. 0.12 for 12%)
    sigma: float,    # annual volatility (e.g. 0.25)
    T: float = 1.0,  # years
    n_paths: int = 1000,
    n_steps: int = 252,
) -> Dict:
    """
    Geometric Brownian Motion price simulation.
    GBM: dS = μS dt + σS dW
    Used for: option pricing, risk assessment, scenario analysis.
    """
    dt = T / n_steps
    paths = np.zeros((n_paths, n_steps + 1))
    paths[:, 0] = S0

    for t in range(1, n_steps + 1):
        z = np.random.standard_normal(n_paths)
        paths[:, t] = paths[:, t-1] * np.exp((mu - 0.5*sigma**2)*dt + sigma*np.sqrt(dt)*z)

    final = paths[:, -1]
    return {
        "mean_price":    round(float(final.mean()), 2),
        "median_price":  round(float(np.median(final)), 2),
        "p5_price":      round(float(np.percentile(final, 5)), 2),
        "p95_price":     round(float(np.percentile(final, 95)), 2),
        "prob_profit":   round(float((final > S0).mean() * 100), 1),
        "expected_gain": round(float((final.mean() / S0 - 1) * 100), 2),
        "paths":         paths,  # full simulation matrix
    }


# ─────────────────────────────────────────────────────────────────────────────
# 5. VAR & CVAR — Parametric + Historical
# ─────────────────────────────────────────────────────────────────────────────
def compute_var_cvar(
    returns: np.ndarray,
    capital: float,
    confidence: float = 0.95,
    method: str = "historical",
) -> Dict:
    """
    VaR = maximum expected loss at confidence level.
    CVaR (Expected Shortfall) = average loss beyond VaR.

    Both Historical and Parametric methods.
    """
    try:
        if method == "historical":
            var_pct  = float(np.percentile(returns, (1-confidence)*100))
            tail     = returns[returns <= var_pct]
            cvar_pct = float(tail.mean()) if len(tail) > 0 else var_pct
        else:  # parametric
            mu, sigma = returns.mean(), returns.std()
            from scipy.stats import norm
            var_pct  = float(norm.ppf(1-confidence, mu, sigma))
            cvar_pct = float(mu - sigma * norm.pdf(norm.ppf(1-confidence)) / (1-confidence))

        return {
            "var_pct":     round(abs(var_pct) * 100, 2),
            "var_inr":     round(abs(var_pct) * capital, 0),
            "cvar_pct":    round(abs(cvar_pct) * 100, 2),
            "cvar_inr":    round(abs(cvar_pct) * capital, 0),
            "confidence":  confidence,
            "method":      method,
        }
    except Exception as e:
        return {"error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# 6. KELLY CRITERION — Optimal Position Sizing
# ─────────────────────────────────────────────────────────────────────────────
def kelly_fraction(
    win_rate: float,
    avg_win: float,
    avg_loss: float,
    fraction: float = 0.5,  # 0.5 = half Kelly (safer)
) -> Dict:
    """
    Kelly Criterion: f* = (p×b - q) / b
    where p=win_rate, q=1-p, b=avg_win/avg_loss (odds)

    Full Kelly maximizes long-run growth but is volatile.
    Half Kelly (fraction=0.5) is standard for trading.
    """
    if avg_loss <= 0 or win_rate <= 0 or win_rate >= 1:
        return {"kelly_f": 0.0, "half_kelly": 0.0}

    b = avg_win / avg_loss
    q = 1.0 - win_rate
    full_kelly = (win_rate * b - q) / b
    full_kelly = max(0.0, min(full_kelly, 1.0))
    applied    = full_kelly * fraction

    return {
        "full_kelly":    round(full_kelly, 4),
        "applied_kelly": round(applied, 4),
        "fraction":      fraction,
        "edge":          round((win_rate * avg_win - q * avg_loss), 2),
        "odds_ratio":    round(b, 2),
        "description": (
            f"Bet {applied*100:.1f}% of capital per trade "
            f"({fraction*100:.0f}% Kelly for safety)"
        ),
    }
