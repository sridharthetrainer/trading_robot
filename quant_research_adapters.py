"""Frozen Q01/Q06 daily research adaptations; supplied-data backtests only.

Outputs are UNDERLYING_PROXY, never option P&L or order instructions. Historical
CLI callers must retain research admission. No broker imports or live scheduling.
"""
from __future__ import annotations
import hashlib
import json
import math
import pandas as pd
import numpy as np

SPECS = {
    'Q01': dict(version='q01_daily_v1', timeframe='1d', sma_periods=[10,20,50,100,200],
                adx_period=14, adx_min=20, confirmation_bars=5,
                entry='Next observed daily open after first completed five-bar gate',
                stop='Fifth gate bar low/high; skip if next open is already beyond stop',
                exit='Fast SMA reversal at next open, or intraday fixed stop; gap uses worse open',
                rearm='Gate must become false before a new five-bar gate', max_holding_bars=None),
    'Q06': dict(version='q06_daily_v1', timeframe='1d', lookbacks=[30,40],
                entry='Daily close comparison, executed next observed daily open',
                stop='Fixed 2 x Wilder ATR(14) from signal close, a project adaptation',
                exit='Next-open flatten/reversal, fixed stop, or 20-bar time limit',
                max_holding_bars=20),
}
COMMON = dict(evidence_kind='UNDERLYING_PROXY', risk_fraction=.005, notional_cap_fraction=1.,
              slippage_bps_each_side=5., fee_bps_each_side=10., max_positions=1,
              sizing='Integer proxy units, stop-based risk capped by gross notional; not NSE lots',
              end_of_window='Liquidate at final close, explicitly tagged window_end',
              costs='Declared proxy fee/slippage stress assumptions, not certified broker tax rates',
              overnight='Daily proxy can hold overnight; options translation requires separate policy',
              options_translation='UNIMPLEMENTED; exact contract, dated metadata and executable quotes required',
              live_approved=False)


def specification(model_id):
    if model_id not in SPECS:
        raise KeyError(model_id)
    spec = dict(SPECS[model_id], **COMMON)
    return json.loads(json.dumps(spec))


def validate_daily(data):
    if not isinstance(data, pd.DataFrame) or not isinstance(data.index, pd.DatetimeIndex) or data.empty:
        raise ValueError('Nonempty timestamped daily OHLC required')
    frame = data.copy()
    frame.columns = [str(c).lower() for c in frame.columns]
    if frame.columns.has_duplicates or not {'open','high','low','close'} <= set(frame.columns):
        raise ValueError('Unique OHLC columns required')
    if not frame.index.is_monotonic_increasing or frame.index.has_duplicates:
        raise ValueError('Ordered unique timestamps required')
    if len(set(frame.index.date)) != len(frame):
        raise ValueError('One completed bar per session required')
    if frame.attrs.get('completed_bars') is not True or frame.attrs.get('interval') != '1d':
        raise ValueError('Explicit completed_bars=True and interval=1d metadata required')
    values = frame[['open','high','low','close']].apply(pd.to_numeric, errors='coerce')
    if not np.isfinite(values.to_numpy()).all() or (values <= 0).any().any():
        raise ValueError('Finite positive prices required')
    if ((values.high < values[['open','close','low']].max(axis=1)) |
        (values.low > values[['open','close','high']].min(axis=1))).any():
        raise ValueError('Invalid candle geometry')
    frame[values.columns] = values
    return frame


def signals(model_id, data):
    from indicators import calculate_adx, calculate_atr
    frame = validate_daily(data)
    specification(model_id)
    out = pd.DataFrame(index=frame.index)
    close = frame.close
    if model_id == 'Q01':
        averages = [close.rolling(p, min_periods=p).mean() for p in [10,20,50,100,200]]
        bull = pd.concat([a > b for a,b in zip(averages, averages[1:])], axis=1).all(axis=1)
        bear = pd.concat([a < b for a,b in zip(averages, averages[1:])], axis=1).all(axis=1)
        strong = calculate_adx(frame, 14) >= 20
        long_gate = (bull & strong).rolling(5, min_periods=5).sum() == 5
        short_gate = (bear & strong).rolling(5, min_periods=5).sum() == 5
        out['entry'] = np.where(long_gate & ~long_gate.shift(1, fill_value=False), 1,
                                np.where(short_gate & ~short_gate.shift(1, fill_value=False), -1, 0))
        out['stop_long'], out['stop_short'] = frame.low, frame.high
        out['exit_long'], out['exit_short'] = averages[0] < averages[1], averages[0] > averages[1]
    else:
        out['entry'] = np.where((close < close.shift(30)) & (close > close.shift(40)), 1,
                                np.where((close > close.shift(30)) & (close < close.shift(40)), -1, 0))
        atr = calculate_atr(frame, 14)
        out['stop_long'], out['stop_short'] = close-2*atr, close+2*atr
        out['exit_long'], out['exit_short'] = out.entry != 1, out.entry != -1
    return out


def backtest(model_id, *, data, initial_capital=500000.):
    """Deterministic next-open simulator; no fetching, parameter search or option fills."""
    if not math.isfinite(initial_capital) or initial_capital <= 0:
        raise ValueError('Finite positive initial capital required')
    frame = validate_daily(data)
    spec = specification(model_id)
    sig = signals(model_id, frame)
    capital = float(initial_capital)
    position = None
    trades, equity = [], []
    slip, fee = spec['slippage_bps_each_side']/10000, spec['fee_bps_each_side']/10000

    def close_position(price, stamp, reason):
        nonlocal capital, position
        p = position
        fill = price * (1-slip*p['side'])
        exit_fee = fill*p['quantity']*fee
        gross = (fill-p['entry'])*p['side']*p['quantity']
        capital += gross-exit_fee
        trades.append(dict(p, exit=fill, exit_time=stamp.isoformat(), reason=reason,
                           gross_pnl=gross, fees=p['entry_fee']+exit_fee,
                           net_pnl=gross-p['entry_fee']-exit_fee))
        position = None

    for i, (stamp, row) in enumerate(frame.iterrows()):
        previous = sig.iloc[i-1] if i else None
        had_stop = False
        if position:
            side, stop = position['side'], position['stop']
            gap = row.open <= stop if side == 1 else row.open >= stop
            timed = spec['max_holding_bars'] is not None and i-position['bar'] >= spec['max_holding_bars']
            exit_signal = bool(previous['exit_long' if side == 1 else 'exit_short'])
            if gap or timed or exit_signal:
                close_position(float(row.open), stamp, 'gap_stop' if gap else 'time_exit' if timed else 'signal_exit')
                had_stop = bool(gap or timed)
        if position is None and previous is not None and not had_stop and i < len(frame)-1 and capital > 0:
            side = int(previous.entry)
            stop = float(previous['stop_long' if side == 1 else 'stop_short']) if side else float('nan')
            entry = float(row.open)*(1+slip*side)
            distance = abs(entry-stop)
            valid_stop = stop < row.open if side == 1 else stop > row.open
            if side and math.isfinite(stop) and stop > 0 and valid_stop and distance > 0:
                # Include both-side friction in the planned stop budget.
                risk_per_unit = distance + (entry+stop)*fee + stop*slip
                qty = math.floor(min(capital*spec['risk_fraction']/risk_per_unit,
                                     capital*spec['notional_cap_fraction']/(entry*(1+fee))))
                if qty > 0:
                    entry_fee = entry*qty*fee
                    capital -= entry_fee
                    position = dict(side=side, quantity=qty, entry=entry, stop=stop,
                                    entry_time=stamp.isoformat(), signal_time=frame.index[i-1].isoformat(),
                                    entry_fee=entry_fee, bar=i)
        if position:
            side, stop = position['side'], position['stop']
            triggered = row.low <= stop if side == 1 else row.high >= stop
            if triggered:
                close_position(stop, stamp, 'intraday_stop')
        if i == len(frame)-1 and position:
            close_position(float(row.close), stamp, 'window_end')
        unrealized = ((row.close-position['entry'])*position['side']*position['quantity']) if position else 0.
        equity.append(dict(timestamp=stamp.isoformat(), equity=capital+unrealized))
    equity_values = np.array([initial_capital]+[r['equity'] for r in equity])
    dd = np.maximum.accumulate(equity_values)-equity_values
    return dict(model_id=model_id, specification=spec,
                specification_sha256=hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest(),
                evidence_kind='UNDERLYING_PROXY', live_approved=False, independent_forward_evidence=False,
                initial_capital=initial_capital, final_capital=capital, net_pnl=capital-initial_capital,
                max_drawdown=float(dd.max()), trades=trades, equity=equity,
                option_pnl=None, monthly_target_probability=None)


def main(argv=None):
    import argparse
    from pathlib import Path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', choices=sorted(SPECS), required=True)
    parser.add_argument('--csv', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--proposal-id')
    parser.add_argument('--plan', action='store_true')
    args = parser.parse_args(argv)
    if args.plan:
        result = specification(args.model)
    else:
        if not args.csv or not args.proposal_id:
            parser.error('Historical evaluation requires --csv and --proposal-id')
        from research_admission import require_admission
        require_admission(args.proposal_id)
        data = pd.read_csv(args.csv, index_col='timestamp', parse_dates=True)
        data.attrs.update(completed_bars=True, interval='1d')
        result = backtest(args.model, data=data)
        result['research_proposal_id'] = args.proposal_id
    text = json.dumps(result, indent=2, allow_nan=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text+'\n')
    else:
        print(text)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
