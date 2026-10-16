"""Bounded, chronological ML research. Writes research reports, never live models.

All preprocessing is fit inside each training fold. Development alone selects
model and entry threshold. A single winner is tested on reserved later days.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


def load_evidence(db):
    from ml_feature_builder import _encode_row
    from signal_quality import price_row_ok
    with sqlite3.connect(Path(db).resolve().as_uri() + '?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(
            'SELECT * FROM signal_log WHERE training_eligible=1 AND tb_label IN (-1,0,1) '
            'AND stop_loss>0 AND target>0 AND rr>0 ORDER BY signal_date,signal_time,id')]
    features, evidence = [], []
    for row in rows:
        try:
            net = float(row['tb_r_multiple_net'])
        except (TypeError, ValueError):
            continue
        if not price_row_ok(row.get('entry_price'), row.get('outcome_price'))[0]:
            continue
        if not np.isfinite(net) or not row.get('signal_date'):
            continue
        start = pd.to_datetime(f"{row['signal_date']} {row['signal_time']}", errors='coerce')
        end = pd.to_datetime(row.get('outcome_time'), utc=True, errors='coerce')
        if pd.isna(start) or pd.isna(end):
            continue
        start = start.tz_localize('Asia/Kolkata').tz_convert('UTC')
        if end < start:
            continue
        feat = _encode_row(row)
        feat.update(strategy=str(row.get('strategy') or ''), symbol=str(row.get('symbol') or ''),
                    side=str(row.get('side') or ''))
        features.append(feat)
        evidence.append({'day': row['signal_date'], 'start': start, 'end': end,
                         'net_r': net, 'win': int(row['tb_label'] == 1),
                         'strategy': row.get('strategy'), 'id': row['id']})
    return pd.DataFrame(features).replace([np.inf, -np.inf], np.nan), pd.DataFrame(evidence)


def forward_splits(meta, folds=3, gap_days=1):
    """Expanding session splits; purge outcomes unavailable at test start."""
    days = np.array(sorted(meta.day.unique()))
    if len(days) < 12:
        return
    initial = max(5, len(days) // 3)
    for chunk in np.array_split(days[initial:], folds):
        if not len(chunk):
            continue
        first = list(days).index(chunk[0])
        train_days = days[:max(0, first - gap_days)]
        test = np.flatnonzero(meta.day.isin(chunk).to_numpy())
        cutoff = meta.iloc[test].start.min()
        train = np.flatnonzero((meta.day.isin(train_days) & (meta.end < cutoff)).to_numpy())
        if len(train) >= 100 and len(test) >= 50:
            yield train, test


def candidates(numeric, categorical):
    from sklearn.compose import ColumnTransformer
    from sklearn.ensemble import (ExtraTreesClassifier, RandomForestClassifier,
                                  HistGradientBoostingClassifier, HistGradientBoostingRegressor, VotingClassifier)
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression, Ridge
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler
    models = {}
    for c in (.1, 1., 10.):
        models[f'logistic_C{c}'] = ('classifier', LogisticRegression(C=c, max_iter=600, random_state=42))
    models['logistic_balanced'] = ('classifier', LogisticRegression(C=.1, class_weight='balanced', max_iter=600, random_state=42))
    for depth, leaf in [(2, 50), (4, 50), (6, 100)]:
        models[f'hist_classifier_d{depth}'] = ('classifier', HistGradientBoostingClassifier(
            max_iter=100, max_depth=depth, min_samples_leaf=leaf, l2_regularization=5,
            learning_rate=.05, early_stopping=False, random_state=42))
        models[f'hist_net_return_d{depth}'] = ('regressor', HistGradientBoostingRegressor(
            max_iter=100, max_depth=depth, min_samples_leaf=leaf, l2_regularization=5,
            learning_rate=.05, early_stopping=False, random_state=42))
    for family, klass in [('random_forest', RandomForestClassifier), ('extra_trees', ExtraTreesClassifier)]:
        for depth in (4, 8):
            models[f'{family}_d{depth}'] = ('classifier', klass(
                n_estimators=100, max_depth=depth, min_samples_leaf=50, n_jobs=2, random_state=42))
    models['ridge_net_return'] = ('regressor', Ridge(alpha=100))
    models['soft_voting_ensemble'] = ('classifier', VotingClassifier(estimators=[
        ('logistic', LogisticRegression(C=1., max_iter=600, random_state=42)),
        ('hist', HistGradientBoostingClassifier(max_iter=100, max_depth=4,
             min_samples_leaf=50, l2_regularization=5, learning_rate=.05,
             early_stopping=False, random_state=42)),
        ('extra', ExtraTreesClassifier(n_estimators=100, max_depth=8,
             min_samples_leaf=50, n_jobs=2, random_state=42)),
    ], voting='soft'))
    out = {}
    for name, (kind, model) in models.items():
        transform = ColumnTransformer([
            ('numeric', Pipeline([('impute', SimpleImputer(strategy='median', keep_empty_features=True)),
                                  ('scale', StandardScaler())]), numeric),
            ('categories', OneHotEncoder(handle_unknown='ignore', sparse_output=False), categorical)])
        out[name] = (kind, Pipeline([('features', transform), ('model', model)]))
    return out


def utility(meta, predictions, threshold, *, min_samples=50, min_days=5):
    selected = np.isfinite(predictions) & (predictions >= threshold)
    values = meta.loc[selected, ['day', 'net_r']]
    days = values.groupby('day').net_r.mean()
    out = {'threshold': float(threshold), 'selected': len(values), 'days': len(days),
           'coverage': float(selected.sum() / np.isfinite(predictions).sum()) if np.isfinite(predictions).any() else 0., 'mean_net_r': None,
           'daily_mean_net_r': None, 'daily_lcb95': None, 'eligible': False}
    if len(values):
        out['mean_net_r'] = float(values.net_r.mean())
    if len(values) < min_samples or len(days) < min_days:
        return out
    # Resample whole sessions, preserving same-day dependence between signals.
    rng = np.random.default_rng(42)
    bootstrap = rng.choice(days.to_numpy(), size=(1000, len(days)), replace=True).mean(axis=1)
    out.update(daily_mean_net_r=float(days.mean()), daily_lcb95=float(np.quantile(bootstrap, .05)), eligible=True)
    return out


def research(X, meta, *, holdout_fraction=.2):
    from sklearn.base import clone
    from sklearn.metrics import roc_auc_score, brier_score_loss, mean_squared_error
    days = sorted(meta.day.unique())
    if len(days) < 18:
        return {'status': 'insufficient_days', 'days': len(days), 'live_changes': False}
    n_test = max(5, int(np.ceil(len(days) * holdout_fraction)))
    test_mask = meta.day.isin(days[-n_test:])
    holdout_start = meta.loc[test_mask, 'start'].min()
    # Omit one complete session plus any outcomes crossing the boundary.
    dev_mask = meta.day.isin(days[:-n_test-1]) & (meta.end < holdout_start)
    dev, test = meta.loc[dev_mask].reset_index(drop=True), meta.loc[test_mask].reset_index(drop=True)
    xd, xt = X.loc[dev_mask].reset_index(drop=True), X.loc[test_mask].reset_index(drop=True)
    numeric = [c for c in X.columns if c not in ('strategy', 'symbol', 'side')]
    models = candidates(numeric, ['strategy', 'symbol', 'side'])
    splits = list(forward_splits(dev))
    board = []
    for name, (kind, pipeline) in models.items():
        pred = np.full(len(dev), np.nan)
        try:
            for train_idx, valid_idx in splits:
                if kind == 'classifier' and dev.iloc[train_idx].win.nunique() < 2:
                    continue
                target = dev.win if kind == 'classifier' else dev.net_r
                fitted = clone(pipeline).fit(xd.iloc[train_idx], target.iloc[train_idx])
                pred[valid_idx] = (fitted.predict_proba(xd.iloc[valid_idx])[:, 1] if kind == 'classifier'
                                   else fitted.predict(xd.iloc[valid_idx]))
            scored = np.isfinite(pred)
            if scored.sum() < 100:
                board.append({'name': name, 'status': 'insufficient_folds'})
                continue
            thresholds = (.05, .1, .2, .35, .5, .65, .8) if kind == 'classifier' else (0., .05, .1, .2, .4)
            options = [utility(dev, pred, t) for t in thresholds]
            eligible = [o for o in options if o['eligible']]
            best = max(eligible, key=lambda o: o['daily_lcb95']) if eligible else None
            row = {'name': name, 'kind': kind, 'status': 'evaluated', 'scored_rows': int(scored.sum()),
                   'threshold_trials': len(options), 'best_utility': best}
            if kind == 'classifier':
                row['auc'] = float(roc_auc_score(dev.win[scored], pred[scored])) if dev.win[scored].nunique() == 2 else None
                row['brier'] = float(brier_score_loss(dev.win[scored], pred[scored]))
            else:
                row['mse'] = float(mean_squared_error(dev.net_r[scored], pred[scored]))
            board.append(row)
            print(f'{name}: {best}', flush=True)
        except Exception as exc:
            board.append({'name': name, 'status': 'error', 'reason': str(exc)})
    eligible = [r for r in board if r.get('best_utility')]
    report = {'generated_at': datetime.now(ZoneInfo('Asia/Kolkata')).isoformat(),
              'mode': 'research_only', 'live_changes': False, 'rows': len(meta),
              'development_rows': len(dev), 'holdout_rows': len(test),
              'development_days': sorted(dev.day.unique()), 'holdout_days': sorted(test.day.unique()),
              'forward_folds': len(splits), 'candidate_count': len(models), 'leaderboard': board,
              'feature_columns': list(X.columns), 'strategy_count': int(meta.strategy.nunique()),
              'selection_rule': 'maximum development session-bootstrap lower bound',
              'classification_thresholds': [.05, .1, .2, .35, .5, .65, .8],
              'regression_thresholds': [0., .05, .1, .2, .4],
              'candidate_parameters': {name: (pipe.named_steps['model'] if hasattr(pipe, 'named_steps') else pipe).get_params(deep=False)
                                       for name, (_, pipe) in models.items()},
              'status': 'no_candidate', 'selected_model': None,
              'limitations': ['Historical holdout is not fresh prospective evidence.',
                  'Bootstrap bounds are diagnostics, not corrected for model and threshold selection.',
                  'Net R relies on upstream cost assumptions; no portfolio execution simulation.',
                  'Labels must have recorded outcome times; rows missing them are excluded.',
                  'Only recorded entry features used; no current candle data joined to history.']}
    if not eligible:
        return report
    winner = max(eligible, key=lambda r: r['best_utility']['daily_lcb95'])
    name, threshold = winner['name'], winner['best_utility']['threshold']
    kind, pipe = models[name]
    pipe.fit(xd, dev.win if kind == 'classifier' else dev.net_r)
    test_pred = pipe.predict_proba(xt)[:, 1] if kind == 'classifier' else pipe.predict(xt)
    held = utility(test, test_pred, threshold)
    report.update(selected_model=name, selected_threshold=threshold, holdout=held,
                  status='requires_prospective_validation' if (
                      winner['best_utility']['daily_lcb95'] > 0
                      and winner['best_utility']['mean_net_r'] > 0 and held['eligible']
                      and held['daily_lcb95'] > 0 and held['mean_net_r'] > 0) else 'rejected')
    selected = np.isfinite(test_pred) & (test_pred >= threshold)
    report['holdout_by_strategy'] = [
        {'strategy': strategy, 'selected': len(group), 'mean_net_r': float(group.net_r.mean())}
        for strategy, group in test.loc[selected].groupby('strategy')]
    # Export research scores and configuration only; no runtime-compatible model.
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default='signal_log.db')
    parser.add_argument('--output', default='reports/advanced_ml_strategy_research.json')
    args = parser.parse_args()
    X, meta = load_evidence(args.db)
    if meta.empty:
        report = {'status': 'no_usable_evidence', 'live_changes': False}
    else:
        report = research(X, meta)
        fingerprint = pd.util.hash_pandas_object(pd.concat([X, meta.astype(str)], axis=1), index=False)
        report['evidence_sha256'] = hashlib.sha256(fingerprint.to_numpy().tobytes()).hexdigest()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    report['source_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    output.write_text(json.dumps(report, indent=2, allow_nan=False, default=str) + '\n')
    print(json.dumps({'status': report['status'], 'report': str(output)}))


if __name__ == '__main__':
    main()
