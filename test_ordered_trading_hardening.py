import copy
import json
from datetime import datetime,timedelta
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from quant_research_adapters import backtest, signals, specification
from option_research_data import ResearchOptionStore, validate_quote, validate_contract
from option_execution_simulator import replay_limit_buy
from option_portfolio_gate import evaluate_option_portfolio,OptionRiskLimits
from strategy_probability_report import performance_report


def daily(n=260):
    close=np.arange(n,dtype=float)+100
    frame=pd.DataFrame(dict(open=close,high=close+2,low=close-2,close=close),
                       index=pd.bdate_range('2024-01-01',periods=n))
    frame.attrs.update(interval='1d',completed_bars=True)
    return frame


def test_perfect_order_gate_waits_five_complete_bars_then_next_open():
    frame=daily()
    signal=signals('Q01',frame)
    assert signal.entry.iloc[:203].eq(0).all()
    assert signal.entry.iloc[203]==1
    result=backtest('Q01',data=frame)
    assert len(result['trades'])==1
    assert result['trades'][0]['signal_time']==frame.index[203].isoformat()
    assert result['trades'][0]['entry_time']==frame.index[204].isoformat()
    assert result['trades'][0]['entry']>frame.open.iloc[204]
    assert result['trades'][0]['stop']==frame.low.iloc[203]
    assert result['trades'][0]['reason']=='window_end'
    assert result['net_pnl']==pytest.approx(sum(t['net_pnl'] for t in result['trades']))
    assert result['option_pnl'] is None and not result['live_approved']


@pytest.mark.parametrize('model',['Q01','Q06'])
def test_future_prices_cannot_change_past_signals(model):
    frame=daily()
    short=signals(model,frame.iloc[:220])
    changed=frame.copy();changed.iloc[220:]=changed.iloc[220:]*3
    pd.testing.assert_frame_equal(short,signals(model,changed).iloc[:220])


def test_hybrid_uses_next_open_and_explicit_proxy_costs():
    frame=daily(65)
    close=np.full(65,100.);close[0]=90;close[10]=110
    frame[['open','close']]=np.column_stack([close,close])
    frame['high']=close+2;frame['low']=close-2
    result=backtest('Q06',data=frame)
    trade=result['trades'][0]
    assert trade['signal_time']==frame.index[40].isoformat()
    assert trade['entry_time']==frame.index[41].isoformat()
    assert trade['quantity']*trade['entry']<=500000
    assert trade['fees']>0
    assert result['net_pnl']==pytest.approx(sum(t['net_pnl'] for t in result['trades']))


def test_daily_adapter_rejects_unknown_bar_completion_and_intraday():
    frame=daily();frame.attrs={}
    with pytest.raises(ValueError,match='completed_bars'):backtest('Q01',data=frame)
    frame=daily();frame.index=pd.date_range('2024-01-01',periods=len(frame),freq='5min')
    with pytest.raises(ValueError,match='session'):backtest('Q01',data=frame)


def test_gap_beyond_signal_stop_skips_entry():
    frame=daily()
    frame.iloc[204,frame.columns.get_loc('open')]=frame.low.iloc[203]-2
    frame.iloc[204,frame.columns.get_loc('low')]=frame.open.iloc[204]-1
    result=backtest('Q01',data=frame)
    assert result['trades']==[]


@pytest.fixture
def contract():
    return dict(contract='TEST29OCT26CE',exchange='NFO',underlying='NIFTY',expiry='2026-10-29',
                option_type='CE',strike=25000.,lot_size=65,tick_size=.05,
                effective_from='2026-10-01',effective_to='2026-10-29',source='dated_fixture')


@pytest.fixture
def quote(contract):
    return dict(contract=contract['contract'],exchange='NFO',source='fixture_feed',
                exchange_timestamp='2026-10-15T10:00:01+05:30',received_at='2026-10-15T10:00:01+05:30',
                bid=99.95,ask=100.,bid_quantity=130,ask_quantity=65)


def test_dated_metadata_import_is_idempotent_and_rejects_conflicts(tmp_path,contract,quote):
    store=ResearchOptionStore(tmp_path/'quotes.db')
    assert store.add_contract(contract)
    assert not store.add_contract(contract)
    changed=dict(contract,lot_size=75)
    with pytest.raises(ValueError,match='overlapping'):store.add_contract(changed)
    assert store.add_quote(quote)
    assert not store.add_quote(quote)
    assert store.coverage()['observed_quotes']==1


def test_import_never_guesses_historical_lot_size(tmp_path,quote):
    store=ResearchOptionStore(tmp_path/'quotes.db')
    with pytest.raises(ValueError,match='No dated'):store.add_quote(quote)


@pytest.mark.parametrize('change',['naive','stale','future','crossed','zero_quantity','tick','metadata_date'])
def test_quote_validation_fails_closed(contract,quote,change):
    now='2026-10-15T10:00:02+05:30'
    if change=='naive':quote['exchange_timestamp']='2026-10-15T10:00:01'
    if change=='stale':quote['exchange_timestamp']='2026-10-15T09:59:00+05:30'
    if change=='future':quote['exchange_timestamp']='2026-10-15T10:01:00+05:30'
    if change=='crossed':quote['bid']=101.
    if change=='zero_quantity':quote['ask_quantity']=0
    if change=='tick':quote['ask']=100.03
    if change=='metadata_date':contract['effective_from']='2026-10-16'
    with pytest.raises(ValueError):validate_quote(quote,contract,as_of=now)


def replay(contract,events,**kw):
    return replay_limit_buy(contract=contract,quantity=130,limit=100.,
                            confirmed_at='2026-10-15T10:00:00+05:30',
                            expires_at='2026-10-15T10:01:00+05:30',events=events,**kw)


def event(quote,stamp=None):
    return dict(type='quote',time=stamp or quote['received_at'],quote=quote)


def test_partial_fill_never_reuses_repeated_depth(contract,quote):
    duplicate=dict(quote,received_at='2026-10-15T10:00:02+05:30')
    result=replay(contract,[event(quote),event(duplicate)])
    assert result['filled_quantity']==65 and result['remaining_quantity']==65
    assert result['state']=='PARTIAL' and result['reconciliation_required']
    assert result['average_price']==100 and result['modeled_entry_fees']>0
    assert 'duplicate_quote' in result['skipped']


def test_two_new_quotes_fill_only_approved_quantity(contract,quote):
    new=dict(quote,ask_quantity=130,received_at='2026-10-15T10:00:03+05:30',exchange_timestamp='2026-10-15T10:00:03+05:30')
    result=replay(contract,[event(quote),event(new)])
    assert result['filled_quantity']==130 and result['state']=='FILLED'
    assert not result['actual_fills_verified']


@pytest.mark.parametrize('kind',['reject','cancel_ack','submission_timeout'])
def test_terminal_replay_events_never_fill_later(contract,quote,kind):
    result=replay(contract,[dict(type=kind,time='2026-10-15T10:00:00+05:30'),event(quote)])
    assert result['filled_quantity']==0
    assert result['average_price'] is None


def test_disconnect_and_delayed_confirmation_cannot_fabricate_fill(contract,quote):
    result=replay(contract,[dict(type='disconnect',time='2026-10-15T10:00:00+05:30'),event(quote)])
    assert result['filled_quantity']==0
    result=replay(contract,[event(quote)],latency_ms=2000)
    assert result['filled_quantity']==0


def test_portfolio_gate_counts_full_premium_and_never_resizes():
    now=datetime.fromisoformat('2026-10-15T10:00:00+05:30')
    plan=dict(side='BUY',product='INTRADAY',quantity=65,limit=100.,correlation_group='INDEX')
    result=evaluate_option_portfolio(plan,[],daily_pnl=0,snapshot_at=now,now=now)
    assert result['allowed'] and result['approved_quantity']==65 and result['total_premium_at_risk']==6500
    position=dict(side='BUY',premium_at_risk=5000,correlation_group='INDEX',opened_at=now.isoformat())
    assert not evaluate_option_portfolio(plan,[position],daily_pnl=0,snapshot_at=now,now=now)['allowed']


@pytest.mark.parametrize('fault',['daily','overnight','stale','cutoff','nan','aggregate'])
def test_portfolio_gate_blocks_bad_evidence(fault):
    now=datetime.fromisoformat('2026-10-15T10:00:00+05:30');stamp=now
    plan=dict(side='BUY',product='INTRADAY',quantity=65,limit=100.,correlation_group='INDEX')
    positions=[];pnl=0
    if fault=='daily':pnl=-5000
    if fault=='overnight':positions=[dict(side='BUY',premium_at_risk=1000,correlation_group='OTHER',opened_at=(now-timedelta(days=1)).isoformat())]
    if fault=='stale':stamp=now-timedelta(seconds=31)
    if fault=='cutoff':now=now.replace(hour=15,minute=10);stamp=now
    if fault=='nan':plan['limit']=float('nan')
    if fault=='aggregate':positions=[dict(side='BUY',premium_at_risk=49000,correlation_group='OTHER',opened_at=now.isoformat())]
    assert not evaluate_option_portfolio(plan,positions,daily_pnl=pnl,snapshot_at=stamp,now=now)['allowed']


def test_probability_is_unknown_for_model_or_sparse_outcomes():
    result=performance_report([dict(date='2026-10-01',net_pnl=1000,closed_trades=1,complete_session=True)])
    assert result['monthly_target_probability'] is None
    assert result['blocks'] and not result['live_approved']


def probability_fixture():
    dates=pd.bdate_range('2024-01-01','2024-12-31')
    rows=[dict(date=d.date().isoformat(),net_pnl=100.,closed_trades=1,complete_session=True) for d in dates]
    trades=[dict(trade_id=str(i),net_pnl=100.) for i in range(len(rows))]
    return rows,trades


def test_conditional_probability_requires_reconciled_qualified_evidence():
    rows,trades=probability_fixture()
    kwargs=dict(evidence_kind='OBSERVED_OPTION_NET_DAILY',independent_forward=True,
                complete_months_verified=True,flat_at_boundaries=True,trade_rows=trades,samples=100)
    result=performance_report(rows,**kwargs)
    assert result['probability_status']=='CONDITIONAL_HISTORICAL_ESTIMATE'
    assert result['monthly_target_probability']==0
    assert result['monthly_target_probability_interval'][1]>0 # no false certainty from zero events
    assert result['net_expectancy_per_closed_trade']==100
    assert result==performance_report(rows,**kwargs)
    assert performance_report(rows,**dict(kwargs,trade_rows=trades[:-1]))['monthly_target_probability'] is None


def test_probability_rejects_duplicate_or_incomplete_days():
    row=dict(date='2026-10-01',net_pnl=1000,closed_trades=1,complete_session=True)
    with pytest.raises(ValueError):performance_report([row,row])
    with pytest.raises(ValueError):performance_report([dict(row,complete_session=False)])


def test_existing_fill_simulator_does_not_assume_missing_quantity():
    from option_institutional_controls import simulate_limit_fill,OptionQuote
    result=simulate_limit_fill(side='BUY',quantity=65,limit_price=100.,submitted_at=100.,
                               quotes=[OptionQuote(ts=101.,bid=99.,ask=100.)])
    assert not result.filled and result.quantity==0


def test_native_portfolio_manager_blocks_nan_daily_evidence():
    from portfolio_risk import PortfolioRiskManager
    result=PortfolioRiskManager(capital=500000).evaluate_new_trade('NIFTY',100.,90.,65,
                                                                 current_daily_pnl=float('nan'),is_options=True)
    assert not result.allowed


def test_exchange_timestamp_and_symbol_survive_fetch_flatten_record(monkeypatch,tmp_path):
    import sqlite3,sys
    import option_chain_fetcher as fetch
    import option_chain_recorder as recorder
    import angel_option_chain
    stamp='2026-10-15T10:00:01'
    class Engine:
        def __init__(self,**kw):pass
        def scan_option_chain(self):
            return 25000.,25000.,'2026-10-29',[dict(strike=25000,CE_LTP=100,PE_LTP=90,
                CE_symbol='TESTCE',PE_symbol='TESTPE',CE_exch_time=stamp,PE_exch_time=stamp)]
    monkeypatch.setitem(sys.modules,'angel',SimpleNamespace(AngelOne=lambda **kw:object()))
    monkeypatch.setattr(angel_option_chain,'AngelOptionChainEngine',Engine)
    client=fetch.NSEOptionChainFetcher('NIFTY')
    raw=client._fetch_from_angel()
    raw['_provider_fetched_at']='2026-10-15T10:00:02+05:30'
    df=client.build_dataframe(raw,expiry='2026-10-29',spot_price=25000)
    assert df.iloc[0]['CE_exchange_timestamp']==stamp+'+05:30'
    assert df.iloc[0]['CE_symbol']=='TESTCE'
    result=SimpleNamespace(summary={},dataframe=df,spot=25000,expiry='2026-10-29',atm_strike=25000)
    class Fake:
        last_source='angel'
        last_request_id='fixture'
        def __init__(self,**kw):pass
        def fetch_and_analyze(self):return result
    monkeypatch.setattr(fetch,'NSEOptionChainFetcher',Fake)
    monkeypatch.setattr(recorder,'_direction_context',lambda _:('UNKNOWN','UNKNOWN'))
    db=tmp_path/'chain.db'
    recorder.record_option_chain_snapshot('NIFTY',db_path=str(db))
    with sqlite3.connect(db) as conn:
        rows=json.loads(conn.execute('SELECT rows_json FROM option_chain_snapshots').fetchone()[0])
    assert rows[0]['CE_exchange_timestamp']==stamp+'+05:30'
    assert rows[0]['PE_symbol']=='TESTPE'
    assert rows[0]['quote_received_at']==raw['_provider_fetched_at']


def test_durable_reconciliation_requires_exact_terminal_broker_evidence(tmp_path):
    from confirmed_option_orders import OrderReviews,AngelReviewGateway
    now=100
    class Gateway:
        evidence=None
        def review(self,*args):return dict(mode='LIVE',quantity=65,limit=100.,symbol='TESTCE')
        def recheck(self,plan):pass
        def submit(self,plan):return 'acknowledged, fill unknown'
        def reconcile(self,plan):return self.evidence
    gateway=Gateway();service=OrderReviews(tmp_path/'orders.db',gateway,clock=lambda:now)
    ident,_=service.prepare('owner','TESTCE',1,100.)
    service.act('owner',ident)
    assert 'ACKNOWLEDGED' in service.status('owner')
    nextid,_=service.prepare('owner','TESTCE',1,100.)
    assert 'reconciliation' in service.act('owner',nextid)
    assert not service.reconcile('intruder',ident)
    gateway.evidence={'status':'cancelled','filled_quantity':0}
    assert service.reconcile('owner',ident)
    assert 'RECONCILED' in service.status('owner')


def test_broker_reconciliation_rejects_open_mismatched_and_unknown_fills():
    from confirmed_option_orders import AngelReviewGateway,broker_review_tag
    gateway=AngelReviewGateway(None)
    plan=dict(review_id='review',symbol='TESTCE',exchange='NFO',side='BUY',product='INTRADAY',quantity=65,limit=100.)
    row=dict(ordertag=broker_review_tag('review'),tradingsymbol='TESTCE',exchange='NFO',transactiontype='BUY',producttype='INTRADAY',
             quantity='65',price='100',status='complete',filledshares='65',averageprice='99.95',orderid='123')
    book=[row]
    gateway.broker=lambda:SimpleNamespace(client_id="fixture",obj=SimpleNamespace(orderBook=lambda:{'status':True,'data':book}))
    plan['account_fingerprint']=gateway.account_fingerprint()
    assert gateway.reconcile(plan)['filled_quantity']==65
    for changes in ({'status':'open'},{'averageprice':'0'},{'tradingsymbol':'OTHER'}, {'filledshares':'0'}):
        book[0]=dict(row,**changes)
        assert gateway.reconcile(plan) is None


def test_direction_score_is_not_presented_as_win_probability():
    from trade_direction_engine import format_direction
    text=format_direction(dict(action='WAIT',symbol='NIFTY',confidence=80,spot=25000,pcr=1,
                               oi_bias='NEUTRAL',quality_score=50,reasons=[],entry=None))
    assert 'Confluence score: 80/100' in text and 'Win probability: uncalibrated' in text
    assert 'Confidence: 80%' not in text


def test_probability_telegram_status_remains_unknown_without_report(tmp_path):
    from strategy_probability_report import render_probability_status
    assert 'unknown' in render_probability_status(tmp_path/'missing.json')
    path=tmp_path/'bad.json';path.write_text('{')
    assert 'invalid' in render_probability_status(path)


def test_existing_var_model_failure_blocks_instead_of_skipping(monkeypatch):
    import portfolio_risk as risk
    import config
    monkeypatch.setattr(config,'ENABLE_VAR',True,raising=False)
    monkeypatch.setattr(risk,'_VAR_AVAIL',True)
    def fail(*args):raise RuntimeError('unavailable')
    monkeypatch.setattr(risk,'_get_var',fail,raising=False)
    result=risk.PortfolioRiskManager(capital=500000).evaluate_new_trade('NIFTY',100.,90.,65,is_options=True)
    assert not result.allowed and 'VaR evidence' in result.reason


def test_catalog_research_adapters_are_explicit_underlying_adaptations():
    from quant_model_catalog import get_research_adapter,build_integration_plan,load_catalog
    adapter=get_research_adapter('Q01')
    result=adapter(data=daily())
    assert result['evidence_kind']=='UNDERLYING_PROXY'
    assert result['option_pnl'] is None
    assert len(build_integration_plan(load_catalog())['research_adaptations'])==2


def test_probability_attestation_strings_do_not_qualify_as_boolean_evidence():
    rows,trades=probability_fixture()
    report=performance_report(rows,evidence_kind='OBSERVED_OPTION_NET_DAILY',independent_forward='false',
                              complete_months_verified=True,flat_at_boundaries=True,trade_rows=trades,samples=100)
    assert report['monthly_target_probability'] is None


def test_new_feed_timestamp_alone_cannot_replenish_consumed_depth(contract,quote):
    fresh=dict(quote,received_at='2026-10-15T10:00:03+05:30',exchange_timestamp='2026-10-15T10:00:03+05:30')
    result=replay(contract,[event(quote),event(fresh)])
    assert result['filled_quantity']==65 and result['state']=='PARTIAL'
