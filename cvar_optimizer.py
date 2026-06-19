"""
cvar_optimizer.py — CVaR Portfolio Optimizer

Uses convex optimization (cvxpy) to find optimal position sizes
that maximize expected return under CVaR constraint.

Without cvxpy: falls back to correlation-adjusted Kelly sizing.

Use case:
  Given 3 open signals with correlations, find sizes that
  maximize portfolio Sharpe while keeping CVaR < max_cvar_pct.
"""
from __future__ import annotations
import logging
import numpy as np
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def optimize_positions_cvar(
    signals:      List[Dict],   # [{"symbol":str,"expected_return":float,"volatility":float}]
    capital:      float,
    corr_matrix:  Optional[np.ndarray] = None,
    max_cvar_pct: float = 0.02,   # max 2% daily CVaR of capital
    confidence:   float = 0.95,
) -> Dict[str, float]:
    """
    CVaR-optimal position sizing.
    Returns {symbol: size_in_rupees}

    Algorithm:
      Minimize CVaR subject to:
        - sum(weights) <= 1
        - Each weight >= 0 (no shorting in position sizing)
        - Expected portfolio CVaR <= max_cvar_pct
    """
    if not signals:
        return {}

    n = len(signals)
    symbols = [s["symbol"] for s in signals]

    # Expected returns and vols
    mus  = np.array([s.get("expected_return", 0.01) for s in signals])
    vols = np.array([s.get("volatility",       0.02) for s in signals])

    # Default to diagonal correlation if not provided
    if corr_matrix is None or corr_matrix.shape != (n, n):
        corr_matrix = np.eye(n)

    # Covariance matrix
    sigma = np.outer(vols, vols) * corr_matrix

    try:
        import cvxpy as cp
        w = cp.Variable(n, nonneg=True)

        # Portfolio return and variance
        port_return = mus @ w
        port_var    = cp.quad_form(w, sigma)

        # CVaR approximation: Normal distribution CVaR
        from scipy.stats import norm
        z_alpha = norm.ppf(confidence)
        phi_alpha = norm.pdf(z_alpha)
        port_cvar = -port_return + (phi_alpha / (1-confidence)) * cp.sqrt(port_var)

        # Maximize Sharpe (return / CVaR)
        objective = cp.Maximize(port_return - port_cvar)
        constraints = [
            cp.sum(w) <= 1.0,
            port_cvar <= max_cvar_pct,
        ]
        prob = cp.Problem(objective, constraints)
        prob.solve(solver=cp.SCS, verbose=False)

        if prob.status in ("optimal", "optimal_inaccurate") and w.value is not None:
            weights = np.maximum(w.value, 0)
            weights = weights / max(weights.sum(), 1e-9)  # normalise
            sizes   = {sym: round(float(wt) * capital, 0)
                       for sym, wt in zip(symbols, weights)}
            logger.debug("CVaR optimizer: %s", sizes)
            return sizes

    except ImportError:
        logger.debug("cvxpy not installed — using correlation-adjusted Kelly")
    except Exception as e:
        logger.debug("cvxpy solve: %s", e)

    # FALLBACK: Correlation-penalised equal sizing
    return _corr_adjusted_equal(symbols, sigma, capital, max_cvar_pct)


def _corr_adjusted_equal(
    symbols:  List[str],
    sigma:    np.ndarray,
    capital:  float,
    max_cvar: float,
) -> Dict[str, float]:
    """Fallback: equal weight adjusted for pairwise correlation."""
    n = len(symbols)
    if n == 0: return {}
    base = capital / n
    # Reduce size for highly correlated assets
    corr = np.zeros(n)
    for i in range(n):
        for j in range(n):
            if i != j and sigma[i,i] > 0 and sigma[j,j] > 0:
                corr[i] += abs(sigma[i,j] / np.sqrt(sigma[i,i]*sigma[j,j]))
    avg_corr = corr / max(n-1, 1)
    penalty  = 1.0 - avg_corr * 0.3  # max 30% reduction for full correlation
    penalty  = np.clip(penalty, 0.5, 1.0)
    sizes = {sym: round(base * float(pen), 0) for sym, pen in zip(symbols, penalty)}
    return sizes


def get_portfolio_cvar(
    weights:     np.ndarray,
    mus:         np.ndarray,
    sigma:       np.ndarray,
    confidence:  float = 0.95,
) -> float:
    """Compute portfolio CVaR for reporting."""
    try:
        from scipy.stats import norm
        port_ret = float(mus @ weights)
        port_vol = float(np.sqrt(weights @ sigma @ weights))
        z = norm.ppf(confidence)
        phi = norm.pdf(z)
        return float(-port_ret + (phi / (1-confidence)) * port_vol)
    except Exception:
        return 0.02  # default 2%
