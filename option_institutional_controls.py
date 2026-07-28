"""Quote reality, portfolio-risk, evidence, volatility and compliance controls."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import ipaddress
import math
import os
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple


@dataclass(frozen=True)
class OptionQuote:
    ts: float
    bid: float
    ask: float
    bid_qty: int = 0
    ask_qty: int = 0

    @property
    def valid(self) -> bool:
        return self.ts > 0 and self.bid > 0 and self.ask >= self.bid


@dataclass(frozen=True)
class SimulatedFill:
    filled: bool
    price: float = 0.0
    quantity: int = 0
    ts: float = 0.0
    reason: str = ""
    implementation_shortfall: float = 0.0


def smart_limit_schedule(
    *, side: str, bid: float, ask: float, attempts: int = 3,
    max_cross_fraction: float = 0.75, tick: float = 0.05,
) -> Tuple[float, ...]:
    """Deterministic mid-to-market schedule that never blindly crosses."""
    side = side.upper()
    if bid <= 0 or ask < bid or side not in {"BUY", "SELL"}:
        return ()
    count = max(1, int(attempts))
    midpoint = (bid + ask) / 2.0
    market = ask if side == "BUY" else bid
    direction = 1.0 if side == "BUY" else -1.0
    prices = []
    for index in range(count):
        fraction = max_cross_fraction * (index / max(count - 1, 1))
        raw = midpoint + direction * abs(market - midpoint) * fraction
        prices.append(round(round(raw / tick) * tick, 2))
    return tuple(dict.fromkeys(prices))


def simulate_limit_fill(
    *, side: str, quantity: int, limit_price: float, submitted_at: float,
    quotes: Sequence[OptionQuote], latency_sec: float = 0.0,
    max_quote_age_sec: float = 5.0,
) -> SimulatedFill:
    """Conservative quote replay: fills only from a later, fresh market quote."""
    side = side.upper()
    if side not in {"BUY", "SELL"} or quantity <= 0 or limit_price <= 0:
        return SimulatedFill(False, reason="invalid_order")
    eligible_at = submitted_at + max(0.0, latency_sec)
    for quote in sorted(quotes, key=lambda q: q.ts):
        if quote.ts < eligible_at or not quote.valid:
            continue
        if quote.ts - eligible_at > max_quote_age_sec:
            return SimulatedFill(False, reason="stale_quote")
        marketable = quote.ask <= limit_price if side == "BUY" else quote.bid >= limit_price
        if not marketable:
            continue
        available = quote.ask_qty if side == "BUY" else quote.bid_qty
        filled_qty = min(quantity, available) if available > 0 else quantity
        price = quote.ask if side == "BUY" else quote.bid
        decision_mid = (quote.bid + quote.ask) / 2.0
        shortfall = (price - decision_mid) * filled_qty * (1 if side == "BUY" else -1)
        return SimulatedFill(
            filled_qty > 0, round(price, 4), filled_qty, quote.ts,
            "filled" if filled_qty == quantity else "partial_fill",
            round(shortfall, 2),
        )
    return SimulatedFill(False, reason="not_marketable")


def combo_fillable(
    leg_fills: Sequence[SimulatedFill], *, max_timestamp_skew_sec: float = 1.0
) -> Tuple[bool, str]:
    if not leg_fills or any(not fill.filled for fill in leg_fills):
        return False, "one_or_more_legs_unfilled"
    times = [fill.ts for fill in leg_fills]
    if max(times) - min(times) > max_timestamp_skew_sec:
        return False, "legging_timestamp_skew"
    return True, "fillable"


def aggregate_greeks(positions: Iterable[Mapping[str, Any]]) -> Dict[str, float]:
    totals = {key: 0.0 for key in ("delta", "gamma", "theta", "vega")}
    for position in positions:
        multiplier = float(position.get("quantity", 0) or 0)
        if str(position.get("side", "BUY")).upper() == "SELL":
            multiplier *= -1.0
        for key in totals:
            totals[key] += float(position.get(key, 0) or 0) * multiplier
    return {key: round(value, 6) for key, value in totals.items()}


def scenario_pnl(
    positions: Iterable[Mapping[str, Any]], *, spot_move_pct: float,
    iv_change_points: float, days_elapsed: float = 0.0,
) -> float:
    """Second-order Greek approximation for a portfolio stress scenario."""
    total = 0.0
    for position in positions:
        spot = float(position.get("spot", 0) or 0)
        quantity = float(position.get("quantity", 0) or 0)
        sign = -1.0 if str(position.get("side", "BUY")).upper() == "SELL" else 1.0
        ds = spot * spot_move_pct
        pnl_one = (
            float(position.get("delta", 0) or 0) * ds
            + 0.5 * float(position.get("gamma", 0) or 0) * ds * ds
            + float(position.get("vega", 0) or 0) * iv_change_points
            + float(position.get("theta", 0) or 0) * days_elapsed
        )
        total += sign * quantity * pnl_one
    return round(total, 2)


def portfolio_risk_gate(
    positions: Sequence[Mapping[str, Any]], *,
    max_abs_delta: float, max_abs_gamma: float, max_abs_theta: float,
    max_abs_vega: float, max_stress_loss: float,
) -> Dict[str, Any]:
    greeks = aggregate_greeks(positions)
    breaches = []
    for key, limit in (
        ("delta", max_abs_delta), ("gamma", max_abs_gamma),
        ("theta", max_abs_theta), ("vega", max_abs_vega),
    ):
        if abs(greeks[key]) > max(0.0, float(limit)):
            breaches.append(f"{key}_limit")
    scenarios = {}
    for move in (-0.03, -0.02, 0.02, 0.03):
        for iv_change in (-5.0, 5.0):
            name = f"spot_{move:+.0%}_iv_{iv_change:+.0f}"
            scenarios[name] = scenario_pnl(
                positions, spot_move_pct=move, iv_change_points=iv_change, days_elapsed=1.0
            )
    worst = min(scenarios.values(), default=0.0)
    if worst < -abs(float(max_stress_loss)):
        breaches.append("scenario_loss_limit")
    return {
        "allowed": not breaches, "breaches": breaches, "greeks": greeks,
        "scenarios": scenarios, "worst_scenario_pnl": worst,
    }


def clustered_evidence(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Collapse correlated strikes into one underlying/snapshot decision."""
    clusters: Dict[Tuple[str, str, str], List[float]] = {}
    for row in rows:
        snapshot = str(row.get("snapshot_time", ""))
        minute = snapshot[:16]
        key = (
            str(row.get("underlying", "")).upper(),
            minute,
            str(row.get("direction", "")).upper(),
        )
        clusters.setdefault(key, []).append(float(row.get("net_r", 0) or 0))
    outcomes = [sum(values) / len(values) for values in clusters.values() if values]
    days = {key[1][:10] for key in clusters}
    mean = sum(outcomes) / len(outcomes) if outcomes else 0.0
    variance = (
        sum((value - mean) ** 2 for value in outcomes) / (len(outcomes) - 1)
        if len(outcomes) > 1 else 0.0
    )
    se = math.sqrt(variance / len(outcomes)) if outcomes else 0.0
    return {
        "rows": len(rows), "independent_clusters": len(outcomes),
        "independent_days": len(days), "mean_net_r": round(mean, 6),
        "ci95_low": round(mean - 1.96 * se, 6),
        "ci95_high": round(mean + 1.96 * se, 6),
    }


def volatility_edge(
    *, forecast_realized_vol: float, executable_implied_vol: float,
    spread_vol_points: float, hedging_cost_vol_points: float,
    uncertainty_vol_points: float,
) -> Dict[str, Any]:
    hurdle = sum(max(0.0, float(x)) for x in (
        spread_vol_points, hedging_cost_vol_points, uncertainty_vol_points
    ))
    raw = float(forecast_realized_vol) - float(executable_implied_vol)
    net = raw - math.copysign(hurdle, raw) if raw else 0.0
    direction = "LONG_VOL" if net > 0 else "SHORT_VOL" if net < 0 else "NO_TRADE"
    return {
        "raw_vol_edge": round(raw, 4), "cost_hurdle": round(hurdle, 4),
        "net_vol_edge": round(net, 4), "direction": direction,
        "tradable": abs(raw) > hurdle,
    }


def retail_algo_readiness(
    *, paper_trading: bool, enable_real_trading: bool, static_ips: Sequence[str],
    broker_algo_registered: bool, strategy_registered: bool,
    order_tags_enabled: bool, audit_chain_ok: bool,
) -> Dict[str, Any]:
    valid_ips = []
    for value in static_ips:
        try:
            ip = ipaddress.ip_address(str(value).strip())
            if not ip.is_private and not ip.is_loopback:
                valid_ips.append(str(ip))
        except ValueError:
            pass
    checks = {
        "paper_safety": bool(paper_trading and not enable_real_trading),
        "static_ip": bool(valid_ips),
        "broker_algo_registered": bool(broker_algo_registered),
        "strategy_registered": bool(strategy_registered),
        "order_tags": bool(order_tags_enabled),
        "audit_chain": bool(audit_chain_ok),
    }
    return {
        "live_ready": all(checks.values()) and not paper_trading,
        "checks": checks,
        "valid_static_ips": valid_ips,
        "blocks": [name for name, ok in checks.items() if not ok],
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
