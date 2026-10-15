"""Dated option-contract and executable-quote research evidence.

No broker connections, synthetic timestamps, interpolation or guessed lot sizes.
An explicit import writes a separate SQLite evidence store, never live settings.
"""
from __future__ import annotations
from contextlib import closing
from datetime import datetime, date
import hashlib
import json
import math
from pathlib import Path
import sqlite3


def aware_time(value):
    stamp = datetime.fromisoformat(str(value))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError('Explicit timezone required')
    return stamp


def positive(value, label):
    if isinstance(value, bool):
        raise ValueError(f'Invalid {label}')
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f'Finite positive {label} required')
    return number


def validate_contract(record):
    required = ('contract','underlying','exchange','expiry','lot_size','tick_size',
                'effective_from','effective_to','source')
    if any(record.get(k) in (None, '') for k in required):
        raise ValueError('Dated contract metadata and provenance required')
    start, end, expiry = (date.fromisoformat(record[k]) for k in ('effective_from','effective_to','expiry'))
    if start > end or start > expiry or end > expiry:
        raise ValueError('Invalid metadata effective dates')
    lot = positive(record['lot_size'], 'lot size')
    if not lot.is_integer():
        raise ValueError('Integer lot size required')
    positive(record['tick_size'], 'tick size')
    if record['exchange'] not in ('NFO','BFO') or record.get('option_type') not in ('CE','PE'):
        raise ValueError('Verified option exchange/type required')
    positive(record.get('strike'), 'strike')
    return record


def validate_quote(record, contract, *, as_of, max_age_seconds=10):
    validate_contract(contract)
    now = aware_time(as_of)
    feed, received = aware_time(record.get('exchange_timestamp')), aware_time(record.get('received_at'))
    if record.get('contract') != contract['contract'] or record.get('exchange') != contract['exchange']:
        raise ValueError('Contract/exchange mismatch')
    if record.get('source') in (None, '', 'cache', 'cache_eod', 'modeled'):
        raise ValueError('Observed quote provenance required')
    if not 0 <= (now-feed).total_seconds() <= max_age_seconds or not feed <= received <= now:
        raise ValueError('Stale, future or impossible quote timestamps')
    from zoneinfo import ZoneInfo
    trade_date = now.astimezone(ZoneInfo('Asia/Kolkata')).date()
    if not date.fromisoformat(contract['effective_from']) <= trade_date <= date.fromisoformat(contract['effective_to']):
        raise ValueError('Historical lot/tick metadata missing for quote date')
    bid, ask = positive(record.get('bid'), 'bid'), positive(record.get('ask'), 'ask')
    if bid > ask:
        raise ValueError('Crossed quote')
    for name in ('bid_quantity','ask_quantity'):
        if not positive(record.get(name), name).is_integer():
            raise ValueError('Integer displayed quantities required')
    tick = float(contract['tick_size'])
    if any(abs(price/tick-round(price/tick)) > 1e-6 for price in (bid,ask)):
        raise ValueError('Quote violates dated tick size')
    return record


class ResearchOptionStore:
    def __init__(self, path):
        self.path = str(path)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS contracts (contract TEXT, exchange TEXT, start TEXT, end TEXT, payload TEXT, PRIMARY KEY(contract,exchange,start))')
            db.execute('CREATE TABLE IF NOT EXISTS quotes (fingerprint TEXT PRIMARY KEY,contract TEXT,exchange TEXT,stamp REAL,payload TEXT)')
            db.execute('CREATE INDEX IF NOT EXISTS quote_lookup ON quotes(contract,exchange,stamp)')

    def add_contract(self, record):
        validate_contract(record)
        payload = json.dumps(record, sort_keys=True, allow_nan=False)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute('SELECT payload FROM contracts WHERE contract=? AND exchange=? AND start<=? AND end>=?',
                              (record['contract'],record['exchange'],record['effective_to'],record['effective_from'])).fetchall()
            if rows:
                if len(rows)==1 and rows[0][0]==payload:
                    return False
                raise ValueError('Conflicting/overlapping dated contract metadata')
            db.execute('INSERT INTO contracts VALUES (?,?,?,?,?)',
                       (record['contract'],record['exchange'],record['effective_from'],record['effective_to'],payload))
        return True

    def add_quote(self, record):
        received = aware_time(record.get('received_at'))
        # Canonical option venues are in India; metadata validity uses that session date.
        from zoneinfo import ZoneInfo
        day = received.astimezone(ZoneInfo('Asia/Kolkata')).date().isoformat()
        with closing(sqlite3.connect(self.path)) as db, db:
            row = db.execute('SELECT payload FROM contracts WHERE contract=? AND exchange=? AND start<=? AND end>=?',
                             (record.get('contract'),record.get('exchange'),day,day)).fetchone()
            if row is None:
                raise ValueError('No dated contract metadata; no lot-size fallback')
            validate_quote(record,json.loads(row[0]),as_of=record['received_at'])
            payload = json.dumps(record,sort_keys=True,allow_nan=False)
            digest = hashlib.sha256(payload.encode()).hexdigest()
            cursor = db.execute('INSERT OR IGNORE INTO quotes VALUES (?,?,?,?,?)',
                       (digest,record['contract'],record['exchange'],aware_time(record['exchange_timestamp']).timestamp(),payload))
            return bool(cursor.rowcount)

    def coverage(self):
        with closing(sqlite3.connect(self.path)) as db:
            return dict(contract_versions=db.execute('SELECT COUNT(*) FROM contracts').fetchone()[0],
                        observed_quotes=db.execute('SELECT COUNT(*) FROM quotes').fetchone()[0],
                        synthetic_quotes=0, historical_coverage_certified=False,
                        note='Validated imported records only; coverage and fills are not inferred.')


def main(argv=None):
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,required=True)
    parser.add_argument('--contracts',type=Path)
    parser.add_argument('--quotes',type=Path)
    args=parser.parse_args(argv)
    store=ResearchOptionStore(args.db)
    counts={'contracts_imported':0,'quotes_imported':0,'rejected':[]}
    for path,method,key in ((args.contracts,store.add_contract,'contracts_imported'),(args.quotes,store.add_quote,'quotes_imported')):
        if path:
            for n,line in enumerate(path.read_text().splitlines(),1):
                try:
                    counts[key]+=method(json.loads(line))
                except (ValueError,TypeError,KeyError) as exc:
                    counts['rejected'].append({'file':str(path),'line':n,'reason':str(exc)})
    print(json.dumps(dict(counts,coverage=store.coverage()),indent=2))
    return 1 if counts['rejected'] else 0


if __name__=='__main__':
    raise SystemExit(main())
