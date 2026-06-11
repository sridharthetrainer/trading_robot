"""
trading_agent.py

LLM-powered trading agent layer using Claude API.

Converts the rule-based system into a true reasoning agent:

  LAYER 1 — Perception:  build_market_context()
  LAYER 2 — Reasoning:   should_trade_today() + approve_signal()
  LAYER 3 — Execution:   existing trade_manager.py (unchanged)
  LAYER 4 — Reflection:  reflect_on_trades()

TELEGRAM COMMANDS (two-way):
  /why          — why did bot skip signals today?
  /status       — current positions + market view
  /avoid SYMBOL — block a symbol until next day
  /pause        — pause all trading (manual override)
  /resume       — resume after pause
  /explain TRADE_ID — explain reasoning for a specific trade

COST ESTIMATE:
  ~15 API calls/day (morning context + per-signal approval)
  ~2,000 tokens/call average
  ~30,000 tokens/day = ~$0.09/day at Claude Haiku pricing
  Monthly: ~$2.70 — negligible
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# narrative_engine.py handles all LLM calls (provider-agnostic via LLM_PROVIDER env var).
# TradingAgent below is kept for backward-compat; new narrative calls go via narrative_engine.
CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"   # kept for backward compat only
CLAUDE_MODEL   = "claude-haiku-4-5-20251001"
CLAUDE_KEY_ENV = "ANTHROPIC_API_KEY"                        # not required if LLM_PROVIDER != anthropic

# Agent state file — persists decisions across bot cycles
AGENT_STATE_FILE = "agent_state.json"


class TradingAgent:
    """
    LLM reasoning layer that sits between signal_engine and trade_manager.

    Usage:
        agent = TradingAgent()

        # Morning — build context and decide day strategy
        day_view = agent.morning_briefing(market_data)

        # Per signal — agent approves or rejects
        approved, reason = agent.approve_signal(signal, market_context)

        # Evening — reflect on today's trades
        agent.evening_reflection(closed_trades)
    """

    def __init__(self, alerts=None) -> None:
        self._alerts  = alerts
        self._api_key = os.getenv(CLAUDE_KEY_ENV, "")
        self._state   = self._load_state()
        self._paused  = self._state.get("paused", False)
        self._blocked = set(self._state.get("blocked_symbols", []))
        self._day_view: str = ""
        self._last_briefing: str = ""

    # ── Public API ────────────────────────────────────────────────────────────

    def is_available(self) -> bool:
        """True if Claude API key is configured."""
        return bool(self._api_key)

    def is_paused(self) -> bool:
        return self._paused

    def is_blocked(self, symbol: str) -> bool:
        return symbol.upper() in self._blocked

    def pause(self, reason: str = "manual") -> None:
        self._paused = True
        self._save_state()
        logger.info("Agent: trading PAUSED — %s", reason)

    def resume(self) -> None:
        self._paused = False
        self._save_state()
        logger.info("Agent: trading RESUMED")

    def block_symbol(self, symbol: str, until: Optional[str] = None) -> None:
        self._blocked.add(symbol.upper())
        self._save_state()
        logger.info("Agent: %s blocked until %s", symbol, until or "next day")

    def unblock_symbol(self, symbol: str) -> None:
        self._blocked.discard(symbol.upper())
        self._save_state()

    # ── Layer 1: Morning briefing ─────────────────────────────────────────────

    def morning_briefing(self, market_data: dict) -> str:
        """
        Called at 9:00 AM. Builds day view from all available context.
        Returns a natural language summary of today's market conditions.
        """
        if not self.is_available():
            return ""

        today = str(date.today())
        if self._last_briefing == today:
            return self._day_view   # already done today

        prompt = self._build_briefing_prompt(market_data)
        response = self._call_claude(
            system=(
                "You are a concise NSE market analyst. Analyse the provided data "
                "and give a SHORT (4-6 line) trading day outlook. Focus on: "
                "1) Overall market bias (bullish/bearish/sideways), "
                "2) Key risk factors today, "
                "3) Best strategy types for today, "
                "4) Symbols to favour or avoid. "
                "Be direct. No disclaimers. Output plain text only."
            ),
            user=prompt,
            max_tokens=300,
        )

        self._day_view      = response
        self._last_briefing = today
        self._save_state()

        logger.info("Agent morning briefing: %s", response[:100])

        if self._alerts and response:
            self._alerts.send(
                f"🤖 <b>AGENT MORNING BRIEFING</b>\n{response}",
                dedup_key=f"agent_briefing_{today}"
            )
        return response

    # ── Layer 2: Signal approval ──────────────────────────────────────────────

    def approve_signal(
        self,
        signal:         dict,
        market_context: dict,
        recent_trades:  Optional[List[dict]] = None,
    ) -> tuple[bool, str]:
        """
        Agent reviews a signal before execution.

        Returns (approved: bool, reason: str)

        Fast path: if confidence is obvious (very high score + aligned context),
        approve immediately without API call to save cost.
        """
        symbol    = signal.get("symbol", "")
        direction = signal.get("direction", "")
        score     = float(signal.get("score", 0))
        strategy  = signal.get("strategy", "")

        # Cheap checks first (no API call)
        if self._paused:
            return False, "agent_paused_manual_override"
        if symbol and self.is_blocked(symbol):
            return False, f"symbol_{symbol}_blocked_by_agent"

        # Fast approve: very high score + no red flags = no API call needed
        vix        = float(market_context.get("vix", 15))
        has_event  = market_context.get("event_today", False)
        gift_bias  = market_context.get("gift_nifty_bias", "NEUTRAL")

        if not self.is_available():
            return True, "agent_not_configured_allow_all"

        # Fast approve conditions (skip API to save cost)
        if score >= 8.5 and not has_event and vix < 17:
            return True, f"fast_approve_score={score:.1f}_low_risk"

        # Fast reject conditions
        if has_event and score < 7.0:
            return False, "event_day_risk_score_too_low"
        if vix > 22 and direction == "BUY":
            return False, "vix_too_high_for_buying"
        if gift_bias == "BEARISH" and direction == "BUY" and score < 7.5:
            return False, "gift_nifty_bearish_score_insufficient"

        # Medium confidence — ask Claude
        prompt = self._build_approval_prompt(signal, market_context, recent_trades)
        response = self._call_claude(
            system=(
                "You are a risk manager reviewing an NSE options trade signal. "
                "Respond with EXACTLY one line: "
                "APPROVE: <brief reason> "
                "or "
                "REJECT: <brief reason> "
                "Consider: signal score, market conditions, VIX, event risk, strategy type. "
                "Be decisive. No hedging."
            ),
            user=prompt,
            max_tokens=60,
        )

        if response.upper().startswith("APPROVE"):
            reason = response.split(":", 1)[-1].strip() if ":" in response else "agent_approved"
            return True, f"agent_approved_{reason[:50]}"
        else:
            reason = response.split(":", 1)[-1].strip() if ":" in response else "agent_rejected"
            return False, f"agent_rejected_{reason[:50]}"

    # ── Layer 4: Evening reflection ───────────────────────────────────────────

    def evening_reflection(self, closed_trades: List[dict]) -> str:
        """
        Called at 4 PM after market close.
        Analyses today's trades and extracts lessons.
        """
        if not self.is_available() or not closed_trades:
            return ""

        today = str(date.today())
        wins   = [t for t in closed_trades if t.get("net_pnl", 0) > 0]
        losses = [t for t in closed_trades if t.get("net_pnl", 0) <= 0]

        prompt = (
            f"Today's trades ({len(closed_trades)} total, "
            f"{len(wins)} wins, {len(losses)} losses):\n\n"
        )
        for t in closed_trades[:10]:   # limit to 10 trades
            prompt += (
                f"  {t.get('strategy','?')} {t.get('side','?')} {t.get('symbol','?')} "
                f"entry={t.get('entry_price',0):.0f} exit={t.get('exit_price',0):.0f} "
                f"P&L=₹{t.get('net_pnl',0):+.0f} reason={t.get('exit_reason','?')}\n"
            )
        prompt += f"\nDay view used: {self._day_view or 'not available'}\n"
        prompt += "What patterns do you see? What should we do differently tomorrow?"

        response = self._call_claude(
            system=(
                "You are a trading coach reviewing today's NSE options trades. "
                "Give 3-4 specific, actionable observations in plain text. "
                "Focus on patterns in what worked and what failed. Be direct."
            ),
            user=prompt,
            max_tokens=250,
        )

        logger.info("Agent evening reflection: %s", response[:100])
        if self._alerts and response:
            self._alerts.send(
                f"🤖 <b>AGENT DAILY REFLECTION</b>\n{response}",
                dedup_key=f"agent_reflection_{today}"
            )
        return response

    # ── Telegram command handler ──────────────────────────────────────────────

    def handle_command(self, command: str, args: str = "") -> str:
        """
        Handle natural language commands from Telegram.
        Returns response string to send back.
        """
        cmd = command.strip().lower().lstrip("/")

        if cmd == "why":
            return self._explain_todays_decisions()

        elif cmd == "status":
            return self._get_status()

        elif cmd == "avoid" and args:
            sym = args.strip().upper()
            self.block_symbol(sym)
            return f"✅ {sym} blocked for today. Will unblock tomorrow morning."

        elif cmd == "pause":
            self.pause("telegram_command")
            return "⏸ Trading PAUSED. Send /resume to restart."

        elif cmd == "resume":
            self.resume()
            return "▶️ Trading RESUMED."

        elif cmd == "explain" and args:
            return self._explain_trade(args.strip())

        elif cmd in ("help", "commands"):
            return (
                "🤖 <b>AGENT COMMANDS</b>\n"
                "/why — why skip signals today?\n"
                "/status — positions + market view\n"
                "/avoid NIFTY — block a symbol today\n"
                "/pause — stop all trading\n"
                "/resume — restart trading\n"
                "/explain TRADE_ID — explain a trade"
            )

        return f"Unknown command: {command}. Send /help for list."

    # ── Claude API call ───────────────────────────────────────────────────────

    def _call_claude(
        self,
        system:     str,
        user:       str,
        max_tokens: int = 200,
    ) -> str:
        """Call Claude API. Returns response text or empty string on error."""
        if not self._api_key:
            return ""
        try:
            import urllib.request
            payload = json.dumps({
                "model":      CLAUDE_MODEL,
                "max_tokens": max_tokens,
                "system":     system,
                "messages":   [{"role": "user", "content": user}],
            }).encode()
            req = urllib.request.Request(
                CLAUDE_API_URL,
                data=payload,
                headers={
                    "Content-Type":            "application/json",
                    "x-api-key":               self._api_key,
                    "anthropic-version":       "2023-06-01",
                },
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read())
                return data["content"][0]["text"].strip()
        except Exception as e:
            logger.debug("Claude API error: %s", e)
            return ""

    # ── Prompt builders ───────────────────────────────────────────────────────

    def _build_briefing_prompt(self, mkt: dict) -> str:
        vix        = mkt.get("vix", "N/A")
        gift       = mkt.get("gift_nifty_change_pct", "N/A")
        fii        = mkt.get("fii_net_cr", 0)
        dii        = mkt.get("dii_net_cr", 0)
        pcr        = mkt.get("pcr", "N/A")
        events     = mkt.get("events_today", [])
        nifty_prev = mkt.get("nifty_prev_close", "N/A")
        cpr_type   = mkt.get("cpr_day_type", "NORMAL")
        news       = mkt.get("top_headlines", [])

        return (
            f"NSE Market Data — {date.today()}\n\n"
            f"NIFTY prev close: {nifty_prev}\n"
            f"GIFT Nifty change: {gift}%\n"
            f"India VIX: {vix}\n"
            f"FII net: ₹{fii:,.0f}Cr | DII net: ₹{dii:,.0f}Cr\n"
            f"PCR: {pcr}\n"
            f"CPR day type: {cpr_type}\n"
            f"Events today: {', '.join(events) if events else 'None'}\n"
            f"Top headlines: {'; '.join(news[:3]) if news else 'None'}\n\n"
            f"Give a concise trading day outlook."
        )

    def _build_approval_prompt(
        self, sig: dict, mkt: dict, recent: Optional[List[dict]]
    ) -> str:
        recent_str = ""
        if recent:
            last3 = recent[-3:]
            recent_str = "Recent trades: " + ", ".join(
                f"{t.get('strategy','?')} ₹{t.get('net_pnl',0):+.0f}"
                for t in last3
            )

        return (
            f"Signal: {sig.get('strategy','?')} {sig.get('direction','?')} "
            f"{sig.get('symbol','NIFTY')} score={sig.get('score',0):.1f}\n"
            f"Reason: {sig.get('reason','')[:80]}\n\n"
            f"Market: VIX={mkt.get('vix','?')} "
            f"GIFT={mkt.get('gift_nifty_change_pct','?')}% "
            f"Events={mkt.get('events_today',[])} "
            f"CPR={mkt.get('cpr_day_type','NORMAL')}\n"
            f"Day view: {self._day_view[:100] if self._day_view else 'not available'}\n"
            f"{recent_str}\n\n"
            f"Should we take this trade? APPROVE or REJECT with brief reason."
        )

    # ── Helper commands ───────────────────────────────────────────────────────

    def _explain_todays_decisions(self) -> str:
        if not self.is_available():
            return "Agent not configured (no ANTHROPIC_API_KEY)"
        state = self._load_state()
        decisions = state.get("todays_decisions", [])
        if not decisions:
            return "No agent decisions recorded today yet."
        summary = "\n".join(
            f"  {d['time']} {d['signal']} → {d['decision']}: {d['reason']}"
            for d in decisions[-10:]
        )
        return f"🤖 <b>TODAY'S AGENT DECISIONS</b>\n{summary}"

    def _get_status(self) -> str:
        lines = [
            "🤖 <b>AGENT STATUS</b>",
            f"Mode: {'⏸ PAUSED' if self._paused else '▶️ ACTIVE'}",
            f"Blocked symbols: {', '.join(self._blocked) or 'none'}",
            f"Day view: {self._day_view[:100] if self._day_view else 'not set'}",
        ]
        return "\n".join(lines)

    def _explain_trade(self, trade_id: str) -> str:
        try:
            import sqlite3, config as cfg
            conn = sqlite3.connect(getattr(cfg, "TRADES_DB", "trades.db"))
            row  = conn.execute(
                "SELECT * FROM trades WHERE trade_id=?", (trade_id,)
            ).fetchone()
            conn.close()
            if not row:
                return f"Trade {trade_id} not found."
            return f"Trade {trade_id}: {row}"
        except Exception as e:
            return f"Cannot retrieve trade: {e}"

    # ── State persistence ─────────────────────────────────────────────────────

    def _load_state(self) -> dict:
        try:
            p = Path(AGENT_STATE_FILE)
            if p.exists():
                return json.loads(p.read_text())
        except Exception:
            pass
        return {}

    def _save_state(self) -> None:
        try:
            state = self._load_state()
            state.update({
                "paused":          self._paused,
                "blocked_symbols": list(self._blocked),
                "day_view":        self._day_view,
                "last_briefing":   self._last_briefing,
            })
            Path(AGENT_STATE_FILE).write_text(json.dumps(state, indent=2))
        except Exception:
            pass

    def record_decision(
        self, signal: dict, approved: bool, reason: str
    ) -> None:
        """Record an approval decision for /why command."""
        try:
            state = self._load_state()
            decisions = state.get("todays_decisions", [])
            today = str(date.today())
            # Clear old decisions
            decisions = [d for d in decisions if d.get("date") == today]
            decisions.append({
                "date":     today,
                "time":     datetime.now().strftime("%H:%M"),
                "signal":   f"{signal.get('strategy','?')} {signal.get('direction','?')} score={signal.get('score',0):.1f}",
                "decision": "APPROVED" if approved else "REJECTED",
                "reason":   reason[:80],
            })
            state["todays_decisions"] = decisions[-50:]  # keep last 50
            Path(AGENT_STATE_FILE).write_text(json.dumps(state, indent=2))
        except Exception:
            pass


# ── Singleton ─────────────────────────────────────────────────────────────────
_agent: Optional[TradingAgent] = None

def get_agent(alerts=None) -> TradingAgent:
    global _agent
    if _agent is None:
        _agent = TradingAgent(alerts)
    if alerts and not _agent._alerts:
        _agent._alerts = alerts
    return _agent
