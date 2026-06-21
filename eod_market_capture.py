"""
eod_market_capture.py — persist daily FII/DII + sector-rotation snapshots so they
are available for analysis and (later) ML features.

Why this exists
---------------
Both were being computed live but NOT saved as a historical series:
  - FII/DII: fii_data_fetcher has fetch + save, but refresh_fii_data_eod() was
    never scheduled, so fii_history.csv never accumulated (and signal_log's
    fii_* columns were all defaults).
  - Sector: sector_rotation_engine only wrote a TTL CACHE (sector_rotation_cache.json),
    never a dated history.

This module captures a dated snapshot of each, once per EOD, appending to CSVs
(same pattern as the existing fii_history.csv). It is purely additive and is
wired into the nightly post_market_ml pipeline. No-ops gracefully on non-trading
days (NSE returns nothing).

API:  run_eod_capture() -> {"fii_dii": {...}, "sectors": {...}}
"""
from __future__ import annotations

import csv
import logging
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

SECTOR_CSV   = Path(os.getenv("SECTOR_HISTORY_CSV", "sector_history.csv"))
SECTOR_FIELDS = ["date", "sector", "index", "price", "chg_1d", "chg_5d", "rs", "score", "rank"]
KEEP_DAYS    = int(os.getenv("MARKET_CAPTURE_KEEP_DAYS", "180"))


def capture_fii_dii() -> Dict[str, Any]:
    """Fetch + persist today's FII/DII via the existing saver (fii_history.csv).
    No-op on non-trading days (fetch returns nothing)."""
    try:
        from fii_data_fetcher import fetch_nse_fii_dii_today, save_fii_data
        data = fetch_nse_fii_dii_today()
        if data and (data.get("fii_net") is not None or data.get("dii_net") is not None):
            ok = save_fii_data(data)
            return {"saved": bool(ok), "fii_net": data.get("fii_net"),
                    "dii_net": data.get("dii_net"), "date": data.get("date")}
        return {"saved": False, "reason": "no FII/DII data (non-trading day or NSE down)"}
    except Exception as exc:
        logger.debug("capture_fii_dii: %s", exc)
        return {"saved": False, "error": str(exc)}


def _read_sector_csv(today: str) -> List[Dict[str, str]]:
    if not SECTOR_CSV.exists():
        return []
    try:
        with open(SECTOR_CSV, newline="") as f:
            rows = [r for r in csv.DictReader(f) if r.get("date") != today]
    except Exception:
        return []
    # cap history to the most recent KEEP_DAYS distinct dates
    if rows:
        keep = sorted({r["date"] for r in rows})[-KEEP_DAYS:]
        rows = [r for r in rows if r["date"] in keep]
    return rows


def capture_sectors() -> Dict[str, Any]:
    """Append today's sector-rotation ranking snapshot to sector_history.csv."""
    try:
        from sector_rotation_engine import rank_sectors
        ranked = rank_sectors()
        if not ranked:
            return {"saved": False, "reason": "no sector data"}
        today = date.today().isoformat()
        prior = _read_sector_csv(today)            # drop any existing today + cap
        with open(SECTOR_CSV, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=SECTOR_FIELDS)
            w.writeheader()
            for r in prior:
                w.writerow({k: r.get(k, "") for k in SECTOR_FIELDS})
            for rank, r in enumerate(ranked, start=1):
                w.writerow({"date": today, "sector": r.get("sector", ""),
                            "index": r.get("index", ""), "price": r.get("price", ""),
                            "chg_1d": r.get("chg_1d", ""), "chg_5d": r.get("chg_5d", ""),
                            "rs": r.get("rs", ""), "score": r.get("score", ""),
                            "rank": rank})
        return {"saved": True, "sectors": len(ranked), "file": str(SECTOR_CSV)}
    except Exception as exc:
        logger.debug("capture_sectors: %s", exc)
        return {"saved": False, "error": str(exc)}


def capture_participant_oi(days: int = 7) -> Dict[str, Any]:
    """Refresh participant_oi.db from the NSE archive for the last `days`
    (idempotent — days already stored are skipped). Keeps participant_oi's
    get_participant_data() reading fresh FII/DII/PRO/CLIENT positioning, since
    the live NSE JSON endpoints it tried first return nothing."""
    try:
        from participant_oi_backfill import backfill
        end = date.today()
        stats = backfill(end - timedelta(days=days), end, force=False)
        return {"saved": stats.get("stored", 0) > 0, **stats}
    except Exception as exc:
        logger.debug("capture_participant_oi: %s", exc)
        return {"saved": False, "error": str(exc)}


def run_eod_capture() -> Dict[str, Any]:
    """Capture FII/DII + sector + participant-OI snapshots. Safe to call nightly."""
    fii = capture_fii_dii()
    sec = capture_sectors()
    poi = capture_participant_oi()
    try:
        from nse_data_hub import collect_all_nse_data
        nse = collect_all_nse_data(force=True)
    except Exception as exc:
        logger.debug("capture_nse_data_hub: %s", exc)
        nse = {"ok": False, "error": str(exc)}
    logger.info("EOD market capture: FII/DII=%s | sectors=%s | participant_oi=%s | nse=%s",
                fii, sec, poi, {"ok": nse.get("ok"), "ok_count": nse.get("ok_count")})
    return {"fii_dii": fii, "sectors": sec, "participant_oi": poi, "nse_data_hub": nse}


def market_context_by_date() -> Dict[str, Dict[str, float]]:
    """date('YYYY-MM-DD') -> {hist_fii_net, hist_fii_cum5, sector_breadth} from the
    saved history files. Used to JOIN market context into the ML/analysis feature
    matrix by signal date. Empty dict until history accumulates (trading days)."""
    out: Dict[str, Dict[str, float]] = {}
    # FII/DII cash (fii_history.csv)
    try:
        from fii_data_fetcher import get_fii_history
        h = get_fii_history(400)
        if h is not None and len(h):
            h = h.copy()
            h["date"] = h["date"].astype(str).str[:10]
            h = h.sort_values("date")
            h["_cum5"] = h["fii_net"].rolling(5, min_periods=1).sum()
            for _, r in h.iterrows():
                d = str(r["date"])
                out.setdefault(d, {})["hist_fii_net"]  = float(r.get("fii_net", 0) or 0)
                out[d]["hist_fii_cum5"] = float(r.get("_cum5", 0) or 0)
    except Exception as exc:
        logger.debug("market_context_by_date FII: %s", exc)
    # Sector breadth = mean 1-day change across sectors that day (sector_history.csv)
    try:
        if SECTOR_CSV.exists():
            from collections import defaultdict
            by_date = defaultdict(list)
            with open(SECTOR_CSV, newline="") as f:
                for row in csv.DictReader(f):
                    try:
                        by_date[row["date"]].append(float(row.get("chg_1d") or 0))
                    except (TypeError, ValueError):
                        pass
            for d, chgs in by_date.items():
                if chgs:
                    out.setdefault(d, {})["sector_breadth"] = round(sum(chgs) / len(chgs), 3)
    except Exception as exc:
        logger.debug("market_context_by_date sector: %s", exc)
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    import json
    print(json.dumps(run_eod_capture(), indent=2))
