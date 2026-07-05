from __future__ import annotations


def _get_pnl(trade: dict) -> float:
    """Extract P&L from trade dict — handles all column name variants."""
    for key in ("pnl", "pnl", "net_pnl", "gross_pnl", "profit_loss"):
        val = trade.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass
    return 0.0

"""
performance_analytics.py — Institutional-Grade Performance Engine

Inspired by:
  - "Advances in Financial Machine Learning" — Marcos Lopez de Prado
  - "Active Portfolio Management" — Grinold & Kahn
  - Renaissance Technologies risk framework
  - Two Sigma signal-to-noise methodology
  - Quantopian/Zipline performance attribution

Computes:
  - Sharpe, Sortino, Calmar, Omega ratios
  - Maximum drawdown + recovery time
  - Strategy attribution (P&L by strategy/symbol/sector)
  - Rolling win rate + expectancy
  - Information ratio vs NIFTY benchmark
  - Trade quality score (0-100)
"""
import logging, sqlite3
from datetime import timedelta, date
from pathlib import Path
from typing import Dict, List, Tuple
import math

logger = logging.getLogger(__name__)

_DB = Path("trades.db")
_RISK_FREE_RATE = 0.065  # India 10Y Gsec ~6.5%


def _net_pnl(trade: dict) -> float:
    """Canonical net P&L from the current trades schema."""
    for key in ("realized_pnl", "net_pnl", "pnl", "gross_pnl"):
        if trade.get(key) is not None:
            try:
                return float(trade[key])
            except (TypeError, ValueError):
                continue
    return 0.0


def _load_trades(days: int = 90) -> List[dict]:
    """Load closed trades from SQLite."""
    if not _DB.exists():
        return []
    try:
        conn = sqlite3.connect(str(_DB))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT * FROM trades WHERE status='CLOSED'
                 AND exit_time >= strftime('%s','now',?) ORDER BY exit_time""",
            (f"-{max(1, int(days))} days",)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.debug("load_trades: %s", e)
        return []


def _daily_returns(trades: List[dict]) -> Dict[str, float]:
    """Aggregate trade P&L into daily returns."""
    daily = {}
    for t in trades:
        try:
            day = __import__("datetime").datetime.fromtimestamp(float(t.get("exit_time") or 0)).date().isoformat()
            pnl = _net_pnl(t)
            capital = float(t.get("capital_used") or t.get("entry_value") or t.get("capital") or 26964 or 26964)
            ret = pnl / capital if capital > 0 else 0
            daily[day] = daily.get(day, 0) + ret
        except Exception:
            pass
    return dict(sorted(daily.items()))


def sharpe_ratio(returns: List[float], periods_per_year: int = 252) -> float:
    """Annualised Sharpe ratio."""
    if len(returns) < 5:
        return 0.0
    n = len(returns)
    mean = sum(returns) / n
    variance = sum((r - mean) ** 2 for r in returns) / max(n - 1, 1)
    std = math.sqrt(variance)
    if std == 0:
        return 0.0
    daily_rf = _RISK_FREE_RATE / periods_per_year
    return round((mean - daily_rf) / std * math.sqrt(periods_per_year), 3)


def sortino_ratio(returns: List[float], periods_per_year: int = 252) -> float:
    """Sortino ratio — penalises only downside volatility."""
    if len(returns) < 5:
        return 0.0
    mean = sum(returns) / len(returns)
    daily_rf = _RISK_FREE_RATE / periods_per_year
    downside = [r for r in returns if r < daily_rf]
    if not downside:
        return 10.0  # no losing days
    down_var = sum((r - daily_rf) ** 2 for r in downside) / len(downside)
    down_std = math.sqrt(down_var)
    if down_std == 0:
        return 10.0
    return round((mean - daily_rf) / down_std * math.sqrt(periods_per_year), 3)


def max_drawdown(returns: List[float]) -> Tuple[float, int]:
    """Returns (max_drawdown_pct, recovery_days)."""
    if not returns:
        return 0.0, 0
    equity = [1.0]
    for r in returns:
        equity.append(equity[-1] * (1 + r))
    peak = equity[0]
    max_dd = 0.0
    dd_start = 0
    max_recovery = 0
    for i, e in enumerate(equity):
        if e > peak:
            peak = e
            dd_start = i
        dd = (peak - e) / peak
        if dd > max_dd:
            max_dd = dd
            # Estimate recovery days
            future = equity[i:]
            recovery = next((j for j, v in enumerate(future) if v >= peak), len(future))
            max_recovery = recovery
    return round(max_dd * 100, 2), max_recovery


def calmar_ratio(returns: List[float], periods_per_year: int = 252) -> float:
    """Calmar = annualised return / max drawdown."""
    if len(returns) < 5:
        return 0.0
    annual_ret = (sum(returns) / len(returns)) * periods_per_year
    mdd, _ = max_drawdown(returns)
    if mdd == 0:
        return 10.0
    return round(annual_ret / (mdd / 100), 3)


def k_ratio(returns: List[float]) -> float:
    """
    K-ratio — measures equity curve slope consistency (Kestner 1996).
    Formula: slope of OLS fit on log-cumulative returns / (std of residuals * sqrt(n)).
    Interpretation: >1.0 = consistent growth; <0.5 = lumpy/lucky equity curve.
    QuantConnect and NinjaTrader use this to distinguish skill from noise.
    """
    if len(returns) < 10:
        return 0.0
    try:
        cum = 0.0
        log_eq = []
        for r in returns:
            cum += r
            log_eq.append(cum)  # cumulative return (approximate log-equity)
        n = len(log_eq)
        xs = list(range(1, n + 1))
        mean_x = sum(xs) / n
        mean_y = sum(log_eq) / n
        ss_xy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, log_eq))
        ss_xx = sum((x - mean_x) ** 2 for x in xs)
        if ss_xx == 0:
            return 0.0
        slope = ss_xy / ss_xx
        intercept = mean_y - slope * mean_x
        residuals = [(log_eq[i] - (slope * xs[i] + intercept)) for i in range(n)]
        res_std = math.sqrt(sum(r * r for r in residuals) / max(n - 2, 1))
        if res_std == 0:
            return 10.0
        return round(slope / res_std * math.sqrt(n), 3)
    except Exception:
        return 0.0


def recovery_factor(returns: List[float]) -> float:
    """
    Recovery Factor = net total return / max drawdown.
    TradeStation/NinjaTrader standard: >3 is good, >5 is excellent.
    Shows how many times net profits have 'covered' the worst drawdown.
    """
    if len(returns) < 5:
        return 0.0
    net_ret = sum(returns)
    mdd, _ = max_drawdown(returns)
    if mdd <= 0:
        return 10.0 if net_ret > 0 else 0.0
    return round(net_ret / (mdd / 100), 3)


def omega_ratio(returns: List[float], threshold: float = 0.0) -> float:
    """Omega ratio — gains/losses above/below threshold."""
    gains = sum(r - threshold for r in returns if r > threshold)
    losses = sum(threshold - r for r in returns if r <= threshold)
    if losses == 0:
        return 10.0
    return round(gains / losses, 3)


def win_rate_expectancy(trades: List[dict]) -> dict:
    """Win rate, avg win, avg loss, expectancy."""
    if not trades:
        return {"win_rate": 0, "avg_win": 0, "avg_loss": 0, "expectancy": 0, "total": 0}
    pnls = [_net_pnl(t) for t in trades]
    wins  = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    total = len(pnls)
    win_rate = len(wins) / total * 100 if total else 0
    avg_win  = sum(wins) / len(wins) if wins else 0
    avg_loss = sum(losses) / len(losses) if losses else 0
    expectancy = (win_rate/100 * avg_win) + ((1 - win_rate/100) * avg_loss)
    return {
        "win_rate":    round(win_rate, 1),
        "avg_win":     round(avg_win, 2),
        "avg_loss":    round(avg_loss, 2),
        "expectancy":  round(expectancy, 2),
        "total_trades": total,
        "winners":     len(wins),
        "losers":      len(losses),
    }


def strategy_attribution(trades: List[dict]) -> List[dict]:
    """P&L breakdown by strategy."""
    by_strat = {}
    for t in trades:
        strat = str(t.get("strategy") or t.get("strategy_name") or "unknown" or "unknown")
        pnl = _net_pnl(t)
        if strat not in by_strat:
            by_strat[strat] = {"pnl": 0, "trades": 0, "wins": 0}
        by_strat[strat]["pnl"] += pnl
        by_strat[strat]["trades"] += 1
        if pnl > 0:
            by_strat[strat]["wins"] += 1

    result = []
    for strat, data in by_strat.items():
        wr = data["wins"] / data["trades"] * 100 if data["trades"] else 0
        result.append({
            "strategy":   strat,
            "pnl":        round(data["pnl"], 2),
            "trades":     data["trades"],
            "win_rate":   round(wr, 1),
        })
    return sorted(result, key=lambda x: x["pnl"], reverse=True)


def symbol_attribution(trades: List[dict]) -> List[dict]:
    """P&L breakdown by symbol."""
    by_sym = {}
    for t in trades:
        sym = str(t.get("symbol", "?") or "?")
        pnl = _net_pnl(t)
        if sym not in by_sym:
            by_sym[sym] = {"pnl": 0, "trades": 0, "wins": 0}
        by_sym[sym]["pnl"] += pnl
        by_sym[sym]["trades"] += 1
        if pnl > 0:
            by_sym[sym]["wins"] += 1

    result = []
    for sym, data in by_sym.items():
        wr = data["wins"] / data["trades"] * 100 if data["trades"] else 0
        result.append({
            "symbol":   sym,
            "pnl":      round(data["pnl"], 2),
            "trades":   data["trades"],
            "win_rate": round(wr, 1),
        })
    return sorted(result, key=lambda x: x["pnl"], reverse=True)


def trade_quality_score(trades: List[dict]) -> float:
    """
    Composite trade quality 0-100.
    Inspired by Renaissance Technologies' quality-first approach.
    Components: win rate, expectancy, Sharpe, drawdown.
    """
    if len(trades) < 5:
        return 0.0
    we = win_rate_expectancy(trades)
    daily_ret = list(_daily_returns(trades).values())
    sh = sharpe_ratio(daily_ret)
    mdd, _ = max_drawdown(daily_ret)

    # Score components (each 0-25)
    wr_score = min(25, we["win_rate"] / 2)          # 50% WR = 25 pts
    exp_score = min(25, max(0, we["expectancy"] / 20 * 25))  # ₹500 exp = 25 pts
    sh_score  = min(25, max(0, sh / 3 * 25))        # Sharpe 3 = 25 pts
    dd_score  = min(25, max(0, (20 - mdd) / 20 * 25))  # 0% DD = 25 pts

    return round(wr_score + exp_score + sh_score + dd_score, 1)


def get_full_report(days: int = 30) -> dict:
    """Full institutional performance report."""
    trades = _load_trades(days)
    daily  = _daily_returns(trades)
    rets   = list(daily.values())

    total_pnl = sum(_net_pnl(t) for t in trades)
    we = win_rate_expectancy(trades)
    mdd, rec = max_drawdown(rets)

    return {
        "period_days":    days,
        "total_trades":   len(trades),
        "total_pnl":      round(total_pnl, 2),
        "win_rate":       we["win_rate"],
        "avg_win":        we["avg_win"],
        "avg_loss":       we["avg_loss"],
        "expectancy":     we["expectancy"],
        "sharpe":         sharpe_ratio(rets),
        "sortino":        sortino_ratio(rets),
        "calmar":         calmar_ratio(rets),
        "omega":          omega_ratio(rets),
        "max_drawdown_pct": mdd,
        "recovery_days":  rec,
        "quality_score":  trade_quality_score(trades),
        "k_ratio":        k_ratio(rets),
        "recovery_factor": recovery_factor(rets),
        "by_strategy":    strategy_attribution(trades)[:5],
        "by_symbol":      symbol_attribution(trades)[:5],
    }


def format_telegram_report(days: int = 30) -> str:
    """Institutional P&L report for /pnl command."""
    r = get_full_report(days)
    from datetime import datetime as _dt

    if r["total_trades"] == 0:
        return (
            f"📊 <b>PERFORMANCE REPORT</b> | Last {days} days\n\n"
            "  ℹ️  No closed trades yet\n"
            "  Bot starts trading at 9:15 AM on market days\n"
            "  First signal expected within 20 min of market open"
        )

    pnl_icon = "🟢" if r["total_pnl"] >= 0 else "🔴"
    q = r["quality_score"]
    q_label = "EXCELLENT" if q >= 75 else "GOOD" if q >= 55 else "AVERAGE" if q >= 35 else "BUILDING"
    q_icon  = "🏆" if q >= 75 else "✅" if q >= 55 else "⚡" if q >= 35 else "🔨"

    kr  = r.get("k_ratio", 0.0)
    rf  = r.get("recovery_factor", 0.0)
    kr_lbl  = "consistent" if kr >= 1.0 else ("building" if kr >= 0.5 else "lumpy")
    rf_lbl  = "excellent" if rf >= 5 else ("good" if rf >= 3 else ("fair" if rf >= 1 else "low"))
    lines = [
        f"📊 <b>PERFORMANCE REPORT</b> | Last {days}d",
        f"  {_dt.now().strftime('%d-%b %H:%M')}",
        "",
        f"  {pnl_icon} Total P&L:    ₹{r['total_pnl']:>10,.2f}",
        f"  📈 Total Trades:  {r['total_trades']:>6}",
        f"  🎯 Win Rate:      {r['win_rate']:>5.1f}%",
        f"  💰 Avg Win:      ₹{r['avg_win']:>9,.2f}",
        f"  📉 Avg Loss:     ₹{r['avg_loss']:>9,.2f}",
        f"  ⚡ Expectancy:   ₹{r['expectancy']:>9,.2f}",
        "",
        "  <b>RISK METRICS</b>",
        f"  📐 Sharpe Ratio:  {r['sharpe']:>6.2f}",
        f"  📐 Sortino Ratio: {r['sortino']:>6.2f}",
        f"  📐 Calmar Ratio:  {r['calmar']:>6.2f}",
        f"  📐 K-Ratio:       {kr:>6.2f}  ({kr_lbl})",
        f"  📐 Recovery Fct:  {rf:>6.2f}  ({rf_lbl})",
        f"  📉 Max Drawdown:  {r['max_drawdown_pct']:>5.1f}%",
        f"  {q_icon} Quality Score: {q:>5.1f}/100 → {q_label}",
        "",
    ]

    if r["by_strategy"]:
        lines.append("  <b>TOP STRATEGIES</b>")
        for s in r["by_strategy"][:3]:
            icon = "🟢" if s["pnl"] >= 0 else "🔴"
            lines.append(f"  {icon} {s['strategy'][:18]:18} ₹{s['pnl']:>8,.0f} ({s['win_rate']:.0f}%)")

    if r["by_symbol"]:
        lines.append("")
        lines.append("  <b>TOP SYMBOLS</b>")
        for s in r["by_symbol"][:3]:
            icon = "🟢" if s["pnl"] >= 0 else "🔴"
            lines.append(f"  {icon} {s['symbol'][:12]:12} ₹{s['pnl']:>8,.0f} ({s['win_rate']:.0f}%)")

    # Benchmark note
    lines += [
        "",
        f"  📌 Benchmark: NIFTY 50 (passive)",
        f"  Sharpe > 1.5 = institutional grade",
        f"  Sharpe > 2.0 = top decile globally",
    ]

    return "\n".join(lines)


def get_win_rate_by_hour(db_path: str = "trades.db") -> dict:
    """
    GAP 6: Win rate by time-of-day.
    Reveals which hours are profitable for each strategy.
    """
    import sqlite3
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT strftime('%H', entry_time,'unixepoch','localtime') as hour, "
            "COUNT(*) as total, "
            "SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins, "
            "AVG(realized_pnl) as avg_pnl "
            "FROM trades WHERE status='CLOSED' AND realized_pnl IS NOT NULL "
            "GROUP BY hour ORDER BY hour"
        ).fetchall()
        conn.close()
        result = {}
        for hour, total, wins, avg_pnl in rows:
            wr = wins/total*100 if total else 0
            result[f"{hour}:00"] = {
                "total": total, "wins": wins,
                "win_rate": round(wr, 1),
                "avg_pnl": round(float(avg_pnl or 0), 2),
                "quality": "GOOD" if wr >= 55 else "OK" if wr >= 45 else "POOR"
            }
        return result
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug("win_by_hour: %s", e)
        return {}


def get_win_rate_by_regime(db_path: str = "trades.db") -> dict:
    """
    GAP 7: Win rate by market regime.
    Reveals if HMM regime detection is actually adding alpha.
    """
    import sqlite3
    try:
        conn = sqlite3.connect(db_path)
        # regime stored in signal_metadata JSON column
        rows = conn.execute(
            "SELECT signal_metadata, realized_pnl FROM trades "
            "WHERE status='CLOSED' AND realized_pnl IS NOT NULL "
            "AND signal_metadata IS NOT NULL"
        ).fetchall()
        conn.close()
        import json
        regime_stats: dict = {}
        for meta_str, pnl in rows:
            try:
                meta   = json.loads(meta_str) if isinstance(meta_str, str) else {}
                regime = str(meta.get("regime", "UNKNOWN")).upper()
                if regime not in regime_stats:
                    regime_stats[regime] = {"total": 0, "wins": 0, "pnl": 0.0}
                regime_stats[regime]["total"] += 1
                if float(pnl or 0) > 0:
                    regime_stats[regime]["wins"] += 1
                regime_stats[regime]["pnl"] += float(pnl or 0)
            except Exception:
                pass
        result = {}
        for regime, s in regime_stats.items():
            wr = s["wins"]/s["total"]*100 if s["total"] else 0
            result[regime] = {
                "total": s["total"], "wins": s["wins"],
                "win_rate": round(wr, 1),
                "total_pnl": round(s["pnl"], 2),
            }
        return result
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug("win_by_regime: %s", e)
        return {}


def format_analytics_report() -> str:
    """Full analytics including hour + regime breakdown."""
    from datetime import datetime
    lines = [f"📊 <b>TRADING ANALYTICS</b> | {datetime.now().strftime('%d-%b')}"]

    # Hour analysis
    by_hour = get_win_rate_by_hour()
    if by_hour:
        lines += ["", "  <b>WIN RATE BY HOUR</b>"]
        for hour, data in sorted(by_hour.items()):
            icon = "🟢" if data["quality"] == "GOOD" else "🔴" if data["quality"] == "POOR" else "⚪"
            lines.append(f"  {icon} {hour}  {data['win_rate']:.0f}%wr  "
                         f"₹{data['avg_pnl']:+.0f}avg  ({data['total']}t)")

    # Regime analysis
    by_regime = get_win_rate_by_regime()
    if by_regime:
        lines += ["", "  <b>WIN RATE BY REGIME</b>"]
        for regime, data in sorted(by_regime.items()):
            wr = data["win_rate"]
            icon = "🟢" if wr >= 55 else "🔴" if wr < 45 else "⚪"
            lines.append(f"  {icon} {regime:12}  {wr:.0f}%wr  "
                         f"₹{data['total_pnl']:+,.0f}  ({data['total']}t)")

    lines += ["", "  Use /pnl for full P&L breakdown"]
    return "\n".join(lines)
