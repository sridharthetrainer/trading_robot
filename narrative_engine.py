"""
narrative_engine.py — Rule-Based Signal Narrative Engine

Generates a 2-sentence plain-English explanation for every trade signal:
  Sentence 1: WHY this setup is technically valid (specific indicators/data)
  Sentence 2: WHAT exact condition would invalidate it (specific level/event)

Default mode: PURE RULE-BASED — zero API cost, zero external dependency.
The rule engine uses all available signal metadata (NR4/7, CRSI, ER, CHOP,
VPIN, volume, gap bias, GEX, regime, confluence, FII flow) to build a
specific, data-grounded narrative.

Optional LLM mode (provider-agnostic):
  Set LLM_PROVIDER in .env to enable. Supported providers:
    LLM_PROVIDER=ollama   → local Ollama (free, private)
                            LLM_URL=http://localhost:11434 (default)
                            LLM_MODEL=llama3.2 (default)
    LLM_PROVIDER=openai   → OpenAI API
                            OPENAI_API_KEY=sk-...
                            LLM_MODEL=gpt-4o-mini (default)
    LLM_PROVIDER=groq     → Groq API (fast, cheap)
                            GROQ_API_KEY=gsk_...
                            LLM_MODEL=llama-3.1-8b-instant (default)
    LLM_PROVIDER=gemini   → Google Gemini
                            GEMINI_API_KEY=AIza...
                            LLM_MODEL=gemini-1.5-flash (default)
    LLM_PROVIDER=none     → rule-based only (default)

Cache: in-memory, keyed by (symbol, strategy, direction, score_bucket).
  TTL: 300 seconds — same signal context returns cached narrative.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from datetime import date
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_CACHE: Dict[str, Dict] = {}
_TTL   = 300   # 5-minute cache

# ── System prompt used for ALL LLM providers ─────────────────────────────────
_SYSTEM_PROMPT = (
    "You are a professional NSE intraday trader. Explain trade setups in plain English. "
    "Be specific and direct. Reference the actual indicator values provided. "
    "No emojis. No hedging language. Max 35 words per sentence."
)


# ─────────────────────────────────────────────────────────────────────────────
# Provider-agnostic LLM call
# ─────────────────────────────────────────────────────────────────────────────

def _llm_provider() -> str:
    return os.getenv("LLM_PROVIDER", "none").lower().strip()


def _call_llm(prompt: str) -> str:
    """
    Call whichever LLM provider is configured in .env.
    Returns response text or empty string if unavailable/disabled.
    """
    provider = _llm_provider()

    if provider == "none" or not provider:
        return ""

    if provider == "ollama":
        return _call_ollama(prompt)
    if provider == "openai":
        return _call_openai(prompt)
    if provider == "groq":
        return _call_groq(prompt)
    if provider == "gemini":
        return _call_gemini(prompt)

    logger.debug("narrative_engine: unknown LLM_PROVIDER=%s, using rule-based", provider)
    return ""


def _call_ollama(prompt: str) -> str:
    base_url = os.getenv("LLM_URL", "http://localhost:11434").rstrip("/")
    model    = os.getenv("LLM_MODEL", "llama3.2")
    try:
        payload = json.dumps({
            "model":  model,
            "prompt": f"{_SYSTEM_PROMPT}\n\n{prompt}",
            "stream": False,
        }).encode()
        req = urllib.request.Request(
            f"{base_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
            return str(data.get("response", "")).strip()
    except Exception as e:
        logger.debug("Ollama error: %s", e)
        return ""


def _call_openai(prompt: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return ""
    model   = os.getenv("LLM_MODEL", "gpt-4o-mini")
    try:
        payload = json.dumps({
            "model": model,
            "max_tokens": 150,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": prompt},
            ],
        }).encode()
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.debug("OpenAI error: %s", e)
        return ""


def _call_groq(prompt: str) -> str:
    api_key = os.getenv("GROQ_API_KEY", "")
    if not api_key:
        return ""
    model   = os.getenv("LLM_MODEL", "llama-3.1-8b-instant")
    try:
        payload = json.dumps({
            "model": model,
            "max_tokens": 150,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": prompt},
            ],
        }).encode()
        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.debug("Groq error: %s", e)
        return ""


def _call_gemini(prompt: str) -> str:
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        return ""
    model = os.getenv("LLM_MODEL", "gemini-1.5-flash")
    try:
        payload = json.dumps({
            "contents": [{"parts": [{"text": f"{_SYSTEM_PROMPT}\n\n{prompt}"}]}],
            "generationConfig": {"maxOutputTokens": 150},
        }).encode()
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key={api_key}")
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        logger.debug("Gemini error: %s", e)
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# LLM prompt builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_llm_prompt(signal: Dict) -> str:
    symbol    = signal.get("symbol", "UNKNOWN")
    strategy  = signal.get("strategy", "unknown")
    direction = signal.get("direction") or signal.get("side", "UNKNOWN")
    score     = float(signal.get("score", 0))
    regime    = signal.get("regime", "UNKNOWN")
    stop      = signal.get("stop_loss", signal.get("stop", "N/A"))
    target    = signal.get("target", "N/A")
    meta      = signal.get("signal_meta", signal.get("meta", {})) or {}

    nr7  = int(meta.get("nr7", 0))
    nr4  = int(meta.get("nr4", 0))
    crsi = meta.get("connors_rsi", "N/A")
    er   = meta.get("efficiency_ratio", "N/A")
    chop = meta.get("choppiness_index", "N/A")
    vpin = meta.get("vpin", "N/A")
    vol  = meta.get("volume_ratio", "N/A")
    gap  = meta.get("gap_strategy_bias", "N/A")
    gex  = meta.get("gex_modifier", "N/A")
    skew = meta.get("skew_velocity_mod", "N/A")

    compression = "NR7 compression confirmed. " if nr7 else ("NR4 compression. " if nr4 else "")

    return (
        f"{symbol} {direction} via {strategy} | Score {score:.1f}/10 | Regime {regime}\n"
        f"Stop {stop} | Target {target}\n"
        f"CRSI {crsi} | ER {er} | CHOP {chop} | VPIN {vpin} | Vol {vol}x | {compression}"
        f"Gap bias {gap} | GEX mod {gex} | Skew vel {skew}\n\n"
        f"Write exactly 2 sentences:\n"
        f"1. WHY this {direction} setup is valid right now (cite specific values above).\n"
        f"2. WHAT specific condition immediately invalidates it (exact level or event).\n"
        f"No intro. Start with the reason directly."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Rule-based narrative engine
# ─────────────────────────────────────────────────────────────────────────────

_STRATEGY_WHY = {
    "orb":               "Opening range {dir}: price broke the {period}-minute opening range with volume confirmation",
    "vwap_reversion":    "{dir} from {delta} ATR extension beyond VWAP; mean-reversion to VWAP expected",
    "mean_reversion":    "Mean-reversion {dir}: price at statistical extreme, reversal bias elevated",
    "trend":             "Trend continuation {dir}: multi-indicator alignment in {regime} regime",
    "breakout":          "Breakout {dir}: price clearing key resistance/support with volume expansion",
    "supertrend_mtf":    "Multi-timeframe SuperTrend aligned {dir} across intraday and higher timeframe",
    "cpr":               "{dir} off CPR pivot: price interacting with Central Pivot Range at key level",
    "expiry_scalp":      "Expiry-day scalp {dir}: gamma acceleration near {strike} strike with OI buildup",
    "morning_momentum":  "Morning momentum {dir}: gap direction confirmed with first-bar volume strength",
    "order_block":       "{dir} from institutional order block: smart money supply/demand zone holding",
    "liquidity_sweep":   "Liquidity sweep {dir}: stop hunt completed, price reversing from swept level",
    "market_structure":  "Market structure {dir}: Break of Structure confirmed, CHoCH pattern complete",
    "ichimoku":          "Ichimoku {dir}: price and Cloud alignment signal continuation in {regime}",
    "rsi_divergence":    "RSI divergence {dir}: price and RSI disagreeing, reversal signal building",
    "gap_fill":          "Gap fill {dir}: opening gap of {gap_pct}% entering statistical fill zone",
    "ma_cross":          "Moving average cross {dir}: fast EMA crossed slow EMA with momentum",
    "scalping":          "Scalp {dir}: short-term momentum with above-average volume in tight range",
    "heikin_ashi":       "Heikin-Ashi {dir}: consecutive HA candles confirm directional conviction",
    "vpoc_magnet":       "{dir} toward VPOC: volume point of control acting as price magnet",
    "vwap_bands":        "{dir} from VWAP band: price at statistical deviation from volume anchor",
    "institutional_scalp": "{dir} with institutional flow: order flow imbalance supporting direction",
    "pivot_boss":        "{dir} off Pivot Boss level: price at key pivot with structure confirmation",
}

_INVALIDATE = {
    "orb":            "fails on close back inside the opening range",
    "vwap_reversion": "fails if price continues away from VWAP without reversal",
    "mean_reversion": "fails if price extends further in the original direction",
    "trend":          "fails on break below/above the key EMA support/resistance",
    "breakout":       "fails on close back below/above the breakout level",
    "expiry_scalp":   "fails on delta spike or circuit breaker event",
    "default":        "fails if price closes beyond stop level",
}


def _safe_float(val) -> Optional[float]:
    try:
        return float(val) if val not in (None, "N/A", "") else None
    except (ValueError, TypeError):
        return None


def _build_why_sentence(signal: Dict, meta: Dict) -> str:
    """Build the WHY sentence from signal metadata using richer context."""
    direction = str(signal.get("direction") or signal.get("side", "")).upper()
    strategy  = str(signal.get("strategy", "")).lower().replace(" ", "_")
    score     = float(signal.get("score", 0))
    regime    = str(signal.get("regime", "")).upper()
    symbol    = str(signal.get("symbol", ""))

    # Extract numeric indicators
    nr7  = int(meta.get("nr7", 0) or 0)
    nr4  = int(meta.get("nr4", 0) or 0)
    crsi = _safe_float(meta.get("connors_rsi"))
    er   = _safe_float(meta.get("efficiency_ratio"))
    chop = _safe_float(meta.get("choppiness_index"))
    vpin = _safe_float(meta.get("vpin"))
    vol  = _safe_float(meta.get("volume_ratio"))
    gap  = str(meta.get("gap_strategy_bias", ""))
    gex  = _safe_float(meta.get("gex_modifier"))
    n_agree = int(signal.get("n_agree", 1) or 1)
    confluence = str(signal.get("confluence", "SINGLE"))

    # Build evidence list (most specific first)
    evidence = []

    if nr7:
        evidence.append("NR7 volatility compression just ended")
    elif nr4:
        evidence.append("NR4 compression pattern confirmed")

    if crsi is not None:
        if crsi < 10 and direction == "BUY":
            evidence.append(f"Connors RSI at extreme low ({crsi:.0f}) — mean-reversion setup")
        elif crsi > 90 and direction == "SELL":
            evidence.append(f"Connors RSI at extreme high ({crsi:.0f}) — mean-reversion setup")

    if er is not None:
        if er > 0.60:
            evidence.append(f"market strongly directional (Efficiency Ratio {er:.2f})")
        elif er < 0.25:
            evidence.append(f"low efficiency ratio ({er:.2f}) — scalp/MR conditions")

    if chop is not None:
        if chop < 38.2:
            evidence.append(f"Choppiness Index at {chop:.0f} — confirmed trending market")
        elif chop > 61.8:
            evidence.append(f"Choppiness Index at {chop:.0f} — confirmed ranging market")

    if vpin is not None and vpin > 0.65:
        evidence.append(f"VPIN at {vpin:.2f} — elevated informed institutional flow")

    if vol is not None and vol > 1.4:
        evidence.append(f"volume {vol:.1f}× above average — conviction move")

    if gap == "EXPECT_FILL" and direction == "SELL":
        evidence.append("gap expected to fill — short bias confirmed")
    elif gap == "EXPECT_CONTINUE" and direction == "BUY":
        evidence.append("large gap likely to continue higher — momentum confirmed")

    if gex is not None and abs(gex) > 0.3:
        if gex > 0:
            evidence.append("GEX regime supports this direction")
        else:
            evidence.append("price near GEX resistance/support — key level")

    if n_agree >= 3:
        evidence.append(f"{n_agree} strategies agreeing ({confluence} confluence)")
    elif n_agree == 2:
        evidence.append("dual-strategy confluence")

    # Get strategy-specific template
    strat_key = strategy
    # Try partial match
    if strat_key not in _STRATEGY_WHY:
        for k in _STRATEGY_WHY:
            if k in strat_key or strat_key.startswith(k):
                strat_key = k
                break
        else:
            strat_key = None

    if strat_key:
        tmpl = _STRATEGY_WHY[strat_key]
        base = tmpl.format(
            dir=direction, regime=regime, symbol=symbol,
            delta="1.5", period="5", strike="ATM", gap_pct="0.5"
        )
    else:
        base = f"{direction} signal from {strategy.replace('_', ' ')} (score {score:.1f}/10, {regime} regime)"

    if evidence:
        return f"{base}: {'; '.join(evidence[:2])}."
    return f"{base}."


def _build_invalidate_sentence(signal: Dict, meta: Dict) -> str:
    """Build the INVALIDATION sentence."""
    strategy = str(signal.get("strategy", "")).lower()
    stop     = signal.get("stop_loss", signal.get("stop"))
    target   = signal.get("target")
    direction = str(signal.get("direction") or signal.get("side", "")).upper()
    n_conflict = int(signal.get("n_conflict", 0) or 0)

    # Strategy-specific invalidation
    strat_key = strategy
    if strat_key not in _INVALIDATE:
        for k in _INVALIDATE:
            if k in strat_key:
                strat_key = k
                break
        else:
            strat_key = "default"

    base_inv = _INVALIDATE[strat_key]

    # Build specific level reference
    parts = []
    if stop and stop not in ("N/A", None, ""):
        parts.append(f"price close beyond stop {stop}")
    else:
        parts.append(base_inv)

    if target and target not in ("N/A", None, ""):
        parts.append(f"target {target} not reached within 3 bars")

    if n_conflict > 0:
        parts.append(f"{n_conflict} opposing strategy signal{'s' if n_conflict > 1 else ''} — watch for reversal")

    er = _safe_float(meta.get("efficiency_ratio"))
    if er is not None and er > 0.50:
        opp_dir = "below key EMA" if direction == "BUY" else "above key EMA"
        parts.append(f"or ER drops below 0.25 (market turns choppy)")

    return "Setup fails if " + "; or ".join(parts[:2]) + "."


def _rule_based_narrative(signal: Dict) -> str:
    """
    Primary narrative generator — pure rule-based, zero dependencies.
    Uses all available signal metadata for a specific, data-grounded explanation.
    """
    meta = signal.get("signal_meta", signal.get("meta", {})) or {}
    why        = _build_why_sentence(signal, meta)
    invalidate = _build_invalidate_sentence(signal, meta)
    return f"{why} {invalidate}"


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def _cache_key(symbol: str, strategy: str, direction: str, score: float) -> str:
    return f"{symbol}|{strategy}|{direction}|{round(score, 0)}"


def generate_signal_narrative(signal: Dict, use_cache: bool = True) -> str:
    """
    Generate a 2-sentence plain-English explanation for a trade signal.

    Default: rule-based (zero cost, zero API dependency).
    Optional: set LLM_PROVIDER in .env for any LLM (ollama/openai/groq/gemini).

    Args:
        signal:    Signal dict from generate_signal(). Expected keys:
                   symbol, strategy, direction/side, score, regime,
                   stop/stop_loss, target, signal_meta.
        use_cache: Return cached narrative for same signal context.

    Returns:
        str: 2-sentence narrative — WHY valid + WHAT invalidates it.
    """
    symbol    = str(signal.get("symbol", "UNKNOWN"))
    strategy  = str(signal.get("strategy", "unknown"))
    direction = str(signal.get("direction") or signal.get("side", "UNKNOWN"))
    score     = float(signal.get("score", 0))

    key = _cache_key(symbol, strategy, direction, score)
    if use_cache and key in _CACHE:
        entry = _CACHE[key]
        if time.time() - entry["ts"] < _TTL:
            return entry["text"]

    # Try optional LLM first, fall back to rule-based
    text = ""
    if _llm_provider() != "none":
        prompt = _build_llm_prompt(signal)
        text   = _call_llm(prompt)

    if not text:
        text = _rule_based_narrative(signal)

    _CACHE[key] = {"text": text, "ts": time.time()}
    return text


def generate_morning_brief(market_context: Dict) -> str:
    """
    Generate a morning market brief for Telegram / daily video script.
    Pure rule-based by default; LLM-enhanced if LLM_PROVIDER is set.
    """
    vix    = market_context.get("vix", "N/A")
    regime = market_context.get("regime", "UNKNOWN")
    gap    = market_context.get("gap_bias", "AMBIGUOUS")
    fii    = market_context.get("fii_net_cr", 0)
    dii    = market_context.get("dii_net_cr", 0)
    pcr    = market_context.get("pcr", "N/A")
    top3   = market_context.get("top_signals", [])[:3]

    if _llm_provider() != "none":
        signals_str = "\n".join(
            f"  {s.get('symbol','?')} {s.get('direction','?')} via "
            f"{s.get('strategy','?')} (score {s.get('score',0):.1f})"
            for s in top3
        )
        prompt = (
            f"NSE morning brief for {date.today()}.\n"
            f"VIX={vix} | FII=₹{fii}Cr | DII=₹{dii}Cr | PCR={pcr} | "
            f"Regime={regime} | Gap bias={gap}\n"
            f"Top signals:\n{signals_str or '  None yet'}\n\n"
            f"Write a 150-word market brief: (1) today's expected character, "
            f"(2) best strategies for this regime, (3) main risk. "
            f"Plain English. No bullets. No emojis."
        )
        text = _call_llm(prompt)
        if text:
            return text

    # Rule-based morning brief
    regime_desc = {
        "TRENDING":       "trending day expected — momentum strategies preferred",
        "MEAN_REVERTING": "ranging day expected — mean-reversion and VWAP plays favored",
        "HIGH_VOL":       "high-volatility session — reduce position sizing, wider stops",
        "BREAKOUT":       "breakout conditions active — watch ORB and volume expansions",
        "LOW_VOL_CHOP":   "choppy low-volatility session — scalping and theta strategies",
        "NO_TRADE":       "no-trade conditions — sit out until regime clarifies",
    }.get(regime.upper(), f"regime: {regime}")

    gap_note = {
        "EXPECT_FILL":     "Opening gap likely to fill — favour VWAP reversion early.",
        "EXPECT_CONTINUE": "Large gap expected to continue — favour momentum entries.",
        "AMBIGUOUS":       "Gap direction ambiguous — wait for first 15-min confirmation.",
    }.get(gap, "")

    fii_note = ""
    try:
        fii_f = float(fii)
        if fii_f > 1000:    fii_note = f"FII buying ₹{fii_f:.0f}Cr — institutional support."
        elif fii_f < -1000: fii_note = f"FII selling ₹{abs(fii_f):.0f}Cr — institutional headwind."
    except Exception:
        pass

    top_str = ""
    if top3:
        top_str = " Top setups: " + "; ".join(
            f"{s.get('symbol','?')} {s.get('direction','?')} ({s.get('strategy','?')})"
            for s in top3
        ) + "."

    return (
        f"NSE Market Brief — {date.today()}. "
        f"VIX at {vix}, {regime_desc}. "
        f"PCR {pcr}. "
        f"{gap_note} "
        f"{fii_note}"
        f"{top_str} "
        f"System scanning for high-confluence setups."
    ).strip()
