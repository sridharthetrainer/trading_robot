"""Immutable contract shared by option backtest, shadow, paper and live phases."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Any, Dict, Iterable, Tuple


VALID_PHASES = {"backtest", "shadow", "paper", "live"}
VALID_SIDES = {"BUY", "SELL"}
VALID_TYPES = {"CE", "PE"}
VALID_ORDERS = {"LIMIT", "MARKET", "SMART_LIMIT"}


@dataclass(frozen=True)
class OptionLegContract:
    option_type: str
    side: str
    quantity_lots: int
    expiry_rule: str
    strike_rule: str
    order_type: str = "SMART_LIMIT"

    def validate(self) -> Tuple[str, ...]:
        errors = []
        if self.option_type.upper() not in VALID_TYPES:
            errors.append("invalid_option_type")
        if self.side.upper() not in VALID_SIDES:
            errors.append("invalid_side")
        if int(self.quantity_lots) <= 0:
            errors.append("invalid_quantity_lots")
        if not str(self.expiry_rule).strip():
            errors.append("missing_expiry_rule")
        if not str(self.strike_rule).strip():
            errors.append("missing_strike_rule")
        if self.order_type.upper() not in VALID_ORDERS:
            errors.append("invalid_order_type")
        return tuple(errors)


@dataclass(frozen=True)
class OptionStrategyContract:
    strategy_id: str
    version: int
    underlying: str
    entry_rule: str
    exit_rule: str
    max_holding_minutes: int
    max_loss_rupees: float
    legs: Tuple[OptionLegContract, ...]
    adjustment_rules: Tuple[str, ...] = field(default_factory=tuple)
    hypothesis_id: str = ""
    research_cutoff: str = ""

    def canonical_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["legs"] = [asdict(leg) for leg in self.legs]
        return payload

    @property
    def contract_hash(self) -> str:
        raw = json.dumps(self.canonical_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def validate(self) -> Tuple[str, ...]:
        errors = []
        if not self.strategy_id.strip():
            errors.append("missing_strategy_id")
        if self.version <= 0:
            errors.append("invalid_version")
        if not self.underlying.strip():
            errors.append("missing_underlying")
        if not self.entry_rule.strip() or not self.exit_rule.strip():
            errors.append("missing_entry_or_exit_rule")
        if self.max_holding_minutes <= 0:
            errors.append("invalid_max_holding_minutes")
        if self.max_loss_rupees <= 0:
            errors.append("invalid_max_loss_rupees")
        if not self.legs:
            errors.append("missing_legs")
        for index, leg in enumerate(self.legs):
            errors.extend(f"leg_{index}:{error}" for error in leg.validate())
        return tuple(errors)


def contract_from_dict(data: Dict[str, Any]) -> OptionStrategyContract:
    legs = tuple(OptionLegContract(**leg) for leg in data.get("legs", []))
    return OptionStrategyContract(
        strategy_id=str(data.get("strategy_id", "")),
        version=int(data.get("version", 0) or 0),
        underlying=str(data.get("underlying", "")),
        entry_rule=str(data.get("entry_rule", "")),
        exit_rule=str(data.get("exit_rule", "")),
        max_holding_minutes=int(data.get("max_holding_minutes", 0) or 0),
        max_loss_rupees=float(data.get("max_loss_rupees", 0) or 0),
        legs=legs,
        adjustment_rules=tuple(data.get("adjustment_rules", ()) or ()),
        hypothesis_id=str(data.get("hypothesis_id", "")),
        research_cutoff=str(data.get("research_cutoff", "")),
    )


def assert_phase_parity(phase_hashes: Dict[str, str]) -> None:
    """Fail closed when backtest/shadow/paper/live do not use one contract."""
    supplied = {str(k).lower(): str(v) for k, v in phase_hashes.items() if v}
    unknown = set(supplied) - VALID_PHASES
    if unknown:
        raise ValueError(f"unknown phases: {sorted(unknown)}")
    if len(set(supplied.values())) > 1:
        raise ValueError("option strategy contract drift across phases")


def catalog_fingerprint(contracts: Iterable[OptionStrategyContract]) -> str:
    rows = sorted((c.strategy_id, c.version, c.contract_hash) for c in contracts)
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()
