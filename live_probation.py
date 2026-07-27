"""
live_probation.py

Tiny controlled live-trading gate for validation-blocked strategies.

This is deliberately separate from the main live-eligibility manifest. It lets
the bot place very small probation live legs while keeping unproven strategies
paper-first and tightly capped.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Set


@dataclass
class ProbationDecision:
    allowed: bool
    reason: str
    live_qty: int = 0
    max_capital: float = 0.0
    max_trades_per_day: int = 0
    trades_today: int = 0
    daily_pnl: float = 0.0
    mode: str = "blocked"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _cfg(name: str, default: Any) -> Any:
    try:
        import config
        return getattr(config, name, default)
    except Exception:
        return default


def _state_path() -> Path:
    return Path(str(_cfg("LIVE_PROBATION_STATE_FILE", "live_probation_state.json")))


def _today() -> str:
    return date.today().isoformat()


def load_state(path: Optional[str] = None) -> Dict[str, Any]:
    p = Path(path) if path else _state_path()
    if not p.exists():
        return {"date": _today(), "trades": [], "daily_pnl": 0.0, "loss_locked": False}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        state = raw if isinstance(raw, dict) else {}
    except Exception:
        state = {}
    if state.get("date") != _today():
        return {"date": _today(), "trades": [], "daily_pnl": 0.0, "loss_locked": False}
    state.setdefault("trades", [])
    state.setdefault("daily_pnl", 0.0)
    state.setdefault("loss_locked", False)
    return state


def save_state(state: Dict[str, Any], path: Optional[str] = None) -> None:
    p = Path(path) if path else _state_path()
    p.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def _is_option_symbol(symbol: str, metadata: Dict[str, Any]) -> bool:
    sym = str(symbol or "").upper()
    return (
        str(metadata.get("asset_type", "")).upper() == "OPTION"
        or sym.endswith(("CE", "PE"))
    )


def _cfg_set(name: str, default: Iterable[str]) -> Set[str]:
    value = _cfg(name, default)
    if isinstance(value, str):
        return {x.strip().upper() for x in value.split(",") if x.strip()}
    try:
        return {str(x).strip().upper() for x in value if str(x).strip()}
    except Exception:
        return {str(x).strip().upper() for x in default if str(x).strip()}


def _first_status(*values: Any) -> str:
    for value in values:
        if isinstance(value, dict):
            nested = _first_status(
                value.get("status"),
                value.get("edge_policy"),
                value.get("autonomous_edge_status"),
            )
            if nested:
                return nested
        text = str(value or "").strip().upper()
        if text:
            return text
    return ""


def _evidence_status(metadata: Dict[str, Any], meta_signal: Dict[str, Any]) -> str:
    return _first_status(
        metadata.get("edge_policy"),
        metadata.get("option_edge_policy"),
        metadata.get("autonomous_edge_status"),
        metadata.get("autonomous_edge_evidence"),
        meta_signal.get("edge_policy"),
        meta_signal.get("option_edge_policy"),
        meta_signal.get("autonomous_edge_status"),
        meta_signal.get("autonomous_edge_evidence"),
        meta_signal.get("strategy_edge_status"),
    )


def _parse_snapshot_ts(value: Any) -> Optional[float]:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S%z"):
        try:
            return datetime.strptime(text, fmt).timestamp()
        except Exception:
            pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _latest_live_option_snapshot_age_sec() -> Optional[float]:
    db = Path("option_chain_snapshots.db")
    if not db.exists():
        return None
    try:
        conn = sqlite3.connect(str(db))
        row = conn.execute(
            "SELECT snapshot_time,ts FROM option_chain_snapshots "
            "WHERE ok=1 AND is_live=1 AND lower(COALESCE(source,''))='angel' "
            "ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        conn.close()
    except Exception:
        return None
    if not row:
        return None
    ts = _parse_snapshot_ts(row[0]) or float(row[1] or 0.0)
    if ts <= 0:
        return None
    return max(0.0, time.time() - ts)


def _health_snapshot_ok() -> bool:
    p = Path("health_snapshot.json")
    if not p.exists():
        return False
    try:
        age = time.time() - p.stat().st_mtime
        if age > float(_cfg("LIVE_PROBATION_MAX_HEALTH_SNAPSHOT_AGE_SEC", 900) or 900):
            return False
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return False
    broker_status = raw.get("broker_status", []) if isinstance(raw, dict) else []
    return any(
        bool(row.get("connected"))
        for row in broker_status
        if isinstance(row, dict)
    )


def _system_health_ok(symbol: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Return probation-specific data/feed/Angel health.

    Tests and higher-level callers may pass explicit booleans in
    metadata["health"]. In production, option probation requires a recent live
    Angel option-chain snapshot; non-option probation can use a fresh broker
    health snapshot.
    """
    override = metadata.get("health") if isinstance(metadata.get("health"), dict) else {}
    if override:
        data_ok = bool(override.get("data", False))
        feed_ok = bool(override.get("feed", False))
        angel_ok = bool(override.get("angel", False))
        return {
            "ok": data_ok and feed_ok and angel_ok,
            "data": data_ok,
            "feed": feed_ok,
            "angel": angel_ok,
            "source": "metadata",
        }

    is_option = _is_option_symbol(symbol, {**metadata, "symbol": symbol})
    max_age = float(_cfg("LIVE_PROBATION_MAX_OPTION_SNAPSHOT_AGE_SEC", 300) or 300)
    option_age = _latest_live_option_snapshot_age_sec()
    option_ok = option_age is not None and option_age <= max_age
    health_ok = _health_snapshot_ok()
    if is_option:
        data_ok = feed_ok = angel_ok = option_ok
    else:
        data_ok = feed_ok = angel_ok = health_ok
    return {
        "ok": data_ok and feed_ok and angel_ok,
        "data": data_ok,
        "feed": feed_ok,
        "angel": angel_ok,
        "source": "option_chain_snapshots.db" if is_option else "health_snapshot.json",
        "latest_option_snapshot_age_sec": round(option_age, 3) if option_age is not None else None,
        "max_option_snapshot_age_sec": max_age,
        "health_snapshot_ok": health_ok,
    }


def _lot_capped_qty(requested_qty: int, entry_price: float, metadata: Dict[str, Any]) -> int:
    max_capital = float(_cfg("LIVE_PROBATION_MAX_CAPITAL", 500.0) or 0.0)
    max_lots = int(_cfg("LIVE_PROBATION_MAX_LOTS", 1) or 1)
    lot_size = int(metadata.get("lot_size") or _cfg("OPTION_LOT_SIZE", 65) or 1)
    lot_size = max(1, lot_size)
    requested_qty = max(0, int(requested_qty or 0))
    entry_price = float(entry_price or 0.0)
    if requested_qty <= 0 or entry_price <= 0 or max_capital <= 0:
        return 0
    by_capital = int(max_capital // entry_price)
    if _is_option_symbol(str(metadata.get("symbol", "")), metadata):
        by_lots = max_lots * lot_size
        capped = min(requested_qty, by_capital, by_lots)
        capped = (capped // lot_size) * lot_size
        return max(0, capped)
    return max(0, min(requested_qty, by_capital))


def evaluate_probation(
    *,
    symbol: str,
    strategy: str,
    requested_qty: int,
    entry_price: float,
    score: float = 0.0,
    confidence: Optional[float] = None,
    metadata: Optional[Dict[str, Any]] = None,
    state: Optional[Dict[str, Any]] = None,
) -> ProbationDecision:
    metadata = metadata if isinstance(metadata, dict) else {}
    state = state if isinstance(state, dict) else load_state()
    meta_signal = metadata.get("signal_data") if isinstance(metadata.get("signal_data"), dict) else {}
    strategy_lc = str(strategy or meta_signal.get("strategy", "") or "").lower()
    block_text = ",".join(
        str(x or "")
        for x in (
            metadata.get("live_block_reason"),
            metadata.get("strategy_live_block_reason"),
            meta_signal.get("live_block_reason"),
            meta_signal.get("strategy_live_block_reason"),
        )
    ).lower()

    max_trades = int(_cfg("LIVE_PROBATION_MAX_TRADES_PER_DAY", 1) or 0)
    max_capital = float(_cfg("LIVE_PROBATION_MAX_CAPITAL", 500.0) or 0.0)
    daily_pnl = float(state.get("daily_pnl", 0.0) or 0.0)
    max_daily_loss = float(_cfg("LIVE_PROBATION_MAX_DAILY_LOSS", max_capital) or max_capital)
    trades_today = len(state.get("trades", []) or [])

    base = {
        "max_capital": max_capital,
        "max_trades_per_day": max_trades,
        "trades_today": trades_today,
        "daily_pnl": daily_pnl,
    }
    if not bool(_cfg("LIVE_PROBATION_ENABLED", False)):
        return ProbationDecision(False, "probation_disabled", **base)
    if bool(_cfg("PAPER_TRADING", False)):
        return ProbationDecision(False, "paper_trading_enabled", **base)
    if not bool(_cfg("ENABLE_REAL_TRADING", False)):
        return ProbationDecision(False, "enable_real_trading_false", **base)
    if bool(state.get("loss_locked", False)):
        return ProbationDecision(False, "probation_loss_locked", **base)
    hard_blocks = (
        "ai_filter_block",
        "paper_only_strategy",
        "spread_too_wide",
        "stale_signal",
        "risk_blocked",
        "vix_too_high",
        "global_market_blocked",
        "sector_correlation_blocked",
        "fallback_disabled",
    )
    if any(reason in block_text for reason in hard_blocks):
        return ProbationDecision(False, "probation_hard_block_reason", **base)
    if max_trades <= 0 or trades_today >= max_trades:
        return ProbationDecision(False, "probation_daily_trade_limit", **base)
    if max_daily_loss > 0 and daily_pnl <= -abs(max_daily_loss):
        return ProbationDecision(False, "probation_daily_loss_limit", **base)
    if bool(_cfg("LIVE_PROBATION_BLOCK_HERO_ZERO", True)) and strategy_lc == "hero_zero":
        return ProbationDecision(False, "probation_blocks_hero_zero", **base)
    evidence_status = _evidence_status(metadata, meta_signal)
    allowed_statuses = _cfg_set(
        "LIVE_PROBATION_ALLOWED_STATUSES",
        ("PAPER_PROMISING", "LIVE_EVIDENCE_READY"),
    )
    if evidence_status not in allowed_statuses:
        return ProbationDecision(
            False,
            f"probation_evidence_status_{evidence_status or 'missing'}",
            **base,
        )
    if bool(_cfg("LIVE_PROBATION_REQUIRE_SYSTEM_HEALTH", True)):
        health = _system_health_ok(symbol, metadata)
        if not health.get("ok"):
            return ProbationDecision(False, "probation_system_health_not_ok", **base)
    try:
        from universe_manager import is_probation_symbol_allowed
        source_symbol = str(metadata.get("source_symbol") or meta_signal.get("symbol") or "")
        if not is_probation_symbol_allowed(symbol, source_symbol=source_symbol):
            return ProbationDecision(False, "probation_symbol_not_allowed", **base)
    except Exception:
        return ProbationDecision(False, "probation_universe_unavailable", **base)

    is_option = _is_option_symbol(symbol, {**metadata, "symbol": symbol})
    if bool(_cfg("LIVE_PROBATION_OPTIONS_ONLY", False)) and not is_option:
        return ProbationDecision(False, "probation_options_only", **base)

    min_score = float(_cfg("LIVE_PROBATION_MIN_SCORE", 0.0) or 0.0)
    if min_score and float(score or 0.0) < min_score:
        return ProbationDecision(False, "probation_score_too_low", **base)
    min_conf = float(_cfg("LIVE_PROBATION_MIN_CONFIDENCE", 0.0) or 0.0)
    if min_conf and confidence is not None and float(confidence or 0.0) < min_conf:
        return ProbationDecision(False, "probation_confidence_too_low", **base)

    qty = _lot_capped_qty(requested_qty, entry_price, {**metadata, "symbol": symbol})
    if qty <= 0:
        return ProbationDecision(False, "probation_capital_cannot_fit_min_qty", **base)
    return ProbationDecision(True, "probation_allowed", live_qty=qty, mode="probation", **base)


def record_probation_entry(
    *,
    trade_id: str,
    symbol: str,
    strategy: str,
    live_qty: int,
    entry_price: float,
    decision: Optional[ProbationDecision] = None,
    path: Optional[str] = None,
) -> Dict[str, Any]:
    state = load_state(path)
    trades = state.setdefault("trades", [])
    if not any(str(t.get("trade_id", "")) == str(trade_id) for t in trades if isinstance(t, dict)):
        trades.append({
            "trade_id": str(trade_id or ""),
            "symbol": str(symbol or ""),
            "strategy": str(strategy or ""),
            "live_qty": int(live_qty or 0),
            "entry_price": float(entry_price or 0.0),
            "ts": time.time(),
            "decision": decision.to_dict() if decision else {},
        })
    save_state(state, path)
    return state


def record_probation_exit(
    trade_id: str,
    *,
    pnl: float,
    path: Optional[str] = None,
) -> Dict[str, Any]:
    state = load_state(path)
    pnl = float(pnl or 0.0)
    state["daily_pnl"] = round(float(state.get("daily_pnl", 0.0) or 0.0) + pnl, 2)
    if pnl < 0:
        state["loss_locked"] = True
    for trade in state.get("trades", []) or []:
        if isinstance(trade, dict) and str(trade.get("trade_id", "")) == str(trade_id):
            trade["closed"] = True
            trade["pnl"] = pnl
            trade["exit_ts"] = time.time()
    save_state(state, path)
    return state


def get_probation_status(path: Optional[str] = None) -> Dict[str, Any]:
    state = load_state(path)
    return {
        "enabled": bool(_cfg("LIVE_PROBATION_ENABLED", False)),
        "max_trades_per_day": int(_cfg("LIVE_PROBATION_MAX_TRADES_PER_DAY", 1) or 0),
        "max_capital": float(_cfg("LIVE_PROBATION_MAX_CAPITAL", 500.0) or 0.0),
        "max_lots": int(_cfg("LIVE_PROBATION_MAX_LOTS", 1) or 0),
        "allowed_statuses": sorted(_cfg_set(
            "LIVE_PROBATION_ALLOWED_STATUSES",
            ("PAPER_PROMISING", "LIVE_EVIDENCE_READY"),
        )),
        "require_system_health": bool(_cfg("LIVE_PROBATION_REQUIRE_SYSTEM_HEALTH", True)),
        "daily_pnl": float(state.get("daily_pnl", 0.0) or 0.0),
        "trades_today": len(state.get("trades", []) or []),
        "loss_locked": bool(state.get("loss_locked", False)),
        "state_file": str(_state_path()),
    }


def probation_preflight(
    *,
    sample_option_premium: float = 20.0,
    sample_equity_price: float = 500.0,
) -> Dict[str, Any]:
    """Static config sanity check for probation-only live mode."""
    enabled = bool(_cfg("LIVE_PROBATION_ENABLED", False))
    paper = bool(_cfg("PAPER_TRADING", False))
    real = bool(_cfg("ENABLE_REAL_TRADING", False))
    max_capital = float(_cfg("LIVE_PROBATION_MAX_CAPITAL", 500.0) or 0.0)
    lot_size = int(_cfg("OPTION_LOT_SIZE", 65) or 1)
    max_lots = int(_cfg("LIVE_PROBATION_MAX_LOTS", 1) or 0)
    option_min_cost = float(sample_option_premium or 0.0) * max(1, lot_size)
    equity_min_cost = float(sample_equity_price or 0.0)

    warnings = []
    if not enabled:
        warnings.append("probation_disabled")
    if paper:
        warnings.append("paper_trading_must_be_false_for_live_probation")
    if not real:
        warnings.append("enable_real_trading_must_be_true_for_live_probation")
    if max_lots <= 0:
        warnings.append("max_lots_must_be_positive")
    if max_capital <= 0:
        warnings.append("max_capital_must_be_positive")
    if max_capital > 0 and option_min_cost > max_capital:
        warnings.append("max_capital_cannot_fit_sample_option_lot")
    if max_capital > 0 and equity_min_cost > max_capital:
        warnings.append("max_capital_cannot_fit_sample_equity_share")

    return {
        "enabled": enabled,
        "paper_trading": paper,
        "enable_real_trading": real,
        "max_capital": max_capital,
        "max_lots": max_lots,
        "option_lot_size": lot_size,
        "sample_option_premium": float(sample_option_premium or 0.0),
        "sample_option_min_cost": round(option_min_cost, 2),
        "sample_equity_price": float(sample_equity_price or 0.0),
        "sample_equity_min_cost": round(equity_min_cost, 2),
        "warnings": warnings,
        "ok": enabled and real and not paper and not warnings,
    }
