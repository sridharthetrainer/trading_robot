"""
advisory_engine.parser — LAYER 1
================================
Regex/NLP ingestion of unstructured advisory text into AdvisorySignal.

Handles real-world message sloppiness:
  - "Buy MOTHERSON 26 May 130 CE between 126-127 SL 122 TGT 135"
  - "BUY HDFC Life Ins. Company above 640, stoploss 628, targets 655/668"
  - "Sell NIFTY 26500 PE @ 95 to 98 | SL: 110 | Target: 70 (VWAP detachment,
     volume spike, reducing OI)"

Everything is best-effort: missing fields are left None and recorded in
`parse_errors` so downstream layers can abort loudly instead of guessing.
"""

from __future__ import annotations

import logging
import re
from typing import Optional, List, Tuple

from .models import Action, Segment, StrategyTag, AdvisorySignal

logger = logging.getLogger("advisory.parser")

_NUM = r"(\d+(?:[.,]\d+)?)"          # 126 / 126.5 / 1,265.50

# ── Field regexes (case-insensitive, applied to the whole message) ─────────────

_RE_TIMESTAMP = re.compile(
    r"(\d{1,2}[-/]\d{1,2}[-/]\d{2,4}(?:[ ,T]+\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM)?)?"
    r"|\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?"
    r"|\d{1,2}:\d{2}\s*(?:AM|PM))",
    re.IGNORECASE)

_RE_ACTION_BUY = re.compile(r"\b(buy|long|go\s+long|accumulate|bought)\b", re.IGNORECASE)
_RE_ACTION_SELL = re.compile(r"\b(sell|short|go\s+short|exit\s+long|sold)\b", re.IGNORECASE)

# "26 May 130 CE" / "130 CE 26MAY" / "26-May-2026 expiry" / "30 JUN"
# Optional year is strictly 2 or 4 digits so a following strike ("26 Jun 130 CE")
# is never swallowed as a year.
_RE_EXPIRY = re.compile(
    r"\b(\d{1,2}\s*(?:st|nd|rd|th)?[\s-]*"
    r"(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|SEPT|OCT|NOV|DEC)[A-Z]*"
    r"(?:[\s-]*(?:20\d{2}|\d{2})(?!\d))?)\b",
    re.IGNORECASE)

# strike + option type: "130 CE", "26500PE", "strike 640 call", "640 PUT"
_RE_OPTION = re.compile(
    rf"\b{_NUM}\s*(CE|PE|CALL|PUT)S?\b", re.IGNORECASE)

_RE_FUT = re.compile(r"\b(FUT|FUTURE|FUTURES)\b", re.IGNORECASE)

# entry zone: "between 126-127", "@ 126 to 127", "entry 126-127", "above 640",
# "around 126.5", "CMP 126", "@126"
_RE_ENTRY_RANGE = re.compile(
    rf"(?:between|entry(?:\s+zone)?|buy(?:ing)?\s+(?:zone|range)|@|at|around|near|cmp)?"
    rf"[\s:]*{_NUM}\s*(?:-|–|—|to)\s*{_NUM}",
    re.IGNORECASE)
_RE_ENTRY_SINGLE = re.compile(
    rf"(?:entry|@|at|above|around|near|cmp)[\s:]*{_NUM}", re.IGNORECASE)

_RE_SL = re.compile(
    rf"(?:\bsl\b|stop[\s-]*loss|stoploss|\bsls\b)[\s:=@]*{_NUM}", re.IGNORECASE)

# first target only ("targets 655/668" -> 655)
_RE_TARGET = re.compile(
    rf"(?:target[s]?|\btgt\b|\btg\b|\btp\b|book\s+at)[\s:=@]*{_NUM}", re.IGNORECASE)

# ── Strategy-tag routing keywords (per validation matrix spec) ─────────────────

_TAG_KEYWORDS = [
    (StrategyTag.BOLLINGER_MACD,
     [r"middle\s+bollinger\s+band", r"bollinger", r"macd\s+cross", r"macd\s+crossover",
      r"overbought", r"oversold"]),
    (StrategyTag.VWAP_OI,
     [r"detach\w*\s+(?:cleanly\s+)?from\s+(?:the\s+)?vwap", r"vwap",
      r"volume\s+spike", r"reduc\w*\s+open\s+interest", r"\boi\s+unwind",
      r"short[\s-]*cover"]),
    (StrategyTag.TRENDLINE_EMA,
     [r"rising\s+trend\s*line", r"trend\s*line", r"pole\s+and\s+flag", r"flag",
      r"reclaim\w*\s+short[\s-]*term\s+emas?", r"\bema\b", r"base\s+formation",
      r"breakout"]),
]

# Words that must never be mistaken for the instrument name
_NOISE_WORDS = {
    "BUY", "SELL", "LONG", "SHORT", "CE", "PE", "CALL", "PUT", "FUT", "FUTURE",
    "FUTURES", "SL", "TGT", "TARGET", "TARGETS", "TP", "ENTRY", "BETWEEN", "ABOVE",
    "BELOW", "AT", "TO", "CMP", "STOPLOSS", "STOP", "LOSS", "ZONE", "AROUND",
    "NEAR", "BOOK", "EXIT", "ACCUMULATE", "AND", "THE", "FOR", "WITH", "EXPIRY",
}


def _to_float(s: str) -> Optional[float]:
    try:
        return float(s.replace(",", ""))
    except (ValueError, AttributeError):
        return None


def _extract_instrument(text: str) -> Optional[str]:
    """
    Heuristic: the instrument is the run of capitalisable words right after the
    action verb, stopping at the first number / option keyword / field keyword.
    Works for "Buy Samvardhana Motherson Int 130 CE ..." and "SELL HDFCLIFE @640".
    """
    m = re.search(r"\b(?:buy|sell|long|short|accumulate)\b[\s:]*([^\d@|,]+)",
                  text, re.IGNORECASE)
    candidate_zone = m.group(1) if m else text
    words: List[str] = []
    for w in re.split(r"[\s]+", candidate_zone.strip()):
        wu = re.sub(r"[^\w&.\-]", "", w).upper().rstrip(".")
        if not wu or wu in _NOISE_WORDS:
            break
        if re.fullmatch(r"\d+(?:\.\d+)?", wu):
            break
        words.append(wu)
        if len(words) >= 5:          # company names rarely exceed 5 tokens
            break
    name = " ".join(words).strip()
    return name or None


def _classify_segment(text: str) -> Tuple[Segment, Optional[float], Optional[str]]:
    """Returns (segment, strike, expiry)."""
    opt = _RE_OPTION.search(text)
    expiry_m = _RE_EXPIRY.search(text)
    expiry = expiry_m.group(1).strip() if expiry_m else None
    if opt:
        strike = _to_float(opt.group(1))
        kind = opt.group(2).upper()
        seg = Segment.DERIVATIVE_CALL if kind in ("CE", "CALL") else Segment.DERIVATIVE_PUT
        return seg, strike, expiry
    return Segment.EQUITY, None, (expiry if _RE_FUT.search(text) else None)


def _route_strategy_tag(text: str) -> StrategyTag:
    low = text.lower()
    best, best_hits = StrategyTag.UNKNOWN, 0
    for tag, patterns in _TAG_KEYWORDS:
        hits = sum(1 for p in patterns if re.search(p, low))
        if hits > best_hits:
            best, best_hits = tag, hits
    return best


def _extract_entry(text: str, strike: Optional[float],
                   stop_loss: Optional[float],
                   target: Optional[float]) -> Tuple[Optional[float], Optional[float]]:
    """
    Entry range first; fall back to a single entry level (min == max).
    Avoid latching onto strike / SL / target numbers when scanning singles.
    """
    taken = {v for v in (strike, stop_loss, target) if v is not None}
    m = _RE_ENTRY_RANGE.search(text)
    if m:
        a, b = _to_float(m.group(1)), _to_float(m.group(2))
        if a is not None and b is not None and {a, b}.isdisjoint(taken):
            return min(a, b), max(a, b)
    for m in _RE_ENTRY_SINGLE.finditer(text):
        v = _to_float(m.group(1))
        if v is not None and v not in taken:
            return v, v
    return None, None


class AdvisoryParser:
    """LAYER 1 — turns one raw advisory message into an AdvisorySignal."""

    def parse(self, text: str) -> AdvisorySignal:
        sig = AdvisorySignal(raw_text=text)
        logger.info("L1 ▶ parsing message: %r", text[:160])

        ts = _RE_TIMESTAMP.search(text)
        sig.timestamp = ts.group(1) if ts else None
        # blank out the timestamp so date fragments ("10-06-2026") can never be
        # mistaken for an entry range by the numeric regexes below
        scan_text = (text[:ts.start()] + " " * (ts.end() - ts.start())
                     + text[ts.end():]) if ts else text

        if _RE_ACTION_BUY.search(scan_text):
            sig.action = Action.BUY
        elif _RE_ACTION_SELL.search(scan_text):
            sig.action = Action.SELL
        else:
            sig.parse_errors.append("action: no BUY/SELL keyword found")

        sig.segment, sig.strike, sig.expiry = _classify_segment(scan_text)
        if sig.expiry:
            sig.expiry = sig.expiry.strip()

        sl = _RE_SL.search(scan_text)
        sig.stop_loss = _to_float(sl.group(1)) if sl else None
        if sig.stop_loss is None:
            sig.parse_errors.append("stop_loss: not found")

        tgt = _RE_TARGET.search(scan_text)
        sig.target = _to_float(tgt.group(1)) if tgt else None
        if sig.target is None:
            sig.parse_errors.append("target: not found")

        sig.entry_min, sig.entry_max = _extract_entry(
            scan_text, sig.strike, sig.stop_loss, sig.target)
        if sig.entry_min is None:
            sig.parse_errors.append("entry: no range or level found")

        sig.tradable_instrument = _extract_instrument(scan_text)
        if not sig.tradable_instrument:
            sig.parse_errors.append("instrument: could not isolate name")

        sig.strategy_tag = _route_strategy_tag(text)
        if sig.strategy_tag is StrategyTag.UNKNOWN:
            sig.parse_errors.append("strategy_tag: no routing keywords matched")

        # sanity: SL must sit on the loss side of entry
        if (sig.action and sig.entry_min is not None and sig.stop_loss is not None):
            if sig.action is Action.BUY and sig.stop_loss >= sig.entry_max:
                sig.parse_errors.append(
                    f"sanity: BUY but SL {sig.stop_loss} >= entry_max {sig.entry_max}")
            if sig.action is Action.SELL and sig.stop_loss <= sig.entry_min:
                sig.parse_errors.append(
                    f"sanity: SELL but SL {sig.stop_loss} <= entry_min {sig.entry_min}")

        logger.info("L1 ◀ parsed: action=%s instrument=%r segment=%s strike=%s "
                    "entry=%s-%s sl=%s tgt=%s tag=%s errors=%s",
                    sig.action, sig.tradable_instrument, sig.segment.value,
                    sig.strike, sig.entry_min, sig.entry_max, sig.stop_loss,
                    sig.target, sig.strategy_tag.value, sig.parse_errors or "none")
        return sig
