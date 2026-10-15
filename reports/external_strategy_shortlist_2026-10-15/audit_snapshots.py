"""Read-only quote coverage audit; no broker imports, orders or simulated P&L."""
import hashlib
import json
import math
import sqlite3
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent

def positive(value):
    try:
        x = float(value)
        return math.isfinite(x) and x > 0
    except (TypeError, ValueError):
        return False


def main():
    counts = Counter()
    dates = set()
    timestamps = []
    hashes = set()
    all_keys = set()
    summary_keys = set()
    with sqlite3.connect((ROOT / 'option_chain_snapshots.db').as_uri() + '?mode=ro', uri=True) as db:
        rows = db.execute("SELECT snapshot_time,rows_json,summary_json FROM option_chain_snapshots WHERE underlying='NIFTY' AND ok=1 AND is_live=1 ORDER BY snapshot_time")
        for stamp, payload, summary in rows:
            counts['snapshots_marked_live_and_ok'] += 1
            try:
                when = datetime.fromisoformat(stamp)
                dates.add(when.date().isoformat())
                timestamps.append(when)
                options = json.loads(payload)
                info = json.loads(summary or '{}')
                if isinstance(info, dict):
                    summary_keys.update(info)
                digest = hashlib.sha256(payload.encode()).hexdigest()
                counts['duplicate_payload_snapshots'] += digest in hashes
                hashes.add(digest)
                if not isinstance(options, list):
                    raise ValueError('non-list option rows')
                for row in options:
                    all_keys.update(row)
                    for side in ('CE', 'PE'):
                        counts['option_sides_examined'] += 1
                        bid, ask = row.get(side + '_bidPrice'), row.get(side + '_askPrice')
                        if not positive(bid) or not positive(ask):
                            counts['missing_or_nonpositive_l1'] += 1
                        elif float(ask) < float(bid):
                            counts['crossed_l1'] += 1
                        elif not positive(row.get(side + '_bidQty')) or not positive(row.get(side + '_askQty')):
                            counts['missing_or_nonpositive_l1_quantity'] += 1
                        else:
                            counts['positive_uncrossed_l1_with_quantity'] += 1
            except (ValueError, TypeError, KeyError):
                counts['malformed_snapshots'] += 1
    gaps = sorted((b-a).total_seconds() for a,b in zip(timestamps,timestamps[1:])
                  if a.date() == b.date() and b >= a)
    result = dict(as_of='2026-10-15', counts=dict(counts), distinct_snapshot_dates=len(dates),
                  first_date=min(dates) if dates else None, last_date=max(dates) if dates else None,
                  within_day_gap_median_seconds=statistics.median(gaps) if gaps else None,
                  within_day_gap_p95_seconds=gaps[int(.95*(len(gaps)-1))] if gaps else None,
                  row_keys=sorted(all_keys), summary_keys=sorted(summary_keys),
                  interpretation='Live/source flags and positive L1 values do not establish exchange freshness or actual fills. No income probability estimated.')
    (OUT / 'nifty_quote_quality.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))

if __name__ == '__main__':
    main()
