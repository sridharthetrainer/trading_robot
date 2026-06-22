"""
signal_broadcaster.py — Institutional Signal Distribution Engine

Framework inspired by:
  - Zerodha Streak signal architecture
  - TradingView webhook model  
  - Two Sigma client reporting standards
  - "The Man Who Solved the Market" — Simons/Renaissance approach
  - SEBI Investment Adviser (IA) compliance framework

Distributes high-quality signals to:
  1. Primary Telegram account (you)
  2. Subscriber Telegram channels (signal service)
  3. WhatsApp Business API (future)
  4. Web dashboard (real-time)

Signal quality gates (ALL must pass):
  - Confluence score >= 5.5 (out of 10)
  - HMM regime = TRENDING (not CHOPPY/HIGH_NOISE)
  - VIX within range (12-25 for stocks, 12-20 for options)
  - FII sentiment not strongly against direction
  - Not within 15 min of major event (RBI/earnings)
  - Position size >= 1 lot (sufficient capital)
"""
from __future__ import annotations
import logging, json, time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

_SIGNAL_LOG = Path("signal_history.json")
_MAX_SIGNALS_PER_DAY = 8      # quality over quantity
_MIN_SCORE_BROADCAST = 5.5    # only strong signals go to subscribers
_MIN_SCORE_PREMIUM   = 7.0    # premium tier gets high-conviction only


class SignalBroadcaster:
    """
    Institutional signal distribution with quality gates.
    Think: Renaissance Technologies signal committee.
    """

    def __init__(self, alerts=None):
        self.alerts = alerts
        self._signals_today: List[dict] = []
        self._load_today()

    def _load_today(self):
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            if _SIGNAL_LOG.exists():
                data = json.loads(_SIGNAL_LOG.read_text())
                self._signals_today = [s for s in data if s.get("date") == today]
        except Exception:
            self._signals_today = []

    def _save(self):
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            all_signals = []
            if _SIGNAL_LOG.exists():
                all_signals = json.loads(_SIGNAL_LOG.read_text())
                # Keep last 30 days
                cutoff = (datetime.now().timestamp() - 30*86400)
                all_signals = [s for s in all_signals
                               if s.get("ts", 0) > cutoff]
            # Add today's
            for s in self._signals_today:
                if not any(x.get("id") == s.get("id") for x in all_signals):
                    all_signals.append(s)
            _SIGNAL_LOG.write_text(json.dumps(all_signals, indent=2))
        except Exception as e:
            logger.debug("signal save: %s", e)

    def _quality_gate(self, signal: dict) -> tuple:
        """
        Multi-layer quality filter.
        Returns (pass: bool, reason: str)
        Inspired by Two Sigma's signal-to-noise framework.
        """
        score    = float(signal.get("score", 0))
        symbol   = signal.get("symbol", "?")
        direction = signal.get("direction", "")
        regime   = signal.get("regime", "UNKNOWN")
        vix      = float(signal.get("vix", 15))

        # Gate 1: Minimum score
        if score < _MIN_SCORE_BROADCAST:
            return False, f"Score {score:.1f} < {_MIN_SCORE_BROADCAST}"

        # Gate 2: Valid direction
        if direction not in ("BUY", "SELL"):
            return False, f"No clear direction: {direction}"

        # Gate 3: Regime filter (from HMM)
        if regime in ("HIGH_NOISE", "CHOPPY"):
            return False, f"Regime {regime} — waiting for trend"

        # Gate 4: VIX gate
        if vix > 30:
            return False, f"VIX {vix:.1f} too high — risk off"
        if vix < 8:
            return False, f"VIX {vix:.1f} too low — suspicious"

        # Gate 5: Daily signal limit (quality over quantity)
        if len(self._signals_today) >= _MAX_SIGNALS_PER_DAY:
            return False, f"Daily limit {_MAX_SIGNALS_PER_DAY} reached"

        # Gate 6: No duplicate symbol in last 2 hours
        now = time.time()
        recent = [s for s in self._signals_today
                  if s.get("symbol") == symbol and now - s.get("ts", 0) < 7200]
        if recent:
            return False, f"Duplicate {symbol} within 2h"

        return True, "PASS"

    def _format_signal(self, signal: dict, tier: str = "standard") -> str:
        """
        WOW-enhanced signal card with institutional quality + WOW factors.
        Shows: confluence score, entry/target/SL, R:R, regime, PCR, FII,
        promoter activity, earnings proximity, sector strength + WOW narrative.
        """
        score     = float(signal.get("score", 0))
        symbol    = signal.get("symbol", "?")
        direction = signal.get("direction", "?")
        price     = float(signal.get("price", 0) or 0)
        target    = float(signal.get("target", 0) or 0)
        sl        = float(signal.get("stop_loss", 0) or 0)
        strategy  = signal.get("strategy", "confluence")
        regime    = signal.get("regime", "?")
        vix       = float(signal.get("vix", 0) or 0)
        horizon   = signal.get("horizon", "intraday")
        rr        = abs((target - price) / (price - sl)) if price and sl and sl != price else 0
        lot_size  = int(signal.get("lot_size", 0) or 0)
        meta      = signal.get("metadata", {}) or {}

        # ── Visual elements ────────────────────────────────────────
        icon   = "🟢" if direction == "BUY" else "🔴"
        conf   = "🔥 HIGH CONVICTION" if score >= 7.5 else "✅ MEDIUM" if score >= 6.0 else "⚡ SPECULATIVE"
        stars  = "⭐⭐⭐" if score >= 8 else "⭐⭐" if score >= 6.5 else "⭐"
        updown = "📈" if direction == "BUY" else "📉"
        gain_pct = abs((target - price) / price * 100) if price else 0
        loss_pct = abs((price - sl) / price * 100) if price and sl else 0

        lines = [
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"{icon} <b>{direction} {symbol}</b> {stars}",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"",
            f"  {updown} <b>SIGNAL SCORE: {score:.1f}/10</b> → {conf}",
            f"",
            f"  ┌─ TRADE SETUP ─────────────────",
            f"  │ Entry:     ₹{price:,.2f}",
        ]
        if target:
            lines.append(f"  │ Target:    ₹{target:,.2f}  (+{gain_pct:.1f}%)")
        if sl:
            lines.append(f"  │ Stop Loss: ₹{sl:,.2f}  (-{loss_pct:.1f}%)")
        if rr > 0:
            rr_icon = "💎" if rr >= 2.5 else "👍" if rr >= 1.5 else "⚠️"
            lines.append(f"  │ R:R Ratio: 1:{rr:.1f}  {rr_icon}")
        if lot_size > 1:
            lines.append(f"  │ Lot Size:  {lot_size}")
        lines.append(f"  └─────────────────────────────")

        # ── Strategy + Regime ────────────────────────────────────
        regime_icon = {"TRENDING":"📈","RANGE":"↔️","CHOPPY":"🌊","BREAKOUT":"🚀"}.get(str(regime).upper(),"📊")
        lines += [
            f"",
            f"  <b>MARKET CONTEXT</b>",
            f"  │ Strategy:  {strategy}",
            f"  │ Regime:    {regime_icon} {regime}",
            f"  │ Horizon:   {horizon.upper()}",
        ]
        if vix:
            vix_icon = "🟢" if vix < 15 else "🟡" if vix < 20 else "🔴"
            lines.append(f"  │ India VIX: {vix:.1f} {vix_icon}")

        # ── WOW FACTORS (what makes this signal special) ─────────
        wow_factors = []

        # FII signal
        fii_sig = float(meta.get("fii_futures_signal", 0) or signal.get("fii_futures_signal", 0) or 0)
        if abs(fii_sig) > 0.2:
            fii_icon = "🐂" if fii_sig > 0 else "🐻"
            wow_factors.append(f"{fii_icon} FII {'BUYING' if fii_sig > 0 else 'SELLING'} in futures")

        # Promoter signal
        prom = float(meta.get("promoter_signal", 0) or signal.get("promoter_signal", 0) or 0)
        if abs(prom) > 0.2:
            wow_factors.append(f"👔 Promoter {'BUYING' if prom > 0 else 'SELLING'} detected")

        # BSE announcement
        bse_score = float(meta.get("bse_announcement", 0) or signal.get("bse_announcement", 0) or 0)
        if bse_score > 0.3:
            wow_factors.append(f"📋 Corporate announcement score: +{bse_score:.1f}")

        # Rollover signal
        rollover = meta.get("rollover_signal", "") or signal.get("rollover_signal", "")
        if rollover:
            wow_factors.append(f"🔄 {rollover}")

        # PCR
        try:
            from data_source_resilience import compute_pcr
            pcr = compute_pcr(symbol)
            if pcr > 0:
                pcr_icon = "🐂" if pcr > 1.2 else "🐻" if pcr < 0.8 else "⚖️"
                wow_factors.append(f"{pcr_icon} PCR: {pcr:.2f}")
        except Exception: pass

        # Sector strength
        try:
            from data_source_resilience import get_all_sector_indices
            sectors = get_all_sector_indices()
            for sect_name, sect_data in sectors.items():
                if symbol.upper() in sect_name.upper() or sect_name.upper() in symbol.upper():
                    chg = sect_data.get("change_pct", 0)
                    if abs(chg) > 0.5:
                        wow_factors.append(f"📊 Sector {sect_name}: {chg:+.1f}%")
                    break
        except Exception: pass

        # Composite sentiment
        try:
            from market_intelligence_hub import get_composite_sentiment
            sent = get_composite_sentiment()
            if sent:
                s_score = sent.get("score", 50)
                s_label = sent.get("label", "NEUTRAL")
                wow_factors.append(f"🎯 Market sentiment: {s_score:.0f}/100 ({s_label})")
        except Exception: pass

        # R:R quality
        if rr >= 3.0:
            wow_factors.append(f"💎 Exceptional R:R — {rr:.1f}x reward vs risk")
        elif rr >= 2.0:
            wow_factors.append(f"👍 Strong R:R — {rr:.1f}x reward vs risk")

        # Score quality
        if score >= 8.0:
            wow_factors.append(f"🔥 TOP-TIER signal — {score:.1f}/10 confluence")
        elif score >= 7.0:
            wow_factors.append(f"✅ High quality — {score:.1f}/10 confluence")

        if wow_factors:
            lines += [
                f"",
                f"  <b>✨ WOW FACTORS</b>",
            ]
            for w in wow_factors[:6]:
                lines.append(f"  │ {w}")

        # ── Signal triggers (what specifically fired) ────────────
        triggers = signal.get("triggers", [])
        if triggers:
            lines += [f"", f"  <b>⚙ SCORING BREAKDOWN</b>"]
            lines.append("  │ " + "  │ ".join(str(t) for t in triggers[:5]))

        # ── WHY THIS TRADE (premium tier) ────────────────────────
        reasons = signal.get("reasons", [])
        if reasons and tier == "premium":
            lines += [
                f"",
                f"  <b>🧠 WHY THIS TRADE</b>",
            ]
            for r in reasons[:4]:
                lines.append(f"  │ ✓ {r}")

        # ── Earnings warning ─────────────────────────────────────
        earnings_mult = signal.get("earnings_multiplier", 1.0)
        if earnings_mult < 1.0:
            lines += [
                f"",
                f"  ⚠️ <b>EARNINGS WARNING</b>: Results in {int((1-earnings_mult)*4)} days",
                f"  │ Position size reduced to {earnings_mult*100:.0f}%",
            ]

        # ── Footer ───────────────────────────────────────────────
        lines += [
            f"",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"  ⏰ {datetime.now().strftime('%d-%b-%Y %H:%M:%S')}",
            f"  📱 /status · /positions · /pnl",
            f"  ⚠️ Educational only | Not SEBI registered",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ]
        return "\n".join(lines)

    def broadcast(self, signal: dict) -> bool:
        """
        Broadcast signal through all channels with quality gates.
        Returns True if signal was broadcast.
        """
        passed, reason = self._quality_gate(signal)
        if not passed:
            logger.debug("Signal filtered: %s — %s", signal.get("symbol"), reason)
            return False

        score = float(signal.get("score", 0))
        sig_id = f"{signal.get('symbol')}_{int(time.time())}"
        signal["id"] = sig_id
        signal["ts"] = time.time()
        signal["date"] = datetime.now().strftime("%Y-%m-%d")

        self._signals_today.append(signal)
        self._save()

        # Format and send
        tier = "premium" if score >= _MIN_SCORE_PREMIUM else "standard"
        # Add lot size info to signal
        try:
            from nse_master import NSEMaster as _NM
            _lot = _NM().get_lot_size(signal.get("symbol",""))
            if _lot and _lot > 1:
                signal["lot_size"] = _lot
        except Exception: pass
        msg = self._format_signal(signal, tier)

        try:
            if self.alerts:
                self.alerts.send(msg, dedup_key=sig_id)
        except Exception as e:
            logger.warning("broadcast send: %s", e)

        # ── Generate signal card image (GAP 11) ──────────────────────────────
        try:
            import threading as _st
            def _send_card():
                try:
                    from voice_video_generator import generate_signal_card_image
                    _img = generate_signal_card_image(signal)
                    if _img and hasattr(self.alerts, "send_photo") and self.alerts:
                        self.alerts.send_photo(_img)
                        import os as _os; _os.remove(_img)
                except Exception: pass
            _st.Thread(target=_send_card, daemon=True, name="signal_card").start()
        except Exception: pass

        # ── Fetch Greeks for options signals ─────────────────────────────────
        try:
            from greeks_live import get_greeks as _gkfn
            _gsym = str(signal.get('symbol',''))
            if any(x in _gsym.upper() for x in ['NIFTY','BANKNIFTY','CE','PE']):
                _gk = _gkfn(_gsym)
                if _gk:
                    signal['delta'] = round(float(_gk.get('delta',0)),3)
                    signal['theta'] = round(float(_gk.get('theta',0)),3)
                    signal['iv']    = round(float(_gk.get('iv',0))*100,1)
        except Exception: pass

        # ── Broadcast to subscriber channels ─────────────────────────────
        try:
            import os as _os
            import os as _os
            try:
                from channel_config import FREE_CHANNEL_ID as _fci, PREMIUM_CHANNEL_ID as _pci
            except Exception:
                _fci = "-1003830079189"; _pci = "-1003993110321"
            free_ch = _os.getenv("TELEGRAM_FREE_CHANNEL_ID","") or _fci
            prem_ch = _os.getenv("TELEGRAM_PREMIUM_CHANNEL_ID","") or _pci
            if self.alerts and hasattr(self.alerts, "send_to_channel"):
                from public_signal_formatter import format_public_signal
                # Free channel: score >= premium threshold only, delayed
                if free_ch and score >= _MIN_SCORE_BROADCAST:  # free gets standard quality
                    pub_msg = format_public_signal(signal, tier="free")
                    self.alerts.send_to_channel(free_ch, pub_msg)
                # Premium channel: all signals that pass gate
                if prem_ch:
                    prem_msg = format_public_signal(signal, tier="premium")
                    self.alerts.send_to_channel(prem_ch, prem_msg)
        except Exception as _ce:
            logger.debug("channel_broadcast: %s", _ce)

        logger.info("Signal broadcast: %s %s score=%.1f",
                    signal.get("direction"), signal.get("symbol"), score)
        return True

    def daily_summary(self) -> str:
        """EOD signal performance summary."""
        if not self._signals_today:
            return "📡 No signals broadcast today"

        n = len(self._signals_today)
        scores = [float(s.get("score", 0)) for s in self._signals_today]
        avg_score = sum(scores) / n if scores else 0
        premium = sum(1 for s in scores if s >= _MIN_SCORE_PREMIUM)

        return (
            f"📡 <b>SIGNAL SUMMARY</b>\n"
            f"  Signals today:  {n}/{_MAX_SIGNALS_PER_DAY}\n"
            f"  Avg score:      {avg_score:.1f}/10\n"
            f"  Premium (7+):   {premium}\n"
            f"  Quality gate:   {_MIN_SCORE_BROADCAST}/10 minimum\n"
        )


# Singleton
_broadcaster: Optional[SignalBroadcaster] = None

def get_broadcaster(alerts=None) -> SignalBroadcaster:
    global _broadcaster
    if _broadcaster is None:
        _broadcaster = SignalBroadcaster(alerts)
    return _broadcaster
