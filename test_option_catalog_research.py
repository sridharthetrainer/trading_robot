import copy
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import math
import sqlite3
import time
import pandas as pd
import pytest
import option_catalog_research as research
from quant_models import black_scholes

IST=ZoneInfo('Asia/Kolkata')
NOW=datetime(2026,10,14,10,30,tzinfo=IST)


def chain(expiry='2026-10-20',days=6):
    rows=[]
    for strike in range(19000,25001,50):
        row={'strikePrice':strike,'expiryDate':expiry}
        for kind,opt in [('CE','call'),('PE','put')]:
            price=max(1.,black_scholes(22000,strike,days/365,.065,.14,opt)['price'])
            row[kind]={'lastPrice':price,'bidprice':price*.99,'askPrice':price*1.01,'openInterest':50000,'changeinOpenInterest':5000,'totalTradedVolume':10000,'impliedVolatility':14.}
        rows.append(row)
    return {'spot':22000,'ts':time.time(),'chain':rows}


def frame():
    index=list(pd.date_range('2026-10-13 09:15',periods=75,freq='5min',tz=IST))+list(pd.date_range('2026-10-14 09:15',periods=16,freq='5min',tz=IST))
    prices=[22000+math.sin(i/3)*10 for i in range(len(index))]
    return pd.DataFrame({'open':prices,'high':[p+4 for p in prices],'low':[p-4 for p in prices],'close':prices,'volume':1000.},index=index)


def fake_features(df,now):
    return {'close':22000.,'previous':22002.,'ema9':22002.,'ema21':21995.,'adx':18.,'atr':80.,'vwap':21900.,'rsi':60.,'st':1,
            'day':pd.DataFrame({'open':[22000.],'high':[22001.],'low':[21999.]}),'pdh':22050.,'pdl':21950.,'cpr_high':22010.,'cpr_low':21990.}


@pytest.mark.parametrize('strategy_id',research.NEW_IDS)
def test_every_new_strategy_has_specific_quote_research_branch(strategy_id,monkeypatch):
    monkeypatch.setattr(research,'features',fake_features)
    monkeypatch.setattr(research,'direction_for',lambda *a:1)
    data=chain()
    if strategy_id in {'C5','D7'}:
        data['chain'] += chain('2026-10-27',13)['chain']
    ivp=20 if strategy_id.startswith('D') and strategy_id!='D2' else 60
    regime={'primary':'RANGE','next_expiry':'2026-10-20','days_to_expiry':6,'vix':14.,'event':strategy_id=='D3'}
    future={'symbol':'NIFTY','instrument':'FUT','bid':22000.,'ask':22001.,'ts':NOW.timestamp(),'lot_size':65}
    result=research.evaluate(strategy_id,'NIFTY',df=frame(),df_htf=frame(),option_data=data,regime=regime,now=NOW,journal=False,
                             gap_risk_manager=SimpleNamespace(get_iv_percentile=lambda _:ivp),iv_rank=20,
                             event_completed=True,pre_event_iv=20,post_event_iv=15,futures_quote=future)
    assert result['status'] not in {'error','blocked_unsupported_research_contract'}, result
    assert not result['live_ready'] and not result['fills_verified']
    if result['status']=='selected':
        assert result['legs'] and result['research_state_template']['status']=='PROPOSED'
        assert result['estimated_round_trip_fees'] > 0
        assert all(l['bid'] <= l['ask'] for l in result['legs'])
        assert result['max_loss_per_unit'] is None or result['max_loss_per_unit'] >= 0


@pytest.mark.parametrize('strategy_id',research.NEW_IDS)
def test_all_research_entries_fail_closed_without_quotes(strategy_id):
    result=research.evaluate(strategy_id,'NIFTY',now=NOW,journal=False)
    assert result['status'].startswith('blocked_')


def test_causal_frame_excludes_unfinished_poisoned_bar():
    data=frame()
    clean=research.frame_asof(data,NOW)
    poisoned=data.copy()
    poisoned.iloc[-1]=[1e9,1e9,1e9,1e9,1e9]
    pd.testing.assert_frame_equal(clean,research.frame_asof(poisoned,NOW))


def test_payoff_bounds_detect_unhedged_call_tail_and_ratio_hedge():
    def leg(k,side,ratio,price):
        return {'expiry':'2026-10-20','strike':k,'option_type':'CE','side':side,'ratio':ratio,'price':price}
    naked=[leg(100,'BUY',1,10),leg(110,'SELL',2,3)]
    assert research.payoff_bounds(naked)[0] is None
    hedged=naked+[leg(120,'BUY',1,1)]
    loss,profit=research.payoff_bounds(hedged)
    assert loss==5 and profit==5


def test_open_state_can_flatten_without_entry_indicators_or_regime():
    data=chain()
    entry=data['chain'][60]['CE']['lastPrice']
    state={'status':'OPEN','strategy':'E2','symbol':'NIFTY','session':NOW.date().isoformat(),'expiry':'2026-10-20',
           'legs':[{'strike':22000,'option_type':'CE','side':'SELL','ratio':1,'price':entry,'expiry':'2026-10-20'}],
           'estimated_costs_per_unit':1.,'loss_limit_per_unit':-30.,'profit_target_per_unit':30.}
    result=research.evaluate('E2','NIFTY',option_data=data,regime={'next_expiry':'2026-10-20','days_to_expiry':6},
                             now=NOW.replace(hour=15,minute=6),shadow_state=state,journal=False)
    assert result['status']=='research_close_proposal'
    assert result['state_mutated'] is False and state['status']=='OPEN'


def test_nan_state_cannot_generate_profitable_exit():
    state={'status':'OPEN','strategy':'E2','symbol':'NIFTY','session':NOW.date().isoformat(),'expiry':'2026-10-20',
           'legs':[{'strike':22000,'option_type':'CE','side':'SELL','price':float('nan'),'expiry':'2026-10-20'}]}
    result=research.evaluate('E2','NIFTY',option_data=chain(),regime={'next_expiry':'2026-10-20','days_to_expiry':6},now=NOW,shadow_state=state,journal=False)
    assert result['reason']=='finite_positive_entry_prices_required'


def test_calendar_quote_does_not_claim_defined_expiry_payoff(monkeypatch):
    monkeypatch.setattr(research,'features',fake_features)
    data=chain(); data['chain']+=chain('2026-10-27',13)['chain']
    result=research.evaluate('C5','NIFTY',df=frame(),option_data=data,regime={'primary':'RANGE','next_expiry':'2026-10-20','days_to_expiry':6},now=NOW,iv_rank=20,journal=False)
    assert result['status']=='research_structure_quote'
    assert result['max_loss_per_unit'] is None


def test_shadow_leg_persistence_preserves_individual_sides_and_ratios():
    from option_core_strategies import _persist_shadow_legs
    conn=sqlite3.connect(':memory:')
    legs=[{'strike':22000,'option_type':'CE','side':'BUY','price':100,'ratio':1}, {'strike':22100,'option_type':'CE','side':'SELL','price':50,'ratio':2}]
    _persist_shadow_legs(conn,legs,symbol='NIFTY',expiry='2026-10-20',strategy='B5',combo_id='test',side='BUY',snapshot_time=NOW.isoformat())
    assert conn.execute('SELECT side,leg_ratio FROM option_strike_signals ORDER BY rowid').fetchall()==[('BUY',1),('SELL',2)]


def test_weekday_windows_and_rolling_entry_limits(monkeypatch):
    import option_strategy_registry as registry
    assert registry.entry_window_ok('mon_10:00-11:30_or_tue_13:00-14:30',datetime(2026,10,12,10,30,tzinfo=IST))
    assert not registry.entry_window_ok('mon_10:00-11:30_or_tue_13:00-14:30',NOW)
    monkeypatch.setattr(research,'features',fake_features)
    state={'status':'CLOSED','strategy':'E4','symbol':'NIFTY','session':NOW.date().isoformat(),'entries':3,'close_reason':'STOP_LOSS'}
    result=research.evaluate('E4','NIFTY',df=frame(),option_data=chain(),regime={'primary':'RANGE','next_expiry':'2026-10-20','days_to_expiry':6},now=NOW,shadow_state=state,journal=False)
    assert result['reason']=='rolling_reentry_cap_or_state_invalid'


def test_feature_cache_is_scoped_to_a_catalog_call(monkeypatch):
    calls=[]
    def counted(df,now):
        calls.append(1)
        return fake_features(df,now)
    monkeypatch.setattr(research,'features',counted)
    cache={}; data=frame(); quotes=chain()
    kw=dict(symbol='NIFTY',df=data,option_data=quotes,regime={'primary':'RANGE','next_expiry':'2026-10-20','days_to_expiry':6},now=NOW,journal=False,research_cache=cache)
    research.evaluate('D3',**kw)
    research.evaluate('D4',**kw)
    assert len(calls)==1


def test_declared_delta_parameter_cannot_be_nan(monkeypatch):
    monkeypatch.setattr(research,'features',fake_features)
    monkeypatch.setattr(research,'direction_for',lambda *a:1)
    monkeypatch.setenv('OPT_STRAT_A2_DELTA_TARGET','nan')
    result=research.evaluate('A2','NIFTY',df=frame(),option_data=chain(),regime={'primary':'RANGE','next_expiry':'2026-10-20','days_to_expiry':6},now=NOW,journal=False)
    assert result['reason']=='finite_delta_target_required'


def test_foreign_symbol_state_cannot_be_marked_with_current_quotes():
    state={'status':'OPEN','strategy':'E2','symbol':'BANKNIFTY','expiry':'2026-10-20'}
    result=research.evaluate('E2','NIFTY',option_data=chain(),regime={'next_expiry':'2026-10-20'},now=NOW,shadow_state=state,journal=False)
    assert result['reason']=='matching_strategy_symbol_expiry_state_required'


def test_futures_pnl_is_included_before_portfolio_close_decision():
    quote={'symbol':'NIFTY','instrument':'FUT','tradingsymbol':'NIFTYOCTFUT','expiry':'2026-10-27','bid':21950.,'ask':21951.,'ts':NOW.timestamp(),'lot_size':65}
    state={'status':'OPEN','strategy':'E3','symbol':'NIFTY','session':NOW.date().isoformat(),'expiry':'2026-10-20',
           'legs':[{'strike':22000,'option_type':'CE','side':'BUY','price':100.,'expiry':'2026-10-20'}],
           'estimated_costs_per_unit':1.,'loss_limit_per_unit':-30.,'profit_target_per_unit':100.,'option_quantity':65,
           'futures_hedge_lots':1,'futures_entry_price':22000.,'futures_contract':'NIFTYOCTFUT'}
    def leg(*args):
        return {'price':110.,'delta':.5}
    result=research.manage_state('E3',state,leg,None,{},NOW,{'id':'E3','live_ready':False},{'futures_quote':quote})
    assert result['status']=='research_close_proposal'
    assert result['net_marked_pnl_per_unit']==-41
    assert result['futures_close_proposal']['lots']==-1


def test_threatened_position_is_flattened_after_adjustment_cap():
    state={'strategy':'E2','symbol':'NIFTY','session':NOW.date().isoformat(),'expiry':'2026-10-20',
           'legs':[{'strike':22000,'option_type':'CE','side':'SELL','price':100.,'expiry':'2026-10-20'}],
           'estimated_costs_per_unit':1.,'loss_limit_per_unit':-100.,'profit_target_per_unit':100.,'adjustments':2}
    result=research.manage_state('E2',state,lambda *a:{'price':105.,'delta':.45},None,{},NOW,{'id':'E2'}, {})
    assert result['status']=='research_close_proposal'
    assert result['reason']=='adjustment_cap_reached'


def test_schema_upgrade_does_not_invent_buy_stops_for_short_catalog_leg():
    from option_core_strategies import _persist_shadow_legs
    from option_multistrike_signals import ensure_multistrike_schema
    conn=sqlite3.connect(':memory:')
    _persist_shadow_legs(conn,[{'strike':22000,'option_type':'CE','side':'SELL','price':100.,'ratio':2,'spread_pct':.02}],symbol='NIFTY',expiry='2026-10-20',strategy='B5',combo_id='combo',side='SELL',snapshot_time=NOW.isoformat())
    ensure_multistrike_schema(conn)
    row=conn.execute('SELECT side,entry_price,stop_loss,target_1,leg_ratio FROM option_strike_signals').fetchone()
    assert row==('SELL',0.,0.,0.,2)
