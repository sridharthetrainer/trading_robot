"""
value_at_risk.py — Value at Risk (VaR) for NSE Algo Trading System

WHAT IS VaR?
  VaR answers: "What is the maximum I can lose in one day with 95% confidence?"
  Example: VaR(95%) = ₹3,200 means on 95% of days, you will NOT lose more than ₹3,200.
  The other 5% of days (roughly 1 day per month), losses could exceed ₹3,200.

THREE METHODS IMPLEMENTED:
  1. Historical Simulation VaR  — uses actual past P&L distribution (most accurate)
  2. Parametric (Gaussian) VaR  — assumes normal distribution (fast, simple)
  3. Monte Carlo VaR             — simulates 10,000 scenarios (most flexible)

ALSO COMPUTED:
  CVaR (Conditional VaR / Expected Shortfall) — average loss BEYOND the VaR threshold
  Example: VaR=₹3,200, CVaR=₹5,100 means on bad days you average ₹5,100 loss

INTEGRATION:
  → Called by portfolio_risk.py before approving each new trade
  → If new trade would breach VaR limit → reduce size or block
  → Sent in daily summary Telegram alert
  → Displayed in /status Telegram command
  → Used by adaptive_position_sizer for Kelly-adjusted sizing

REFERENCES:
  Riskmetrics (JPMorgan) — original VaR methodology
  Hull "Options, Futures and Other Derivatives" — Chapter 21-22
  Lopez de Prado "Advances in Financial ML" — proper backtesting
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_CACHE_FILE  = Path("var_cache.json")
_CACHE_TTL   = 3600   # 1 hour


# ── Core VaR Computations ─────────────────────────────────────────────────────

def _historical_var(
    returns:    np.ndarray,
    confidence: float = 0.95,
) -> Tuple[float, float]:
    """
    Historical Simulation VaR.
    Sorts actual past returns, picks the percentile cutoff.
    Most accurate because it uses real distribution (fat tails included).

    Returns: (var, cvar) — both as POSITIVE loss amounts.
    """
    if len(returns) < 5:
        return 0.0, 0.0
    sorted_r = np.sort(returns)           # ascending: worst first
    idx      = int((1 - confidence) * len(sorted_r))
    idx      = max(0, min(idx, len(sorted_r) - 1))
    var      = float(-sorted_r[idx])      # convert to positive loss
    cvar     = float(-sorted_r[:idx + 1].mean()) if idx > 0 else var
    return max(0.0, var), max(0.0, cvar)


def _parametric_var(
    returns:    np.ndarray,
    confidence: float = 0.95,
) -> Tuple[float, float]:
    """
    Parametric (Gaussian) VaR.
    Assumes returns are normally distributed.
    Fast but underestimates tail risk (options can have fat tails).

    z-scores: 90%=1.282, 95%=1.645, 99%=2.326, 99.5%=2.576
    """
    if len(returns) < 3:
        return 0.0, 0.0
    mu     = float(np.mean(returns))
    sigma  = float(np.std(returns, ddof=1)) + 1e-9
    z_map  = {0.90: 1.282, 0.95: 1.645, 0.99: 2.326, 0.995: 2.576}
    z      = z_map.get(confidence, 1.645)
    var    = max(0.0, -(mu - z * sigma))

    # CVaR for normal distribution: CVaR = mu - sigma * phi(z) / (1-c)
    from math import exp, pi, sqrt
    phi_z  = exp(-0.5 * z**2) / sqrt(2 * pi)
    cvar   = max(0.0, -(mu - sigma * phi_z / (1 - confidence)))
    return var, cvar


def _monte_carlo_var(
    returns:        np.ndarray,
    capital:        float,
    confidence:     float = 0.95,
    n_simulations:  int   = 10_000,
    horizon_days:   int   = 1,
) -> Tuple[float, float]:
    """
    Monte Carlo VaR.
    Simulates 10,000 possible P&L scenarios using bootstrapped returns.
    Best for options portfolios (non-linear payoff).

    Uses block bootstrap (preserves autocorrelation in returns).
    """
    if len(returns) < 5 or capital <= 0:
        return 0.0, 0.0
    rng       = np.random.default_rng(seed=42)
    block     = max(1, len(returns) // 10)   # bootstrap block size
    simulated = []
    for _ in range(n_simulations):
        # Sample 'horizon_days' blocks with replacement
        total_r = 0.0
        for _ in range(horizon_days):
            start = rng.integers(0, len(returns) - block + 1)
            total_r += float(returns[start:start + block].mean())
        simulated.append(total_r * capital)
    sim_arr = np.array(simulated)
    var  = float(-np.percentile(sim_arr, (1 - confidence) * 100))
    bad  = sim_arr[sim_arr <= -var]
    cvar = float(-bad.mean()) if len(bad) > 0 else var
    return max(0.0, var), max(0.0, cvar)


# ── Data Loading ──────────────────────────────────────────────────────────────

def _load_daily_pnl(
    db_path:   str = "trades.db",
    days_back: int = 252,           # 1 trading year
) -> np.ndarray:
    """
    Load daily realised P&L from trades.db.
    Returns array of daily P&L values (positive = profit, negative = loss).
    """
    try:
        cutoff = (date.today() - timedelta(days=days_back)).isoformat()
        conn   = sqlite3.connect(db_path, timeout=10)
        # Try to get per-day realised P&L
        rows = conn.execute(
            """
            SELECT DATE(COALESCE(exit_time, entry_time), 'unixepoch', 'localtime') as trade_date,
                   SUM(COALESCE(realized_pnl, paper_pnl, live_pnl, 0)) as day_pnl
            FROM   trades
            WHERE  DATE(COALESCE(exit_time, entry_time), 'unixepoch', 'localtime') >= ?
              AND  status IN ('CLOSED','SQUARED_OFF','EXPIRED')
            GROUP  BY trade_date
            ORDER  BY trade_date
            """,
            (cutoff,)
        ).fetchall()
        conn.close()
        if rows:
            return np.array([float(r[1]) for r in rows])
    except Exception as e:
        logger.debug("_load_daily_pnl trades.db: %s", e)

    # Fallback: try signal_log.db if available
    try:
        conn = sqlite3.connect("signal_log.db", timeout=10)
        rows = conn.execute(
            """
            SELECT signal_date,
                   SUM(CASE WHEN tb_label=1 THEN ABS(score)*100
                            WHEN tb_label=-1 THEN -ABS(score)*100
                            ELSE 0 END) as proxy_pnl
            FROM   signal_log
            WHERE  executed=1 AND tb_label != -99
              AND  signal_date >= ?
            GROUP  BY signal_date
            """,
            (cutoff,)
        ).fetchall()
        conn.close()
        if rows:
            return np.array([float(r[1]) for r in rows])
    except Exception as e:
        logger.debug("_load_daily_pnl signal_log: %s", e)

    return np.array([])


def _load_position_values(open_trades: List[Dict]) -> List[float]:
    """
    Extract current position values from open trades list.
    Returns list of (unrealised P&L) per position.
    """
    vals = []
    for t in (open_trades or []):
        try:
            vals.append(float(
                t.get("unrealized_pnl",
                      t.get("unrealised_pnl",
                            t.get("pnl", 0))) or 0
            ))
        except Exception:
            vals.append(0.0)
    return vals


# ── Main VaR Calculator ───────────────────────────────────────────────────────

class ValueAtRisk:
    """
    Full VaR engine for the trading system.

    Usage:
        var_engine = ValueAtRisk(capital=100_000)
        report = var_engine.compute()
        # report.var_95      → ₹3,200  (95% confidence)
        # report.cvar_95     → ₹5,100  (expected shortfall)
        # report.var_pct     → 3.2%    (as % of capital)
        # report.safe_to_trade → True/False
    """

    def __init__(
        self,
        capital:      float = 100_000,
        db_path:      str   = "trades.db",
        days_back:    int   = 252,
        confidence:   float = 0.95,
        var_limit_pct: float = 0.05,    # block trading if VaR > 5% of capital
    ) -> None:
        self.capital       = float(capital)
        self.db_path       = db_path
        self.days_back     = days_back
        self.confidence    = confidence
        self.var_limit_pct = var_limit_pct
        self._cache: Dict  = {}

    def compute(
        self,
        open_trades:   Optional[List[Dict]] = None,
        force_refresh: bool = False,
    ) -> "VaRReport":
        """
        Compute full VaR report.
        Uses cache (1 hour TTL) to avoid recomputing every cycle.
        """
        # Try cache
        if not force_refresh and self._cache:
            age = time.time() - self._cache.get("ts", 0)
            if age < _CACHE_TTL:
                rpt = VaRReport(**self._cache.get("data", {}))
                rpt.open_trades = open_trades or []
                return rpt

        daily_pnl = _load_daily_pnl(self.db_path, self.days_back)
        n_days    = len(daily_pnl)

        # Need at least 5 data points for meaningful VaR
        if n_days < 5:
            logger.info("VaR: insufficient history (%d days) — using parametric", n_days)
            rpt = self._bootstrap_var()
        else:
            rpt = self._full_var(daily_pnl)

        # Add unrealised P&L component from open positions
        if open_trades:
            pos_vals   = _load_position_values(open_trades)
            unreal_var = self._position_var(pos_vals)
            rpt.position_var = unreal_var
            rpt.total_var    = rpt.hist_var + unreal_var

        rpt.n_days_history  = n_days
        rpt.safe_to_trade   = rpt.total_var < self.capital * self.var_limit_pct
        rpt.var_limit       = self.capital * self.var_limit_pct

        # Cache result
        self._cache = {"ts": time.time(), "data": rpt.to_dict_base()}
        return rpt

    def _full_var(self, daily_pnl: np.ndarray) -> "VaRReport":
        """Compute all three VaR methods on real history."""
        # Returns as fraction of capital
        returns = daily_pnl / max(self.capital, 1.0)

        # Method 1: Historical simulation
        hist_var,  hist_cvar  = _historical_var(daily_pnl, self.confidence)
        # Method 2: Parametric
        para_var,  para_cvar  = _parametric_var(daily_pnl, self.confidence)
        # Method 3: Monte Carlo
        mc_var,    mc_cvar    = _monte_carlo_var(returns, self.capital, self.confidence)

        # Stressed VaR (99% confidence)
        s_var, s_cvar = _historical_var(daily_pnl, 0.99)

        # Annualised volatility (daily vol × √252)
        daily_vol = float(np.std(daily_pnl)) if len(daily_pnl) > 1 else 0.0
        ann_vol   = daily_vol * math.sqrt(252)

        # Worst/Best/Avg day
        worst = float(np.min(daily_pnl))
        best  = float(np.max(daily_pnl))
        avg   = float(np.mean(daily_pnl))

        # Max drawdown from cumulative P&L
        cum       = np.cumsum(daily_pnl)
        peak      = np.maximum.accumulate(cum)
        drawdowns = peak - cum
        max_dd    = float(drawdowns.max()) if len(drawdowns) > 0 else 0.0

        # Sharpe (annualised, assuming 0 risk-free)
        sharpe = float(avg / (daily_vol + 1e-9) * math.sqrt(252))

        # Win rate and profit factor
        wins   = daily_pnl[daily_pnl > 0]
        losses = daily_pnl[daily_pnl < 0]
        wr     = len(wins) / n_days if (n_days := len(daily_pnl)) > 0 else 0.0
        pf     = (float(wins.sum()) / max(abs(float(losses.sum())), 1)
                  if len(wins) > 0 and len(losses) > 0 else 0.0)

        return VaRReport(
            capital       = self.capital,
            confidence    = self.confidence,
            n_days_history= n_days,
            hist_var      = hist_var,
            hist_cvar     = hist_cvar,
            para_var      = para_var,
            para_cvar     = para_cvar,
            mc_var        = mc_var,
            mc_cvar       = mc_cvar,
            stressed_var  = s_var,
            stressed_cvar = s_cvar,
            total_var     = hist_var,  # updated later if open positions
            position_var  = 0.0,
            var_pct       = hist_var / max(self.capital, 1) * 100,
            daily_vol     = daily_vol,
            ann_vol       = ann_vol,
            worst_day     = worst,
            best_day      = best,
            avg_day       = avg,
            max_drawdown  = max_dd,
            sharpe        = round(sharpe, 3),
            win_rate      = round(wr, 3),
            profit_factor = round(pf, 3),
            var_limit     = self.capital * self.var_limit_pct,
            safe_to_trade = True,   # updated after position_var added
            open_trades   = [],
        )

    def _bootstrap_var(self) -> "VaRReport":
        """
        Bootstrap VaR when insufficient history.
        Uses config max_daily_loss as proxy.
        """
        try:
            import config as cfg
            configured_loss = float(getattr(cfg, "MAX_DAILY_LOSS", 3000))
        except Exception:
            configured_loss = self.capital * 0.03    # 3% default
        var_limit = self.capital * self.var_limit_pct
        # With thin history, do not let a fixed rupee MAX_DAILY_LOSS consume
        # all VaR headroom on small accounts. Start conservatively at half the
        # VaR budget, then let realised history take over once enough data exists.
        max_loss = max(1.0, min(configured_loss, var_limit * 0.50))
        return VaRReport(
            capital       = self.capital,
            confidence    = self.confidence,
            n_days_history= 0,
            hist_var      = max_loss,
            hist_cvar     = max_loss * 1.5,
            para_var      = max_loss,
            para_cvar     = max_loss * 1.5,
            mc_var        = max_loss,
            mc_cvar       = max_loss * 1.5,
            stressed_var  = max_loss * 2,
            stressed_cvar = max_loss * 3,
            total_var     = max_loss,
            position_var  = 0.0,
            var_pct       = max_loss / max(self.capital, 1) * 100,
            daily_vol     = max_loss / 2,
            ann_vol       = max_loss / 2 * math.sqrt(252),
            worst_day     = -max_loss,
            best_day      = max_loss,
            avg_day       = 0.0,
            max_drawdown  = 0.0,
            sharpe        = 0.0,
            win_rate      = 0.0,
            profit_factor = 0.0,
            var_limit     = self.capital * self.var_limit_pct,
            safe_to_trade = True,
            open_trades   = [],
        )

    def _position_var(self, pos_vals: List[float]) -> float:
        """
        Estimate VaR contribution from open positions.
        Uses historical volatility of underlying × position value.
        Simplified: assumes 2% adverse move on all positions (NSE 5-min ATR ≈ 0.3-0.5%)
        """
        if not pos_vals:
            return 0.0
        total_exposure = sum(abs(v) for v in pos_vals)
        # 2% adverse move at 95% confidence (NSE intraday 2σ ≈ 2%)
        return total_exposure * 0.02

    def check_new_trade_var(
        self,
        trade_risk:    float,
        current_var:   Optional[float] = None,
    ) -> Dict:
        """
        Check if adding a new trade keeps total VaR within limit.

        Args:
            trade_risk:  estimated max loss for the new trade (stop_loss distance × qty)
            current_var: current portfolio VaR (computed if not provided)

        Returns:
            allowed:      bool
            combined_var: VaR if trade added
            headroom:     remaining VaR capacity
            reason:       explanation
        """
        if current_var is None:
            rpt = self.compute()
            current_var = rpt.total_var

        combined_var = current_var + trade_risk * 0.5  # diversification benefit ≈ 50%
        limit        = self.capital * self.var_limit_pct
        headroom     = limit - combined_var
        allowed      = combined_var <= limit

        return {
            "allowed":      allowed,
            "combined_var": round(combined_var, 0),
            "var_limit":    round(limit, 0),
            "headroom":     round(headroom, 0),
            "trade_risk":   round(trade_risk, 0),
            "reason":       (
                f"VaR OK: ₹{combined_var:,.0f} < limit ₹{limit:,.0f}"
                if allowed else
                f"VaR BREACH: ₹{combined_var:,.0f} > limit ₹{limit:,.0f}"
            ),
        }

    def telegram_summary(self) -> str:
        """Format VaR report for Telegram /risk command."""
        rpt = self.compute()
        conf_pct = int(self.confidence * 100)
        c95  = "🟢" if rpt.total_var < rpt.var_limit * 0.6 else \
               "🟡" if rpt.total_var < rpt.var_limit else "🔴"
        lines = [
            f"📊 <b>VALUE AT RISK REPORT</b>",
            f"Capital: ₹{self.capital:,.0f}  |  {rpt.n_days_history}d history",
            f"{'─'*32}",
            f"  {c95} VaR ({conf_pct}%)   <b>₹{rpt.total_var:,.0f}</b>  "
            f"({rpt.var_pct:.1f}% of capital)",
            f"  💀 CVaR ({conf_pct}%)   ₹{rpt.hist_cvar:,.0f}  "
            f"(expected loss on bad day)",
            f"  🚨 Stressed VaR  ₹{rpt.stressed_var:,.0f}  (99% confidence)",
            f"  📉 Max Drawdown  ₹{rpt.max_drawdown:,.0f}",
            f"{'─'*32}",
            f"  Limit:   ₹{rpt.var_limit:,.0f} ({self.var_limit_pct*100:.0f}% cap)",
            f"  Headroom: ₹{max(0.0, rpt.var_limit - rpt.total_var):,.0f}",
            f"{'─'*32}",
            f"  Daily vol:  ₹{rpt.daily_vol:,.0f}",
            f"  Ann vol:    ₹{rpt.ann_vol:,.0f}",
            f"  Sharpe:     {rpt.sharpe:.2f}",
            f"  Win rate:   {rpt.win_rate*100:.0f}%",
            f"  Pfactor:    {rpt.profit_factor:.2f}",
            f"{'─'*32}",
            f"  {'✅ Safe to trade' if rpt.safe_to_trade else '🛑 VaR LIMIT BREACHED — reduce exposure'}",
        ]
        return "\n".join(lines)


# ── Report Dataclass ──────────────────────────────────────────────────────────

class VaRReport:
    __slots__ = [
        'capital','confidence','n_days_history',
        'hist_var','hist_cvar','para_var','para_cvar','mc_var','mc_cvar',
        'stressed_var','stressed_cvar','total_var','position_var','var_pct',
        'daily_vol','ann_vol','worst_day','best_day','avg_day',
        'max_drawdown','sharpe','win_rate','profit_factor',
        'var_limit','safe_to_trade','open_trades',
    ]
    def __init__(self, **kwargs):
        for k in self.__slots__:
            setattr(self, k, kwargs.get(k, 0.0))

    def to_dict_base(self) -> dict:
        return {k: getattr(self, k) for k in self.__slots__ if k != 'open_trades'}

    def __repr__(self) -> str:
        return (f"VaRReport(VaR95=₹{self.total_var:,.0f} "
                f"CVaR=₹{self.hist_cvar:,.0f} "
                f"safe={self.safe_to_trade})")


# ── Singleton ─────────────────────────────────────────────────────────────────

_var_engine: Optional[ValueAtRisk] = None

def get_var_engine(capital: float = 100_000) -> ValueAtRisk:
    global _var_engine
    if _var_engine is None or abs(_var_engine.capital - capital) > 1000:
        try:
            import config as cfg
            _cap = float(capital or getattr(cfg, 'CAPITAL', 100_000))
            _lim = float(getattr(cfg, 'VAR_LIMIT_PCT', 0.05))
        except Exception:
            _cap, _lim = capital, 0.05
        _var_engine = ValueAtRisk(capital=_cap, var_limit_pct=_lim)
    return _var_engine
