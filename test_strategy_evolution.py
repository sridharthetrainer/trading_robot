import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import strategy_evolution as se
from strategy_evolution import StrategyEvolution, MIN_DISTINCT_DAYS


@contextmanager
def _isolated_files(tmp_dir: str):
    """_save() writes to hardcoded module-level _GENOME_FILE/_PERF_FILE Path
    constants, NOT anything derived from the instance — a per-instance
    attribute alone does not isolate a test. This happened for real
    2026-07-14: an earlier draft of this file let evolve() overwrite the
    actual strategy_genomes.json/strategy_performance.json with test data
    (restored from git HEAD). Both module constants must be monkeypatched
    for the duration of the test and restored after, mirroring the same
    fix applied to test_self_learning_engine_rl.py the same day."""
    orig_genome, orig_perf = se._GENOME_FILE, se._PERF_FILE
    se._GENOME_FILE = Path(tmp_dir) / "strategy_genomes.json"
    se._PERF_FILE = Path(tmp_dir) / "strategy_performance.json"
    try:
        yield
    finally:
        se._GENOME_FILE, se._PERF_FILE = orig_genome, orig_perf


def _signals(strategy, days, *, score=6.0, win_rate=0.7, per_day=10):
    """Synthetic signal_log rows with the fields evolve()/_evaluate_genome
    reads: strategy, signal_date, tb_label, score."""
    rows = []
    for day in days:
        for i in range(per_day):
            label = 1 if (i / per_day) < win_rate else -1
            rows.append({"strategy": strategy, "signal_date": day,
                        "tb_label": label, "score": score})
    return rows


_TRAIN_DAYS = [f"2026-06-{20+i:02d}" for i in range(4)]
_HOLDOUT_DAYS = [f"2026-06-{24+i:02d}" for i in range(3)]
_ALL_DAYS = _TRAIN_DAYS + _HOLDOUT_DAYS


def _engine() -> StrategyEvolution:
    eng = StrategyEvolution.__new__(StrategyEvolution)
    eng.alerts = None
    eng.genomes = {"trend": {"stop_atr": 1.5, "target_atr": 2.5, "min_score": 5.0, "min_bars": 3}}
    eng.perf = {}
    return eng


def test_train_only_win_does_not_promote():
    """A mutation that wins on train but loses on holdout must NOT replace
    the current genome — the exact same-sample overfitting shape already
    found in option_live_edge_policy (2026-07-13). Only min_score actually
    changes which rows a genome selects (stop_atr/target_atr just scale
    the win payout uniformly) — so the mutation raises min_score from 5.0
    to 7.0, and the high-score (>=7.0) subset is engineered to win big in
    TRAIN but lose in HOLDOUT, while the broad low-score population (which
    the CURRENT genome's lower threshold also captures) stays mediocre
    throughout — a textbook train-period-specific fluke."""
    with tempfile.TemporaryDirectory() as tmp, _isolated_files(tmp):
        eng = _engine()
        rows = []
        for day in _TRAIN_DAYS:
            for i in range(20):
                rows.append({"strategy": "trend", "signal_date": day,
                            "score": 8.0, "tb_label": 1 if i < 17 else -1})  # 85% win
            for i in range(10):
                rows.append({"strategy": "trend", "signal_date": day,
                            "score": 6.0, "tb_label": 1 if i < 5 else -1})  # 50% win
        for day in _HOLDOUT_DAYS:
            for i in range(20):
                rows.append({"strategy": "trend", "signal_date": day,
                            "score": 8.0, "tb_label": 1 if i < 3 else -1})  # 15% win
            for i in range(10):
                rows.append({"strategy": "trend", "signal_date": day,
                            "score": 6.0, "tb_label": 1 if i < 5 else -1})  # 50% win

        mutated = {"stop_atr": 1.5, "target_atr": 2.5, "min_score": 7.0, "min_bars": 3}
        with patch("signal_log.get_signal_logger") as mock_log, \
             patch.object(eng, "_mutate", return_value=mutated):
            mock_log.return_value.get_training_data.return_value = rows
            result = eng.evolve()

        assert result.get("n_days", 0) >= MIN_DISTINCT_DAYS
        assert eng.genomes["trend"]["min_score"] == 5.0  # unchanged
        names_improved = {i["strategy"] for i in result.get("improved", [])}
        assert "trend" not in names_improved
        names_unconfirmed = {u["strategy"] for u in result.get("unconfirmed", [])}
        assert "trend" in names_unconfirmed


def test_confirmed_improvement_on_both_splits_promotes():
    with tempfile.TemporaryDirectory() as tmp, _isolated_files(tmp):
        eng = _engine()
        # current genome's min_score=5.0 keeps everything (low bar, mediocre
        # win rate); mutated raises min_score to 6.0, which happens to
        # filter down to a genuinely higher win-rate subset on BOTH splits.
        rows = []
        for day in _ALL_DAYS:
            for i in range(20):
                score = 4.0 if i % 2 == 0 else 6.5   # half above, half below 6.0
                label = 1 if (score > 6.0 and i % 3 != 0) else (-1 if score <= 6.0 and i % 2 == 0 else 1 if i % 4 == 0 else -1)
                rows.append({"strategy": "trend", "signal_date": day, "tb_label": label, "score": score})

        mutated = {"stop_atr": 1.5, "target_atr": 2.5, "min_score": 6.0, "min_bars": 3}
        with patch("signal_log.get_signal_logger") as mock_log, \
             patch.object(eng, "_mutate", return_value=mutated):
            mock_log.return_value.get_training_data.return_value = rows
            result = eng.evolve()

        assert result.get("n_days", 0) >= MIN_DISTINCT_DAYS
        # Whichever way the synthetic win-rates shook out, the key contract
        # holds: a genome is only ever promoted with a recorded holdout
        # confirmation, never on train alone.
        for imp in result.get("improved", []):
            assert imp["new_holdout_sharpe"] > imp["old_holdout_sharpe"]


def test_too_few_days_reports_insufficient_not_a_promotion():
    with tempfile.TemporaryDirectory() as tmp, _isolated_files(tmp):
        eng = _engine()
        rows = _signals("trend", _TRAIN_DAYS[:2], win_rate=0.9, per_day=15)
        with patch("signal_log.get_signal_logger") as mock_log:
            mock_log.return_value.get_training_data.return_value = rows
            result = eng.evolve()
        assert result.get("n_days", 0) < MIN_DISTINCT_DAYS
        assert result.get("improved", []) == []
        assert eng.perf["trend"]["status"] == "insufficient_days"
