from __future__ import annotations


# F&O ban cache (refreshed every 30 min)
_FNO_BAN_CACHE: list = []
_FNO_BAN_TS: float = 0.0


def is_fno_banned(symbol: str) -> bool:
    """Check if symbol is in F&O ban list. Cached 30 min."""
    global _FNO_BAN_CACHE, _FNO_BAN_TS
    import time as _tb
    if _tb.time() - _FNO_BAN_TS > 1800:
        try:
            from omnisource_news_engine import fetch_fno_ban_list
            _FNO_BAN_CACHE = [s.upper() for s in fetch_fno_ban_list()]
            _FNO_BAN_TS = _tb.time()
        except Exception as _e:
            import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)
    return symbol.upper() in _FNO_BAN_CACHE


def get_intelligence_score_modifier() -> float:
    """
    Get market intelligence score to boost/reduce signal scores.
    Strongly bullish news + max pain alignment = +1.5 to signal score.
    Strongly bearish news = -1.5.
    """
    try:
        from mega_intelligence_engine import get_full_intelligence
        d = get_full_intelligence()
        return float(d.get("intelligence_score", 0))
    except Exception:
        return 0.0


def _evidence_safe_context_modifier(value: float, cfg: dict) -> float:
    """Do not let unvalidated context narratives promote a signal.

    Negative values remain useful as risk vetoes. Positive boosts are restored
    only after an explicit forward-validation switch is enabled.
    """
    value = float(value or 0.0)
    if bool(cfg.get("allow_unvalidated_context_boosts", False)):
        return value
    return min(0.0, value)

"""
signal_engine.py

Higher-level signal engine that combines regime detection, HTF bias,
per-strategy scoring and regime adjustments, and fallback logic.

Fixes applied
-------------
1. Hardcoded capital of ₹10,000 in _get_qty()
   The original function used `capital = 10000.0` — completely
   disconnected from the real account size (default ₹1,00,000 in
   config.py).  Every signal's `qty` field was therefore 10× too small
   (e.g. qty=4 at NIFTY 22000 instead of 45).

   Because the SelfLearningEngine stores `qty` as a trade feature,
   this systematically biased the ML model and RL reward magnitudes.

   Fix: `_get_qty()` now accepts `capital` as a required argument.
   `generate_signal()` accepts an optional `capital` parameter.
   If no capital is passed, it reads `config.get_runtime_capital()`
   with a safe fallback to ₹1,00,000 so the import failure mode is
   handled gracefully.

2. `generate_signal()` now propagates `capital` to the qty calculation
   in both the candidate-selection path and the fallback path.
"""

import importlib.util
try:
    from market_regime import get_regime_engine as _get_regime
    _REGIME_AVAIL = True
except ImportError:
    _REGIME_AVAIL = False

try:
    from oi_tracker import get_oi_tracker as _get_oi_tracker
    _OI_TRACKER_SIG_AVAIL = True
except ImportError:
    _OI_TRACKER_SIG_AVAIL = False

try:
    from greeks_live import (
        compute_gex, get_implied_move, get_iv_term_structure,
        get_pcr_by_strike, get_event_playbook,
    )
    _GREEKS_AVAIL = True
except ImportError:
    _GREEKS_AVAIL = False

try:
    from hero_zero_strategy import run_hero_zero_strategy
    _HZ_AVAIL = True
except ImportError:
    _HZ_AVAIL = False

try:
    from elliott_wave  import run_elliott_wave_strategy
    _EW_AVAIL = True
except ImportError: _EW_AVAIL = False

try:
    from order_flow    import run_order_flow_strategy
    _OF_STRAT_AVAIL = True
except ImportError:
    _OF_STRAT_AVAIL = False
    def run_order_flow_strategy(*a,**k): return {"strategy":"order_flow","score":0.0,"direction":None,"side":None}

try:
    from hmm_regime    import detect_regime_hmm, get_regime_score_modifier
    _HMM_AVAIL = True
except ImportError: _HMM_AVAIL = False

try:
    from order_flow    import compute_order_flow as _compute_of
    _OF_AVAIL = True
except ImportError: _OF_AVAIL = False

try:
    from dark_pool     import get_dark_pool_score as _get_dp
    _DP_AVAIL = True
except ImportError: _DP_AVAIL = False

try:
    from fii_options_positioning import get_fii_options_positioning as _get_fii_opts
    _FII_OPT_AVAIL = True
except ImportError: _FII_OPT_AVAIL = False

try:
    from meta_learner import composite_score as _meta_composite
    from meta_learner import get_meta_learner as _get_ml
    _META_AVAIL = True
except ImportError:
    _META_AVAIL = False

try:
    from signal_refinements import (
        apply_score_decay, get_nifty_weight_multiplier,
        check_earnings_risk, detect_sr_levels,
        get_sector_rotation_score, check_iv_skew,
    )
    _REFINE_AVAIL = True
except ImportError:
    _REFINE_AVAIL = False

try:
    from cpr_ema_strategy import run_cpr_ema_strategy
    _CPR_EMA_AVAIL = True
except ImportError:
    _CPR_EMA_AVAIL = False

try:
    from volume_profile_advanced import (
        run_vrvp_zone_strategy,
        run_failed_auction_strategy,
        run_ema_cloud_sd_strategy,
        get_global_macro_score as _get_macro,
        get_president_sentiment_score as _get_president,
        get_stt_breakeven_points as _get_stt_be,
    )
    _VP_ADV_AVAIL = True
except ImportError:
    _VP_ADV_AVAIL = False

try:
    from tradingview_strategies import (
        run_ema_ribbon_strategy,
        run_waddah_attar_strategy,
        run_chaikin_mf_strategy,
        run_awesome_oscillator_strategy,
        run_bb_percentb_strategy,
        run_ehlers_fisher_strategy,
        run_vix_fix_strategy,
    )
    _TV_AVAIL = True
except ImportError:
    _TV_AVAIL = False

try:
    from greeks_live import (
        compute_gex, get_implied_move, get_iv_term_structure,
        get_pcr_by_strike, get_event_playbook,
    )
    _GREEKS_AVAIL = True
except ImportError:
    _GREEKS_AVAIL = False

try:
    from hero_zero_strategy import run_hero_zero_strategy
    _HZ_AVAIL = True
except ImportError:
    _HZ_AVAIL = False

try:
    from elliott_wave  import run_elliott_wave_strategy
    _EW_AVAIL = True
except ImportError: _EW_AVAIL = False

try:
    from order_flow    import run_order_flow_strategy
    _OF_STRAT_AVAIL = True
except ImportError:
    _OF_STRAT_AVAIL = False
    def run_order_flow_strategy(*a,**k): return {"strategy":"order_flow","score":0.0,"direction":None,"side":None}

try:
    from hmm_regime    import detect_regime_hmm, get_regime_score_modifier
    _HMM_AVAIL = True
except ImportError: _HMM_AVAIL = False

try:
    from order_flow    import compute_order_flow as _compute_of
    _OF_AVAIL = True
except ImportError: _OF_AVAIL = False

try:
    from dark_pool     import get_dark_pool_score as _get_dp
    _DP_AVAIL = True
except ImportError: _DP_AVAIL = False

try:
    from fii_options_positioning import get_fii_options_positioning as _get_fii_opts
    _FII_OPT_AVAIL = True
except ImportError: _FII_OPT_AVAIL = False

try:
    from meta_learner import composite_score as _meta_composite
    from meta_learner import get_meta_learner as _get_ml
    _META_AVAIL = True
except ImportError:
    _META_AVAIL = False

try:
    from signal_refinements import (
        apply_score_decay, get_nifty_weight_multiplier,
        check_earnings_risk, detect_sr_levels,
        get_sector_rotation_score, check_iv_skew,
    )
    _REFINE_AVAIL = True
except ImportError:
    _REFINE_AVAIL = False

try:
    from cpr_ema_strategy import run_cpr_ema_strategy
    _CPR_EMA_AVAIL = True
except ImportError:
    _CPR_EMA_AVAIL = False

try:
    from volume_profile_advanced import (
        run_vrvp_zone_strategy,
        run_failed_auction_strategy,
        run_ema_cloud_sd_strategy,
        get_global_macro_score as _get_macro,
        get_president_sentiment_score as _get_president,
        get_stt_breakeven_points as _get_stt_be,
    )
    _VP_ADV_AVAIL = True
except ImportError:
    _VP_ADV_AVAIL = False

try:
    from tradingview_strategies import (
        run_ema_ribbon_strategy,
        run_volume_profile_full_strategy,
        run_waddah_attar_strategy,
        run_chaikin_mf_strategy,
        run_awesome_oscillator_strategy,
        run_bb_percentb_strategy,
        run_ehlers_fisher_strategy,
        run_vix_fix_strategy,
    )
    _TV_AVAIL = True
except ImportError:
    _TV_AVAIL = False

try:
    from institutional_strategies import (
        run_vsa_strategy,
        run_anchored_vwap_strategy,
        run_parabolic_sar_strategy,
        run_delta_neutral_theta,
        run_gamma_scalp_strategy,
        run_event_driven_filter,
    )
    _INST_AVAIL = True
except ImportError:
    _INST_AVAIL = False

try:
    from advanced_confluence import (
        run_smc_strategy,
        run_vwap_bands_strategy,
        run_heikin_ashi_strategy,
        run_orb_2min_strategy,
        run_canslim_filter,
    )
    _ADV_AVAIL = True
except ImportError:
    _ADV_AVAIL = False
try:
    from price_structure import (
        run_price_structure_strategy,
        get_pdh_pdl_pdc, get_weekly_monthly_levels, score_vs_key_levels
    )
    _PS_AVAILABLE = True
except ImportError:
    _PS_AVAILABLE = False

# Cache for S/R levels — computed once per symbol per hour
_SR_LEVEL_CACHE: dict = {}  # symbol → {ts, levels}

try:
    from whale_tracker import get_whale_composite_score as _get_whale_score
    _WHALE_AVAILABLE = True
except ImportError:
    _WHALE_AVAILABLE = False

try:
    from strategy_evolution import get_evolution as _get_evo
    _EVO_AVAILABLE = True
except ImportError:
    _EVO_AVAILABLE = False


import logging
import time
import pandas as pd
from datetime import datetime
from typing import Any, Dict, List, Optional

from regime import detect_market_regime
from signal_score import calculate_signal_score
from mtf import get_htf_bias
from time_regime import get_strategy_weight, get_time_zone

# ── Indicator cache integration ─────────────────────────────────
def _get_cached_indicators(df, symbol=""):
    """Get pre-computed indicators from cache. Falls back to raw compute."""
    try:
        from indicator_cache import get_indicators
        return get_indicators(df, symbol)
    except Exception:
        return {}


# Strategy-specific modules (lazy-safe imports)
try:
    from orb_strategy import orb_signal as _orb_signal
    _ORB_AVAILABLE = True
except ImportError:
    _ORB_AVAILABLE = False
try:
    from vwap_reversion_strategy import vwap_reversion_signal as _vwap_rev_signal
    _VWAP_REV_AVAILABLE = True
except ImportError:
    _VWAP_REV_AVAILABLE = False
try:
    from supertrend_mtf_strategy import supertrend_mtf_signal as _st_mtf_signal
    _ST_MTF_AVAILABLE = True
except ImportError:
    _ST_MTF_AVAILABLE = False
# ── Advanced strategies (new) ────────────────────────────────────────────────
try:
    from advanced_strategies import (
        rsi_divergence_signal as _rsi_div_sig,
        gap_fill_signal       as _gap_fill_sig,
        ichimoku_signal       as _ichimoku_sig,
        vp_breakout_signal    as _vp_breakout_sig,
        expiry_week_regime    as _expiry_week_regime,
    )
    from advanced_strategies import get_rs_filter as _get_rs_filter
    from advanced_strategies import get_global_macro as _get_global_macro
    _ADV_AVAILABLE = True
except ImportError as _e:
    _ADV_AVAILABLE = False

try:
    from greeks_live import (
        compute_gex, get_implied_move, get_iv_term_structure,
        get_pcr_by_strike, get_event_playbook,
    )
    _GREEKS_AVAIL = True
except ImportError:
    _GREEKS_AVAIL = False

try:
    from hero_zero_strategy import run_hero_zero_strategy
    _HZ_AVAIL = True
except ImportError:
    _HZ_AVAIL = False

try:
    from elliott_wave  import run_elliott_wave_strategy
    _EW_AVAIL = True
except ImportError: _EW_AVAIL = False

try:
    from order_flow    import run_order_flow_strategy
    _OF_STRAT_AVAIL = True
except ImportError:
    _OF_STRAT_AVAIL = False
    def run_order_flow_strategy(*a,**k): return {"strategy":"order_flow","score":0.0,"direction":None,"side":None}

try:
    from hmm_regime    import detect_regime_hmm, get_regime_score_modifier
    _HMM_AVAIL = True
except ImportError: _HMM_AVAIL = False

try:
    from order_flow    import compute_order_flow as _compute_of
    _OF_AVAIL = True
except ImportError: _OF_AVAIL = False

try:
    from dark_pool     import get_dark_pool_score as _get_dp
    _DP_AVAIL = True
except ImportError: _DP_AVAIL = False

try:
    from fii_options_positioning import get_fii_options_positioning as _get_fii_opts
    _FII_OPT_AVAIL = True
except ImportError: _FII_OPT_AVAIL = False

try:
    from meta_learner import composite_score as _meta_composite
    from meta_learner import get_meta_learner as _get_ml
    _META_AVAIL = True
except ImportError:
    _META_AVAIL = False

try:
    from signal_refinements import (
        apply_score_decay, get_nifty_weight_multiplier,
        check_earnings_risk, detect_sr_levels,
        get_sector_rotation_score, check_iv_skew,
    )
    _REFINE_AVAIL = True
except ImportError:
    _REFINE_AVAIL = False

try:
    from cpr_ema_strategy import run_cpr_ema_strategy
    _CPR_EMA_AVAIL = True
except ImportError:
    _CPR_EMA_AVAIL = False

try:
    from volume_profile_advanced import (
        run_vrvp_zone_strategy,
        run_failed_auction_strategy,
        run_ema_cloud_sd_strategy,
        get_global_macro_score as _get_macro,
        get_president_sentiment_score as _get_president,
        get_stt_breakeven_points as _get_stt_be,
    )
    _VP_ADV_AVAIL = True
except ImportError:
    _VP_ADV_AVAIL = False

try:
    from tradingview_strategies import (
        run_ema_ribbon_strategy,
        run_waddah_attar_strategy,
        run_chaikin_mf_strategy,
        run_awesome_oscillator_strategy,
        run_bb_percentb_strategy,
        run_ehlers_fisher_strategy,
        run_vix_fix_strategy,
    )
    _TV_AVAIL = True
except ImportError:
    _TV_AVAIL = False

try:
    from greeks_live import (
        compute_gex, get_implied_move, get_iv_term_structure,
        get_pcr_by_strike, get_event_playbook,
    )
    _GREEKS_AVAIL = True
except ImportError:
    _GREEKS_AVAIL = False

try:
    from hero_zero_strategy import run_hero_zero_strategy
    _HZ_AVAIL = True
except ImportError:
    _HZ_AVAIL = False

try:
    from elliott_wave  import run_elliott_wave_strategy
    _EW_AVAIL = True
except ImportError: _EW_AVAIL = False

try:
    from order_flow    import run_order_flow_strategy
    _OF_STRAT_AVAIL = True
except ImportError:
    _OF_STRAT_AVAIL = False
    def run_order_flow_strategy(*a,**k): return {"strategy":"order_flow","score":0.0,"direction":None,"side":None}

try:
    from hmm_regime    import detect_regime_hmm, get_regime_score_modifier
    _HMM_AVAIL = True
except ImportError: _HMM_AVAIL = False

try:
    from order_flow    import compute_order_flow as _compute_of
    _OF_AVAIL = True
except ImportError: _OF_AVAIL = False

try:
    from dark_pool     import get_dark_pool_score as _get_dp
    _DP_AVAIL = True
except ImportError: _DP_AVAIL = False

try:
    from fii_options_positioning import get_fii_options_positioning as _get_fii_opts
    _FII_OPT_AVAIL = True
except ImportError: _FII_OPT_AVAIL = False

try:
    from meta_learner import composite_score as _meta_composite
    from meta_learner import get_meta_learner as _get_ml
    _META_AVAIL = True
except ImportError:
    _META_AVAIL = False

try:
    from signal_refinements import (
        apply_score_decay, get_nifty_weight_multiplier,
        check_earnings_risk, detect_sr_levels,
        get_sector_rotation_score, check_iv_skew,
    )
    _REFINE_AVAIL = True
except ImportError:
    _REFINE_AVAIL = False

try:
    from cpr_ema_strategy import run_cpr_ema_strategy
    _CPR_EMA_AVAIL = True
except ImportError:
    _CPR_EMA_AVAIL = False

try:
    from volume_profile_advanced import (
        run_vrvp_zone_strategy,
        run_failed_auction_strategy,
        run_ema_cloud_sd_strategy,
        get_global_macro_score as _get_macro,
        get_president_sentiment_score as _get_president,
        get_stt_breakeven_points as _get_stt_be,
    )
    _VP_ADV_AVAIL = True
except ImportError:
    _VP_ADV_AVAIL = False

try:
    from tradingview_strategies import (
        run_ema_ribbon_strategy,
        run_volume_profile_full_strategy,
        run_waddah_attar_strategy,
        run_chaikin_mf_strategy,
        run_awesome_oscillator_strategy,
        run_bb_percentb_strategy,
        run_ehlers_fisher_strategy,
        run_vix_fix_strategy,
    )
    _TV_AVAIL = True
except ImportError:
    _TV_AVAIL = False

# ── Extended strategy imports ─────────────────────────────────────────────
try:
    from chart_patterns import run_chart_pattern_strategy
    _CHPAT_AVAIL = True
except ImportError:
    _CHPAT_AVAIL = False
    def run_chart_pattern_strategy(df, **kw): return {}

try:
    from tradingview_strategies import (
        run_ema_ribbon_strategy, run_waddah_attar_strategy,
        run_chaikin_mf_strategy, run_awesome_oscillator_strategy,
        run_bb_percentb_strategy, run_ehlers_fisher_strategy,
        run_vix_fix_strategy, run_volume_profile_full_strategy,
    )
    _TVSTRATS_AVAIL = True
except ImportError:
    _TVSTRATS_AVAIL = False

try:
    from volume_profile_advanced import (
        run_vrvp_zone_strategy, run_failed_auction_strategy,
        run_ema_cloud_sd_strategy,
    )
    _VOLPADV_AVAIL = True
except ImportError:
    _VOLPADV_AVAIL = False

try:
    from williams_systems import (
        run_williams_r_strategy, run_volatility_breakout_strategy,
        run_oops_strategy,
    )
    _WILLIAMS_AVAIL = True
except ImportError:
    _WILLIAMS_AVAIL = False

try:
    from order_flow import run_order_flow_strategy
    _ORDERFLOW_AVAIL = True
except ImportError:
    _ORDERFLOW_AVAIL = False
    def run_order_flow_strategy(df, **kw): return {}



try:
    from institutional_strategies import (
        cvd_scalp_signal as _cvd_scalp,
        order_block_signal as _ob_signal,
        liquidity_sweep_signal as _liq_sweep,
        vpoc_magnet_signal as _vpoc_magnet,
        hour_orb_signal as _hour_orb,
        market_structure_signal as _ms_signal,
    )
    _INST_AVAILABLE = True
except ImportError:
    _INST_AVAILABLE = False

logger = logging.getLogger(__name__)

# Per-symbol optimised params from autonomous_backtest.py
# Loaded at startup from symbol_params.json
_SYMBOL_PARAMS: dict = {}

def get_symbol_params(symbol: str) -> dict:
    """Return backtested optimal params for a symbol, or defaults."""
    p = _SYMBOL_PARAMS.get(symbol, {})
    return {
        "stop_mult":   float(p.get("stop_mult",   1.5)),
        "target_mult": float(p.get("target_mult", 2.5)),
    }

# ---------------------------------------------------------------------------
# Runtime capital — read from config once at import time.
# All call sites that pass `capital` explicitly bypass this default.
# ---------------------------------------------------------------------------
_DEFAULT_CAPITAL = 100_000.0

try:
    import config as _config_module
    _DEFAULT_CAPITAL = float(_config_module.get_runtime_capital())
except Exception:
    logger.debug("signal_engine: config not available, using default capital %.0f", _DEFAULT_CAPITAL)


# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------
DEFAULT_CONFIG: Dict[str, Any] = {
    "min_score":                  3.5,
    "trend_min_score":            4.0,
    "range_min_score":            3.0,
    "breakout_min_score":         4.0,
    "require_htf_alignment":      True,    # ENABLED: blocks counter-HTF entries
    "allow_sideways_htf":         True,
    "enable_fallback":            True,
    "fallback_score_threshold":   float(getattr(__import__("config"), "FALLBACK_SCORE_THRESHOLD", 5.0)),
    "fallback_trade":             bool(getattr(__import__("config"), "FALLBACK_TRADE", False)),
    "allow_countertrend_in_range": True,
    "min_volume_ratio_entry":      0.40,    # reject when vol < 40% of avg
    "min_breakout_volume_ratio":   1.20,    # breakout requires 120% of avg volume
    "enable_entry_quality_filter": True,    # VWAP/ADX/DI/ST/MACD/ATR execution quality
    "entry_quality_block_live":     True,
    "entry_quality_max_penalty":    1.25,
    "enable_indicator_confluence":  True,    # grouped all-indicator trade confluence
    "indicator_confluence_block_live": False,
    "indicator_confluence_min_live": 4.8,
    "indicator_confluence_max_boost": 1.25,
    "indicator_confluence_max_penalty": 1.25,
    "enable_structure_confirmation": True,   # HH/HL + BOS/CHoCH quality layer
    "structure_confirm_block_live": False,
    "structure_lookback": 60,
    "structure_htf_lookback": 40,
    "structure_max_boost": 1.20,
    "structure_max_penalty": 1.20,
    "structure_retest_tolerance_pct": 0.002,
    "enable_market_quality_gate": True,
    "market_quality_block_live": True,
    "market_quality_max_penalty": 1.50,
    "min_core_indicator_coverage": 0.65,
    "enable_candidate_quality_gate": True,
    "candidate_quality_block_live": False,
    "min_candidate_confirmations": 2,
    "candidate_quality_max_penalty": 1.00,
    "enable_mtf_indicator_context": True,
    "mtf_indicator_block_live": False,
    "mtf_indicator_min_live": -0.35,
    "mtf_indicator_max_boost": 1.25,
    "mtf_indicator_max_penalty": 1.25,
    "allow_missing_volume_for_indices": True,
    "allow_missing_volume_entry": False,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_direction(direction: Optional[str]) -> Optional[str]:
    if not direction:
        return None
    d = str(direction).upper().strip()
    if d in ("BUY", "LONG", "CALL", "BUY_CALL", "BULLISH"):
        return "BUY"
    if d in ("SELL", "SHORT", "PUT", "BUY_PUT", "BEARISH"):
        return "SELL"
    return None


def _safe_get_htf_bias(df_htf) -> str:
    try:
        bias = get_htf_bias(df_htf)
        return str(bias).upper() if bias else "SIDEWAYS"
    except Exception:
        return "SIDEWAYS"


def _adjust_score_for_regime(strategy: str, score: float, regime: str) -> float:
    strategy = str(strategy).lower().strip()
    regime   = str(regime).upper().strip()

    if regime == "TREND":
        if strategy == "trend":
            return score + 1.0
        if strategy == "breakout":
            return score + 0.75
        if strategy == "mean_reversion":
            return score - 0.50

    if regime == "BREAKOUT":
        if strategy == "breakout":
            return score + 1.25
        if strategy == "trend":
            return score + 0.50

    if regime == "RANGE":
        if strategy == "mean_reversion":
            return score + 1.0
        if strategy == "trend":
            return score - 0.25
        if strategy == "breakout":
            return score - 0.25

    if regime in ("SIDEWAYS", "UNCLEAR"):
        if strategy == "mean_reversion":
            return score + 0.25
        if strategy == "scalping":
            return score + 0.50    # scalping does well in sideways markets

    if regime in ("EARLY_TREND",):
        if strategy == "ma_cross":
            return score + 0.50   # MA cross excels at catching emerging trends

    return score


def _structure_confirmation_adjustment(
    df,
    df_htf,
    direction: str,
    cfg: dict,
) -> Dict[str, Any]:
    """
    Convert HH/HL, LH/LL, BOS and CHoCH into a bounded quality modifier.

    Structure is a confirmation layer: it boosts aligned candidates, penalizes
    opposing candidates, and optionally marks hard blocks for live mode.
    """
    if not bool(cfg.get("enable_structure_confirmation", True)):
        return {"score_modifier": 0.0, "enabled": False}

    side = _normalize_direction(direction)
    wanted = "BULLISH" if side == "BUY" else "BEARISH" if side == "SELL" else "NEUTRAL"
    max_boost = abs(float(cfg.get("structure_max_boost", 1.20) or 1.20))
    max_penalty = abs(float(cfg.get("structure_max_penalty", 1.20) or 1.20))
    try:
        from institutional_indicators import detect_market_structure

        primary = detect_market_structure(
            df,
            lookback=int(cfg.get("structure_lookback", 60) or 60),
        )
        htf = {}
        if df_htf is not None and hasattr(df_htf, "__len__") and len(df_htf) >= 12:
            htf = detect_market_structure(
                df_htf,
                lookback=int(cfg.get("structure_htf_lookback", 40) or 40),
            )
    except Exception as exc:
        logger.debug("structure confirmation unavailable: %s", exc)
        return {"score_modifier": 0.0, "enabled": True, "error": str(exc)}

    def _dir(ctx: Dict[str, Any]) -> str:
        return str(ctx.get("structure_direction") or "NEUTRAL").upper()

    def _opposite(value: str) -> bool:
        return (
            (wanted == "BULLISH" and value == "BEARISH")
            or (wanted == "BEARISH" and value == "BULLISH")
        )

    p_dir = _dir(primary)
    htf_dir = _dir(htf) if htf else "NEUTRAL"
    p_aligned = wanted != "NEUTRAL" and p_dir == wanted
    htf_aligned = wanted != "NEUTRAL" and htf_dir == wanted
    p_opposes = wanted != "NEUTRAL" and _opposite(p_dir)
    htf_opposes = wanted != "NEUTRAL" and _opposite(htf_dir)

    score_mod = 0.0
    reasons: List[str] = []
    if p_aligned:
        score_mod += 0.70
        reasons.append("primary_structure_aligned")
    elif p_opposes:
        score_mod -= 0.85
        reasons.append("primary_structure_opposes")

    if htf_aligned:
        score_mod += 0.35
        reasons.append("htf_structure_aligned")
    elif htf_opposes:
        score_mod -= 0.45
        reasons.append("htf_structure_opposes")

    choch = str(primary.get("choch_direction") or "").upper()
    bos = str(primary.get("last_bos") or "").upper()
    if choch == wanted:
        score_mod += 0.25
        reasons.append("choch_aligned")
    elif choch and _opposite(choch):
        score_mod -= 0.35
        reasons.append("choch_opposes")
    if bos == wanted:
        score_mod += 0.20
        reasons.append("bos_aligned")
    elif bos and _opposite(bos):
        score_mod -= 0.25
        reasons.append("bos_opposes")

    try:
        close_col = "Close" if "Close" in df.columns else "close"
        close = float(df[close_col].iloc[-1])
        level = float(
            primary.get("last_swing_high", 0.0)
            if wanted == "BULLISH"
            else primary.get("last_swing_low", 0.0)
        )
        tolerance = float(cfg.get("structure_retest_tolerance_pct", 0.002) or 0.002)
        retest_confirmed = bool(level > 0 and abs(close - level) / level <= tolerance)
    except Exception:
        level = 0.0
        retest_confirmed = False
    if retest_confirmed and (p_aligned or bos == wanted or choch == wanted):
        score_mod += 0.20
        reasons.append("structure_retest")

    if score_mod >= 0:
        score_mod = min(score_mod, max_boost)
    else:
        score_mod = max(score_mod, -max_penalty)

    hard_block = bool(
        cfg.get("structure_confirm_block_live", False)
        and (p_opposes or htf_opposes or (choch and _opposite(choch)))
    )
    return {
        "enabled": True,
        "score_modifier": round(float(score_mod), 3),
        "hard_block": hard_block,
        "wanted_direction": wanted,
        "structure": primary.get("structure"),
        "structure_direction": p_dir,
        "structure_label": primary.get("structure_label", ""),
        "labels": primary.get("labels", {}),
        "last_bos": primary.get("last_bos"),
        "choch_direction": primary.get("choch_direction"),
        "choch_detected": bool(primary.get("choch_detected", False)),
        "structure_score": primary.get("structure_score", 0.0),
        "last_swing_high": primary.get("last_swing_high", 0.0),
        "last_swing_low": primary.get("last_swing_low", 0.0),
        "aligned": bool(p_aligned),
        "opposes": bool(p_opposes),
        "htf_structure": htf.get("structure") if htf else "",
        "htf_direction": htf_dir,
        "htf_label": htf.get("structure_label", "") if htf else "",
        "htf_aligned": bool(htf_aligned),
        "htf_opposes": bool(htf_opposes),
        "retest_confirmed": bool(retest_confirmed),
        "retest_level": round(float(level or 0.0), 2),
        "reasons": reasons,
    }


def _market_quality_snapshot(df, cfg: dict) -> Dict[str, Any]:
    """
    Shared data/indicator health check applied before any strategy can win.

    This catches weak inputs that make otherwise good strategies noisy: missing
    core indicators, NaN-heavy last bars, flat ATR/range, and low participation.
    """
    if not bool(cfg.get("enable_market_quality_gate", True)):
        return {"enabled": False, "score_modifier": 0.0, "hard_block": False}

    reasons: List[str] = []
    metrics: Dict[str, Any] = {}
    penalty = 0.0
    try:
        if df is None or not hasattr(df, "columns") or len(df) < 30:
            return {
                "enabled": True,
                "score_modifier": -1.0,
                "hard_block": True,
                "reasons": ["insufficient_bars"],
                "metrics": {"bars": 0 if df is None else len(df)},
            }

        cols_l = {str(c).lower(): c for c in df.columns}
        groups = (
            ("close",),
            ("high",),
            ("low",),
            ("ema_fast", "ema_9", "ema20", "ema_20"),
            ("ema_slow", "ema_21", "ema50", "ema_50"),
            ("ema_trend", "ema_200", "ema200"),
            ("rsi", "rsi_14"),
            ("atr", "atr_14"),
            ("vwap",),
            ("volume_ratio", "vol_ratio"),
        )
        present = 0
        last_missing = 0
        for group in groups:
            col = next((cols_l[g] for g in group if g in cols_l), None)
            if col is None:
                continue
            present += 1
            try:
                if pd.isna(df[col].iloc[-1]):
                    last_missing += 1
            except Exception:
                last_missing += 1

        coverage = present / max(1, len(groups))
        metrics["indicator_coverage"] = round(coverage, 3)
        metrics["last_bar_missing"] = int(last_missing)
        min_cov = float(cfg.get("min_core_indicator_coverage", 0.65) or 0.65)
        if coverage < min_cov:
            penalty -= 0.80
            reasons.append("low_indicator_coverage")
        if last_missing >= 3:
            penalty -= 0.50
            reasons.append("last_bar_indicator_gaps")

        close_col = cols_l.get("close")
        high_col = cols_l.get("high")
        low_col = cols_l.get("low")
        if close_col and high_col and low_col:
            close = pd.to_numeric(df[close_col], errors="coerce")
            high = pd.to_numeric(df[high_col], errors="coerce")
            low = pd.to_numeric(df[low_col], errors="coerce")
            price = float(close.iloc[-1])
            bar_range_pct = float((high.iloc[-1] - low.iloc[-1]) / price) if price else 0.0
            recent_range_pct = float((high.tail(10).max() - low.tail(10).min()) / price) if price else 0.0
            metrics["bar_range_pct"] = round(bar_range_pct, 5)
            metrics["recent_range_pct"] = round(recent_range_pct, 5)
            if recent_range_pct < 0.0015:
                penalty -= 0.45
                reasons.append("flat_recent_range")

        vr_col = cols_l.get("volume_ratio") or cols_l.get("vol_ratio")
        if vr_col:
            try:
                vr = float(pd.to_numeric(df[vr_col], errors="coerce").iloc[-1])
                metrics["volume_ratio"] = round(vr, 3)
                if 0 < vr < float(cfg.get("min_volume_ratio_entry", 0.40) or 0.40):
                    penalty -= 0.55
                    reasons.append("thin_volume")
            except Exception:
                pass

        max_penalty = abs(float(cfg.get("market_quality_max_penalty", 1.50) or 1.50))
        score_mod = max(-max_penalty, penalty)
        hard_block = bool(score_mod <= -1.25 or coverage < max(0.30, min_cov * 0.55))
        return {
            "enabled": True,
            "score_modifier": round(score_mod, 3),
            "hard_block": hard_block,
            "reasons": reasons,
            "metrics": metrics,
        }
    except Exception as exc:
        logger.debug("market quality snapshot unavailable: %s", exc)
        return {
            "enabled": True,
            "score_modifier": 0.0,
            "hard_block": False,
            "reasons": ["market_quality_unavailable"],
            "metrics": {},
        }


def _candidate_quality_adjustment(
    meta: Dict[str, Any],
    factors: set,
    cfg: dict,
) -> Dict[str, Any]:
    """
    Universal candidate filter: require independent confirmations and penalize
    indicator conflicts before confluence boosting chooses a winner.
    """
    if not bool(cfg.get("enable_candidate_quality_gate", True)):
        return {"enabled": False, "score_modifier": 0.0, "hard_block": False}

    confirmations = {str(f) for f in (factors or set()) if f}
    reasons: List[str] = []
    if meta.get("structure_context", {}).get("aligned"):
        confirmations.add("MARKET_STRUCTURE")
    if float(meta.get("structure_mod", 0.0) or 0.0) > 0.05:
        confirmations.add("MARKET_STRUCTURE")
    if float(meta.get("entry_quality_mod", 0.0) or 0.0) > 0.05:
        confirmations.add("ENTRY_QUALITY")
    if float(meta.get("indicator_confluence_mod", 0.0) or 0.0) > 0.05:
        confirmations.add("INDICATOR_CONFLUENCE")
    try:
        if float(meta.get("volume_ratio", 0.0) or 0.0) >= 1.0:
            confirmations.add("VOLUME")
    except Exception:
        pass

    ic = meta.get("indicator_confluence", {}) or {}
    aligned_groups = int(ic.get("aligned_groups", 0) or 0)
    opposing_groups = int(ic.get("opposing_groups", 0) or 0)
    if aligned_groups >= 3:
        confirmations.add("BROAD_INDICATORS")

    min_confirm = int(cfg.get("min_candidate_confirmations", 2) or 2)
    score_mod = 0.0
    if len(confirmations) < min_confirm:
        missing = min_confirm - len(confirmations)
        score_mod -= min(
            abs(float(cfg.get("candidate_quality_max_penalty", 1.00) or 1.00)),
            0.40 * missing,
        )
        reasons.append("few_independent_confirmations")

    if opposing_groups >= aligned_groups + 2 and opposing_groups >= 3:
        score_mod -= 0.45
        reasons.append("broad_indicator_conflict")

    if meta.get("market_quality", {}).get("hard_block"):
        score_mod -= 0.55
        reasons.append("market_quality_headwind")

    max_penalty = abs(float(cfg.get("candidate_quality_max_penalty", 1.00) or 1.00))
    score_mod = max(-max_penalty, min(0.35, score_mod))
    hard_block = bool(
        cfg.get("candidate_quality_block_live", False)
        and (len(confirmations) == 0 or "broad_indicator_conflict" in reasons)
    )
    return {
        "enabled": True,
        "score_modifier": round(score_mod, 3),
        "hard_block": hard_block,
        "confirmations": sorted(confirmations),
        "confirmation_count": len(confirmations),
        "aligned_groups": aligned_groups,
        "opposing_groups": opposing_groups,
        "reasons": reasons,
    }


def _calculate_volatility(df) -> float:
    try:
        close_col = "Close" if "Close" in df.columns else "close"
        returns   = df[close_col].pct_change().dropna()
        return float(returns.std()) if len(returns) > 0 else 0.0
    except Exception:
        return 0.0


def _get_price(df) -> float:
    close_col = "Close" if "Close" in df.columns else "close"
    return float(df[close_col].iloc[-1])


def _get_qty(price: float, capital: float) -> int:
    """
    Estimate a naive position size based on account capital.
    This is an upper-bound estimate — the actual size is determined
    by PortfolioRiskManager / NiftyOptionsEngine before order placement.

    Parameters
    ----------
    price   : float — current market price (or option premium)
    capital : float — total account capital in INR
    """
    if price <= 0 or capital <= 0:
        return 0
    return max(1, int(capital / price))



def _passes_volume_gate(df, cfg: dict, symbol: str = "") -> bool:
    """
    Reject when volume_ratio < min_volume_ratio_entry.
    Prevents entries during dead-market / zero-participation conditions.

    Default min_volume_ratio_entry = 0.40 (40% of 20-bar average).
    Time-of-day relaxation: before 10:00 uses 0.20 (volume naturally low at open).
    Set to 0.0 in config to disable.
    """
    min_ratio = float(cfg.get("min_volume_ratio_entry", 0.40))
    # Relax threshold early in session — volume builds through the morning
    try:
        import datetime as _dtv
        _now_h = _dtv.datetime.now()
        _t = _now_h.time()
        from datetime import time as _dtm
        if _t < _dtm(10, 0):
            min_ratio = min(min_ratio, 0.20)   # first 45 min: volume still building
        elif _t < _dtm(11, 0):
            min_ratio = min(min_ratio, 0.30)   # 10-11 AM: partial relaxation
    except Exception:
        pass
    if min_ratio <= 0:
        return True
    try:
        vr_col = (
            "volume_ratio" if "volume_ratio" in df.columns
            else "vol_ratio" if "vol_ratio" in df.columns
            else None
        )
        symbol_u = str(symbol or "").upper()
        index_like = symbol_u in {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"}
        if vr_col is None:
            return bool(
                cfg.get("allow_missing_volume_entry", False)
                or (index_like and cfg.get("allow_missing_volume_for_indices", True))
                or cfg.get("feed_degraded", False)
            )
        vr = float(df[vr_col].iloc[-1])
        if vr <= 0:
            return bool(
                cfg.get("allow_missing_volume_entry", False)
                or (index_like and cfg.get("allow_missing_volume_for_indices", True))
                or cfg.get("feed_degraded", False)
            )
        return vr >= min_ratio
    except Exception:
        return True


def _ensure_indicator_columns(df, symbol: str = ""):
    """
    Ensure the scoring stack has the indicator columns it expects.

    Live data is normalised to Title-Case OHLCV, while some caches used lowercase
    names and some strategies expect legacy aliases. This adapter fills the
    common aliases once before scoring.
    """
    if df is None or not hasattr(df, "columns") or len(df) < 5:
        return df

    required_any = (
        ("ema_fast", "ema20", "ema_20"),
        ("ema_slow", "ema50", "ema_50"),
        ("ema_trend", "ema200", "ema_200"),
        ("rsi", "rsi_14"),
        ("atr", "atr_14"),
        ("volume_ratio", "vol_ratio"),
    )
    if all(any(col in df.columns for col in group) for group in required_any):
        return df

    out = df.copy()
    try:
        indicators = _get_cached_indicators(out, symbol)
        for key, value in indicators.items():
            try:
                if hasattr(value, "__len__") and len(value) == len(out):
                    out[key] = value.values if hasattr(value, "values") else value
            except Exception:
                continue
    except Exception:
        pass

    # Last-resort local compute for direct callers/tests that bypass live precompute.
    try:
        missing_core = not all(
            any(col in out.columns for col in group) for group in required_any[:5]
        )
        if missing_core:
            from indicators import add_all_indicators
            enriched = add_all_indicators(out, include_cpr=True)
            for col in enriched.columns:
                if col not in out.columns:
                    out[col] = enriched[col]
    except Exception:
        pass

    alias_pairs = {
        "ema20": "ema_20",
        "ema50": "ema_50",
        "ema200": "ema_200",
        "ema_fast": "ema_9",
        "ema_slow": "ema_50",
        "ema_trend": "ema_200",
        "volume_ratio": "vol_ratio",
        "atr": "atr_14",
        "rsi": "rsi_14",
    }
    for alias, source in alias_pairs.items():
        if alias not in out.columns and source in out.columns:
            out[alias] = out[source]

    return out


def _strategy_factor(strategy: str) -> str:
    try:
        from strategy_clusters import factor_of
        return str(factor_of(strategy))
    except Exception:
        return "OTHER:" + str(strategy).lower()


def _passes_htf_filter(
    direction: str,
    htf_bias: str,
    regime: str,
    cfg: Dict[str, Any],
) -> bool:
    if not cfg.get("require_htf_alignment", False):
        return True

    if htf_bias == "SIDEWAYS":
        return bool(cfg.get("allow_sideways_htf", True))

    if direction == "BUY"  and htf_bias == "BULLISH":
        return True
    if direction == "SELL" and htf_bias == "BEARISH":
        return True

    if regime == "RANGE" and cfg.get("allow_countertrend_in_range", True):
        return True

    return False


def _min_score_for_regime(regime: str, strategy: str, cfg: Dict[str, Any]) -> float:
    regime   = str(regime).upper().strip()
    strategy = str(strategy).lower().strip()

    if regime == "TREND":
        return float(cfg["trend_min_score"])
    if regime == "RANGE":
        return float(cfg["range_min_score"])
    if regime == "BREAKOUT":
        return float(cfg["breakout_min_score"])
    if strategy == "mean_reversion":
        return float(cfg["range_min_score"])
    return float(cfg["min_score"])


# ---------------------------------------------------------------------------
# Per-strategy wrappers
# ---------------------------------------------------------------------------

def run_trend_strategy(df, df_htf, option_data, config=None):
    score, direction = calculate_signal_score(df, df_htf, option_data)
    return {
        "strategy":  "trend",
        "score":     float(score),
        "direction": _normalize_direction(direction),
    }


def run_mean_reversion_strategy(df, df_htf, option_data):
    score, direction = calculate_signal_score(df, df_htf, option_data)
    return {
        "strategy":  "mean_reversion",
        "score":     float(score) - 0.25,
        "direction": _normalize_direction(direction),
    }


def run_breakout_strategy(df, df_htf, option_data):
    score, direction = calculate_signal_score(df, df_htf, option_data)
    return {
        "strategy":  "breakout",
        "score":     float(score),
        "direction": _normalize_direction(direction),
    }


def run_scalping_strategy(df, df_htf, option_data):
    """
    Scalping: VWAP + RSI + EMA crossover.
    Lower score offset (-0.5) to prefer higher-timeframe strategies
    unless market is quiet (RANGE / SIDEWAYS regime).
    """
    score, direction = calculate_signal_score(df, df_htf, option_data)
    return {
        "strategy":  "scalping",
        "score":     float(score) - 0.50,
        "direction": _normalize_direction(direction),
    }


def run_ma_cross_strategy(df, df_htf, option_data):
    """
    Moving average crossover / Supertrend.
    Score offset +0.10 in trending markets (boosted by regime adjustment).
    """
    score, direction = calculate_signal_score(df, df_htf, option_data)
    return {
        "strategy":  "ma_cross",
        "score":     float(score) + 0.10,
        "direction": _normalize_direction(direction),
    }


def run_orb_strategy(df, df_htf, option_data):
    """Opening Range Breakout — best in 9:30-10:30 window."""
    if not _ORB_AVAILABLE:
        return {"strategy": "orb", "score": 0.0, "direction": None}
    r = _orb_signal(df)
    action = r.get("action", "HOLD")
    return {
        "strategy":  "orb",
        "score":     float(r.get("confidence", 0.0)) * 8.0,
        "direction": _normalize_direction(action) if action != "HOLD" else None,
    }

def run_vwap_reversion_strategy(df, df_htf, option_data):
    """VWAP deviation reversion — best in 11:00-13:00 window."""
    if not _VWAP_REV_AVAILABLE:
        return {"strategy": "vwap_reversion", "score": 0.0, "direction": None}
    r = _vwap_rev_signal(df)
    action = r.get("action", "HOLD")
    return {
        "strategy":  "vwap_reversion",
        "score":     float(r.get("confidence", 0.0)) * 7.5,
        "direction": _normalize_direction(action) if action != "HOLD" else None,
    }

def run_supertrend_mtf_strategy(df, df_htf, option_data):
    """Multi-timeframe Supertrend — works across all session windows."""
    if not _ST_MTF_AVAILABLE:
        return {"strategy": "supertrend_mtf", "score": 0.0, "direction": None}
    r = _st_mtf_signal(df, df_htf)
    action = r.get("action", "HOLD")
    return {
        "strategy":  "supertrend_mtf",
        "score":     float(r.get("confidence", 0.0)) * 8.5,
        "direction": _normalize_direction(action) if action != "HOLD" else None,
    }


def run_institutional_scalp_strategy(df, df_htf, option_data):
    """CVD-based institutional scalping — reads order flow, not lagging EMAs."""
    if not _INST_AVAILABLE:
        return {"strategy": "institutional_scalp", "score": 0.0, "direction": None}
    r = _cvd_scalp(df)
    action = r.get("action", "HOLD")
    return {"strategy": "institutional_scalp",
            "score": float(r.get("confidence", 0.0)) * 9.0,
            "direction": _normalize_direction(action) if action != "HOLD" else None}

def run_order_block_strategy(df, df_htf, option_data):
    """Order Block retest — institutional entry zones."""
    if not _INST_AVAILABLE:
        return {"strategy": "order_block", "score": 0.0, "direction": None}
    r = _ob_signal(df)
    action = r.get("action", "HOLD")
    return {"strategy": "order_block",
            "score": float(r.get("confidence", 0.0)) * 9.0,
            "direction": _normalize_direction(action) if action != "HOLD" else None}

def run_liquidity_sweep_strategy(df, df_htf, option_data):
    """Liquidity sweep reversal — catches stop hunts after institutions accumulate."""
    if not _INST_AVAILABLE:
        return {"strategy": "liquidity_sweep", "score": 0.0, "direction": None}
    r = _liq_sweep(df)
    action = r.get("action", "HOLD")
    return {"strategy": "liquidity_sweep",
            "score": float(r.get("confidence", 0.0)) * 9.5,
            "direction": _normalize_direction(action) if action != "HOLD" else None}

def run_vpoc_magnet_strategy(df, df_htf, option_data):
    """VPOC magnet — price drawn to highest volume level."""
    if not _INST_AVAILABLE:
        return {"strategy": "vpoc_magnet", "score": 0.0, "direction": None}
    r = _vpoc_magnet(df)
    action = r.get("action", "HOLD")
    return {"strategy": "vpoc_magnet",
            "score": float(r.get("confidence", 0.0)) * 8.0,
            "direction": _normalize_direction(action) if action != "HOLD" else None}

def run_hour_orb_strategy(df, df_htf, option_data):
    """1-Hour ORB — institutional timeframe (9:15-10:15)."""
    if not _INST_AVAILABLE:
        return {"strategy": "hour_orb", "score": 0.0, "direction": None}
    r = _hour_orb(df)
    action = r.get("action", "HOLD")
    return {"strategy": "hour_orb",
            "score": float(r.get("confidence", 0.0)) * 9.5,
            "direction": _normalize_direction(action) if action != "HOLD" else None}

def run_market_structure_strategy(df, df_htf, option_data):
    """Market structure BOS/CHOCH — institutional trend definition."""
    if not _INST_AVAILABLE:
        return {"strategy": "market_structure", "score": 0.0, "direction": None}
    r = _ms_signal(df)
    action = r.get("action", "HOLD")
    return {"strategy": "market_structure",
            "score": float(r.get("confidence", 0.0)) * 9.0,
            "direction": _normalize_direction(action) if action != "HOLD" else None}



def run_rsi_divergence_strategy(df, df_htf, option_data):
    """RSI divergence — bullish/bearish divergence between price and RSI."""
    if not _ADV_AVAILABLE:
        return {"strategy": "rsi_divergence", "score": 0.0, "direction": None}
    r = _rsi_div_sig(df)
    action = r.get("action", "HOLD")
    return {"strategy": "rsi_divergence",
            "score": float(r.get("confidence", 0.0)) * 8.5 + float(r.get("score_boost", 0.0)),
            "direction": _normalize_direction(action) if action != "HOLD" else None}


def run_gap_fill_strategy(df, df_htf, option_data):
    """Gap fill — fade opening gaps > 0.5%. 65% fill same day."""
    if not _ADV_AVAILABLE:
        return {"strategy": "gap_fill", "score": 0.0, "direction": None}
    r = _gap_fill_sig(df)
    action = r.get("action", "HOLD")
    return {"strategy": "gap_fill",
            "score": float(r.get("confidence", 0.0)) * 8.0 + float(r.get("score_boost", 0.0)),
            "direction": _normalize_direction(action) if action != "HOLD" else None}


def run_ichimoku_strategy(df, df_htf, option_data):
    """Ichimoku Cloud — Asian institutional trend confirmation."""
    if not _ADV_AVAILABLE:
        return {"strategy": "ichimoku", "score": 0.0, "direction": None}
    r = _ichimoku_sig(df)
    action = r.get("action", "HOLD")
    return {"strategy": "ichimoku",
            "score": float(r.get("confidence", 0.0)) * 8.0 + float(r.get("score_boost", 0.0)),
            "direction": _normalize_direction(action) if action != "HOLD" else None}


def run_vp_breakout_strategy(df, df_htf, option_data):
    """Volume Profile VAH/VAL breakout — institutional level confirmation."""
    if not _ADV_AVAILABLE:
        return {"strategy": "vp_breakout", "score": 0.0, "direction": None}
    r = _vp_breakout_sig(df)
    action = r.get("action", "HOLD")
    return {"strategy": "vp_breakout",
            "score": float(r.get("confidence", 0.0)) * 8.5 + float(r.get("score_boost", 0.0)),
            "direction": _normalize_direction(action) if action != "HOLD" else None}


def run_stat_arb_strategy(df, df_htf, option_data):
    """NIFTY-BANKNIFTY statistical arbitrage — DE Shaw Z-score spread."""
    try:
        from institutional_alpha import get_alpha_engine
        alpha = get_alpha_engine()
        # Needs both spot prices — uses cached values from last cycle
        nifty_price = float(df["Close"].iloc[-1] if "Close" in df.columns else df["close"].iloc[-1])
        # BNF price approximated from ratio
        sig = alpha.stat_arb.signal(nifty_price, nifty_price * 2.2)  # 2.2 approximate ratio
        action = sig.get("action", "HOLD")
        preferred = sig.get("preferred", "")
        return {"strategy": "stat_arb_deshaw",
                "score": float(sig.get("confidence", 0.0)) * 7.5 + float(sig.get("score_boost", 0.0)),
                "direction": _normalize_direction(action) if action != "HOLD" and preferred else None}
    except Exception:
        return {"strategy": "stat_arb_deshaw", "score": 0.0, "direction": None}


def calculate_cpr(df: "pd.DataFrame") -> dict:
    """
    Central Pivot Range — most widely used level by Indian traders.
    CPR = (High + Low + Close) / 3  (using previous day's data)
    TC  = (Pivot + High) / 2        (Top Central)
    BC  = (Pivot + Low) / 2         (Bottom Central)
    """
    try:
        import pandas as pd
        close_col = "Close" if "Close" in df.columns else "close"
        high_col  = "High"  if "High"  in df.columns else "high"
        low_col   = "Low"   if "Low"   in df.columns else "low"
        # Use the full previous session (78 5-min bars = 1 trading day)
        # If we have more than 156 bars (2 days), use bars 78-156 for prev day
        if len(df) >= 156:
            prev = df.iloc[78:156]   # actual previous day
        else:
            prev_len = min(78, len(df) - 1)
            prev     = df.iloc[:prev_len]
        H = float(prev[high_col].max())
        L = float(prev[low_col].min())
        C = float(prev[close_col].iloc[-1])
        pivot = round((H + L + C) / 3, 2)
        tc    = round((pivot + H) / 2, 2)
        bc    = round((pivot + L) / 2, 2)
        return {"pivot": pivot, "tc": tc, "bc": bc, "H": H, "L": L}
    except Exception:
        return {}


def run_cpr_strategy(df, df_htf, option_data):
    """
    CPR (Central Pivot Range) Strategy.

    BUY signals:
    - Price crossing ABOVE TC (Top Central) with volume → breakout above CPR
    - Price bouncing off BC (Bottom Central) as support → reversion buy

    SELL signals:
    - Price crossing BELOW BC (Bottom Central) → breakdown below CPR
    - Price rejected at TC (Top Central) → reversion sell

    Score boosted when:
    - Pivot level is narrow (< 0.3% of price) → trending day likely
    - Price is far from pivot → strong directional bias
    """
    if df is None or len(df) < 80:
        return {"strategy": "cpr", "score": 0.0, "direction": None}

    try:
        cpr = calculate_cpr(df)
        if not cpr or not cpr.get("pivot"):
            return {"strategy": "cpr", "score": 0.0, "direction": None}

        close_col = "Close" if "Close" in df.columns else "close"
        vol_col   = "Volume" if "Volume" in df.columns else "volume"
        close     = float(df[close_col].iloc[-1])
        prev_close = float(df[close_col].iloc[-2])

        pivot = cpr["pivot"]
        tc    = cpr["tc"]
        bc    = cpr["bc"]

        # CPR width as % of price — narrow = strong trend day
        cpr_width_pct = abs(tc - bc) / pivot

        direction = None
        score     = 0.0

        # Bullish: price crossed above TC
        if prev_close <= tc and close > tc:
            direction = "BUY"
            score     = 6.5 + (1.5 if cpr_width_pct < 0.003 else 0)

        # Bullish: price at BC support (within 0.1%)
        elif abs(close - bc) / bc < 0.001 and close > bc * 0.998:
            direction = "BUY"
            score     = 5.5

        # Bearish: price crossed below BC
        elif prev_close >= bc and close < bc:
            direction = "SELL"
            score     = 6.5 + (1.5 if cpr_width_pct < 0.003 else 0)

        # Bearish: price rejected at TC (within 0.1%)
        elif abs(close - tc) / tc < 0.001 and close < tc * 1.002:
            direction = "SELL"
            score     = 5.5

        # Price below pivot = overall bearish bias
        elif close < pivot:
            direction = "SELL"
            score     = 4.0

        # Price above pivot = overall bullish bias
        elif close > pivot:
            direction = "BUY"
            score     = 4.0

        if not direction:
            return {"strategy": "cpr", "score": 0.0, "direction": None}

        return {
            "strategy":  "cpr",
            "score":     round(score, 2),
            "direction": _normalize_direction(direction),
            "pivot":     pivot,
            "tc":        tc,
            "bc":        bc,
        }
    except Exception as e:
        return {"strategy": "cpr", "score": 0.0, "direction": None}


# ── Book strategy imports (all optional — system runs without them) ────────────


try:
    from bhav_copy import delivery_pct_score as _bhav_score, oi_quadrant_score as _oi_q
    _BHAV_AVAILABLE = True
except ImportError:
    _BHAV_AVAILABLE = False

try:
    from cross_asset import get_cross_asset_score as _ca_score, get_market_bias as _ca_bias
    _CA_AVAILABLE = True
except ImportError:
    _CA_AVAILABLE = False

try:
    from intraday_profile import get_time_bucket_weight as _tb_weight
    _TB_AVAILABLE = True
except ImportError:
    _TB_AVAILABLE = False

try:
    from strike_pcr import build_strike_pcr_map as _build_pcr, get_score_mod as _pcr_mod
    _PCR_AVAILABLE = True
except ImportError:
    _PCR_AVAILABLE = False

try:
    from feature_importance import get_tracker as _get_fi_tracker
    _FI_AVAILABLE = True
except ImportError:
    _FI_AVAILABLE = False


try:
    from participant_oi import get_participant_score as _part_score
    _PART_AVAILABLE = True
except ImportError:
    _PART_AVAILABLE = False

try:
    from fno_ban_list import is_banned as _is_banned
    _BAN_AVAILABLE = True
except ImportError:
    _BAN_AVAILABLE = False

try:
    from expiry_regime import get_expiry_regime as _get_expiry, apply_expiry_score_modifier as _exp_mod, get_sip_calendar_boost as _sip_boost
    _EXPIRY_AVAILABLE = True
except ImportError:
    _EXPIRY_AVAILABLE = False

try:
    from bulk_deals import bulk_deal_score as _bulk_score
    _BULK_AVAILABLE = True
except ImportError:
    _BULK_AVAILABLE = False

try:
    from theta_strategy import get_theta_score_mod as _theta_mod, pcr_momentum_signal as _pcr_mom
    _THETA_AVAILABLE = True
except ImportError:
    _THETA_AVAILABLE = False

try:
    from index_rebalancing import get_rebalancing_signal as _rebal_score
    _REBAL_AVAILABLE = True
except ImportError:
    _REBAL_AVAILABLE = False


try:
    from news_nlp import get_news_sentiment as _news_score
    _NEWS_AVAILABLE = True
except ImportError:
    _NEWS_AVAILABLE = False

try:
    from corporate_actions import has_corporate_action_today as _has_ca
    _CA_AVAILABLE2 = True
except ImportError:
    _CA_AVAILABLE2 = False

_INDEX_SYMBOLS_PB = {"NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY","SENSEX","NIFTYNEXT50","BANKEX"}

try:
    from bse_option_chain import get_sensex_banknifty_divergence as _bse_div
    _BSE_DIV = True
except ImportError:
    _BSE_DIV = False



# MTF pivot levels loaded once per generate_signal() call
_MTF_PIVOT_CACHE: dict = {}   # symbol → {weekly_levels, monthly_levels, ts}


try:
    from pivot_boss import (
        run_pivot_boss_strategy   as _pb_strategy,
        calc_cpr                  as _pb_calc_cpr,
        calc_floor_pivots         as _pb_floor_pivots,
        calc_camarilla_pivots     as _pb_camarilla,
        classify_cpr_width        as _pb_cpr_width,
        is_virgin_cpr             as _pb_virgin_cpr,
    )
    _PB_AVAILABLE = True
except ImportError:
    _PB_AVAILABLE = False

def _run_pb_index_only(df, df_htf, option_data):
    """Pivot Boss as strategy — indices only."""
    if not _PB_AVAILABLE:
        return {"strategy": "pivot_boss", "score": 0.0, "direction": None}
    symbol = ""
    if option_data and isinstance(option_data, dict):
        symbol = str(option_data.get("symbol", "")).upper()
    is_stock = bool(symbol) and symbol not in _INDEX_SYMBOLS_PB and                not any(idx in symbol for idx in _INDEX_SYMBOLS_PB)
    if is_stock:
        return {"strategy": "pivot_boss", "score": 0.0, "direction": None,
                "reason": "stock_pivot_boss_indicator_only"}
    return _pb_strategy(df, df_htf, option_data)

try:
    from candlestick_signals import run_candlestick_strategy as _cs_strategy
    _CS_AVAILABLE = True
except ImportError:
    _CS_AVAILABLE = False

try:
    from failed_breakout import run_failed_breakout_strategy as _fb_strategy
    _FB_AVAILABLE = True
except ImportError:
    _FB_AVAILABLE = False

try:
    from ttm_squeeze import run_ttm_squeeze_strategy as _ttm_strategy
    _TTM_AVAILABLE = True
except ImportError:
    _TTM_AVAILABLE = False

try:
    from holy_grail import run_holy_grail_strategy as _hg_strategy
    _HG_AVAILABLE = True
except ImportError:
    _HG_AVAILABLE = False

try:
    from williams_systems import (
        run_williams_r_strategy          as _wr_strategy,
        run_volatility_breakout_strategy as _vb_strategy,
        run_oops_strategy                as _oops_strategy,
    )
    _WR_AVAILABLE = True
except ImportError:
    _WR_AVAILABLE = False

try:
    from td_sequential import run_td_sequential_strategy as _td_strategy
    _TD_AVAILABLE = True
except ImportError:
    _TD_AVAILABLE = False

try:
    from weinstein_stage import run_weinstein_stage_strategy as _ws_strategy
    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False

try:
    from weinstein_stage import weinstein_stage_filter as _weinstein_filter
    _WEINSTEIN_AVAILABLE = True
except ImportError:
    _WEINSTEIN_AVAILABLE = False

# Weinstein stage analysis needs DAILY bars (MA_PRIMARY=150 sessions). The call
# site used to pass df_htf (1h), so the filter hit its no-daily-data path on
# nearly every stock (13 nonzero weinstein_mod across ~30k logged signals).
# candle_cache holds ~1y of 1d bars per symbol; cache one frame per symbol/day.
_WEINSTEIN_DAILY: dict = {}

def _weinstein_daily_df(symbol: str):
    from datetime import date as _wd_date
    today = _wd_date.today()
    hit = _WEINSTEIN_DAILY.get(symbol)
    if hit is not None and hit[0] == today:
        return hit[1]
    df_daily = None
    try:
        from candle_cache import get_cached_candles
        df_daily = get_cached_candles(symbol, "1d", days=400)
    except Exception:
        df_daily = None
    _WEINSTEIN_DAILY[symbol] = (today, df_daily)
    return df_daily

try:
    from strategy_performance_matrix import get_strategy_matrix as _get_sm
    _SM_AVAILABLE = True
    _strat_matrix_global = _get_sm()
except ImportError:
    _SM_AVAILABLE = False
    _strat_matrix_global = None

try:
    from iv_percentile import get_ivp
    _IVP_AVAILABLE = True
except ImportError:
    _IVP_AVAILABLE = False


def run_expiry_scalp_strategy(df, df_htf=None, option_data=None) -> Dict:
    """
    0DTE / 1DTE expiry scalping strategy.
    Fires when price is within 0.5% of max pain on expiry day.
    Also fires on rapid mean reversion to pivot on expiry afternoon (2-3 PM).
    This strategy fills the gap left when trend/breakout suppressed on expiry.
    """
    try:
        if _EXPIRY_AVAILABLE:
            _er = _get_expiry()
            dte = _er.get("days_to_expiry", 5)
            if dte > 1:
                return {"strategy": "expiry_scalp", "score": 0.0, "direction": None}
        else:
            return {"strategy": "expiry_scalp", "score": 0.0, "direction": None}

        if df is None or len(df) < 20:
            return {"strategy": "expiry_scalp", "score": 0.0, "direction": None}

        df_c = df.copy()
        df_c.columns = [c.lower() for c in df_c.columns]
        close = float(df_c["close"].iloc[-1])
        prev  = float(df_c["close"].iloc[-2])
        chg   = (close - prev) / prev

        # Signal 1: Max pain pull (near max pain = mean revert toward it)
        score = 0.0
        direction = None
        if option_data and option_data.get("max_pain"):
            mp  = float(option_data.get("max_pain", close))
            dist = (mp - close) / close
            if abs(dist) < 0.005:   # within 0.5% of max pain
                if dist > 0 and chg < 0:   # below max pain, falling → bounce
                    direction = "BUY";  score = 5.5
                elif dist < 0 and chg > 0: # above max pain, rising → fade
                    direction = "SELL"; score = 5.5

        # Signal 2: Power hour momentum (14:30-15:25 on expiry)
        from datetime import datetime as _dt
        _now = _dt.now().time()
        from datetime import time as _dtime
        if _dtime(14, 30) <= _now <= _dtime(15, 20) and score == 0.0:
            if "ema_20" in df_c.columns and "ema_50" in df_c.columns:
                e20 = float(df_c["ema_20"].iloc[-1])
                e50 = float(df_c["ema_50"].iloc[-1])
                if close > e20 > e50 and chg > 0:
                    direction = "BUY";  score = 4.5
                elif close < e20 < e50 and chg < 0:
                    direction = "SELL"; score = 4.5

        return {
            "strategy":  "expiry_scalp",
            "score":     score,
            "direction": direction,
            "side":      direction,
        }
    except Exception as e:
        logger.debug("expiry_scalp: %s", e)
        return {"strategy": "expiry_scalp", "score": 0.0, "direction": None}


def run_morning_momentum_strategy(df, df_htf=None, option_data=None) -> dict:
    """
    MORNING MOMENTUM — fires in first 30 min (9:15-9:45 AM).
    Uses pre-market GIFT Nifty gap + first-bar direction.
    Fills the gap when ORB hasn't established yet.
    Requires: GIFT Nifty gap > 0.3% OR strong first-bar move.
    """
    try:
        from datetime import datetime as _dt, time as _dtime
        _now = _dt.now().time()
        # Only fire in first 30 min
        if not (_dtime(9,15) <= _now <= _dtime(9,45)):
            return {"strategy": "morning_momentum", "score": 0.0, "direction": None, "side": None}

        if df is None or len(df) < 3:
            return {"strategy": "morning_momentum", "score": 0.0, "direction": None, "side": None}

        df_c = df.copy()
        df_c.columns = [c.lower() for c in df_c.columns]

        # Today's bars only (after 9:15)
        today_bars = df_c.tail(min(6, len(df_c)))
        if len(today_bars) < 1:
            return {"strategy": "morning_momentum", "score": 0.0, "direction": None, "side": None}

        open_price = float(today_bars["open"].iloc[0]  if "open"  in today_bars.columns else today_bars.iloc[0,-1])
        close      = float(today_bars["close"].iloc[-1] if "close" in today_bars.columns else today_bars.iloc[-1,-1])
        prev_close = float(df_c["close"].iloc[-len(today_bars)-1]) if len(df_c) > len(today_bars) else open_price

        gap_pct  = (open_price - prev_close) / prev_close * 100
        move_pct = (close - open_price) / open_price * 100

        direction = None
        score     = 0.0

        # Gap up + continuing: buy
        if gap_pct > 0.3 and move_pct > 0.1:
            direction = "BUY"
            score     = 3.5 + min(gap_pct, 1.5)
        # Gap down + continuing: sell
        elif gap_pct < -0.3 and move_pct < -0.1:
            direction = "SELL"
            score     = 3.5 + min(abs(gap_pct), 1.5)
        # Strong opening move with no gap (strong institutional order at open)
        elif abs(move_pct) > 0.5:
            direction = "BUY" if move_pct > 0 else "SELL"
            score     = 3.0

        return {
            "strategy":  "morning_momentum",
            "score":     round(score, 2),
            "direction": direction,
            "side":      direction,
            "gap_pct":   round(gap_pct, 3),
            "move_pct":  round(move_pct, 3),
        }
    except Exception as e:
        logger.debug("morning_momentum: %s", e)
        return {"strategy": "morning_momentum", "score": 0.0, "direction": None, "side": None}

# ── Additional availability flags ─────────────────────────────────────────
# These flags are set by the try/except import blocks above.
# Provide safe defaults if not already set by imports.
try: _CDLSTK_AVAIL
except NameError: _CDLSTK_AVAIL = False

try: _CHPAT_AVAIL
except NameError: _CHPAT_AVAIL = False

try: _CPR_EMA_AVAIL
except NameError: _CPR_EMA_AVAIL = False

try: _TV_AVAIL
except NameError: _TV_AVAIL = False

try: _VP_ADV_AVAIL
except NameError: _VP_ADV_AVAIL = False

try: _CDLSTK_AVAIL
except NameError: _CDLSTK_AVAIL = False

try: _OF_STRAT_AVAIL
except NameError: _OF_STRAT_AVAIL = False



# ── Additional AVAIL flags for optional modules ───────────────────────────────
try:
    from fno_ban_list import get_ban_list as _fno_get_ban
    _BAN_AVAIL = True
except ImportError:
    _BAN_AVAIL = False

try:
    from bhavcopy_cache import get_history as _bhav_get
    _BHAV_AVAIL = True
except ImportError:
    _BHAV_AVAIL = False

try:
    from bulk_deals import get_bulk_deals as _bulk_get
    _BULK_AVAIL = True
except ImportError:
    _BULK_AVAIL = False

try:
    from corporate_actions import get_corporate_actions as _ca_get
    _CA_AVAIL = True
except ImportError:
    _CA_AVAIL = False

try:
    from expiry_regime import get_expiry_regime as _expiry_get
    _EXPIRY_AVAIL = True
except ImportError:
    _EXPIRY_AVAIL = False

try:
    from news_sentiment_engine import get_full_sentiment as _news_sent
    _NEWS_AVAIL = True
except ImportError:
    _NEWS_AVAIL = False

try:
    from participant_oi import get_participant_oi as _part_oi
    _PART_AVAIL = True
except ImportError:
    _PART_AVAIL = False

try:
    from price_structure import run_price_structure_strategy as _ps_strat
    _PS_AVAIL = True
    _PB_AVAIL = True
except ImportError:
    _PS_AVAIL = False
    _PB_AVAIL = False

try:
    from index_rebalancing import get_rebalancing_events as _rebal_get
    _REBAL_AVAIL = True
except ImportError:
    _REBAL_AVAIL = False

try:
    from strategy_performance_matrix import get_strategy_matrix as _sm_get
    _SM_AVAIL = True
except ImportError:
    _SM_AVAIL = False

try:
    from time_regime import get_time_regime as _tr_get
    _TB_AVAIL = True
except ImportError:
    _TB_AVAIL = False

try:
    from theta_strategy import run_theta_strategy as _theta_strat
    _THETA_AVAIL = True
except ImportError:
    _THETA_AVAIL = False

try:
    from whale_tracker import get_whale_activity as _whale_get
    _WHALE_AVAIL = True
except ImportError:
    _WHALE_AVAIL = False

# ── Import all extended strategies with proper guards ───────────────────────

# Advanced Confluence (SMC, VWAP Bands, Heikin Ashi, ORB 2min, CANSLIM)
try:
    from advanced_confluence import (
        run_smc_strategy, run_vwap_bands_strategy,
        run_heikin_ashi_strategy, run_orb_2min_strategy, run_canslim_filter,
    )
    _ADV_AVAIL = True
except Exception:
    _ADV_AVAIL = False
    def run_smc_strategy(df, **kw):          return {}
    def run_vwap_bands_strategy(df, **kw):   return {}
    def run_heikin_ashi_strategy(df, **kw):  return {}
    def run_orb_2min_strategy(df, **kw):     return {}
    def run_canslim_filter(df, **kw):        return {}

# Candlestick Signals
try:
    from candlestick_signals import run_candlestick_strategy
    _CDLSTK_AVAIL = True
except Exception:
    _CDLSTK_AVAIL = False
    def run_candlestick_strategy(df, **kw): return {}

# Chart Patterns
try:
    from chart_patterns import run_chart_pattern_strategy
    _CHPAT_AVAIL = True
except Exception:
    _CHPAT_AVAIL = False
    def run_chart_pattern_strategy(df, **kw): return {}

# CPR + EMA
try:
    from cpr_ema_strategy import run_cpr_ema_strategy
    _CPREMA_AVAIL = True
except Exception:
    _CPREMA_AVAIL = False
    def run_cpr_ema_strategy(df, **kw): return {}

# CPR/Camarilla index option scalper
try:
    from pivot_scalping_strategy import run_pivot_scalping_strategy
    _PIVOT_SCALP_AVAIL = True
except Exception:
    _PIVOT_SCALP_AVAIL = False
    def run_pivot_scalping_strategy(df, **kw): return {}

# Elliott Wave
try:
    from elliott_wave import run_elliott_wave_strategy
    _ELWAVE_AVAIL = True
except Exception:
    _ELWAVE_AVAIL = False
    def run_elliott_wave_strategy(df, **kw): return {}

# Failed Breakout
try:
    from failed_breakout import run_failed_breakout_strategy
    _FAILBK_AVAIL = True
except Exception:
    _FAILBK_AVAIL = False
    def run_failed_breakout_strategy(df, **kw): return {}

# Hero Zero
try:
    from hero_zero_strategy import run_hero_zero_strategy
    _HEROZERO_AVAIL = True
except Exception:
    _HEROZERO_AVAIL = False
    def run_hero_zero_strategy(df, **kw): return {}

# Holy Grail
try:
    from holy_grail import run_holy_grail_strategy
    _HOLYG_AVAIL = True
except Exception:
    _HOLYG_AVAIL = False
    def run_holy_grail_strategy(df, **kw): return {}

# Institutional: VSA, Anchored VWAP, Parabolic SAR, Delta-Neutral, Gamma, Event
try:
    from institutional_strategies import (
        run_vsa_strategy, run_anchored_vwap_strategy,
        run_parabolic_sar_strategy, run_delta_neutral_theta,
        run_gamma_scalp_strategy, run_event_driven_filter,
    )
    _INST_AVAIL = True
except Exception:
    _INST_AVAIL = False
    def run_vsa_strategy(df, **kw):             return {}
    def run_anchored_vwap_strategy(df, **kw):   return {}
    def run_parabolic_sar_strategy(df, **kw):   return {}
    def run_delta_neutral_theta(df, **kw):       return {}
    def run_gamma_scalp_strategy(df, **kw):     return {}
    def run_event_driven_filter(df, **kw):      return {}

# Order Flow
try:
    from order_flow import run_order_flow_strategy
    _ORDERFLOW_AVAIL = True
except Exception:
    _ORDERFLOW_AVAIL = False
    def run_order_flow_strategy(df, **kw): return {}

# Pivot Boss
try:
    from pivot_boss import run_pivot_boss_strategy
    _PIVBOSS_AVAIL = True
except Exception:
    _PIVBOSS_AVAIL = False
    def run_pivot_boss_strategy(df, **kw): return {}

# Price Structure
try:
    from price_structure import run_price_structure_strategy
    _PSTRUCT_AVAIL = True
except Exception:
    _PSTRUCT_AVAIL = False
    def run_price_structure_strategy(df, **kw): return {}

# TD Sequential
try:
    from td_sequential import run_td_sequential_strategy
    _TDSQ_AVAIL = True
except Exception:
    _TDSQ_AVAIL = False
    def run_td_sequential_strategy(df, **kw): return {}

# TTM Squeeze
try:
    from ttm_squeeze import run_ttm_squeeze_strategy
    _TTMSQ_AVAIL = True
except Exception:
    _TTMSQ_AVAIL = False
    def run_ttm_squeeze_strategy(df, **kw): return {}

# TradingView: EMA Ribbon, Waddah Attar, Chaikin MF, AO, BB%B, Ehlers, VIX Fix, Vol Profile
try:
    from tradingview_strategies import (
        run_ema_ribbon_strategy, run_waddah_attar_strategy,
        run_chaikin_mf_strategy, run_awesome_oscillator_strategy,
        run_bb_percentb_strategy, run_ehlers_fisher_strategy,
        run_vix_fix_strategy, run_volume_profile_full_strategy,
    )
    _TVSTRATS_AVAIL = True
except Exception:
    _TVSTRATS_AVAIL = False
    def run_ema_ribbon_strategy(df, **kw):           return {}
    def run_waddah_attar_strategy(df, **kw):         return {}
    def run_chaikin_mf_strategy(df, **kw):           return {}
    def run_awesome_oscillator_strategy(df, **kw):   return {}
    def run_bb_percentb_strategy(df, **kw):          return {}
    def run_ehlers_fisher_strategy(df, **kw):        return {}
    def run_vix_fix_strategy(df, **kw):              return {}
    def run_volume_profile_full_strategy(df, **kw):  return {}

# Volume Profile Advanced: VRVP, Failed Auction, EMA Cloud
try:
    from volume_profile_advanced import (
        run_vrvp_zone_strategy, run_failed_auction_strategy,
        run_ema_cloud_sd_strategy,
    )
    _VOLPADV_AVAIL = True
except Exception:
    _VOLPADV_AVAIL = False
    def run_vrvp_zone_strategy(df, **kw):       return {}
    def run_failed_auction_strategy(df, **kw):  return {}
    def run_ema_cloud_sd_strategy(df, **kw):    return {}

# Williams Systems: Williams %R, Volatility Breakout, OOPS
try:
    from williams_systems import (
        run_williams_r_strategy, run_volatility_breakout_strategy,
        run_oops_strategy,
    )
    _WILLIAMS_AVAIL = True
except Exception:
    _WILLIAMS_AVAIL = False
    def run_williams_r_strategy(df, **kw):          return {}
    def run_volatility_breakout_strategy(df, **kw): return {}
    def run_oops_strategy(df, **kw):                return {}

# Weinstein Stage Analysis
try:
    from weinstein_stage import run_weinstein_stage_strategy
    _WEINSTEIN_AVAIL = True
except Exception:
    _WEINSTEIN_AVAIL = False
    def run_weinstein_stage_strategy(df, **kw): return {}

# ── NEW STRATEGIES (gap analysis additions v1.2.6) ───────────────────────────
try:
    from strategies_new import (
        run_rsi2_mr_strategy,
        run_vix_extreme_strategy,
        run_alligator_ao_strategy,
        run_cross_momentum_strategy,
        run_elder_triple_screen_strategy,
        run_fii_dii_trend_strategy,
        run_gap_and_go_strategy,
        run_cci_trend_strategy,
        run_donchian_breakout_strategy,
        run_kama_trend_strategy,
        run_kst_strategy,
        run_elder_ray_strategy,
        run_harmonic_gartley_strategy,
        run_uo_strategy,
        run_aroon_strategy,
    )
    _NEW_STRATEGIES = [
        run_rsi2_mr_strategy,
        run_vix_extreme_strategy,
        run_alligator_ao_strategy,
        run_cross_momentum_strategy,
        run_elder_triple_screen_strategy,
        run_fii_dii_trend_strategy,
        run_gap_and_go_strategy,
        run_cci_trend_strategy,
        run_donchian_breakout_strategy,
        run_kama_trend_strategy,
        run_kst_strategy,
        run_elder_ray_strategy,
        run_harmonic_gartley_strategy,
        run_uo_strategy,
        run_aroon_strategy,
    ]
    logger.info("New strategies loaded: %d", len(_NEW_STRATEGIES))
except Exception as _ns_err:
    logger.warning("strategies_new import failed: %s", _ns_err)
    _NEW_STRATEGIES = []

try:
    from alternative_price_representations import (
        build_representation_features as _build_representation_features,
        run_hollow_candle_state_strategy,
        run_three_line_break_strategy,
        run_kagi_reversal_strategy,
        run_point_and_figure_strategy,
        run_range_bar_momentum_strategy,
        run_ohlcv_footprint_proxy_strategy,
    )
    _ALT_PRICE_REP_AVAILABLE = True
except Exception as _alt_rep_exc:
    logger.warning("alternative price representations unavailable: %s", _alt_rep_exc)
    _ALT_PRICE_REP_AVAILABLE = False

# ── STRATEGIES LIST ──────────────────────────────────────────────────────────
# run_scalping_strategy is OOS-disabled by default: walk-forward 2026-06-12
# (420d NIFTY 5m, 7 windows) measured 0/7 profitable windows, avg −₹377,616
# per window — by far the worst of the 5 backtested strategies. Re-enable
# only after it clears the validation harness: DISABLE_SCALPING_STRATEGY=false
import os as _os_se
_SCALPING_OOS_DISABLED = _os_se.getenv(
    "DISABLE_SCALPING_STRATEGY", "true").lower() == "true"

# Prune mechanism (EDGE_STRATEGY "reduce, don't add"): gate off strategies/modifiers
# by name — reversible, NEVER deletes code. Empty by default → zero behavior change.
try:
    import pruning as _pruning
    _PRUNE_STRATS, _PRUNE_MODS = _pruning.load_pruned()
except Exception:
    _PRUNE_STRATS, _PRUNE_MODS = set(), set()

# Macro filter is an UNVALIDATED heuristic. Default = SHADOW: record its decision on
# the signal but do NOT block/alter live selection (so it stays measurable, and an
# unproven filter can't gate trades). Set MACRO_FILTER_ENFORCE=true to enforce.
_MACRO_ENFORCE = _os_se.getenv("MACRO_FILTER_ENFORCE", "false").lower() == "true"

STRATEGIES = [
    # ── CORE (22 — always loaded) ────────────────────────────────────────────
    run_trend_strategy,
    run_mean_reversion_strategy,
    run_breakout_strategy,
    *([] if _SCALPING_OOS_DISABLED else [run_scalping_strategy]),
    *([run_pivot_scalping_strategy] if _PIVOT_SCALP_AVAIL else []),
    run_ma_cross_strategy,
    run_orb_strategy,
    run_vwap_reversion_strategy,
    run_supertrend_mtf_strategy,
    run_institutional_scalp_strategy,
    run_order_block_strategy,
    run_liquidity_sweep_strategy,
    run_vpoc_magnet_strategy,
    run_hour_orb_strategy,
    run_market_structure_strategy,
    run_rsi_divergence_strategy,
    run_gap_fill_strategy,
    run_ichimoku_strategy,
    run_vp_breakout_strategy,
    run_stat_arb_strategy,
    run_cpr_strategy,
    run_expiry_scalp_strategy,
    run_morning_momentum_strategy,

    # ── EXTENDED (conditional) ───────────────────────────────────────────────
    *([run_price_structure_strategy]   if _PSTRUCT_AVAIL   else []),
    *([run_smc_strategy, run_vwap_bands_strategy,
       run_heikin_ashi_strategy, run_orb_2min_strategy,
       run_canslim_filter]             if _ADV_AVAIL        else []),
    *([run_candlestick_strategy]       if _CDLSTK_AVAIL    else []),
    *([run_chart_pattern_strategy]     if _CHPAT_AVAIL     else []),
    *([run_cpr_ema_strategy]           if _CPREMA_AVAIL    else []),
    *([run_elliott_wave_strategy]      if _ELWAVE_AVAIL    else []),
    *([run_failed_breakout_strategy]   if _FAILBK_AVAIL    else []),
    *([run_hero_zero_strategy]         if _HEROZERO_AVAIL  else []),
    *([run_holy_grail_strategy]        if _HOLYG_AVAIL     else []),
    *([run_vsa_strategy, run_anchored_vwap_strategy,
       run_parabolic_sar_strategy, run_event_driven_filter]
                                       if _INST_AVAIL      else []),
    *([run_order_flow_strategy]        if _ORDERFLOW_AVAIL else []),
    *([run_pivot_boss_strategy]        if _PIVBOSS_AVAIL   else []),
    *([run_td_sequential_strategy]     if _TDSQ_AVAIL      else []),
    *([run_ema_ribbon_strategy, run_waddah_attar_strategy,
       run_chaikin_mf_strategy, run_awesome_oscillator_strategy,
       run_bb_percentb_strategy, run_ehlers_fisher_strategy,
       run_vix_fix_strategy, run_volume_profile_full_strategy]
                                       if _TVSTRATS_AVAIL  else []),
    *([run_ttm_squeeze_strategy]       if _TTMSQ_AVAIL     else []),
    *([run_vrvp_zone_strategy, run_failed_auction_strategy,
       run_ema_cloud_sd_strategy]      if _VOLPADV_AVAIL   else []),
    *([run_williams_r_strategy, run_volatility_breakout_strategy,
       run_oops_strategy]              if _WILLIAMS_AVAIL  else []),
    *([run_weinstein_stage_strategy]   if _WEINSTEIN_AVAIL else []),
    *([
        run_hollow_candle_state_strategy,
        run_three_line_break_strategy,
        run_kagi_reversal_strategy,
        run_point_and_figure_strategy,
        run_range_bar_momentum_strategy,
        run_ohlcv_footprint_proxy_strategy,
    ] if _ALT_PRICE_REP_AVAILABLE else []),

    # ── NEW STRATEGIES (gap analysis v1.2.6) ─────────────────────────────────
    *(_NEW_STRATEGIES),
]

# These are portfolio structures, not directional one-leg signals. Keep them
# callable for research, but never pass them through the CE/PE selector until a
# validated atomic multi-leg executor and delta-hedge lifecycle exist.
RESEARCH_ONLY_MULTILEG_STRATEGIES = (
    [run_delta_neutral_theta, run_gamma_scalp_strategy] if _INST_AVAIL else []
)


# ---------------------------------------------------------------------------
# Strategy call adapter
# ---------------------------------------------------------------------------
# Strategies were authored with INCONSISTENT signatures — (df, df_htf,
# option_data), (df, df_htf, symbol[, option_data]), or (df,) — but the engine
# historically called them ALL as strategy_fn(df, df_htf, option_data). That made
# ~16 raise TypeError every call (silently swallowed → never voted) and bound
# option_data to the wrong param in others. This adapter inspects each function's
# real signature ONCE (cached) and supplies the right argument for each positional
# parameter by name, so every strategy is finally called correctly.
import inspect as _inspect

_STRAT_CALL_NAMES: Dict[Any, Any] = {}


def _invoke_strategy(fn, df, df_htf, option_data, symbol):
    if fn not in _STRAT_CALL_NAMES:
        try:
            _sig = _inspect.signature(fn)
            if any(p.kind == p.VAR_POSITIONAL for p in _sig.parameters.values()):
                _names = None   # *args present → legacy positional call is safe
            else:
                _names = [n for n, p in _sig.parameters.items()
                          if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        except (ValueError, TypeError):
            _names = None
        _STRAT_CALL_NAMES[fn] = _names
    names = _STRAT_CALL_NAMES[fn]
    if not names:
        return fn(df, df_htf, option_data)
    _vals = {
        "df": df, "data": df, "ohlc": df, "candles": df,
        "df_htf": df_htf, "htf": df_htf, "df_high": df_htf, "higher_tf": df_htf,
        "symbol": symbol, "sym": symbol,
        "option_data": option_data, "option": option_data, "opt_data": option_data,
        "options": option_data, "df_1min": None, "df_1m": None,
        "config": None, "cfg": None, "capital": None,
    }
    args = []
    for p in names:
        if p in _vals:
            args.append(_vals[p])
        else:
            break   # unknown param → stop; the rest fall back to their defaults
    try:
        return fn(*args)
    except TypeError:
        for attempt in ((df, df_htf, option_data), (df, df_htf), (df,)):
            try:
                return fn(*attempt)
            except TypeError:
                continue
        raise


# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------


def _get_pivot_score_modifier(
    df,
    direction:  str,
    strategy:   str,
    day_type:   str = "NORMAL",
) -> float:
    """
    ROLE 2: Pivot Boss as indicator for all strategies.
    
    Adjusts any strategy's score based on pivot level relationship:
    
    BUY signals:
      Price just crossed above R1 → +1.5 (pivot confirmed breakout)
      Price approaching R1/R2 → -1.0 (resistance ahead, reduce target)
      Price bouncing off S1    → +1.0 (pivot support confirmed)
      Price below PDL          → -2.0 (strong bearish structure)
    
    SELL signals:
      Price just crossed below S1 → +1.5 (pivot confirmed breakdown)
      Price approaching S1/S2    → -1.0 (support ahead)
      Price rejected at R1       → +1.0 (pivot resistance confirmed)
      Price above PDH            → -2.0 (strong bullish structure)
    
    Day type:
      TRENDING + breakout strategy  → +1.0 (CPR confirms trend)
      SIDEWAYS + mean_reversion     → +1.0 (CPR confirms range)
      TRENDING + mean_reversion     → -1.0 (wrong strategy for day type)
      SIDEWAYS + breakout           → -1.5 (breakout less reliable today)
    """
    if not _PB_AVAILABLE or df is None or len(df) < 80:
        return 0.0
    
    try:
        df_c = df.copy()
        df_c.columns = [c.lower() for c in df_c.columns]
        
        if "close" not in df_c.columns:
            return 0.0
        
        # Get previous day data
        prev = df_c.iloc[:78] if len(df_c) >= 78 else df_c
        if len(prev) < 20:
            return 0.0
            
        H = float(prev["high"].max())  if "high"  in prev.columns else 0
        L = float(prev["low"].min())   if "low"   in prev.columns else 0
        C = float(prev["close"].iloc[-1])
        
        if H <= 0 or L <= 0:
            return 0.0
        
        floor  = _pb_floor_pivots(H, L, C)
        cpr    = _pb_calc_cpr(H, L, C)
        
        close      = float(df_c["close"].iloc[-1])
        prev_close = float(df_c["close"].iloc[-2])
        r1 = floor["R1"]; r2 = floor["R2"]
        s1 = floor["S1"]; s2 = floor["S2"]
        p  = floor["P"]
        
        # PDH/PDL
        pdh = float(prev["high"].max()) if "high" in prev.columns else 0
        pdl = float(prev["low"].min())  if "low"  in prev.columns else 0
        
        modifier = 0.0
        
        # ── Day type modifier ─────────────────────────────────────────
        strat_lc = strategy.lower()
        is_breakout = any(x in strat_lc for x in ["breakout","trend","orb","cpr","pivot"])
        is_reversion = any(x in strat_lc for x in ["mean_rev","vwap","scalp","mr"])
        
        if day_type == "TRENDING":
            if is_breakout:  modifier += 1.0
            if is_reversion: modifier -= 1.0
        elif day_type == "SIDEWAYS":
            if is_reversion: modifier += 1.0
            if is_breakout:  modifier -= 1.5
        
        # ── Price vs pivot levels ─────────────────────────────────────
        if direction == "BUY":
            # Just crossed above R1 → pivot breakout confirmed
            if prev_close <= r1 < close:
                modifier += 1.5
            # Approaching R1 (within 0.3%) → resistance ahead
            elif r1 > 0 and 0 < (r1 - close) / r1 < 0.003:
                modifier -= 1.0
            # Bouncing off S1 (within 0.2%)
            elif s1 > 0 and abs(close - s1) / s1 < 0.002 and close > prev_close:
                modifier += 1.0
            # Above PDH → strong structure
            elif pdh > 0 and close > pdh:
                modifier += 0.5
            # Below PDL → bearish structure, don't buy
            elif pdl > 0 and close < pdl:
                modifier -= 2.0
            # Price above pivot = bullish structure
            if close > p:
                modifier += 0.3
            else:
                modifier -= 0.3
                
        elif direction == "SELL":
            # Just crossed below S1 → pivot breakdown confirmed
            if prev_close >= s1 > close:
                modifier += 1.5
            # Approaching S1 (within 0.3%) → support ahead
            elif s1 > 0 and 0 < (close - s1) / s1 < 0.003:
                modifier -= 1.0
            # Rejected at R1 (within 0.2%)
            elif r1 > 0 and abs(close - r1) / r1 < 0.002 and close < prev_close:
                modifier += 1.0
            # Below PDL → strong bearish structure
            elif pdl > 0 and close < pdl:
                modifier += 0.5
            # Above PDH → bullish structure, don't sell
            elif pdh > 0 and close > pdh:
                modifier -= 2.0
            # Price below pivot = bearish structure
            if close < p:
                modifier += 0.3
            else:
                modifier -= 0.3
        
        return round(modifier, 2)
        
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug("Pivot indicator error: %s", e)
        return 0.0


def get_omnisource_score_modifier(symbol: str, direction: str) -> float:
    """
    Apply news + insider + options intelligence to signal score.
    Called from generate_signal() to boost/reduce confidence.
    """
    try:
        # 1. Symbol-specific news
        from omnisource_news_engine import get_symbol_specific_news
        sym_intel = get_symbol_specific_news(symbol)

        # Block if in F&O ban
        if sym_intel.get("in_fno_ban"):
            return -5.0  # effectively blocks this signal

        news_adj = sym_intel.get("score_adjustment", 0.0)

        # 2. WOW factors v2
        from wow_factors_v2 import get_all_wow_scores
        wow = get_all_wow_scores(symbol, direction)
        wow_adj = wow.get("total_wow_adj", 0.0) * 0.3  # 30% weight

        return round(news_adj + wow_adj, 2)
    except Exception:
        return 0.0


def _score_mtf_agreement(signal_5m: dict, df_htf) -> float:
    """
    IMPROVEMENT 6: Score multi-timeframe signal agreement.
    BUY on 5min + BUY on 15min + BUY on 1hr = strong signal (+1.5)
    BUY on 5min but SELL on 15min = weak signal (-0.5)
    """
    if not signal_5m or df_htf is None:
        return 0.0
    try:
        direction = signal_5m.get('direction','NEUTRAL')
        if direction == 'NEUTRAL':
            return 0.0
        # Quick EMA cross on HTF
        closes = list(df_htf['close'] if hasattr(df_htf,'close') else df_htf.get('close',[]))
        if len(closes) < 20:
            return 0.0
        ema9  = sum(closes[-9:])  / 9
        ema21 = sum(closes[-21:]) / 21
        htf_bull = ema9 > ema21
        htf_bear = ema9 < ema21
        if direction == 'BUY'  and htf_bull: return  1.0
        if direction == 'SELL' and htf_bear: return  1.0
        if direction == 'BUY'  and htf_bear: return -0.5
        if direction == 'SELL' and htf_bull: return -0.5
    except Exception as _e:
        import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)
    return 0.0


def get_cost_of_carry_signal(symbol: str) -> dict:
    """
    Cost-of-carry / futures basis signal.
    When futures trade at premium to spot: bulls rolling → bullish
    When futures at discount (backwardation): bears rolling → bearish
    Threshold: >0.3% premium = BULLISH, <-0.1% = BEARISH
    """
    try:
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0",
                          "Referer": "https://www.nseindia.com"})
        s.get("https://www.nseindia.com/", timeout=4)
        r = s.get(
            f"https://www.nseindia.com/api/quote-derivative?symbol={symbol}",
            timeout=8
        )
        if r.status_code != 200:
            return {"signal": "NEUTRAL", "score_adj": 0.0, "basis_pct": 0.0}
        data  = r.json()
        spot  = float(data.get("underlyingValue", 0) or 0)
        futures = 0.0
        for item in data.get("stocks", []):
            meta = item.get("metadata", {})
            if meta.get("instrumentType") == "FUT" and spot:
                futures = float(meta.get("lastPrice", 0) or 0)
                break
        if not spot or not futures:
            return {"signal": "NEUTRAL", "score_adj": 0.0, "basis_pct": 0.0}
        basis_pct = (futures - spot) / spot * 100
        if basis_pct > 0.3:
            return {"signal": "BULLISH", "score_adj": 0.4,
                    "basis_pct": round(basis_pct, 3),
                    "reason": f"Futures premium {basis_pct:.2f}% — bulls rolling"}
        elif basis_pct < -0.1:
            return {"signal": "BEARISH", "score_adj": -0.4,
                    "basis_pct": round(basis_pct, 3),
                    "reason": f"Futures discount {basis_pct:.2f}% — bears rolling"}
        return {"signal": "NEUTRAL", "score_adj": 0.0, "basis_pct": round(basis_pct, 3)}
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug("cost_of_carry %s: %s", symbol, e)
        return {"signal": "NEUTRAL", "score_adj": 0.0, "basis_pct": 0.0}


def _entry_quality_adjustment(
    df,
    direction: str,
    strategy: str,
    cfg: Dict[str, Any],
    paper_training_mode: bool = False,
) -> Dict[str, Any]:
    """
    Last-bar execution quality layer.

    Keeps the strategy engine from promoting signals that are directionally
    plausible but technically poor entries: weak ADX, no DI confirmation,
    counter-VWAP momentum, poor candle body, thin volume, or ATR over-extension.
    """
    if not bool(cfg.get("enable_entry_quality_filter", True)):
        return {"score_modifier": 0.0, "hard_block": False, "reasons": [], "metrics": {}}

    try:
        from indicators import calculate_entry_quality_score
        quality = calculate_entry_quality_score(df, direction=direction, strategy=strategy)
    except Exception as exc:
        logger.debug("entry quality unavailable: %s", exc)
        return {
            "score_modifier": 0.0,
            "hard_block": False,
            "reasons": ["quality_unavailable"],
            "metrics": {},
        }

    max_penalty = float(cfg.get("entry_quality_max_penalty", 1.25))
    if paper_training_mode:
        max_penalty = min(max_penalty, 0.50)
        quality["hard_block"] = False

    modifier = float(quality.get("score_modifier", 0.0) or 0.0)
    if modifier < 0:
        modifier = max(-max_penalty, modifier)
    quality["score_modifier"] = round(modifier, 3)
    return quality


def generate_signal(
    df,
    df_htf,
    symbol: str,
    option_data: Optional[dict] = None,
    config: Optional[Dict[str, Any]] = None,
    capital: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Generate a trading signal for `symbol`.

    Parameters
    ----------
    df          : pd.DataFrame — 5-min OHLCV with indicator columns
    df_htf      : pd.DataFrame — higher time-frame OHLCV (15-min / 1-hr)
    symbol      : str          — instrument name (e.g. "NIFTY")
    option_data : dict | None  — optional option chain data
    config      : dict | None  — override DEFAULT_CONFIG keys
    capital     : float | None — account capital for qty sizing.
                  If None, falls back to config.get_runtime_capital()
                  then to ₹1,00,000.
    """
    cfg = DEFAULT_CONFIG.copy()
    if config:
        cfg.update(config)
    paper_training_mode = bool(cfg.get("paper_training_mode", False))
    if paper_training_mode:
        # Training mode should collect labelled examples.  Live execution is
        # still controlled downstream by PAPER_ORDERS_ONLY / dual-mode arming.
        cfg["require_htf_alignment"] = False
        cfg["vote_threshold"] = min(float(cfg.get("vote_threshold", 1.5)), 0.75)
        cfg["post_confluence_min_score"] = min(
            float(cfg.get("post_confluence_min_score", 3.5)), 2.0
        )
        cfg["fallback_score_threshold"] = min(
            float(cfg.get("fallback_score_threshold", 2.5)), 1.5
        )
        cfg["min_volume_ratio_entry"] = min(
            float(cfg.get("min_volume_ratio_entry", 0.40)), 0.20
        )
        cfg["min_breakout_volume_ratio"] = min(
            float(cfg.get("min_breakout_volume_ratio", 1.20)), 0.80
        )

    df = _ensure_indicator_columns(df, symbol)
    if df_htf is not None and id(df_htf) != id(df):
        df_htf = _ensure_indicator_columns(df_htf, f"{symbol}:HTF")
    _market_quality = _market_quality_snapshot(df, cfg)

    # instrument_type gates two option-only score tweaks below (event-day boost,
    # earnings block). It was never defined → those branches NameError'd (masked
    # by try/except, so they silently never applied). Source it from option_data
    # when present; "" leaves the option-only tweaks inert, as they were.
    instrument_type = str((option_data or {}).get("instrument_type", "")).upper()

    # Resolve account capital for position-size estimate
    effective_capital: float = (
        float(capital)
        if capital is not None and float(capital) > 0
        else _DEFAULT_CAPITAL
    )

    regime   = detect_market_regime(df)
    regime   = str(regime).upper() if regime else "NO_TRADE"

    # ── Auto-blacklist + per-symbol pause check ──────────────────────────
    try:
        from trade_manager import check_and_blacklist_symbol as _bl_chk
        if _bl_chk(symbol):
            return {"strategy":"blacklisted","score":0,"direction":"NEUTRAL",
                    "reason":f"{symbol} auto-blacklisted (3+ SL hits — 14 day ban)"}
    except Exception: pass
    # SL cooldown (smart re-entry)
    try:
        from market_intelligence_hub import is_in_sl_cooldown as _isc
        if _isc(symbol):
            return {"strategy":"sl_cooldown","score":0,"direction":"NEUTRAL",
                    "reason":f"{symbol} in SL cooldown — 30min after stop hit"}
    except Exception: pass

    try:
        import json as _j, os as _o
        _pf = "paused_symbols.json"
        if _o.path.exists(_pf):
            if symbol.upper() in _j.loads(open(_pf).read()):
                return {"strategy":"paused","score":0,"direction":"NEUTRAL",
                        "reason":f"{symbol} manually paused — /pause_sym {symbol} to resume"}
    except Exception: pass

    # ── Per-strategy pause gate — load once for this scan cycle ──────
    _manually_paused_strategies: set = set()
    try:
        import json as _j2, os as _o2
        _spf = "paused_strategies.json"
        if _o2.path.exists(_spf):
            _raw_ps = _j2.loads(open(_spf).read())
            if isinstance(_raw_ps, list):
                _manually_paused_strategies = set(s.upper() for s in _raw_ps)
    except Exception: pass

    # ── Earnings proximity check (GAP 6) ──────────────────────────────
    try:
        from data_source_resilience import get_earnings_size_multiplier
        _em = get_earnings_size_multiplier(symbol)
        if _em == 0.0:
            return {"strategy":"earnings_blackout","score":0,
                    "direction":"NEUTRAL",
                    "reason":f"{symbol} results today — no trade"}
        elif _em < 1.0:
            locals_dict = locals()
            if 'result' in locals_dict:
                locals_dict['result']['earnings_multiplier'] = _em
    except Exception: pass

    htf_bias = _safe_get_htf_bias(df_htf)

    candidates: List[Dict[str, Any]] = []
    rejections: List[str]            = []
    _sig_meta: dict = {}    # per-signal metadata collected during scoring (for narrative + SHAP)
    if _market_quality:
        _sig_meta["market_quality"] = _market_quality
    try:
        from tick_order_flow import get_global_tick_bias

        _tick_flow = get_global_tick_bias(symbol)
        if int(_tick_flow.get("total", 0) or 0) > 0:
            _sig_meta["tick_order_flow"] = _tick_flow
    except Exception:
        pass

    # ── Compute PDH/PDL/PWH/PWL/PMH/PML S/R levels ─────────────────────
    _sr_levels = {}
    if _PS_AVAILABLE:
        try:
            _now_ts = __import__("time").time()
            _cached = _SR_LEVEL_CACHE.get(symbol, {})
            if _now_ts - _cached.get("ts", 0) > 3600:  # refresh hourly
                _pd_lvls = get_pdh_pdl_pdc(df)
                _wm_lvls = get_weekly_monthly_levels(df_htf) if df_htf is not None else {}
                _sr_levels = {**_pd_lvls, **_wm_lvls}
                _SR_LEVEL_CACHE[symbol] = {"ts": _now_ts, "levels": _sr_levels}
            else:
                _sr_levels = _cached.get("levels", {})
        except Exception: pass

    # ── Compute weekly/monthly pivot levels for MTF context ────────────
    _weekly_lvls  = {}
    _monthly_lvls = {}
    _daily_floor  = {}
    try:
        if _PB_AVAILABLE and df_htf is not None and len(df_htf) >= 20:
            _dfc = df_htf.copy()
            _dfc.columns = [c.lower() for c in _dfc.columns]
            # Weekly: last 5 HTF bars
            _wb = _dfc.tail(6).iloc[:-1]
            if len(_wb) >= 3:
                from pivot_boss import calc_weekly_pivots, calc_monthly_pivots, get_mtf_pivot_score_mod, calc_floor_pivots
                from pivot_boss import build_ochao_levels
                _weekly_lvls = calc_weekly_pivots(
                    float(_wb["high"].max()), float(_wb["low"].min()), float(_wb["close"].iloc[-1])
                )
                # Monthly: last 20 HTF bars
                _mb = _dfc.tail(22).iloc[:-2]
                if len(_mb) >= 10:
                    _monthly_lvls = calc_monthly_pivots(
                        float(_mb["high"].max()), float(_mb["low"].min()), float(_mb["close"].iloc[-1])
                    )
                # Daily floor for confluence check
                _pb = _dfc.tail(2).iloc[0]
                _daily_floor = calc_floor_pivots(
                    float(_pb.get("high",0)), float(_pb.get("low",0)), float(_pb.get("close",0))
                )
                _ochao_levels = build_ochao_levels(df, df_htf)
                if _ochao_levels:
                    _sig_meta["ochao_levels"] = _ochao_levels
                # 2026-07-10: expose W/M pivot levels for signal_log context —
                # above_weekly_pvt / pct_from_w_r1 etc. logged constant 0 for
                # every signal because these levels were computed here but
                # never left this scope (the log call had nothing to pass).
                if _weekly_lvls:
                    _sig_meta["weekly_pivot"] = float(_weekly_lvls.get("W_P", 0) or 0)
                    _sig_meta["weekly_r1"]    = float(_weekly_lvls.get("W_R1", 0) or 0)
                if _monthly_lvls:
                    _sig_meta["monthly_pivot"] = float(_monthly_lvls.get("M_P", 0) or 0)
                    _sig_meta["monthly_r1"]    = float(_monthly_lvls.get("M_R1", 0) or 0)
    except Exception as _e:
        import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)


    _cross_modifier = 0.0   # accumulated cross-sectional score modifier (e.g. momentum rank)
    _context_keys = (
        "vix", "india_vix", "vix_source",
        "iv", "implied_volatility", "atm_iv", "iv_percentile", "ivp",
        "pcr", "put_call_ratio", "pcr_oi", "pcr_change_oi", "pcr_volume",
        "option_chain_signal", "option_chain_bias", "option_summary",
        "volume_data_quality", "data_confidence",
        "global_bias", "global_change_pct", "global_source", "cross_asset_bias",
        "news_score", "expiry_dte", "expiry_regime", "whale_mod",
        "feed_degraded", "feed_degraded_reasons",
        "mtf_context", "mtf_indicator_context",
    )
    for _ctx_key in _context_keys:
        if option_data and _ctx_key in option_data:
            _sig_meta[_ctx_key] = option_data.get(_ctx_key)
        elif _ctx_key in cfg:
            _sig_meta[_ctx_key] = cfg.get(_ctx_key)

    _mtf_indicator_context = {}
    if bool(cfg.get("enable_mtf_indicator_context", True)):
        try:
            from mtf_context import build_mtf_context

            _mtf_indicator_context = (
                _sig_meta.get("mtf_indicator_context")
                or _sig_meta.get("mtf_context")
                or {}
            )
            if not _mtf_indicator_context:
                _mtf_indicator_context = build_mtf_context({
                    "primary": df,
                    "htf": df_htf,
                })
            if _mtf_indicator_context:
                _sig_meta["mtf_indicator_context"] = _mtf_indicator_context
        except Exception as _mtf_exc:
            logger.debug("MTF indicator context unavailable: %s", _mtf_exc)
            _mtf_indicator_context = {}

    _market_profile_context = {}
    if bool(cfg.get("enable_market_profile_context", True)):
        try:
            from market_profile_context import build_market_profile_context

            _market_profile_context = build_market_profile_context(
                df,
                symbol=symbol,
                side="",
                timeframe=str(cfg.get("primary_interval", "5m") or "5m"),
                persist=True,
            )
            if _market_profile_context.get("available"):
                _sig_meta["market_profile"] = _market_profile_context
        except Exception as _mp_exc:
            logger.debug("Market profile context unavailable: %s", _mp_exc)
            _market_profile_context = {}

    _representation_features = {}
    if _ALT_PRICE_REP_AVAILABLE:
        try:
            _representation_features = _build_representation_features(df)
            _sig_meta["representation_features"] = _representation_features
            _sig_meta["footprint_source"] = "OHLCV_CLOSE_LOCATION_PROXY"
        except Exception as _alt_feature_exc:
            logger.debug("alternative representation features unavailable: %s", _alt_feature_exc)

    for strategy_fn in STRATEGIES:
        # ── Regime-aware strategy routing ─────────────────────────────────
        try:
            from market_intelligence_hub import should_strategy_run_in_regime as _rsrr
            _rn = getattr(strategy_fn,'__name__','')
            _regime_now = str(regime or "UNKNOWN")
            _ok_r, _r_reason = _rsrr(_rn, _regime_now)
            if not _ok_r:
                rejections.append(f'{_rn}: regime_blocked ({_regime_now})')
                continue
        except Exception: pass

        # ── Manual per-strategy pause gate ───────────────────────────────
        if _manually_paused_strategies:
            try:
                _fn_upper = strategy_fn.__name__.upper()
                if any(p in _fn_upper for p in _manually_paused_strategies):
                    rejections.append(f"{strategy_fn.__name__}: manually_paused")
                    continue
            except Exception: pass

        try:
            # Check if strategy is auto-disabled due to poor performance
            if _SM_AVAILABLE and _strat_matrix_global:
                _strat_nm = strategy_fn.__name__.replace("run_","").replace("_strategy","")
                _should_run, _reason = _strat_matrix_global.should_run_strategy(_strat_nm)
                if not _should_run:
                    # Only hard-disable if truly bad — not just a few losses
                    # Soft-disable: add penalty instead of skipping
                    _hard_disable = "win_rate" in _reason.lower() or "banned" in _reason.lower()
                    if _hard_disable:
                        rejections.append(f"{_strat_nm}: auto_disabled_{_reason}")
                        continue
                    else:
                        # Soft disable: apply penalty but still let it vote
                        pass  # score penalty applied by strategy_matrix weight below
            if _HEROZERO_AVAIL and strategy_fn is run_hero_zero_strategy:
                # Hero-zero takes (df, df_htf, symbol, option_data, **catalysts).
                # The generic positional call would bind option_data to its
                # `symbol` param and the strategy stays inert — so feed it the
                # real inputs we already have in scope: symbol, VIX, a PDH/PDL
                # break, and an OI-direction buildup. (EMA200-cross/GIFT-gap have
                # no clean source here and are left to their safe defaults.)
                _hz_vix = float((option_data or {}).get("vix", 15) or 15)
                try:
                    _cl = df.copy(); _cl.columns = [c.lower() for c in _cl.columns]
                    _hz_close = float(_cl["close"].iloc[-1])
                except Exception:
                    _hz_close = 0.0
                _hz_pdh = float(_sr_levels.get("pdh", 0) or 0)
                _hz_pdl = float(_sr_levels.get("pdl", 0) or 0)
                _hz_break = bool((_hz_pdh and _hz_close > _hz_pdh) or
                                 (_hz_pdl and _hz_close < _hz_pdl))
                _hz_oi = False
                try:
                    from oi_tracker import get_oi_tracker as _hz_get_oi
                    _hz_d = _hz_get_oi().get_current_direction(symbol)
                    _hz_oi = bool(_hz_d and _hz_d.get("direction") not in (None, "NEUTRAL"))
                except Exception:
                    pass
                # GIFT-gap catalyst (NIFTY only — GIFT Nifty is the NIFTY proxy);
                # prev close comes from the precomputed PDC. Cached 300s upstream.
                # (ema200_cross has no clean 1-min source here — left at default.)
                _hz_gap = 0.0
                if symbol.upper() == "NIFTY":
                    try:
                        from data_source_resilience import get_gift_nifty_gap
                        _hz_pdc = float(_sr_levels.get("pdc", 0) or 0)
                        if _hz_pdc > 0:
                            _hz_gap = float(get_gift_nifty_gap(_hz_pdc).get("gap_pct", 0) or 0)
                    except Exception:
                        pass
                # VIX 30-min change (from the live feeds singleton, if running).
                _hz_vixchg = 0.0
                try:
                    from market_data_feeds import get_market_feeds
                    _hz_vixchg = float(get_market_feeds().vix.get_change() or 0)
                except Exception:
                    pass
                # CPR break: price above TC (bullish) / below BC (bearish), where
                # the central pivot range is derived from the prev-day H/L/C.
                _hz_cpr = False
                try:
                    _pdh = float(_sr_levels.get("pdh", 0) or 0)
                    _pdl = float(_sr_levels.get("pdl", 0) or 0)
                    _pdc = float(_sr_levels.get("pdc", 0) or 0)
                    if _pdh and _pdl and _pdc and _hz_close:
                        _piv = (_pdh + _pdl + _pdc) / 3.0
                        _bc  = (_pdh + _pdl) / 2.0
                        _tc  = 2.0 * _piv - _bc
                        _hz_cpr = bool(_hz_close > max(_tc, _bc) or
                                       _hz_close < min(_tc, _bc))
                except Exception:
                    pass
                result = strategy_fn(
                    df, df_htf, symbol=symbol, option_data=option_data,
                    vix=_hz_vix, vix_change=_hz_vixchg, gift_gap=_hz_gap,
                    cpr_break=_hz_cpr, pdh_pdl_break=_hz_break, oi_buildup=_hz_oi,
                )
            else:
                result    = _invoke_strategy(strategy_fn, df, df_htf, option_data, symbol)
            # Modifier-only strategies (e.g. cross_momentum) return score_modifier
            # without a direction — accumulate and skip directional processing
            if "score_modifier" in result and "direction" not in result:
                _cross_modifier += float(result.get("score_modifier", 0))
                continue
            direction = _normalize_direction(
                result.get("direction") or result.get("side") or result.get("action")
            )
            raw_score = float(result.get("score", 0.0))
            strategy  = str(result.get("strategy", strategy_fn.__name__))

            if _PRUNE_STRATS and (strategy in _PRUNE_STRATS or strategy_fn.__name__ in _PRUNE_STRATS):
                continue   # gated off via pruning (reversible)

            if not direction:
                rejections.append(f"{strategy}: no direction")
                continue

            adjusted_score = _adjust_score_for_regime(strategy, raw_score, regime) + _cross_modifier

            # Hurst Exponent soft multiplier — adjusts score by market memory type
            # Trend strategies get a boost in H>0.55 markets; MR strategies in H<0.45.
            # Cap multiplier to [0.6, 1.4] so no strategy is hard-blocked.
            try:
                from hurst_exponent import hurst_strategy_multiplier as _hm
                _closes = df["Close"].dropna().tolist()[-60:]
                _strat_type = ("TREND" if any(k in strategy.upper()
                               for k in ("TREND","BREAKOUT","MOMENTUM","MA_CROSS","ORB"))
                               else "MEAN_REVERT" if any(k in strategy.upper()
                               for k in ("MR","REVERT","SCALP","RSI2","CPR"))
                               else "OTHER")
                if _strat_type != "OTHER":
                    _h_mult = max(0.6, min(1.4, _hm(_closes, _strat_type)))
                    adjusted_score = adjusted_score * _h_mult
            except Exception: pass

            _cand_meta: dict = {
                "raw_strategy": strategy,
                "base_factor": _strategy_factor(strategy),
            }
            if _representation_features:
                _cand_meta["representation_features"] = dict(_representation_features)
                _cand_meta["footprint_source"] = "OHLCV_CLOSE_LOCATION_PROXY"
            _cand_factors = {_cand_meta["base_factor"]}
            if bool(cfg.get("enable_market_profile_context", True)):
                try:
                    from market_profile_context import build_market_profile_context

                    _profile_ctx = build_market_profile_context(
                        df,
                        symbol=symbol,
                        side=direction,
                        timeframe=str(cfg.get("primary_interval", "5m") or "5m"),
                        persist=False,
                    )
                    if _profile_ctx.get("available"):
                        _profile_mod = float(_profile_ctx.get("score_modifier", 0.0) or 0.0)
                        adjusted_score += _profile_mod
                        _cand_meta["market_profile"] = _profile_ctx
                        _cand_meta["market_profile_mod"] = round(_profile_mod, 3)
                        _cand_meta["profile_bias"] = _profile_ctx.get("profile_bias", "NEUTRAL")
                        _cand_meta["profile_position"] = _profile_ctx.get("profile_position", "UNKNOWN")
                        _cand_meta["profile_poc"] = _profile_ctx.get("poc", 0)
                        _cand_meta["profile_vah"] = _profile_ctx.get("vah", 0)
                        _cand_meta["profile_val"] = _profile_ctx.get("val", 0)
                        _cand_meta["profile_poc_distance_pct"] = _profile_ctx.get("poc_distance_pct", 0)
                        _cand_meta["profile_value_width_pct"] = _profile_ctx.get("value_width_pct", 0)
                        _cand_meta["profile_acceptance"] = _profile_ctx.get("acceptance_state", "")
                        if _profile_mod > 0.05:
                            _cand_factors.add("MARKET_PROFILE")
                        elif _profile_mod < -0.05:
                            _cand_meta["market_profile_headwind"] = round(_profile_mod, 3)
                except Exception as _mp_exc:
                    logger.debug("Market profile score unavailable: %s", _mp_exc)
            if _market_quality:
                _mq_mod = float(_market_quality.get("score_modifier", 0.0) or 0.0)
                adjusted_score += _mq_mod
                _cand_meta["market_quality"] = _market_quality
                if abs(_mq_mod) >= 0.01:
                    _cand_meta["market_quality_mod"] = round(_mq_mod, 3)
                if _mq_mod < -0.05:
                    _cand_meta["market_quality_headwind"] = round(_mq_mod, 3)
                if (
                    bool(cfg.get("market_quality_block_live", True))
                    and not paper_training_mode
                    and _market_quality.get("hard_block")
                ):
                    rejections.append(
                        f"{strategy}: market_quality_block "
                        f"{','.join(_market_quality.get('reasons', [])[:3])}"
                    )
                    continue
            if result.get("pattern"):
                _cand_meta["pattern"] = result.get("pattern")
                _cand_factors.add("PATTERN")
            if result.get("patterns"):
                _cand_meta["patterns"] = result.get("patterns")
                _cand_factors.add("PATTERN")
            if result.get("all_patterns"):
                _cand_meta["all_patterns"] = result.get("all_patterns")
                _cand_factors.add("PATTERN")
            for _pattern_meta_key in (
                "risk_reward",
                "breakout_confirmed",
                "pattern_breakout",
                "pattern_breakout_confirmed",
                "level_breakout",
                "level_breakout_confirmed",
                "breakout_retest",
                "retest_confirmed",
                "breakout_rejection",
                "level_rejection",
                "rejection_direction",
                "failed_breakout",
                "failed_breakdown",
                "volume_confirmation",
                "pattern_engine",
                "confidence",
                "iv",
                "implied_volatility",
                "atm_iv",
                "iv_percentile",
                "ivp",
                "vix",
                "india_vix",
                "pcr",
                "put_call_ratio",
                "pcr_oi",
                "pcr_change_oi",
                "pcr_volume",
                "option_chain_signal",
                "option_chain_bias",
                "volume_data_quality",
                "data_confidence",
                "global_bias",
                "global_change_pct",
                "global_source",
                "cross_asset_bias",
                "news_score",
                "expiry_dte",
                "expiry_regime",
                "whale_mod",
                "levels",
                "ochao_levels",
                "day_type",
                "cpr_width",
                "cpr_bias",
                "style",
                "instrument_type",
                "option_underlying",
                "cpr_structure",
                "pivot_scalp_context",
                "mtf_context",
                "mtf_indicator_context",
                "structure_context",
                "structure_label",
                "structure_direction",
                "structure_bos",
                "structure_choch",
                "structure_score",
            ):
                if _pattern_meta_key in result:
                    _cand_meta[_pattern_meta_key] = result.get(_pattern_meta_key)
                elif option_data and _pattern_meta_key in option_data:
                    _cand_meta[_pattern_meta_key] = option_data.get(_pattern_meta_key)
                elif _pattern_meta_key in cfg:
                    _cand_meta[_pattern_meta_key] = cfg.get(_pattern_meta_key)
            if result.get("levels_ctx"):
                _cand_meta["levels_ctx"] = result.get("levels_ctx")
                _cand_factors.add("SUPPORT_RESISTANCE")
            if "cpr" in strategy.lower() or result.get("cpr_bias"):
                if result.get("cpr_bias"):
                    _cand_meta["cpr_bias"] = result.get("cpr_bias")
                if result.get("cpr_structure"):
                    _cand_meta["cpr_structure"] = result.get("cpr_structure")
                _cand_factors.add("CPR_PIVOT")
            # Apply Pivot Boss indicator modifier (Role 2)
            _pv_mod = 0.0
            if _PB_AVAILABLE:
                try:
                    _day_type = _pb_cpr_width(
                        _pb_calc_cpr(
                            float(df["high"].max() if "high" in df.columns else df.get("High",df.iloc[:,1]).max()),
                            float(df["low"].min()  if "low"  in df.columns else df.get("Low", df.iloc[:,2]).min()),
                            float(df["close"].iloc[-1] if "close" in df.columns else df.iloc[-1,3])
                        )
                    ).get("day_type","NORMAL")
                    _pv_mod = _get_pivot_score_modifier(df, direction, strategy, _day_type)
                    adjusted_score = adjusted_score + _pv_mod
                    if abs(_pv_mod) > 0.05:
                        _cand_meta["pivot_boss_mod"] = _pv_mod
                        _cand_meta["pivot_day_type"] = _day_type
                        if _pv_mod > 0:
                            _cand_factors.add("CPR_PIVOT")
                        else:
                            _cand_meta["pivot_headwind"] = _pv_mod
                except Exception as _e:
                    import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)
            # ── GATE: Corporate action check ─────────────────────────────
            if _CA_AVAILABLE2 and symbol and symbol not in {"NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY"}:
                try:
                    if _has_ca(symbol):
                        return {"strategy": strategy, "score": 0.0, "direction": None,
                                "reason": "corporate_action_today"}
                except Exception: pass

            # ── GATE: F&O Ban list check ────────────────────────────────
            if _BAN_AVAILABLE and symbol and symbol not in {"NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY"}:
                try:
                    if _is_banned(symbol):
                        logger.debug("Signal blocked: %s in F&O ban list", symbol)
                        return {"strategy": strategy, "score": 0.0, "direction": None,
                                "reason": "fno_ban_list"}
                except Exception: pass

            # ── GATE: ASM / GSM Surveillance check ──────────────────────
            if symbol and symbol not in {"NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY"}:
                try:
                    from asm_gsm_filter import is_under_surveillance as _is_asm
                    if _is_asm(symbol):
                        logger.debug("Signal blocked: %s on ASM/GSM surveillance list", symbol)
                        return {"strategy": strategy, "score": 0.0, "direction": None,
                                "reason": "asm_gsm_surveillance"}
                except Exception: pass

            # ── NEW INTELLIGENCE MODULES ──────────────────────────────────
            # 1. BhavCopy delivery % score
            if _BHAV_AVAILABLE and symbol:
                try:
                    _bhav_v = _bhav_score(symbol, direction)
                    adjusted_score += _bhav_v
                    _cand_meta["bhav_delivery"] = round(float(_bhav_v or 0), 4)
                except Exception: pass

            # 2. Cross-asset momentum score
            if _CA_AVAILABLE and symbol:
                try:
                    _ca_v = _ca_score(symbol, direction)
                    adjusted_score += _ca_v
                    _cand_meta["cross_asset"] = round(float(_ca_v or 0), 4)
                except Exception: pass

            # 3. Intraday time-bucket weight
            if _TB_AVAILABLE:
                try:
                    from datetime import datetime as _dtnow
                    _tb = _tb_weight(strategy, _dtnow.now().time())
                    adjusted_score *= _tb
                    _cand_meta["time_bucket"] = round(float(_tb or 1), 4)
                except Exception: pass

            # 4. Participant-wise OI (FII/DII/PRO/CLIENT) + cumulative FII
            if _PART_AVAILABLE:
                try:
                    _pm, _pn = _part_score(direction)
                    _pm_applied = _evidence_safe_context_modifier(_pm, cfg)
                    adjusted_score += _pm_applied
                    _cand_meta["participant_oi"] = round(float(_pm or 0), 4)
                    _cand_meta["participant_oi_applied"] = round(float(_pm_applied), 4)
                    from participant_oi import get_cumulative_fii
                    _cum5 = get_cumulative_fii(5)
                    # Oversold bounce: 5d outflow > ₹10,000Cr
                    if _cum5 < -10000 and direction == "BUY":
                        adjusted_score += 0.8
                    elif _cum5 > 10000 and direction == "SELL":
                        adjusted_score += 0.5
                except Exception: pass

            # 5. Expiry regime modifier — ADDITIVE nudge (never kills signal)
            if _EXPIRY_AVAILABLE:
                try:
                    _er = _get_expiry()
                    _mods = _er.get("score_modifiers", {})
                    _stl  = strategy.lower()
                    # Get multiplier for this strategy type
                    if any(x in _stl for x in ["break","orb","vp_break"]):
                        _exp_mult = float(_mods.get("breakout", 1.0))
                    elif any(x in _stl for x in ["trend","ma_cross","holy","supertrend"]):
                        _exp_mult = float(_mods.get("trend", 1.0))
                    elif any(x in _stl for x in ["mean","vwap","cpr","rsi","ichin"]):
                        _exp_mult = float(_mods.get("mean_rev", 1.0))
                    elif any(x in _stl for x in ["scalp"]):
                        _exp_mult = float(_mods.get("scalping", 1.0))
                    else:
                        _exp_mult = 1.0
                    # Convert multiplier to additive nudge: 1.4→+0.4, 0.6→-0.4
                    # Cap nudge at ±0.8 — never kill a signal
                    _exp_nudge = round(max(-0.8, min(0.8, _exp_mult - 1.0)), 2)
                    adjusted_score = adjusted_score + _exp_nudge
                    _cand_meta["expiry_regime"] = float(_exp_nudge or 0)
                    # SIP calendar boost (BUY only, 3rd-4th Monday)
                    if direction == "BUY":
                        _sb = _sip_boost()
                        adjusted_score += _sb
                        _cand_meta["sip_boost"] = round(float(_sb or 0), 4)
                    # Friday post-expiry: fresh week ahead, boost trend signals
                    import datetime as _dttm
                    if _dttm.datetime.now().weekday() == 4:  # Friday
                        if any(x in strategy.lower() for x in ["trend","breakout","orb"]):
                            adjusted_score += 0.4
                except Exception: pass

            # 6. Bulk deal smart money signal
            if _BULK_AVAILABLE and symbol:
                try:
                    _bulk_v = _bulk_score(symbol, direction)
                    adjusted_score += _bulk_v
                    _cand_meta["bulk_deal"] = round(float(_bulk_v or 0), 4)
                except Exception: pass

            # 7. Theta environment (IV% modifier)
            if _THETA_AVAILABLE:
                try:
                    _ivp = float((option_data or {}).get("iv_percentile", 50))
                    _vix = float((option_data or {}).get("vix", 15))
                    _theta_v = _theta_mod(_ivp, _vix, direction)
                    adjusted_score += _theta_v
                    _cand_meta["theta"] = round(float(_theta_v or 0), 4)
                except Exception: pass

            # 8. Index rebalancing signal
            if _REBAL_AVAILABLE and symbol:
                try:
                    _rebal_v = _rebal_score(symbol, direction)
                    adjusted_score += _rebal_v
                    _cand_meta["rebalancing"] = round(float(_rebal_v or 0), 4)
                except Exception: pass

            # 9. News NLP sentiment
            if symbol:
                try:
                    # Prefer the working OmniSource per-symbol score (30 sources, no
                    # API key, cached). news_nlp needs NEWS_API_KEY (unset) → always 0.
                    _news_v = 0.0
                    try:
                        from omnisource_news_engine import get_symbol_specific_news as _osn
                        # 2026-07-10: the producer returns "score_adjustment";
                        # this read "score_adj" (key drift) so news was 0 on
                        # every signal since the 06-14 omnisource rewire.
                        _osn_r = _osn(symbol) or {}
                        _adj = float(_osn_r.get("score_adjustment",
                                                _osn_r.get("score_adj", 0)) or 0)
                        # bullish(+) helps BUY, hurts SELL; clamp to a nudge
                        _news_v = round(max(-1.0, min(1.0, _adj * 0.4)) * (1 if direction == "BUY" else -1), 3)
                    except Exception:
                        if _NEWS_AVAILABLE:
                            _news_v = _news_score(symbol, direction)  # fallback (0 without NEWS_API_KEY)
                    adjusted_score += _news_v
                    _cand_meta["news"] = round(float(_news_v or 0), 4)
                except Exception: pass

            # 8a-crsi. Connors RSI modifier for mean-reversion and scalping strategies
            try:
                _df_lc2 = df.copy(); _df_lc2.columns = [c.lower() for c in _df_lc2.columns]
                _crsi = float(_df_lc2.get("connors_rsi", pd.Series([50.0])).iloc[-1])
                if not pd.isna(_crsi):
                    _stl = strategy.lower()
                    if any(x in _stl for x in ["mean_rev","vwap_rev","rsi_div","cpr","scalp"]):
                        _crsi_mod = 0.0
                        if direction == "BUY"  and _crsi < 10:  _crsi_mod = 0.8
                        elif direction == "BUY"  and _crsi > 90:  _crsi_mod = -0.8
                        elif direction == "SELL" and _crsi > 90:  _crsi_mod = 0.8
                        elif direction == "SELL" and _crsi < 10:  _crsi_mod = -0.8
                        adjusted_score += _crsi_mod
                        _sig_meta["crsi_mod"] = float(_crsi_mod)
                    _sig_meta["connors_rsi"] = round(_crsi, 1)
            except Exception: pass

            # 8a0. Choppiness Index / Efficiency Ratio gate
            try:
                _df_lc = df.copy(); _df_lc.columns = [c.lower() for c in _df_lc.columns]
                _er   = float(_df_lc.get("efficiency_ratio", pd.Series([0.5])).iloc[-1])
                _chop = float(_df_lc.get("choppiness_index", pd.Series([50.0])).iloc[-1])
                _stl  = strategy.lower()
                _is_trend_strat = any(x in _stl for x in ["trend","break","orb","momentum","supertrend","ma_cross","donchian"])
                _is_mr_strat    = any(x in _stl for x in ["mean_reversion","vwap_rev","rsi_div","cpr","scalp"])
                if _is_trend_strat and not pd.isna(_er):
                    if _er < 0.20:   adjusted_score -= 1.5   # very choppy — penalise trend signals
                    elif _er < 0.35: adjusted_score -= 0.7
                if _is_mr_strat and not pd.isna(_chop):
                    if _chop < 38.2: adjusted_score -= 1.0   # strongly trending — penalise mean-rev
                    elif _chop > 55: adjusted_score += 0.5   # confirmed ranging — boost mean-rev
            except Exception: pass

            # 8a. Regime-based strategy weight modifier
            if _REGIME_AVAIL:
                try:
                    _rw = _get_regime().strategy_weight(strategy)
                    if _rw != 1.0:
                        adjusted_score = adjusted_score * _rw
                except Exception: pass
            # 8b0. NR4/NR7 compression boost for breakout/trend entries
            try:
                _df_c = df.copy(); _df_c.columns = [c.lower() for c in _df_c.columns]
                _nr7 = int(_df_c.get("nr7", pd.Series([0])).iloc[-1])
                _nr4 = int(_df_c.get("nr4", pd.Series([0])).iloc[-1])
                _stl = strategy.lower()
                if any(x in _stl for x in ["break","orb","volatility","momentum","trend"]):
                    _nr_mod = 1.2 if _nr7 else (0.7 if _nr4 else 0.0)   # compression boost
                    adjusted_score += _nr_mod
                    _sig_meta["nr_mod"] = float(_nr_mod)
                _sig_meta["nr7"] = _nr7; _sig_meta["nr4"] = _nr4
            except Exception: pass

            # 8b. Volume confirmation on breakout/trend signals
            try:
                _df_c = df.copy()
                _df_c.columns = [c.lower() for c in _df_c.columns]
                if "volume" in _df_c.columns and len(_df_c) >= 20:
                    _vol_now = float(_df_c["volume"].iloc[-1])
                    _vol_avg = float(_df_c["volume"].tail(20).mean())
                    _vol_ratio = _vol_now / max(_vol_avg, 1)
                    _stl = strategy.lower()
                    # Volume spike: breakout/trend needs above-avg volume
                    if any(x in _stl for x in ["break","trend","orb","momentum"]):
                        if _vol_ratio > 1.5:
                            adjusted_score += 1.0  # strong volume
                            _cand_factors.add("VOLUME")
                            _sig_meta["volume_mod"] = 1.0
                        elif _vol_ratio > 1.2:
                            adjusted_score += 0.5  # moderate volume
                            _cand_factors.add("VOLUME")
                            _sig_meta["volume_mod"] = 0.5
                        elif _vol_ratio < 0.7:
                            adjusted_score -= 1.0  # weak volume: reject
                            _cand_meta["volume_headwind"] = round(_vol_ratio, 2)
                            _sig_meta["volume_mod"] = -1.0
                    # Volume contraction good for mean reversion
                    elif any(x in _stl for x in ["mean","vwap","cpr","reversion"]):
                        if _vol_ratio < 0.8:
                            adjusted_score += 0.5   # low volume pullback
                            _cand_factors.add("VOLUME")
                            _sig_meta["volume_mod"] = 0.5
                    # Store for signal metadata
                    _sig_meta["volume_ratio"] = round(_vol_ratio, 2)
                    _cand_meta["volume_ratio"] = round(_vol_ratio, 2)
            except Exception: pass
            # 9a. PDH/PDL/PWH/PWL/PMH/PML score modifier
            if _PS_AVAILABLE and _sr_levels:
                try:
                    _price = float(df["close"].iloc[-1] if "close" in df.columns else df.iloc[-1,-1])
                    _sr_mod, _sr_ctx = score_vs_key_levels(
                        _price, direction,
                        pdh=_sr_levels.get("PDH",0), pdl=_sr_levels.get("PDL",0),
                        pdc=_sr_levels.get("PDC",0),
                        pwh=_sr_levels.get("PWH",0), pwl=_sr_levels.get("PWL",0),
                        pmh=_sr_levels.get("PMH",0), pml=_sr_levels.get("PML",0),
                    )
                    _sr_applied = _evidence_safe_context_modifier(_sr_mod, cfg)
                    adjusted_score += _sr_applied
                    if _sr_mod != 0:
                        _sig_meta["sr_level_mod"] = _sr_mod
                        _sig_meta["sr_level_ctx"] = " | ".join(_sr_ctx[:2])
                        _cand_meta["sr_level_mod"] = _sr_mod
                        _cand_meta["sr_level_mod_applied"] = _sr_applied
                        _cand_meta["sr_level_ctx"] = " | ".join(_sr_ctx[:2])
                        if _sr_mod > 0:
                            _cand_factors.add("SUPPORT_RESISTANCE")
                        else:
                            _cand_meta["sr_headwind"] = _sr_mod
                except Exception: pass
            # 10aa. Momentum quality (R-squared) — how clean is the trend?
            try:
                import numpy as _np
                _df_r = df.copy()
                _df_r.columns = [c.lower() for c in _df_r.columns]
                if "close" in _df_r.columns and len(_df_r) >= 15:
                    _c = _df_r["close"].tail(15).values
                    _x = _np.arange(len(_c))
                    _p  = _np.polyfit(_x, _c, 1)
                    _yhat = _np.polyval(_p, _x)
                    _ss_res = _np.sum((_c - _yhat)**2)
                    _ss_tot = _np.sum((_c - _np.mean(_c))**2)
                    _r2 = 1 - _ss_res / max(_ss_tot, 1e-9)
                    # Clean trend (R²>0.8): boost trend/breakout signals
                    if _r2 > 0.85 and any(x in strategy.lower() for x in ["trend","break","orb"]):
                        if (direction=="BUY" and _p[0] > 0) or (direction=="SELL" and _p[0] < 0):
                            adjusted_score += 0.8
                            _sig_meta["r_squared"] = round(float(_r2), 3)
                    # Noisy (R²<0.4): penalise trend signals
                    elif _r2 < 0.4 and "trend" in strategy.lower():
                        adjusted_score -= 0.5
            except Exception: pass
            # 9c. OI direction modifier (5-min real-time)
            if _OI_TRACKER_SIG_AVAIL:
                try:
                    _sym_for_oi = "BANKNIFTY" if "BANK" in symbol.upper() else "NIFTY"
                    _oi_dir = _get_oi_tracker().get_current_direction(_sym_for_oi)
                    if _oi_dir:
                        _oi_d  = _oi_dir.get("direction","NEUTRAL")
                        _oi_cv = _oi_dir.get("conviction","")
                        _oi_w  = {"STRONG":1.5,"MODERATE":0.8,"WEAK":0.3}.get(_oi_cv,0)
                        _cand_meta["oi_direction"] = _oi_d
                        _cand_meta["oi_conviction"] = _oi_cv
                        if _oi_d == direction and _oi_d != "NEUTRAL":
                            adjusted_score += _oi_w   # OI confirms signal
                            _cand_meta["oi_mod"] = _oi_w
                            if _oi_w > 0:
                                _cand_factors.add("OI")
                        elif _oi_d != "NEUTRAL" and _oi_d != direction:
                            adjusted_score -= _oi_w   # OI contradicts signal
                            _cand_meta["oi_mod"] = -_oi_w
                            if _oi_w > 0:
                                _cand_meta["oi_headwind"] = -_oi_w
                except Exception: pass
            # 9d. Global macro modifier (S&P, crude, USD/INR)
            if _VP_ADV_AVAIL:
                try:
                    _macro = _get_macro(symbol)
                    _mm    = _macro.get("score_mod", 0.0)
                    _mbias = _macro.get("bias","NEUTRAL")
                    if _mm != 0:
                        # Only apply if macro confirms trade direction
                        if (direction == "BUY"  and _mbias == "BULLISH") or \
                           (direction == "SELL" and _mbias == "BEARISH"):
                            adjusted_score += abs(_mm) * 0.5
                        elif (direction == "BUY"  and _mbias == "BEARISH") or \
                             (direction == "SELL" and _mbias == "BULLISH"):
                            adjusted_score -= abs(_mm) * 0.3
                except Exception: pass
            # 9b. HMM regime modifier
            if _HMM_AVAIL:
                try:
                    _hrm = get_regime_score_modifier(regime, str(strategy))
                    adjusted_score += _hrm
                    if abs(float(_hrm or 0.0)) > 0.05:
                        _cand_meta["regime_mod"] = round(float(_hrm), 3)
                        if _hrm > 0:
                            _cand_factors.add("REGIME")
                except Exception: pass
            # 9b2. Order flow modifier + VPIN toxicity filter
            if _OF_AVAIL:
                try:
                    _of = _compute_of(df)
                    adjusted_score += _of.get("score_modifier",0)
                except Exception: pass
            try:
                from order_flow import calculate_vpin as _calc_vpin
                _vpin = _calc_vpin(df)
                _stl  = strategy.lower()
                # High VPIN → informed flow active → only trade WITH the detected direction
                if _vpin > 0.7:
                    _of_result = _compute_of(df) if _OF_AVAIL else {}
                    _of_dir = "BUY" if _of_result.get("score_modifier", 0) > 0 else "SELL"
                    if direction != _of_dir:
                        adjusted_score -= 1.2   # penalise trading against informed flow
                    else:
                        adjusted_score += 0.6   # reward trading with informed flow
                _sig_meta["vpin"] = _vpin
            except Exception: pass
            # 9b3. Dark pool modifier
            if _DP_AVAIL:
                try:
                    _dp = _get_dp(symbol)
                    if _dp.get("direction")==direction:
                        adjusted_score += min(_dp.get("score",0)*0.3, 0.8)
                except Exception: pass
            # 9b4. FII options positioning
            if _FII_OPT_AVAIL:
                try:
                    _fo = _get_fii_opts(symbol)
                    adjusted_score += _fo.get("score_modifier",0)*0.4
                except Exception: pass
            # 9c. GEX + Implied Move modifiers
            if _GREEKS_AVAIL:
                try:
                    _ep = get_event_playbook(symbol)
                    if _ep.get("has_event") and instrument_type in ("CE","PE"):
                        adjusted_score += 1.0  # event day = options opportunity
                except Exception: pass
            # 9c-gex. GEX score modifier (regime + delta wall + flip point)
            try:
                from gex_engine import get_gex_score_modifier as _gex_mod
                _price_now = float(df["close"].iloc[-1] if "close" in df.columns else df.iloc[-1, 3])
                _gex_adj   = _gex_mod(symbol, _price_now, direction, strategy)
                if abs(_gex_adj) > 0.05:
                    adjusted_score += _gex_adj
                    _sig_meta["gex_modifier"] = round(_gex_adj, 3)
                    _cand_meta["gex_mod"] = round(_gex_adj, 3)
            except Exception: pass
            # 9d. Signal refinements
            if _REFINE_AVAIL:
                try:
                    # Score decay by time of day
                    adjusted_score, _decay_note = apply_score_decay(
                        adjusted_score, datetime.now(), strategy=str(strategy))
                    # Nifty weight amplification
                    _wt = get_nifty_weight_multiplier(symbol)
                    if _wt > 1.0: adjusted_score *= _wt
                    # Auto S/R level check
                    _sr = detect_sr_levels(df)
                    adjusted_score += _sr.get("score_mod", 0)
                    # Earnings risk block (options only)
                    if instrument_type in ("CE","PE"):
                        _er = check_earnings_risk(symbol, "options")
                        if _er.get("blocked"): adjusted_score *= 0.3
                    # Sector rotation modifier
                    _sect = get_sector_rotation_score(symbol)
                    _sm = _sect.get("score_mod", 0)
                    if (direction=="BUY" and _sm>0) or (direction=="SELL" and _sm<0):
                        adjusted_score += abs(_sm) * 0.5
                        _sig_meta["sector_mod"] = float(abs(_sm) * 0.5)
                    elif abs(_sm) > 0.5:
                        adjusted_score -= abs(_sm) * 0.3
                        _sig_meta["sector_mod"] = float(-abs(_sm) * 0.3)
                except Exception: pass
            # 9d-extra-gap. Gap strategy bias modifier (fill vs continuation bucketing)
            try:
                from day_classifier import get_day_classifier as _get_dc
                _dc  = _get_dc()
                _gp  = _dc.profile if _dc and _dc.profile else None
                if _gp:
                    _gbias = getattr(_gp, "gap_strategy_bias", "")
                    _stl   = strategy.lower()
                    _is_mr = any(x in _stl for x in ["vwap_rev","mean_rev","cpr","rsi_div"])
                    _is_mo = any(x in _stl for x in ["morning_momentum","gap_fill","gap_and_go"])
                    if _gbias == "EXPECT_FILL":
                        if _is_mr:   adjusted_score += 0.8   # confirmed: fade the gap
                        if _is_mo:   adjusted_score -= 0.5   # warn momentum: gap likely fills
                    elif _gbias == "EXPECT_CONTINUE":
                        if _is_mo:   adjusted_score += 0.8   # confirmed: ride the gap
                        if _is_mr:   adjusted_score -= 0.5   # warn MR: gap likely continues
                    _sig_meta["gap_strategy_bias"] = _gbias
            except Exception: pass

            # 9d-extra0. BSE announcement score (GAP 7)
            try:
                from data_source_resilience import get_bse_announcement_score
                _bse_score = get_bse_announcement_score(symbol)
                if abs(_bse_score) > 0.1:
                    adjusted_score += _bse_score
                    _sig_meta["bse_announcement"] = _bse_score
            except Exception: pass

            # 9d-extra1. FII futures positioning signal (GAP 8)
            try:
                if symbol.upper() in {"NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY"}:
                    from data_source_resilience import get_fii_futures_signal
                    _fii_f = get_fii_futures_signal()
                    if abs(_fii_f) > 0.1:
                        adjusted_score += _fii_f
                        _sig_meta["fii_futures_signal"] = _fii_f
            except Exception: pass

            # 9d-skew-vel. IV Skew Velocity modifier (Natenberg / Sinclair)
            try:
                from institutional_alpha import skew_velocity_signal as _sv_signal
                _sv_mod = _sv_signal(direction)
                if abs(_sv_mod) > 0.05:
                    adjusted_score += _sv_mod
                    _sig_meta["skew_velocity_mod"] = round(_sv_mod, 2)
                    _cand_meta["skew_mod"] = round(_sv_mod, 2)
            except Exception: pass

            # 9d-extra. Promoter buying signal (strongest fundamental)
            try:
                from market_intelligence_hub import get_promoter_signal
                _prom = get_promoter_signal(symbol)
                if abs(_prom) > 0.2:
                    if (_prom > 0 and direction == "BUY") or (_prom < 0 and direction == "SELL"):
                        adjusted_score += abs(_prom) * 0.8
                        _sig_meta["promoter_signal"] = _prom
                    elif (_prom > 0 and direction == "SELL") or (_prom < 0 and direction == "BUY"):
                        adjusted_score -= abs(_prom) * 0.5  # counter-signal
            except Exception: pass

            # 9d-extra2. Rollover/cost-of-carry signal
            try:
                if symbol.upper() in {"NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY"}:
                    from market_intelligence_hub import get_rollover_signal
                    _rl = get_rollover_signal(symbol)
                    _rl_score = float(_rl.get("score", 0))
                    if abs(_rl_score) > 0.1:
                        if (_rl_score > 0 and direction == "BUY") or (_rl_score < 0 and direction == "SELL"):
                            adjusted_score += abs(_rl_score) * 0.5
                        _sig_meta["rollover_signal"] = _rl.get("narrative","")
            except Exception: pass

            # 9e. FII/DII signal boost
            try:
                from fii_tracker import analyse_fii_patterns as _afii
                _fp = _afii()
                _fscore = _fp.get("score", 0)
                # +0.5 for large-cap if FII buying >₹2000Cr 3-day
                if _fp.get("fii_5d", 0) > 2000 and direction == "BUY":
                    adjusted_score += 0.5
                elif _fp.get("fii_5d", 0) < -2000 and direction == "SELL":
                    adjusted_score += 0.3
            except Exception: pass
            # 9f. President/geopolitical sentiment modifier
            if _VP_ADV_AVAIL:
                try:
                    _presn = _get_president()
                    _tm    = _presn.get("score_mod", 0.0)
                    _tbias = _presn.get("bias", "NEUTRAL")
                    if abs(_tm) > 0.3:
                        if (direction == "BUY"  and _tbias == "BULLISH") or \
                           (direction == "SELL" and _tbias == "BEARISH"):
                            adjusted_score += abs(_tm) * 0.5
                        elif _tbias != "NEUTRAL":
                            adjusted_score -= abs(_tm) * 0.3
                except Exception: pass
            # 10a. Whale/institutional composite score
            if _WHALE_AVAILABLE:
                try:
                    _whale = _get_whale_score(symbol, for_option=True)
                    _whale_mod = float(_whale.get("score_mod", 0))
                    if abs(_whale_mod) > 0.1:
                        adjusted_score += _whale_mod
                        # Store for signal metadata
                        _sig_meta["whale_mod"] = _whale_mod
                        _cand_meta["whale_mod"] = _whale_mod
                        _sig_meta["whale_signal"] = _whale.get("narrative","")[:40]
                except Exception: pass
            # 10b. SENSEX vs NIFTY divergence (index signals only)
            if _BSE_DIV and symbol in {"NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY"}:
                try:
                    _div = _bse_div()
                    if _div.get("signal") == "IT_SPECIFIC" and "FIN" not in symbol:
                        adjusted_score += 0.5 if direction == "BUY" and _div["sensex_pct"] > 0 else -0.5
                    elif _div.get("signal") == "BANK_SPECIFIC":
                        if symbol == "BANKNIFTY":
                            adjusted_score += 0.5 if direction == "BUY" and _div["banknifty_pct"] > 0 else -0.5
                except Exception: pass

            # 10. Multi-timeframe pivot levels (weekly + monthly)
            if _PB_AVAILABLE and (_weekly_lvls or _monthly_lvls):
                try:
                    _mtf_mod, _mtf_ctx = get_mtf_pivot_score_mod(
                        price          = float(df["close"].iloc[-1] if "close" in df.columns else df.iloc[-1,-1]),
                        direction      = direction,
                        daily_levels   = _daily_floor,
                        weekly_levels  = _weekly_lvls or None,
                        monthly_levels = _monthly_lvls or None,
                    )
                    _mtf_applied = _evidence_safe_context_modifier(_mtf_mod, cfg)
                    adjusted_score += _mtf_applied
                    _cand_meta["mtf_pivot"] = round(float(_mtf_mod or 0), 3)
                    _cand_meta["mtf_pivot_applied"] = round(float(_mtf_applied or 0), 3)
                    if abs(float(_mtf_mod or 0.0)) > 0.05:
                        _cand_meta["mtf_pivot_mod"] = round(float(_mtf_mod), 3)
                        _cand_meta["mtf_pivot_ctx"] = _mtf_ctx
                        if _mtf_mod > 0:
                            _cand_factors.add("MTF_PIVOT")
                        else:
                            _cand_meta["mtf_pivot_headwind"] = round(float(_mtf_mod), 3)
                except Exception: pass

            # 10b. Multi-timeframe indicator context. This is applied to every
            # strategy so pattern, breakout, rejection, and level trades all
            # respect the same MTF EMA/VWAP/Supertrend backdrop.
            if bool(cfg.get("enable_mtf_indicator_context", True)) and _mtf_indicator_context:
                try:
                    from mtf_context import score_mtf_alignment

                    _mtf_adj = score_mtf_alignment(_mtf_indicator_context, direction, strategy)
                    _mtf_mod = float(_mtf_adj.get("score_modifier", 0.0) or 0.0)
                    if _mtf_mod >= 0:
                        _mtf_mod = min(_mtf_mod, float(cfg.get("mtf_indicator_max_boost", 1.25)))
                    else:
                        _mtf_mod = max(_mtf_mod, -float(cfg.get("mtf_indicator_max_penalty", 1.25)))
                    adjusted_score += _mtf_mod
                    _cand_meta["mtf_indicator_mod"] = round(_mtf_mod, 3)
                    _cand_meta["mtf_indicator_bias"] = _mtf_adj.get("bias", "NEUTRAL")
                    _cand_meta["mtf_indicator_aligned"] = _mtf_adj.get("aligned", 0)
                    _cand_meta["mtf_indicator_conflicts"] = _mtf_adj.get("conflicts", 0)
                    _cand_meta["mtf_indicator_frame_biases"] = _mtf_adj.get("frame_biases", {})
                    _cand_meta["mtf_indicator_context"] = _mtf_indicator_context
                    if _mtf_mod > 0.05:
                        _cand_factors.add("MTF_INDICATORS")
                    elif _mtf_mod < -0.05:
                        _cand_meta["mtf_indicator_headwind"] = round(_mtf_mod, 3)
                    if (
                        bool(cfg.get("mtf_indicator_block_live", False))
                        and not paper_training_mode
                        and _mtf_mod < float(cfg.get("mtf_indicator_min_live", -0.35))
                    ):
                        rejections.append(
                            f"{strategy}: mtf_indicator_headwind mod={_mtf_mod:.2f}"
                        )
                        continue
                except Exception as _mtf_exc:
                    logger.debug("MTF indicator score unavailable: %s", _mtf_exc)

            # 11. Entry-quality indicator layer: VWAP + EMA + Supertrend +
            # ADX/DI + MACD + volume + candle/ATR extension.
            _entry_quality = _entry_quality_adjustment(
                df=df,
                direction=direction,
                strategy=strategy,
                cfg=cfg,
                paper_training_mode=paper_training_mode,
            )
            _eq_mod = float(_entry_quality.get("score_modifier", 0.0) or 0.0)
            adjusted_score += _eq_mod
            if _entry_quality.get("reasons"):
                _sig_meta["entry_quality_reasons"] = _entry_quality.get("reasons", [])
                _sig_meta["entry_quality_metrics"] = _entry_quality.get("metrics", {})
                _cand_meta["entry_quality_reasons"] = _entry_quality.get("reasons", [])
                _cand_meta["entry_quality_metrics"] = _entry_quality.get("metrics", {})
            if _eq_mod > 0.05:
                _cand_meta["entry_quality_mod"] = round(_eq_mod, 3)
                _cand_factors.add("INDICATOR_QUALITY")
            elif _eq_mod < -0.05:
                _cand_meta["entry_quality_headwind"] = round(_eq_mod, 3)
            if (
                bool(cfg.get("entry_quality_block_live", True))
                and not paper_training_mode
                and _entry_quality.get("hard_block")
            ):
                rejections.append(
                    f"{strategy}: entry_quality_block "
                    f"{','.join(_entry_quality.get('reasons', [])[:3])}"
                )
                continue

            # 12. Market-structure confirmation: HH/HL, LH/LL, BOS and CHoCH.
            # This is bounded and additive so structure improves signal quality
            # without becoming an unfiltered standalone strategy.
            _structure_ctx = _structure_confirmation_adjustment(
                df=df,
                df_htf=df_htf,
                direction=direction,
                cfg=cfg,
            )
            _structure_mod = float(_structure_ctx.get("score_modifier", 0.0) or 0.0)
            adjusted_score += _structure_mod
            _cand_meta["structure_context"] = _structure_ctx
            _cand_meta["structure_mod"] = round(_structure_mod, 3)
            _cand_meta["structure_label"] = _structure_ctx.get("structure_label", "")
            _cand_meta["structure_direction"] = _structure_ctx.get("structure_direction", "NEUTRAL")
            _cand_meta["structure_bos"] = _structure_ctx.get("last_bos")
            _cand_meta["structure_choch"] = _structure_ctx.get("choch_direction")
            _cand_meta["structure_score"] = _structure_ctx.get("structure_score", 0.0)
            _sig_meta["structure_context"] = _structure_ctx
            if _structure_mod > 0.05:
                _cand_factors.add("MARKET_STRUCTURE")
            elif _structure_mod < -0.05:
                _cand_meta["structure_headwind"] = round(_structure_mod, 3)
            if (
                bool(cfg.get("structure_confirm_block_live", False))
                and not paper_training_mode
                and _structure_ctx.get("hard_block")
            ):
                rejections.append(
                    f"{strategy}: structure_block "
                    f"{','.join(_structure_ctx.get('reasons', [])[:3])}"
                )
                continue

            # 13. Grouped all-indicator confluence.
            # Correlated indicators are blended by family first so one family
            # cannot dominate the trade just by having more columns available.
            if bool(cfg.get("enable_indicator_confluence", True)):
                try:
                    from indicator_confluence import calculate_indicator_confluence
                    _indicator_confluence = calculate_indicator_confluence(
                        df,
                        direction=direction,
                        strategy=strategy,
                        signal_meta={**_sig_meta, **_cand_meta},
                        option_data=option_data,
                        weights=cfg.get("indicator_confluence_weights"),
                    )
                    _ic_mod = float(_indicator_confluence.get("score_modifier", 0.0) or 0.0)
                    if _ic_mod >= 0:
                        _ic_mod = min(
                            _ic_mod,
                            float(cfg.get("indicator_confluence_max_boost", 1.25)),
                        )
                    else:
                        _ic_mod = max(
                            _ic_mod,
                            -float(cfg.get("indicator_confluence_max_penalty", 1.25)),
                        )
                    adjusted_score += _ic_mod
                    _cand_meta["indicator_confluence_score"] = _indicator_confluence.get("score")
                    _cand_meta["indicator_confluence_mod"] = round(_ic_mod, 3)
                    _cand_meta["indicator_confluence"] = _indicator_confluence
                    if _ic_mod > 0.05:
                        _cand_factors.add("INDICATOR_CONFLUENCE")
                    elif _ic_mod < -0.05:
                        _cand_meta["indicator_confluence_headwind"] = round(_ic_mod, 3)

                    _ic_score = float(_indicator_confluence.get("score", 5.0) or 5.0)
                    if (
                        bool(cfg.get("indicator_confluence_block_live", False))
                        and not paper_training_mode
                        and _ic_score < float(cfg.get("indicator_confluence_min_live", 4.8))
                    ):
                        rejections.append(
                            f"{strategy}: indicator_confluence_below_min "
                            f"score={_ic_score:.2f}"
                        )
                        continue
                except Exception as _ic_exc:
                    logger.debug("indicator confluence unavailable: %s", _ic_exc)

            # 14. Universal candidate-quality gate: enough independent evidence
            # must support the candidate before confluence boosting can crown it.
            _candidate_quality = _candidate_quality_adjustment(_cand_meta, _cand_factors, cfg)
            _cq_mod = float(_candidate_quality.get("score_modifier", 0.0) or 0.0)
            adjusted_score += _cq_mod
            _cand_meta["candidate_quality"] = _candidate_quality
            _cand_meta["candidate_quality_confirmations"] = _candidate_quality.get("confirmations", [])
            if abs(_cq_mod) >= 0.01:
                _cand_meta["candidate_quality_mod"] = round(_cq_mod, 3)
            if _cq_mod < -0.05:
                _cand_meta["candidate_quality_headwind"] = round(_cq_mod, 3)
            if (
                bool(cfg.get("candidate_quality_block_live", False))
                and not paper_training_mode
                and _candidate_quality.get("hard_block")
            ):
                rejections.append(
                    f"{strategy}: candidate_quality_block "
                    f"{','.join(_candidate_quality.get('reasons', [])[:3])}"
                )
                continue

            # Apply time-of-day zone weight — ADDITIVE only (never kills signal)
            # Converts multiplier to ±1.0 nudge: 2.0→+1.0, 0.3→-0.7, 1.0→0.0
            tz_weight = get_strategy_weight(strategy)
            tz_nudge  = round(max(-1.0, min(1.0, (tz_weight - 1.0))), 2)
            adjusted_score = adjusted_score + tz_nudge  # nudge only, never zero out

            # EOD learner weight: previous P&L + triple-barrier outcomes provide
            # a bounded strategy/indicator nudge for the next trading day.
            try:
                from eod_weight_engine import apply_learned_weight_nudge as _eod_nudge
                adjusted_score, _eod_weight_ctx = _eod_nudge(
                    adjusted_score,
                    strategy,
                    {**_sig_meta, **_cand_meta},
                )
                if abs(float(_eod_weight_ctx.get("nudge", 0) or 0)) >= 0.01:
                    _sig_meta["eod_learning_weight"] = _eod_weight_ctx
                    _cand_meta["eod_learning_weight"] = _eod_weight_ctx
            except Exception:
                pass

            # ── Condition multiplier from strategy_performance_matrix ────────
            # Uses historical win-rate per (day_type, time_bucket, VIX, regime)
            # to scale the score up/down (0.3x terrible → 1.5x excellent).
            # Requires ≥30 labelled outcomes before moving weights (min-sample guard).
            # This wires the triple-barrier learning loop into live signal scoring.
            try:
                if _SM_AVAILABLE and _strat_matrix_global:
                    _strat_nm_m = strategy_fn.__name__.replace("run_", "").replace("_strategy", "")
                    _time_bkt   = _strat_matrix_global.get_time_bucket()
                    # `_is_expiry` was never defined here → NameError silently
                    # swallowed by the except below, so the condition multiplier
                    # never applied. Use the guarded expiry-day flag like the
                    # rest of generate_signal (see 3494/3634).
                    _is_exp_day = bool(_EXPIRY_AVAILABLE and (_get_expiry() or {}).get("is_expiry_day", False))
                    _day_t      = "EXPIRY" if _is_exp_day else ("MONDAY" if __import__("datetime").datetime.now().weekday() == 0 else "NORMAL")
                    _vix_now    = float((option_data or {}).get("vix", 15) or 15)
                    _cond_mult  = _strat_matrix_global.get_condition_multiplier(
                        _strat_nm_m, day_type=_day_t,
                        time_bucket=_time_bkt, vix=_vix_now, regime=str(regime)
                    )
                    if _cond_mult != 1.0:
                        adjusted_score = adjusted_score * _cond_mult
                        _cand_meta["cond_multiplier"] = round(_cond_mult, 3)
            except Exception: pass

            min_score      = _min_score_for_regime(regime, strategy, cfg)

            # Two-stage filter:
            # Stage 1: vote_threshold — must be credible to vote
            _vote_threshold = float(cfg.get("vote_threshold", 1.5))
            if adjusted_score < _vote_threshold:
                rejections.append(
                    f"{strategy}: below_vote_threshold raw={raw_score:.2f} "
                    f"adj={adjusted_score:.2f} min_vote={_vote_threshold:.1f}"
                )
                continue
            # STT breakeven check — April 2026: need 15+ pts minimum
            if _VP_ADV_AVAIL and symbol:
                try:
                    _be = _get_stt_be(symbol)
                    _min_pts = _be.get("min_target_pts", 0)
                    _target_pts = abs(raw_score) * 5  # score → approximate points
                    if _min_pts > 0 and _target_pts < _min_pts * 0.5:
                        pass  # advisory only — logged not blocked
                except Exception: pass
            # Stage 2: per-strategy min_score recorded but NOT used to eliminate
            # Individual score stored; final gate applied AFTER confluence boost
            _pre_confluence_min = min_score  # stored for post-confluence check

            # Relax HTF filter on expiry day (HTF often SIDEWAYS on expiry)
            _htf_passes = _passes_htf_filter(direction, htf_bias, regime, cfg)
            if not _htf_passes:
                # On expiry day allow mean-reversion despite SIDEWAYS HTF
                _is_mr = any(x in strategy.lower() for x in ["mean","vwap","revert","cpr"])
                _is_expiry_today = False
                if _EXPIRY_AVAILABLE:
                    try:
                        _er_now = _get_expiry()
                        _is_expiry_today = _er_now.get("is_expiry_day", False)
                    except Exception: pass
                if _is_mr and _is_expiry_today:
                    pass   # allow mean-reversion on expiry despite SIDEWAYS HTF
                else:
                    # Soft penalty instead of hard block (AlgoTest / QuantConnect approach).
                    # -2.5 deduction: weak signals die, strong confluence signals survive.
                    # Hard block was discarding ~30-40% of valid signals on every scan.
                    _htf_penalty = float(cfg.get("htf_misalign_penalty", -2.5))
                    adjusted_score = adjusted_score + _htf_penalty
                    _cand_meta["htf_penalty"] = _htf_penalty
                    rejections.append(f"{strategy}: HTF_soft_penalty={_htf_penalty} ({htf_bias})")

            # Weinstein Stage filter for stocks — needs DAILY bars, not df_htf
            # (1h): with 1h the filter returned its no-daily-data default on
            # nearly every stock. None is handled (allow, score_mod 0).
            if _WEINSTEIN_AVAILABLE and symbol:
                try:
                    _ws = _weinstein_filter(symbol, direction, _weinstein_daily_df(symbol))
                    if not _ws.get("allow", True):
                        rejections.append(f"{strategy}: weinstein_{_ws.get('reason','blocked')}")
                        continue
                    adjusted_score += _ws.get("score_mod", 0.0)
                    _cand_meta["weinstein_mod"] = float(_ws.get("score_mod", 0.0) or 0.0)
                except Exception as _e:
                    import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

            if not _passes_volume_gate(df, cfg, symbol=symbol):
                rejections.append(f"{strategy}: volume_ratio_too_low")
                continue

            # Breakout requires stronger volume confirmation
            if strategy == "breakout":
                try:
                    min_bo_vr = float(cfg.get("min_breakout_volume_ratio", 1.20))
                    vr_col = (
                        "volume_ratio" if "volume_ratio" in df.columns
                        else "vol_ratio" if "vol_ratio" in df.columns
                        else None
                    )
                    if vr_col and min_bo_vr > 0:
                        vr = float(df[vr_col].iloc[-1])
                        if 0 < vr < min_bo_vr:
                            rejections.append(f"breakout: insufficient_volume ratio={vr:.2f}<{min_bo_vr:.2f}")
                            continue
                except Exception as _e:
                    import logging; logging.getLogger(__name__).debug("suppressed: %s", _e)

            # Build a human-readable trigger list for signal explainability
            _triggers = []
            if raw_score != adjusted_score:
                _triggers.append(f"base={raw_score:.1f}→adj={adjusted_score:.1f}")
            if _cand_meta.get("cond_multiplier") and _cand_meta["cond_multiplier"] != 1.0:
                _triggers.append(f"cond_mult×{_cand_meta['cond_multiplier']:.2f}")
            if _cand_meta.get("htf_penalty"):
                _triggers.append(f"htf_penalty={_cand_meta['htf_penalty']}")
            if _cand_meta.get("eod_learning_weight"):
                _nudge = _cand_meta["eod_learning_weight"].get("nudge", 0)
                if abs(float(_nudge or 0)) >= 0.01:
                    _triggers.append(f"eod_nudge={float(_nudge):+.2f}")
            if _cand_factors:
                _triggers.append("factors=" + "+".join(sorted(_cand_factors)[:3]))

            # Prune: subtract any gated-off ADDITIVE modifiers' captured contribution
            # from the final score (reversible; empty by default → no-op). Only numeric
            # modifier values are subtracted; operator disables additive-modifier keys.
            # Analyzer/prune names differ from the meta keys, and clamped modifiers
            # store RAW alongside the *_applied value actually added to the score —
            # subtract the applied value, never the raw one.
            if _PRUNE_MODS:
                _PRUNE_KEYS = {
                    "cross_asset_mod": "cross_asset",
                    "expiry_mod":      "expiry_regime",
                    "mtf_pivot_mod":   "mtf_pivot_applied",
                    "sr_level_mod":    "sr_level_mod_applied",
                    "participant_mod": "participant_oi_applied",
                }
                for _mk in _PRUNE_MODS:
                    _kk = _PRUNE_KEYS.get(_mk, _mk)
                    _mv = _cand_meta.get(_kk, _sig_meta.get(_kk))
                    if isinstance(_mv, (int, float)):
                        adjusted_score -= float(_mv)

            candidates.append({
                "symbol":    symbol,
                "strategy":  strategy,
                "side":      direction,
                "score":     round(adjusted_score, 4),
                "raw_score": round(raw_score, 4),
                "entry_quality": _entry_quality,
                "factor_confirmations": sorted(f for f in _cand_factors if f),
                "signal_meta": {**_sig_meta, **_cand_meta},
                "volume_ratio": _cand_meta.get("volume_ratio"),
                "triggers":  _triggers,
            })

        except Exception as exc:
            rejections.append(f"{strategy_fn.__name__}: {exc}")
            # Also leave a log trail — rejection stats are per-scan and ephemeral,
            # so a strategy that errors every bar was otherwise invisible in logs.
            logger.debug("strategy %s raised: %s", strategy_fn.__name__, exc, exc_info=True)

    # ── CONFLUENCE ENGINE: All strategies run, vote by direction ────────────
    # This is the core upgrade — no strategy is time-gated to zero.
    # Every strategy runs on every bar. Confluence = multiple strategies
    # pointing the same direction = higher confidence trade.
    #
    # Scoring table:
    #   1 strategy agrees  → no boost (min_score filter still applies)
    #   2 strategies agree → +1.5 boost (WEAK confluence)
    #   3 strategies agree → +3.0 boost (MEDIUM confluence)
    #   4-5 agree          → +5.0 boost (STRONG confluence)
    #   6+ agree           → +7.0 boost (VERY STRONG confluence)
    #
    # Also: conflicting signals (BUY and SELL same bar) reduce score.

    shadow_candidates: List[Dict[str, Any]] = []

    def _shadow_from_candidate(cand: Dict[str, Any], reason: str) -> Dict[str, Any]:
        meta = dict(cand.get("signal_meta", {}) or {})
        meta["shadow_reason"] = reason
        return {
            "symbol": symbol,
            "side": cand.get("side"),
            "direction": cand.get("side"),
            "strategy": cand.get("strategy", ""),
            "score": round(float(cand.get("score", 0) or 0), 4),
            "raw_score": round(float(cand.get("raw_score", cand.get("score", 0)) or 0), 4),
            "confluence": cand.get("confluence", "SHADOW"),
            "n_agree": cand.get("n_agree", 1),
            "n_conflict": cand.get("n_conflict", 0),
            "agreeing": cand.get("agreeing_strategies", [cand.get("strategy", "")]),
            "volume_ratio": cand.get("volume_ratio"),
            "signal_meta": meta,
            "metadata": {
                "score_modifiers": meta,
                "market_profile": meta.get("market_profile"),
                "market_quality": meta.get("market_quality"),
                "candidate_quality": meta.get("candidate_quality"),
                "shadow_reason": reason,
            },
            "reason": reason,
            "shadow_signal": True,
            "paper_training_mode": paper_training_mode,
            "regime": regime,
            "htf_bias": htf_bias,
            "feed_degraded": bool(cfg.get("feed_degraded", False)),
            "feed_degraded_reasons": list(cfg.get("feed_degraded_reasons", []) or []),
            "strategy_live_ready": bool(cfg.get("strategy_live_ready", True)),
            "strategy_selection_mode": str(cfg.get("strategy_selection_mode", "")),
            "strategy_live_block_reason": str(cfg.get("strategy_live_block_reason", "")),
        }

    if len(candidates) >= 1:
        raw_candidates = list(candidates)
        # Count votes by direction
        buy_cands  = [c for c in candidates if c["side"] in ("BUY","LONG")]
        sell_cands = [c for c in candidates if c["side"] in ("SELL","SHORT")]
        n_buy  = len(buy_cands)
        n_sell = len(sell_cands)

        # Conflict penalty: opposing signals reduce conviction
        conflict_penalty = 0.0
        if n_buy > 0 and n_sell > 0:
            conflict_ratio   = min(n_buy, n_sell) / max(n_buy, n_sell)
            conflict_penalty = round(-conflict_ratio * 2.0, 2)

        # Pick majority direction
        if n_buy >= n_sell:
            majority_dir  = "BUY"
            n_agree       = n_buy
            majority_cands = buy_cands
        else:
            majority_dir  = "SELL"
            n_agree       = n_sell
            majority_cands = sell_cands
        majority_ids = {id(c) for c in majority_cands}
        for c in raw_candidates:
            if id(c) not in majority_ids:
                shadow_candidates.append(_shadow_from_candidate(c, "shadow_minority_confluence"))

        # De-correlated confluence: count distinct market FACTORS that agree,
        # not raw strategy votes. Five trend indicators all firing bullish is one
        # factor (trend), not five confirmations — counting raw votes inflates
        # confidence on correlated signals. Falls back to the raw count if the
        # cluster map is unavailable.
        n_agree_eff = n_agree
        _factor_set = set()
        try:
            from strategy_clusters import effective_confluence, factors_present
            _strats = [c.get("strategy", "") for c in majority_cands]
            n_agree_eff = effective_confluence(_strats)
            _factor_set.update(factors_present(_strats))
        except Exception:
            pass
        for _cand in majority_cands:
            _factor_set.update(_cand.get("factor_confirmations", []) or [])
        _factor_set.discard("")
        if _factor_set:
            n_agree_eff = max(n_agree_eff, len(_factor_set))
        _factors = ",".join(sorted(_factor_set))

        # Expiry-day flag (used by single-strategy boost below). Recomputed
        # here at confluence scope — the per-strategy _is_expiry_today above
        # is loop-local and not in scope at this point.
        _is_expiry_day = False
        if _EXPIRY_AVAILABLE:
            try:
                _is_expiry_day = bool(_get_expiry().get("is_expiry_day", False))
            except Exception:
                _is_expiry_day = False

        # Confluence boost table — keyed on EFFECTIVE (de-correlated) factor count.
        # On expiry day: single strategy is enough (max pain pull is the signal)
        if   n_agree_eff >= 5: boost = 7.0; conf_label = "VERY_STRONG"
        elif n_agree_eff >= 4: boost = 5.0; conf_label = "STRONG"
        elif n_agree_eff >= 3: boost = 3.0; conf_label = "MEDIUM"
        elif n_agree_eff == 2: boost = 1.5; conf_label = "WEAK"
        elif _is_expiry_day: boost = 0.5; conf_label = "EXPIRY_SINGLE"
        else:              boost = 0.0; conf_label = "SINGLE"

        total_boost = boost + conflict_penalty

        # Apply boost to all majority candidates
        for c in majority_cands:
            c["score"]       = round(c["score"] + total_boost, 4)
            c["confluence"]  = conf_label
            c["n_agree"]     = n_agree
            c["n_agree_eff"] = n_agree_eff
            c["factors"]     = _factors
            c["n_conflict"]  = min(n_buy, n_sell)
            c["conflict_pen"]= conflict_penalty

        # List which strategies agreed
        agreeing_strategies = [c["strategy"] for c in majority_cands]
        for c in majority_cands:
            c["agreeing_strategies"] = agreeing_strategies

        candidates = majority_cands

    # ── POST-CONFLUENCE THRESHOLD — the REAL gate ──────────────────────────────
    # Now apply the actual min_score threshold on BOOSTED scores
    # Strategies that were individually weak but collectively strong PASS here
    _post_min = float(cfg.get("post_confluence_min_score", 3.5))
    _pre_filter_count = len(candidates)
    pre_post_filter_candidates = list(candidates)
    candidates = [c for c in candidates if c.get("score", 0) >= _post_min]
    passed_ids = {id(c) for c in candidates}
    for c in pre_post_filter_candidates:
        if id(c) not in passed_ids:
            shadow_candidates.append(_shadow_from_candidate(c, "shadow_post_confluence_below_min"))
    if len(candidates) < _pre_filter_count:
        rejections.append(
            f"post_confluence_filter: {_pre_filter_count - len(candidates)} "
            f"candidates below {_post_min:.1f} after boost"
        )

    if not candidates:
        # Log what killed all signals — helps debug
        rejection_summary = {}
        for r in rejections:
            reason = r.split(":")[1].strip().split(" ")[0] if ":" in r else "unknown"
            rejection_summary[reason] = rejection_summary.get(reason, 0) + 1
        logger.info(
            "ZERO SIGNALS for %s | regime=%s htf=%s | rejections: %s",
            symbol, regime, htf_bias,
            ", ".join(f"{k}×{v}" for k,v in sorted(rejection_summary.items(),key=lambda x:-x[1])[:5])
        )

    if candidates:
        best       = sorted(candidates, key=lambda x: x["score"], reverse=True)[0]
        best_id    = id(best)
        for c in candidates:
            if id(c) != best_id:
                shadow_candidates.append(_shadow_from_candidate(c, "shadow_passed_not_best"))
        price      = _get_price(df)
        qty        = _get_qty(price, effective_capital)
        volatility = _calculate_volatility(df)
        best_meta  = dict(best.get("signal_meta", {}) or {})
        if best.get("factors"):
            best_meta["confluence_factors"] = best.get("factors")
        if best.get("factor_confirmations"):
            best_meta["factor_confirmations"] = best.get("factor_confirmations")
        _decision_inputs = {
            "patterns": best_meta.get("patterns"),
            "all_patterns": best_meta.get("all_patterns"),
            "levels": best_meta.get("levels"),
            "ochao_levels": best_meta.get("ochao_levels") or _sig_meta.get("ochao_levels"),
            "cpr_width": best_meta.get("cpr_width"),
            "cpr_bias": best_meta.get("cpr_bias"),
            "cpr_structure": best_meta.get("cpr_structure"),
            "pivot_scalp_context": best_meta.get("pivot_scalp_context"),
            "style": best_meta.get("style"),
            "instrument_type": best_meta.get("instrument_type"),
            "mtf_pivot_mod": best_meta.get("mtf_pivot_mod"),
            "mtf_pivot_ctx": best_meta.get("mtf_pivot_ctx"),
            "mtf_indicator_mod": best_meta.get("mtf_indicator_mod"),
            "mtf_indicator_bias": best_meta.get("mtf_indicator_bias") or _sig_meta.get("mtf_indicator_context", {}).get("bias"),
            "mtf_indicator_context": best_meta.get("mtf_indicator_context") or _sig_meta.get("mtf_indicator_context"),
            "indicator_confluence_score": best_meta.get("indicator_confluence_score"),
            "indicator_confluence": best_meta.get("indicator_confluence"),
            "structure_context": best_meta.get("structure_context") or _sig_meta.get("structure_context"),
            "structure_mod": best_meta.get("structure_mod"),
            "structure_label": best_meta.get("structure_label"),
            "structure_direction": best_meta.get("structure_direction"),
            "structure_bos": best_meta.get("structure_bos"),
            "structure_choch": best_meta.get("structure_choch"),
            "structure_score": best_meta.get("structure_score"),
            "market_quality": best_meta.get("market_quality") or _sig_meta.get("market_quality"),
            "market_quality_mod": best_meta.get("market_quality_mod"),
            "market_profile": best_meta.get("market_profile") or _sig_meta.get("market_profile"),
            "market_profile_mod": best_meta.get("market_profile_mod"),
            "profile_bias": best_meta.get("profile_bias"),
            "profile_position": best_meta.get("profile_position"),
            "profile_poc": best_meta.get("profile_poc"),
            "profile_vah": best_meta.get("profile_vah"),
            "profile_val": best_meta.get("profile_val"),
            "profile_poc_distance_pct": best_meta.get("profile_poc_distance_pct"),
            "profile_value_width_pct": best_meta.get("profile_value_width_pct"),
            "profile_acceptance": best_meta.get("profile_acceptance"),
            "candidate_quality": best_meta.get("candidate_quality"),
            "candidate_quality_mod": best_meta.get("candidate_quality_mod"),
            "candidate_quality_confirmations": best_meta.get("candidate_quality_confirmations"),
            "volume_ratio": best_meta.get("volume_ratio") or _sig_meta.get("volume_ratio"),
            "volume_data_quality": best_meta.get("volume_data_quality") or _sig_meta.get("volume_data_quality"),
            "vix": best_meta.get("vix") or _sig_meta.get("vix"),
            "iv": best_meta.get("iv") or best_meta.get("atm_iv") or _sig_meta.get("iv"),
            "iv_percentile": best_meta.get("iv_percentile") or _sig_meta.get("iv_percentile"),
            "pcr": best_meta.get("pcr") or _sig_meta.get("pcr"),
            "oi_direction": best_meta.get("oi_direction"),
            "gex_modifier": best_meta.get("gex_modifier") or _sig_meta.get("gex_modifier"),
            "skew_velocity_mod": best_meta.get("skew_velocity_mod") or _sig_meta.get("skew_velocity_mod"),
            "global_bias": best_meta.get("global_bias") or _sig_meta.get("global_bias"),
            "global_change_pct": best_meta.get("global_change_pct") or _sig_meta.get("global_change_pct"),
            "cross_asset_bias": best_meta.get("cross_asset_bias") or _sig_meta.get("cross_asset_bias"),
            "news_score": best_meta.get("news_score") or _sig_meta.get("news_score"),
            "expiry_dte": best_meta.get("expiry_dte") or _sig_meta.get("expiry_dte"),
            "expiry_regime": best_meta.get("expiry_regime") or _sig_meta.get("expiry_regime"),
            "data_confidence": best_meta.get("data_confidence") or _sig_meta.get("data_confidence"),
            "feed_degraded": bool(best_meta.get("feed_degraded") or _sig_meta.get("feed_degraded", False)),
            "feed_degraded_reasons": best_meta.get("feed_degraded_reasons") or _sig_meta.get("feed_degraded_reasons"),
            "regime": regime,
            "htf_bias": htf_bias,
        }
        best_meta["decision_inputs"] = _decision_inputs

        final_signal = {
            "symbol":     symbol,
            "side":       best["side"],
            "direction":  best["side"],   # alias for side — used by some callers
            "strategy":   best["strategy"],
            "generated_at": time.time(),
            "score":      best["score"],
            "raw_score":  best["raw_score"],
            "confluence":  best.get("confluence", "SINGLE"),
            "n_agree":     best.get("n_agree", 1),
            "n_agree_eff": best.get("n_agree_eff", best.get("n_agree", 1)),
            "factors":     best.get("factors", ""),
            "n_conflict":  best.get("n_conflict", 0),
            "agreeing":    best.get("agreeing_strategies", [best["strategy"]]),
            "all_candidates": [
                (c["strategy"], c["score"])
                for c in sorted(candidates, key=lambda x: x["score"], reverse=True)[
                    : int(cfg.get("max_candidates_logged_per_cycle", 25) or 25)
                ]
            ],
            "price":      price,
            "qty":        qty,
            "strength":   best["score"],
            "volatility": volatility,
            "regime":     regime,
            "htf_bias":   htf_bias,
            "reason":     "best_candidate",
            "paper_training_mode": paper_training_mode,
            "feed_degraded": bool(cfg.get("feed_degraded", False)),
            "feed_degraded_reasons": list(cfg.get("feed_degraded_reasons", []) or []),
            "strategy_live_ready": bool(cfg.get("strategy_live_ready", True)),
            "strategy_selection_mode": str(cfg.get("strategy_selection_mode", "")),
            "strategy_live_block_reason": str(cfg.get("strategy_live_block_reason", "")),
            "candidates": candidates,
            "shadow_candidates": shadow_candidates[:int(cfg.get("shadow_max_candidates_per_symbol", 12) or 12)],
            "rejections": rejections,
            "capital_used": effective_capital,
            "signal_meta": best_meta,
            "triggers":   best.get("triggers", []),
        }

        # Generate AI plain-English narrative (async-safe, cached, graceful fallback)
        try:
            from narrative_engine import generate_signal_narrative as _narrate
            final_signal["ai_reason"] = _narrate(final_signal)
        except Exception:
            final_signal["ai_reason"] = ""

        # Calibrated logistic regression probability (cross-check on XGBoost)
        try:
            from signal_calibrator import calibrate_signal as _calibrate
            _cal = _calibrate(final_signal)
            final_signal["calibrated_prob"]  = _cal.get("calibrated_prob", 0.5)
            final_signal["log_reg_verdict"]  = _cal.get("log_reg_verdict", "NO_MODEL")
            final_signal["cal_top_positive"] = _cal.get("top_positive", [])
        except Exception:
            pass

        # ── Learned-filters auto-upgrade layer ───────────────────────────────
        # Reads learned_filters.json written by post_market_ml.py nightly.
        # Applies pattern-matched score multipliers from yesterday's autopsy.
        try:
            from ml_feature_builder import build_signal_context as _build_ctx
            from learned_filters    import apply_learned_filters as _lf
            _ctx    = _build_ctx(final_signal)
            _ctx["__symbol"] = symbol
            # Raw strategy name so promoted cross rules (strategy×regime etc.)
            # can match at trade time — the numeric context has no strategy.
            _ctx["__strategy"] = str(final_signal.get("strategy", ""))
            _lf_res = _lf(_ctx)
            if _lf_res["mult"] != 1.0:
                final_signal["score"] = round(final_signal["score"] * _lf_res["mult"], 4)
                final_signal["lf_mult"]   = _lf_res["mult"]
                final_signal["lf_reason"] = _lf_res["reason"]
            final_signal["ml_win_prob"] = _lf_res.get("win_prob", 0.5)
        except Exception:
            pass

        # Macro/global profit filter for index option buying.  It never places
        # orders; it blocks/waits only when macro context, CPR/VWAP, trap and
        # premium-decay quality say the candidate is low quality.
        try:
            _macro_symbols = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"}
            _sym_u = str(symbol or "").upper()
            _is_index_option_context = (
                _sym_u in _macro_symbols
                or str((option_data or {}).get("underlying", "")).upper() in _macro_symbols
                or str((option_data or {}).get("asset_type", "")).upper() == "OPTION"
                or str((option_data or {}).get("instrument_type", "")).upper() in {"OPTIDX", "OPTION"}
            )
            if _is_index_option_context:
                from macro_global_profit_engine import apply_macro_profit_filter

                _macro_filter = apply_macro_profit_filter(
                    final_signal,
                    technical_context=best_meta.get("decision_inputs", best_meta),
                    option_data=option_data,
                )
                _macro_snapshot = _macro_filter.get("snapshot", {}) or {}
                _macro_adj = float(_macro_filter.get("confidence_adjustment", 0.0) or 0.0)
                final_signal["macro_profit_filter"] = {
                    "trade_permission": _macro_filter.get("trade_permission"),
                    "allowed_direction": _macro_filter.get("allowed_direction"),
                    "global_score": _macro_filter.get("global_score"),
                    "profit_quality_score": _macro_filter.get("profit_quality_score"),
                    "confidence_adjustment": _macro_adj,
                    "risk_warning": _macro_filter.get("risk_warning", ""),
                    "reasons": _macro_filter.get("reasons", []),
                    "gap_prediction": _macro_snapshot.get("gap_prediction"),
                    "market_regime": _macro_snapshot.get("market_regime"),
                }
                final_signal["macro_global_score"] = _macro_filter.get("global_score")
                final_signal["macro_profit_quality_score"] = _macro_filter.get("profit_quality_score")
                final_signal["macro_trade_permission"] = _macro_filter.get("trade_permission")
                final_signal["macro_allowed_direction"] = _macro_filter.get("allowed_direction")
                final_signal["macro_regime"] = _macro_snapshot.get("market_regime")
                final_signal["macro_gap_prediction"] = _macro_snapshot.get("gap_prediction")

                if _MACRO_ENFORCE and _macro_filter.get("trade_permission") == "BLOCK":
                    final_signal["side"] = None
                    final_signal["direction"] = None
                    final_signal["reason"] = "macro_global_profit_filter_block"
                    final_signal.setdefault("rejections", []).append(
                        "macro_global_profit_filter: "
                        + str(_macro_filter.get("risk_warning") or _macro_filter.get("reasons", ["blocked"])[0])
                    )
                    return final_signal
                if _MACRO_ENFORCE and _macro_filter.get("trade_permission") == "WAIT":
                    final_signal["score"] = round(float(final_signal.get("score", 0) or 0) * 0.75, 4)
                    final_signal["strength"] = final_signal["score"]
                    final_signal.setdefault("triggers", []).append("macro_wait_x0.75")
                elif _MACRO_ENFORCE and _macro_adj:
                    final_signal["score"] = round(float(final_signal.get("score", 0) or 0) * (1.0 + _macro_adj), 4)
                    final_signal["strength"] = final_signal["score"]
                    final_signal.setdefault("triggers", []).append(f"macro_adj={_macro_adj:+.2f}")
        except Exception as _macro_exc:
            final_signal.setdefault("macro_profit_filter_error", str(_macro_exc)[:120])

        return final_signal

    # Fallback path
    if cfg.get("enable_fallback", True):
        score, direction = calculate_signal_score(df, df_htf, option_data)
        direction        = _normalize_direction(direction)
        score            = float(score)

        if direction and score >= float(cfg["fallback_score_threshold"]):
            _fallback_quality = _entry_quality_adjustment(
                df=df,
                direction=direction,
                strategy="fallback",
                cfg=cfg,
                paper_training_mode=paper_training_mode,
            )
            score += float(_fallback_quality.get("score_modifier", 0.0) or 0.0)
            if (
                bool(cfg.get("entry_quality_block_live", True))
                and not paper_training_mode
                and _fallback_quality.get("hard_block")
            ):
                return {
                    "symbol": symbol,
                    "side": None,
                    "reason": "fallback_entry_quality_block",
                    "quality": _fallback_quality,
                    "paper_training_mode": paper_training_mode,
                    "feed_degraded": bool(cfg.get("feed_degraded", False)),
                    "feed_degraded_reasons": list(cfg.get("feed_degraded_reasons", []) or []),
                    "strategy_live_ready": bool(cfg.get("strategy_live_ready", True)),
                    "strategy_selection_mode": str(cfg.get("strategy_selection_mode", "")),
                    "strategy_live_block_reason": str(cfg.get("strategy_live_block_reason", "")),
                    "rejections": rejections + ["fallback: entry_quality_block"],
                    "shadow_candidates": shadow_candidates[:int(cfg.get("shadow_max_candidates_per_symbol", 12) or 12)],
                    "regime": regime,
                    "htf_bias": htf_bias,
                }
            if score < float(cfg["fallback_score_threshold"]):
                return {
                    "symbol": symbol,
                    "side": None,
                    "reason": "fallback_entry_quality_below_threshold",
                    "quality": _fallback_quality,
                    "paper_training_mode": paper_training_mode,
                    "feed_degraded": bool(cfg.get("feed_degraded", False)),
                    "feed_degraded_reasons": list(cfg.get("feed_degraded_reasons", []) or []),
                    "strategy_live_ready": bool(cfg.get("strategy_live_ready", True)),
                    "strategy_selection_mode": str(cfg.get("strategy_selection_mode", "")),
                    "strategy_live_block_reason": str(cfg.get("strategy_live_block_reason", "")),
                    "rejections": rejections + ["fallback: entry_quality_below_threshold"],
                    "shadow_candidates": shadow_candidates[:int(cfg.get("shadow_max_candidates_per_symbol", 12) or 12)],
                    "regime": regime,
                    "htf_bias": htf_bias,
                }
            # Fallback trade gate: disabled until edge validated over multiple months.
            # Enable via FALLBACK_TRADE=true in .env once out-of-sample WR confirmed.
            if not cfg.get("fallback_trade", False):
                return {
                    "symbol":             symbol,
                    "side":               None,
                    "reason":             "fallback_no_trade_mode",
                    "score":              round(score, 4),
                    "direction_would_be": direction,
                    "quality":            _fallback_quality,
                    "paper_training_mode": paper_training_mode,
                    "feed_degraded": bool(cfg.get("feed_degraded", False)),
                    "feed_degraded_reasons": list(cfg.get("feed_degraded_reasons", []) or []),
                    "strategy_live_ready": bool(cfg.get("strategy_live_ready", True)),
                    "strategy_selection_mode": str(cfg.get("strategy_selection_mode", "")),
                    "strategy_live_block_reason": str(cfg.get("strategy_live_block_reason", "")),
                    "rejections": rejections + ["fallback: no_trade_mode"],
                    "shadow_candidates": shadow_candidates[:int(cfg.get("shadow_max_candidates_per_symbol", 12) or 12)],
                    "regime":    regime,
                    "htf_bias":  htf_bias,
                }
            price = _get_price(df)
            qty   = _get_qty(price, effective_capital)

            return {
                "symbol":     symbol,
                "side":       direction,
                "direction":  direction,
                "strategy":   "fallback",
                "score":      round(score, 4),
                "raw_score":  round(score, 4),
                "price":      price,
                "qty":        qty,
                "strength":   score,
                "volatility": _calculate_volatility(df),
                "regime":     regime,
                "htf_bias":   htf_bias,
                "reason":     "fallback",
                "generated_at": time.time(),
                "paper_training_mode": paper_training_mode,
                "feed_degraded": bool(cfg.get("feed_degraded", False)),
                "feed_degraded_reasons": list(cfg.get("feed_degraded_reasons", []) or []),
                "strategy_live_ready": bool(cfg.get("strategy_live_ready", True)),
                "strategy_selection_mode": str(cfg.get("strategy_selection_mode", "")),
                "strategy_live_block_reason": str(cfg.get("strategy_live_block_reason", "")),
                "rejections": rejections,
                "shadow_candidates": shadow_candidates[:int(cfg.get("shadow_max_candidates_per_symbol", 12) or 12)],
                "capital_used": effective_capital,
                "quality": _fallback_quality,
            }

    return {
        "symbol":     symbol,
        "side":       None,
        "reason":     "NO_VALID_SIGNAL",
        "paper_training_mode": paper_training_mode,
        "feed_degraded": bool(cfg.get("feed_degraded", False)),
        "feed_degraded_reasons": list(cfg.get("feed_degraded_reasons", []) or []),
        "strategy_live_ready": bool(cfg.get("strategy_live_ready", True)),
        "strategy_selection_mode": str(cfg.get("strategy_selection_mode", "")),
        "strategy_live_block_reason": str(cfg.get("strategy_live_block_reason", "")),
        "rejections": rejections,
        "shadow_candidates": shadow_candidates[:int(cfg.get("shadow_max_candidates_per_symbol", 12) or 12)],
        "regime":     regime,
        "htf_bias":   htf_bias,
    }
