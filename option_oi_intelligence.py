"""
option_oi_intelligence.py

OI-based directional intelligence for options.

Fixes applied
-------------
Column name mismatch with option_chain_fetcher.py output.

OptionOIIntelligence.analyze() required columns:
    "strike", "CE_OI", "PE_OI", "CE_CHG_OI", "PE_CHG_OI"

option_chain_fetcher.py builds DataFrames with columns:
    "strikePrice", "CE_openInterest", "PE_openInterest",
    "CE_changeinOpenInterest", "PE_changeinOpenInterest"

Result: `required - set(df.columns)` always found all 5 columns
missing, so analyze() logged a warning and returned None on every call.
The OI bias feature was completely non-functional.

Fix: _normalize_columns() is called at the top of analyze() and maps
both naming conventions to the internal names the rest of the method
uses. Both naming schemes (and lowercase variants) are handled so
the class works with data from any source.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Column normalization map
# Maps alternative column names → internal names used throughout this module
# ---------------------------------------------------------------------------
_COL_RENAMES = {
    # strikePrice → strike
    "strikeprice":              "strike",
    "strike_price":             "strike",

    # CE OI
    "ce_openinterest":          "CE_OI",
    "ce_oi":                    "CE_OI",

    # PE OI
    "pe_openinterest":          "PE_OI",
    "pe_oi":                    "PE_OI",

    # CE change OI
    "ce_changeinopeninterest":  "CE_CHG_OI",
    "ce_chg_oi":                "CE_CHG_OI",

    # PE change OI
    "pe_changeinopeninterest":  "PE_CHG_OI",
    "pe_chg_oi":                "PE_CHG_OI",
}


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Rename columns to internal names, accepting both naming conventions:
    - option_chain_fetcher.py: strikePrice, CE_openInterest, etc.
    - internal/legacy:         strike, CE_OI, etc.
    """
    rename = {}
    for col in df.columns:
        key = str(col).strip().replace(" ", "").replace("-", "_").lower()
        if key in _COL_RENAMES:
            rename[col] = _COL_RENAMES[key]
    if rename:
        df = df.rename(columns=rename)
    return df


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class OIInsight:
    symbol:                str
    expiry:                str
    spot:                  float
    atm_strike:            float
    pcr_oi:                float
    pcr_change_oi:         float
    total_call_oi:         float
    total_put_oi:          float
    total_call_change_oi:  float
    total_put_change_oi:   float
    call_wall:             Optional[float]
    put_wall:              Optional[float]
    call_buildup_strikes:  List[float]
    put_buildup_strikes:   List[float]
    call_unwinding_strikes: List[float]
    put_unwinding_strikes: List[float]
    oi_bias:               str
    confidence:            float


class OptionOIIntelligence:
    def __init__(
        self,
        symbol:                   str = "NIFTY",
        strike_window_each_side:  int = 3,
    ) -> None:
        self.symbol                  = symbol.upper()
        self.strike_window_each_side = strike_window_each_side

    def analyze(
        self,
        df:     pd.DataFrame,
        spot:   float,
        expiry: str,
    ) -> Optional[OIInsight]:
        if df is None or df.empty:
            logger.warning("OI analyze called with empty dataframe")
            return None

        # Normalize column names before checking requirements
        work = _normalize_columns(df.copy())

        required = {"strike", "CE_OI", "PE_OI", "CE_CHG_OI", "PE_CHG_OI"}
        missing  = required - set(work.columns)
        if missing:
            logger.warning("OI intelligence missing columns: %s", sorted(missing))
            return None

        numeric_cols = ["strike", "CE_OI", "PE_OI", "CE_CHG_OI", "PE_CHG_OI"]
        for col in numeric_cols:
            work[col] = pd.to_numeric(work[col], errors="coerce")

        work = work.dropna(subset=["strike"])
        if work.empty:
            logger.warning("OI analyze: no valid strike rows")
            return None

        atm = min(work["strike"].tolist(), key=lambda x: abs(float(x) - float(spot)))

        strikes_sorted = sorted(work["strike"].unique().tolist())
        atm_idx        = strikes_sorted.index(atm)

        lo = max(0, atm_idx - self.strike_window_each_side)
        hi = min(len(strikes_sorted), atm_idx + self.strike_window_each_side + 1)
        selected_strikes = strikes_sorted[lo:hi]

        work = work[work["strike"].isin(selected_strikes)].copy()
        if work.empty:
            logger.warning("OI analyze: strike window became empty")
            return None

        total_call_oi        = float(work["CE_OI"].fillna(0).sum())
        total_put_oi         = float(work["PE_OI"].fillna(0).sum())
        total_call_change_oi = float(work["CE_CHG_OI"].fillna(0).sum())
        total_put_change_oi  = float(work["PE_CHG_OI"].fillna(0).sum())

        pcr_oi        = self._safe_ratio(total_put_oi,        total_call_oi)
        pcr_change_oi = self._safe_ratio(total_put_change_oi, total_call_change_oi)

        call_wall = self._top_strike(work, "CE_OI")
        put_wall  = self._top_strike(work, "PE_OI")

        call_buildup = self._strikes_by_condition(work, side="CE", mode="buildup")
        put_buildup  = self._strikes_by_condition(work, side="PE", mode="buildup")
        call_unwind  = self._strikes_by_condition(work, side="CE", mode="unwind")
        put_unwind   = self._strikes_by_condition(work, side="PE", mode="unwind")

        oi_bias, confidence = self._derive_bias(
            pcr_oi              = pcr_oi,
            pcr_change_oi       = pcr_change_oi,
            call_buildup_count  = len(call_buildup),
            put_buildup_count   = len(put_buildup),
            call_unwind_count   = len(call_unwind),
            put_unwind_count    = len(put_unwind),
            spot                = float(spot),
            call_wall           = call_wall,
            put_wall            = put_wall,
        )

        insight = OIInsight(
            symbol                 = self.symbol,
            expiry                 = str(expiry),
            spot                   = float(spot),
            atm_strike             = float(atm),
            pcr_oi                 = round(pcr_oi,        4),
            pcr_change_oi          = round(pcr_change_oi, 4),
            total_call_oi          = round(total_call_oi,        2),
            total_put_oi           = round(total_put_oi,         2),
            total_call_change_oi   = round(total_call_change_oi, 2),
            total_put_change_oi    = round(total_put_change_oi,  2),
            call_wall              = call_wall,
            put_wall               = put_wall,
            call_buildup_strikes   = call_buildup,
            put_buildup_strikes    = put_buildup,
            call_unwinding_strikes = call_unwind,
            put_unwinding_strikes  = put_unwind,
            oi_bias                = oi_bias,
            confidence             = round(confidence, 2),
        )

        logger.info("OI insight | %s spot=%.0f atm=%.0f bias=%s conf=%.2f",
                    self.symbol, spot, atm, oi_bias, confidence)
        return insight

    def to_signal(self, insight: OIInsight) -> Optional[Dict]:
        if insight is None:
            return None

        signal = None
        reason = []

        if insight.oi_bias == "BULLISH":
            signal = "BUY_CALL"
            reason.append("OI bullish bias")
        elif insight.oi_bias == "BEARISH":
            signal = "BUY_PUT"
            reason.append("OI bearish bias")
        else:
            logger.info("OI bias neutral, no signal")
            return None

        if signal == "BUY_CALL" and insight.pcr_oi > 1.05:
            reason.append("PCR OI supportive")
        if signal == "BUY_PUT"  and insight.pcr_oi < 0.95:
            reason.append("PCR OI supportive")

        if signal == "BUY_CALL" and insight.put_wall  is not None and insight.spot >= insight.put_wall:
            reason.append("spot above put wall")
        if signal == "BUY_PUT"  and insight.call_wall is not None and insight.spot <= insight.call_wall:
            reason.append("spot below call wall")

        return {
            "style":     "scalping",
            "signal":    signal,
            "use_otm":   False,
            "reason":    " | ".join(reason),
            "confidence": float(insight.confidence),
            "expiry":    insight.expiry,
            "spot":      float(insight.spot),
            "atm_strike": float(insight.atm_strike),
            "pcr_oi":    float(insight.pcr_oi),
            "pcr_change_oi": float(insight.pcr_change_oi),
            "oi_bias":   insight.oi_bias,
            "flow": {
                "call_score": round(max(0.0, 1.0 - insight.pcr_oi), 2),
                "put_score":  round(max(0.0, insight.pcr_oi), 2),
                "imbalance":  round(insight.pcr_oi - 1.0, 2),
            },
            "delta_imbalance": {
                "direction":      signal,
                "intensity":      float(insight.confidence),
                "call_aggression": round(max(0.0, 1.0 - insight.pcr_oi), 2),
                "put_aggression":  round(max(0.0, insight.pcr_oi), 2),
                "imbalance_ratio": float(insight.pcr_oi),
            },
            "totals": asdict(insight),
            "walls":  {"call_wall": insight.call_wall, "put_wall": insight.put_wall},
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _top_strike(self, df: pd.DataFrame, col: str) -> Optional[float]:
        if df.empty or col not in df.columns:
            return None
        idx = df[col].fillna(0).idxmax()
        return float(df.loc[idx, "strike"])

    def _strikes_by_condition(
        self, df: pd.DataFrame, side: str, mode: str
    ) -> List[float]:
        oi_col  = f"{side}_OI"
        chg_col = f"{side}_CHG_OI"
        if mode == "buildup":
            subset = df[(df[oi_col] > 0) & (df[chg_col] > 0)]
        else:
            subset = df[(df[oi_col] > 0) & (df[chg_col] < 0)]
        return sorted(subset["strike"].astype(float).tolist())

    def _derive_bias(
        self,
        pcr_oi:             float,
        pcr_change_oi:      float,
        call_buildup_count: int,
        put_buildup_count:  int,
        call_unwind_count:  int,
        put_unwind_count:   int,
        spot:               float,
        call_wall:          Optional[float],
        put_wall:           Optional[float],
    ):
        bull = bear = 0.0

        if pcr_oi > 1.10:   bull += 2.0
        elif pcr_oi > 1.00: bull += 1.0
        elif pcr_oi < 0.90: bear += 2.0
        elif pcr_oi < 1.00: bear += 1.0

        if pcr_change_oi > 1.05:   bull += 2.0
        elif pcr_change_oi < 0.95: bear += 2.0

        if put_buildup_count  > call_buildup_count: bull += 1.0
        elif call_buildup_count > put_buildup_count: bear += 1.0

        if call_unwind_count > put_unwind_count:  bull += 0.5
        elif put_unwind_count > call_unwind_count: bear += 0.5

        if put_wall  is not None and spot >= put_wall:  bull += 0.5
        if call_wall is not None and spot <= call_wall: bear += 0.5

        if bull - bear >= 1.0:
            return "BULLISH", min(0.90, 0.55 + 0.08 * (bull - bear))
        if bear - bull >= 1.0:
            return "BEARISH", min(0.90, 0.55 + 0.08 * (bear - bull))
        return "NEUTRAL", 0.50

    @staticmethod
    def _safe_ratio(a: float, b: float) -> float:
        return 0.0 if b in (0, 0.0) else float(a) / float(b)
