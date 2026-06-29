"""
option_chain_engine.py

Complete institutional option selection engine.

Replaces and extends nifty_options_engine.py with:

1. CE vs PE — Dynamic, always correct
   BUY signal  → BUY CE (bullish)
   SELL signal → BUY PE (bearish / market falling)
   Strong momentum override: if price moving > 1 ATR in 2 bars,
   buy even if option chain hasn't confirmed yet

2. Real-time ATM — Fetches LIVE spot at execution moment
   Not the stale price from the signal (which can be 30s old)
   Recalculates ATM at the moment the order is about to be placed

3. Smart expiry selection by style and DTE requirement
   Intraday:  Current week's expiry (nearest Thursday)
   Scalping:  Current week's expiry (nearest Thursday)
   Swing:     Needs DTE ≥ 5 → automatically selects next week
              if this week has DTE < 5

4. Correct lot sizes (current contracts, refreshed via NSEMaster)
   NIFTY:      65 shares per lot
   BANKNIFTY:  30 shares per lot
   FINNIFTY:   60 shares per lot
   MIDCPNIFTY: 120 shares per lot
   All updatable via config without code change

5. Momentum override for fast markets
   When price drops > 0.5 ATR in last 2 bars and option chain
   is lagging: allow PE purchase without chain confirmation
   This catches gap-down opens and fast intraday drops

6. Option chain alignment scoring
   PE purchase confirmed by option chain bearish signal: +1.5
   PE purchase with neutral chain but strong momentum: 0
   PE purchase against bullish chain: blocked (wrong direction)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, asdict, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import pandas as pd
    _PANDAS_AVAILABLE = True
except ImportError:
    pd = None  # type: ignore[assignment]
    _PANDAS_AVAILABLE = False

logger = logging.getLogger(__name__)

try:
    import config as cfg
except Exception:  # pragma: no cover - config is available in production
    cfg = None  # type: ignore[assignment]

# ── NSE lot sizes: dynamically loaded from NSEMaster, fallback to defaults ───
# These defaults are used only if NSEMaster is unavailable
NSE_LOT_SIZES: Dict[str, int] = {
    "NIFTY":      65,
    "BANKNIFTY":  30,
    "FINNIFTY":   60,
    "MIDCPNIFTY": 120,
    "SENSEX":     20,
    "BANKEX":     30,
}

try:
    from nse_master import get_nse_master as _get_nse_master
    _NSE_MASTER_AVAILABLE = True
except ImportError:
    _NSE_MASTER_AVAILABLE = False

# ── Strike intervals ──────────────────────────────────────────────────────────
STRIKE_INTERVALS: Dict[str, int] = {
    "NIFTY":      50,
    "BANKNIFTY":  100,
    "FINNIFTY":   50,
    "MIDCPNIFTY": 25,
    "SENSEX":     100,   # BSE, strike interval 100
    "BANKEX":     100,   # BSE Banking index
}

# ── NSE expiry holidays: loaded dynamically from NSEMaster ───────────────────
# Fallback used only when NSEMaster is unavailable
_FALLBACK_HOLIDAYS: Set[date] = {
    date(2025, 1, 26), date(2025, 2, 26), date(2025, 3, 14),
    date(2025, 4, 14), date(2025, 4, 18), date(2025, 5, 1),
    date(2025, 8, 15), date(2025, 8, 27), date(2025, 10, 2),
    date(2025, 10, 24), date(2025, 11, 5), date(2025, 11, 14),
    date(2025, 12, 25),
    date(2026, 1, 26), date(2026, 3, 6),  date(2026, 3, 25),
    date(2026, 4, 3),  date(2026, 4, 14), date(2026, 5, 1),
    date(2026, 8, 15), date(2026, 10, 2), date(2026, 12, 25),
}

def _is_expiry_holiday(d: date) -> bool:
    """Check if a date is an NSE holiday — uses NSEMaster if available."""
    if _NSE_MASTER_AVAILABLE:
        try:
            return _get_nse_master().is_trading_holiday(d)
        except Exception:
            pass
    return d in _FALLBACK_HOLIDAYS or d.weekday() >= 5


# ── Authoritative expiry dates from the broker master contract ─────────────────
# NSE changes expiry weekdays often (NIFTY moved Thu→Tue; most weeklies removed),
# so a hardcoded weekday rule produces non-existent contracts. The master file on
# disk lists the REAL tradeable expiries — use it as the source of truth.
_MASTER_EXPIRY_CACHE: Dict[str, Any] = {"date": None, "data": {}}


def _parse_expiry_str(e: str):
    import datetime as _dt
    e = (e or "").strip().upper()
    for fmt in ("%d%b%Y", "%d%b%y", "%Y-%m-%d", "%d-%b-%Y"):
        try:
            return _dt.datetime.strptime(e, fmt).date()
        except Exception:
            pass
    return None


def _load_master_expiries() -> Dict[str, list]:
    """{UNDERLYING: [sorted upcoming expiry dates]} from the local master file."""
    import csv as _csv, os as _os
    today = date.today()
    if _MASTER_EXPIRY_CACHE["date"] == today and _MASTER_EXPIRY_CACHE["data"]:
        return _MASTER_EXPIRY_CACHE["data"]
    targets = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"}
    data: Dict[str, set] = {}
    for fname in ("OpenAPIScripMaster.csv", "MasterContract_ALL.csv",
                  "MasterContract_NFO.csv"):
        if not _os.path.exists(fname):
            continue
        try:
            with open(fname, errors="replace") as fh:
                for row in _csv.DictReader(fh):
                    if str(row.get("instrumenttype", "")).upper() != "OPTIDX":
                        continue
                    nm = str(row.get("name", "")).upper().strip()
                    if nm not in targets:
                        continue
                    d = _parse_expiry_str(row.get("expiry", ""))
                    if d and d >= today:
                        data.setdefault(nm, set()).add(d)
            if data:
                break
        except Exception:
            continue
    out = {k: sorted(v) for k, v in data.items()}
    _MASTER_EXPIRY_CACHE["date"] = today
    _MASTER_EXPIRY_CACHE["data"] = out
    return out


# ── Minimum DTE per trade style ────────────────────────────────────────────────
MIN_DTE_BY_STYLE: Dict[str, int] = {
    "scalping":  0,   # can use 0-DTE
    "intraday":  0,   # current week is fine
    "swing":     5,   # need at least 5 days
    "position":  10,  # slower positional option trades need more theta buffer
}


@dataclass
class OptionContract:
    """A fully resolved, tradeable option contract."""
    underlying:      str
    option_type:     str    # "CE" or "PE"
    strike:          int
    expiry_date:     date
    expiry_str:      str    # formatted for Angel One API
    symbol:          str    # Angel One tradeable symbol
    lot_size:        int
    lots:            int
    quantity:        int
    premium:         float
    spot_price:      float
    dte:             int
    style:           str
    signal_side:     str    # "BUY" (underlying) or "SELL" (underlying)
    option_side:     str    # always "BUY" — we buy CE or PE
    strike_type:     str    # "ATM", "1OTM", "2OTM"
    capital_required:float
    momentum_override: bool = False  # True if chain was bypassed due to momentum
    autotune:        Dict[str, Any] = field(default_factory=dict)
    shadow_candidates: List[Dict[str, Any]] = field(default_factory=list)


class OptionChainEngine:
    """
    Complete option selection engine with CE/PE switching,
    real-time ATM, smart expiry, and correct lot sizes.
    """

    def __init__(self, broker=None) -> None:
        self._broker = broker

    # ─────────────────────────────────────────────────────────────────────────
    # MAIN PUBLIC API
    # ─────────────────────────────────────────────────────────────────────────

    def select_option(
        self,
        underlying:     str,
        signal_side:    str,          # "BUY" or "SELL" (underlying direction)
        style:          str,          # "intraday", "scalping", "swing"
        confidence:     float,
        trade_capital:  float,
        df:             Optional[pd.DataFrame] = None,
        option_chain_signal: Optional[str] = None,   # "BUY_CALL" or "BUY_PUT" from chain
        max_lots:       int   = 10,
        force_atm:      bool  = False,
    ) -> Optional[OptionContract]:
        """
        Select the correct option contract for a trade.

        Parameters
        ----------
        underlying        : "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"
        signal_side       : "BUY" = market going up → buy CE
                            "SELL" = market going down → buy PE
        style             : trade duration style
        confidence        : signal confidence 0.0-1.0
        trade_capital     : capital available for this trade
        df                : 5-min OHLCV (for momentum override check)
        option_chain_signal: option chain direction if available
        max_lots          : maximum lots allowed
        force_atm         : skip OTM selection even for low confidence
        """
        underlying = underlying.upper()
        style      = style.lower()
        signal_side= signal_side.upper()

        # ── Step 1: Determine CE or PE ────────────────────────────────────────
        option_type, momentum_override = self._decide_option_type(
            signal_side         = signal_side,
            option_chain_signal = option_chain_signal,
            df                  = df,
            underlying          = underlying,
        )
        if option_type is None:
            logger.info(
                "option_type=None | %s %s chain=%s — blocked (conflicting signals)",
                underlying, signal_side, option_chain_signal,
            )
            return None

        # ── Step 2: Fetch real-time spot price ────────────────────────────────
        spot = self._get_live_spot(underlying)
        if spot <= 0:
            logger.error("Cannot get spot price for %s", underlying)
            return None

        # ── Step 3: Select default strike ─────────────────────────────────────
        strike, strike_type = self._select_strike(
            underlying  = underlying,
            spot        = spot,
            option_type = option_type,
            confidence  = confidence,
            force_atm   = force_atm,
        )

        # ── Step 4: Select expiry by style ────────────────────────────────────
        expiry = self._select_expiry(underlying=underlying, style=style)
        dte    = (expiry - date.today()).days

        # Validate DTE meets style requirement
        min_dte = MIN_DTE_BY_STYLE.get(style, 0)
        if dte < min_dte:
            logger.warning(
                "DTE %d < required %d for %s style — trying next expiry",
                dte, min_dte, style,
            )
            expiry = self._select_expiry(underlying=underlying, style=style, skip_current=True)
            dte    = (expiry - date.today()).days

        # ── Step 5: Build and resolve tradeable symbol ────────────────────────
        tuned = self._select_tradeable_strike_with_autotune(
            underlying=underlying,
            expiry=expiry,
            option_type=option_type,
            spot=spot,
            confidence=confidence,
            default_strike=strike,
            default_strike_type=strike_type,
            force_atm=force_atm,
        )
        if not tuned:
            logger.error(
                "Cannot resolve symbol | %s %s %d %s expiry=%s",
                underlying, option_type, strike, style, expiry,
            )
            return None
        strike = int(tuned["strike"])
        strike_type = str(tuned["strike_type"])
        symbol = str(tuned["symbol"])
        premium = float(tuned["premium"])
        autotune = tuned.get("autotune", {}) if isinstance(tuned.get("autotune"), dict) else {}
        shadow_candidates = tuned.get("shadow_candidates", [])
        if not isinstance(shadow_candidates, list):
            shadow_candidates = []

        if not symbol or premium <= 0:
            logger.error(
                "Cannot resolve symbol | %s %s %d %s expiry=%s",
                underlying, option_type, strike, style, expiry,
            )
            return None

        # ── Step 6: Size the position ─────────────────────────────────────────
        lot_size = self.get_lot_size(underlying)
        lots     = self._compute_lots(
            trade_capital = trade_capital,
            premium       = premium,
            lot_size      = lot_size,
            confidence    = confidence,
            max_lots      = max_lots,
        )
        if lots <= 0:
            logger.warning(
                "0 lots for %s premium=%.2f capital=%.0f",
                underlying, premium, trade_capital,
            )
            return None

        qty              = lots * lot_size
        capital_required = round(premium * qty, 2)

        contract = OptionContract(
            underlying       = underlying,
            option_type      = option_type,
            strike           = strike,
            expiry_date      = expiry,
            expiry_str       = expiry.strftime("%d%b%y").upper(),
            symbol           = symbol,
            lot_size         = lot_size,
            lots             = lots,
            quantity         = qty,
            premium          = round(premium, 2),
            spot_price       = round(spot, 2),
            dte              = dte,
            style            = style,
            signal_side      = signal_side,
            option_side      = "BUY",
            strike_type      = strike_type,
            capital_required = capital_required,
            momentum_override= momentum_override,
            autotune         = autotune,
            shadow_candidates= shadow_candidates,
        )

        logger.info(
            "Option selected | %s %s %d %s dte=%d premium=%.2f "
            "lots=%d qty=%d capital=₹%.0f override=%s",
            underlying, option_type, strike, style, dte, premium,
            lots, qty, capital_required, momentum_override,
        )
        return contract

    # ─────────────────────────────────────────────────────────────────────────
    # CE vs PE DECISION — THE CRITICAL LOGIC
    # ─────────────────────────────────────────────────────────────────────────

    def _decide_option_type(
        self,
        signal_side:         str,
        option_chain_signal: Optional[str],
        df:                  Optional[pd.DataFrame],
        underlying:          str,
    ) -> Tuple[Optional[str], bool]:
        """
        Decide CE or PE. Returns (option_type, momentum_override).

        Rules (in priority order):
        ─────────────────────────
        1. Check for momentum override first (fast-moving market)
           If price fell > 0.8 ATR in last 2 bars AND signal is SELL:
           → Buy PE regardless of option chain (momentum_override=True)

        2. Signal and option chain AGREE:
           SELL signal + BUY_PUT chain = strong bearish → PE ✅
           BUY signal  + BUY_CALL chain = strong bullish → CE ✅

        3. Signal says direction, chain is NEUTRAL (no signal from chain):
           SELL signal, no chain signal → PE (trust price action)
           BUY signal,  no chain signal → CE (trust price action)

        4. Signal and option chain DISAGREE (conflicting):
           SELL signal + BUY_CALL chain → BLOCK (market may be reversing)
           BUY signal  + BUY_PUT chain  → BLOCK (institutions distributing)

        The momentum override at step 1 exists because on fast-falling
        days, the option chain data lags by 3-5 minutes. A SELL signal
        from our price-based strategies fires immediately while the option
        chain still shows the previous state. We don't want to miss
        genuine downmoves just because the chain hasn't caught up yet.
        """
        # Step 1: Momentum override
        if signal_side == "SELL" and df is not None and len(df) >= 3:
            momentum_override = self._check_bearish_momentum(df, underlying)
            if momentum_override:
                logger.info(
                    "MOMENTUM OVERRIDE: strong bearish move → buying PE without chain confirmation"
                )
                return "PE", True

        if signal_side == "BUY" and df is not None and len(df) >= 3:
            momentum_override = self._check_bullish_momentum(df, underlying)
            if momentum_override:
                return "CE", True

        momentum_override = False

        # Step 2: Derive from signal direction
        if signal_side == "BUY":
            natural_type = "CE"
        elif signal_side == "SELL":
            natural_type = "PE"
        else:
            return None, False

        # Step 3: Check option chain alignment
        if option_chain_signal is None:
            # No chain data — trust signal direction
            return natural_type, False

        chain_direction = "BULLISH" if option_chain_signal == "BUY_CALL" else "BEARISH"
        signal_direction = "BULLISH" if signal_side == "BUY" else "BEARISH"

        if signal_direction == chain_direction:
            # Full agreement — high confidence
            return natural_type, False

        # Disagreement — block (unless momentum override above triggered)
        logger.info(
            "CE/PE BLOCK: signal=%s chain=%s disagree — conflicting signals",
            signal_direction, chain_direction,
        )
        return None, False

    def _check_bearish_momentum(
        self, df: pd.DataFrame, underlying: str
    ) -> bool:
        """True if price fell > 0.8 ATR in the last 2 bars (fast drop)."""
        if not _PANDAS_AVAILABLE:
            return False
        try:
            from indicators import calculate_atr
            close = pd.to_numeric(
                df["Close"] if "Close" in df.columns else df["close"], errors="coerce"
            )
            atr = float(calculate_atr(df, 14).iloc[-1])
            drop = float(close.iloc[-3]) - float(close.iloc[-1])
            if atr > 0 and drop > 0.8 * atr:
                logger.debug("Bearish momentum: drop=%.2f atr=%.2f (%.1f×)", drop, atr, drop/atr)
                return True
        except Exception:
            pass
        return False

    def _check_bullish_momentum(
        self, df: pd.DataFrame, underlying: str
    ) -> bool:
        """True if price rose > 0.8 ATR in the last 2 bars (fast rise)."""
        if not _PANDAS_AVAILABLE:
            return False
        try:
            from indicators import calculate_atr
            close = pd.to_numeric(
                df["Close"] if "Close" in df.columns else df["close"], errors="coerce"
            )
            atr  = float(calculate_atr(df, 14).iloc[-1])
            rise = float(close.iloc[-1]) - float(close.iloc[-3])
            if atr > 0 and rise > 0.8 * atr:
                return True
        except Exception:
            pass
        return False

    # ─────────────────────────────────────────────────────────────────────────
    # REAL-TIME SPOT PRICE
    # ─────────────────────────────────────────────────────────────────────────

    def _get_live_spot(self, underlying: str) -> float:
        """Fetch live spot price from broker. Falls back to last known."""
        if self._broker:
            # BSE indices (SENSEX/BANKEX) quote on the BSE exchange, not NSE.
            _bse = underlying.upper() in ("SENSEX", "BANKEX")
            _attempts = ([(underlying, "BSE")] if _bse else []) + [
                (underlying,            "NSE"),
                (f"{underlying}-INDEX", "NSE"),
                (f"{underlying} 50",    "NSE"),
                (underlying,            "NFO"),
            ]
            for sym, exch in _attempts:
                try:
                    ltp = self._broker.get_ltp(sym, exchange=exch)
                    if isinstance(ltp, tuple): ltp = ltp[-1]
                    if ltp and float(ltp) > 100:
                        return float(ltp)
                except Exception:
                    pass
        # yfinance fallback (for paper trading)
        try:
            import yf_compat as yf  # yfinance replaced: Yahoo API broken
            sym  = "^NSEI" if underlying == "NIFTY" else "^NSEBANK" if underlying == "BANKNIFTY" else f"{underlying}.NS"
            tick = yf.Ticker(sym)
            data = tick.history(period="1d", interval="1m")
            if data is not None and len(data) > 0:
                return float(data["Close"].iloc[-1])
        except Exception:
            pass
        return 0.0

    # ─────────────────────────────────────────────────────────────────────────
    # STRIKE SELECTION
    # ─────────────────────────────────────────────────────────────────────────

    def _select_strike(
        self,
        underlying:  str,
        spot:        float,
        option_type: str,
        confidence:  float,
        force_atm:   bool,
    ) -> Tuple[int, str]:
        """
        Select strike based on confidence and option type.

        CE buying (bullish):
          ATM (high conf): buy at spot rounded to interval
          1-OTM (med conf): one strike above spot for CE
        PE buying (bearish):
          ATM (high conf): buy at spot rounded to interval
          1-OTM (med conf): one strike below spot for PE
        """
        step = STRIKE_INTERVALS.get(underlying, 50)
        atm  = int(round(spot / step) * step)

        if force_atm or confidence >= 0.70:
            return atm, "ATM"

        if confidence >= 0.55:
            # 1 strike OTM
            if option_type == "CE":
                return atm + step, "1OTM"
            else:
                return atm - step, "1OTM"

        # Very low confidence: 2 strikes OTM (only on strong trend days)
        if option_type == "CE":
            return atm + 2 * step, "2OTM"
        else:
            return atm - 2 * step, "2OTM"

    def _strike_candidates(
        self,
        underlying: str,
        spot: float,
        option_type: str,
        confidence: float,
        default_strike: int,
        default_strike_type: str,
        force_atm: bool,
    ) -> List[Tuple[int, str, float]]:
        step = STRIKE_INTERVALS.get(underlying, 50)
        atm = int(round(spot / step) * step)
        if force_atm:
            return [(atm, "ATM", 1.0)]

        direction = 1 if option_type == "CE" else -1
        otm_steps = max(0, min(6, int(getattr(cfg, "OPTION_STRIKE_LADDER_OTM_STEPS", 3))))
        itm_steps = max(0, min(3, int(getattr(cfg, "OPTION_STRIKE_LADDER_ITM_STEPS", 1))))
        raw = [(atm, "ATM")]
        raw.extend((atm + direction * step * i, f"{i}OTM") for i in range(1, otm_steps + 1))
        raw.extend((atm - direction * step * i, f"{i}ITM") for i in range(1, itm_steps + 1))
        base_by_type = {
            "ATM": 1.0 if default_strike_type == "ATM" else 0.94,
            "1OTM": 1.0 if default_strike_type == "1OTM" else 0.94,
            "2OTM": 1.0 if default_strike_type == "2OTM" else 0.90,
        }
        if confidence >= 0.70:
            base_by_type.update({"ATM": 1.0, "1OTM": 0.95, "2OTM": 0.86})
        elif confidence >= 0.55:
            base_by_type.update({"ATM": 0.96, "1OTM": 1.0, "2OTM": 0.90})
        else:
            base_by_type.update({"ATM": 0.90, "1OTM": 0.96, "2OTM": 1.0})

        out: List[Tuple[int, str, float]] = []
        seen = set()
        for strike, strike_type in raw + [(default_strike, default_strike_type)]:
            key = (int(strike), str(strike_type))
            if key in seen:
                continue
            seen.add(key)
            if str(strike_type).endswith("ITM"):
                try:
                    depth = max(1, int(str(strike_type).replace("ITM", "")))
                except Exception:
                    depth = 1
                base_score = 0.91 - (0.04 * (depth - 1))
            elif str(strike_type).endswith("OTM"):
                try:
                    depth = max(1, int(str(strike_type).replace("OTM", "")))
                except Exception:
                    depth = 1
                base_score = base_by_type.get(str(strike_type), max(0.72, 0.94 - 0.08 * (depth - 1)))
            else:
                base_score = base_by_type.get(str(strike_type), 0.90)
            out.append((int(strike), str(strike_type), float(max(0.50, base_score))))
        return out

    def _select_tradeable_strike_with_autotune(
        self,
        *,
        underlying: str,
        expiry: date,
        option_type: str,
        spot: float,
        confidence: float,
        default_strike: int,
        default_strike_type: str,
        force_atm: bool,
    ) -> Optional[Dict[str, Any]]:
        best: Optional[Dict[str, Any]] = None
        resolved: List[Dict[str, Any]] = []
        try:
            from option_strike_autotune import score_candidate_with_autotune
        except Exception:
            score_candidate_with_autotune = None

        for strike, strike_type, base_score in self._strike_candidates(
            underlying, spot, option_type, confidence,
            default_strike, default_strike_type, force_atm,
        ):
            symbols = self._build_symbol_candidates(underlying, expiry, strike, option_type)
            symbol, premium = self._resolve_symbol(symbols)
            if not symbol or premium <= 0:
                continue
            candidate = {
                "symbol": symbol,
                "strike": strike,
                "option_type": option_type,
                "premium": round(float(premium), 2),
                "spot": round(float(spot), 2),
                "otm_pct": round(abs(float(strike) - float(spot)) / max(float(spot), 1.0) * 100.0, 4),
                "quality_score": max(0.0, min(1.0, float(confidence or 0.0))),
                "strike_type": strike_type,
            }
            if score_candidate_with_autotune:
                try:
                    auto = score_candidate_with_autotune(
                        candidate,
                        quality={},
                        side="BUY" if option_type == "CE" else "SELL",
                    )
                except Exception:
                    auto = {"multiplier": 1.0, "reason": "autotune_error"}
            else:
                auto = {"multiplier": 1.0, "reason": "autotune_unavailable"}
            multiplier = float(auto.get("multiplier", 1.0) or 1.0)
            score = float(base_score) * multiplier
            item = {
                **candidate,
                "premium": float(premium),
                "base_score": round(float(base_score), 4),
                "autotune": auto,
                "autotune_score": round(score, 4),
                "shadow": True,
            }
            resolved.append(item)
            if best is None or item["autotune_score"] > best["autotune_score"]:
                best = item

        if best is not None:
            best["shadow_candidates"] = resolved
        return best

    # ─────────────────────────────────────────────────────────────────────────
    # SMART EXPIRY SELECTION
    # ─────────────────────────────────────────────────────────────────────────

    # ── Per-index expiry weekday ─────────────────────────────────────────────────
    # Fallback expiry weekday schedule. The master contract path above is the
    # source of truth; these are used only when local contracts are unavailable.
    #   NIFTY 50      → Tuesday  (weekday=1), Sep 2025+
    #   BANKNIFTY     → Wednesday (weekday=2)
    #   FINNIFTY      → Tuesday  (weekday=1)
    #   MIDCPNIFTY    → Monday   (weekday=0)
    #   NIFTYNEXT50   → Friday   (weekday=4)
    #   SENSEX (BSE)  → Friday   (weekday=4) — BSE, different exchange
    # Individual stocks → Monthly last Thursday
    EXPIRY_WEEKDAY = {
        "NIFTY":       1,   # Tuesday
        "BANKNIFTY":   2,   # Wednesday
        "FINNIFTY":    1,   # Tuesday
        "MIDCPNIFTY":  0,   # Monday
        "NIFTYNEXT50": 4,   # Friday
        "SENSEX":      4,   # Friday (BSE)
        "BANKEX":      4,   # Friday (BSE)
    }
    MONTHLY_EXPIRY_UNDERLYING = {"NIFTY", "BANKNIFTY"}  # also have monthly options

    def _select_expiry(
        self,
        underlying:   str,
        style:        str,
        skip_current: bool = False,
    ) -> date:
        """
        Select correct expiry based on underlying's actual expiry weekday.

        Each index expires on a different day of the week (NSE design to
        spread expiry risk across the week).

        BANKNIFTY → Wednesday, FINNIFTY → Tuesday, MIDCPNIFTY → Monday
        """
        today    = date.today()
        min_dte  = MIN_DTE_BY_STYLE.get(style, 0)
        sym_up   = underlying.upper()

        # Primary: real expiries from the broker master contract (authoritative,
        # immune to NSE's frequent expiry-weekday changes). Falls through to the
        # weekday heuristic only if the master file is unavailable.
        try:
            _exps = _load_master_expiries().get(sym_up, [])
            _valid = [d for d in _exps if (d - today).days >= min_dte] or _exps
            if _valid:
                if skip_current and len(_valid) > 1:
                    return _valid[1]
                return _valid[0]
        except Exception:
            pass

        # Determine which weekday this underlying expires
        expiry_wd = self.EXPIRY_WEEKDAY.get(sym_up, 3)  # default Thursday

        # Individual stocks expire monthly (last Thursday of month)
        if sym_up not in self.EXPIRY_WEEKDAY and sym_up not in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"):
            return self._get_monthly_expiry(today, style)

        # Find nearest upcoming expiry weekday
        current_wd   = today.weekday()
        days_to_exp  = (expiry_wd - current_wd) % 7
        if days_to_exp == 0:
            # Today IS the expiry day — if after 3:30 PM, roll to next week
            if datetime.now().hour >= 15 and datetime.now().minute >= 30:
                days_to_exp = 7
        expiry = today + timedelta(days=days_to_exp)

        if skip_current:
            expiry += timedelta(days=7)

        # If DTE < min required, advance week by week
        week_count = 0
        while (expiry - today).days < min_dte and week_count < 6:
            expiry    += timedelta(days=7)
            week_count += 1

        # Roll back from holidays (max 3 days back)
        for _ in range(4):
            if not _is_expiry_holiday(expiry):
                break
            expiry -= timedelta(days=1)

        # Ensure the rolled-back expiry still satisfies the minimum DTE.
        if (expiry - today).days < min_dte:
            while (expiry - today).days < min_dte:
                expiry += timedelta(days=7)
                for _ in range(4):
                    if not _is_expiry_holiday(expiry):
                        break
                    expiry -= timedelta(days=1)

        return expiry

    def _get_monthly_expiry(self, today: date, style: str) -> date:
        """Monthly expiry = last Thursday of the month."""
        import calendar
        y, m = today.year, today.month
        # Find last Thursday of current month
        cal   = calendar.monthcalendar(y, m)
        thursdays = [week[3] for week in cal if week[3] != 0]
        last_thu  = date(y, m, thursdays[-1])
        # If it's past the last Thursday, use next month's
        if last_thu <= today:
            m += 1
            if m > 12: m = 1; y += 1
            cal       = calendar.monthcalendar(y, m)
            thursdays = [week[3] for week in cal if week[3] != 0]
            last_thu  = date(y, m, thursdays[-1])
        # Roll back if holiday
        for _ in range(4):
            if not _is_expiry_holiday(last_thu):
                break
            last_thu -= timedelta(days=1)
        return last_thu

    # ─────────────────────────────────────────────────────────────────────────
    # SYMBOL RESOLUTION — multiple format candidates
    # ─────────────────────────────────────────────────────────────────────────

    def _build_symbol_candidates(
        self, underlying: str, expiry: date, strike: int, option_type: str
    ) -> List[str]:
        """
        Build multiple symbol format candidates.
        Angel One accepts several formats — try all until one resolves.
        """
        dd      = expiry.strftime("%d")
        mmm     = expiry.strftime("%b").upper()
        yy      = expiry.strftime("%y")
        yyyy    = expiry.strftime("%Y")
        mon_num = expiry.strftime("%m")

        # BSE (SENSEX/BANKEX) use a different format than NSE, and weekly vs
        # monthly differ. Try both BSE forms first for those underlyings.
        if underlying.upper() in ("SENSEX", "BANKEX"):
            mdigit = "123456789OND"[expiry.month - 1]  # Oct/Nov/Dec → O/N/D
            # Weekly format encodes the exact day, so try it FIRST — the monthly
            # format omits the day and would otherwise wrongly match the monthly
            # contract when a weekly expiry was intended.
            return [
                f"{underlying}{yy}{mdigit}{dd}{strike}{option_type}",   # weekly:  SENSEX2661174200CE
                f"{underlying}{yy}{mmm}{strike}{option_type}",          # monthly: BANKEX26JUN65000PE
                f"{underlying}{dd}{mmm}{yy}{strike}{option_type}",      # NSE-style fallback
            ]

        return [
            f"{underlying}{dd}{mmm}{yy}{strike}{option_type}",    # NIFTY27MAR25 22000CE
            f"{underlying}{dd}{mmm}{yyyy}{strike}{option_type}",  # NIFTY27MAR2025 22000CE
            f"{underlying}{yy}{mmm}{dd}{strike}{option_type}",    # NIFTY25MAR27 22000CE
            f"{underlying}{yy}{mon_num}{dd}{strike}{option_type}",# NIFTY2503 27 22000CE
            f"{underlying}{expiry.strftime('%Y%m%d')}{strike}{option_type}",  # NIFTY20250327 22000CE
        ]

    def _get_exchange(self, underlying: str) -> str:
        """Returns the correct exchange for an underlying."""
        bse_symbols = {"SENSEX", "BANKEX"}
        return "BSE" if underlying.upper() in bse_symbols else "NFO"

    def _resolve_symbol(
        self, candidates: List[str]
    ) -> Tuple[Optional[str], float]:
        """Try each candidate symbol until we get a valid LTP."""
        if not self._broker:
            return candidates[0] if candidates else None, 0.0

        for sym in candidates:
            try:
                # Detect exchange from symbol prefix
                _exch = "BFO" if any(sym.upper().startswith(b) for b in ("SENSEX","BANKEX")) else "NFO"
                ltp = self._broker.get_ltp(sym, exchange=_exch)
                if isinstance(ltp, tuple): ltp = ltp[-1]
                if ltp is not None and float(ltp) > 0:
                    return sym, float(ltp)
            except Exception:
                pass
        return None, 0.0

    # ─────────────────────────────────────────────────────────────────────────
    # LOT SIZING
    # ─────────────────────────────────────────────────────────────────────────

    def _compute_lots(
        self,
        trade_capital: float,
        premium:       float,
        lot_size:      int,
        confidence:    float,
        max_lots:      int,
    ) -> int:
        """
        Compute lots from trade capital.
        Never allocates more than 40% of trade_capital to one option.
        Confidence scaling: high confidence → up to 40% per trade,
                            low confidence → max 20% per trade.
        """
        if premium <= 0 or lot_size <= 0:
            return 0

        # Confidence-based fraction
        if confidence >= 0.80:
            max_alloc_pct = 0.40
        elif confidence >= 0.65:
            max_alloc_pct = 0.30
        elif confidence >= 0.50:
            max_alloc_pct = 0.20
        else:
            max_alloc_pct = 0.15

        usable_capital = trade_capital * max_alloc_pct
        cost_per_lot   = premium * lot_size
        lots           = int(usable_capital // cost_per_lot)
        lots           = max(0, min(lots, max_lots))
        # Telegram-settable runtime ceiling (/optlots N) — caps lots for today.
        from option_lot_override import apply_override
        return apply_override(lots)

    # ─────────────────────────────────────────────────────────────────────────
    # UTILITY
    # ─────────────────────────────────────────────────────────────────────────

    def get_lot_size(self, underlying: str) -> int:
        """Return lot size — live from NSEMaster or hardcoded fallback."""
        if _NSE_MASTER_AVAILABLE:
            try:
                return _get_nse_master().get_lot_size(underlying)
            except Exception:
                pass
        return NSE_LOT_SIZES.get(underlying.upper(), 75)

    def get_strike_interval(self, underlying: str) -> int:
        return STRIKE_INTERVALS.get(underlying.upper(), 50)

    def to_dict(self, contract: OptionContract) -> Dict[str, Any]:
        return asdict(contract)
