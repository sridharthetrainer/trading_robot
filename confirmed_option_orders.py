"""Durable, single-use Telegram option order reviews. No submission on prepare."""
import csv
import hashlib
import json
import math
import secrets
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent


def broker_review_tag(review_id):
    # Angel strips punctuation; hash to a stable 20-character alphanumeric tag.
    return 'TGCF'+hashlib.sha256(str(review_id).encode()).hexdigest()[:16]


class OrderReviews:
    def __init__(self, path, gateway, clock=time.time):
        self.path, self.gateway, self.clock = str(path), gateway, clock
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS reviews (id TEXT PRIMARY KEY, owner TEXT, expires REAL, status TEXT, plan TEXT, result TEXT)')

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def prepare(self, owner, symbol, lots, limit):
        if not symbol.isalnum() or not symbol.endswith(('CE', 'PE')):
            raise ValueError('Use an exact option trading symbol.')
        if isinstance(lots, bool) or not isinstance(lots, int) or not 1 <= lots <= 2:
            raise ValueError('Choose 1 or 2 lots.')
        if not math.isfinite(limit) or limit <= 0:
            raise ValueError('A positive limit price is required.')
        plan = self.gateway.review(symbol, lots, limit)
        ident = secrets.token_urlsafe(16)
        plan['review_id'] = ident
        plan['order_tag'] = broker_review_tag(ident)
        with self.connect() as db:
            db.execute('INSERT INTO reviews VALUES (?,?,?,?,?,?)',
                       (ident, owner, self.clock() + 60, 'PENDING', json.dumps(plan, sort_keys=True), ''))
        return ident, plan

    def act(self, owner, ident, cancel=False):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT owner,expires,status,plan FROM reviews WHERE id=?', (ident,)).fetchone()
            if not row or row[0] != owner:
                return 'Review unavailable for this owner.'
            if row[2] != 'PENDING':
                return f'Review is {row[2]}; it cannot be submitted again.'
            if cancel or self.clock() >= row[1]:
                state = 'CANCELLED' if cancel else 'EXPIRED'
                db.execute('UPDATE reviews SET status=? WHERE id=?', (state, ident))
                return f'Review {state.lower()}.'
            unresolved = db.execute("SELECT id FROM reviews WHERE status IN ('SUBMITTING','UNCERTAIN','ACKNOWLEDGED')", ()).fetchone()
            if unresolved:
                return 'An earlier order needs broker reconciliation. Submission blocked.'
            db.execute("UPDATE reviews SET status='SUBMITTING' WHERE id=?", (ident,))
        plan = json.loads(row[3])
        # A process crash after the committed claim stays SUBMITTING, never retryable.
        try:
            self.gateway.recheck(plan)
            if self.clock() >= row[1]:
                raise ValueError('Review expired during final checks')
        except Exception as exc:
            self.finish(ident, 'BLOCKED', str(exc))
            return f'Order blocked: {exc}. Prepare a new review.'
        try:
            result = self.gateway.submit(plan)
            if not result:
                raise RuntimeError('No unambiguous broker acknowledgement')
        except Exception:
            self.finish(ident, 'UNCERTAIN', 'Check broker order book; do not retry.')
            return 'Submission outcome uncertain. Check the broker order book; automatic retry is blocked.'
        self.finish(ident, 'ACKNOWLEDGED' if plan.get('mode')=='LIVE' else 'SUBMITTED', result)
        return result

    def finish(self, ident, state, result):
        with self.connect() as db:
            db.execute('UPDATE reviews SET status=?,result=? WHERE id=?', (state, str(result), ident))

    def reconcile(self, owner, ident):
        # Read-only broker evidence is required; Telegram has no force-unlock.
        with self.connect() as db:
            row=db.execute('SELECT owner,status,plan FROM reviews WHERE id=?',(ident,)).fetchone()
        if not row or row[0]!=owner:
            return False
        if row[1] not in {'SUBMITTING','UNCERTAIN','ACKNOWLEDGED'}:
            return False
        evidence=self.gateway.reconcile(json.loads(row[2]))
        if not evidence:
            return False
        with self.connect() as db:
            db.execute("UPDATE reviews SET status='RECONCILED',result=? WHERE id=? AND status=?",
                       (json.dumps(evidence,sort_keys=True),ident,row[1]))
        return True

    def status(self, owner):
        with self.connect() as db:
            rows = db.execute('SELECT id,status,result FROM reviews WHERE owner=? ORDER BY rowid DESC LIMIT 5', (owner,)).fetchall()
        if hasattr(self.gateway,'reconcile'):
            changed=False
            for ident,state,_ in rows:
                if state in {'SUBMITTING','UNCERTAIN','ACKNOWLEDGED'}:
                    try:
                        changed=self.reconcile(owner,ident) or changed
                    except Exception:
                        pass  # Failure retains the durable lock.
            if changed:
                with self.connect() as db:
                    rows=db.execute('SELECT id,status,result FROM reviews WHERE owner=? ORDER BY rowid DESC LIMIT 5',(owner,)).fetchall()
        return '\n'.join(f'{i}: {s} {r}' for i, s, r in rows) or 'No order reviews.'


class AngelReviewGateway:
    """Conservative BUY LIMIT entries only; no market orders or spread fallback."""
    def __init__(self, bot):
        self.bot = bot

    def mode(self):
        import config
        status = json.loads((ROOT / 'live_status.json').read_text())
        now = datetime.now(ZoneInfo('Asia/Kolkata'))
        stamp = datetime.fromisoformat(status.get('timestamp', ''))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=now.tzinfo)
        if not 0 <= (now - stamp).total_seconds() <= 120:
            raise ValueError('Runtime mode status is stale')
        runtime = str(status.get('mode', '')).upper()
        paper = bool(config.PAPER_TRADING or getattr(config, 'PAPER_ORDERS_ONLY', False))
        if runtime == 'PAPER':
            return 'PAPER'
        if runtime != 'LIVE' or paper or not config.ENABLE_REAL_TRADING:
            raise ValueError('Execution mode is unavailable or inconsistent')
        from live_admission import evaluate_live_admission
        admission = evaluate_live_admission(force=True)
        if not admission.get('allowed'):
            raise ValueError('Live readiness gate has not passed')
        return 'LIVE'

    def broker(self):
        from angel import AngelOne
        # Reuse the connected singleton. Never log in or alter its mode here.
        candidates = [b for b in AngelOne._instances.values() if b.obj is not None]
        if len(candidates) != 1:
            raise ValueError('A unique connected Angel account is required')
        broker = candidates[0]
        if broker is None or broker.obj is None:
            raise ValueError('Connected Angel broker is unavailable')
        return broker

    def account_fingerprint(self):
        account=str(getattr(self.broker(),'client_id','')).strip()
        if not account:
            raise ValueError('Broker account identity unavailable')
        return hashlib.sha256(account.encode()).hexdigest()

    def review(self, symbol, lots, limit):
        mode = self.mode()
        tm = getattr(getattr(self.bot, 'live_engine', None), 'trade_manager', None)
        if tm is None or getattr(tm, 'trading_locked', True):
            raise ValueError('Trading manager unavailable or locked')
        now = datetime.now(ZoneInfo('Asia/Kolkata'))
        from trading_calendar import is_trading_day
        if not is_trading_day(now.date()) or not (915 <= now.hour * 100 + now.minute < 1530):
            raise ValueError('Market is closed')
        with (ROOT / 'MasterContract_ALL.csv').open() as handle:
            contract = next((r for r in csv.DictReader(line for line in handle if line.strip())
                             if r.get('symbol') == symbol and r.get('exch_seg') == 'NFO'), None)
        if not contract or contract['instrumenttype'] not in ('OPTIDX', 'OPTSTK'):
            raise ValueError('Verified NFO option contract not found')
        if datetime.strptime(contract['expiry'], '%d%b%Y').date() < now.date():
            raise ValueError('Contract has expired')
        lot = int(contract['lotsize'])
        tick = float(contract['tick_size']) / 100
        qty = lots * lot
        if lot <= 0 or tick <= 0 or abs(limit / tick - round(limit / tick)) > 1e-6:
            raise ValueError('Invalid lot size or limit price tick')
        if int(contract.get('freeze_qty') or 0) and qty >= int(contract['freeze_qty']):
            raise ValueError('Quantity reaches exchange freeze threshold')
        broker = self.broker()
        if mode == 'LIVE' and (broker.paper_trade or getattr(broker, 'block_real_orders', False)):
            raise ValueError('Broker blocks live orders')
        from request_governor import acquire
        acquire('angel_quote', 0.35)
        response = broker.obj.getMarketData(mode='FULL', exchangeTokens={'NFO': [contract['token']]})
        fetched = (response.get('data') or {}).get('fetched') or []
        quote = next((q for q in fetched if str(q.get('symbolToken')) == contract['token']), None)
        if not response.get('status') or not quote:
            raise ValueError('Fresh full quote unavailable')
        stamp = datetime.strptime(quote.get('exchFeedTime', ''), '%d-%b-%Y %H:%M:%S').replace(tzinfo=now.tzinfo)
        age = (datetime.now(now.tzinfo) - stamp).total_seconds()
        ask_rows = (quote.get('depth') or {}).get('sell') or []
        ask = min((float(r['price']) for r in ask_rows if float(r.get('quantity', 0)) > 0 and float(r['price']) > 0), default=0)
        if not 0 <= age <= 10 or not math.isfinite(ask) or ask <= 0:
            raise ValueError('Quote is stale or executable ask is unavailable')
        if limit < ask or limit > ask * 1.01:
            raise ValueError('Limit must cover the ask and be within 1% of it')
        # Fail closed if manual daily risk evidence cannot be read.
        risk_path = ROOT / 'manual_trades.db'
        with sqlite3.connect(f'file:{risk_path}?mode=ro', uri=True) as db:
            realized = float(db.execute("SELECT COALESCE(SUM(CASE WHEN status='CLOSED' AND substr(exit_time,1,10)=? THEN pnl WHEN status='OPEN' THEN realized_pnl ELSE 0 END),0) FROM manual_trades", (now.date().isoformat(),)).fetchone()[0])
        # manual_trade_tracker excludes ALGO-tagged bot fills, so these are
        # disjoint books. Include both in the account's daily-loss lock.
        native_realized=float(getattr(tm,'daily_realized_pnl',float('nan')))
        if not math.isfinite(native_realized):
            raise ValueError('Native daily P&L evidence unavailable')
        realized+=native_realized
        if not math.isfinite(realized) or realized <= -5000:
            raise ValueError('Daily risk evidence missing or loss lock reached')
        premium = qty * limit
        # Full premium exposure capped at 5% of the stated 500,000 capital.
        if premium > 25000:
            raise ValueError('Full premium exposure exceeds the 25,000 review cap')
        from option_portfolio_gate import evaluate_option_portfolio
        risk_now=datetime.now(ZoneInfo('Asia/Kolkata'))
        decision=evaluate_option_portfolio(
            dict(side='BUY',product='INTRADAY',quantity=qty,limit=limit,correlation_group='INDEX'), [],
            daily_pnl=realized,snapshot_at=risk_now,now=risk_now)
        if not decision['allowed']:
            raise ValueError(decision['reason'])
        if mode == 'LIVE':
            native_positions=getattr(tm,'open_trades',None)
            if native_positions is None or len(native_positions)>0:
                raise ValueError('Native portfolio positions require reconciliation')
            # Conservative first version: no entry alongside existing broker exposure.
            positions = broker.obj.position()
            orders = broker.obj.orderBook()
            if not positions.get('status') or not orders.get('status'):
                raise ValueError('Broker exposure could not be verified')
            if any(int(r.get('netqty', 0)) != 0 for r in positions.get('data') or []):
                raise ValueError('Existing broker positions require review before a new entry')
            terminal = {'complete', 'rejected', 'cancelled', 'canceled'}
            if any(str(r.get('status', '')).lower() not in terminal for r in orders.get('data') or []):
                raise ValueError('Existing broker orders require reconciliation')
            funds = broker.obj.rmsLimit()
            cash = float((funds.get('data') or {}).get('availablecash', 'nan'))
            if not funds.get('status') or not math.isfinite(cash) or cash < premium * 1.02:
                raise ValueError('Verified cash is insufficient (including 2% reserve)')
        return dict(symbol=symbol, lots=lots, quantity=qty, limit=limit, exchange='NFO',
                    token=contract['token'], product='INTRADAY', side='BUY', mode=mode,
                    premium=premium, ask=ask, account_fingerprint=self.account_fingerprint(), quote_age_seconds=round(age, 2), quote_time=stamp.isoformat())

    def reconcile(self, plan):
        import math
        broker=self.broker()
        response=broker.obj.orderBook()
        if not response.get('status'):
            return None
        if plan.get('account_fingerprint')!=self.account_fingerprint():
            return None
        tag=broker_review_tag(plan['review_id'])
        matches=[r for r in response.get('data') or [] if r.get('ordertag')==tag]
        if len(matches)!=1:
            return None
        row=matches[0]
        expected={'tradingsymbol':plan['symbol'],'exchange':plan['exchange'],
                  'transactiontype':plan['side'],'producttype':plan['product']}
        if any(row.get(k)!=v for k,v in expected.items()):
            return None
        if float(row.get('quantity','nan'))!=plan['quantity'] or float(row.get('price','nan'))!=plan['limit']:
            return None
        state=str(row.get('status','')).lower()
        if state not in {'complete','cancelled','canceled','rejected','expired'}:
            return None
        filled=float(row.get('filledshares','nan'))
        order_id=str(row.get('orderid',''))
        if not math.isfinite(filled) or not filled.is_integer() or not 0<=filled<=plan['quantity'] or not order_id.isdigit():
            return None
        if state=='complete' and filled!=plan['quantity']:
            return None
        avg=float(row.get('averageprice','nan')) if filled else None
        if filled and (not math.isfinite(avg) or avg<=0):
            return None
        return dict(order_id=order_id,status=state,filled_quantity=int(filled),average_price=avg,
                    remaining_order_terminal=True,position_still_requires_monitoring=filled>0)

    def recheck(self, plan):
        current = self.review(plan['symbol'], plan['lots'], plan['limit'])
        if (datetime.now(ZoneInfo('Asia/Kolkata')) - datetime.fromisoformat(current['quote_time'])).total_seconds() > 10:
            raise ValueError('Quote expired during final checks')
        for field in ('symbol', 'quantity', 'limit', 'exchange', 'token', 'product', 'side', 'mode', 'account_fingerprint'):
            if current[field] != plan[field]:
                raise ValueError(f'Reviewed {field} changed')
        plan['verified_quote_time'] = current['quote_time']

    def submit(self, plan):
        if plan['mode'] == 'PAPER':
            return f"PAPER simulation acknowledged: BUY {plan['quantity']} {plan['symbol']} LIMIT {plan['limit']:.2f}. No broker order or fill."
        broker = self.broker()
        import config
        if plan.get('account_fingerprint')!=self.account_fingerprint():
            raise ValueError('Broker account changed before submission')
        if (config.PAPER_TRADING or getattr(config, 'PAPER_ORDERS_ONLY', False)
                or not config.ENABLE_REAL_TRADING or broker.paper_trade
                or getattr(broker, 'block_real_orders', False)):
            raise ValueError('Live execution flags changed before submission')
        tm = getattr(getattr(self.bot, 'live_engine', None), 'trade_manager', None)
        if tm is None or getattr(tm, 'trading_locked', True):
            raise ValueError('Trading locked before submission')
        age = (datetime.now(ZoneInfo('Asia/Kolkata')) - datetime.fromisoformat(plan['verified_quote_time'])).total_seconds()
        if not 0 <= age <= 10:
            raise ValueError('Quote expired before submission')
        # Keep the central broker's paper flags, pacing and order safeguards.
        # Its fallback fill price is deliberately discarded.
        acknowledgement = broker.place_order(plan['symbol'], plan['quantity'], 'BUY',
            order_type='LIMIT', price=plan['limit'], producttype='INTRADAY', exchange='NFO',
            order_tag=broker_review_tag(plan['review_id']))
        order_id = acknowledgement[0] if isinstance(acknowledgement, tuple) and acknowledgement else None
        if not isinstance(order_id, str) or not order_id.isdigit():
            raise RuntimeError('Ambiguous broker acknowledgement')
        return f'Broker acknowledged order {order_id}. Fill is unverified; monitor the broker order book. No stop order is attached.'
