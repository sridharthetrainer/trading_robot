#!/usr/bin/env python3
"""Unified NSE data collector for trading context and audits.

The hub uses official NSE pages/endpoints where available and falls back to the
project's existing cache/modules. It is intentionally additive: failures are
reported per dataset and never block the caller.
"""

from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List

import requests


CACHE_FILE = Path("nse_data_hub_cache.json")
TTL_SEC = 60 * 60

NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    "Accept": "application/json,text/html,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(NSE_HEADERS)
    try:
        s.get("https://www.nseindia.com/", timeout=6)
    except Exception:
        pass
    return s


def _json_get(session: requests.Session, url: str, timeout: int = 12) -> Any:
    r = session.get(url, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    return r.json()


def _rows(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("data", "rows", "results"):
            val = payload.get(key)
            if isinstance(val, list):
                return [r for r in val if isinstance(r, dict)]
    return []


def _ok(name: str, data: Any, **extra: Any) -> Dict[str, Any]:
    count = len(data) if isinstance(data, list) else len(data or {}) if isinstance(data, dict) else 0
    return {"ok": True, "name": name, "count": count, "data": data, **extra}


def _fail(name: str, reason: str, stale: Any = None) -> Dict[str, Any]:
    out = {"ok": False, "name": name, "reason": str(reason)[:180]}
    if stale is not None:
        out["stale"] = stale
    return out


def _cached_payload() -> Dict[str, Any]:
    try:
        raw = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _write_cache(payload: Dict[str, Any]) -> None:
    try:
        CACHE_FILE.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    except Exception:
        pass


def fetch_security_delivery(symbols: Iterable[str] | None = None) -> Dict[str, Any]:
    try:
        from bhav_copy import get_bhav_data

        data = get_bhav_data()
        if symbols:
            wanted = {str(s).upper() for s in symbols}
            data = {k: v for k, v in (data or {}).items() if str(k).upper() in wanted}
        return _ok("security_delivery", data or {})
    except Exception as exc:
        return _fail("security_delivery", str(exc))


def fetch_fno_bhavcopy() -> Dict[str, Any]:
    try:
        from fno_bhavcopy_oi import download_fno_bhavcopy

        df = download_fno_bhavcopy()
        rows = int(len(df) if df is not None else 0)
        cols = list(getattr(df, "columns", []) or [])[:30] if df is not None else []
        return _ok("fno_bhavcopy", {"rows": rows, "columns": cols})
    except Exception as exc:
        return _fail("fno_bhavcopy", str(exc))


def fetch_most_active_contracts(session: requests.Session) -> Dict[str, Any]:
    urls = [
        "https://www.nseindia.com/api/liveEquity-derivatives?index=stock_opt",
        "https://www.nseindia.com/api/liveEquity-derivatives?index=single_stock_fut",
    ]
    collected: List[Dict[str, Any]] = []
    errors = []
    for url in urls:
        try:
            collected.extend(_rows(_json_get(session, url))[:30])
        except Exception as exc:
            errors.append(f"{url}: {exc}")
    if collected:
        return _ok("most_active_contracts", collected[:50])
    return _fail("most_active_contracts", "; ".join(errors) or "no_rows")


def fetch_most_active_underlyings(session: requests.Session) -> Dict[str, Any]:
    urls = [
        "https://www.nseindia.com/api/equity-stockIndices?index=SECURITIES%20IN%20F%26O",
        "https://www.nseindia.com/api/live-analysis-most-active-securities?index=value",
        "https://www.nseindia.com/api/live-analysis-most-active-securities?index=volume",
    ]
    collected: List[Dict[str, Any]] = []
    errors = []
    for url in urls:
        try:
            collected.extend(_rows(_json_get(session, url))[:50])
        except Exception as exc:
            errors.append(f"{url}: {exc}")
    if collected:
        return _ok("most_active_underlyings", collected[:80])
    return _fail("most_active_underlyings", "; ".join(errors) or "no_rows")


def fetch_change_oi(session: requests.Session) -> Dict[str, Any]:
    # NSE changes these endpoints occasionally; try live derivatives first and
    # fall back to local option intelligence if direct rows are unavailable.
    urls = [
        "https://www.nseindia.com/api/liveEquity-derivatives?index=stock_opt",
        "https://www.nseindia.com/api/liveEquity-derivatives?index=index_opt",
    ]
    rows_out: List[Dict[str, Any]] = []
    errors = []
    for url in urls:
        try:
            rows = _rows(_json_get(session, url))
            for row in rows:
                chg = row.get("changeInOpenInterest", row.get("changeinOpenInterest", row.get("CHG_IN_OI")))
                if chg not in (None, "", 0):
                    rows_out.append(row)
            if rows and not rows_out:
                rows_out.extend(rows[:30])
        except Exception as exc:
            errors.append(f"{url}: {exc}")
    if rows_out:
        return _ok("change_in_oi", rows_out[:80])
    return _fail("change_in_oi", "; ".join(errors) or "no_rows")


def fetch_corporate_filings(session: requests.Session) -> Dict[str, Any]:
    endpoints = {
        "announcements": "https://www.nseindia.com/api/corporate-announcements?index=equities",
        "actions": "https://www.nseindia.com/api/corporates-corporateActions?index=equities",
        "board_meetings": "https://www.nseindia.com/api/corporate-board-meetings?index=equities",
        "financial_results": "https://www.nseindia.com/api/corporates-financial-results?index=equities",
    }
    out: Dict[str, Any] = {}
    errors: Dict[str, str] = {}
    for key, url in endpoints.items():
        try:
            out[key] = _rows(_json_get(session, url))[:50]
        except Exception as exc:
            errors[key] = str(exc)[:120]
    if any(out.values()):
        return _ok("corporate_filings", out, errors=errors)
    return _fail("corporate_filings", json.dumps(errors) if errors else "no_rows")


def fetch_surveillance() -> Dict[str, Any]:
    try:
        from asm_gsm_filter import get_asm_gsm_list
        from fno_ban_list import get_ban_list

        asm_gsm = sorted(get_asm_gsm_list(force=True))
        try:
            fno_ban = sorted(get_ban_list(force=True))
        except TypeError:
            fno_ban = sorted(get_ban_list())
        return _ok("surveillance", {"asm_gsm": asm_gsm, "fno_ban": fno_ban})
    except Exception as exc:
        return _fail("surveillance", str(exc))


def fetch_bulk_block() -> Dict[str, Any]:
    try:
        from bulk_deals import get_bulk_deals

        deals = get_bulk_deals(force=True)
        return _ok("bulk_block_deals", deals or [])
    except Exception as exc:
        return _fail("bulk_block_deals", str(exc))


def fetch_fii_dii() -> Dict[str, Any]:
    try:
        from fii_data_fetcher import fetch_nse_fii_dii_today

        return _ok("fii_dii", fetch_nse_fii_dii_today() or {})
    except Exception as exc:
        return _fail("fii_dii", str(exc))


def fetch_participant_oi() -> Dict[str, Any]:
    try:
        from participant_oi import get_participant_data

        return _ok("participant_oi", get_participant_data(force=True) or {})
    except TypeError:
        try:
            from participant_oi import get_participant_data

            return _ok("participant_oi", get_participant_data() or {})
        except Exception as exc:
            return _fail("participant_oi", str(exc))
    except Exception as exc:
        return _fail("participant_oi", str(exc))


def collect_all_nse_data(
    *,
    symbols: Iterable[str] | None = None,
    force: bool = False,
) -> Dict[str, Any]:
    cached = _cached_payload()
    if not force and cached and time.time() - float(cached.get("ts", 0) or 0) < TTL_SEC:
        return cached

    session = _session()
    datasets = {
        "security_delivery": fetch_security_delivery(symbols),
        "fno_bhavcopy": fetch_fno_bhavcopy(),
        "most_active_contracts": fetch_most_active_contracts(session),
        "most_active_underlyings": fetch_most_active_underlyings(session),
        "change_in_oi": fetch_change_oi(session),
        "corporate_filings": fetch_corporate_filings(session),
        "surveillance": fetch_surveillance(),
        "bulk_block_deals": fetch_bulk_block(),
        "fii_dii": fetch_fii_dii(),
        "participant_oi": fetch_participant_oi(),
    }
    ok_count = sum(1 for d in datasets.values() if d.get("ok"))
    payload = {
        "ts": time.time(),
        "date": date.today().isoformat(),
        "ok": ok_count >= 6,
        "ok_count": ok_count,
        "dataset_count": len(datasets),
        "datasets": datasets,
    }
    _write_cache(payload)
    return payload


def summarize_nse_data(payload: Dict[str, Any] | None = None) -> str:
    payload = payload or collect_all_nse_data()
    lines = [
        "NSE data hub",
        f"date={payload.get('date')} ok={payload.get('ok')} {payload.get('ok_count', 0)}/{payload.get('dataset_count', 0)} datasets",
    ]
    for name, data in (payload.get("datasets", {}) or {}).items():
        state = "OK" if data.get("ok") else "WARN"
        detail = f"count={data.get('count', 0)}" if data.get("ok") else data.get("reason", "")
        lines.append(f"{state} {name}: {detail}")
    return "\n".join(lines)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--symbols", default="")
    args = parser.parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or None
    payload = collect_all_nse_data(symbols=symbols, force=args.force)
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(summarize_nse_data(payload))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
