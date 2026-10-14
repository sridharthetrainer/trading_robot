"""Fetch daily India VIX closes (2019-01 -> today) from NSE's historicalOR API.
Output: india_vix_2019_2026.csv (date, vix_close). Read-only otherwise.
    .venv/bin/python reports/research_2026-10-14/fetch_india_vix.py
"""
from __future__ import annotations

import csv
import time
from datetime import date, datetime
from pathlib import Path

import requests

OUT = Path(__file__).resolve().parent / "india_vix_2019_2026.csv"


def main() -> None:
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)", "Accept": "*/*",
                      "Referer": "https://www.nseindia.com/reports-indices-historical-vix"})
    s.get("https://www.nseindia.com/reports-indices-historical-vix", timeout=15)
    rows, failed = {}, []
    y, m = 2019, 1
    today = date.today()
    while (y, m) <= (today.year, today.month):
        first = date(y, m, 1)
        nxt = date(y + (m == 12), m % 12 + 1, 1)
        last = min(date.fromordinal(nxt.toordinal() - 1), today)
        url = ("https://www.nseindia.com/api/historicalOR/vixhistory"
               f"?from={first:%d-%m-%Y}&to={last:%d-%m-%Y}")
        for _ in range(3):
            try:
                for rec in s.get(url, timeout=20).json()["data"]:
                    d = datetime.strptime(rec["EOD_TIMESTAMP"], "%d-%b-%Y").date().isoformat()
                    rows[d] = float(rec["EOD_CLOSE_INDEX_VAL"])
                break
            except Exception:
                time.sleep(3)
        else:
            failed.append(f"{first:%Y-%m}")
        y, m = nxt.year, nxt.month
        time.sleep(1.0)
    with OUT.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "vix_close"])
        for d in sorted(rows):
            w.writerow([d, rows[d]])
    print(f"days={len(rows)} first={min(rows)} last={max(rows)} failed_months={failed}")


if __name__ == "__main__":
    main()
