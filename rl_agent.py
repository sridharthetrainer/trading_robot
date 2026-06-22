"""
rl_agent.py — Reinforcement Learning Agent for Signal Scoring

Uses Q-learning to adjust signal scores based on trade outcomes.
State: (regime, vix_level, hour, strategy_name)
Action: score_multiplier (0.5x, 0.75x, 1.0x, 1.25x, 1.5x)
Reward: trade PnL normalised by risk
"""
from __future__ import annotations
import json, logging, random
from pathlib import Path
from typing import Dict

logger = logging.getLogger(__name__)

_Q_FILE   = Path("rl_qtable.json")
_ALPHA    = 0.1   # learning rate
_GAMMA    = 0.9   # discount factor
_EPSILON  = 0.1   # exploration rate
_ACTIONS  = [0.5, 0.75, 1.0, 1.25, 1.5]

class RLAgent:
    """Simple Q-learning agent for signal score adjustment."""

    def __init__(self):
        self._q: Dict[str, Dict[str, float]] = {}
        self._load()

    def _load(self):
        try:
            if _Q_FILE.exists():
                self._q = json.loads(_Q_FILE.read_text())
        except Exception: self._q = {}

    def _save(self):
        try: _Q_FILE.write_text(json.dumps(self._q))
        except Exception: pass

    def _state(self, regime: str, vix: float, hour: int, strategy: str) -> str:
        vix_level = "low" if vix < 15 else "mid" if vix < 22 else "high"
        time_slot = "open" if hour < 10 else "mid" if hour < 14 else "close"
        return f"{regime}_{vix_level}_{time_slot}_{strategy[:12]}"

    def get_multiplier(self, regime: str, vix: float,
                       hour: int, strategy: str) -> float:
        """Get recommended score multiplier for this context."""
        state = self._state(regime, vix, hour, strategy)
        if state not in self._q:
            return 1.0  # default — no info yet
        q_vals = self._q[state]
        if random.random() < _EPSILON:
            return random.choice(_ACTIONS)
        best_action = max(q_vals, key=q_vals.get)
        return float(best_action)

    def update(self, regime: str, vix: float, hour: int,
               strategy: str, action: float, reward: float):
        """Update Q-table after trade outcome is known."""
        state = self._state(regime, vix, hour, strategy)
        action_key = str(action)
        if state not in self._q:
            self._q[state] = {str(a): 0.0 for a in _ACTIONS}
        current_q = self._q[state].get(action_key, 0.0)
        max_next   = max(self._q[state].values())
        new_q      = current_q + _ALPHA * (reward + _GAMMA * max_next - current_q)
        self._q[state][action_key] = round(new_q, 4)
        self._save()
        logger.debug("RL update: state=%s action=%.2f reward=%.2f new_q=%.4f",
                     state, action, reward, new_q)

    def get_stats(self) -> dict:
        return {
            "states_learned": len(self._q),
            "q_file": str(_Q_FILE),
            "epsilon": _EPSILON,
        }

_AGENT: RLAgent = None  # type: ignore

def get_rl_agent() -> RLAgent:
    global _AGENT
    if _AGENT is None:
        _AGENT = RLAgent()
    return _AGENT

def rl_score_adjustment(regime: str, vix: float,
                        hour: int, strategy: str) -> float:
    """Get RL-based score multiplier. Returns 0.5-1.5."""
    try:
        return get_rl_agent().get_multiplier(regime, vix, hour, strategy)
    except Exception:
        return 1.0

def rl_record_outcome(regime: str, vix: float, hour: int,
                      strategy: str, action: float, pnl: float,
                      risk: float = 1000.0):
    """Record trade outcome for RL learning."""
    try:
        reward = pnl / max(risk, 1.0)  # normalised reward
        get_rl_agent().update(regime, vix, hour, strategy, action, reward)
    except Exception as e:
        logger.debug("rl_record: %s", e)
