"""
bulk_deals.py  —  NSE Bulk/Block Deal + Promoter Pledge Tracker

BULK DEALS = large single transactions (>0.5% of shares) reported SAME DAY
BLOCK DEALS = negotiated large trades executed in 35-min block window
INSIDER/PROMOTER = SEBI PIT filings (1-7 day delay but free)

SIGNALS:
  MF/FII bulk buy → institutional accumulation → +score for 3-5 days
  Promoter buy    → insider conviction → +score
  Promoter pledge → stress signal → -score (reduce long bias)
  Promoter revoke → confidence restored → +score
  Bulk sell by FII → distribution phase → -score
  Cluster buys (3+ insiders in 10d) → strong conviction → +score

DATA SOURCES:
  NSE bulk deals:   nseindia.com/api/historical/bulk-deals", "https://www.nseindia.com/api/block-deal (free)
  BSE bulk deals:   bseindia.com bulk deal API (free)
  Promoter data:    nseindia.com announcements (free)
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date, timedelta
from pathlib import Path
from typing import List

import requests

logger = logging.getLogger(__name__)
_BULK_CACHE  = Path("bulk_deals_cache.json")
_TTL         = 3600 * 6   # 6 hour cache


def _fetch_bulk_deals(days: int = 5) -> List[dict]:
    """Fetch recent bulk deals from NSE."""
    deals = []
    try:
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0",
                           "Referer": "https://www.nseindia.com/"})
        s.get("https://www.nseindia.com/", timeout=6)
        url = (
            "https://www.nseindia.com/api/historical/bulk-deals"
            f"?from={_date_str(-days)}&to={_date_str(0)}"
        )
        r = s.get(url, timeout=12)
        if r.status_code == 200:
            raw = r.json()
            for row in (raw.get("data", []) or []):
                deals.append({
                    "symbol":   str(row.get("symbol", "")).upper(),
                    "client":   str(row.get("clientName", "")),
                    "trade":    str(row.get("buySell", "")).upper(),
                    "qty":      float(row.get("quantityTraded", 0) or 0),
                    "price":    float(row.get("tradePrice", 0) or 0),
                    "date":     str(row.get("mktType", date.today().isoformat())),
                })
    except Exception as e:
        logger.debug("Bulk deals fetch: %s", e)
    return deals


def _date_str(offset_days: int) -> str:
    d = date.today() + timedelta(days=offset_days)
    return d.strftime("%d-%m-%Y")


def get_bulk_deals(force: bool = False) -> List[dict]:
    if not force:
        try:
            if _BULK_CACHE.exists():
                d = json.loads(_BULK_CACHE.read_text())
                if time.time() - d.get("ts", 0) < _TTL:
                    return d.get("deals", [])
        except Exception:
            pass
    deals = _fetch_bulk_deals(days=5)
    try:
        _BULK_CACHE.write_text(json.dumps({"ts": time.time(), "deals": deals}))
    except Exception:
        pass
    return deals


def bulk_deal_score(symbol: str, direction: str) -> float:
    """
    Score modifier based on recent bulk deals in a symbol.
    Positive = smart money aligned with your direction.
    Negative = smart money opposing your direction.
    """
    deals = get_bulk_deals()
    sym = symbol.upper().replace(".NS", "")

    sym_deals = [d for d in deals if d.get("symbol", "") == sym]
    if not sym_deals:
        return 0.0

    # Identify deal types
    institutional_clients = {"MUTUAL FUND", "FII", "FPI", "INSURANCE",
                              "LIC", "SBI", "HDFC", "ICICI", "RELIANCE"}

    inst_buys  = 0
    inst_sells = 0
    promo_buys = 0

    for d in sym_deals:
        client = d.get("client", "").upper()
        trade  = d.get("trade", "")
        is_inst = any(kw in client for kw in institutional_clients)
        is_promo = "PROMOTER" in client or "DIRECTOR" in client

        if is_inst:
            if "BUY" in trade:  inst_buys  += 1
            else:               inst_sells += 1
        if is_promo:
            if "BUY" in trade:  promo_buys += 1

    modifier = 0.0
    if inst_buys >= 2 and direction == "BUY":
        modifier += 1.0   # institutional accumulation
    if inst_sells >= 2 and direction == "BUY":
        modifier -= 0.8   # institutional distribution
    if promo_buys >= 1 and direction == "BUY":
        modifier += 1.2   # promoter conviction
    if inst_buys >= 1 and direction == "SELL":
        modifier -= 0.6

    return round(modifier, 2)


def get_bulk_deal_summary() -> str:
    """Summary for Telegram morning brief."""
    deals = get_bulk_deals()
    if not deals:
        return "Bulk deals: no recent data"
    buys  = [d for d in deals if "BUY" in d.get("trade","").upper()]
    sells = [d for d in deals if "SELL" in d.get("trade","").upper()]
    syms_bought = list({d["symbol"] for d in buys})[:5]
    syms_sold   = list({d["symbol"] for d in sells})[:3]
    lines = [f"📦 Bulk deals (5d): {len(deals)} total"]
    if syms_bought: lines.append(f"  Bought: {', '.join(syms_bought)}")
    if syms_sold:   lines.append(f"  Sold:   {', '.join(syms_sold)}")
    return "\n".join(lines)
