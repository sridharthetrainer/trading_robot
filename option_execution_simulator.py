"""Observed-quote limit-order replay for research, never a broker executor.

Crossing a recorded ask models a possible fill bounded by displayed quantity;
queue priority, latency and actual exchange fills are not certified by replay.
"""
from __future__ import annotations
import hashlib
import json
from datetime import timedelta
from option_research_data import aware_time, positive, validate_quote


def replay_limit_buy(*, contract, quantity, limit, confirmed_at, expires_at, events, latency_ms=250):
    lot = int(contract['lot_size'])
    if isinstance(quantity,bool) or not isinstance(quantity,int) or quantity <= 0 or quantity % lot:
        raise ValueError('Positive whole-lot order quantity required')
    positive(limit,'limit')
    if abs(limit/contract['tick_size']-round(limit/contract['tick_size'])) > 1e-6:
        raise ValueError('Limit tick mismatch')
    confirmed, expiry = aware_time(confirmed_at), aware_time(expires_at)
    if expiry <= confirmed or latency_ms < 0:
        raise ValueError('Invalid order timing')
    active = confirmed+timedelta(milliseconds=latency_ms)
    state, connected, turnover, filled, last = 'WORKING', True, 0., 0, None
    seen, fills, skipped = set(), [], []
    consumed_by_ask = {}
    for event in events:
        now = aware_time(event['time'])
        if last is not None and now < last:
            raise ValueError('Events must be chronologically ordered')
        last = now
        if state in {'FILLED','CANCELLED','REJECTED','UNCERTAIN','EXPIRED'}:
            break
        if now < confirmed:
            skipped.append('before_confirmation'); continue
        if now >= expiry:
            state='EXPIRED'; break
        kind=event['type']
        if kind=='disconnect': connected=False; continue
        if kind=='reconnect': connected=True; continue
        if kind=='cancel_ack': state='CANCELLED'; continue
        if kind=='reject': state='REJECTED'; continue
        if kind=='submission_timeout': state='UNCERTAIN'; break
        if kind!='quote': raise ValueError('Unknown replay event')
        if not connected or now < active:
            skipped.append('disconnected_or_latency'); continue
        quote=event['quote']
        try:
            validate_quote(quote,contract,as_of=event['time'])
        except (ValueError,TypeError):
            skipped.append('invalid_quote'); continue
        if aware_time(quote['exchange_timestamp']) < active:
            skipped.append('pre_activation_quote'); continue
        # Repeated received payloads cannot replenish displayed liquidity.
        identity={k:v for k,v in quote.items() if k not in {'received_at','exchange_timestamp'}}
        digest=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
        if digest in seen:
            skipped.append('duplicate_quote'); continue
        seen.add(digest)
        if quote['ask'] > limit: continue
        # Fresh timestamps alone cannot prove replenishment of the same level.
        # Consume displayed capacity once per ask price unless larger size appears.
        capacity=max(0,int(quote['ask_quantity'])-consumed_by_ask.get(quote['ask'],0))
        qty=min(quantity-filled,capacity//lot*lot)
        if qty <= 0: continue
        consumed_by_ask[quote['ask']]=consumed_by_ask.get(quote['ask'],0)+qty
        filled+=qty; turnover+=qty*quote['ask']
        fills.append(dict(quantity=qty,price=quote['ask'],time=event['time']))
        state='FILLED' if filled==quantity else 'PARTIAL'
    if state not in {'FILLED','CANCELLED','REJECTED','UNCERTAIN','EXPIRED'}:
        state='EXPIRED' if last is not None and last>=expiry else 'PARTIAL' if filled else 'WORKING'
    fees=None
    if filled:
        from nse_cost_model import NseCostModel
        fees=NseCostModel().single_leg_cost(turnover,'OPT_BUY','BUY',symbol=contract['underlying'],
            include_slippage=False,trade_date=confirmed.date()).total
    return dict(state=state,requested_quantity=quantity,filled_quantity=filled,
                average_price=turnover/filled if filled else None,remaining_quantity=quantity-filled,
                modeled_entry_fees=fees,fills=fills,skipped=skipped,
                reconciliation_required=state in {'WORKING','PARTIAL','UNCERTAIN'},
                evidence_kind='OBSERVED_QUOTE_REPLAY_MODEL',actual_fills_verified=False,live_approved=False)
