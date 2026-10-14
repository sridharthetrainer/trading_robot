import sqlite3
import pandas as pd
import pytest
from research_backtest_adapters import window_data, contextual_candles, EXTENDED_BACKTESTS, MODEL_GRIDS, get_backtest_callable


def candles(n=100):
    data=pd.DataFrame({'Open':100.,'High':102.,'Low':99.,'Close':101.,'Volume':1000.},index=pd.date_range('2025-01-02 09:15',periods=n,freq='5min'))
    data.attrs['timestamp_semantics']='close'
    return data


def test_context_loader_never_reads_outside_validation_window(monkeypatch):
    import single_leg_intraday_option_backtest as shared
    monkeypatch.setattr(shared.sqlite3, 'connect', lambda *a, **k: pytest.fail('read full database'))
    df=candles()
    with window_data(df.iloc[10:30]):
        result=shared.load_nifty_candles()
        assert result.index.min() == df.index[10]
        assert result.index.max() == df.index[29]
        result.iloc[0,0]=999
        assert shared.load_nifty_candles().iloc[0,0] == 100
    assert contextual_candles() is None


def test_window_context_resets_after_failure():
    with pytest.raises(RuntimeError):
        with window_data(candles()):
            raise RuntimeError('test')
    assert contextual_candles() is None


def test_resampling_drops_incomplete_buckets():
    df=candles(8)
    with window_data(df):
        result=contextual_candles(interval='15m')
        assert len(result) == 2
        assert result.index.max() <= df.index.max()
        assert result['volume'].tolist() == [3000,3000]


def test_all_extended_sources_have_callables():
    import importlib
    for module, function in EXTENDED_BACKTESTS.values():
        assert callable(getattr(importlib.import_module(module), function))


def test_models_reject_banknifty_before_loading_evidence():
    fn=get_backtest_callable('bollinger_otm_reversal')
    with pytest.raises(ValueError,match='NIFTY only'):
        fn(symbol='BANKNIFTY', data=candles())


def test_model_adapter_limits_size_and_filters_data(tmp_path,monkeypatch):
    import backtest_bollinger_otm_reversal as module
    captured={}
    def fake(**kwargs):
        captured.update(kwargs)
        captured['data']=contextual_candles()
        return {'total_pnl':10,'num_trades':1,'sharpe':0,'win_rate':1}
    monkeypatch.setattr(module,'backtest_bollinger_otm_reversal',fake)
    monkeypatch.chdir(tmp_path)
    (tmp_path/'options_nifty.db').touch()
    result=get_backtest_callable('bollinger_otm_reversal')(symbol='NIFTY',data=candles(50),period=14,std_mult=1.5)
    assert captured['lots'] == 1
    assert len(captured['data']) == 50
    assert result['evidence_kind'] == 'MODELED_OPTION_RESEARCH'
    assert not result['live_approved']


def test_audit_count_has_deadline_and_cannot_create_database(tmp_path):
    from data_pipeline_audit import _sqlite_table_count
    db=tmp_path/'data.db'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE rows (id INTEGER)')
        conn.executemany('INSERT INTO rows VALUES (?)',[(n,) for n in range(20000)])
    timed=_sqlite_table_count(str(db),'rows',timeout_sec=0)
    assert 'error' in timed
    assert _sqlite_table_count(str(db),'rows')['count'] == 20000
    assert 'error' in _sqlite_table_count(str(db),'rows; DROP TABLE rows')
    missing=tmp_path/'missing.db'
    assert not _sqlite_table_count(str(missing),'rows')['exists']
    assert not missing.exists()


def test_open_timestamp_is_not_used_for_a_close_derived_signal():
    data=candles(6)
    data.attrs.clear()
    with window_data(data):
        result=contextual_candles()
        assert result.index[0] == data.index[0]+pd.Timedelta(minutes=5)
        aggregate=contextual_candles(interval='15m')
        assert aggregate.index[0] == data.index[0]+pd.Timedelta(minutes=15)
        assert len(aggregate)==2


def test_adapter_drawdown_includes_initial_loss(tmp_path,monkeypatch):
    import backtest_bollinger_otm_reversal as module
    monkeypatch.setattr(module,'backtest_bollinger_otm_reversal',lambda **kw:{'trades':[{'pnl':-100},{'pnl':50}], 'total_pnl':-50,'num_trades':2,'sharpe':-1,'win_rate':.5})
    monkeypatch.chdir(tmp_path)
    (tmp_path/'options_nifty.db').touch()
    r=get_backtest_callable('bollinger_otm_reversal')(symbol='NIFTY',data=candles())
    assert r['max_drawdown']==100


def test_metadata_catalog_does_not_count_rows_or_claim_integrity(tmp_path):
    from data_evidence_catalog import build_evidence_catalog
    path=tmp_path/'evidence.db'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE evidence (value INTEGER)')
        conn.execute('INSERT INTO evidence VALUES (1)')
    r=build_evidence_catalog(root=str(tmp_path),write=False,metadata_only=True)
    assert r['databases'][0]['tables'][0]['rows'] is None
    assert not r['databases'][0]['integrity_verified']
    assert not r['total_rows_complete']
    full=build_evidence_catalog(root=str(tmp_path),write=False)
    assert full['total_rows']==1 and full['total_rows_complete']


def test_carver_accepts_exact_daily_window_and_counts_initial_drawdown():
    import numpy as np
    from backtest_carver_ewmac import backtest_carver_ewmac
    data=pd.DataFrame({'Close':22000+np.arange(80)*4+np.sin(np.arange(80))*10},index=pd.bdate_range('2025-01-01',periods=80))
    result=backtest_carver_ewmac(data=data,capital=500000,verbose=False,close_at_end=True)
    assert result['daily'][-1]['date']==data.index[-1].date().isoformat()
    assert result['total_cost'] > 0
    assert result['max_drawdown'] >= 0


def test_adx_straddle_requires_real_one_minute_input():
    from research_backtest_adapters import adapter_metadata
    assert adapter_metadata('adx_long_straddle')['interval']=='1m'
    fn=get_backtest_callable('adx_long_straddle')
    with pytest.raises(ValueError,match='timeframe mismatch'):
        fn(symbol='NIFTY',data=candles(),interval_minutes=5)
    minute=candles(30)
    minute.index=pd.date_range('2025-01-02 09:15',periods=30,freq='min')
    with window_data(minute,1):
        assert len(contextual_candles(interval='1m'))==30
    with window_data(candles(),5):
        with pytest.raises(ValueError,match='unsupported'):
            contextual_candles(interval='1m')


def test_zero_budget_watchdog_reports_incomplete_audit(tmp_path):
    from data_quality_watchdog import audit_candle_cache
    db=tmp_path/'candles.db'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE candles (symbol TEXT,interval TEXT,timestamp TEXT,volume INTEGER)')
    result=audit_candle_cache(str(db),write=False,query_budget=0)
    assert result['ok'] is False
    assert result['reason']=='incomplete_database_audit'


def test_failed_grid_trials_are_counted_and_diagnosed():
    from validation_harness import _run_wf_grid
    index=pd.DatetimeIndex([day+pd.Timedelta(minutes=5*i) for day in pd.bdate_range('2025-01-01',periods=20) for i in range(10)])
    data=pd.DataFrame({'Close':100},index=index)
    def broken(**kw):
        raise ValueError('bad_backtest_contract')
    diagnostics=[]
    result=_run_wf_grid(broken,data,{'parameter':[1,2]},3,3,3,5,500000,'NIFTY',diagnostics=diagnostics)
    assert result[2] > 2
    assert not result[1]
    assert diagnostics[0]['error_type']=='ValueError'


def test_no_write_validation_does_not_log_real_experiment(monkeypatch):
    import experiment_registry
    from validation_harness import run_validation
    monkeypatch.setattr(experiment_registry,'log_result',lambda *a,**kw:pytest.fail('unexpected experiment write'))
    index=pd.DatetimeIndex([day+pd.Timedelta(minutes=5*i) for day in pd.bdate_range('2025-01-01',periods=20) for i in range(10)])
    data=pd.DataFrame({'Close':100},index=index)
    result=run_validation('test',lambda **kw:{'total_pnl':1,'num_trades':1,'sharpe':.1,'win_rate':.5},data,{'parameter':[1]},train_days=3,test_days=3,record_experiment=False)
    assert result.verdict!='PASS'


def test_precomputed_default_signals_equal_prefix_calls_and_ignore_future_poison():
    import numpy as np
    from signals import get_signal,precompute_default_signals
    rng=np.random.default_rng(42)
    close=22000+rng.normal(size=100).cumsum()
    data=pd.DataFrame({'Open':close,'High':close+2,'Low':close-2,'Close':close,'Volume':1000.},index=pd.date_range('2025-01-01',periods=100,freq='5min'))
    prepared=precompute_default_signals(data)
    for i in [0,28,29,35,50,70,99]:
        assert prepared[i] == get_signal(data.iloc[:i+1],{})
    poisoned=data.copy()
    poisoned.iloc[-1]=[1000000,1000001,999999,1000000,100000]
    assert prepared[:-1] == precompute_default_signals(poisoned)[:-1]
