import copy
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from pivot_boss import is_virgin_cpr,_completed_period_ohlc,build_ochao_levels,get_pdh_pdl,calc_cpr
from pivot_boss_research import load_catalog,cpr_relationship,action_triggers,profile_from_price_counts,virgin_level_status


def frame(rows,start='2026-10-14 09:20',freq='5min'):
    df=pd.DataFrame(rows,columns=['open','high','low','close'],
                    index=pd.date_range(start,periods=len(rows),freq=freq,tz='Asia/Kolkata'))
    df.attrs['timestamp_semantics']='close'
    return df


def test_source_catalog_is_preserved_and_never_approves_odds():
    c=load_catalog()
    assert len(c['entries'])==25 and len(c['supplied_sections'])==5
    assert all(not r['live_approved'] and not r['backtest_registered'] for r in c['entries'])
    assert c['entries'][8]['reported_touch_claim']['trade_win_probability'] is None
    assert c['entries'][19]['reported_touch_claim']['validated_in_this_system'] is False


@pytest.mark.parametrize('current,expected',[
    ((12,13),'HIGHER'),((7,8),'LOWER'),((10.5,11.5),'OVERLAPPING_HIGHER'),
    ((9.5,10.5),'OVERLAPPING_LOWER'),((10,11),'UNCHANGED'),
    ((9,12),'OUTSIDE'),((10.2,10.8),'INSIDE'),((11,12),'OVERLAPPING_HIGHER'),
    ((10,10.8),'INSIDE'),((10,12),'OUTSIDE')])
def test_seven_cpr_relationships_and_boundary_ties(current,expected):
    assert cpr_relationship(dict(bc=current[0],tc=current[1]),dict(bc=10,tc=11))==expected


def test_cpr_normalizes_reversed_raw_bounds():
    c=calc_cpr(110,100,100)
    assert c['bc']<=c['pivot']<=c['tc']
    assert cpr_relationship(dict(bc=13,tc=12),dict(bc=11,tc=10))=='HIGHER'


def test_wick_requires_next_bar_confirmation_not_future_entry():
    df=frame([(10,11.2,7,11),(11,11.6,10.8,11.4)])
    early=action_triggers(df,as_of=df.index[0])
    assert not early.wick_bull.any()
    complete=action_triggers(df,as_of=df.index[-1])
    assert complete.wick_bull.tolist()==[False,True]
    assert not complete.attrs['live_approved']


def test_extreme_average_excludes_extreme_bar_itself():
    df=frame([(10,11,9,10)]*3+[(11.5,12,9,9.5),(9.5,10.2,9.4,10)])
    out=action_triggers(df,as_of=df.index[-1],average_range_lookback=3)
    assert not out.extreme_bull.iloc[-2] and out.extreme_bull.iloc[-1]


def test_outside_comparisons_reference_previous_bar():
    df=frame([(10,11,9,10)]*3+[(9.5,11.1,8.9,11.05)])
    out=action_triggers(df,as_of=df.index[-1],average_range_lookback=3)
    assert out.outside_bull.iloc[-1]


def test_doji_confirmation_is_first_of_next_two_closes():
    rows=[(19.5,21,19,20)]*10+[(10,10.5,9.5,10.05),(10.1,10.4,9.9,10.3),(10.3,10.7,10.2,10.6)]
    df=frame(rows)
    out=action_triggers(df,as_of=df.index[-1])
    assert not out.doji_bull.iloc[-2] and out.doji_bull.iloc[-1]
    df.iloc[-2]=[10.1,10.8,9.9,10.7]
    out=action_triggers(df,as_of=df.index[-1])
    assert out.doji_bull.iloc[-2] and not out.doji_bull.iloc[-1]


def test_future_candles_cannot_change_past_features():
    df=frame([(10,11,9,10)]*25+[(10,11.2,7,11),(11,11.6,10.8,11.4)])
    before=action_triggers(df,as_of=df.index[24])
    df.iloc[25:]=df.iloc[25:]*10
    pd.testing.assert_frame_equal(before,action_triggers(df,as_of=df.index[24]))


def test_missing_timestamp_semantics_and_invalid_ohlc_rejected():
    df=frame([(10,11,9,10)])
    df.attrs.clear()
    with pytest.raises(ValueError,match='close timestamps'):action_triggers(df,as_of=df.index[-1])
    df.attrs['timestamp_semantics']='close';df['high']=8
    with pytest.raises(ValueError,match='geometry'):action_triggers(df,as_of=df.index[-1])


def test_virgin_cpr_handles_datetime_and_noncontiguous_indices():
    df=frame([(12,13,12,12.5),(10,10.5,9.5,10),(12,13,12,12.5)])
    c=dict(bc=9,tc=11,pivot=10)
    out=is_virgin_cpr(df,c)
    assert out['touched'] and out['pivot_touched'] and out['bars_since_touch']==1
    df.index=[10,20,30]
    assert is_virgin_cpr(df,c)['bars_since_touch']==1


def test_missing_virgin_observations_are_unknown():
    assert is_virgin_cpr(None,dict(bc=9,tc=11,pivot=10))['virgin'] is None
    assert is_virgin_cpr(pd.DataFrame({'close':[10]}),dict(bc=9,tc=11,pivot=10))['virgin'] is None


def test_completed_period_never_falls_back_to_current_partial_period():
    df=frame([(10,11,9,10)]*5)
    assert _completed_period_ohlc(df,'D')==(0.,0.,0.)
    levels=build_ochao_levels(df)
    assert all(not value for value in levels.values())


def test_current_session_never_changes_prior_daily_levels():
    first=frame([(10,11,9,10)]*4,start='2026-10-13 09:20')
    second=frame([(20,21,19,20)]*4,start='2026-10-14 09:20')
    df=pd.concat([first,second])
    assert _completed_period_ohlc(df,'D')==(11.,9.,10.)
    assert get_pdh_pdl(df)['PDH']==11
    df.iloc[4:]=df.iloc[4:]*100
    assert _completed_period_ohlc(df,'D')==(11.,9.,10.)


def test_daily_source_future_rows_do_not_leak_into_week_month_year():
    idx=pd.to_datetime(['2025-12-31','2026-09-30','2026-10-09','2026-10-14','2026-12-31']).tz_localize('Asia/Kolkata')
    daily=pd.DataFrame(dict(high=[11,21,31,41,9999],low=[9,19,29,39,9990],close=[10,20,30,40,9995]),index=idx)
    now=frame([(40,41,39,40)]*5,start='2026-10-14 09:20')
    levels=build_ochao_levels(now,daily)
    assert levels['weekly']['W_H']==31
    assert levels['monthly']['M_H']==21
    assert levels['yearly']['Y_H']==11


def test_profile_kinds_are_distinct_and_not_inferred_from_candles():
    counts=[dict(price=100,count=1),dict(price=101,count=10),dict(price=102,count=1)]
    volume=profile_from_price_counts(counts,kind='VOLUME_AT_PRICE',tick_size=1)
    time=profile_from_price_counts(counts,kind='TIME_PRICE_OPPORTUNITIES',tick_size=1)
    assert volume['poc']==time['poc']==101
    assert volume['profile_kind']!=time['profile_kind']
    assert volume['captured_fraction']>=.7 and volume['win_probability'] is None
    with pytest.raises(ValueError):profile_from_price_counts(counts,kind='OHLCV',tick_size=1)


def test_virgin_poc_ignores_formation_session_and_requires_complete_later_history():
    origin=frame([(10,11,9,10)],start='2026-10-13 09:20')
    later=frame([(12,13,11.5,12)],start='2026-10-14 09:20')
    df=pd.concat([origin,later]);df.attrs['timestamp_semantics']='close'
    unknown=virgin_level_status(10,origin_session='2026-10-13',subsequent_bars=df,as_of=df.index[-1])
    assert unknown['virgin'] is None
    out=virgin_level_status(10,origin_session='2026-10-13',subsequent_bars=df,as_of=df.index[-1],complete_coverage=True)
    assert out['virgin'] is True and out['probability'] is None
    df.iloc[-1]=[10,11,9,10]
    assert virgin_level_status(10,origin_session='2026-10-13',subsequent_bars=df,as_of=df.index[-1])['virgin'] is False


def test_ohlcv_profile_is_labeled_proxy_and_keeps_flat_bar_volume():
    from volume_profile_advanced import build_volume_profile
    df=frame([(10,11,9,10)]*4+[(10,10,10,10)])
    df['volume']=100.
    out=build_volume_profile(df,n_bins=10)
    assert out['profile_kind']=='OHLCV_UNIFORM_VOLUME_PROXY'
    assert not out['observed_volume_at_price']
    assert sum(out['profile'])==pytest.approx(500.)


def test_missing_immediate_prior_calendar_period_cannot_use_older_one():
    df=frame([(10,11,9,10)],start='2024-01-02 09:20')
    for period in ('W','M','Y'):
        assert _completed_period_ohlc(df,period,as_of=pd.Timestamp('2026-10-15',tz='Asia/Kolkata'))==(0.,0.,0.)
