"""Explicit research adapters. Modeled option prices never establish live readiness."""
from __future__ import annotations
from contextlib import contextmanager
from contextvars import ContextVar
import importlib
import math
from pathlib import Path

WINDOW = ContextVar('research_backtest_window', default=None)

# Each grid is a hypothesis declared before reading outcomes. Position size is
# fixed at one lot; daily turnover is not manufactured into completed trades.
MODEL_GRIDS = {
    'adx_dmi_crossover': {'period': [14, 20], 'adx_threshold': [20., 25.]},
    'adx_long_straddle': {'period': [14, 20], 'threshold': [20., 25.]},
    'aroon_crossover': {'period': [14, 25]},
    'bollinger_otm_momentum': {'period': [14, 20], 'std_mult': [1.5, 2.]},
    'bollinger_otm_reversal': {'period': [14, 20], 'std_mult': [1.5, 2.]},
    'contrarian_sell': {'leg_sl_pct': [.25, .30]},
    'delta_hedged_iron_butterfly': {'wing_pts': [150., 200.], 'rebalance_band': [.10, .15]},
    'di_momentum_call': {'di_period': [14, 20], 'mom_period': [5, 10]},
    'donchian_breakout': {'period': [14, 20]},
    'ema_supertrend_confirm': {'profit_target': [2500., 3750.]},
    'macd_crossover': {'fast': [8, 12], 'slow': [21, 26], 'signal': [9]},
    'premium_breakout': {'target_premium': [100., 150.], 'target_points': [15., 20.]},
    'real_option_translation': {'loss_limit_pct': [.25, .30]},
    'rolling_short_straddle': {'cycle_profit': [2500., 3750.], 'cycle_loss': [-2500.]},
    'sma20_atm_option': {'period': [14, 20]},
    'stochastic_crossover': {'k_period': [14], 'd_period': [3], 'oversold': [20., 30.], 'overbought': [70., 80.]},
}
NATIVE_GRIDS = {
    'confluence_backtest': {'stop_atr_mult': [1.5, 2.]},
    'legacy_mean_reversion': {'rsi_period': [14], 'bb_period': [20], 'bb_std': [1.5, 2.]},
    'sahi_daily': {'risk_per_trade': [.0025, .005], 'use_enhancements': [False, True]},
    'carver_ewmac': {'target_risk_pct': [.10, .15]},
}
EXTENDED_BACKTESTS = {
    name: ('backtest_' + name, 'backtest_' + name) for name in MODEL_GRIDS
}
EXTENDED_BACKTESTS.update({
    'confluence_backtest': ('backtest', 'backtest_symbol'),
    'legacy_mean_reversion': ('backtest_mean_reversion', 'backtest_mean_reversion'),
    'sahi_daily': ('backtest_sahi_strategy', 'backtest_sahi_strategy'),
    'carver_ewmac': ('backtest_carver_ewmac', 'backtest_carver_ewmac'),
})
DEPRECATED = {'backtest_iron_condor.py': 'Quarantined synthetic option credit; use condor_backtest_real.py on actual EOD quotes.'}


def adapter_metadata(name):
    if name not in EXTENDED_BACKTESTS:
        return None
    return {'interval': '1d' if name in {'sahi_daily', 'carver_ewmac'} else '1m' if name=='adx_long_straddle' else '5m',
            'symbols': ['NIFTY'] if name in MODEL_GRIDS else None,
            'evidence_kind': 'MODELED_OPTION_RESEARCH' if name in MODEL_GRIDS else 'UNDERLYING_OR_FUTURES_PROXY',
            'metric_unit': 'DAILY_MARK_TO_MARKET' if name=='carver_ewmac' else 'CLOSED_TRADES',
            'display_name': 'Legacy default EMA/RSI backtest' if name=='confluence_backtest' else name,
            'live_approved': False}


@contextmanager
def window_data(data, interval_minutes=5):
    import pandas as pd
    frame = data.copy()
    frame.columns = [str(c).lower() for c in frame.columns]
    if frame.empty or not isinstance(frame.index, pd.DatetimeIndex) or not frame.index.is_monotonic_increasing or frame.index.has_duplicates:
        raise ValueError('adapter requires ordered unique timestamped data')
    if frame.index.tz is not None:
        frame.index = frame.index.tz_convert('Asia/Kolkata').tz_localize(None)
    # Cached bars are stamped at their opens; close-derived signals cannot be
    # priced at that earlier clock. Contextual model bars use close timestamps.
    if data.attrs.get('timestamp_semantics') != 'close':
        frame.index = frame.index + pd.Timedelta(minutes=interval_minutes)
    frame.attrs['timestamp_semantics']='close'
    token = WINDOW.set((frame, interval_minutes))
    try:
        yield
    finally:
        WINDOW.reset(token)


def contextual_candles(symbol='NIFTY', interval='5m'):
    """Return the exact supplied window; resample only completed same-session bars."""
    payload = WINDOW.get()
    if payload is None:
        return None
    frame, base_minutes = payload
    if symbol != 'NIFTY':
        raise ValueError('option model supports NIFTY only')
    minutes = {'1m':1, '5m': 5, '15m': 15}.get(interval)
    if minutes is None or minutes < base_minutes or minutes % base_minutes:
        raise ValueError('unsupported model candle interval')
    if minutes == base_minutes:
        return frame.copy()
    # Models read finalized bars. Incomplete final buckets cannot borrow later
    # observations from outside a training/test slice or from another day.
    pieces = []
    for _, day in frame.groupby(frame.index.date):
        rule = f'{minutes}min'
        groups = day.resample(rule, origin='start_day', offset='15min', closed='right', label='right')
        out = groups.agg({'open':'first', 'high':'max', 'low':'min', 'close':'last', 'volume':'sum'})
        pieces.append(out.loc[groups['close'].count() == minutes // base_minutes].dropna())
    import pandas as pd
    return pd.concat(pieces).sort_index() if pieces else frame.iloc[:0].copy()


def get_backtest_callable(name):
    module_name, function_name = EXTENDED_BACKTESTS[name]
    module = importlib.import_module(module_name)
    original = getattr(module, function_name)

    def adapted(*, symbol, data, initial_capital=500000., interval_minutes=5,
                verbose=False, close_at_end=True, **params):
        import inspect
        meta = adapter_metadata(name)
        if meta['symbols'] and symbol not in meta['symbols']:
            raise ValueError('option model supports NIFTY only')
        expected = {'1d':1440,'1m':1,'5m':5}[meta['interval']]
        if interval_minutes != expected:
            raise ValueError('adapter timeframe mismatch')
        if not close_at_end:
            raise ValueError('validation requires positions closed at window end')
        if name in MODEL_GRIDS:
            if not Path('options_nifty.db').exists():
                raise ValueError('real EOD option evidence database unavailable')
            supported = inspect.signature(original).parameters
            kwargs = dict(params, verbose=verbose, lots=1)
            if 'lot_size' in supported:
                kwargs['lot_size'] = 65
            # Shared one-lot risk hypotheses; never the original 10-lot defaults.
            if 'profit_target' in supported:
                kwargs.setdefault('profit_target', 3750.)
            if 'loss_limit' in supported:
                kwargs.setdefault('loss_limit', -2500.)
            with window_data(data, interval_minutes):
                result = original(**kwargs)
        elif name == 'confluence_backtest':
            result = original(symbol=symbol, data=data,
                              config={'STOP_ATR_MULTIPLIER': params['stop_atr_mult']},
                              initial_capital=initial_capital, interval_minutes=interval_minutes,
                              close_at_end=True, verbose=verbose, precompute_signals=True)
        elif name == 'legacy_mean_reversion':
            result = original(symbol=symbol, data=data, initial_capital=initial_capital,
                              close_at_end=True, verbose=verbose, **params)
        elif name == 'sahi_daily':
            if symbol != 'NIFTY':
                # Benchmark input must share the exact validation slice.
                from candle_cache import get_cached_candles
                benchmark = get_cached_candles('NIFTY', '1d', days=400)
                if benchmark is None:
                    raise ValueError('daily NIFTY benchmark unavailable')
                benchmark = benchmark.loc[(benchmark.index >= data.index.min()) & (benchmark.index <= data.index.max())]
            else:
                benchmark = data.copy()
            result = original(symbol=symbol, data=data, nifty_data=benchmark,
                              start=str(data.index.min().date()), end=str(data.index.max().date()),
                              initial_capital=initial_capital, close_at_end=True, verbose=verbose, **params)
        else:
            result = original(symbol=symbol, capital=initial_capital, data=data,
                              close_at_end=True, verbose=verbose, **params)
        if not isinstance(result, dict):
            raise ValueError('backtester did not return metrics')
        result = dict(result)
        result.update(evidence_kind=meta['evidence_kind'], live_approved=False,
                      executable_fills_verified=False, funding_verified=False)
        trades=result.get('trades')
        if isinstance(trades,list) and trades:
            pnls=[]
            for trade in trades:
                record=trade if isinstance(trade,dict) else vars(trade)
                value=record.get('net_pnl',record.get('pnl'))
                if value is None or not math.isfinite(float(value)):
                    raise ValueError('finite after-cost trade P&L required')
                pnls.append(float(value))
            # Include starting capital so the very first loss counts as DD.
            peak=equity=float(initial_capital); drawdown=0.
            for pnl in pnls:
                equity+=pnl; peak=max(peak,equity); drawdown=max(drawdown,peak-equity)
            result['max_drawdown']=drawdown
            result['drawdown_source']='closed_trade_equity_including_initial_capital'
        # Daily mark-to-market observations are not completed trades.
        result.setdefault('num_trades', 0)
        result.setdefault('win_rate', 0.)
        result.setdefault('sharpe', 0.)
        result.setdefault('total_pnl', 0.)
        result.setdefault('max_drawdown', 0.)
        for key in ('total_pnl', 'sharpe', 'win_rate', 'max_drawdown'):
            if not math.isfinite(float(result[key])):
                raise ValueError('nonfinite backtest metric')
        return result
    return adapted
