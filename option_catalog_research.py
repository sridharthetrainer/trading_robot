"""Causal entry hypotheses and quote-based lifecycle research for the option catalog.

No order placement, no fill assertions. Every result is unvalidated shadow research.
Dynamic plans require an explicit simulated OPEN state; proposals are not fills.
"""
from __future__ import annotations
import math
from datetime import datetime
from zoneinfo import ZoneInfo
from functools import partial
import pandas as pd

IST = ZoneInfo('Asia/Kolkata')
DYNAMIC_IDS = {'C9', 'D5', 'E1', 'E2', 'E3', 'E4', 'E5', 'E6'}
NEW_IDS = ([f'A{i}' for i in range(1,15)] + [f'B{i}' for i in range(1,6)]
           + [f'C{i}' for i in range(2,10) if i != 3] + [f'D{i}' for i in range(2,8)]
           + [f'E{i}' for i in range(1,8)])


def number(value):
    try:
        value=float(value)
        return value if math.isfinite(value) else None
    except (TypeError,ValueError):
        return None


def frame_asof(frame, now, minutes=5):
    if frame is None or len(frame) < 3 or not isinstance(frame.index,pd.DatetimeIndex):
        raise ValueError('timestamped_candles_required')
    df=frame.copy()
    df.columns=[str(c).lower() for c in df.columns]
    if df.columns.has_duplicates or not {'open','high','low','close','volume'}.issubset(df):
        raise ValueError('invalid_candle_columns')
    df.index=df.index.tz_localize(IST) if df.index.tz is None else df.index.tz_convert(IST)
    if not df.index.is_monotonic_increasing or df.index.has_duplicates:
        raise ValueError('unordered_candles')
    # Established cache timestamps identify bar opens unless explicitly marked.
    closes=df.index if frame.attrs.get('timestamp_semantics') == 'close' else df.index+pd.Timedelta(minutes=minutes)
    df=df.loc[closes <= now]
    df=df.between_time('09:15','15:30',inclusive='left')
    values=df[['open','high','low','close','volume']].apply(pd.to_numeric,errors='coerce')
    if len(df) < 3 or values.isna().any().any() or not all(math.isfinite(v) for v in values.to_numpy().ravel()):
        raise ValueError('invalid_or_incomplete_candles')
    if (values[['open','high','low','close']] <= 0).any().any() or (values.volume < 0).any():
        raise ValueError('invalid_candle_prices')
    if ((values.high < values[['open','close','low']].max(axis=1)) | (values.low > values[['open','close','high']].min(axis=1))).any():
        raise ValueError('invalid_candle_geometry')
    df[values.columns]=values
    if df.index[-1].date() != now.date() or (now-df.index[-1]).total_seconds() > minutes*60+180:
        raise ValueError('stale_candles')
    return df


def features(df,now):
    from indicators import calculate_atr,calculate_adx,calculate_supertrend,calculate_vwap,calculate_rsi,calculate_stochastic
    if len(df) < 35:
        raise ValueError('indicator_warmup_required')
    adx=number(calculate_adx(df).iloc[-1])
    atr=number(calculate_atr(df).iloc[-1])
    vwap=number(calculate_vwap(df).iloc[-1])
    rsi=number(calculate_rsi(df.close).iloc[-1])
    _,st=calculate_supertrend(df)
    k,d=calculate_stochastic(df)
    day=df.loc[df.index.date == now.date()]
    old=df.loc[df.index.date < now.date()]
    result={'close':float(df.close.iloc[-1]),'previous':float(df.close.iloc[-2]),
            'ema9':float(df.close.ewm(span=9,adjust=False).mean().iloc[-1]),
            'ema21':float(df.close.ewm(span=21,adjust=False).mean().iloc[-1]),
            'adx':adx,'atr':atr,'vwap':vwap,'rsi':rsi,'st':int(st.iloc[-1]),'day':day,
            'stoch_k':number(k.iloc[-1]),'stoch_d':number(d.iloc[-1]),
            'stoch_previous_k':number(k.iloc[-2]),'stoch_previous_d':number(d.iloc[-2])}
    if any(result[x] is None for x in ['adx','atr','rsi']) or atr <= 0:
        raise ValueError('indicator_evidence_unavailable')
    if len(old):
        previous_day=old.loc[old.index.date == old.index[-1].date()]
        high,low,close=float(previous_day.high.max()),float(previous_day.low.min()),float(previous_day.close.iloc[-1])
        pivot=(high+low+close)/3
        bc=(high+low)/2
        tc=2*pivot-bc
        result.update(pdh=high,pdl=low,cpr_low=min(bc,tc),cpr_high=max(bc,tc))
    return result


def direction_for(strategy_id,f,df,df_htf,now,kw):
    close,prev=f['close'],f['previous']
    if strategy_id in {'A4','A5','A11','C8'} and f['vwap'] is None:
        raise ValueError('vwap_evidence_required')
    bullish=f['ema9'] > f['ema21'] and (f['vwap'] is None or close > f['vwap'])
    bearish=f['ema9'] < f['ema21'] and (f['vwap'] is None or close < f['vwap'])
    trend=1 if bullish else -1 if bearish else 0
    if strategy_id == 'A1':
        opening=f['day'].loc[f['day'].index.time < datetime.strptime('09:30','%H:%M').time()]
        if len(opening) < 3 or now.time() < datetime.strptime('09:30','%H:%M').time():
            raise ValueError('opening_range_incomplete')
        return 1 if prev <= opening.high.max() < close else -1 if close < opening.low.min() <= prev else 0
    if strategy_id in {'A2','A13'}:
        return f['st'] if f['adx'] >= 20 and trend == f['st'] else 0
    if strategy_id in {'A3','A10'}:
        return trend if (df.close.diff().iloc[-3:] * trend > 0).all() else 0
    if strategy_id in {'A4','A11'}:
        if 'cpr_high' not in f:
            raise ValueError('prior_session_cpr_required')
        high=f['pdh'] if strategy_id == 'A11' else f['cpr_high']
        low=f['pdl'] if strategy_id == 'A11' else f['cpr_low']
        return 1 if prev <= high < close and close > f['vwap'] else -1 if close < low <= prev and close < f['vwap'] else 0
    if strategy_id == 'A5':
        return trend if f['adx'] >= 20 and ((trend == 1 and f['rsi'] >= 55) or (trend == -1 and f['rsi'] <= 45)) else 0
    if strategy_id in {'A6','A8'}:
        htf=frame_asof(df_htf,now,15)
        if strategy_id == 'A8':
            fast=htf.close.ewm(span=9,adjust=False).mean().iloc[-1]
            slow=htf.close.ewm(span=21,adjust=False).mean().iloc[-1]
            return trend if f['adx'] >= 20 and (fast-slow)*trend > 0 else 0
        a,b,c=htf.iloc[-3],htf.iloc[-2],htf.iloc[-1]
        return 1 if b.low < a.low and c.close > b.open else -1 if b.high > a.high and c.close < b.open else 0
    if strategy_id == 'A7':
        if any(f[x] is None for x in ['stoch_k','stoch_d','stoch_previous_k','stoch_previous_d']):
            raise ValueError('stochastic_warmup_required')
        mid=df.close.rolling(20).mean(); sd=df.close.rolling(20).std()
        return 1 if prev < mid.iloc[-2]-2*sd.iloc[-2] and close > mid.iloc[-1]-2*sd.iloc[-1] and f['stoch_previous_k'] <= f['stoch_previous_d'] and f['stoch_k'] > f['stoch_d'] else -1 if prev > mid.iloc[-2]+2*sd.iloc[-2] and close < mid.iloc[-1]+2*sd.iloc[-1] and f['stoch_previous_k'] >= f['stoch_previous_d'] and f['stoch_k'] < f['stoch_d'] else 0
    if strategy_id == 'A9':
        return trend if (trend == 1 and df.low.iloc[-2] <= f['ema21'] < close) or (trend == -1 and df.high.iloc[-2] >= f['ema21'] > close) else 0
    if strategy_id == 'C7':
        if 'cpr_high' not in f:
            raise ValueError('prior_session_cpr_required')
        return -1 if close > f['cpr_high'] and close < prev else 1 if close < f['cpr_low'] and close > prev else 0
    if strategy_id == 'C8':
        stretch=(close-f['vwap'])/f['atr']
        return -1 if 1.5 <= stretch < 2.5 and close < prev else 1 if -2.5 < stretch <= -1.5 and close > prev else 0
    if strategy_id in {'A12','A14','B1','B2','B5','E7'}:
        return trend
    if strategy_id == 'B3':
        return 1 if trend == 1 else 0
    if strategy_id == 'B4':
        return -1 if trend == -1 else 0
    return 0


def payoff_bounds(legs):
    """Expiry payoff for same-expiry options; detect both unbounded tails."""
    expiries={leg['expiry'] for leg in legs}
    if len(expiries) != 1 or any(leg.get('instrument','OPTION') != 'OPTION' for leg in legs):
        return None,None
    strikes=[leg['strike'] for leg in legs]
    debit=sum((1 if l['side']=='BUY' else -1)*l['price']*l.get('ratio',1) for l in legs)
    upper_slope=sum((1 if l['side']=='BUY' else -1)*l.get('ratio',1) for l in legs if l['option_type']=='CE')
    values=[]
    for spot in [0.] + strikes:
        gross=sum((1 if l['side']=='BUY' else -1)*l.get('ratio',1)*max(spot-l['strike'] if l['option_type']=='CE' else l['strike']-spot,0.) for l in legs)
        values.append(gross-debit)
    maximum_loss=None if upper_slope < 0 else max(0.,-min(values))
    maximum_profit=None if upper_slope > 0 else max(0.,max(values))
    return maximum_loss,maximum_profit


def evaluate(strategy_id, symbol, df=None,df_htf=None,option_data=None,regime=None,now=None,conn=None,**kw):
    from option_quality import chain_age_sec,extract_chain_rows,extract_spot,filter_expiry_rows,normalize_expiry
    from option_core_strategies import _leg_quote,_delta_of,_strike_gap,_iv_percentile
    from option_strategy_registry import _param
    regime=regime or {}
    now=now or datetime.now(IST)
    now=now.replace(tzinfo=IST) if now.tzinfo is None else now.astimezone(IST)
    base={'id':strategy_id,'evaluation_mode':'shadow','live_ready':False,'unvalidated':True,
          'implementation_scope':'entry_and_quote_lifecycle_research','fills_verified':False}
    def blocked(reason):
        return {**base,'status':'blocked_'+reason,'reason':reason}
    try:
        if now.weekday() >= 5 or not datetime.strptime('09:15','%H:%M').time() <= now.time() < datetime.strptime('15:30','%H:%M').time():
            return blocked('outside_session')
        age=chain_age_sec(option_data)
        if age is None or age > 180:
            return blocked('fresh_quote_evidence_required')
        expiry=normalize_expiry(regime.get('next_expiry'))
        if not expiry or expiry < now.date().isoformat():
            return blocked('known_unexpired_contract_required')
        rows=filter_expiry_rows(extract_chain_rows(option_data),expiry)
        spot=number(extract_spot(option_data))
        if not rows or spot is None or spot <= 0:
            return blocked('chain_data_required')
        expiry_close=datetime.fromisoformat(expiry).replace(hour=15,minute=30,tzinfo=IST)
        t=(expiry_close-now).total_seconds()/(365*86400)
        if t <= 0:
            return blocked('contract_expired')
        gap=_strike_gap(symbol)
        center=round(spot/gap)*gap
        quote_cache=kw.get('research_cache')
        def leg(strike,kind,side,ratio=1,contract_expiry=expiry,contract_rows=None):
            if kind not in {'CE','PE'} or side not in {'BUY','SELL'} or number(ratio) not in {1.,2.}:
                raise ValueError('invalid_option_leg_contract')
            use_rows=rows if contract_rows is None else contract_rows
            cache_key=('contract',id(option_data),contract_expiry,strike,kind)
            cached=quote_cache.get(cache_key) if isinstance(quote_cache,dict) else None
            q=cached[0] if cached else _leg_quote(use_rows,strike,kind)
            if q['bid'] <= 0 or q['ask'] < q['bid'] or q['volume'] <= 0 or q['oi'] <= 0 or q['spread_pct'] > .05:
                raise ValueError('liquid_two_sided_quotes_required')
            years=t if contract_expiry == expiry else (datetime.fromisoformat(contract_expiry).replace(hour=15,minute=30,tzinfo=IST)-now).total_seconds()/(365*86400)
            delta=cached[1] if cached else _delta_of(use_rows,strike,kind,spot,years)
            if delta == 0 or not math.isfinite(delta):
                raise ValueError('valid_contract_greeks_required')
            if isinstance(quote_cache,dict) and cached is None:
                quote_cache[cache_key]=(q,delta)
            return {'strike':strike,'option_type':kind,'side':side,'ratio':ratio,
                    'expiry':contract_expiry,'price':q['ask'] if side=='BUY' else q['bid'],
                    'bid':q['bid'],'ask':q['ask'],'delta':delta,'iv':q['iv']/100 if q['iv']>1 else q['iv'],'volume':q['volume'],'spread_pct':q['spread_pct']}
        def delta_leg(kind,side,target,ratio=1):
            target=number(target)
            if target is None or not 0 < target < 1:
                raise ValueError('finite_delta_target_required')
            choices=[]
            for row in rows:
                strike=number(row.get('strikePrice'))
                if strike is None:
                    continue
                try:
                    candidate=leg(strike,kind,side,ratio)
                    choices.append((abs(abs(candidate['delta'])-target),candidate))
                except ValueError:
                    continue
            if not choices:
                raise ValueError('delta_contract_unavailable')
            deviation,candidate=min(choices,key=lambda item:item[0])
            if deviation > .08:
                raise ValueError('delta_contract_outside_tolerance')
            return candidate
        state=kw.get('shadow_state')
        if strategy_id in DYNAMIC_IDS and isinstance(state,dict) and state.get('status') == 'OPEN':
            if state.get('strategy')!=strategy_id or state.get('symbol')!=symbol or state.get('expiry')!=expiry:
                return blocked('matching_strategy_symbol_expiry_state_required')
            return manage_state(strategy_id,state,leg,delta_leg,{'close':spot,'vix':number(regime.get('vix'))},now,base,kw)
        entries=1
        if strategy_id=='E4' and isinstance(state,dict) and state.get('status')=='CLOSED':
            previous_entries=number(state.get('entries'))
            if state.get('strategy')!='E4' or state.get('symbol')!=symbol or state.get('session')!=now.date().isoformat() or previous_entries is None or previous_entries != int(previous_entries) or not 1 <= previous_entries < 3:
                return blocked('rolling_reentry_cap_or_state_invalid')
            if state.get('close_reason')!='STOP_LOSS' or now.time() >= datetime.strptime('14:30','%H:%M').time():
                return blocked('rolling_reentry_not_permitted')
            entries=int(previous_entries)+1
        cache=kw.get('research_cache')
        cache_key=(id(df),now)
        if isinstance(cache,dict) and cache_key in cache:
            frame,f=cache[cache_key]
        else:
            frame=frame_asof(df,now)
            f=features(frame,now)
            if isinstance(cache,dict):
                cache[cache_key]=(frame,f)
        if regime.get('primary','UNKNOWN') == 'UNKNOWN':
            return blocked('known_regime_required')
        if abs(f['close']-spot)/spot > .01:
            return blocked('spot_candle_mismatch')
        direction=direction_for(strategy_id,f,frame,df_htf,now,kw)
        directional=set(f'A{i}' for i in range(1,15))|{'B1','B2','B3','B4','B5','C7','C8','E7'}
        if strategy_id in directional and direction == 0:
            return blocked('no_causal_trigger')
        if strategy_id in {'A10','C6'} and expiry != now.date().isoformat():
            return blocked('expiry_day_required')
        theta=strategy_id in {'B2','B3','B4','C2','C4','C6','C7','C8','C9','E1','E2','E3','E4','E5','E6'}
        ivp=_iv_percentile(kw.get('gap_risk_manager'),symbol)
        if theta:
            if regime.get('event') or regime.get('gap') or f['adx'] >= 25:
                return blocked('short_volatility_regime_conflict')
            if ivp is None:
                return blocked('iv_percentile_evidence_required')
        legs=[]
        exit_rules={}
        if strategy_id.startswith('A') and strategy_id not in {'A13','A14'}:
            kind='CE' if direction > 0 else 'PE'
            target=float(_param(strategy_id,'delta_target',.5))
            selected=delta_leg(kind,'BUY',target)
            if strategy_id in {'A3','A12'}:
                previous=kw.get('previous_option_data')
                old_age=chain_age_sec(previous)
                if old_age is None or old_age <= age or old_age-age > 900:
                    return blocked('nearby_previous_quote_required')
                old_rows=filter_expiry_rows(extract_chain_rows(previous),expiry)
                old=_leg_quote(old_rows,selected['strike'],kind)
                current=_leg_quote(rows,selected['strike'],kind)
                if old['last_price'] <= 0 or current['last_price'] <= old['last_price'] or current['oi_change'] <= 0:
                    return blocked('premium_oi_momentum_not_confirmed')
            legs=[selected]
            if strategy_id in {'A4','A5','A6','A8','A9','A11'}:
                points=(75 if symbol=='BANKNIFTY' else 25) if strategy_id=='A8' else f['atr']
                risk=max(abs(selected['delta'])*points,selected['price']*.10)
                exit_rules={'premium_stop':max(.01,selected['price']-risk),'premium_target':selected['price']+1.5*risk,
                            'underlying_stop':f['close']-direction*points,'stop_source':'unvalidated_index_distance_translation'}
            else:
                stop_fraction=.10 if strategy_id=='A10' else .15 if strategy_id=='A3' else .25 if strategy_id=='A12' else .30
                target_fraction=.20 if strategy_id=='A10' else .25 if strategy_id=='A3' else .60 if strategy_id=='A12' else .45
                exit_rules={'premium_stop':selected['price']*(1-stop_fraction),'premium_target':selected['price']*(1+target_fraction)}
        elif strategy_id in {'A13','B1','B2','B3','B4','C7','C8'}:
            credit=strategy_id in {'B2','B3','B4','C7','C8'}
            kind=('PE' if direction > 0 else 'CE') if credit else ('CE' if direction > 0 else 'PE')
            if credit:
                short=delta_leg(kind,'SELL',float(_param(strategy_id,'short_delta_target',.22)))
                width=300 if symbol=='BANKNIFTY' else 100
                hedge=leg(short['strike']+(width if kind=='CE' else -width),kind,'BUY')
                legs=[short,hedge]
                exit_rules={'credit_stop_multiple':2.,'credit_target_decay_fraction':.5,'exit_at_vwap':strategy_id=='C8'}
            else:
                long=delta_leg(kind,'BUY',float(_param(strategy_id,'long_delta_target',.5)))
                short=delta_leg(kind,'SELL',.30)
                if (short['strike']-long['strike'])*(1 if kind=='CE' else -1) <= 0:
                    return blocked('ordered_vertical_strikes_required')
                legs=[long,short]
                exit_rules={'debit_stop_fraction':.25,'debit_target_gain_fraction':.50} if strategy_id=='A13' else {'debit_stop_fraction':.40,'target_fraction_of_max_profit':.65}
        elif strategy_id in {'A14','B5','E7'}:
            kind='CE' if direction > 0 else 'PE'
            if strategy_id=='A14':
                legs=[leg(center,kind,'BUY'),leg(center,'PE' if kind=='CE' else 'CE','SELL')]
            else:
                long=delta_leg(kind,'BUY',.5); short=delta_leg(kind,'SELL',.25,2)
                if (short['strike']-long['strike'])*(1 if kind=='CE' else -1) <= 0:
                    return blocked('ordered_ratio_strikes_required')
                legs=[long,short]
                if strategy_id=='E7':
                    width=abs(short['strike']-long['strike'])
                    legs.append(leg(short['strike']+(width if kind=='CE' else -width),kind,'BUY'))
            exit_rules={'underlying_stop':f['close']-direction*.8*f['atr'],'max_daily_reentries':0}
        elif strategy_id in {'C2','C9','E1','E2','E3','E4','E5','E6','C4','C6'}:
            straddle=strategy_id in {'C4','C9','E2','E3','E4'}
            target=.10 if strategy_id=='C6' else .30 if strategy_id=='E6' else .225 if strategy_id=='E5' else .20
            ce=leg(center,'CE','SELL') if straddle else delta_leg('CE','SELL',target)
            pe=leg(center,'PE','SELL') if straddle else delta_leg('PE','SELL',target)
            legs=[ce,pe]
            if strategy_id in {'C4','C6','E6'}:
                width=400 if symbol=='BANKNIFTY' else 150
                legs += [leg(ce['strike']+width,'CE','BUY'),leg(pe['strike']-width,'PE','BUY')]
            if strategy_id=='C2' and any(not 40 <= l['price'] <= 60 for l in legs):
                return blocked('short_premium_band_not_met')
            if strategy_id=='C6':
                if any(not 8 <= l['price'] <= 15 for l in legs[:2]) or any(l['price'] > 3 for l in legs[2:]):
                    return blocked('expiry_premium_and_hedge_caps_not_met')
                if number(regime.get('vix')) is None or regime['vix'] > 18 or abs(spot/f['day'].open.iloc[0]-1) > .01:
                    return blocked('expiry_volatility_gate')
            if strategy_id=='E3' and not valid_future_quote(kw.get('futures_quote'),now,symbol):
                return blocked('executable_futures_hedge_quote_required')
            exit_rules={'credit_stop_multiple':1.5 if strategy_id=='C4' else 2.,'credit_target_decay_fraction':.30 if strategy_id=='C4' else .40 if strategy_id=='C2' else .50,
                        'exit_time':'15:05','max_adjustments':2,'state_required_for_adjustments':True}
        elif strategy_id in {'D2','D3','D4','D5','D6'}:
            if strategy_id=='D2':
                before=number(kw.get('pre_event_iv')); after=number(kw.get('post_event_iv'))
                if not kw.get('event_completed') or before is None or after is None or not 0 < after < before:
                    return blocked('confirmed_event_and_iv_crush_required')
                if ivp is None:
                    return blocked('iv_percentile_evidence_required')
                legs=[delta_leg('CE','SELL',.15),delta_leg('PE','SELL',.15)]
                exit_rules={'credit_stop_multiple':1.5,'credit_target_decay_fraction':.40}
            else:
                if ivp is None or ivp > 30:
                    return blocked('low_iv_evidence_required')
                if strategy_id in {'D3','D5'}:
                    if strategy_id=='D3' and not regime.get('event'):
                        return blocked('known_event_required')
                    legs=[leg(center,'CE','BUY'),leg(center,'PE','BUY')]
                else:
                    recent_atr=number(f['day'].high.sub(f['day'].low).tail(5).mean())
                    if strategy_id=='D4' and (recent_atr is None or recent_atr/f['atr'] > .7):
                        return blocked('compression_not_confirmed')
                    target=.30 if strategy_id=='D6' else .275
                    legs=[delta_leg('CE','BUY',target),delta_leg('PE','BUY',target)]
                    if strategy_id=='D6':
                        legs += [delta_leg('CE','SELL',.15),delta_leg('PE','SELL',.15)]
                if strategy_id=='D5' and not valid_future_quote(kw.get('futures_quote'),now,symbol):
                    return blocked('executable_futures_hedge_quote_required')
                exit_rules={'debit_stop_fraction':.40 if strategy_id=='D6' else .30,'debit_target_gain_fraction':1. if strategy_id in {'D3','D6'} else .60,
                            'exit_time':'15:05','state_required_for_adjustments':strategy_id=='D5'}
        elif strategy_id in {'C5','D7'}:
            ivrank=number(kw.get('iv_rank'))
            if ivrank is None or not 0 <= ivrank <= 30:
                return blocked('low_iv_rank_evidence_required')
            allrows=extract_chain_rows(option_data)
            future_expiries=sorted({normalize_expiry(r.get('expiryDate',r.get('expiry',''))) for r in allrows} - {None,'',expiry})
            future_expiries=[e for e in future_expiries if e > expiry]
            if not future_expiries:
                return blocked('fresh_second_expiry_quotes_required')
            later=future_expiries[0]
            later_rows=filter_expiry_rows(allrows,later)
            short=leg(center,'CE','SELL')
            long_strike=center if strategy_id=='C5' else center+gap
            long=leg(long_strike,'CE','BUY',contract_expiry=later,contract_rows=later_rows)
            legs=[short,long]
            exit_rules={'exit_time':'15:00','cross_expiry_risk_requires_scenario_validation':True}
        else:
            return blocked('unsupported_research_contract')
        debit=sum((1 if l['side']=='BUY' else -1)*l['price']*l.get('ratio',1) for l in legs)
        max_loss,max_profit=payoff_bounds(legs)
        defined=strategy_id in {'A13','B1','B2','B3','B4','C4','C6','C7','C8','D6','E6','E7'}
        if defined and (max_loss is None or max_loss <= 0 or max_profit is None or max_profit <= 0):
            return blocked('valid_bounded_payoff_required')
        if strategy_id in {'A13','B1','D3','D4','D5','D6','C5','D7'} and debit <= 0:
            return blocked('positive_net_debit_required')
        if theta or strategy_id=='D2':
            if debit >= 0:
                return blocked('positive_net_credit_required')
        # Quote-side liquidation cost is known; fees are a separate estimate.
        spread_drag=sum((l['ask']-l['bid'])*l.get('ratio',1) for l in legs)
        from nse_master import DEFAULT_LOT_SIZES
        quantity=number(kw.get('lot_size',DEFAULT_LOT_SIZES.get(symbol)))
        if quantity is None or quantity < 1 or quantity != int(quantity):
            return blocked('known_positive_lot_size_required')
        from nse_cost_model import get_cost_model
        model=get_cost_model()
        fees=0.
        for item in legs:
            opposite='SELL' if item['side']=='BUY' else 'BUY'
            exit_quote=item['bid'] if opposite=='SELL' else item['ask']
            for price,side in [(item['price'],item['side']),(exit_quote,opposite)]:
                fees += model.single_leg_cost(price*quantity*item.get('ratio',1),'OPT_BUY' if side=='BUY' else 'OPT_SELL',side,symbol,include_slippage=False,trade_date=now.date()).total
        fee_per_unit=fees/quantity
        if not math.isfinite(fees) or not math.isfinite(fee_per_unit):
            return blocked('finite_cost_estimate_required')
        if max_profit is not None and max_profit <= spread_drag:
            return blocked('quoted_payoff_cannot_cover_spread')
        if max_profit is not None and max_profit <= fee_per_unit:
            return blocked('quoted_payoff_cannot_cover_fees')
        if len(legs)==1 and exit_rules.get('premium_target') is not None:
            if exit_rules['premium_target']-legs[0]['price'] <= fee_per_unit+spread_drag:
                return blocked('single_leg_target_cannot_cover_cost_hurdle')
        metadata={'regime':regime,'unvalidated':True,'execution_mode':'shadow_research','fills_verified':False,
                  'lifecycle_automation':'proposal_only_requires_explicit_open_simulation_state',
                  'exit_rules':exit_rules,'gross_payoff_before_fees':True}
        metadata.update(estimated_round_trip_fees=fees,quantity=quantity,lot_size_source='explicit_or_master_default')
        result={**base,'status':'selected','legs':legs,'net_debit':max(debit,0.),'net_credit':max(-debit,0.),
                'max_loss_per_unit':max_loss,'max_profit_per_unit':max_profit,'exit_rules':exit_rules,
                'estimated_round_trip_fees':fees,'quantity':quantity,
                'research_state_template':{'status':'PROPOSED','strategy':strategy_id,'symbol':symbol,'session':now.date().isoformat(),'expiry':expiry,'legs':legs,'entry_spot':spot,'option_quantity':int(quantity),'entries':entries,'adjustments':0,'realized_pnl_per_unit':0.}}
        if strategy_id in {'C5','D7'}:
            # Cross-expiry payoff is a structure quote, not a bounded trade.
            result['status']='research_structure_quote'
        if kw.get('journal',True):
            import option_decision_journal as journal
            selected_payload={'legs':legs if len(legs)>1 else [],'expiry':expiry,'net_debit':result['net_debit'],'net_credit':result['net_credit'],'max_loss':max_loss}
            if len(legs)==1:
                selected_payload.update(strike=legs[0]['strike'],option_type=legs[0]['option_type'],premium=legs[0]['price'],
                                        trade_side=legs[0]['side'],underlying_direction='BULLISH' if direction>0 else 'BEARISH',
                                        stop_loss=exit_rules.get('premium_stop'),target_price=exit_rules.get('premium_target'))
            journal.record_option_decision(strategy=strategy_id,symbol=symbol,decision='selected' if result['status']=='selected' else 'research_structure_quote',
                side='BUY' if debit > 0 else 'SELL',spot=spot,selected=selected_payload,metadata=metadata)
        if conn is not None and result['status']=='selected':
            from option_core_strategies import _persist_shadow_legs
            _persist_shadow_legs(conn,legs,symbol=symbol,expiry=expiry,strategy=strategy_id,
                                 combo_id=f'{symbol}_{strategy_id}_{now.isoformat()}',side='BUY' if debit > 0 else 'SELL',snapshot_time=now.isoformat())
        return result
    except (ValueError,TypeError,KeyError,IndexError) as exc:
        return blocked(str(exc))


def valid_future_quote(quote,now,symbol):
    if not isinstance(quote,dict) or quote.get('symbol') != symbol or quote.get('instrument') != 'FUT':
        return False
    bid,ask=number(quote.get('bid')),number(quote.get('ask'))
    stamp=number(quote.get('ts'))
    lot=number(quote.get('lot_size'))
    from option_quality import normalize_expiry
    expiry=normalize_expiry(quote.get('expiry'))
    return bid is not None and ask is not None and 0 < bid <= ask and stamp is not None and 0 <= now.timestamp()-stamp <= 30 and lot is not None and lot > 0 and lot==int(lot) and bool(quote.get('tradingsymbol')) and bool(expiry) and expiry>=now.date().isoformat()


def manage_state(strategy_id,state,leg,delta_leg,f,now,base,kw):
    """Pure next-action calculator. It cannot turn a proposal into a fill."""
    def block(reason):
        return {**base,'status':'blocked_'+reason,'reason':reason}
    if state.get('strategy') != strategy_id or state.get('session') != now.date().isoformat() or not state.get('legs'):
        return block('matching_open_simulation_state_required')
    marked=[]; unrealized=0.; entry_credit=0.
    for old in state['legs']:
        if number(old.get('price')) is None or number(old.get('price')) <= 0 or old.get('side') not in {'BUY','SELL'}:
            return block('finite_positive_entry_prices_required')
        if old.get('expiry') != state.get('expiry'):
            return block('state_expiry_mismatch')
        current=leg(old['strike'],old['option_type'],'SELL' if old['side']=='BUY' else 'BUY',old.get('ratio',1))
        sign=1 if old['side']=='BUY' else -1
        unrealized += sign*(current['price']-old['price'])*old.get('ratio',1)
        entry_credit -= sign*old['price']*old.get('ratio',1)
        marked.append((old,current))
    realized=number(state.get('realized_pnl_per_unit',0))
    fees=number(state.get('estimated_costs_per_unit'))
    if realized is None or fees is None or fees < 0:
        return block('finite_cost_and_realized_pnl_state_required')
    net=unrealized+realized-fees
    future=None
    held=0.
    quantity=None
    if strategy_id in {'D5','E3'}:
        held=number(state.get('futures_hedge_lots',0))
        if held is None or held != int(held):
            return block('finite_hedge_position_required')
        if held:
            future=kw.get('futures_quote')
            quantity=number(state.get('option_quantity'))
            entry=number(state.get('futures_entry_price'))
            if not valid_future_quote(future,now,state.get('symbol')) or future.get('tradingsymbol')!=state.get('futures_contract') or quantity is None or quantity < 1 or entry is None or entry <= 0:
                return block('matching_futures_position_quote_required')
            liquidation=future['bid'] if held > 0 else future['ask']
            net+=(liquidation-entry)*held*future['lot_size']/quantity
    if not math.isfinite(net):
        return block('finite_marked_pnl_required')
    stop=number(state.get('loss_limit_per_unit')); target=number(state.get('profit_target_per_unit'))
    if stop is None or target is None or not stop < 0 < target:
        return block('explicit_simulation_stop_and_target_required')
    if now.time() >= datetime.strptime('15:05','%H:%M').time() or net <= stop or net >= target:
        return {**base,'status':'research_close_proposal','action':'FLATTEN','net_marked_pnl_per_unit':net,'quote_legs':[x[1] for x in marked],
                'futures_close_proposal':{'lots':-held,'contract':state.get('futures_contract')} if held else None,'state_mutated':False}
    adjustments=number(state.get('adjustments',0))
    if adjustments is None or adjustments < 0 or adjustments != int(adjustments):
        return block('invalid_adjustment_count')
    if strategy_id in {'D5','E3'}:
        future=kw.get('futures_quote')
        if not valid_future_quote(future,now,state.get('symbol')):
            return block('executable_futures_hedge_quote_required')
        current_delta=sum((1 if a['side']=='BUY' else -1)*b['delta']*a.get('ratio',1) for a,b in marked)
        quantity=number(state.get('option_quantity'))
        if held is None or held != int(held) or quantity is None or quantity < 1 or quantity != int(quantity):
            return block('finite_hedge_position_required')
        target_lots=-current_delta*quantity/future['lot_size']
        if not math.isfinite(target_lots):
            return block('finite_hedge_target_required')
        if abs(target_lots-held) > .15 and adjustments < 6:
            return {**base,'status':'research_hedge_proposal','action':'HEDGE','target_futures_lots':target_lots,'whole_lot_execution_supported':False,'state_mutated':False}
        if abs(target_lots-held) > .15 and adjustments >= 6:
            return {**base,'status':'research_close_proposal','action':'FLATTEN','reason':'hedge_cap_reached','net_marked_pnl_per_unit':net,'quote_legs':[x[1] for x in marked],
                    'futures_close_proposal':{'lots':-held,'contract':state.get('futures_contract')} if held else None,'state_mutated':False}
        return {**base,'status':'research_hold','net_marked_pnl_per_unit':net,'state_mutated':False}
    threatened=[(old,current) for old,current in marked if old['side']=='SELL' and (abs(current['delta']) >= .35 or current['price'] >= old['price']*1.25)]
    if not threatened:
        return {**base,'status':'research_hold','net_marked_pnl_per_unit':net,'state_mutated':False}
    if adjustments >= (1 if strategy_id=='E6' else 2):
        return {**base,'status':'research_close_proposal','action':'FLATTEN','reason':'adjustment_cap_reached','quote_legs':[x[1] for x in marked],'net_marked_pnl_per_unit':net,'state_mutated':False}
    before=number(kw.get('margin_before')); after=number(kw.get('margin_after'))
    if before is None or after is None or not 0 <= after <= before:
        return block('nonincreasing_margin_evidence_required')
    old,_=threatened[0]
    replacement=delta_leg(old['option_type'],'SELL',.25,old.get('ratio',1))
    if replacement['strike']==old['strike']:
        return {**base,'status':'research_hold','net_marked_pnl_per_unit':net,'state_mutated':False}
    proposed=[replacement if l is old else dict(l) for l in state['legs']]
    new_loss,_=payoff_bounds(proposed)
    old_loss=number(state.get('risk_cap_per_unit',state.get('max_loss_per_unit')))
    if strategy_id=='E6':
        extra_cost=number(kw.get('estimated_adjustment_cost_per_unit'))
        if extra_cost is None or extra_cost < 0:
            return block('adjustment_cost_estimate_required')
        closed=next(cur for original,cur in marked if original is old)
        closing_pnl=-(closed['price']-old['price'])*old.get('ratio',1)
        effective_loss=None if new_loss is None else new_loss-realized-closing_pnl+fees+extra_cost
        if old_loss is None or effective_loss is None or effective_loss > old_loss:
            return block('adjustment_must_preserve_defined_risk')
    return {**base,'status':'research_roll_proposal','action':'ROLL','close_leg':next(cur for original,cur in marked if original is old),
            'replacement_leg':replacement,'net_marked_pnl_per_unit':net,'maximum_adjustments':2,'state_mutated':False}


def evaluators():
    return {name:partial(evaluate,name) for name in NEW_IDS}
