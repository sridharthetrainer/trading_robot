import math
import json
import pytest
from autonomous_learning_cycle import _step, _top_weight_changes
from param_bridge import ParamBridge
from autonomous_knowledge import ExperimentMemory


def test_failed_nested_step_is_not_success():
    assert not _step('audit', lambda: {'ok': False})['ok']
    assert _step('audit', lambda: {'count': 0})['ok']


def test_weight_changes_preserve_zero_and_direction():
    result = _top_weight_changes({'feature_weights': {'up': 0, 'down': 2, 'bad': 'invalid'}}, {'feature_weights': {'up': 1, 'down': 0, 'bad': float('nan')}})
    assert [r['feature'] for r in result['promoted']] == ['up']
    assert [r['feature'] for r in result['demoted']] == ['down']
    assert result['promoted'][0]['before'] == 0


@pytest.mark.parametrize('overrides', [
    {'validation_verdict': 'FAIL'}, {'validation_verdict': 'INSUFFICIENT_DATA'},
    {'paper_training_only': True}, {'sharpe': math.nan}, {'sharpe': math.inf},
    {'win_rate': math.nan}, {'win_rate': 101}, {'num_trades': math.inf},
])
def test_runtime_params_reject_bad_evidence(overrides):
    bridge = ParamBridge.__new__(ParamBridge)
    metrics = {'num_trades': 30, 'sharpe': 1.2, 'win_rate': .6, **overrides}
    assert not bridge._validate_params({'fast_ema': 9}, metrics, 'trend')


def test_invalid_market_values_fail_closed(tmp_path):
    from test_autonomous_knowledge import frame, JOB
    memory = ExperimentMemory(tmp_path / 'memory.db')
    assert memory.assess(JOB, frame(0))['reason'] == 'empty_data'
    bad = frame().astype(object)
    bad.iloc[0, 0] = 'garbage'
    assert memory.assess(JOB, bad)['reason'] == 'invalid_ohlcv'


def test_waiting_jobs_rotate_without_completed_experiments(tmp_path, monkeypatch):
    import autonomous_knowledge as knowledge
    memory = ExperimentMemory(tmp_path / 'memory.db')
    jobs = [{'symbol': s, 'strategy': 'trend', 'interval': '5m'} for s in ['NIFTY', 'BANKNIFTY']]
    monkeypatch.setattr(knowledge.time, 'time', lambda: 0)
    assert memory.plan(jobs, 1)[0]['symbol'] == 'NIFTY'
    monkeypatch.setattr(knowledge.time, 'time', lambda: 86400)
    assert memory.plan(jobs, 1)[0]['symbol'] == 'BANKNIFTY'


def test_zero_training_budget_is_respected(tmp_path, monkeypatch):
    import autonomous_param_trainer as trainer
    monkeypatch.setenv('AUTONOMOUS_KNOWLEDGE_DB', str(tmp_path / 'memory.db'))
    report = trainer.run_autonomous_param_training(max_runs=0, dry_run=True, write=False)
    assert report['planned'] == []


def test_no_write_insufficient_data_does_not_log(tmp_path, monkeypatch):
    import autonomous_param_trainer as trainer
    monkeypatch.setenv('AUTONOMOUS_KNOWLEDGE_DB', str(tmp_path / 'memory.db'))
    monkeypatch.setattr(trainer, '_load_training_data', lambda *a: (None, 'missing'))
    monkeypatch.setattr(trainer, '_log_experiment_row', lambda **kw: pytest.fail('unexpected write'))
    report = trainer.run_autonomous_param_training(strategies=['trend'], symbols=['NIFTY'], write=False, force=True)
    assert report['results'][0]['reason'] == 'missing'


def test_simultaneous_trainer_is_skipped(tmp_path, monkeypatch):
    import fcntl
    import autonomous_param_trainer as trainer
    db = str(tmp_path / 'memory.db')
    monkeypatch.setenv('AUTONOMOUS_KNOWLEDGE_DB', db)
    with open(db + '.lock', 'a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        report = trainer.run_autonomous_param_training(force=True)
        assert report['skipped'][0]['reason'] == 'training_already_running'


def test_cache_rejects_nonfinite_ohlc():
    import candle_cache
    assert not candle_cache._valid_ohlc(100, math.inf, 99, 101)
    assert not candle_cache._valid_ohlc(math.nan, 102, 99, 101)


def test_cache_boundary_day_accepts_legacy_space_separator(tmp_path, monkeypatch):
    import candle_cache
    from datetime import datetime, timedelta
    monkeypatch.setattr(candle_cache, '_DB_PATH', tmp_path / 'candles.db')
    monkeypatch.setattr(candle_cache, '_INIT_DONE', False)
    day = (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d')
    with candle_cache._get_conn() as conn:
        for clock in ['09:15:00', '09:20:00']:
            conn.execute('INSERT INTO candles VALUES (?,?,?,?,?,?,?,?)', ('NIFTY','5m',day+' '+clock,100,102,99,101,1000))
    data = candle_cache.get_cached_candles('NIFTY', '5m', days=5)
    assert data is not None and len(data) == 2


def test_pipeline_propagates_explicit_failure():
    from daily_pipeline import _run_step
    result = _run_step('training', lambda: {'ok': False, 'error': 'failed_validation'})
    assert result['status'] == 'error'
    assert result['error'] == 'failed_validation'


def test_zero_holdout_sharpe_is_not_replaced_by_development():
    from autonomous_param_trainer import _validation_metrics
    from types import SimpleNamespace
    metrics = _validation_metrics(SimpleNamespace(holdout_sharpe=0, dev_avg_sharpe=5, parameter_stability_cv=0))
    assert metrics['sharpe'] == 0
    assert metrics['parameter_stability_cv'] == 0


def test_future_training_bars_rejected(tmp_path):
    import pandas as pd
    from test_autonomous_knowledge import frame, JOB
    data = frame(2)
    data.index = pd.date_range(pd.Timestamp.now(tz='Asia/Kolkata') + pd.Timedelta(days=1), periods=2, freq='D')
    assert ExperimentMemory(tmp_path / 'memory.db').assess(JOB, data)['reason'] == 'future_dated_bars'


def test_pipeline_keeps_weekend_report_skip_benign():
    from daily_pipeline import _run_step
    assert _run_step('report', lambda: {'ok': False, 'skipped': 'not_post_market'})['status'] == 'ok'
