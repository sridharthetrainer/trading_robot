from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)


# =============================================================================
# DATA MODELS
# =============================================================================

@dataclass
class StrikeMetrics:
    strike: float
    call_oi: float
    put_oi: float
    call_change_oi: float
    put_change_oi: float
    call_volume: float
    put_volume: float
    call_ltp: float
    put_ltp: float
    call_iv: float
    put_iv: float
    call_gamma: float
    put_gamma: float


@dataclass
class OptionChainSummary:
    underlying: str
    spot: float
    atm_strike: float
    pcr_oi: float
    pcr_change_oi: float
    pcr_volume: float

    bullish_score: float
    bearish_score: float
    net_bias: str                 # BULLISH / BEARISH / NEUTRAL
    regime: str                   # BULLISH_TREND / BEARISH_TREND / SIDEWAYS / UNCLEAR
    signal_strength: float        # abs(bullish - bearish)

    oi_buildup_signal: str

    call_wall: Optional[float]
    put_wall: Optional[float]
    gamma_support: Optional[float]
    gamma_resistance: Optional[float]

    call_unwinding_strikes: List[float]
    put_unwinding_strikes: List[float]
    call_buildup_strikes: List[float]
    put_buildup_strikes: List[float]

    strongest_call_volume_strike: Optional[float]
    strongest_put_volume_strike: Optional[float]
    strongest_call_gamma_strike: Optional[float]
    strongest_put_gamma_strike: Optional[float]

    directional_score: float      # 0 to 5
    confidence: float             # 0 to 1


# =============================================================================
# ENGINE
# =============================================================================

class OptionChainIntelligence:
    """
    Expects a DataFrame with these columns:

    Required:
        strikePrice
        CE_openInterest
        PE_openInterest
        CE_changeinOpenInterest
        PE_changeinOpenInterest

    Optional but recommended:
        CE_totalTradedVolume
        PE_totalTradedVolume
        CE_lastPrice
        PE_lastPrice
        CE_impliedVolatility
        PE_impliedVolatility
        CE_gamma
        PE_gamma
    """

    def __init__(self, underlying: str = "NIFTY", strike_window: int = 10):
        self.underlying = underlying.upper()
        self.strike_window = int(strike_window)

    # -------------------------------------------------------------------------
    # PUBLIC
    # -------------------------------------------------------------------------
    def analyze(
        self,
        option_chain_df: pd.DataFrame,
        spot_price: float,
    ) -> OptionChainSummary:
        df = self._prepare(option_chain_df)

        if df.empty:
            raise ValueError("Option chain dataframe is empty after preparation")

        atm_strike = self._find_atm_strike(df, spot_price)
        df_window = self._window_around_atm(df, atm_strike, self.strike_window)

        pcr_oi = self._safe_ratio(
            df_window["PE_openInterest"].sum(),
            df_window["CE_openInterest"].sum(),
        )
        pcr_change_oi = self._safe_ratio(
            df_window["PE_changeinOpenInterest"].sum(),
            df_window["CE_changeinOpenInterest"].sum(),
        )
        pcr_volume = self._safe_ratio(
            df_window["PE_totalTradedVolume"].sum(),
            df_window["CE_totalTradedVolume"].sum(),
        )

        call_wall = self._top_strike_by(df_window, "CE_openInterest")
        put_wall = self._top_strike_by(df_window, "PE_openInterest")
        gamma_support = self._top_strike_by(df_window, "PE_gamma")
        gamma_resistance = self._top_strike_by(df_window, "CE_gamma")

        call_buildup_strikes = self._buildup_strikes(df_window, side="CE")
        put_buildup_strikes = self._buildup_strikes(df_window, side="PE")
        call_unwinding_strikes = self._unwinding_strikes(df_window, side="CE")
        put_unwinding_strikes = self._unwinding_strikes(df_window, side="PE")

        strongest_call_volume_strike = self._top_strike_by(df_window, "CE_totalTradedVolume")
        strongest_put_volume_strike = self._top_strike_by(df_window, "PE_totalTradedVolume")
        strongest_call_gamma_strike = self._top_strike_by(df_window, "CE_gamma")
        strongest_put_gamma_strike = self._top_strike_by(df_window, "PE_gamma")

        bullish_score, bearish_score, net_bias = self._compute_bias_scores(
            df_window=df_window,
            spot_price=spot_price,
            atm_strike=atm_strike,
            pcr_oi=pcr_oi,
            pcr_change_oi=pcr_change_oi,
            pcr_volume=pcr_volume,
            call_wall=call_wall,
            put_wall=put_wall,
            gamma_support=gamma_support,
            gamma_resistance=gamma_resistance,
        )

        regime = self._detect_regime(
            pcr_oi=pcr_oi,
            pcr_change_oi=pcr_change_oi,
            bullish_score=bullish_score,
            bearish_score=bearish_score,
        )

        signal_strength = abs(bullish_score - bearish_score)

        oi_buildup_signal = self._detect_oi_buildup_signal(
            pcr_change_oi=pcr_change_oi,
            call_buildup_count=len(call_buildup_strikes),
            put_buildup_count=len(put_buildup_strikes),
            call_unwind_count=len(call_unwinding_strikes),
            put_unwind_count=len(put_unwinding_strikes),
        )

        directional_score, confidence = self._compute_trade_quality(
            summary_bias=net_bias,
            regime=regime,
            spot=float(spot_price),
            pcr_oi=float(pcr_oi),
            pcr_change_oi=float(pcr_change_oi),
            pcr_volume=float(pcr_volume),
            call_wall=call_wall,
            put_wall=put_wall,
            gamma_support=gamma_support,
            gamma_resistance=gamma_resistance,
            bullish_score=float(bullish_score),
            bearish_score=float(bearish_score),
            call_unwinding_count=len(call_unwinding_strikes),
            put_unwinding_count=len(put_unwinding_strikes),
            call_buildup_count=len(call_buildup_strikes),
            put_buildup_count=len(put_buildup_strikes),
        )

        return OptionChainSummary(
            underlying=self.underlying,
            spot=float(spot_price),
            atm_strike=float(atm_strike),
            pcr_oi=float(pcr_oi),
            pcr_change_oi=float(pcr_change_oi),
            pcr_volume=float(pcr_volume),
            bullish_score=float(bullish_score),
            bearish_score=float(bearish_score),
            net_bias=net_bias,
            regime=regime,
            signal_strength=float(signal_strength),
            oi_buildup_signal=oi_buildup_signal,
            call_wall=call_wall,
            put_wall=put_wall,
            gamma_support=gamma_support,
            gamma_resistance=gamma_resistance,
            call_unwinding_strikes=call_unwinding_strikes,
            put_unwinding_strikes=put_unwinding_strikes,
            call_buildup_strikes=call_buildup_strikes,
            put_buildup_strikes=put_buildup_strikes,
            strongest_call_volume_strike=strongest_call_volume_strike,
            strongest_put_volume_strike=strongest_put_volume_strike,
            strongest_call_gamma_strike=strongest_call_gamma_strike,
            strongest_put_gamma_strike=strongest_put_gamma_strike,
            directional_score=float(directional_score),
            confidence=float(confidence),
        )

    def build_trade_signal(
        self,
        summary: OptionChainSummary,
    ) -> Optional[Dict]:
        """
        Converts option-chain intelligence into a stricter bot-ready signal.
        """

        if summary.regime in ("SIDEWAYS", "UNCLEAR"):
            logger.info(
                "Skipping signal: weak regime | regime=%s bias=%s strength=%.2f",
                summary.regime,
                summary.net_bias,
                summary.signal_strength,
            )
            return None

        if summary.net_bias == "BULLISH":
            signal = "BUY_CALL"
        elif summary.net_bias == "BEARISH":
            signal = "BUY_PUT"
        else:
            return None

        if summary.directional_score < 3.0:
            logger.info(
                "Skipping signal: directional_score too low | score=%.2f",
                summary.directional_score,
            )
            return None

        if summary.confidence < 0.60:
            logger.info(
                "Skipping signal: confidence too low | confidence=%.2f",
                summary.confidence,
            )
            return None

        reason_parts: List[str] = [
            f"regime={summary.regime}",
            f"net_bias={summary.net_bias}",
            f"dir_score={summary.directional_score:.2f}",
            f"conf={summary.confidence:.2f}",
        ]

        if signal == "BUY_CALL":
            if summary.pcr_oi > 1.05:
                reason_parts.append("PCR OI supportive")
            if summary.pcr_change_oi > 1.05:
                reason_parts.append("fresh put-side buildup")
            if summary.put_wall is not None and summary.spot >= summary.put_wall:
                reason_parts.append("spot above put wall")
            if summary.gamma_support is not None and summary.spot >= summary.gamma_support:
                reason_parts.append("gamma support below spot")
            if summary.call_unwinding_strikes:
                reason_parts.append("calls unwinding")
        else:
            if summary.pcr_oi < 0.95:
                reason_parts.append("PCR OI supportive")
            if summary.pcr_change_oi < 0.95:
                reason_parts.append("fresh call-side buildup")
            if summary.call_wall is not None and summary.spot <= summary.call_wall:
                reason_parts.append("spot below call wall")
            if summary.gamma_resistance is not None and summary.spot <= summary.gamma_resistance:
                reason_parts.append("gamma resistance above spot")
            if summary.put_unwinding_strikes:
                reason_parts.append("puts unwinding")

        use_otm = summary.confidence >= 0.80

        return {
            "style": "swing" if summary.confidence >= 0.80 else "scalping",
            "signal": signal,
            "use_otm": use_otm,
            "reason": " | ".join(reason_parts),
            "confidence": round(min(summary.confidence, 0.95), 2),
            "expiry": None,
            "spot": round(summary.spot, 2),
            "atm_strike": round(summary.atm_strike, 2),
            "pcr_oi": round(summary.pcr_oi, 4),
            "pcr_change_oi": round(summary.pcr_change_oi, 4),
            "flow": {
                "call_score": round(summary.bearish_score, 2),
                "put_score": round(summary.bullish_score, 2),
                "imbalance": round(summary.bullish_score - summary.bearish_score, 2),
            },
            "delta_imbalance": {
                "direction": summary.net_bias,
                "intensity": round(min(summary.signal_strength / 4.0, 1.0), 2),
                "call_aggression": round(summary.bearish_score, 2),
                "put_aggression": round(summary.bullish_score, 2),
                "imbalance_ratio": round(
                    self._safe_ratio(summary.bullish_score, max(summary.bearish_score, 0.01)),
                    2,
                ),
            },
            "totals": {
                "oi_buildup_signal": summary.oi_buildup_signal,
                "pcr_volume": round(summary.pcr_volume, 4),
                "regime": summary.regime,
                "directional_score": round(summary.directional_score, 2),
                "signal_strength": round(summary.signal_strength, 2),
            },
            "walls": {
                "call_wall": summary.call_wall,
                "put_wall": summary.put_wall,
                "gamma_support": summary.gamma_support,
                "gamma_resistance": summary.gamma_resistance,
            },
        }

    # -------------------------------------------------------------------------
    # PREP
    # -------------------------------------------------------------------------
    def _prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        working = df.copy()

        rename_map = {
            "strikeprice": "strikePrice",
            "ce_openinterest": "CE_openInterest",
            "pe_openinterest": "PE_openInterest",
            "ce_changeinopeninterest": "CE_changeinOpenInterest",
            "pe_changeinopeninterest": "PE_changeinOpenInterest",
            "ce_totaltradedvolume": "CE_totalTradedVolume",
            "pe_totaltradedvolume": "PE_totalTradedVolume",
            "ce_lastprice": "CE_lastPrice",
            "pe_lastprice": "PE_lastPrice",
            "ce_impliedvolatility": "CE_impliedVolatility",
            "pe_impliedvolatility": "PE_impliedVolatility",
            "ce_gamma": "CE_gamma",
            "pe_gamma": "PE_gamma",
        }

        normalized_columns = {}
        for col in working.columns:
            key = str(col).strip().replace(" ", "").replace("-", "_").lower()
            normalized_columns[col] = rename_map.get(key, col)

        working = working.rename(columns=normalized_columns)

        required = [
            "strikePrice",
            "CE_openInterest",
            "PE_openInterest",
            "CE_changeinOpenInterest",
            "PE_changeinOpenInterest",
        ]

        for col in required:
            if col not in working.columns:
                raise ValueError(f"Missing required column: {col}")

        optional_defaults = {
            "CE_totalTradedVolume": 0.0,
            "PE_totalTradedVolume": 0.0,
            "CE_lastPrice": 0.0,
            "PE_lastPrice": 0.0,
            "CE_impliedVolatility": 0.0,
            "PE_impliedVolatility": 0.0,
            "CE_gamma": 0.0,
            "PE_gamma": 0.0,
        }

        for col, default in optional_defaults.items():
            if col not in working.columns:
                working[col] = default

        numeric_cols = [
            "strikePrice",
            "CE_openInterest",
            "PE_openInterest",
            "CE_changeinOpenInterest",
            "PE_changeinOpenInterest",
            "CE_totalTradedVolume",
            "PE_totalTradedVolume",
            "CE_lastPrice",
            "PE_lastPrice",
            "CE_impliedVolatility",
            "PE_impliedVolatility",
            "CE_gamma",
            "PE_gamma",
        ]

        for col in numeric_cols:
            working[col] = pd.to_numeric(working[col], errors="coerce").fillna(0.0)

        working = working.sort_values("strikePrice").reset_index(drop=True)
        return working

    def _find_atm_strike(self, df: pd.DataFrame, spot_price: float) -> float:
        idx = (df["strikePrice"] - float(spot_price)).abs().idxmin()
        return float(df.loc[idx, "strikePrice"])

    def _window_around_atm(self, df: pd.DataFrame, atm_strike: float, window: int) -> pd.DataFrame:
        atm_idx = (df["strikePrice"] - float(atm_strike)).abs().idxmin()
        start = max(0, atm_idx - window)
        end = min(len(df), atm_idx + window + 1)
        return df.iloc[start:end].copy()

    # -------------------------------------------------------------------------
    # METRICS
    # -------------------------------------------------------------------------
    def _top_strike_by(self, df: pd.DataFrame, column: str) -> Optional[float]:
        if column not in df.columns or df.empty:
            return None
        idx = df[column].idxmax()
        if pd.isna(idx):
            return None
        return float(df.loc[idx, "strikePrice"])

    def _buildup_strikes(self, df: pd.DataFrame, side: str) -> List[float]:
        oi_col = f"{side}_changeinOpenInterest"
        ltp_col = f"{side}_lastPrice"
        if oi_col not in df.columns or ltp_col not in df.columns:
            return []
        subset = df[(df[oi_col] > 0) & (df[ltp_col] > 0)]
        return subset["strikePrice"].astype(float).tolist()[:5]

    def _unwinding_strikes(self, df: pd.DataFrame, side: str) -> List[float]:
        oi_col = f"{side}_changeinOpenInterest"
        ltp_col = f"{side}_lastPrice"
        if oi_col not in df.columns or ltp_col not in df.columns:
            return []
        subset = df[(df[oi_col] < 0) & (df[ltp_col] > 0)]
        return subset["strikePrice"].astype(float).tolist()[:5]

    def _detect_oi_buildup_signal(
        self,
        pcr_change_oi: float,
        call_buildup_count: int,
        put_buildup_count: int,
        call_unwind_count: int,
        put_unwind_count: int,
    ) -> str:
        if pcr_change_oi > 1.10 and put_buildup_count >= call_buildup_count:
            return "BULLISH_BUILDUP"
        if pcr_change_oi < 0.90 and call_buildup_count >= put_buildup_count:
            return "BEARISH_BUILDUP"
        if call_unwind_count > put_unwind_count:
            return "CALL_UNWINDING"
        if put_unwind_count > call_unwind_count:
            return "PUT_UNWINDING"
        return "NEUTRAL"

    def _compute_bias_scores(
        self,
        df_window: pd.DataFrame,
        spot_price: float,
        atm_strike: float,
        pcr_oi: float,
        pcr_change_oi: float,
        pcr_volume: float,
        call_wall: Optional[float],
        put_wall: Optional[float],
        gamma_support: Optional[float],
        gamma_resistance: Optional[float],
    ) -> Tuple[float, float, str]:
        bullish_score = 0.0
        bearish_score = 0.0

        # PCR OI
        if pcr_oi >= 1.20:
            bullish_score += 2.0
        elif pcr_oi >= 1.05:
            bullish_score += 1.0
        elif pcr_oi <= 0.80:
            bearish_score += 2.0
        elif pcr_oi <= 0.95:
            bearish_score += 1.0

        # PCR Change OI
        if pcr_change_oi >= 1.15:
            bullish_score += 2.0
        elif pcr_change_oi >= 1.00:
            bullish_score += 1.0
        elif pcr_change_oi <= 0.85:
            bearish_score += 2.0
        elif pcr_change_oi <= 1.00:
            bearish_score += 1.0

        # Volume
        if pcr_volume >= 1.10:
            bullish_score += 1.0
        elif pcr_volume <= 0.90:
            bearish_score += 1.0

        # Walls
        if put_wall is not None and spot_price >= put_wall:
            bullish_score += 1.0
        elif put_wall is not None and abs(spot_price - put_wall) <= 25:
            bullish_score += 0.5

        if call_wall is not None and spot_price <= call_wall:
            bearish_score += 1.0
        elif call_wall is not None and abs(spot_price - call_wall) <= 25:
            bearish_score += 0.5

        # Gamma zones
        if gamma_support is not None and spot_price >= gamma_support:
            bullish_score += 1.0
        if gamma_resistance is not None and spot_price <= gamma_resistance:
            bearish_score += 1.0

        # ATM pressure
        atm_row = df_window.loc[(df_window["strikePrice"] - atm_strike).abs().idxmin()]

        if atm_row["PE_changeinOpenInterest"] > atm_row["CE_changeinOpenInterest"]:
            bullish_score += 1.0
        elif atm_row["CE_changeinOpenInterest"] > atm_row["PE_changeinOpenInterest"]:
            bearish_score += 1.0

        if bullish_score - bearish_score >= 1.5:
            net_bias = "BULLISH"
        elif bearish_score - bullish_score >= 1.5:
            net_bias = "BEARISH"
        else:
            net_bias = "NEUTRAL"

        return bullish_score, bearish_score, net_bias

    def _detect_regime(
        self,
        pcr_oi: float,
        pcr_change_oi: float,
        bullish_score: float,
        bearish_score: float,
    ) -> str:
        imbalance = bullish_score - bearish_score
        strength = abs(imbalance)

        if strength < 1.25:
            return "SIDEWAYS"

        if pcr_change_oi >= 1.08 and pcr_oi >= 1.00 and imbalance > 0:
            return "BULLISH_TREND"

        if pcr_change_oi <= 0.92 and pcr_oi <= 1.00 and imbalance < 0:
            return "BEARISH_TREND"

        if strength >= 2.0:
            return "UNCLEAR"

        return "SIDEWAYS"

    def _compute_trade_quality(
        self,
        summary_bias: str,
        regime: str,
        spot: float,
        pcr_oi: float,
        pcr_change_oi: float,
        pcr_volume: float,
        call_wall: Optional[float],
        put_wall: Optional[float],
        gamma_support: Optional[float],
        gamma_resistance: Optional[float],
        bullish_score: float,
        bearish_score: float,
        call_unwinding_count: int,
        put_unwinding_count: int,
        call_buildup_count: int,
        put_buildup_count: int,
    ) -> Tuple[float, float]:
        score = 0.0

        if summary_bias == "BULLISH":
            if regime == "BULLISH_TREND":
                score += 1.0
            if pcr_oi > 1.05:
                score += 1.0
            if pcr_change_oi > 1.05:
                score += 1.0
            if pcr_volume > 1.02:
                score += 0.5
            if put_wall is not None and spot >= put_wall:
                score += 0.75
            if gamma_support is not None and spot >= gamma_support:
                score += 0.75
            if put_buildup_count >= call_buildup_count:
                score += 0.5
            if call_unwinding_count > 0:
                score += 0.5
            if bearish_score > bullish_score:
                score -= 1.0

        elif summary_bias == "BEARISH":
            if regime == "BEARISH_TREND":
                score += 1.0
            if pcr_oi < 0.95:
                score += 1.0
            if pcr_change_oi < 0.95:
                score += 1.0
            if pcr_volume < 0.98:
                score += 0.5
            if call_wall is not None and spot <= call_wall:
                score += 0.75
            if gamma_resistance is not None and spot <= gamma_resistance:
                score += 0.75
            if call_buildup_count >= put_buildup_count:
                score += 0.5
            if put_unwinding_count > 0:
                score += 0.5
            if bullish_score > bearish_score:
                score -= 1.0

        score = max(0.0, min(score, 5.0))
        confidence = max(0.0, min(score / 5.0, 1.0))
        return score, confidence

    @staticmethod
    def _safe_ratio(a: float, b: float) -> float:
        if b in (0, 0.0):
            return 0.0
        return float(a) / float(b)


# =============================================================================
# HELPER
# =============================================================================

def option_chain_summary_to_dict(summary: OptionChainSummary) -> Dict:
    return asdict(summary)
