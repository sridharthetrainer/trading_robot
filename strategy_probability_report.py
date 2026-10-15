"""Conditional performance uncertainty from qualified net daily option evidence.

No estimate is emitted for proxy/model P&L, incomplete months or a reused holdout.
All bootstrap probabilities are conditional historical estimates, not forecasts.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import date
import math
import numpy as np


def performance_report(daily_rows, *, capital=500000., monthly_target=30000.,
                       evidence_kind='UNKNOWN', independent_forward=False,
                       complete_months_verified=False, trade_rows=None, flat_at_boundaries=False, samples=3000, seed=17):
    if not math.isfinite(capital) or capital<=0 or not math.isfinite(monthly_target) or monthly_target<=0:
        raise ValueError('Positive finite capital and target required')
    if not isinstance(samples,int) or not 100 <= samples <= 100000:
        raise ValueError('Bootstrap budget must be 100..100000')
    rows=list(daily_rows)
    seen=set(); days=[]; monthly=defaultdict(float); trades=0
    for row in rows:
        stamp=date.fromisoformat(row['date'])
        if stamp in seen or days and stamp<=days[-1][0]:
            raise ValueError('Unique chronological daily evidence required')
        seen.add(stamp)
        pnl=float(row['net_pnl']); count=row['closed_trades']
        if not math.isfinite(pnl) or isinstance(count,bool) or not isinstance(count,int) or count<0:
            raise ValueError('Finite net P&L and integer trade counts required')
        if row.get('complete_session') is not True:
            raise ValueError('Incomplete sessions cannot be silently dropped or called zero')
        if float(row.get('capital',capital)) != capital:
            raise ValueError('Mixed capital/position sizing requires a separate experiment')
        days.append((stamp,pnl)); monthly[stamp.strftime('%Y-%m')]+=pnl; trades+=count
    values=np.array([p for _,p in days],dtype=float)
    equity=np.r_[capital,capital+np.cumsum(values)]
    drawdown=np.maximum.accumulate(equity)-equity
    report=dict(report_schema_version=1,evidence_kind=evidence_kind,days=len(rows),closed_trades=trades,
                independent_forward=independent_forward is True,complete_months_verified=complete_months_verified is True,
                flat_at_boundaries=flat_at_boundaries is True,
                calendar_months=len(monthly),capital=capital,monthly_target=monthly_target,
                total_net_pnl=float(values.sum()),mean_net_daily_pnl=float(values.mean()) if len(values) else None,
                max_drawdown=float(drawdown.max()),monthly_net_pnl=dict(monthly),
                monthly_target_probability=None,monthly_target_probability_interval=None,
                mean_net_monthly_pnl_interval=None,probability_status='INSUFFICIENT_QUALIFIED_EVIDENCE',
                live_approved=False,assumptions=[],blocks=[])
    observed_trades=list(trade_rows or [])
    trade_values=[]; trade_ids=set()
    for trade in observed_trades:
        ident=trade['trade_id']; value=float(trade['net_pnl'])
        if not ident or ident in trade_ids or not math.isfinite(value):
            raise ValueError('Unique finite net trade outcomes required')
        trade_ids.add(ident);trade_values.append(value)
    winners=[x for x in trade_values if x>0];losers=[x for x in trade_values if x<0]
    report.update(net_expectancy_per_closed_trade=float(np.mean(trade_values)) if trade_values else None,
                  closed_trade_win_rate=len(winners)/len(trade_values) if trade_values else None,
                  average_net_win=float(np.mean(winners)) if winners else None,
                  average_net_loss=float(np.mean(losers)) if losers else None)
    if flat_at_boundaries is not True:report['blocks'].append('Flat portfolio boundaries required for closed-trade reconciliation')
    report['trade_ledger_reconciled']=len(trade_values)==trades and math.isclose(sum(trade_values),float(values.sum()),rel_tol=1e-8,abs_tol=.01)
    if not report['trade_ledger_reconciled']:
        report['blocks'].append('Closed-trade ledger must reconcile to net daily P&L')
    ordinals=[int(k[:4])*12+int(k[5:]) for k in monthly]
    if any(b-a!=1 for a,b in zip(ordinals,ordinals[1:])):
        report['blocks'].append('Consecutive complete months required')
    if evidence_kind!='OBSERVED_OPTION_NET_DAILY':report['blocks'].append('Observed net option outcomes required; proxy/model P&L excluded')
    if independent_forward is not True:report['blocks'].append('Independent forward evaluation required')
    if complete_months_verified is not True:report['blocks'].append('Complete calendar-month/session coverage must be verified')
    if len(monthly)<12:report['blocks'].append('At least 12 complete calendar months required')
    if trades<100:report['blocks'].append('At least 100 closed trades required')
    if len(values)<200:report['blocks'].append('At least 200 complete sessions required')
    if equity.min()<=0:report['blocks'].append('Capital exhausted during evaluation')
    if report['blocks']:return report
    # Resample whole chronological months in circular 3-month blocks, retaining
    # within-month dependence and some cross-month clustering. Monte Carlo draws
    # are not independent market observations and never determine sample size.
    totals=np.array(list(monthly.values()))
    rng=np.random.default_rng(seed)
    probabilities=[]; means=[]
    for _ in range(samples):
        indices=[]
        while len(indices)<len(totals):
            start=int(rng.integers(len(totals)))
            indices.extend((start+j)%len(totals) for j in range(3))
        selected=totals[indices[:len(totals)]]
        probabilities.append(float(np.mean(selected>=monthly_target)))
        means.append(float(selected.mean()))
    # Avoid a zero-width interval when a small empirical history has no target
    # successes/failures. This effective-block Wilson envelope is a sensitivity
    # bound, not a claim of exact confidence coverage under arbitrary dependence.
    p=float(np.mean(totals>=monthly_target)); n=max(1,len(totals)//3); z=1.959963984540054
    center=(p+z*z/(2*n))/(1+z*z/n)
    half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/(1+z*z/n)
    lower,upper=np.quantile(probabilities,[.025,.975]).tolist()
    interval=[max(0.,min(lower,center-half)),min(1.,max(upper,center+half))]
    report.update(probability_status='CONDITIONAL_HISTORICAL_ESTIMATE',
                  monthly_target_probability=float(np.mean(totals>=monthly_target)),
                  monthly_target_probability_interval=interval,
                  interval_method='3-month block-bootstrap envelope with effective-block Wilson sensitivity bound; approximate coverage',
                  mean_net_monthly_pnl_interval=np.quantile(means,[.025,.975]).tolist(),
                  bootstrap_samples=samples,bootstrap_seed=seed,
                  assumptions=['Monthly blocks approximate dependence; regime changes can invalidate the estimate.',
                               'Coverage and independent-forward provenance are caller attestations, not inferred from P&L.',
                               'Costs, realized slippage and no-trade sessions must already be included.',
                               'Twelve months is a minimum; this is not a guarantee or live admission.'])
    return report


def main(argv=None):
    import argparse,json
    from pathlib import Path
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args(argv)
    payload=json.loads(args.input.read_text())
    report=performance_report(payload['daily_rows'],**payload.get('metadata',{}))
    rendered=json.dumps(report,indent=2,allow_nan=False)+'\n'
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(rendered)
    else:print(rendered,end='')
    return 0


def render_probability_status(path=None):
    """Telegram-safe status; absent/invalid evidence never becomes a zero probability."""
    import json
    from html import escape
    from pathlib import Path
    selected=Path(path) if path is not None else Path(__file__).resolve().parent/'option_probability_report.json'
    if not selected.exists():
        return ('<b>OPTIONS PROBABILITY</b>\n₹30,000/month from ₹500,000: <b>unknown</b>\n'
                'No qualified independent net option report is available. Required: 12 complete months, '
                '100 closed trades, reconciled daily/trade P&amp;L and verified coverage.\n'
                'Direction scores are confluence scores, not win probabilities.')
    try:
        report=json.loads(selected.read_text())
        if report.get('probability_status')!='CONDITIONAL_HISTORICAL_ESTIMATE':
            reasons='; '.join(str(v) for v in report.get('blocks',[])[:4])
            return '<b>OPTIONS PROBABILITY</b>\nMonthly target probability: <b>unknown</b>\n'+escape(reasons)
        if (report.get('report_schema_version')!=1 or report.get('evidence_kind')!='OBSERVED_OPTION_NET_DAILY'
                or report.get('blocks') or not all(report.get(k) is True for k in
                    ('independent_forward','complete_months_verified','flat_at_boundaries','trade_ledger_reconciled'))
                or report.get('calendar_months',0)<12 or report.get('closed_trades',0)<100 or report.get('days',0)<200):
            raise ValueError('Report qualification missing')
        value=float(report['monthly_target_probability']); low,high=report['monthly_target_probability_interval']
        if not all(math.isfinite(float(x)) and 0<=float(x)<=1 for x in (value,low,high)) or not low<=value<=high:
            raise ValueError('Invalid probability range')
        return (f'<b>OPTIONS PROBABILITY</b>\nHistorical target frequency: {value:.1%}\n'
                f'Approximate uncertainty/sensitivity range: {low:.1%}–{high:.1%}\n'
                f"Months: {int(report['calendar_months'])} · closed trades: {int(report['closed_trades'])}\n"
                'Conditional on historical net outcomes and stated assumptions. This is not a forecast or live approval.')
    except (ValueError,TypeError,KeyError):
        return '<b>OPTIONS PROBABILITY</b>\nProbability unknown: the saved evidence report is invalid.'


if __name__=='__main__':
    raise SystemExit(main())
