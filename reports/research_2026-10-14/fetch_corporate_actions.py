"""Fetch NSE split/bonus history (2016-07 → today) and derive price factors.

NSE bhavcopy PREV_CLOSE turned out NOT to be adjusted on ex-dates (checked on
TATASTEEL 1:10, RELIANCE 1:1 bonus, IRCTC 1:5), so split factors come from
NSE's corporate-actions API instead. Output: corporate_actions_2016_2026.json
(split/bonus rows with a parsed price factor). Read-only otherwise.
    .venv/bin/python reports/research_2026-10-14/fetch_corporate_actions.py
"""
from __future__ import annotations

import json
import re
import time
from datetime import date
from pathlib import Path

import requests

OUT = Path(__file__).resolve().parent / "corporate_actions_2016_2026.json"


def price_factor(subject: str) -> float | None:
    """Multiplier applied to pre-ex-date prices (e.g. 1:10 split -> 0.1)."""
    s = subject.upper()
    bonus = re.search(r"BONUS\s*(\d+)\s*:\s*(\d+)", s)
    if bonus:
        new, held = int(bonus.group(1)), int(bonus.group(2))
        return held / (held + new)
    if "SPLIT" in s or "SUB-DIVISION" in s or "CONSOLIDATION" in s:
        fv = re.findall(r"R[SE]\.?\s*(\d+(?:\.\d+)?)", s)
        if len(fv) >= 2 and float(fv[0]) > 0:
            return float(fv[1]) / float(fv[0])
    return None


def months(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        first = date(y, m, 1)
        nxt = date(y + (m == 12), m % 12 + 1, 1)
        yield first, min(nxt.replace(day=1), end)
        y, m = nxt.year, nxt.month


def main() -> None:
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0", "Accept": "application/json",
                      "Referer": "https://www.nseindia.com/"})
    s.get("https://www.nseindia.com/", timeout=10)
    rows, failed = [], []
    for first, nxt in months(date(2016, 7, 1), date.today()):
        last = date.fromordinal(nxt.toordinal() - 1) if nxt > first else first
        url = ("https://www.nseindia.com/api/corporates-corporateActions?index=equities"
               f"&from_date={first:%d-%m-%Y}&to_date={last:%d-%m-%Y}")
        for attempt in range(3):
            try:
                r = s.get(url, timeout=20)
                if r.status_code == 401 or r.status_code == 403:
                    s.get("https://www.nseindia.com/", timeout=10)
                    raise RuntimeError(r.status_code)
                data = r.json()
                break
            except Exception:
                time.sleep(3)
        else:
            failed.append(f"{first:%Y-%m}")
            continue
        for x in data:
            subj = str(x.get("subject", ""))
            if not re.search(r"BONUS|SPLIT|SUB-DIVISION|CONSOLIDATION|DEMERGER|ARRANGEMENT", subj.upper()):
                continue
            rows.append({"symbol": x.get("symbol"), "series": x.get("series"),
                         "ex_date": x.get("exDate"), "subject": subj,
                         "factor": price_factor(subj),
                         # demerger/arrangement: no ratio in the text; the
                         # adjusted-price builder uses the observed ex-date move
                         "kind": "demerger" if re.search(r"DEMERGER|ARRANGEMENT", subj.upper())
                                 and not re.search(r"BONUS|SPLIT", subj.upper()) else "split_bonus"})
        time.sleep(1.5)
    OUT.write_text(json.dumps({"fetched": date.today().isoformat(), "failed_months": failed,
                               "actions": rows}, indent=1))
    print(f"actions={len(rows)} unparsed={sum(r['factor'] is None for r in rows)} failed_months={failed}")


if __name__ == "__main__":
    main()
