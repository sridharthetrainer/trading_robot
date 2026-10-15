"""Strict aggregate bought-option risk gate; never resize a reviewed order."""
from dataclasses import asdict, dataclass
from datetime import datetime
import math
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class OptionRiskLimits:
    capital: float=500000.
    max_trade_premium: float=25000.
    max_total_premium: float=50000.
    max_daily_loss: float=5000.
    max_open_positions: int=2
    max_correlated_positions: int=1
    entry_cutoff_hhmm: int=1510
    allow_overnight: bool=False


def evaluate_option_portfolio(plan, positions, *, daily_pnl, snapshot_at, now, limits=None):
    limits=limits or OptionRiskLimits()
    def deny(reason):return dict(allowed=False,reason=reason,approved_quantity=0)
    if any(not math.isfinite(float(v)) or float(v)<=0 for v in (
        limits.capital,limits.max_trade_premium,limits.max_total_premium,limits.max_daily_loss)):
        return deny('Invalid risk limits')
    if now.tzinfo is None or snapshot_at.tzinfo is None or not 0 <= (now-snapshot_at).total_seconds() <= 30:
        return deny('Fresh timezone-aware portfolio snapshot required')
    local=now.astimezone(ZoneInfo('Asia/Kolkata'))
    if local.hour*100+local.minute >= limits.entry_cutoff_hhmm:
        return deny('New-entry cutoff reached')
    if not math.isfinite(float(daily_pnl)) or daily_pnl <= -limits.max_daily_loss:
        return deny('Daily loss limit reached or risk evidence invalid')
    try:
        if plan.get('side')!='BUY' or plan.get('product')!='INTRADAY' and not limits.allow_overnight:
            return deny('Only bought intraday options admitted')
        quantity=plan['quantity']; price=float(plan['limit'])
        if isinstance(quantity,bool) or not isinstance(quantity,int) or quantity<=0 or not math.isfinite(price) or price<=0:
            return deny('Invalid reviewed quantity/limit')
        premium=price*quantity
        if premium>limits.max_trade_premium:return deny('Per-trade premium cap')
        if len(positions)>=limits.max_open_positions:return deny('Maximum open positions')
        total=0.; correlated=0
        for position in positions:
            if position['side']!='BUY':return deny('Unbounded/unknown option exposure')
            value=float(position['premium_at_risk'])
            if not math.isfinite(value) or value<0:return deny('Invalid existing premium exposure')
            total+=value
            # Unknown groups are conservatively correlated.
            group=position.get('correlation_group')
            if not group or not plan.get('correlation_group') or group==plan['correlation_group']:correlated+=1
            opened=datetime.fromisoformat(position['opened_at'])
            if opened.tzinfo is None:return deny('Existing position timestamp unavailable')
            if not limits.allow_overnight and opened.astimezone(local.tzinfo).date()!=local.date():
                return deny('Overnight position requires reconciliation')
        if correlated>=limits.max_correlated_positions:return deny('Correlated exposure limit')
        if total+premium>limits.max_total_premium:return deny('Aggregate premium cap')
    except (KeyError,ValueError,TypeError):
        return deny('Incomplete portfolio exposure evidence')
    return dict(allowed=True,reason='Approved exact reviewed quantity',approved_quantity=quantity,
                total_premium_at_risk=total+premium,limits=asdict(limits))
