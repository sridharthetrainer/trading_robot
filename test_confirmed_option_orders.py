import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from confirmed_option_orders import OrderReviews
from telegram_commands import TelegramCommandHandler, _quick_execute_option


class Gateway:
    def __init__(self):
        self.submitted = []
        self.block = False
        self.uncertain = False

    def review(self, symbol, lots, limit):
        return dict(symbol=symbol, lots=lots, quantity=lots * 65, limit=limit, mode='PAPER',
                    ask=100.0, quote_age_seconds=1.0, premium=lots * 65 * limit)

    def recheck(self, plan):
        if self.block:
            raise ValueError('quote/mode/risk changed')

    def submit(self, plan):
        self.submitted.append(plan.copy())
        if self.uncertain:
            raise TimeoutError('broker timeout')
        return 'PAPER acknowledged'


@pytest.fixture
def service(tmp_path):
    return OrderReviews(tmp_path / 'orders.db', Gateway(), clock=lambda: 100)


def draft(service):
    return service.prepare('owner', 'NIFTY29OCT2626000CE', 1, 100.05)[0]


def test_prepare_is_side_effect_free_and_exact(service):
    ident = draft(service)
    assert not service.gateway.submitted
    assert service.act('owner', ident) == 'PAPER acknowledged'
    assert service.gateway.submitted[0]['limit'] == 100.05
    assert service.gateway.submitted[0]['quantity'] == 65
    assert 'SUBMITTED' in service.act('owner', ident)
    assert len(service.gateway.submitted) == 1


def test_owner_cannot_use_or_cancel_another_review(service):
    ident = draft(service)
    assert 'unavailable' in service.act('intruder', ident)
    assert 'unavailable' in service.act('intruder', ident, cancel=True)
    assert 'PENDING' in service.status('owner')
    assert not service.gateway.submitted


def test_expiry_is_not_renewed(service):
    ident = draft(service)
    service.clock = lambda: 160
    assert 'expired' in service.act('owner', ident)
    assert not service.gateway.submitted


def test_cancel_is_terminal(service):
    ident = draft(service)
    assert 'cancelled' in service.act('owner', ident, cancel=True)
    assert 'CANCELLED' in service.act('owner', ident)
    assert not service.gateway.submitted


def test_recheck_failure_blocks_submission(service):
    ident = draft(service)
    service.gateway.block = True
    assert 'blocked' in service.act('owner', ident)
    assert not service.gateway.submitted
    assert 'BLOCKED' in service.act('owner', ident)


def test_uncertain_submission_survives_restart_and_blocks_next(service):
    ident = draft(service)
    service.gateway.uncertain = True
    assert 'uncertain' in service.act('owner', ident)
    restarted = OrderReviews(service.path, Gateway(), clock=lambda: 100)
    assert 'UNCERTAIN' in restarted.act('owner', ident)
    assert 'reconciliation' in restarted.act('owner', draft(restarted))
    assert not restarted.gateway.submitted


def test_crash_claim_cannot_retry(service):
    ident = draft(service)
    with service.connect() as db:
        db.execute("UPDATE reviews SET status='SUBMITTING' WHERE id=?", (ident,))
    assert 'SUBMITTING' in service.act('owner', ident)
    assert 'reconciliation' in service.act('owner', draft(service))


def test_concurrent_confirmations_submit_once(service):
    ident = draft(service)
    barrier = threading.Barrier(4)
    def click(_):
        barrier.wait()
        return service.act('owner', ident)
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(click, range(4)))
    assert results.count('PAPER acknowledged') == 1
    assert len(service.gateway.submitted) == 1


@pytest.mark.parametrize('lots,limit', [(0, 100), (3, 100), (1, float('nan')), (1, float('inf')), (1, -1)])
def test_bad_order_inputs_rejected(service, lots, limit):
    with pytest.raises(ValueError):
        service.prepare('owner', 'TESTCE', lots, limit)


def test_legacy_executor_has_no_side_effect():
    assert 'disabled' in _quick_execute_option('TESTCE')


def setup_handler(monkeypatch, service, chat='42'):
    handler = TelegramCommandHandler('token', chat)
    handler._review_service = service
    sent = []
    monkeypatch.setattr(handler, '_api', lambda *a, **k: {'ok': True})
    monkeypatch.setattr(handler, 'send', lambda text, **kw: sent.append((text, kw)))
    return handler, sent


def test_group_member_cannot_prepare_or_confirm(monkeypatch, service):
    monkeypatch.setenv('OPTION_ORDER_OWNER_ID', '42')
    handler, sent = setup_handler(monkeypatch, service, '-1001')
    handler._handle_update({'message': {'chat': {'id': -1001}, 'from': {'id': 7}, 'text': '/prepare TESTCE 1 100'}})
    handler._handle_callback({'id': 'x', 'message': {'chat': {'id': -1001}}, 'from': {'id': 7}, 'data': 'exec:TESTCE'})
    assert len(sent) == 2
    assert all('owner' in t for t, _ in sent)
    assert not service.gateway.submitted


def test_group_without_owner_configuration_fails_closed(monkeypatch, service):
    monkeypatch.delenv('OPTION_ORDER_OWNER_ID', raising=False)
    handler, _ = setup_handler(monkeypatch, service, '-1001')
    assert not handler._order_owner('1001')
    assert not handler._order_owner('-1001')


def test_telegram_prepare_confirm_cancel_routes(monkeypatch, service):
    monkeypatch.delenv('OPTION_ORDER_OWNER_ID', raising=False)
    handler, sent = setup_handler(monkeypatch, service)
    handler._handle_update({'message': {'chat': {'id': 42}, 'from': {'id': 42}, 'text': '/prepare TESTCE 1 100.05'}})
    text, meta = sent[-1]
    assert 'PAPER ORDER REVIEW' in text and 'No stop order' in text
    assert not service.gateway.submitted
    callback = meta['reply_markup']['inline_keyboard'][0][0]['callback_data']
    handler._handle_callback({'id': 'x', 'message': {'chat': {'id': 42}}, 'from': {'id': 42}, 'data': callback})
    assert len(service.gateway.submitted) == 1
    handler._handle_callback({'id': 'y', 'message': {'chat': {'id': 42}}, 'from': {'id': 42}, 'data': callback})
    assert len(service.gateway.submitted) == 1


def test_old_exec_button_only_explains_prepare(monkeypatch, service):
    monkeypatch.delenv('OPTION_ORDER_OWNER_ID', raising=False)
    handler, sent = setup_handler(monkeypatch, service)
    handler._handle_callback({'id': 'x', 'message': {'chat': {'id': 42}}, 'from': {'id': 42}, 'data': 'exec:TESTCE'})
    assert '/prepare TESTCE' in sent[-1][0]
    assert not service.gateway.submitted


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    import csv
    import sqlite3
    from datetime import datetime
    import confirmed_option_orders as module
    import request_governor
    import trading_calendar
    class FixedDate(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 15, 10, 0, 5, tzinfo=tz)
    monkeypatch.setattr(module, 'datetime', FixedDate)
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(request_governor, 'acquire', lambda *a: 0)
    monkeypatch.setattr(trading_calendar, 'is_trading_day', lambda *a: True)
    (tmp_path / 'MasterContract_ALL.csv').write_text(
        'token,symbol,expiry,lotsize,instrumenttype,exch_seg,tick_size,freeze_qty\n'
        '123,TEST29OCT26CE,29OCT2026,65,OPTIDX,NFO,5,1801\n')
    with sqlite3.connect(tmp_path / 'manual_trades.db') as db:
        db.execute('CREATE TABLE manual_trades(status TEXT, exit_time TEXT, pnl REAL, realized_pnl REAL)')
    quote = dict(symbolToken='123', exchFeedTime='15-Oct-2026 10:00:00',
                 depth={'sell': [{'quantity': 100, 'price': 100.0}]})
    obj = SimpleNamespace(
        getMarketData=lambda **kw: {'status': True, 'data': {'fetched': [quote]}},
        rmsLimit=lambda: {'status': True, 'data': {'availablecash': '500000'}},
        position=lambda: {'status': True, 'data': []},
        orderBook=lambda: {'status': True, 'data': []},
        placeOrder=lambda params: '123456')
    broker = SimpleNamespace(obj=obj, client_id="fixture", paper_trade=False, block_real_orders=False)
    g = module.AngelReviewGateway(SimpleNamespace(live_engine=SimpleNamespace(trade_manager=SimpleNamespace(trading_locked=False,daily_realized_pnl=0.,open_trades={}))))
    monkeypatch.setattr(g, 'mode', lambda: 'PAPER')
    monkeypatch.setattr(g, 'broker', lambda: broker)
    return g, quote, obj, tmp_path


def test_real_gateway_review_does_not_submit_and_paper_is_honest(gateway):
    g, _, obj, _ = gateway
    obj.placeOrder = lambda params: pytest.fail('prepare/paper must not submit')
    plan = g.review('TEST29OCT26CE', 1, 100.05)
    g.recheck(plan)
    assert plan['quantity'] == 65
    assert 'No broker order or fill' in g.submit(plan)


@pytest.mark.parametrize('change', ['stale', 'future', 'no_depth', 'nan_ask', 'missing_timestamp'])
def test_invalid_quotes_fail_closed(gateway, change):
    g, q, _, _ = gateway
    if change == 'stale':
        q['exchFeedTime'] = '15-Oct-2026 09:59:00'
    elif change == 'future':
        q['exchFeedTime'] = '15-Oct-2026 10:01:00'
    elif change == 'no_depth':
        q['depth'] = {}
    elif change == 'nan_ask':
        q['depth']['sell'][0]['price'] = float('nan')
    else:
        q.pop('exchFeedTime')
    with pytest.raises(ValueError):
        g.review('TEST29OCT26CE', 1, 100.05)


@pytest.mark.parametrize('limit', [99, 102, 100.03])
def test_unapproved_price_band_or_tick_rejected(gateway, limit):
    with pytest.raises(ValueError):
        gateway[0].review('TEST29OCT26CE', 1, limit)


def test_missing_daily_risk_db_fails_closed(gateway):
    g, _, _, root = gateway
    (root / 'manual_trades.db').unlink()
    with pytest.raises(Exception):
        g.review('TEST29OCT26CE', 1, 100.05)
    assert not (root / 'manual_trades.db').exists()


def test_daily_loss_lock_blocks(gateway):
    import sqlite3
    g, _, _, root = gateway
    with sqlite3.connect(root / 'manual_trades.db') as db:
        db.execute("INSERT INTO manual_trades VALUES ('CLOSED','2026-10-15',-5000,0)")
    with pytest.raises(ValueError, match='loss lock'):
        g.review('TEST29OCT26CE', 1, 100.05)


def test_mode_or_lot_change_rejects_frozen_plan(gateway, monkeypatch):
    g, _, _, _ = gateway
    plan = g.review('TEST29OCT26CE', 1, 100.05)
    monkeypatch.setattr(g, 'mode', lambda: 'LIVE')
    with pytest.raises(ValueError, match='mode changed'):
        g.recheck(plan)


def test_live_submit_sends_exact_limit_once_and_never_claims_fill(gateway, monkeypatch):
    g, _, obj, _ = gateway
    monkeypatch.setattr(g, 'mode', lambda: 'LIVE')
    plan = g.review('TEST29OCT26CE', 1, 100.05)
    import config
    monkeypatch.setattr(config, 'PAPER_TRADING', False)
    monkeypatch.setattr(config, 'PAPER_ORDERS_ONLY', False, raising=False)
    monkeypatch.setattr(config, 'ENABLE_REAL_TRADING', True)
    g.recheck(plan)
    plan['review_id'] = 'test-review-id'
    sent = []
    g.broker().place_order = lambda symbol, qty, side, **kw: sent.append(dict(symbol=symbol, quantity=qty, side=side, **kw)) or ('123456', 999)
    result = g.submit(plan)
    assert len(sent) == 1
    assert sent[0]['order_type'] == 'LIMIT'
    assert sent[0]['price'] == 100.05
    assert sent[0]['quantity'] == 65
    assert 'Fill is unverified' in result


@pytest.mark.parametrize('fault', ['cash', 'positions', 'orders', 'broker_block'])
def test_live_risk_checks_block(gateway, monkeypatch, fault):
    g, _, obj, _ = gateway
    monkeypatch.setattr(g, 'mode', lambda: 'LIVE')
    if fault == 'cash':
        obj.rmsLimit = lambda: {'status': True, 'data': {'availablecash': '1'}}
    elif fault == 'positions':
        obj.position = lambda: {'status': True, 'data': [{'netqty': '65'}]}
    elif fault == 'orders':
        obj.orderBook = lambda: {'status': True, 'data': [{'status': 'open'}]}
    else:
        g.broker().block_real_orders = True
    with pytest.raises(ValueError):
        g.review('TEST29OCT26CE', 1, 100.05)


def test_slow_recheck_cannot_submit_after_expiry(service):
    ident = draft(service)
    service.gateway.recheck = lambda plan: setattr(service, 'clock', lambda: 161)
    assert 'expired' in service.act('owner', ident)
    assert not service.gateway.submitted


def test_runtime_mode_requires_fresh_status_and_live_readiness(gateway, monkeypatch):
    import json
    import config
    import live_admission
    from confirmed_option_orders import AngelReviewGateway
    g, _, _, root = gateway
    monkeypatch.setattr(config, 'PAPER_TRADING', False)
    monkeypatch.setattr(config, 'PAPER_ORDERS_ONLY', False, raising=False)
    monkeypatch.setattr(config, 'ENABLE_REAL_TRADING', True)
    path = root / 'live_status.json'
    path.write_text(json.dumps({'mode': 'LIVE', 'timestamp': '2026-10-15T10:00:00+05:30'}))
    monkeypatch.setattr(live_admission, 'evaluate_live_admission', lambda **kw: {'allowed': False})
    with pytest.raises(ValueError, match='readiness'):
        AngelReviewGateway.mode(g)
    monkeypatch.setattr(live_admission, 'evaluate_live_admission', lambda **kw: {'allowed': True})
    assert AngelReviewGateway.mode(g) == 'LIVE'
    path.write_text(json.dumps({'mode': 'PAPER', 'timestamp': '2026-10-15T10:00:00+05:30'}))
    assert AngelReviewGateway.mode(g) == 'PAPER'
    path.write_text(json.dumps({'mode': 'LIVE', 'timestamp': '2026-10-15T09:00:00+05:30'}))
    with pytest.raises(ValueError, match='stale'):
        AngelReviewGateway.mode(g)


def test_quote_refresh_does_not_change_approved_parameters(gateway):
    g, q, _, _ = gateway
    plan = g.review('TEST29OCT26CE', 1, 100.05)
    q['exchFeedTime'] = '15-Oct-2026 10:00:04'
    g.recheck(plan)
    assert plan['verified_quote_time'].startswith('2026-10-15T10:00:04')
    assert plan['limit'] == 100.05 and plan['quantity'] == 65


def test_ambiguous_broker_acknowledgement_is_not_reported_as_filled(gateway, monkeypatch):
    import config
    g, _, _, _ = gateway
    monkeypatch.setattr(g, 'mode', lambda: 'LIVE')
    monkeypatch.setattr(config, 'PAPER_TRADING', False)
    monkeypatch.setattr(config, 'PAPER_ORDERS_ONLY', False, raising=False)
    monkeypatch.setattr(config, 'ENABLE_REAL_TRADING', True)
    plan = g.review('TEST29OCT26CE', 1, 100.05)
    g.recheck(plan)
    plan['review_id'] = 'test'
    g.broker().place_order = lambda *a, **k: None
    with pytest.raises(RuntimeError, match='Ambiguous'):
        g.submit(plan)


def test_combined_manual_and_native_losses_lock_entries(gateway):
    import sqlite3
    g,_,_,root=gateway
    with sqlite3.connect(root/'manual_trades.db') as db:
        db.execute("INSERT INTO manual_trades VALUES ('CLOSED','2026-10-15',-3000,0)")
    g.bot.live_engine.trade_manager.daily_realized_pnl=-3000
    with pytest.raises(ValueError,match='loss lock'):
        g.review('TEST29OCT26CE',1,100.05)


def test_native_positions_block_manual_live_entry(gateway,monkeypatch):
    g,_,_,_=gateway
    monkeypatch.setattr(g,'mode',lambda:'LIVE')
    g.bot.live_engine.trade_manager.open_trades={'other_broker_trade':object()}
    with pytest.raises(ValueError,match='Native portfolio'):
        g.review('TEST29OCT26CE',1,100.05)


def test_review_cannot_move_to_a_different_broker_account(gateway):
    g,_,_,_=gateway
    plan=g.review('TEST29OCT26CE',1,100.05)
    g.broker().client_id='different-account'
    with pytest.raises(ValueError,match='account_fingerprint changed'):
        g.recheck(plan)


def test_review_tag_survives_broker_punctuation_sanitization():
    from confirmed_option_orders import broker_review_tag
    value=broker_review_tag('review_with-urlsafe.punctuation')
    assert len(value)==20 and value.isalnum()
    assert value==broker_review_tag('review_with-urlsafe.punctuation')
