"""
option_core_strategies.py — full entry/exit/strike-selection/adjustment
logic for exactly 2 of the 12 CORE option strategies from the 2026-07-16
user spec: C1 (09:20 Short Straddle) and C3 (Iron Condor). The remaining 10
CORE ids get full logic in follow-up turns, one at a time.

SHADOW/JOURNAL-ONLY: neither function places an order, not real, not
paper. Each computes a would-be entry, logs it through the existing
option-bot pipeline (option_decision_journal.record_option_decision --
same JSONL + Telegram channel hero_zero_strategy already uses) and writes
shadow rows into option_strike_signals for the cohort-evidence system
(option_live_edge_policy.cohort_policy(strategy=...)). This module must
never import angel, broker_interface, or option_execution_router -- there
is a contract test (test_option_strategy_catalog_no_execution.py) asserting
exactly that.

All numeric parameters (deltas, SL%, targets, widths) come from
option_strategy_params.json via option_strategy_registry._param(), and are
carried over from the user's spec as given -- UNVALIDATED. Nothing here
asserts or implies these numbers are profitable.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from greeks_live import compute_greeks
from option_quality import extract_chain_rows, extract_spot, chain_age_sec
import option_decision_journal as odj

logger = logging.getLogger(__name__)

SNAPSHOT_DB = "option_chain_snapshots.db"
RISK_FREE_RATE = 0.065  # matches greeks_live.py:442 / quant_models.py's existing convention

# Symbol -> strike-ladder step, same values already used independently in
# hero_zero_strategy.py / option_intelligence.py / option_selector.py (this
# repo's established, if duplicated, per-file convention for this constant).
_STRIKE_GAP = {
    "NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50,
    "SENSEX": 100, "MIDCPNIFTY": 25, "BANKEX": 100,
}


def _strike_gap(symbol: str) -> int:
    return _STRIKE_GAP.get(str(symbol or "").upper(), 50)


def _leg_quote(rows: List[Dict[str, Any]], strike: float, option_type: str) -> Dict[str, float]:
    """NSE-nested-shape reader: row['strikePrice'] + row['CE']/row['PE']
    sub-dicts, matching hero_zero_strategy.py's own _leg_quote/_chain_rows
    convention -- this is the shape option_data actually carries in this
    pipeline (market_context_builder.build_market_context wraps raw_json
    ['records']). Deliberately NOT option_quality.extract_contract_liquidity(),
    which assumes a flat CE_lastPrice-keyed row shape used by a different
    caller (option_multistrike_signals' own per-strike scan), not this one."""
    for row in rows:
        try:
            if abs(float(row.get("strikePrice", -1) or -1) - float(strike)) > 1e-6:
                continue
        except Exception:
            continue
        leg = row.get(option_type, {}) or {}

        def _f(*keys: str) -> float:
            for k in keys:
                v = leg.get(k)
                if v is not None:
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        logger.debug("non-numeric chain field %s=%r", k, v)
            return 0.0

        bid, ask = _f("bidprice", "bidPrice", "bestBid"), _f("askPrice", "askprice", "bestAsk")
        spread_pct = (ask - bid) / ((ask + bid) / 2.0) if (bid > 0 and ask >= bid and (ask + bid) > 0) else 0.0
        oi = _f("openInterest", "oi")
        oi_chg = _f("changeinOpenInterest", "changeInOpenInterest", "chg_oi")
        return {
            "last_price": _f("lastPrice", "ltp"),
            "oi": oi,
            "oi_change": oi_chg,
            "volume": _f("totalTradedVolume", "volume"),
            "iv": _f("impliedVolatility", "iv"),
            "spread_pct": spread_pct,
        }
    return {"last_price": 0.0, "oi": 0.0, "oi_change": 0.0, "volume": 0.0, "iv": 0.0, "spread_pct": 0.0}


# 2026-07-20 (operator: "include some image or color indicator of OI
# direction"). NSE's `changeinOpenInterest` is versus the PREVIOUS DAY's
# close, given per-strike in the same chain row -- no second snapshot
# needed. This is deliberately a simple 3-way OI-only classifier (BUILDUP/
# UNWINDING/FLAT), not the 4-way price+OI flow taxonomy
# (option_multistrike_signals._classify's LONG_BUILDUP/SHORT_COVERING/...)
# -- that one needs the leg's OWN premium change too, which this chain row
# shape doesn't carry a clean previous-close for. Reusing that function
# with a fabricated price input would be worse than a narrower, honest one.
_OI_DIRECTION_EMOJI = {"BUILDUP": "🟢", "UNWINDING": "🔴", "FLAT": "⚪"}


def _oi_direction(oi: float, oi_change: float) -> Dict[str, Any]:
    prev_oi = oi - oi_change
    pct = (oi_change / prev_oi * 100.0) if prev_oi > 0 else 0.0
    label = "BUILDUP" if pct > 2.0 else "UNWINDING" if pct < -2.0 else "FLAT"
    return {"oi_direction": label, "oi_change_pct": round(pct, 2),
            "oi_emoji": _OI_DIRECTION_EMOJI[label]}


def _strikes_near(spot: float, gap: int, steps: int) -> List[float]:
    center = round(spot / gap) * gap
    return [center + i * gap for i in range(-steps, steps + 1)]


def _dte_years(regime: Dict[str, Any]) -> float:
    dte = regime.get("days_to_expiry", 5) if isinstance(regime, dict) else 5
    if not isinstance(dte, (int, float)) or dte < 0:
        dte = 5
    # Floored at 1 day: compute_greeks requires T>0, and a 0DTE day's true
    # intraday T (a few hours) is a further refinement not attempted here.
    return max(int(dte), 1) / 365.0


def _delta_of(rows: List[Dict[str, Any]], strike: float, option_type: str,
              spot: float, t_years: float) -> float:
    q = _leg_quote(rows, strike, option_type)
    iv = q["iv"] / 100.0 if q["iv"] > 1.0 else q["iv"]  # NSE reports IV as e.g. 14.5, not 0.145
    if iv <= 0 or spot <= 0:
        return 0.0
    opt = "call" if option_type == "CE" else "put"
    g = compute_greeks(S=spot, K=strike, T=t_years, r=RISK_FREE_RATE, sigma=iv, option=opt)
    return float(g.get("delta", 0.0) or 0.0)


def _select_synthetic_atm(rows: List[Dict[str, Any]], spot: float, gap: int) -> Optional[Dict[str, Any]]:
    """C1's own strike rule: the strike minimizing |CE_LTP - PE_LTP|."""
    best = None
    for strike in _strikes_near(spot, gap, steps=3):
        ce, pe = _leg_quote(rows, strike, "CE"), _leg_quote(rows, strike, "PE")
        if ce["last_price"] <= 0 or pe["last_price"] <= 0:
            continue
        diff = abs(ce["last_price"] - pe["last_price"])
        if best is None or diff < best["diff"]:
            best = {"strike": strike, "diff": diff, "ce": ce, "pe": pe}
    return best


def _select_delta_strike(rows: List[Dict[str, Any]], spot: float, gap: int, target_delta: float,
                          option_type: str, t_years: float, max_steps: int = 20) -> Optional[Dict[str, Any]]:
    """C3's short-leg rule: walk the ladder outward from ATM in the OTM
    direction for `option_type`, stop at the first strike whose |delta|
    drops to/below target_delta."""
    center = round(spot / gap) * gap
    direction = 1 if option_type == "CE" else -1  # OTM calls sit above spot, OTM puts below
    for step in range(0, max_steps + 1):
        strike = center + direction * step * gap
        q = _leg_quote(rows, strike, option_type)
        if q["last_price"] <= 0:
            continue
        delta = abs(_delta_of(rows, strike, option_type, spot, t_years))
        if delta <= target_delta:
            return {"strike": strike, "quote": q, "delta": delta}
    return None


def _combo_id(symbol: str, strategy_id: str, ts: float) -> str:
    return f"{symbol.upper()}_{strategy_id}_{int(ts)}"


def _persist_shadow_legs(conn: sqlite3.Connection, legs: List[Dict[str, Any]], *, symbol: str,
                          expiry: str, strategy: str, combo_id: str, side: str,
                          snapshot_time: str) -> int:
    from option_multistrike_signals import ensure_multistrike_schema
    ensure_multistrike_schema(conn)
    ts = time.time()
    n = 0
    for leg in legs:
        conn.execute(
            """INSERT OR REPLACE INTO option_strike_signals
               (ts, snapshot_time, underlying, expiry, strike, option_type, flow, signal,
                direction, score, tradable, price, reason, source, strategy, combo_id, side)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ts, snapshot_time, symbol.upper(), expiry, float(leg["strike"]), leg["option_type"],
             "SHADOW_STRATEGY", "SHADOW_ENTRY", "NEUTRAL", 0.0, 0, float(leg.get("price", 0.0) or 0.0),
             f"shadow_{strategy}", "shadow_catalog", strategy, combo_id, side),
        )
        n += 1
    conn.commit()
    return n


def _get_conn(conn: Optional[sqlite3.Connection]) -> sqlite3.Connection:
    return conn if conn is not None else sqlite3.connect(SNAPSHOT_DB)


# ── C1: 09:20 Short Straddle ────────────────────────────────────────────────

def evaluate_c1_short_straddle(symbol: str, df=None, df_htf=None, option_data=None,
                                regime: Optional[Dict[str, Any]] = None,
                                conn: Optional[sqlite3.Connection] = None, **kw) -> Dict[str, Any]:
    from option_strategy_registry import _param
    strategy_id = "C1"
    regime = regime or {}

    rows = extract_chain_rows(option_data)
    spot = extract_spot(option_data)
    age = chain_age_sec(option_data)

    def _blocked(reason: str) -> Dict[str, Any]:
        odj.record_option_decision(
            strategy=strategy_id, symbol=symbol, decision=f"blocked_{reason}", reason=reason,
            side="SELL", spot=spot, metadata={"regime": regime, "unvalidated": True})
        return {"id": strategy_id, "status": f"blocked_{reason}"}

    if not rows or spot <= 0:
        return _blocked("no_chain_data")
    if age is not None and age > _param(strategy_id, "max_chain_age_sec", 180):
        return _blocked("stale_chain")

    # Regime gate (spec: range-bound, low VIX; skip if trending, gapped, or
    # VIX itself is elevated -- see option_strategy_params.json's C1 entry
    # for the exact thresholds this is reading).
    is_nifty = symbol.upper() == "NIFTY"
    vix_max = _param(strategy_id, "vix_max_nifty" if is_nifty else "vix_max_banknifty", 12 if is_nifty else 15)
    if float(regime.get("vix", 15.0)) > float(vix_max):
        return _blocked("vix_too_high_for_theta")
    if regime.get("primary") == "STRONG_TREND":
        return _blocked("regime_strong_trend")
    if regime.get("gap"):
        return _blocked("gap_too_large")

    gap_pts = _strike_gap(symbol)
    picked = _select_synthetic_atm(rows, spot, gap_pts)
    if picked is None:
        return _blocked("no_valid_synthetic_atm_strike")

    strike, ce, pe = picked["strike"], picked["ce"], picked["pe"]
    t_years = _dte_years(regime)
    ce_delta = _delta_of(rows, strike, "CE", spot, t_years)
    pe_delta = _delta_of(rows, strike, "PE", spot, t_years)
    net_credit = round(ce["last_price"] + pe["last_price"], 2)

    legs = [
        {"strike": strike, "option_type": "CE", "price": ce["last_price"], "delta": round(ce_delta, 4),
         **_oi_direction(ce["oi"], ce.get("oi_change", 0.0))},
        {"strike": strike, "option_type": "PE", "price": pe["last_price"], "delta": round(pe_delta, 4),
         **_oi_direction(pe["oi"], pe.get("oi_change", 0.0))},
    ]
    ts = time.time()
    combo_id = _combo_id(symbol, strategy_id, ts)
    snapshot_time = datetime.now().isoformat()

    payload = odj.record_option_decision(
        strategy=strategy_id, symbol=symbol, decision="selected",
        reason="synthetic_atm_short_straddle", side="SELL", spot=spot,
        selected={
            "legs": legs, "net_credit": net_credit, "risk_profile": "undefined_risk",
            "combo_id": combo_id,
        },
        metadata={
            "regime": regime,
            # OI direction is OBSERVED CONTEXT, logged for future evidence
            # mining -- NOT a live entry gate. It plays no role in whether
            # this straddle fires; adding it as a gate would need its own
            # forward-holdout validation first, same as every other param.
            "oi_context_unvalidated": True,
            "sl_pct_per_leg": _param(strategy_id, "sl_desc_per_leg_pct", [25, 30]),
            "target_desc": _param(strategy_id, "target_desc", "25-30% total premium"),
            "margin_max_pct_capital": _param(strategy_id, "margin_max_pct_capital", 25),
            "mtm_stop_x_expected_decay": _param(strategy_id, "mtm_stop_x_expected_decay", 1.5),
            "delta_hedge_breach": _param(strategy_id, "delta_hedge_breach", 0.15),
            "unvalidated": True,
        },
    )

    _persist_shadow_legs(
        _get_conn(conn), legs, symbol=symbol, expiry=str(regime.get("next_expiry", "")),
        strategy=strategy_id, combo_id=combo_id, side="SELL", snapshot_time=snapshot_time)

    return {"id": strategy_id, "status": "selected", "combo_id": combo_id,
            "net_credit": net_credit, "decision": payload.get("decision")}


def evaluate_c1_adjustment(current_deltas: Dict[str, float], current_spot: float,
                            entry_spot: float) -> Dict[str, Any]:
    """Pure, independently-testable adjustment logic for C1 (straddle ->
    strangle re-entry / delta-hedge / shift-to-new-ATM flags). Takes the
    CE/PE legs' current deltas directly (e.g. {"CE": 0.62, "PE": -0.31})
    rather than recomputing them from a live chain, so this stays a plain
    pure function testable against synthetic "spot moved N%" scenarios. NOT
    called from any live path this pass -- there is no open-position
    tracker yet to monitor a real straddle against (that's the out-of-scope
    execution layer); built now so a future lifecycle-monitor turn can call
    this directly instead of reinventing it."""
    recs: List[str] = []
    move_pct = abs(current_spot - entry_spot) / entry_spot if entry_spot > 0 else 0.0
    if move_pct > _safe_param("shift_if_spot_move_pct_above", 0.5) / 100.0:
        recs.append("shift_to_new_atm")

    ce_delta, pe_delta = current_deltas.get("CE", 0.0), current_deltas.get("PE", 0.0)
    net_delta = ce_delta + pe_delta
    if abs(net_delta) > _safe_param("delta_hedge_breach", 0.15):
        recs.append("delta_hedge_with_futures")

    threatened_ce = ce_delta > 0.65  # spec's leg-SL-hit proxy: far ITM on either side
    threatened_pe = pe_delta < -0.65
    if threatened_ce or threatened_pe:
        recs.append("convert_to_strangle_on_threatened_side")

    if not recs:
        recs.append("no_adjustment_needed")
    return {"recommendations": recs, "move_pct": round(move_pct, 5), "net_delta": round(net_delta, 4)}


def _safe_param(key: str, default: float) -> float:
    try:
        from option_strategy_registry import _param
        return float(_param("C1", key, default))
    except Exception:
        return float(default)


# ── C3: Iron Condor ─────────────────────────────────────────────────────────

def evaluate_c3_iron_condor(symbol: str, df=None, df_htf=None, option_data=None,
                             regime: Optional[Dict[str, Any]] = None,
                             conn: Optional[sqlite3.Connection] = None, **kw) -> Dict[str, Any]:
    from option_strategy_registry import _param
    strategy_id = "C3"
    regime = regime or {}

    rows = extract_chain_rows(option_data)
    spot = extract_spot(option_data)
    age = chain_age_sec(option_data)

    def _blocked(reason: str) -> Dict[str, Any]:
        odj.record_option_decision(
            strategy=strategy_id, symbol=symbol, decision=f"blocked_{reason}", reason=reason,
            side="SELL", spot=spot, metadata={"regime": regime, "unvalidated": True})
        return {"id": strategy_id, "status": f"blocked_{reason}"}

    if not rows or spot <= 0:
        return _blocked("no_chain_data")
    if age is not None and age > _param(strategy_id, "max_chain_age_sec", 180):
        return _blocked("stale_chain")

    # Regime gate: range-bound preferred; still evaluated on MILD_TREND
    # given the defined-risk wings (per spec's own framing) but not on a
    # confirmed STRONG_TREND or gap day. IVP gating from the spec (50-80)
    # is not applied here -- this repo has no IV-percentile calculator
    # surfaced to this call path yet; noted as a known gap, not silently
    # assumed to pass.
    if regime.get("primary") == "STRONG_TREND":
        return _blocked("regime_strong_trend")
    if regime.get("gap"):
        return _blocked("gap_too_large")

    gap_pts = _strike_gap(symbol)
    t_years = _dte_years(regime)
    target_delta = float(_param(strategy_id, "short_delta_target", 0.16))

    short_ce = _select_delta_strike(rows, spot, gap_pts, target_delta, "CE", t_years)
    short_pe = _select_delta_strike(rows, spot, gap_pts, target_delta, "PE", t_years)
    if short_ce is None or short_pe is None:
        return _blocked("no_valid_short_strike_at_target_delta")

    is_nifty = symbol.upper() == "NIFTY"
    wing_key = "wing_width_pts_nifty" if is_nifty else "wing_width_pts_banknifty"
    wing_default = [100, 150] if is_nifty else [400, 500]
    wing_width = float(_param(strategy_id, wing_key, wing_default)[0])

    long_ce_strike = short_ce["strike"] + wing_width
    long_pe_strike = short_pe["strike"] - wing_width
    long_ce = _leg_quote(rows, long_ce_strike, "CE")
    long_pe = _leg_quote(rows, long_pe_strike, "PE")
    if long_ce["last_price"] <= 0 or long_pe["last_price"] <= 0:
        return _blocked("no_liquid_wing_strike")

    net_credit = round(
        (short_ce["quote"]["last_price"] - long_ce["last_price"])
        + (short_pe["quote"]["last_price"] - long_pe["last_price"]), 2)
    max_loss = round(wing_width - net_credit, 2)

    legs = [
        {"strike": short_ce["strike"], "option_type": "CE", "side": "SELL", "price": short_ce["quote"]["last_price"], "delta": round(short_ce["delta"], 4),
         **_oi_direction(short_ce["quote"]["oi"], short_ce["quote"].get("oi_change", 0.0))},
        {"strike": long_ce_strike, "option_type": "CE", "side": "BUY", "price": long_ce["last_price"], "delta": 0.0,
         **_oi_direction(long_ce["oi"], long_ce.get("oi_change", 0.0))},
        {"strike": short_pe["strike"], "option_type": "PE", "side": "SELL", "price": short_pe["quote"]["last_price"], "delta": round(short_pe["delta"], 4),
         **_oi_direction(short_pe["quote"]["oi"], short_pe["quote"].get("oi_change", 0.0))},
        {"strike": long_pe_strike, "option_type": "PE", "side": "BUY", "price": long_pe["last_price"], "delta": 0.0,
         **_oi_direction(long_pe["oi"], long_pe.get("oi_change", 0.0))},
    ]
    ts = time.time()
    combo_id = _combo_id(symbol, strategy_id, ts)
    snapshot_time = datetime.now().isoformat()

    payload = odj.record_option_decision(
        strategy=strategy_id, symbol=symbol, decision="selected",
        reason="delta_targeted_iron_condor", side="SELL", spot=spot,
        selected={
            "legs": legs, "net_credit": net_credit, "max_loss": max_loss,
            "wing_width": wing_width, "risk_profile": "defined_risk", "combo_id": combo_id,
        },
        metadata={
            "regime": regime,
            "sl_desc": _param(strategy_id, "sl_desc", "2x credit"),
            "target_desc": _param(strategy_id, "target_desc", "50% credit"),
            "risk_pct_capital": _param(strategy_id, "risk_pct_capital", 1.0),
            # OI direction is OBSERVED CONTEXT for future evidence mining --
            # NOT a live entry gate (see C1's identical note).
            "oi_context_unvalidated": True,
            "unvalidated": True,
        },
    )

    # Persisted rows use each leg's own SELL/BUY side (a condor has both).
    persist_legs = [{**leg} for leg in legs]
    _persist_multi_side_shadow_legs(
        _get_conn(conn), persist_legs, symbol=symbol, expiry=str(regime.get("next_expiry", "")),
        strategy=strategy_id, combo_id=combo_id, snapshot_time=snapshot_time)

    return {"id": strategy_id, "status": "selected", "combo_id": combo_id,
            "net_credit": net_credit, "max_loss": max_loss, "decision": payload.get("decision")}


def _persist_multi_side_shadow_legs(conn: sqlite3.Connection, legs: List[Dict[str, Any]], *,
                                     symbol: str, expiry: str, strategy: str, combo_id: str,
                                     snapshot_time: str) -> int:
    """Like _persist_shadow_legs but each leg carries its own side (a condor
    has 2 SELL legs and 2 BUY legs), unlike C1 where every leg shares one
    side."""
    from option_multistrike_signals import ensure_multistrike_schema
    ensure_multistrike_schema(conn)
    ts = time.time()
    n = 0
    for leg in legs:
        conn.execute(
            """INSERT OR REPLACE INTO option_strike_signals
               (ts, snapshot_time, underlying, expiry, strike, option_type, flow, signal,
                direction, score, tradable, price, reason, source, strategy, combo_id, side)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ts, snapshot_time, symbol.upper(), expiry, float(leg["strike"]), leg["option_type"],
             "SHADOW_STRATEGY", "SHADOW_ENTRY", "NEUTRAL", 0.0, 0, float(leg.get("price", 0.0) or 0.0),
             f"shadow_{strategy}", "shadow_catalog", strategy, combo_id, leg.get("side", "SELL")),
        )
        n += 1
    conn.commit()
    return n


def evaluate_c3_adjustment(legs: List[Dict[str, Any]], current_spot: float,
                           short_ce_strike: float, short_pe_strike: float) -> Dict[str, Any]:
    """Pure, independently-testable adjustment logic for C3 (roll untested
    short leg inward once / close breached side). NOT called from any live
    path this pass -- same reasoning as evaluate_c1_adjustment."""
    recs: List[str] = []
    if current_spot > short_ce_strike:
        recs.append("close_call_side_let_put_run_as_credit_vertical")
    elif current_spot < short_pe_strike:
        recs.append("close_put_side_let_call_run_as_credit_vertical")
    else:
        recs.append("no_adjustment_needed")
    return {"recommendations": recs}
