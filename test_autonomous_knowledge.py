import pandas as pd
from autonomous_knowledge import ExperimentMemory

JOB = {'symbol': 'NIFTY', 'strategy': 'trend', 'interval': '5m'}

def frame(days=10):
    return pd.DataFrame({'Open': 100., 'High': 102., 'Low': 99., 'Close': 101., 'Volume': 1000.}, index=pd.bdate_range('2025-09-01', periods=days))

def test_memory_blocks_repeat_and_requires_new_sessions(tmp_path):
    memory = ExperimentMemory(tmp_path / 'memory.db')
    first = memory.assess(JOB, frame())
    assert first['allowed']
    memory.record(JOB, first, {'verdict': 'FAIL'})
    assert memory.assess(JOB, frame())['reason'] == 'already_tested_data'
    assert memory.assess(JOB, frame(14))['reason'] == 'waiting_for_new_sessions'
    assert memory.assess(JOB, frame(15))['allowed']
    assert memory.summary()['experiments'] == 1

def test_revised_old_data_does_not_unlock(tmp_path):
    memory = ExperimentMemory(tmp_path / 'memory.db')
    memory.record(JOB, memory.assess(JOB, frame()), {})
    changed = frame()
    changed['Volume'] = 1234
    assert not memory.assess(JOB, changed)['allowed']

def test_bad_market_data_rejected(tmp_path):
    memory = ExperimentMemory(tmp_path / 'memory.db')
    bad = frame()
    bad.iloc[0, 1] = 98
    assert memory.assess(JOB, bad)['reason'] == 'invalid_candle_geometry'
    assert memory.assess(JOB, frame().iloc[::-1])['reason'] == 'unordered_or_duplicate_bars'
    assert memory.assess(JOB, frame().reset_index(drop=True))['reason'] == 'timestamp_index_required'

def test_readonly_planning_creates_no_database(tmp_path):
    path = tmp_path / 'memory.db'
    memory = ExperimentMemory(path)
    assert memory.plan([JOB], 1) == [JOB]
    assert memory.summary()['experiments'] == 0
    assert not path.exists()

def test_trainer_stages_pass_without_overwriting_runtime(tmp_path, monkeypatch):
    import sys
    from types import ModuleType
    fake = ModuleType('backtest_trend')
    fake.backtest_trend = lambda **kw: None
    monkeypatch.setitem(sys.modules, 'backtest_trend', fake)
    import autonomous_param_trainer as trainer
    import validation_harness as harness
    from types import SimpleNamespace
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('AUTONOMOUS_KNOWLEDGE_DB', str(tmp_path / 'memory.db'))
    monkeypatch.setattr(trainer, '_load_training_data', lambda *a: (frame(110), 'cache'))
    monkeypatch.setattr(harness, '_build_default_param_grid', lambda *a: {'fast_ema': [9]})
    result = SimpleNamespace(verdict='PASS', best_params={'fast_ema': 9})
    monkeypatch.setattr(harness, 'run_validation', lambda **kw: result)
    saves = []
    monkeypatch.setattr(harness, 'save_result', lambda r: saves.append(r))
    runtime = tmp_path / 'symbol_params' / 'NIFTY_trend.json'
    runtime.parent.mkdir()
    runtime.write_text('original settings')
    report = trainer.run_autonomous_param_training(strategies=['trend'], symbols=['NIFTY'], force=True)
    assert not report['promoted']
    assert report['results'][0].get('promotion_block_reason') == 'requires_independent_forward_validation', report
    assert (tmp_path / report['results'][0]['candidate_path']).exists()
    assert runtime.read_text() == 'original settings'
    assert len(saves) == 1
    second = trainer.run_autonomous_param_training(strategies=['trend'], symbols=['NIFTY'], force=True)
    assert second['results'][0]['reason'] == 'already_tested_data'
    assert len(saves) == 1

def test_no_write_training_does_not_save_params_or_memory(tmp_path, monkeypatch):
    import sys
    from types import ModuleType
    fake = ModuleType('backtest_trend')
    fake.backtest_trend = lambda **kw: None
    monkeypatch.setitem(sys.modules, 'backtest_trend', fake)
    import autonomous_param_trainer as trainer
    import validation_harness as harness
    from types import SimpleNamespace
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('AUTONOMOUS_KNOWLEDGE_DB', str(tmp_path / 'memory.db'))
    monkeypatch.setattr(trainer, '_load_training_data', lambda *a: (frame(110), 'cache'))
    monkeypatch.setattr(harness, '_build_default_param_grid', lambda *a: {'fast_ema': [9]})
    monkeypatch.setattr(harness, 'run_validation', lambda **kw: SimpleNamespace(verdict='FAIL', best_params={'fast_ema': 9}))
    monkeypatch.setattr(harness, 'save_result', lambda r: (_ for _ in ()).throw(AssertionError('unexpected save')))
    trainer.run_autonomous_param_training(strategies=['trend'], symbols=['NIFTY'], force=True, write=False)
    assert not list(tmp_path.iterdir())


def test_backfilled_history_unlocks_only_unopened_insufficient_holdout(tmp_path):
    memory=ExperimentMemory(tmp_path/'memory.db')
    old=frame(10)
    evidence=memory.assess(JOB,old)
    memory.record(JOB,evidence,{'verdict':'INSUFFICIENT_DATA','metrics':{'holdout_pnl':None},'evidence':evidence})
    new=frame(20)
    new.index=pd.bdate_range(end=old.index[-1],periods=20)
    result=memory.assess(JOB,new)
    assert result['allowed'] and result['reason']=='newly_backfilled_development_history'
    second=ExperimentMemory(tmp_path/'failed.db')
    second.record(JOB,evidence,{'verdict':'FAIL','evidence':evidence})
    assert not second.assess(JOB,new)['allowed']


def test_seen_holdout_cannot_be_unlocked_by_older_backfill(tmp_path):
    memory=ExperimentMemory(tmp_path/'memory.db')
    old=frame(10); evidence=memory.assess(JOB,old)
    memory.record(JOB,evidence,{'verdict':'INSUFFICIENT_DATA','metrics':{'holdout_pnl':0},'evidence':evidence})
    new=frame(20);new.index=pd.bdate_range(end=old.index[-1],periods=20)
    assert not memory.assess(JOB,new)['allowed']
