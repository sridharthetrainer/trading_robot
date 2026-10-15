"""Pivot Boss source inventory and causal research features; no orders or win odds.

Feature definitions resolve ambiguities explicitly as project adaptations. They
are not newly registered strategies or exact reproductions of the full book.
"""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parent
CATALOG=ROOT/'pivot_boss_research_catalog.json'


def load_catalog(path=CATALOG):
    catalog=json.loads(Path(path).read_text())
    source=(ROOT/catalog['source']['path']).resolve()
    if not source.is_relative_to(ROOT) or hashlib.sha256(source.read_bytes()).hexdigest()!=catalog['source']['sha256']:
        raise ValueError('Pivot source fingerprint/path invalid')
    if catalog['policy']!={'research_only':True,'auto_schedule':False,'live_approved':False,'probability_claims_validated':False}:
        raise ValueError('Pivot catalog must remain research-only')
    ids=[row['id'] for row in catalog['entries']]
    if len(ids)!=25 or len(set(ids))!=25:
        raise ValueError('Expected 25 uniquely identified research concepts')
    if any(row.get('live_approved') is not False or not row.get('missing_rules') for row in catalog['entries']):
        raise ValueError('Incomplete concepts cannot be approved')
    return catalog


def cpr_relationship(current, previous, *, tolerance=0.):
    """Seven disjoint normalized CPR states; boundary contact counts as overlap."""
    if not math.isfinite(tolerance) or tolerance<0:raise ValueError('Nonnegative tolerance required')
    c0,c1=sorted((float(current['bc']),float(current['tc'])))
    p0,p1=sorted((float(previous['bc']),float(previous['tc'])))
    if not all(math.isfinite(x) and x>0 for x in (c0,c1,p0,p1)):raise ValueError('Valid CPR bounds required')
    if abs(c0-p0)<=tolerance and abs(c1-p1)<=tolerance:return 'UNCHANGED'
    if c0>p1+tolerance:return 'HIGHER'
    if c1<p0-tolerance:return 'LOWER'
    if c0>=p0-tolerance and c1<=p1+tolerance:return 'INSIDE'
    if c0<=p0+tolerance and c1>=p1-tolerance:return 'OUTSIDE'
    return 'OVERLAPPING_HIGHER' if c0>=p0-tolerance else 'OVERLAPPING_LOWER'


def _completed_frame(data, as_of):
    if not isinstance(data,pd.DataFrame) or not isinstance(data.index,pd.DatetimeIndex):raise ValueError('Timestamped OHLC required')
    if data.attrs.get('timestamp_semantics')!='close':raise ValueError('Explicit completed-bar close timestamps required')
    anchor=pd.Timestamp(as_of)
    if anchor.tzinfo is None:raise ValueError('Timezone-aware as_of required')
    frame=data.copy();frame.columns=[str(x).lower() for x in frame.columns]
    if frame.index.tz is None:raise ValueError('Timezone-aware candles required')
    frame=frame.loc[frame.index<=anchor]
    if frame.columns.has_duplicates or not {'open','high','low','close'}<=set(frame):raise ValueError('OHLC columns required')
    if not frame.index.is_monotonic_increasing or frame.index.has_duplicates:raise ValueError('Ordered unique candles required')
    values=frame[['open','high','low','close']].apply(pd.to_numeric,errors='coerce')
    if not np.isfinite(values.to_numpy()).all() or (values<=0).any().any():raise ValueError('Finite positive OHLC required')
    if ((values.high<values[['open','close','low']].max(axis=1))|(values.low>values[['open','close','high']].min(axis=1))).any():raise ValueError('Invalid candle geometry')
    frame[values.columns]=values
    return frame


def action_triggers(data, *, as_of, average_range_lookback=20):
    """Four supplied candle concepts, observed at confirmation closes only.

    Lookback=20 and indexed outside-bar interpretation are project choices.
    These are unvalidated features without pivot-zone filters, stops or sizing.
    """
    if isinstance(average_range_lookback,bool) or not isinstance(average_range_lookback,int) or average_range_lookback<2:raise ValueError('Lookback >=2 required')
    f=_completed_frame(data,as_of)
    span=(f.high-f.low).replace(0,np.nan);body=(f.close-f.open).abs()
    lower=np.minimum(f.open,f.close)-f.low;upper=f.high-np.maximum(f.open,f.close)
    safe_body=body.replace(0,np.nan)
    bullish_wick=(lower/safe_body).between(2.5,3.5)&(f.close>=f.low+.65*span)
    bearish_wick=(upper/safe_body).between(2.5,3.5)&(f.close<=f.low+.35*span)
    prior_average=(f.high-f.low).shift(1).rolling(average_range_lookback,min_periods=average_range_lookback).mean().replace(0,np.nan)
    extreme=(span/prior_average).between(1.5,2.)&(body/span).between(.5,.85)
    doji=body/span<=.1
    sma=f.close.rolling(10,min_periods=10).mean()
    bull_doji=doji&(f.high<sma);bear_doji=doji&(f.low>sma)
    result=pd.DataFrame(index=f.index)
    result['wick_bull']=bullish_wick.shift(1,fill_value=False)&(f.close>f.high.shift(1))
    result['wick_bear']=bearish_wick.shift(1,fill_value=False)&(f.close<f.low.shift(1))
    result['extreme_bull']=extreme.shift(1,fill_value=False)&(f.close.shift(1)<f.open.shift(1))&(f.close>f.open)
    result['extreme_bear']=extreme.shift(1,fill_value=False)&(f.close.shift(1)>f.open.shift(1))&(f.close<f.open)
    range_filter=(span/prior_average).between(1.05,1.25)
    result['outside_bull']=range_filter&(f.low<f.low.shift(1))&(f.close>f.high.shift(1))
    result['outside_bear']=range_filter&(f.high>f.high.shift(1))&(f.close<f.low.shift(1))
    result['doji_bull']=(bull_doji.shift(1,fill_value=False)&(f.close>f.high.shift(1)))|(
        bull_doji.shift(2,fill_value=False)&(f.close>f.high.shift(2))&(f.close.shift(1)<=f.high.shift(2)))
    result['doji_bear']=(bear_doji.shift(1,fill_value=False)&(f.close<f.low.shift(1)))|(
        bear_doji.shift(2,fill_value=False)&(f.close<f.low.shift(2))&(f.close.shift(1)>=f.low.shift(2)))
    result.attrs.update(evidence_kind='UNVALIDATED_CANDLE_FEATURES',live_approved=False,
                        entry_timing='Any execution must be after the confirmation close',
                        average_range_lookback=average_range_lookback)
    return result


def profile_from_price_counts(rows, *, kind, tick_size, value_fraction=.70):
    """POC/value area from explicitly supplied volume-at-price or TPO counts.

    POC ties choose the lower price. Value area grows toward the larger adjacent
    count (ties upward). This deterministic policy is a declared adaptation.
    """
    if kind not in {'VOLUME_AT_PRICE','TIME_PRICE_OPPORTUNITIES'}:raise ValueError('Explicit profile kind required')
    if not math.isfinite(tick_size) or tick_size<=0 or not 0<value_fraction<=1:raise ValueError('Invalid profile grid')
    counts={}
    for row in rows:
        price,weight=float(row['price']),float(row['count'])
        if not math.isfinite(price) or price<=0 or not math.isfinite(weight) or weight<0:raise ValueError('Valid observed prices/counts required')
        if kind=='TIME_PRICE_OPPORTUNITIES' and not weight.is_integer():raise ValueError('Integer TPO counts required')
        index=round(price/tick_size)
        if abs(price/tick_size-index)>1e-6:raise ValueError('Tick-aligned profile required')
        counts[index]=counts.get(index,0.)+weight
    if not counts or sum(counts.values())<=0:raise ValueError('Nonzero profile required')
    first,last=min(counts),max(counts)
    if last-first>100000:raise ValueError('Profile grid budget exceeded')
    weights=np.array([counts.get(i,0.) for i in range(first,last+1)])
    poc=int(weights.argmax());lo=hi=poc;inside=weights[poc];total=weights.sum()
    while inside<value_fraction*total and (lo>0 or hi<len(weights)-1):
        left=weights[lo-1] if lo>0 else -1
        right=weights[hi+1] if hi<len(weights)-1 else -1
        if right>=left:hi+=1;inside+=weights[hi]
        else:lo-=1;inside+=weights[lo]
    return dict(profile_kind=kind,poc=(first+poc)*tick_size,val=(first+lo)*tick_size,
                vah=(first+hi)*tick_size,captured_fraction=float(inside/total),
                virgin_status=None,win_probability=None,live_approved=False)


def virgin_level_status(level, *, origin_session, subsequent_bars, as_of, complete_coverage=False):
    """Virgin means untested AFTER origin; missing history never proves virginity."""
    if not math.isfinite(level) or level<=0:raise ValueError('Positive level required')
    frame=_completed_frame(subsequent_bars,as_of)
    origin=pd.Timestamp(origin_session).date()
    # Indian exchange session dates, independent of the input timestamp offset.
    frame=frame.loc[frame.index.tz_convert('Asia/Kolkata').date>origin]
    if frame.empty:return dict(status='UNKNOWN',virgin=None,probability=None)
    touched=bool(((frame.low<=level)&(frame.high>=level)).any())
    if touched:return dict(status='TESTED',virgin=False,probability=None)
    if complete_coverage is not True:return dict(status='UNKNOWN',virgin=None,probability=None)
    return dict(status='UNTESTED_AFTER_ORIGIN',virgin=True,probability=None)


def main(argv=None):
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args(argv)
    catalog=load_catalog()
    report=dict(source=catalog['source'],entry_count=len(catalog['entries']),policy=catalog['policy'],
                corrections=catalog['corrections'],entries=catalog['entries'])
    text=json.dumps(report,indent=2,allow_nan=False)+'\n'
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(text)
    else:print(text,end='')
    return 0


if __name__=='__main__':raise SystemExit(main())
