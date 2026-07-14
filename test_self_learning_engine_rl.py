import tempfile
from pathlib import Path

import self_learning_engine as sle
from self_learning_engine import SelfLearningEngine, RL_SHADOW_SIGNAL_WEIGHT


def _engine(tmp_dir: str):
    """Isolated engine instance. _save_rl_state() ALSO always writes to the
    module-level RL_BACKUP_FILE regardless of self.rl_state_file — that
    constant must be monkeypatched too, or a test silently corrupts the
    real project's rl_state_backup.json (this happened once, 2026-07-14;
    restored from git HEAD — see the commit for this file)."""
    eng = SelfLearningEngine.__new__(SelfLearningEngine)
    eng.rl_state_file = str(Path(tmp_dir) / "rl_state.json")
    eng.rl_state = {}
    return eng


def test_shadow_signal_update_tracks_separately_from_real_trades():
    with tempfile.TemporaryDirectory() as tmp:
        orig_backup = sle.RL_BACKUP_FILE
        sle.RL_BACKUP_FILE = str(Path(tmp) / "rl_state_backup.json")
        try:
            eng = _engine(tmp)
            rows = [
                {"id": 1, "strategy": "TEST_STRAT", "side": "BUY", "entry_price": 100.0,
                 "outcome_price": 102.0, "regime": "TREND"},
                {"id": 2, "strategy": "TEST_STRAT", "side": "SELL", "entry_price": 100.0,
                 "outcome_price": 105.0, "regime": "TREND"},  # a losing SELL
            ]
            result = eng._update_rl_from_signals(rows)
            assert result["updated"] is True and result["signals_processed"] == 2
            st = eng.rl_state["TEST_STRAT"]
            assert st["shadow_signals"] == 2 and st["shadow_wins"] == 1 and st["shadow_losses"] == 1
            # real-trade fields (trades/wins/losses) must stay untouched by shadow updates
            assert st["trades"] == 0 and st["wins"] == 0 and st["losses"] == 0
        finally:
            sle.RL_BACKUP_FILE = orig_backup


def test_shadow_reward_uses_reduced_weight():
    with tempfile.TemporaryDirectory() as tmp:
        orig_backup = sle.RL_BACKUP_FILE
        sle.RL_BACKUP_FILE = str(Path(tmp) / "rl_state_backup.json")
        try:
            eng = _engine(tmp)
            eng._update_rl_from_signals([
                {"id": 1, "strategy": "S", "side": "BUY", "entry_price": 100.0,
                 "outcome_price": 110.0, "regime": "TREND"},
            ])
            expected = (10.0 / 100.0) * RL_SHADOW_SIGNAL_WEIGHT
            assert abs(eng.rl_state["S"]["score"] - expected) < 1e-9
            assert RL_SHADOW_SIGNAL_WEIGHT < 1.0  # must be trusted less than a real trade
        finally:
            sle.RL_BACKUP_FILE = orig_backup


def test_watermark_advances_and_empty_batch_is_noop():
    with tempfile.TemporaryDirectory() as tmp:
        orig_backup = sle.RL_BACKUP_FILE
        sle.RL_BACKUP_FILE = str(Path(tmp) / "rl_state_backup.json")
        try:
            eng = _engine(tmp)
            eng._update_rl_from_signals([
                {"id": 5, "strategy": "S", "side": "BUY", "entry_price": 100.0,
                 "outcome_price": 101.0, "regime": "TREND"},
            ])
            assert eng._get_rl_signal_watermark() == 5
            result = eng._update_rl_from_signals([])
            assert result["updated"] is False
            assert eng._get_rl_signal_watermark() == 5  # unchanged
        finally:
            sle.RL_BACKUP_FILE = orig_backup
