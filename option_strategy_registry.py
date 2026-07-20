"""
option_strategy_registry.py — catalog of the 42 option strategies from the
2026-07-16 user spec (A1-A14, B1-B5, C1-C9, D1-D7, E1-E7). The spec's own
header claims "47 strategies" but its own activation matrix and detailed
catalog only ever enumerate 42 IDs -- that headline/count mismatch is in the
source document, not something invented or "fixed" here.

Every strategy in this catalog is EVALUATE-AND-JOURNAL ONLY in this pass:
it computes whether it would enter, logs the decision through the existing
option-bot pipeline (option_decision_journal.record_option_decision, the
same JSONL + Telegram channel hero_zero_strategy already uses), and writes
shadow rows into option_strike_signals for the cohort-evidence system. NOT
ONE of these strategies places an order -- not real, not paper. That mirrors
hero_zero_strategy.py's existing default-shadow pattern, and is deliberate:
the codebase's own authors already declared multi-leg execution not ready
(signal_engine.py's RESEARCH_ONLY_MULTILEG_STRATEGIES quarantine + its guard
test), and no atomic multi-leg executor exists yet to place these safely.

Only 2 of the 12 CORE ids have real logic this pass: C1 (09:20 Short
Straddle) and C3 (Iron Condor), both in option_core_strategies.py. The
other 40 entries (10 CORE-not-yet-implemented + 30 ADVANCED/DANGEROUS/
DISABLED/SKIPPED) are registered -- every ID is present and callable -- but
route through _placeholder_result() and do nothing else. logic_status
distinguishes "has real code" from activation_status, which is the user's
own CORE/ADVANCED/DANGEROUS/DISABLED/SKIPPED taxonomy, preserved verbatim.

Every numeric parameter (deltas, SL%, targets, ...) lives in
option_strategy_params.json, carried over from the user's spec as given and
explicitly labelled "unvalidated": true -- see that file's _meta note.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

PARAMS_FILE = Path(__file__).parent / "option_strategy_params.json"

_ENTRY_WINDOW_RANGE_RE = re.compile(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$")
_ENTRY_WINDOW_SHARP_RE = re.compile(r"^(\d{1,2}):(\d{2})(?:_sharp)?$")
# 2026-07-20: was a narrow +/-3min SYMMETRIC tolerance. Production data
# showed real per-symbol cycle latency (API timeouts + retries some
# mornings) routinely pushes a symbol's evaluation past a 6-minute window
# entirely -- confirmed live: NIFTY's chain fetch logged at 09:20:32 and
# 09:20:46 on 2026-07-20 (inside the old window), yet C1 produced ZERO
# journal entries all day, because by the time this symbol's full cycle
# reached the catalog call, wall-clock had drifted past 09:23:00. A
# one-shot strategy should fire on the first opportunity AT OR AFTER its
# target time, not require landing inside a window that assumes near-zero
# latency. Small pre-target tolerance kept for clock-skew safety; the real
# fix is the catch-up window after the target (spec's own C1 doc explicitly
# allows a "09:35 variant for less noise", i.e. up to +15min is spec-sane).
_SHARP_EARLY_TOLERANCE_MIN = int(os.getenv("OPT_STRAT_SHARP_EARLY_TOLERANCE_MIN", "1"))
_SHARP_CATCHUP_MIN = int(os.getenv("OPT_STRAT_SHARP_CATCHUP_MIN", "15"))


@dataclass(frozen=True)
class OptionStrategyDef:
    id: str
    name: str
    category: str            # directional | defined_risk_spread | non_directional_theta | long_vega_event | dynamic_adjustment
    risk_profile: str         # defined_risk | undefined_risk | not_implemented
    activation_status: str    # CORE | ADVANCED | DANGEROUS | DISABLED | SKIPPED (verbatim from user spec)
    entry_window: str = ""    # raw spec text, e.g. "09:20_sharp" or "09:30-11:00"
    logic_status: str = "PLACEHOLDER"   # IMPLEMENTED | PLACEHOLDER (new field, orthogonal to activation_status)
    evaluate_fn: Optional[Callable[..., Dict[str, Any]]] = None
    param_key: str = field(default="")

    @property
    def family(self) -> str:
        return self.id[0] if self.id else ""


def _family_category(letter: str) -> str:
    return {
        "A": "directional",
        "B": "defined_risk_spread",
        "C": "non_directional_theta",
        "D": "long_vega_event",
        "E": "dynamic_adjustment",
    }.get(letter, "unknown")


def _def(id_: str, name: str, activation_status: str, entry_window: str,
         risk_profile: str) -> OptionStrategyDef:
    return OptionStrategyDef(
        id=id_, name=name, category=_family_category(id_[0]),
        risk_profile=risk_profile, activation_status=activation_status,
        entry_window=entry_window, param_key=id_,
    )


# ── The 42-entry catalog (activation_status verbatim from the user's own
# activation matrix: CORE=12, ADVANCED=18, DANGEROUS=4, DISABLED=7, SKIPPED=1) ──
OPTION_STRATEGY_CATALOG: List[OptionStrategyDef] = [
    _def("A1",  "ORB Directional Option Buy",              "CORE",     "09:30-10:30", "defined_risk"),
    _def("A2",  "Supertrend + ADX Momentum",                "CORE",     "09:20-10:30,13:30-14:30", "defined_risk"),
    _def("A3",  "High-Frequency Gamma Scalping",            "DISABLED", "09:15-09:45,14:30-15:15", "defined_risk"),
    _def("A4",  "VWAP + CPR Breakout",                      "DISABLED", "09:30-11:00", "defined_risk"),
    _def("A5",  "Triple-Confluence Momentum",                "ADVANCED", "09:30-14:30", "defined_risk"),
    _def("A6",  "15-Minute Candle Sweep (IMPS/CISD)",        "DISABLED", "09:30-14:45", "defined_risk"),
    _def("A7",  "Bollinger Band + Stochastic Directional",  "DISABLED", "09:15-10:00,12:30-14:00", "defined_risk"),
    _def("A8",  "Multi-Timeframe Trend Alignment",          "CORE",     "09:45-12:00", "defined_risk"),
    _def("A9",  "EMA Pullback Continuation",                "DISABLED", "10:00-14:30", "defined_risk"),
    _def("A10", "Gamma Expiry Scalping (0-DTE)",             "DANGEROUS","10:00-14:30", "defined_risk"),
    _def("A11", "CPR Breakout Directional (PDH/PDL)",       "DISABLED", "09:30-10:30", "defined_risk"),
    _def("A12", "Option Buying Momentum (OI+Vol)",           "ADVANCED", "09:30-14:30", "defined_risk"),
    _def("A13", "Multi-Leg SuperTrend Breakout Spread",      "CORE",     "09:20-11:00", "defined_risk"),
    _def("A14", "Synthetic Long/Short",                      "DISABLED", "10:00-12:00", "undefined_risk"),

    _def("B1", "Debit Vertical (Bull Call / Bear Put)",      "CORE",     "09:30-11:30", "defined_risk"),
    _def("B2", "Credit Vertical (Bull Put / Bear Call)",     "CORE",     "09:45-13:00", "defined_risk"),
    _def("B3", "Bull Put Credit Spread",                     "ADVANCED", "09:45-10:30", "defined_risk"),
    _def("B4", "Bear Call Credit Spread",                    "ADVANCED", "09:45-10:30", "defined_risk"),
    _def("B5", "Ratio Spread (1:2)",                         "DANGEROUS","09:45-11:00", "undefined_risk"),

    _def("C1", "09:20 Short Straddle",                       "CORE",     "09:20_sharp", "undefined_risk"),
    _def("C2", "Short Strangle (OTM)",                       "ADVANCED", "09:30-10:30", "undefined_risk"),
    _def("C3", "Iron Condor",                                "CORE",     "09:30-11:00", "defined_risk"),
    _def("C4", "Iron Butterfly",                             "CORE",     "09:45-10:30", "defined_risk"),
    _def("C5", "Calendar Spread",                            "ADVANCED", "mon_10:00-11:30_or_tue_13:00-14:30", "defined_risk"),
    _def("C6", "Expiry-Day OTM Decay Harvest",                "DANGEROUS","13:00-13:30", "defined_risk"),
    _def("C7", "CPR Mean Reversion (Fading the Extremes)",   "ADVANCED", "10:00-11:30", "defined_risk"),
    _def("C8", "VWAP Mean Reversion (Extreme Stretch Fade)", "CORE",     "10:00-14:00", "defined_risk"),
    _def("C9", "Short Straddle -> Strangle Conversion",       "ADVANCED", "09:25_sharp", "undefined_risk"),

    _def("D1", "Pre-Event Long Strangle",                     "CORE",     "09:15-09:30", "defined_risk"),
    _def("D2", "Post-Event Volatility Crush Short Strangle",  "ADVANCED", "11:30-13:00", "undefined_risk"),
    _def("D3", "Long Straddle (Generic Pre-Event)",            "ADVANCED", "10:00-11:00", "defined_risk"),
    _def("D4", "Long Strangle (Volatility Breakout/Compression)", "ADVANCED", "09:45-10:15", "defined_risk"),
    _def("D5", "Gamma Scalping (Long Straddle + Futures Hedge)", "SKIPPED", "", "not_implemented"),
    _def("D6", "Reverse Iron Condor / Reverse Iron Butterfly", "ADVANCED", "10:00-11:00", "defined_risk"),
    _def("D7", "Diagonal Spread",                              "ADVANCED", "mon_10:00-11:30", "defined_risk"),

    _def("E1", "Time-Based Adaptive Strangle",                 "ADVANCED", "09:30", "undefined_risk"),
    _def("E2", "Straddle -> Strangle Auto-Conversion (State Machine)", "CORE", "09:20_sharp", "undefined_risk"),
    _def("E3", "Delta-Neutral Straddle Machine",               "ADVANCED", "09:20_sharp", "undefined_risk"),
    _def("E4", "Rolling Short Straddle (Intraday Re-Entry)",   "ADVANCED", "09:20_sharp", "undefined_risk"),
    _def("E5", "Short Strangle with Delta Rebalancing",        "ADVANCED", "09:15-09:30", "undefined_risk"),
    _def("E6", "Trend-Adjusting Iron Condor",                  "ADVANCED", "09:45-10:30", "defined_risk"),
    _def("E7", "Ratio Spread with Tail Hedge",                 "DANGEROUS","09:45-11:00", "undefined_risk"),
]

_BY_ID: Dict[str, OptionStrategyDef] = {d.id: d for d in OPTION_STRATEGY_CATALOG}

_EXPECTED_IDS = (
    [f"A{i}" for i in range(1, 15)] + [f"B{i}" for i in range(1, 6)]
    + [f"C{i}" for i in range(1, 10)] + [f"D{i}" for i in range(1, 8)]
    + [f"E{i}" for i in range(1, 8)]
)
assert sorted(_BY_ID) == sorted(_EXPECTED_IDS), "catalog ID set drifted from the spec's own A1-E7 enumeration"


# ── Late-bound real logic (avoids a circular import: option_core_strategies
# imports _param from this module, so this module can only import that one
# back after its own top-level definitions are complete) ──────────────────
try:
    from option_core_strategies import evaluate_c1_short_straddle, evaluate_c3_iron_condor
    _CORE_STRATEGIES_AVAIL = True
except Exception as exc:
    _CORE_STRATEGIES_AVAIL = False
    logger.debug("option_core_strategies unavailable: %s", exc)

if _CORE_STRATEGIES_AVAIL:
    _BY_ID["C1"] = OptionStrategyDef(
        **{**_BY_ID["C1"].__dict__, "logic_status": "IMPLEMENTED", "evaluate_fn": evaluate_c1_short_straddle})
    _BY_ID["C3"] = OptionStrategyDef(
        **{**_BY_ID["C3"].__dict__, "logic_status": "IMPLEMENTED", "evaluate_fn": evaluate_c3_iron_condor})
    OPTION_STRATEGY_CATALOG = [_BY_ID[d.id] for d in OPTION_STRATEGY_CATALOG]


# ── Params loading (env-override-first, same spirit as config.py's _fenv/_ienv) ──

_PARAMS_CACHE: Optional[Dict[str, Dict[str, Any]]] = None


def load_strategy_params(path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    global _PARAMS_CACHE
    if path is None and _PARAMS_CACHE is not None:
        return _PARAMS_CACHE
    p = Path(path) if path else PARAMS_FILE
    try:
        data = json.loads(p.read_text())
    except Exception as exc:
        logger.debug("load_strategy_params(%s): %s", p, exc)
        data = {}
    if path is None:
        _PARAMS_CACHE = data
    return data


def _param(strategy_id: str, key: str, default: Any = None) -> Any:
    """Env override (OPT_STRAT_<ID>_<KEY>) first, else the params JSON, else default."""
    env_key = f"OPT_STRAT_{strategy_id.upper()}_{key.upper()}"
    raw = os.getenv(env_key)
    if raw is not None:
        try:
            if isinstance(default, bool):
                return raw.strip().lower() in {"1", "true", "yes", "on"}
            if isinstance(default, int):
                return int(raw)
            if isinstance(default, float):
                return float(raw)
        except (TypeError, ValueError) as exc:
            logger.debug("env override %s=%r not coercible to %s: %s",
                         env_key, raw, type(default).__name__, exc)
        return raw
    params = load_strategy_params()
    entry = params.get(strategy_id, {})
    return entry.get(key, default)


# ── Entry-window parsing (spec: "strategies come with time also") ────────

def _parse_hhmm(text: str) -> Optional[dtime]:
    try:
        h, m = text.split(":")
        return dtime(int(h), int(m))
    except Exception:
        return None


def entry_window_ok(entry_window: str, now: Optional[datetime] = None) -> bool:
    """True if `now` falls inside `entry_window`. Handles the two parseable
    spec forms ("HH:MM-HH:MM" range, "HH:MM" / "HH:MM_sharp" one-shot --
    fires from a small pre-target tolerance through a post-target catch-up
    window, NOT a narrow symmetric band; see the catch-up-window comment
    above), comma-separated multiples of either. Exotic forms
    (day-of-week-qualified, "T-1_...", "same_as_base_strategy" -- all only
    used by strategies that have no real logic yet this pass) are reported
    as not-open rather than raising, since there is nothing to evaluate them
    against yet. Callers of a one-shot ("_sharp") window are responsible for
    their own once-per-day dedup (see evaluate_catalog's _ONE_SHOT_FIRED) --
    this function only answers "is now within the firing opportunity"."""
    if not entry_window:
        return False
    now = now or datetime.now()
    t = now.time()
    for chunk in entry_window.split(","):
        chunk = chunk.strip()
        m = _ENTRY_WINDOW_RANGE_RE.match(chunk)
        if m:
            start = dtime(int(m.group(1)), int(m.group(2)))
            end = dtime(int(m.group(3)), int(m.group(4)))
            if start <= t <= end:
                return True
            continue
        m = _ENTRY_WINDOW_SHARP_RE.match(chunk)
        if m:
            target = dtime(int(m.group(1)), int(m.group(2)))
            target_min = target.hour * 60 + target.minute
            now_min = t.hour * 60 + t.minute
            if -_SHARP_EARLY_TOLERANCE_MIN <= (now_min - target_min) <= _SHARP_CATCHUP_MIN:
                return True
            continue
        # Unrecognized/exotic form -- safe default is "not confirmed open".
    return False


def is_one_shot_window(entry_window: str) -> bool:
    """True for '_sharp'/bare-HH:MM windows (fire-once-per-day semantics),
    False for 'HH:MM-HH:MM' ranges (fire-every-cycle-while-open)."""
    chunk = (entry_window or "").split(",")[0].strip()
    return bool(_ENTRY_WINDOW_SHARP_RE.match(chunk)) and not _ENTRY_WINDOW_RANGE_RE.match(chunk)


# ── Dispatch ───────────────────────────────────────────────────────────────

_PLACEHOLDER_LOGGED: set = set()


def _placeholder_result(strategy_def: OptionStrategyDef, symbol: str) -> Dict[str, Any]:
    key = (strategy_def.id, symbol, datetime.now().strftime("%Y-%m-%d"))
    if key not in _PLACEHOLDER_LOGGED:
        _PLACEHOLDER_LOGGED.add(key)
        logger.debug(
            "option_strategy_catalog: %s (%s) not_active [%s/%s] for %s",
            strategy_def.id, strategy_def.name, strategy_def.activation_status,
            strategy_def.logic_status, symbol,
        )
    return {
        "id": strategy_def.id,
        "status": "not_active",
        "reason": f"{strategy_def.activation_status.lower()}_{strategy_def.logic_status.lower()}",
    }


# One-shot ("_sharp") strategies must fire at most once per (strategy, symbol,
# day) even though their catch-up window now spans several scan cycles --
# without this, C1 could sell a fresh straddle every ~20s for 16 minutes
# straight. Keyed on a "selected" result only: a blocked/error result must
# NOT be marked done, so the strategy keeps retrying within its catch-up
# window (e.g. still-too-high VIX at 09:20 clearing by 09:28).
_ONE_SHOT_FIRED: set = set()


def evaluate_catalog(symbol: str, df, df_htf, option_data, regime: Dict[str, Any],
                      **kw) -> List[Dict[str, Any]]:
    """Evaluate every strategy in the catalog once. IMPLEMENTED entries call
    their real evaluate_fn (shadow/journal-only, never places an order);
    every other entry returns a cheap, inert not_active result. Never raises
    -- a single strategy's exception is caught and reported, not allowed to
    stop the rest of the catalog or bubble into the live loop."""
    today = datetime.now().strftime("%Y-%m-%d")
    results: List[Dict[str, Any]] = []
    for strategy_def in OPTION_STRATEGY_CATALOG:
        if strategy_def.logic_status != "IMPLEMENTED" or strategy_def.evaluate_fn is None:
            results.append(_placeholder_result(strategy_def, symbol))
            continue
        fire_key = (strategy_def.id, symbol, today)
        one_shot = is_one_shot_window(strategy_def.entry_window)
        if one_shot and fire_key in _ONE_SHOT_FIRED:
            results.append({"id": strategy_def.id, "status": "already_fired_today"})
            continue
        try:
            if not entry_window_ok(strategy_def.entry_window):
                results.append({"id": strategy_def.id, "status": "blocked_entry_window"})
                continue
            result = strategy_def.evaluate_fn(
                symbol=symbol, df=df, df_htf=df_htf, option_data=option_data,
                regime=regime, **kw)
            if one_shot and result.get("status") == "selected":
                _ONE_SHOT_FIRED.add(fire_key)
            results.append(result)
        except Exception as exc:
            logger.debug("evaluate_catalog(%s): %s", strategy_def.id, exc)
            results.append({"id": strategy_def.id, "status": "error", "reason": str(exc)[:200]})
    return results


# ── Accessors (used by tests and by anything wanting a slice of the catalog) ──

def get_strategy(strategy_id: str) -> OptionStrategyDef:
    return _BY_ID[strategy_id]


def core_ids() -> List[str]:
    return sorted(d.id for d in OPTION_STRATEGY_CATALOG if d.activation_status == "CORE")


def implemented_ids() -> List[str]:
    return sorted(d.id for d in OPTION_STRATEGY_CATALOG if d.logic_status == "IMPLEMENTED")


def by_activation_status(status: str) -> List[OptionStrategyDef]:
    return [d for d in OPTION_STRATEGY_CATALOG if d.activation_status == status]
