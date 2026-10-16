import numpy as np
import pandas as pd

from advanced_ml_strategy_research import candidates, forward_splits, utility


def metadata(days=24, per_day=30):
    dates = pd.date_range('2026-01-01', periods=days, tz='UTC')
    return pd.DataFrame([
        {'day': date.strftime('%Y-%m-%d'), 'start': date + pd.Timedelta(hours=4),
         'end': date + pd.Timedelta(hours=5), 'net_r': .3 if i % 2 else -.1, 'win': i % 2}
        for date in dates for i in range(per_day)])


def test_forward_folds_purge_unknown_outcomes_and_separate_sessions():
    meta = metadata()
    meta.loc[0, 'end'] = meta.start.max() + pd.Timedelta(days=1)
    folds = list(forward_splits(meta))
    assert len(folds) == 3
    for train, test in folds:
        assert 0 not in train
        assert meta.iloc[train].end.max() < meta.iloc[test].start.min()
        assert set(meta.iloc[train].day).isdisjoint(meta.iloc[test].day)
        assert (meta.iloc[test].start.min() - meta.iloc[train].start.max()).days >= 2


def test_correlated_one_day_cannot_qualify_by_row_count():
    meta = metadata(days=1, per_day=500)
    result = utility(meta, np.ones(len(meta)), .5)
    assert result['selected'] == 500
    assert not result['eligible']
    assert result['daily_lcb95'] is None


def test_threshold_is_fixed_and_nonfinite_predictions_excluded():
    meta = metadata(days=8)
    pred = np.full(len(meta), .6)
    pred[:5] = np.nan
    result = utility(meta, pred, .5)
    assert result['selected'] == len(meta) - 5
    assert result['eligible']
    assert utility(meta, pred, .7)['selected'] == 0


def test_all_models_keep_preprocessing_inside_pipeline():
    from sklearn.base import clone
    models = candidates(['score'], ['strategy', 'symbol', 'side'])
    assert len(models) == 16
    for _, model in models.values():
        assert list(clone(model).named_steps) == ['features', 'model']


def test_fold_preprocessing_accepts_unseen_strategy_and_missing_values():
    X = pd.DataFrame({'score': [1., 2., np.nan, 3., 4., 5.],
                      'strategy': ['trend'] * 6, 'symbol': ['NIFTY'] * 6, 'side': ['BUY'] * 6})
    _, pipe = candidates(['score'], ['strategy', 'symbol', 'side'])['logistic_C0.1']
    pipe.fit(X, [0, 0, 1, 0, 1, 1])
    new = X.iloc[:1].copy()
    new['strategy'] = 'unseen'
    assert np.isfinite(pipe.predict_proba(new)).all()


def test_holdout_outcomes_cannot_change_chosen_model_or_threshold(monkeypatch):
    import advanced_ml_strategy_research as research_module
    from sklearn.linear_model import Ridge
    meta = metadata(days=30, per_day=30)
    meta['strategy'] = 'trend'
    X = pd.DataFrame({'score': np.tile(np.arange(30) / 30, 30)})
    meta['net_r'] = X.score - .5
    monkeypatch.setattr(research_module, 'candidates', lambda *_: {
        'ridge_a': ('regressor', Ridge(alpha=.1)),
        'ridge_b': ('regressor', Ridge(alpha=10))})
    first = research_module.research(X, meta)
    poisoned = meta.copy()
    poisoned.loc[poisoned.day.isin(first['holdout_days']), 'net_r'] = -100
    second = research_module.research(X, poisoned)
    assert first['selected_model'] is not None
    assert first['selected_model'] == second['selected_model']
    assert first['selected_threshold'] == second['selected_threshold']
    assert second['status'] == 'rejected'
    assert second['holdout']['mean_net_r'] == -100


def test_loader_excludes_bad_prices_and_unknown_outcomes(tmp_path):
    import sqlite3
    from advanced_ml_strategy_research import load_evidence
    path = tmp_path / 'signals.db'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE signal_log (id INTEGER, signal_date TEXT, signal_time TEXT, '
                     'outcome_time TEXT, tb_label INTEGER, tb_r_multiple_net REAL, '
                     'training_eligible INTEGER, stop_loss REAL, target REAL, rr REAL, '
                     'entry_price REAL, outcome_price REAL, score REAL, strategy TEXT)')
        for ident, end, price in [(1, '2026-01-01 10:30:00+05:30', 101),
                                  (2, None, 101), (3, '2026-01-01 10:30:00+05:30', 1000)]:
            conn.execute('INSERT INTO signal_log VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                         (ident, '2026-01-01', '10:00:00', end, 1, .5, 1, 99, 102, 2,
                          100, price, 7, 'trend'))
    X, meta = load_evidence(path)
    assert len(X) == len(meta) == 1
    assert meta.id.tolist() == [1]
    assert not {'tb_label', 'tb_r_multiple_net', 'outcome_time', 'outcome_price'} & set(X.columns)


def test_loader_never_creates_missing_database(tmp_path):
    import pytest
    import sqlite3
    from advanced_ml_strategy_research import load_evidence
    path = tmp_path / 'absent.db'
    with pytest.raises(sqlite3.OperationalError):
        load_evidence(path)
    assert not path.exists()
