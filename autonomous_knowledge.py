"""Durable experiment memory; no broker, network or runtime-mode changes."""
from __future__ import annotations
import hashlib
import json
import sqlite3
import time
from pathlib import Path

DEFAULT_DB = 'autonomous_knowledge.db'

class ExperimentMemory:
    def __init__(self, path=DEFAULT_DB):
        self.path = Path(path)

    def history(self):
        if not self.path.exists():
            return []
        with sqlite3.connect(f'file:{self.path.resolve()}?mode=ro', uri=True) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute('SELECT * FROM experiments ORDER BY id')]

    def plan(self, jobs, limit):
        ordered = list({(j['symbol'], j['strategy'], j['interval']): j for j in jobs}.values())
        # Rotate even when oldest jobs are waiting or have no usable data.
        offset = (int(time.time() // 86400) * min(max(0, limit), len(ordered))) % len(ordered) if ordered else 0
        return (ordered[offset:] + ordered[:offset])[:max(0, limit)]

    def assess(self, job, df, min_new_sessions=5):
        import pandas as pd
        if df is None or df.empty:
            return {'allowed': False, 'reason': 'empty_data'}
        if df.columns.has_duplicates:
            return {'allowed': False, 'reason': 'duplicate_columns'}
        index = pd.to_datetime(df.index, errors='coerce')
        if index.isna().any() or not isinstance(df.index, pd.DatetimeIndex):
            return {'allowed': False, 'reason': 'timestamp_index_required'}
        if not index.is_monotonic_increasing or index.has_duplicates:
            return {'allowed': False, 'reason': 'unordered_or_duplicate_bars'}
        columns = ['Open', 'High', 'Low', 'Close', 'Volume']
        if not set(columns).issubset(df.columns):
            return {'allowed': False, 'reason': 'missing_ohlcv'}
        import numpy as np
        try:
            numeric = df[columns].apply(pd.to_numeric, errors='raise')
            values = numeric.to_numpy(dtype=float)
        except (ValueError, TypeError):
            return {'allowed': False, 'reason': 'invalid_ohlcv'}
        if not np.isfinite(values).all() or (values[:, :4] <= 0).any() or (values[:, 4] < 0).any():
            return {'allowed': False, 'reason': 'invalid_ohlcv'}
        if ((numeric.High < numeric[['Open', 'Close', 'Low']].max(axis=1)) | (numeric.Low > numeric[['Open', 'Close', 'High']].min(axis=1))).any():
            return {'allowed': False, 'reason': 'invalid_candle_geometry'}
        fingerprint = hashlib.sha256(pd.util.hash_pandas_object(df[columns], index=True).values.tobytes()).hexdigest()
        local_index = index.tz_localize('Asia/Kolkata') if index.tz is None else index.tz_convert('Asia/Kolkata')
        if (local_index > pd.Timestamp.now(tz='Asia/Kolkata')).any():
            return {'allowed': False, 'reason': 'future_dated_bars'}
        from trading_calendar import is_trading_day
        sessions = sorted(s for s in set(local_index.strftime('%Y-%m-%d')) if is_trading_day(s))
        if not sessions:
            return {'allowed': False, 'reason': 'no_market_sessions'}
        previous = [r for r in self.history() if all(r[k] == job[k] for k in ('symbol', 'strategy', 'interval'))]
        evidence = {'fingerprint': fingerprint, 'last_session': sessions[-1], 'sessions': len(sessions)}
        if any(r['fingerprint'] == fingerprint for r in previous):
            return {**evidence, 'allowed': False, 'reason': 'already_tested_data'}
        if previous and sum(s > previous[-1]['last_session'] for s in sessions) < min_new_sessions:
            last_result=json.loads(previous[-1]['result_json'])
            old_sessions=last_result.get('evidence',{}).get('sessions',0)
            # An insufficient-data run never opened the harness holdout. Clean
            # backfilled development sessions can therefore make it testable.
            if (last_result.get('verdict')=='INSUFFICIENT_DATA'
                    and last_result.get('metrics',{}).get('holdout_pnl') is None
                    and old_sessions > 0 and len(sessions) >= old_sessions+min_new_sessions):
                return {**evidence,'allowed':True,'reason':'newly_backfilled_development_history',
                        'evaluation_kind':'rolling_exploratory_not_fresh_holdout'}
            return {**evidence, 'allowed': False, 'reason': 'waiting_for_new_sessions'}
        return {**evidence, 'allowed': True, 'reason': 'new_evidence', 'evaluation_kind': 'rolling_exploratory_not_fresh_holdout'}

    def record(self, job, evidence, result):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS experiments (id INTEGER PRIMARY KEY, symbol TEXT, strategy TEXT, interval TEXT, fingerprint TEXT, last_session TEXT, created_at REAL, result_json TEXT)')
            conn.execute('INSERT INTO experiments (symbol,strategy,interval,fingerprint,last_session,created_at,result_json) VALUES (?,?,?,?,?,?,?)', (job['symbol'],job['strategy'],job['interval'],evidence['fingerprint'],evidence['last_session'],time.time(),json.dumps(result, default=str)))

    def summary(self):
        rows = self.history()
        return {'experiments': len(rows), 'strategy_symbol_pairs': len({(r['symbol'], r['strategy']) for r in rows}), 'policy': 'staged research; repeated data blocked; five new sessions between experiments; no automatic live activation'}
