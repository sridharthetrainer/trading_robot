"""
options_backtest.py  —  Black-Scholes-priced options backtester (PHASE 1)

WHY THIS EXISTS
---------------
The other backtests (backtest_trend/mr/breakout/...) compute P&L as
(exit-entry) * lot in INDEX POINTS — a futures/linear proxy. That cannot
evaluate OPTION strategies, where P&L comes from the PREMIUM (delta<1, theta
decay, vega/IV). This module prices option legs with Black-Scholes from the
historical underlying spot + historical India VIX (as sigma), marching
day-by-day so theta/delta/vega all evolve. Weekly Thursday expiries.

HONEST LIMITATIONS (this is a MODEL, not tick-accurate option data):
  * Single ATM IV from India VIX → NO volatility skew/smile; OTM legs are
    approximate (real OTM puts trade richer than ATM-IV implies).
  * No real bid/ask — modelled via a slippage % on premium.
  * Assumes fills at BS mid ± slippage; ignores liquidity/margin calls.
A strategy that FAILS here almost certainly fails live; one that PASSES still
needs confirmation on real option-chain data before any capital.

Strategies (phase 1): long_straddle, short_strangle, iron_condor.

Usage:
    python options_backtest.py --strategy short_strangle --days 365
"""
from __future__ import annotations

import argparse
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from quant_models import black_scholes

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────
RISK_FREE      = 0.065      # ~6.5% (matches quant_models / greeks_live default)
LOT_SIZE       = 65         # NIFTY
STRIKE_STEP    = 50         # NIFTY strike interval
EXPIRY_WEEKDAY = 3          # Thursday (Mon=0); configurable via CLI
# Costs (option side): STT is 0.1% on the SELL-side PREMIUM (post-Oct-2024),
# NOT on notional — that was the bug in the index backtests.
STT_RATE_PREMIUM = 0.001    # 0.1% of premium, sell side
BROKERAGE_LEG    = 20.0     # flat per leg
SLIPPAGE_PCT     = 0.01     # 1% of premium per leg (proxy for bid/ask)


# ── Pricing ────────────────────────────────────────────────────────────────
def _round_strike(x: float) -> int:
    return int(round(x / STRIKE_STEP) * STRIKE_STEP)


def leg_price(spot: float, strike: float, T_years: float,
              vix_pct: float, opt_type: str) -> float:
    """BS premium for one option. At/after expiry (T<=0) → intrinsic value."""
    if T_years <= 0:
        if opt_type == "call":
            return max(spot - strike, 0.0)
        return max(strike - spot, 0.0)
    sigma = max(vix_pct, 1e-6) / 100.0
    return float(black_scholes(spot, strike, T_years, RISK_FREE, sigma, opt_type)["price"])


# ── Expiry calendar ────────────────────────────────────────────────────────
def next_expiry_on_or_after(d: date, weekday: int = EXPIRY_WEEKDAY) -> date:
    """Nearest weekly expiry (given weekday) on or after date d."""
    days = (weekday - d.weekday()) % 7
    return d + timedelta(days=days)


# ── Cost model ─────────────────────────────────────────────────────────────
def _entry_cost(premium: float, side: str) -> float:
    """Costs paid when OPENING a leg (per lot of LOT_SIZE)."""
    slip = premium * SLIPPAGE_PCT * LOT_SIZE
    stt  = premium * STT_RATE_PREMIUM * LOT_SIZE if side == "sell" else 0.0
    return BROKERAGE_LEG + slip + stt


# ── Leg bookkeeping ────────────────────────────────────────────────────────
class Leg:
    """One option leg. qty>0 = long (bought), qty<0 = short (sold)."""
    __slots__ = ("strike", "opt_type", "qty", "entry_prem")

    def __init__(self, strike: float, opt_type: str, qty: int, entry_prem: float):
        self.strike, self.opt_type, self.qty, self.entry_prem = strike, opt_type, qty, entry_prem


def _legs_value(legs: List[Leg], spot: float, T: float, vix: float) -> float:
    """Mark-to-market value of the leg portfolio (per LOT_SIZE), signed."""
    v = 0.0
    for lg in legs:
        p = leg_price(spot, lg.strike, T, vix, lg.opt_type)
        v += lg.qty * p * LOT_SIZE
    return v


def _open_costs(legs: List[Leg]) -> float:
    c = 0.0
    for lg in legs:
        side = "sell" if lg.qty < 0 else "buy"
        c += _entry_cost(lg.entry_prem, side) * abs(lg.qty)
    return c


def _close_costs(legs: List[Leg], spot: float, T: float, vix: float) -> float:
    c = 0.0
    for lg in legs:
        p = leg_price(spot, lg.strike, T, vix, lg.opt_type)
        # closing a long = sell (STT); closing a short = buy (no STT)
        side = "long" if lg.qty > 0 else "short_buyback"
        slip = p * SLIPPAGE_PCT * LOT_SIZE
        stt  = p * STT_RATE_PREMIUM * LOT_SIZE if lg.qty > 0 else 0.0
        c += (BROKERAGE_LEG + slip + stt) * abs(lg.qty)
    return c


# ── Strategy leg builders (at entry) ───────────────────────────────────────
def _build_legs(strategy: str, spot: float, T: float, vix: float,
                width_pts: Optional[int], lots: int) -> List[Leg]:
    atm = _round_strike(spot)
    # 1-sigma move estimate for OTM strike placement when width not given
    sd = spot * (max(vix, 1e-6) / 100.0) * (T ** 0.5)
    w  = width_pts if width_pts else max(STRIKE_STEP, _round_strike(sd))

    if strategy == "long_straddle":
        ce = leg_price(spot, atm, T, vix, "call")
        pe = leg_price(spot, atm, T, vix, "put")
        return [Leg(atm, "call", lots, ce), Leg(atm, "put", lots, pe)]

    if strategy == "short_strangle":
        ks_c, ks_p = atm + w, atm - w
        ce = leg_price(spot, ks_c, T, vix, "call")
        pe = leg_price(spot, ks_p, T, vix, "put")
        return [Leg(ks_c, "call", -lots, ce), Leg(ks_p, "put", -lots, pe)]

    if strategy == "iron_condor":
        # short ±w, long protective wings at ±2w (defined risk)
        sc, sp = atm + w, atm - w
        lc, lp = atm + 2 * w, atm - 2 * w
        return [
            Leg(sc, "call", -lots, leg_price(spot, sc, T, vix, "call")),
            Leg(sp, "put",  -lots, leg_price(spot, sp, T, vix, "put")),
            Leg(lc, "call",  lots, leg_price(spot, lc, T, vix, "call")),
            Leg(lp, "put",   lots, leg_price(spot, lp, T, vix, "put")),
        ]
    raise ValueError(f"unknown strategy {strategy}")


# ── Core daily-stepped backtest ────────────────────────────────────────────
def backtest_options(
    daily: pd.DataFrame,            # index=date, cols: spot, vix
    strategy: str = "short_strangle",
    initial_capital: float = 500_000.0,
    lots: int = 1,
    width_pts: Optional[int] = None,
    sl_pct: float = 1.0,            # short: stop if loss >= sl_pct * credit
    tp_pct: float = 0.6,            # short: take profit at tp_pct * credit
    expiry_weekday: int = EXPIRY_WEEKDAY,
    verbose: bool = False,
) -> Dict[str, Any]:
    """
    One position per weekly cycle: open the first trading day after an expiry,
    hold to expiry (or SL/TP), repeat. Equity marked daily (incl. open MTM).
    """
    if daily is None or len(daily) < 20:
        return {"total_pnl": 0.0, "num_trades": 0, "win_rate": 0.0,
                "sharpe": 0.0, "max_drawdown": 0.0,
                "final_capital": initial_capital, "equity_curve": [initial_capital]}

    dates = list(daily.index)
    capital = initial_capital
    equity_curve: List[float] = [capital]
    trades: List[Dict[str, Any]] = []

    legs: Optional[List[Leg]] = None
    entry_value = 0.0          # signed MTM at entry (credit<0 for short net)
    entry_credit = 0.0         # |net premium| received (short) or paid (long)
    expiry: Optional[date] = None
    open_cost = 0.0

    def days_to(d: date, exp: date) -> float:
        return max((exp - d).days, 0) / 365.0

    for i, d in enumerate(dates):
        d = d.date() if isinstance(d, (pd.Timestamp, datetime)) else d
        spot = float(daily["spot"].iloc[i])
        vix  = float(daily["vix"].iloc[i])

        # ---- manage open position ----
        if legs is not None:
            T = days_to(d, expiry)
            cur_value = _legs_value(legs, spot, T, vix)
            # P&L if we closed now (before close costs): value change since entry
            open_pnl = cur_value - entry_value
            exit_now = False
            reason = ""
            if d >= expiry or T <= 0:
                exit_now, reason = True, "expiry"
            elif entry_credit > 0:
                # short net credit: open_pnl positive = good (legs cheaper)
                if open_pnl >= tp_pct * entry_credit:
                    exit_now, reason = True, "take_profit"
                elif open_pnl <= -sl_pct * entry_credit:
                    exit_now, reason = True, "stop_loss"

            if exit_now:
                cc = _close_costs(legs, spot, max(T, 0.0), vix)
                realized = open_pnl - cc          # open_cost already taken at entry
                capital += realized
                trades.append({"exit": str(d), "reason": reason,
                               "pnl": realized, "credit": entry_credit})
                legs = None
                equity_curve.append(capital)
                continue
            else:
                equity_curve.append(capital + open_pnl)  # mark open MTM
                continue

        # ---- no open position: try to open a new weekly cycle ----
        exp = next_expiry_on_or_after(d, expiry_weekday)
        T = days_to(d, exp)
        if T <= 0:                       # it's expiry day itself → wait for next
            equity_curve.append(capital)
            continue
        legs = _build_legs(strategy, spot, T, vix, width_pts, lots)
        entry_value = _legs_value(legs, spot, T, vix)
        # net premium magnitude (credit for short, debit for long)
        entry_credit = abs(entry_value)
        expiry = exp
        open_cost = _open_costs(legs)
        capital -= open_cost
        equity_curve.append(capital)

    # close any dangling position at last mark
    if legs is not None:
        spot = float(daily["spot"].iloc[-1]); vix = float(daily["vix"].iloc[-1])
        T = days_to(dates[-1].date() if isinstance(dates[-1], (pd.Timestamp, datetime)) else dates[-1], expiry)
        open_pnl = _legs_value(legs, spot, T, vix) - entry_value
        capital += open_pnl - _close_costs(legs, spot, max(T, 0.0), vix)
        trades.append({"exit": "eod", "reason": "force_close",
                       "pnl": open_pnl, "credit": entry_credit})
        equity_curve.append(capital)

    # ---- metrics ----
    eq = np.array(equity_curve, dtype=float)
    rets = np.diff(eq) / initial_capital
    sharpe = 0.0
    if len(rets) > 2 and np.std(rets, ddof=1) > 0:
        sharpe = float(np.mean(rets) / np.std(rets, ddof=1) * np.sqrt(252))
    peak = np.maximum.accumulate(eq)
    max_dd = float(np.max(peak - eq)) if len(eq) else 0.0
    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]

    return {
        "total_pnl":     round(float(capital - initial_capital), 2),
        "num_trades":    len(trades),
        "win_rate":      round(len(wins) / len(trades), 4) if trades else 0.0,
        "sharpe":        round(sharpe, 4),
        "max_drawdown":  round(max_dd, 2),
        "final_capital": round(float(capital), 2),
        "avg_trade":     round(float(np.mean(pnls)), 2) if pnls else 0.0,
        "best_trade":    round(float(np.max(pnls)), 2) if pnls else 0.0,
        "worst_trade":   round(float(np.min(pnls)), 2) if pnls else 0.0,
        "equity_curve":  [round(x, 2) for x in equity_curve],
        "trades":        trades,
    }


# ── Walk-forward + locked-holdout validation ───────────────────────────────
def _default_opt_grid() -> List[Dict[str, Any]]:
    import itertools
    grid = {
        "width_pts": [None, 200, 300, 400],
        "sl_pct":    [0.5, 1.0, 2.0],
        "tp_pct":    [0.5, 0.6, 0.8],
    }
    keys = list(grid)
    return [dict(zip(keys, c)) for c in itertools.product(*[grid[k] for k in keys])]


def validate_options(
    strategy: str,
    daily: pd.DataFrame,
    grid: Optional[List[Dict[str, Any]]] = None,
    train_days: int = 120,          # trading-day rows
    test_days:  int = 45,
    holdout_ratio: float = 0.20,
    min_trades: int = 15,
    initial_capital: float = 500_000.0,
    lots: int = 1,
) -> Dict[str, Any]:
    """
    Honest OOS check: lock the most-recent `holdout_ratio` of data, walk-forward
    grid-search on the rest (optimise on train slice, score the NEXT OOS slice),
    deflate the Sharpe for the number of trials, check parameter stability, and
    only if the dev checks pass evaluate the locked holdout. Mirrors
    validation_harness's logic for the daily/weekly options structure.
    """
    from validation_harness import deflated_sharpe_ratio, parameter_stability

    grid = grid or _default_opt_grid()
    n = len(daily)
    cut = int(n * (1 - holdout_ratio))
    dev, holdout = daily.iloc[:cut], daily.iloc[cut:]

    def score(params, df):
        r = backtest_options(df, strategy=strategy, initial_capital=initial_capital,
                             lots=lots, verbose=False, **params)
        return r

    # walk-forward over dev
    window_results: List[Dict[str, Any]] = []
    params_per_win: List[Dict[str, Any]] = []
    n_trials = 0
    off = 0
    while off + train_days + test_days <= len(dev):
        tr = dev.iloc[off: off + train_days]
        te = dev.iloc[off + train_days: off + train_days + test_days]
        off += test_days
        best, best_pnl = None, -1e18
        for p in grid:
            n_trials += 1
            rr = score(p, tr)
            if rr["total_pnl"] > best_pnl:
                best_pnl, best = rr["total_pnl"], p
        if best is None:
            continue
        oos = score(best, te)
        window_results.append(oos)
        params_per_win.append(best)

    if not window_results:
        return {"strategy": strategy, "verdict": "INSUFFICIENT_DATA",
                "dev_windows": 0}

    sharpes = [w["sharpe"] for w in window_results]
    pnls    = [w["total_pnl"] for w in window_results]
    trades  = sum(w["num_trades"] for w in window_results)
    dev_avg_sharpe = float(np.mean(sharpes))
    dev_pct_prof   = float(np.mean([1 if p > 0 else 0 for p in pnls]))
    dsr   = deflated_sharpe_ratio(sr=dev_avg_sharpe, n_trades=trades, n_trials=n_trials)
    stab  = parameter_stability(params_per_win)
    dsr_ok, stab_ok = dsr >= 0.95, stab < 0.5
    min_ok = trades >= min_trades

    # locked holdout — only if dev looks real
    h = None
    if dev_avg_sharpe > 0 and min_ok and stab_ok and dsr > 0.5 and len(holdout) >= 20:
        from collections import Counter
        import json as _j
        frozen = [_j.dumps(p, sort_keys=True) for p in params_per_win]
        best_overall = _j.loads(Counter(frozen).most_common(1)[0][0])
        h = score(best_overall, holdout)

    verdict = "PASS" if (dsr_ok and min_ok and stab_ok and dev_avg_sharpe > 0
                         and h is not None and h["total_pnl"] > 0) else "FAIL"
    return {
        "strategy": strategy, "verdict": verdict,
        "dev_windows": len(window_results), "n_trials": n_trials,
        "dev_avg_sharpe": round(dev_avg_sharpe, 3),
        "dev_pct_profitable": round(dev_pct_prof, 3),
        "dev_total_trades": trades,
        "deflated_sharpe": round(dsr, 3), "min_trade_ok": min_ok,
        "param_stability_cv": round(stab, 3), "stability_ok": stab_ok,
        "holdout_pnl": (round(h["total_pnl"], 2) if h else None),
        "holdout_sharpe": (h["sharpe"] if h else None),
        "holdout_trades": (h["num_trades"] if h else None),
        "holdout_win_rate": (h["win_rate"] if h else None),
    }


# ── Tail / vol-spike stress test ───────────────────────────────────────────
def stress_test(
    daily: pd.DataFrame,
    width_pts: Optional[int] = None,
    lots: int = 1,
    T_entry_days: int = 5,
    initial_capital: float = 500_000.0,
) -> Dict[str, Any]:
    """
    The decisive test for premium SELLERS: what does one adverse overnight GAP +
    VIX spike do to an open position? Crucially, a gap means a naked short can NOT
    fill its stop at the stop level — it exits at the gapped price. An iron condor
    caps the loss at its wing width. We price both under a scenario grid and
    compare the loss to the strategy's own average weekly income.
    """
    # representative entry from the most recent regime
    spot0 = float(daily["spot"].iloc[-1])
    vix0  = float(daily["vix"].iloc[-1])
    T0    = T_entry_days / 365.0

    # average weekly income (from the plain backtest) for context
    base_ss = backtest_options(daily, "short_strangle", initial_capital, lots, width_pts)
    base_ic = backtest_options(daily, "iron_condor",    initial_capital, lots, width_pts)
    inc_ss = base_ss["avg_trade"]
    inc_ic = base_ic["avg_trade"]

    legs_ss = _build_legs("short_strangle", spot0, T0, vix0, width_pts, lots)
    legs_ic = _build_legs("iron_condor",    spot0, T0, vix0, width_pts, lots)
    ev_ss = _legs_value(legs_ss, spot0, T0, vix0)
    ev_ic = _legs_value(legs_ic, spot0, T0, vix0)
    credit_ss = abs(ev_ss)
    credit_ic = abs(ev_ic)

    # iron-condor structural max loss (gap beyond wings): wing width × lot − credit
    wing = (legs_ic[2].strike - legs_ic[0].strike)  # long_call - short_call
    ic_max_loss = wing * LOT_SIZE * lots - credit_ic

    gaps = [-0.07, -0.05, -0.03, -0.02, 0.02, 0.03, 0.05, 0.07]
    vix_mults = [1.5, 2.5]
    rows = []
    T1 = max(T0 - 1 / 365.0, 1e-6)
    for g in gaps:
        for vm in vix_mults:
            ns = spot0 * (1 + g)
            nv = vix0 * vm
            pnl_ss = _legs_value(legs_ss, ns, T1, nv) - ev_ss
            pnl_ic = _legs_value(legs_ic, ns, T1, nv) - ev_ic
            pnl_ic = max(pnl_ic, -ic_max_loss)  # wings cap the loss
            rows.append({"gap": g, "vix_mult": vm,
                         "strangle_pnl": round(pnl_ss, 0),
                         "condor_pnl": round(pnl_ic, 0)})
    return {
        "spot0": spot0, "vix0": vix0,
        "credit_strangle": round(credit_ss, 0), "credit_condor": round(credit_ic, 0),
        "avg_weekly_income_strangle": round(inc_ss, 0),
        "avg_weekly_income_condor": round(inc_ic, 0),
        "condor_max_loss": round(ic_max_loss, 0),
        "scenarios": rows,
    }


# ── Data loading ───────────────────────────────────────────────────────────
def load_daily(symbol: str = "NIFTY", days: int = 365) -> Optional[pd.DataFrame]:
    """Fetch daily underlying + India VIX, aligned on date."""
    import os
    from angel import AngelOne
    ang = AngelOne(api_key=os.getenv("API_KEY", ""), client_id=os.getenv("CLIENT_ID", ""),
                   password=os.getenv("PASSWORD", ""), totp_secret=os.getenv("TOTP_SECRET", ""))
    frm = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
    to  = datetime.now().strftime("%Y-%m-%d %H:%M")
    spot_df = ang.get_historical_data(symbol, interval="ONE_DAY",
                                      from_date=frm, to_date=to, exchange="NSE")
    vix_df  = ang.get_historical_data("India VIX", interval="ONE_DAY",
                                      from_date=frm, to_date=to, exchange="NSE")
    if spot_df is None or vix_df is None or spot_df.empty or vix_df.empty:
        return None
    s = spot_df["close"].copy(); s.index = pd.to_datetime(s.index).normalize()
    v = vix_df["close"].copy();  v.index = pd.to_datetime(v.index).normalize()
    cols = {"spot": s, "vix": v}
    # Carry the daily OPEN too (used by the 0DTE hero-zero backtest for a
    # faithful same-day entry); fall back to close if unavailable.
    if "open" in spot_df.columns:
        o = spot_df["open"].copy(); o.index = pd.to_datetime(o.index).normalize()
        cols["open"] = o
    out = pd.DataFrame(cols).dropna()
    return out


# ── Hero-zero (0DTE deep-OTM BUYING) backtest ───────────────────────────────
def backtest_hero_zero(
    daily: pd.DataFrame,
    otm_pct: float = 1.0,
    lots: int = 1,
    initial_capital: float = 500_000.0,
    expiry_weekday: int = EXPIRY_WEEKDAY,
    hours_to_expiry: float = 5.5,
    mode: str = "both",     # "both" = CE+PE deep OTM; "directional" = gap side
) -> Dict[str, Any]:
    """0DTE deep-OTM 'hero or zero' option BUYING, at daily resolution.

    On each expiry day: BUY deep-OTM option(s) at the day's OPEN, hold to the
    day's CLOSE (= expiry → intrinsic value). Measures the lottery payoff
    distribution — most expiries die worthless (-premium), a few pay big.

    CAVEATS (every one makes BUYING look BETTER than reality, so a loss here is
    a strong negative result): BS with a single daily VIX understates deep-OTM
    0DTE premiums (no vol skew, no intraday surface); no real bid/ask beyond a
    1% slippage proxy; entry at the daily open rather than the 9:15 print; and
    'directional' uses the open-vs-prev-close gap as the side rule.
    """
    if daily is None or len(daily) < 2:
        return {}
    import numpy as np
    has_open = "open" in daily.columns
    T_entry  = max(hours_to_expiry, 0.1) / (6.5 * 252)   # trading-time fraction

    dates = list(daily.index)
    trades: List[Dict[str, Any]] = []
    prev_close: Optional[float] = None
    for dt in dates:
        row   = daily.loc[dt]
        close = float(row["spot"]); vix = float(row["vix"])
        d     = dt.date() if hasattr(dt, "date") else dt
        if d.weekday() == expiry_weekday and prev_close is not None:
            entry_spot = float(row["open"]) if (has_open and float(row.get("open", 0) or 0) > 0) else prev_close
            legs: List = []
            if mode == "directional":
                if entry_spot >= prev_close:
                    legs = [("call", _round_strike(entry_spot * (1 + otm_pct / 100)))]
                else:
                    legs = [("put",  _round_strike(entry_spot * (1 - otm_pct / 100)))]
            else:  # both sides
                legs = [("call", _round_strike(entry_spot * (1 + otm_pct / 100))),
                        ("put",  _round_strike(entry_spot * (1 - otm_pct / 100)))]
            pnl = 0.0
            for ot, k in legs:
                ep = leg_price(entry_spot, k, T_entry, vix, ot)        # entry premium
                ex = leg_price(close, k, 0.0, vix, ot)                 # intrinsic at expiry
                open_cost = _entry_cost(ep, "buy") * lots
                close_cost = 0.0
                if ex > 0:   # exited with value → sell (slippage + STT)
                    close_cost = (BROKERAGE_LEG + ex * SLIPPAGE_PCT * LOT_SIZE
                                  + ex * STT_RATE_PREMIUM * LOT_SIZE) * lots
                pnl += (ex - ep) * LOT_SIZE * lots - open_cost - close_cost
            trades.append({"date": str(d), "pnl": round(pnl, 0)})
        prev_close = close

    if not trades:
        return {}
    arr = np.array([t["pnl"] for t in trades], dtype=float)
    n   = len(arr)
    total = float(arr.sum())
    gross_win  = float(arr[arr > 0].sum())
    gross_loss = float(-arr[arr < 0].sum())
    sharpe = float(arr.mean() / arr.std() * np.sqrt(min(n, 52))) if arr.std() > 0 else 0.0
    return {
        "strategy":      "hero_zero_0dte",
        "mode":          mode,
        "otm_pct":       otm_pct,
        "n_expiries":    n,
        "wins":          int((arr > 0).sum()),
        "win_rate_pct":  round(float((arr > 0).mean()) * 100, 1),
        "total_pnl":     round(total, 0),
        "avg_pnl":       round(float(arr.mean()), 0),
        "median_pnl":    round(float(np.median(arr)), 0),
        "max_win":       round(float(arr.max()), 0),
        "max_loss":      round(float(arr.min()), 0),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "pct_worthless": round(float((arr <= 0).mean()) * 100, 1),
        "pseudo_sharpe": round(sharpe, 2),
        "return_on_capital_pct": round(total / initial_capital * 100, 1),
        "verdict": "POSITIVE (suspect — premiums understated, see caveats)"
                   if total > 0 else "NEGATIVE — bleeds (option-buying edge confirmed negative)",
    }


# ── CLI ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    p = argparse.ArgumentParser(description="Black-Scholes options backtester (phase 1)")
    p.add_argument("--strategy", default="short_strangle",
                   choices=["short_strangle", "long_straddle", "iron_condor",
                            "hero_zero"])
    p.add_argument("--symbol", default="NIFTY")
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--lots", type=int, default=1)
    p.add_argument("--otm", type=float, default=1.0,
                   help="hero_zero: %% OTM from spot for the deep-OTM strike(s)")
    p.add_argument("--hours", type=float, default=5.5,
                   help="hero_zero: hours to expiry at entry (default morning)")
    p.add_argument("--hz-mode", default="both", choices=["both", "directional"],
                   help="hero_zero: buy CE+PE ('both') or gap-momentum side")
    p.add_argument("--width", type=int, default=None, help="OTM width in points (default ~1σ)")
    p.add_argument("--sl", type=float, default=1.0, help="stop at sl*credit (short)")
    p.add_argument("--tp", type=float, default=0.6, help="take profit at tp*credit (short)")
    p.add_argument("--capital", type=float, default=500_000.0)
    p.add_argument("--validate", action="store_true",
                   help="run walk-forward + locked-holdout OOS validation")
    p.add_argument("--stress", action="store_true",
                   help="tail/vol-spike stress test (sellers): gap + VIX shock")
    args = p.parse_args()

    daily = load_daily(args.symbol, args.days)
    if daily is None or len(daily) < 20:
        logger.error("Insufficient daily data")
        raise SystemExit(1)
    logger.info("Loaded %d daily bars %s..%s  spot %.0f→%.0f  vix %.1f→%.1f",
                len(daily), daily.index.min().date(), daily.index.max().date(),
                daily["spot"].iloc[0], daily["spot"].iloc[-1],
                daily["vix"].iloc[0], daily["vix"].iloc[-1])

    if args.strategy == "hero_zero":
        import json as _json
        res = {}
        for _mode in ("both", "directional"):
            r = backtest_hero_zero(daily, otm_pct=args.otm, lots=args.lots,
                                   initial_capital=args.capital,
                                   hours_to_expiry=args.hours, mode=_mode)
            res[_mode] = r
        print("\n" + "=" * 70)
        print(f"HERO-ZERO 0DTE DEEP-OTM BUYING — {args.symbol}  (OTM {args.otm}% , "
              f"{args.hours}h to expiry)")
        print("=" * 70)
        for _mode, r in res.items():
            if not r:
                print(f"  {_mode}: no expiries in window"); continue
            print(f"\n  [{_mode.upper()}]  {r['verdict']}")
            print(f"   expiries={r['n_expiries']}  win_rate={r['win_rate_pct']}%  "
                  f"worthless={r['pct_worthless']}%")
            print(f"   total ₹{r['total_pnl']:,.0f}  avg ₹{r['avg_pnl']:,.0f}  "
                  f"median ₹{r['median_pnl']:,.0f}")
            print(f"   max_win ₹{r['max_win']:,.0f}  max_loss ₹{r['max_loss']:,.0f}  "
                  f"PF={r['profit_factor']}  ROC={r['return_on_capital_pct']}%")
        out = {"run_date": str(date.today()), "symbol": args.symbol,
               "days": args.days, "otm_pct": args.otm, "hours_to_expiry": args.hours,
               "results": res,
               "caveats": "BS understates deep-OTM 0DTE premiums (no skew/intraday "
                          "surface); daily resolution; entry at daily open. All bias "
                          "TOWARD buying, so a loss is a strong negative result."}
        with open("hero_zero_backtest.json", "w") as fh:
            _json.dump(out, fh, indent=2, default=str)
        print("\n  Saved → hero_zero_backtest.json")
        raise SystemExit(0)

    if args.stress:
        s = stress_test(daily, width_pts=args.width, lots=args.lots, initial_capital=args.capital)
        print("\n" + "=" * 70)
        print(f"TAIL / VOL-SPIKE STRESS TEST — {args.symbol}  (1 open position)")
        print("=" * 70)
        print(f"  Entry spot {s['spot0']:.0f}  VIX {s['vix0']:.1f}")
        print(f"  Credit collected: strangle ₹{s['credit_strangle']:,.0f} | "
              f"condor ₹{s['credit_condor']:,.0f}")
        print(f"  Avg WEEKLY income: strangle ₹{s['avg_weekly_income_strangle']:,.0f} | "
              f"condor ₹{s['avg_weekly_income_condor']:,.0f}")
        print(f"  Condor structural MAX loss (gap beyond wings): ₹{s['condor_max_loss']:,.0f}")
        print(f"\n  {'gap':>5} {'vixx':>5} | {'STRANGLE P&L':>14} {'(weeks)':>9} | "
              f"{'CONDOR P&L':>12} {'(weeks)':>9}")
        print("  " + "-" * 66)
        wi_s = s['avg_weekly_income_strangle'] or 1
        wi_c = s['avg_weekly_income_condor'] or 1
        for r in s["scenarios"]:
            wks_s = r['strangle_pnl'] / wi_s if wi_s else 0
            wks_c = r['condor_pnl'] / wi_c if wi_c else 0
            print(f"  {r['gap']*100:>4.0f}% {r['vix_mult']:>4.1f}x | "
                  f"₹{r['strangle_pnl']:>12,.0f} {wks_s:>8.0f} | "
                  f"₹{r['condor_pnl']:>10,.0f} {wks_c:>8.0f}")
        print("\n  NOTE: on an overnight GAP a naked strangle CANNOT exit at its stop —"
              "\n  it fills at the gapped price, so these strangle losses are realistic"
              "\n  worst-cases. The condor's loss is capped by its long wings.")
        raise SystemExit(0)

    if args.validate:
        vr = validate_options(args.strategy, daily, initial_capital=args.capital, lots=args.lots)
        print("\n" + "=" * 64)
        print(f"OPTIONS OOS VALIDATION — {args.strategy.upper()} / {args.symbol}")
        print("=" * 64)
        for k in ("dev_windows", "n_trials", "dev_avg_sharpe", "dev_pct_profitable",
                  "dev_total_trades", "deflated_sharpe", "min_trade_ok",
                  "param_stability_cv", "stability_ok", "holdout_pnl",
                  "holdout_sharpe", "holdout_trades", "holdout_win_rate"):
            print(f"  {k:20s}: {vr.get(k)}")
        print(f"\n  VERDICT: {vr['verdict']}")
        raise SystemExit(0)

    r = backtest_options(daily, strategy=args.strategy, initial_capital=args.capital,
                         lots=args.lots, width_pts=args.width, sl_pct=args.sl, tp_pct=args.tp)

    print("\n" + "=" * 64)
    print(f"OPTIONS BACKTEST (BS-priced) — {args.strategy.upper()} / {args.symbol}")
    print("=" * 64)
    print(f"  Trades        : {r['num_trades']}")
    print(f"  Win rate      : {r['win_rate']*100:.1f}%")
    print(f"  Total P&L     : ₹{r['total_pnl']:+,.0f}  on ₹{args.capital:,.0f}")
    print(f"  Avg / Best / Worst trade: ₹{r['avg_trade']:+,.0f} / ₹{r['best_trade']:+,.0f} / ₹{r['worst_trade']:+,.0f}")
    print(f"  Sharpe (daily): {r['sharpe']:.3f}")
    print(f"  Max drawdown  : ₹{r['max_drawdown']:,.0f}")
    print(f"  Final capital : ₹{r['final_capital']:,.0f}")
