#!/usr/bin/env python3
"""
data_pipeline_audit.py

Audit data wiring without placing orders.

Run:
    python data_pipeline_audit.py
    python data_pipeline_audit.py --fetch-sample 5
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List


REPORT_JSON = "data_pipeline_audit.json"
REPORT_MD = "DATA_PIPELINE_AUDIT.md"
INSTITUTIONAL_TARGET_DAYS = max(
    1, int(os.getenv("INSTITUTIONAL_TARGET_DAYS", os.getenv("ML_TRAINING_DAYS", "15")))
)
INSTITUTIONAL_TARGET_LABELLED = max(
    100, int(os.getenv("INSTITUTIONAL_TARGET_LABELLED", "5000"))
)


SOURCE_CATALOG = [
    {
        "domain": "intraday_price_bars",
        "need": "1m/5m OHLCV for symbols and indices",
        "preferred": "Angel SmartAPI historical candle data",
        "local": ["angel.py", "data_fetcher.py", "candle_cache.db"],
        "env_any": ["API_KEY"],
        "fallbacks": ["NSE direct probes", "local candle cache", "yf_compat informational fallback"],
        "internet_refs": ["https://smartapi.angelbroking.com/docs/Orders"],
        "critical": True,
    },
    {
        "domain": "tick_or_1sec_data",
        "need": "Tick/1-second stream for spike, slippage, stop-hunt and entry timing analysis",
        "preferred": "Broker websocket tick feed captured to local storage",
        "local": ["websocket_engine.py", "websocket_tracker.py", "tick_order_flow.py", "intraday_candle_recorder.py"],
        "env_any": ["API_KEY"],
        "fallbacks": ["1m candles", "5m candles", "intraday candle recorder"],
        "internet_refs": ["https://smartapi.angelbroking.com/docs/WebSocket2"],
        "critical": False,
        "institutional": True,
    },
    {
        "domain": "nse_option_chain",
        "need": "NIFTY/BANKNIFTY/FINNIFTY strike OI, volume, IV and LTP",
        "preferred": "NSE option-chain endpoint with Angel/Sensibull fallback",
        "local": ["option_chain_fetcher.py", "option_chain_intelligence.py", "option_chain_recorder.py"],
        "fallbacks": ["option_chain_cache.json", "option_chain_snapshots.db"],
        "internet_refs": ["https://www.nseindia.com/option-chain"],
        "critical": True,
    },
    {
        "domain": "bse_sensex_bfo",
        "need": "SENSEX/BANKEX/BFO index and option context",
        "preferred": "BSE public APIs and Angel BSE/BFO historical access",
        "local": ["bse_option_chain.py", "bse_option_chain_cache.json", "angel_broker.py"],
        "fallbacks": ["BSE SENSEX API", "local BSE option cache"],
        "internet_refs": ["https://www.bseindia.com/", "https://api.bseindia.com/"],
        "critical": True,
    },
    {
        "domain": "participant_oi",
        "need": "F&O participant positioning and trading volume",
        "preferred": "NSE derivative reports",
        "local": ["participant_oi.py", "participant_oi_cache.json", "participant_oi_history.json"],
        "fallbacks": ["participant OI cache/history"],
        "internet_refs": ["https://www.nseindia.com/all-reports-derivatives"],
        "critical": True,
    },
    {
        "domain": "fii_dii_flows",
        "need": "Cash market FII/DII net buy/sell context",
        "preferred": "NSE FII/FPI & DII reports",
        "local": ["fii_data_fetcher.py", "fii_tracker.py", "omnisource_cache.json"],
        "fallbacks": ["cached FII/DII flow file"],
        "internet_refs": ["https://www.nseindia.com/reports/fii-dii"],
        "critical": True,
    },
    {
        "domain": "bulk_block_deals",
        "need": "Large deal / block deal context",
        "preferred": "NSE bulk/block archives",
        "local": ["bulk_deals.py", "bulk_deals_cache.json"],
        "fallbacks": ["cached NSE bulk/block data"],
        "internet_refs": ["https://www.nseindia.com/report-detail/display-bulk-and-block-deals"],
        "critical": False,
    },
    {
        "domain": "nse_full_market_reports",
        "need": "Delivery, F&O bhavcopy, most active contracts, change OI, filings, surveillance",
        "preferred": "Unified NSE data hub backed by official NSE reports/endpoints",
        "local": ["nse_data_hub.py", "nse_data_hub_cache.json"],
        "fallbacks": [
            "bhav_copy.py",
            "fno_bhavcopy_oi.py",
            "corporate_actions.py",
            "asm_gsm_filter.py",
            "bulk_deals.py",
        ],
        "internet_refs": [
            "https://www.nseindia.com/all-reports",
            "https://www.nseindia.com/all-reports-derivatives",
        ],
        "critical": False,
    },
    {
        "domain": "corporate_actions",
        "need": "Corporate actions and announcements",
        "preferred": "BSE/NSE corporate announcement feeds",
        "local": ["corporate_actions.py", "corporate_actions_cache.json"],
        "fallbacks": ["corporate action cache"],
        "internet_refs": [
            "https://www.bseindia.com/corporates/ann.html",
            "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
        ],
        "critical": False,
    },
    {
        "domain": "news_sentiment",
        "need": "Market and symbol news sentiment",
        "preferred": "NewsAPI key with cached/free fallback",
        "local": ["news_nlp.py", "news_sentiment_cache.json", "news_nlp_cache.json"],
        "env_any": ["NEWS_API_KEY"],
        "fallbacks": ["cached news sentiment"],
        "internet_refs": ["https://newsapi.org/"],
        "critical": False,
    },
    {
        "domain": "option_chain_addon_candidate",
        "need": "Richer all-strike chain with Greeks, bid/ask, IV, volume",
        "preferred": "DhanHQ option-chain API if credentials are intentionally enabled",
        "local": ["dhan_client.py"],
        "env_any": ["DHAN_CLIENT_CODE", "DHAN_TOKEN_ID"],
        "fallbacks": ["current NSE/Angel option-chain stack"],
        "internet_refs": ["https://dhanhq.co/docs/v2/option-chain/"],
        "critical": False,
        "optional": True,
    },
    {
        "domain": "market_depth_orderbook",
        "need": "Bid/ask spread and market depth for selected cash/options instruments",
        "preferred": "Broker quote/depth API or websocket depth packets",
        "local": ["market_intelligence_hub.py", "order_flow.py", "smart_order_router.py", "execution_algo.py"],
        "env_any": ["API_KEY"],
        "fallbacks": ["spread gate", "option quality spread checks"],
        "internet_refs": ["https://smartapi.angelbroking.com/docs/MarketData"],
        "critical": False,
        "institutional": True,
    },
    {
        "domain": "execution_fill_quality",
        "need": "Order, fill, slippage, partial fill, rejection and latency dataset",
        "preferred": "Broker order book/trade book reconciled into trades.db and slippage reports",
        "local": ["trade_manager.py", "execution_monitor.py", "slippage_analyzer.py", "signal_trade_reconciler.py"],
        "fallbacks": ["trades.db", "slippage_report.json"],
        "internet_refs": ["https://smartapi.angelbroking.com/docs/Orders"],
        "critical": False,
        "institutional": True,
    },
    {
        "domain": "market_profile_history",
        "need": "POC, VAH, VAL, HVN/LVN and value acceptance/rejection snapshots",
        "preferred": "market_profile_snapshots.db captured during live scan",
        "local": ["market_profile_context.py", "market_profile_snapshots.db", "volume_profile_advanced.py"],
        "fallbacks": ["on-demand profile from current OHLCV"],
        "critical": False,
        "institutional": True,
    },
    {
        "domain": "sector_breadth_rotation",
        "need": "Advance/decline, sector leadership, contribution and rotation state",
        "preferred": "NSE sector/index feeds plus local sector history",
        "local": ["sector_rotation_engine.py", "sector_history.csv", "market_intelligence.py"],
        "fallbacks": ["sector_rotation_cache.json", "market breadth cache"],
        "internet_refs": ["https://www.nseindia.com/market-data/live-equity-market"],
        "critical": False,
        "institutional": True,
    },
    {
        "domain": "vol_surface_skew",
        "need": "IV surface, CE/PE skew, skew velocity and term structure",
        "preferred": "All-strike option chain IV history",
        "local": ["vol_surface.py", "iv_percentile.py", "greeks_live.py", "institutional_alpha.py"],
        "fallbacks": ["current option chain IV", "India VIX"],
        "critical": False,
        "institutional": True,
    },
    {
        "domain": "broker_latency_health",
        "need": "API latency, quote delay, order latency, fetch failures and reconnect history",
        "preferred": "connection monitor plus health monitor persisted daily",
        "local": ["connection_monitor.py", "health_monitor.py", "open_health_check.py", "system_monitor.py"],
        "fallbacks": ["runtime logs"],
        "critical": False,
        "institutional": True,
    },
    {
        "domain": "alerts_and_backup",
        "need": "Telegram command/alert delivery and Google Drive backup",
        "preferred": "Telegram Bot API and rclone Google Drive remote",
        "local": ["telegram_commands.py", "alerts.py", "gdrive_sync.py"],
        "env_any": ["TELEGRAM_BOT_TOKEN"],
        "fallbacks": ["alert spool", "local files"],
        "internet_refs": ["https://api.telegram.org/", "https://rclone.org/drive/"],
        "critical": False,
    },
]


def _ok(name: str, **extra) -> Dict[str, Any]:
    return {"name": name, "ok": True, **extra}


def _fail(name: str, reason: str, **extra) -> Dict[str, Any]:
    return {"name": name, "ok": False, "reason": reason, **extra}


def _read_json(path: str) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _sqlite_table_count(db_path: str, table: str) -> Dict[str, Any]:
    p = Path(db_path)
    if not p.exists():
        return {"exists": False, "count": 0}
    try:
        conn = sqlite3.connect(str(p))
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        conn.close()
        return {"exists": True, "count": int(count or 0)}
    except Exception as exc:
        return {"exists": True, "count": 0, "error": str(exc)}


def _historical_option_store_count() -> Dict[str, Any]:
    candidates = [
        ("options_nifty.db", "options_eod"),
        ("historical_options.db", "options_eod"),
        ("historical_options.db", "options"),
    ]
    for db_path, table in candidates:
        result = _sqlite_table_count(db_path, table)
        if result.get("exists") and not result.get("error"):
            result.update({"db_path": db_path, "table": table})
            return result
    return {"exists": False, "count": 0}


def _module_check(name: str) -> Dict[str, Any]:
    try:
        importlib.import_module(name)
        return _ok(f"import:{name}")
    except Exception as exc:
        return _fail(f"import:{name}", str(exc))


def _load_env_flags() -> Dict[str, bool]:
    env: Dict[str, str] = {}
    p = Path(".env")
    if p.exists():
        for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip().strip('"').strip("'")
    merged = {**env, **os.environ}
    keys = [
        "API_KEY",
        "CLIENT_ID",
        "PASSWORD",
        "TOTP_SECRET",
        "DHAN_CLIENT_CODE",
        "DHAN_TOKEN_ID",
        "UPSTOX_ACCESS_TOKEN",
        "UPSTOX_TOKEN",
        "FYERS_TOKEN",
        "ZERODHA_API_KEY",
        "ZERODHA_ACCESS_TOKEN",
        "TWELVE_DATA_KEY",
        "ALPHA_VANTAGE_KEY",
        "TIINGO_KEY",
        "NSE_PROXY",
        "NEWS_API_KEY",
        "TELEGRAM_BOT_TOKEN",
        "DISABLE_YFINANCE",
    ]
    return {key: bool(str(merged.get(key, "")).strip()) for key in keys}


def _audit_source_config() -> Dict[str, Any]:
    flags = _load_env_flags()
    angel_ok = all(flags.get(k) for k in ("API_KEY", "CLIENT_ID", "PASSWORD", "TOTP_SECRET"))
    backup_credentials = {
        "dhan": bool(flags.get("DHAN_CLIENT_CODE") and flags.get("DHAN_TOKEN_ID")),
        "upstox": bool(flags.get("UPSTOX_ACCESS_TOKEN") or flags.get("UPSTOX_TOKEN")),
        "fyers": bool(flags.get("FYERS_TOKEN")),
        "zerodha": bool(flags.get("ZERODHA_API_KEY") and flags.get("ZERODHA_ACCESS_TOKEN")),
        "twelve_data": bool(flags.get("TWELVE_DATA_KEY")),
        "alpha_vantage": bool(flags.get("ALPHA_VANTAGE_KEY")),
        "tiingo_env": bool(flags.get("TIINGO_KEY")),
        "nse_proxy": bool(flags.get("NSE_PROXY")),
    }
    free_fallbacks = {
        "nse_direct": True,
        "nse_equity": True,
        "stooq": True,
        "upstox_public": True,
        "bhavcopy_cache": True,
        "local_candle_cache": Path("candle_cache.db").exists(),
    }
    payload = {
        "angel_primary_configured": angel_ok,
        "backup_credentials": backup_credentials,
        "free_fallbacks": free_fallbacks,
        "disable_yfinance": flags.get("DISABLE_YFINANCE", False),
    }
    if angel_ok:
        return _ok("source_config", **payload)
    return _fail("source_config", "angel_primary_credentials_missing", **payload)


def _audit_internet_source_catalog() -> Dict[str, Any]:
    flags = _load_env_flags()
    rows: List[Dict[str, Any]] = []
    critical_gaps: List[str] = []
    optional_gaps: List[str] = []
    for item in SOURCE_CATALOG:
        local = item.get("local", []) or []
        env_any = item.get("env_any", []) or []
        existing = [p for p in local if Path(str(p)).exists()]
        missing = [p for p in local if not Path(str(p)).exists()]
        env_configured = [key for key in env_any if flags.get(str(key))]
        cache_or_module_ok = bool(existing)
        credential_ok = True if not env_any else bool(env_configured)
        has_fallback = bool(item.get("fallbacks"))
        covered = cache_or_module_ok and (credential_ok or has_fallback or item.get("optional"))
        row = {
            "domain": item.get("domain"),
            "need": item.get("need"),
            "preferred": item.get("preferred"),
            "covered": bool(covered),
            "critical": bool(item.get("critical")),
            "optional": bool(item.get("optional")),
            "local_present": existing,
            "local_missing": missing,
            "env_configured": env_configured,
            "env_expected_any": env_any,
            "fallbacks": item.get("fallbacks", []),
            "internet_refs": item.get("internet_refs", []),
        }
        rows.append(row)
        if not covered and item.get("critical"):
            critical_gaps.append(str(item.get("domain")))
        elif not covered:
            optional_gaps.append(str(item.get("domain")))

    recommendations = []
    if not flags.get("NEWS_API_KEY"):
        recommendations.append("Add NEWS_API_KEY only if live headline sentiment should be stronger than cache fallback.")
    if not (flags.get("DHAN_CLIENT_CODE") and flags.get("DHAN_TOKEN_ID")):
        recommendations.append("Optional: Dhan option-chain API can add Greeks, bid/ask and richer all-strike data.")
    if not flags.get("TELEGRAM_BOT_TOKEN"):
        recommendations.append("Add TELEGRAM_BOT_TOKEN for command delivery; alert spool protects local logs without it.")
    payload = {
        "sources": rows,
        "source_count": len(rows),
        "covered_count": sum(1 for r in rows if r.get("covered")),
        "critical_gaps": critical_gaps,
        "optional_gaps": optional_gaps,
        "recommendations": recommendations,
    }
    if critical_gaps:
        return _fail("internet_source_catalog", "critical_source_coverage_gap", **payload)
    return _ok("internet_source_catalog", **payload)


def _probe_url(url: str, timeout: float = 8.0) -> Dict[str, Any]:
    started = time.time()
    try:
        import requests

        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
            "Accept": "text/html,application/json,*/*",
        }
        method = "HEAD"
        try:
            resp = requests.head(url, headers=headers, timeout=timeout, allow_redirects=True)
            if resp.status_code in (403, 405) or resp.status_code >= 500:
                method = "GET"
                resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        except Exception:
            method = "GET"
            resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        return {
            "url": url,
            "ok": 200 <= int(resp.status_code) < 500,
            "status": int(resp.status_code),
            "method": method,
            "duration_sec": round(time.time() - started, 3),
        }
    except Exception as exc:
        return {
            "url": url,
            "ok": False,
            "reason": str(exc)[:180],
            "duration_sec": round(time.time() - started, 3),
        }


def _audit_internet_reachability() -> Dict[str, Any]:
    urls = []
    for item in SOURCE_CATALOG:
        for url in item.get("internet_refs", []) or []:
            if url not in urls:
                urls.append(url)
    # Keep the internet probe bounded; the connection audit tests detailed APIs.
    selected = urls[:12]
    probes = [_probe_url(url) for url in selected]
    ok_count = sum(1 for p in probes if p.get("ok"))
    payload = {"requested": len(selected), "ok_count": ok_count, "results": probes}
    if ok_count >= max(1, len(selected) - 3):
        return _ok("internet_source_reachability", **payload)
    return _fail("internet_source_reachability", "too_many_official_source_probe_failures", **payload)


def _find_check(checks: List[Dict[str, Any]], name: str) -> Dict[str, Any]:
    for check in checks:
        if check.get("name") == name:
            return check
    return {}


def _score_data_pipeline(checks: List[Dict[str, Any]]) -> Dict[str, Any]:
    weights = {
        "source_coverage": 20,
        "broker_runtime": 15,
        "fetcher_universe": 15,
        "storage_capture": 15,
        "learning_readiness": 10,
        "sample_fetch": 15,
        "freshness_reachability": 10,
    }
    parts: Dict[str, Dict[str, Any]] = {}
    improvements: List[str] = []

    source = _find_check(checks, "internet_source_catalog")
    source_count = max(1, int(source.get("source_count", 0) or 0))
    covered_count = int(source.get("covered_count", 0) or 0)
    critical_gaps = source.get("critical_gaps", []) or []
    source_score = weights["source_coverage"] * min(1.0, covered_count / source_count)
    if critical_gaps:
        source_score *= 0.55
        improvements.append(f"Close critical data source gaps: {', '.join(critical_gaps)}.")
    for rec in source.get("recommendations", []) or []:
        improvements.append(str(rec))
    parts["source_coverage"] = {
        "score": round(source_score, 1),
        "max": weights["source_coverage"],
        "detail": f"{covered_count}/{source_count} source domains covered",
    }

    runtime = _find_check(checks, "runtime_broker_wiring")
    broker_score = 0.0
    broker_score += 5.0 if runtime.get("angel_attached") else 0.0
    broker_score += 7.0 if runtime.get("angel_obj_connected") else 0.0
    broker_score += 3.0 if runtime.get("angel_paper_trade") is False else 0.0
    if runtime.get("angel_paper_trade") is not False:
        improvements.append("Confirm live/paper mode before market open; runtime broker is not in live order mode.")
    parts["broker_runtime"] = {
        "score": round(min(weights["broker_runtime"], broker_score), 1),
        "max": weights["broker_runtime"],
        "detail": f"brokers={','.join(runtime.get('brokers', []) or []) or 'none'}",
    }

    fetcher = _find_check(checks, "data_fetcher_wiring")
    universe = _find_check(checks, "universe")
    ordered = int(fetcher.get("ordered_symbols", 0) or 0)
    full = int(fetcher.get("full_ordered_symbols", 0) or 0)
    learning_count = int(universe.get("learning_count", 0) or 0)
    fetcher_score = 0.0
    fetcher_score += 6.0 if full >= 190 else 4.0 if full >= 100 else 0.0
    if ordered >= 150:
        fetcher_score += 5.0
    elif ordered >= 60:
        fetcher_score += 3.5
        improvements.append("Active ordered universe is below full universe; expand after live fetch stability is confirmed.")
    else:
        improvements.append("Active ordered universe is small; check liquidity mode and probation filters.")
    fetcher_score += 4.0 if learning_count >= 60 else 2.5 if learning_count >= 20 else 0.0
    parts["fetcher_universe"] = {
        "score": round(min(weights["fetcher_universe"], fetcher_score), 1),
        "max": weights["fetcher_universe"],
        "detail": f"ordered={ordered}, full={full}, learning={learning_count}",
    }

    storage = _find_check(checks, "storage")
    candles = int((storage.get("candle_cache_db") or {}).get("count", 0) or 0)
    candle_intervals = storage.get("candle_intervals", {}) if isinstance(storage.get("candle_intervals"), dict) else {}
    one_min_symbols = int((candle_intervals.get("1m", {}) or {}).get("symbols", 0) or 0)
    daily_symbols = int((candle_intervals.get("1d", {}) or {}).get("symbols", 0) or 0)
    signals = int((storage.get("signal_log_db") or {}).get("count", 0) or 0)
    option_snaps = int((storage.get("option_chain_snapshots_db") or {}).get("count", 0) or 0)
    historical_options = int((storage.get("historical_options_db") or {}).get("count", 0) or 0)
    confluence = int((storage.get("confluence_features_db") or {}).get("count", 0) or 0)
    coverage_plan = storage.get("candle_coverage_plan") if isinstance(storage.get("candle_coverage_plan"), dict) else {}
    coverage_by_interval = {
        str(row.get("interval")): row
        for row in (coverage_plan.get("interval_plans", []) or [])
        if isinstance(row, dict)
    }
    storage_score = 0.0
    storage_score += 4.0 if candles >= 100000 else 2.0 if candles >= 10000 else 0.0
    storage_score += 3.0 if signals >= 1000 else 1.5 if signals >= 100 else 0.0
    storage_score += 3.0 if confluence >= 1000 else 1.5 if confluence >= 100 else 0.0
    storage_score += 2.0 if storage.get("option_journal_exists") else 0.0
    storage_score += 2.0 if historical_options >= 100000 else 1.0 if historical_options >= 10000 else 0.0
    one_min_plan = coverage_by_interval.get("1m", {})
    daily_plan = coverage_by_interval.get("1d", {})
    if one_min_symbols and one_min_symbols < 100:
        missing = int(one_min_plan.get("missing_symbols", 0) or 0)
        if missing:
            improvements.append(
                f"Expand 1m candle coverage for {missing} missing symbols; run the next candle_coverage_plan batch."
            )
        else:
            improvements.append("Expand 1m candle coverage for entry timing, spike and stop-management learning.")
    if daily_symbols and daily_symbols < 100:
        missing = int(daily_plan.get("missing_symbols", 0) or 0)
        if missing:
            improvements.append(
                f"Expand 1d candle coverage for {missing} missing symbols; run the next candle_coverage_plan batch."
            )
        else:
            improvements.append("Expand 1d candle coverage for trend, regime and gap-context learning.")
    if option_snaps >= 50:
        storage_score += 1.0
    elif option_snaps > 0:
        storage_score += 0.5
        improvements.append("Increase successful intraday option-chain snapshots; this is the biggest gap for strike-flow learning.")
    else:
        improvements.append("Start option-chain recorder during market hours; no option snapshots are available.")
    parts["storage_capture"] = {
        "score": round(min(weights["storage_capture"], storage_score), 1),
        "max": weights["storage_capture"],
        "detail": (
            f"candles={candles}, 1m_symbols={one_min_symbols}, 1d_symbols={daily_symbols}, "
            f"signals={signals}, option_snaps={option_snaps}, "
            f"historical_options={historical_options}, confluence={confluence}, "
            f"coverage_plan={'yes' if coverage_plan else 'no'}"
        ),
    }

    learning = _find_check(checks, "learning_files")
    storage = _find_check(checks, "storage")
    param_training = (
        storage.get("autonomous_param_training")
        if isinstance(storage.get("autonomous_param_training"), dict)
        else {}
    )
    fill_telemetry = (
        storage.get("execution_fill_telemetry")
        if isinstance(storage.get("execution_fill_telemetry"), dict)
        else {}
    )
    derived_daily = (
        storage.get("derived_daily_candles")
        if isinstance(storage.get("derived_daily_candles"), dict)
        else {}
    )
    data_quality = (
        storage.get("data_quality_watchdog")
        if isinstance(storage.get("data_quality_watchdog"), dict)
        else {}
    )
    experiments_logged = int((storage.get("experiments_db") or {}).get("count", 0) or 0)
    live_ready = int(learning.get("live_ready_count", 0) or 0)
    total_strategies = max(1, int(learning.get("total_strategies", 0) or 0))
    selected = int(learning.get("autotune_selected", 0) or 0)
    shadow = int(learning.get("autotune_shadow", 0) or 0)
    learning_score = min(6.0, 6.0 * live_ready / total_strategies)
    learning_score += 2.0 if selected > 0 else 0.0
    learning_score += 2.0 if shadow > 0 else 0.0
    if Path("autonomous_param_trainer.py").exists():
        learning_score += 0.5
    promoted_params = len(param_training.get("promoted", []) or [])
    paper_params = len(param_training.get("paper_only", []) or [])
    if promoted_params > 0:
        learning_score += 1.0
    elif paper_params > 0:
        learning_score += 0.5
    fill_trades = int(fill_telemetry.get("trades", 0) or 0)
    derived_daily_symbols = int(derived_daily.get("symbols_ok", 0) or 0)
    total_quality_groups = max(1, int(data_quality.get("total_groups", 0) or 0))
    bad_quality_groups = int(data_quality.get("bad_groups", 0) or 0)
    if fill_trades >= 10:
        learning_score += 0.5
    if derived_daily_symbols >= 100:
        learning_score += 0.5
    if data_quality and bad_quality_groups / total_quality_groups <= 0.05:
        learning_score += 0.5
    if experiments_logged > 0:
        learning_score += 0.5
    if live_ready == 0:
        improvements.append("Build live-ready strategy labels through shadow/live journaling before increasing size.")
    if shadow == 0:
        improvements.append("Collect shadow option strike outcomes so autotune can compare selected vs missed strikes.")
    if not param_training:
        improvements.append("Run autonomous_param_trainer nightly to validate and promote strategy/indicator parameters.")
    elif promoted_params == 0:
        improvements.append("Parameter trainer is wired; keep collecting data until a parameter set clears DSR and holdout gates.")
    parts["learning_readiness"] = {
        "score": round(min(weights["learning_readiness"], learning_score), 1),
        "max": weights["learning_readiness"],
        "detail": (
            f"live_ready={live_ready}/{total_strategies}, selected={selected}, shadow={shadow}, "
            f"param_promoted={promoted_params}, param_paper={paper_params}, "
            f"fill_trades={fill_trades}, derived_daily_symbols={derived_daily_symbols}, "
            f"bad_candle_groups={bad_quality_groups}/{total_quality_groups}, "
            f"experiments={experiments_logged}"
        ),
    }

    sample = _find_check(checks, "fetch_sample")
    if sample.get("skipped"):
        requested = 0
        ok_count = 0
        sample_score = weights["sample_fetch"]
        sample_detail = "skipped; run with --fetch-sample N for live pull validation"
    else:
        requested = max(1, int(sample.get("requested", 0) or 0))
        ok_count = int(sample.get("ok_count", 0) or 0)
        sample_score = weights["sample_fetch"] * min(1.0, ok_count / requested)
        sample_detail = f"{ok_count}/{requested} sample fetches OK"
    if requested and ok_count < requested:
        improvements.append("Fix failed sample fetch symbols before widening the scan universe.")
    parts["sample_fetch"] = {
        "score": round(sample_score, 1),
        "max": weights["sample_fetch"],
        "detail": sample_detail,
    }

    freshness = _find_check(checks, "market_hours_freshness")
    reach = _find_check(checks, "internet_source_reachability")
    fresh_score = 5.0 if freshness.get("ok") else 0.0
    if freshness.get("skipped"):
        fresh_score = 4.0
    if reach:
        reach_requested = max(1, int(reach.get("requested", 0) or 0))
        reach_ok = int(reach.get("ok_count", 0) or 0)
        reach_score = 5.0 * min(1.0, reach_ok / reach_requested)
        if reach_ok < reach_requested:
            improvements.append("Telegram/API internet reachability still has at least one failed official endpoint.")
    else:
        reach_score = 4.0
    parts["freshness_reachability"] = {
        "score": round(min(weights["freshness_reachability"], fresh_score + reach_score), 1),
        "max": weights["freshness_reachability"],
        "detail": "market-hours freshness plus official-source reachability",
    }

    total = round(sum(p["score"] for p in parts.values()), 1)
    grade = "A" if total >= 90 else "B" if total >= 80 else "C" if total >= 70 else "D" if total >= 60 else "F"
    learning_part_score = float(parts.get("learning_readiness", {}).get("score", 0.0) or 0.0)
    readiness = (
        "LIVE_READY"
        if total >= 88 and learning_part_score >= 5.0 and not critical_gaps
        else "PAPER_OR_SHADOW"
        if total >= 72
        else "FIX_BEFORE_LIVE"
    )
    unique_improvements = []
    seen = set()
    for item in improvements:
        if item and item not in seen:
            unique_improvements.append(item)
            seen.add(item)
    return {
        "total": total,
        "max": sum(weights.values()),
        "grade": grade,
        "readiness": readiness,
        "parts": parts,
        "top_improvements": unique_improvements[:8],
    }


def _audit_runtime_broker_wiring() -> Dict[str, Any]:
    try:
        import config as cfg
        from broker_manager import BrokerManager

        broker_config = {
            "API_KEY": getattr(cfg, "API_KEY", ""),
            "CLIENT_ID": getattr(cfg, "CLIENT_ID", ""),
            "PASSWORD": getattr(cfg, "PASSWORD", ""),
            "TOTP_SECRET": getattr(cfg, "TOTP_SECRET", ""),
            "DHAN_CLIENT_CODE": getattr(cfg, "DHAN_CLIENT_CODE", ""),
            "DHAN_TOKEN_ID": getattr(cfg, "DHAN_TOKEN_ID", ""),
            "PAPER_TRADE": bool(getattr(cfg, "PAPER_TRADE", getattr(cfg, "PAPER_TRADING", True))),
        }
        manager = BrokerManager(broker_config)
        broker_names = []
        angel_attached = False
        angel_obj_connected = False
        angel_paper_trade = None
        for broker in getattr(manager, "brokers", []) or []:
            name = type(broker).__name__
            try:
                name = broker.get_name()
            except Exception:
                pass
            broker_names.append(name)
            angel = getattr(broker, "angel", None)
            if angel is not None and hasattr(angel, "get_historical_data"):
                angel_attached = True
                angel_obj_connected = bool(getattr(angel, "obj", None))
                angel_paper_trade = getattr(angel, "paper_trade", None)
                break
        payload = {
            "brokers": broker_names,
            "angel_attached": angel_attached,
            "angel_obj_connected": angel_obj_connected,
            "angel_paper_trade": angel_paper_trade,
        }
        if angel_attached:
            return _ok("runtime_broker_wiring", **payload)
        return _fail("runtime_broker_wiring", "no_angel_historical_client_attached", **payload)
    except Exception as exc:
        return _fail("runtime_broker_wiring", str(exc))


def _audit_fetcher_fallback_wiring() -> Dict[str, Any]:
    try:
        from data_fetcher import DataFetcher

        methods = [
            "_fetch_from_angel",
            "_fetch_via_smartconnect",
            "_fetch_from_twelvedata",
            "_fetch_from_tiingo",
            "_fetch_from_nse_direct",
            "_fetch_nse_live_single",
        ]
        fetcher = DataFetcher(symbols_csv="nifty200.csv" if Path("nifty200.csv").exists() else None)
        method_flags = {name: hasattr(fetcher, name) for name in methods}
        module_flags = {
            name: importlib.util.find_spec(name) is not None
            for name in (
                "dhan_client",
                "zerodha_client",
                "upstox_data",
                "bhavcopy_cache",
                "candle_cache",
                "data_source_resilience",
                "yf_compat",
            )
        }
        ok = all(method_flags.values()) and all(module_flags.values())
        payload = {"methods": method_flags, "modules": module_flags}
        if ok:
            return _ok("fetcher_fallback_wiring", **payload)
        return _fail("fetcher_fallback_wiring", "missing_fetcher_method_or_module", **payload)
    except Exception as exc:
        return _fail("fetcher_fallback_wiring", str(exc))


def _audit_universe() -> Dict[str, Any]:
    try:
        from universe_manager import describe_universe
        desc = describe_universe()
        ok = int(desc.get("learning_count", 0) or 0) >= 6
        if ok:
            return _ok("universe", **desc)
        return _fail("universe", "learning_universe_too_small", **desc)
    except Exception as exc:
        return _fail("universe", str(exc))


def _audit_symbol_csv() -> Dict[str, Any]:
    p = Path("nifty200.csv")
    if not p.exists():
        return _fail("nifty200.csv", "missing")
    try:
        import csv
        with p.open(newline="", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        cols = reader.fieldnames or []
        ok = "Symbol" in cols and len(rows) > 0
        payload = {"rows": len(rows), "columns": cols, "mtime": p.stat().st_mtime}
        if ok:
            return _ok("nifty200.csv", **payload)
        return _fail("nifty200.csv", "missing_Symbol_column_or_empty", **payload)
    except Exception as exc:
        return _fail("nifty200.csv", str(exc))


def _audit_data_fetcher() -> Dict[str, Any]:
    try:
        from data_fetcher import DataFetcher
        dfetch = DataFetcher(symbols_csv="nifty200.csv" if Path("nifty200.csv").exists() else None)
        symbols = dfetch.get_ordered_symbols() if hasattr(dfetch, "get_ordered_symbols") else []
        full_symbols = (
            dfetch.get_ordered_symbols(include_full_universe=True)
            if hasattr(dfetch, "get_ordered_symbols")
            else []
        )
        return _ok(
            "data_fetcher_wiring",
            ordered_symbols=len(symbols),
            full_ordered_symbols=len(full_symbols),
            preview=symbols[:20],
            angel_attached=bool(getattr(dfetch, "angel", None)),
        )
    except Exception as exc:
        return _fail("data_fetcher_wiring", str(exc))


def _audit_option_fetchers() -> Dict[str, Any]:
    try:
        from live_signal_engine import SUPPORTED_OPTION_UNDERLYINGS
        underlyings = sorted(SUPPORTED_OPTION_UNDERLYINGS)
    except Exception as exc:
        return _fail("option_underlyings", str(exc))
    caches = sorted(str(p) for p in Path(".").glob("*option_chain_cache*.json"))
    return _ok(
        "option_fetchers",
        underlyings=underlyings,
        count=len(underlyings),
        cache_files=caches,
    )


def _audit_storage() -> Dict[str, Any]:
    trades = _sqlite_table_count("trades.db", "trades")
    signals = _sqlite_table_count("signal_log.db", "signal_log")
    candles = _sqlite_table_count("candle_cache.db", "candles")
    candle_meta = _sqlite_table_count("candle_cache.db", "cache_meta")
    option_snaps = _sqlite_table_count("option_chain_snapshots.db", "option_chain_snapshots")
    profile_snaps = _sqlite_table_count("market_profile_snapshots.db", "market_profile_snapshots")
    historical_options = _historical_option_store_count()
    market_snaps = _sqlite_table_count("market_snapshots.db", "market_snapshots")
    confluence = _sqlite_table_count("confluence_features.db", "confluence_features")
    nse_hub = _read_json("nse_data_hub_cache.json")
    candle_coverage_plan = _read_json("candle_coverage_plan.json")
    param_training = _read_json("autonomous_param_training_report.json")
    slippage_report = _read_json("slippage_report.json")
    fill_telemetry = _read_json("execution_fill_telemetry.json")
    derived_daily = _read_json("derived_daily_candles_report.json")
    data_quality = _read_json("data_quality_watchdog_report.json")
    experiments = _sqlite_table_count("experiments.db", "experiments")
    candle_intervals: Dict[str, Any] = {}
    if Path("candle_cache.db").exists():
        try:
            with sqlite3.connect("candle_cache.db") as conn:
                rows = conn.execute(
                    """
                    SELECT interval, COUNT(*) rows, COUNT(DISTINCT symbol) symbols,
                           MIN(timestamp), MAX(timestamp)
                      FROM candles
                     GROUP BY interval
                    """
                ).fetchall()
            candle_intervals = {
                str(r[0]): {
                    "rows": int(r[1] or 0),
                    "symbols": int(r[2] or 0),
                    "first": r[3],
                    "last": r[4],
                }
                for r in rows
            }
        except Exception:
            candle_intervals = {}
    try:
        from option_decision_journal import ensure_option_journal
        ensure_option_journal()
    except Exception:
        pass
    journal = Path("option_decision_journal.jsonl")
    return _ok(
        "storage",
        trades_db=trades,
        signal_log_db=signals,
        candle_cache_db=candles,
        candle_cache_meta=candle_meta,
        candle_intervals=candle_intervals,
        option_chain_snapshots_db=option_snaps,
        market_profile_snapshots_db=profile_snaps,
        historical_options_db=historical_options,
        market_snapshots_db=market_snaps,
        confluence_features_db=confluence,
        nse_data_hub={
            "exists": bool(nse_hub),
            "ok": bool(nse_hub.get("ok")),
            "ok_count": int(nse_hub.get("ok_count", 0) or 0),
            "dataset_count": int(nse_hub.get("dataset_count", 0) or 0),
            "date": nse_hub.get("date", ""),
        },
        candle_coverage_plan=candle_coverage_plan,
        autonomous_param_training=param_training,
        slippage_report=slippage_report,
        execution_fill_telemetry=fill_telemetry,
        derived_daily_candles=derived_daily,
        data_quality_watchdog=data_quality,
        experiments_db=experiments,
        option_journal_exists=journal.exists(),
        option_journal_size=journal.stat().st_size if journal.exists() else 0,
    )


def _audit_labelled_dataset() -> Dict[str, Any]:
    if not Path("signal_log.db").exists():
        return _fail("labelled_dataset", "signal_log_missing", labelled=0, distinct_days=0)
    try:
        conn = sqlite3.connect("signal_log.db")
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN tb_label IN (-1,0,1) THEN 1 ELSE 0 END) AS legacy_labelled,
                SUM(CASE WHEN tb_label IN (-1,0,1) AND training_eligible=1
                          AND stop_loss>0 AND target>0 AND rr>0 THEN 1 ELSE 0 END) AS labelled,
                SUM(CASE WHEN executed = 1 THEN 1 ELSE 0 END) AS executed,
                COUNT(DISTINCT CASE WHEN tb_label IN (-1,0,1) AND training_eligible=1
                          AND stop_loss>0 AND target>0 AND rr>0 THEN signal_date END) AS distinct_days
            FROM signal_log
            """
        ).fetchone()
        by_label = conn.execute(
            """
            SELECT tb_label, COUNT(*) AS n
              FROM signal_log
             GROUP BY tb_label
             ORDER BY tb_label
            """
        ).fetchall()
        conn.close()
        payload = {
            "total": int(row["total"] or 0),
            "labelled": int(row["labelled"] or 0),
            "legacy_labelled": int(row["legacy_labelled"] or 0),
            "executed": int(row["executed"] or 0),
            "distinct_days": int(row["distinct_days"] or 0),
            "by_label": {str(r["tb_label"]): int(r["n"] or 0) for r in by_label},
            "target_labelled": INSTITUTIONAL_TARGET_LABELLED,
            "target_days": INSTITUTIONAL_TARGET_DAYS,
        }
        payload["institutional_gap"] = not (
            payload["labelled"] >= payload["target_labelled"]
            and payload["distinct_days"] >= payload["target_days"]
        )
        ok = payload["labelled"] >= 100 and payload["distinct_days"] >= 1
        if ok:
            return _ok("labelled_dataset", **payload)
        return _fail("labelled_dataset", "more_labelled_days_needed", **payload)
    except Exception as exc:
        return _fail("labelled_dataset", str(exc), labelled=0, distinct_days=0)


def _score_institutional_readiness(checks: List[Dict[str, Any]]) -> Dict[str, Any]:
    source = _find_check(checks, "internet_source_catalog")
    storage = _find_check(checks, "storage")
    labels = _find_check(checks, "labelled_dataset")
    sources = source.get("sources", []) or []

    def covered(domain: str) -> bool:
        for row in sources:
            if row.get("domain") == domain:
                return bool(row.get("covered"))
        return False

    option_snaps = int((storage.get("option_chain_snapshots_db") or {}).get("count", 0) or 0)
    profile_snaps = int((storage.get("market_profile_snapshots_db") or {}).get("count", 0) or 0)
    historical_options = int((storage.get("historical_options_db") or {}).get("count", 0) or 0)
    candles = int((storage.get("candle_cache_db") or {}).get("count", 0) or 0)
    trades = int((storage.get("trades_db") or {}).get("count", 0) or 0)
    slippage_report = storage.get("slippage_report") if isinstance(storage.get("slippage_report"), dict) else {}
    fill_telemetry = (
        storage.get("execution_fill_telemetry")
        if isinstance(storage.get("execution_fill_telemetry"), dict)
        else {}
    )
    matched_slippage = int(slippage_report.get("matched_pairs", 0) or 0)
    fill_trades = int(fill_telemetry.get("trades", 0) or 0)
    order_id_coverage = float(fill_telemetry.get("order_id_coverage_pct", 0.0) or 0.0)
    labelled = int(labels.get("labelled", 0) or 0)
    distinct_days = int(labels.get("distinct_days", 0) or 0)
    target_labelled = int(labels.get("target_labelled", INSTITUTIONAL_TARGET_LABELLED) or INSTITUTIONAL_TARGET_LABELLED)
    target_days = int(labels.get("target_days", INSTITUTIONAL_TARGET_DAYS) or INSTITUTIONAL_TARGET_DAYS)
    mid_labelled = max(1000, int(target_labelled * 0.35))
    mid_days = max(5, int(target_days * 0.5))

    parts = {
        "tick_or_1sec_data": {
            "score": 8 if covered("tick_or_1sec_data") and candles >= 100000 else 4 if candles >= 10000 else 0,
            "max": 10,
            "detail": f"tick modules={covered('tick_or_1sec_data')}, candles={candles}",
        },
        "all_strike_option_history": {
            "score": (
                14 if option_snaps >= 1000
                else 12 if historical_options >= 1000000 and option_snaps >= 100
                else 9 if option_snaps >= 100
                else 7 if historical_options >= 100000
                else 5 if option_snaps > 0
                else 0
            ),
            "max": 15,
            "detail": f"option_snapshots={option_snaps}, historical_options={historical_options}",
        },
        "market_depth_spread": {
            "score": 8 if covered("market_depth_orderbook") else 4,
            "max": 10,
            "detail": "depth modules present; live depth depends on broker entitlement",
        },
        "execution_fill_quality": {
            "score": (
                10 if trades >= 100 and matched_slippage >= 50
                else 8 if fill_trades >= 10 and order_id_coverage >= 80
                else 7 if fill_trades >= 10 and matched_slippage >= 3
                else 6 if trades >= 10
                else 3 if covered("execution_fill_quality")
                else 0
            ),
            "max": 12,
            "detail": (
                f"trades={trades}, fill_report_trades={fill_trades}, "
                f"matched_slippage={matched_slippage}, order_id_coverage={order_id_coverage:.1f}%"
            ),
        },
        "labelled_learning_dataset": {
            "score": (
                16 if labelled >= target_labelled and distinct_days >= target_days
                else 10 if labelled >= mid_labelled and distinct_days >= mid_days
                else 5 if labelled >= 100
                else 0
            ),
            "max": 18,
            "detail": f"labelled={labelled}/{target_labelled}, days={distinct_days}/{target_days}",
        },
        "participant_fii_flows": {
            "score": 10 if covered("participant_oi") and covered("fii_dii_flows") else 5,
            "max": 10,
            "detail": "participant OI plus FII/DII flow coverage",
        },
        "market_profile_history": {
            "score": 8 if profile_snaps >= 500 else 5 if profile_snaps > 0 else 3 if covered("market_profile_history") else 0,
            "max": 8,
            "detail": f"profile_snapshots={profile_snaps}",
        },
        "sector_news_events": {
            "score": 7 if covered("sector_breadth_rotation") and covered("news_sentiment") and covered("corporate_actions") else 4,
            "max": 7,
            "detail": "sector breadth, news and corporate event coverage",
        },
        "vol_surface_skew": {
            "score": 6 if covered("vol_surface_skew") and option_snaps > 0 else 3 if covered("vol_surface_skew") else 0,
            "max": 6,
            "detail": "IV/skew modules require all-strike snapshots for full edge",
        },
        "broker_latency_health": {
            "score": 4 if covered("broker_latency_health") else 0,
            "max": 4,
            "detail": "health monitor modules present; live latency history depends on runtime",
        },
    }
    total = round(sum(float(p["score"]) for p in parts.values()), 1)
    max_score = sum(int(p["max"]) for p in parts.values())
    blockers: List[str] = []
    if option_snaps < 100:
        blockers.append("Need sustained all-strike option-chain snapshots during market hours.")
    if labelled < target_labelled or distinct_days < target_days:
        blockers.append(
            f"Need {target_days} labelled signal days and about {target_labelled} labels before trusting institutional ML weights."
        )
    if trades < 10:
        blockers.append("Need more real/paper execution fill records for slippage and order-quality learning.")
    if profile_snaps == 0:
        blockers.append("Run live scanner to populate market_profile_snapshots.db.")
    grade = "A" if total >= 82 else "B" if total >= 70 else "C" if total >= 55 else "D" if total >= 40 else "F"
    readiness = "INSTITUTIONAL_READY" if total >= 82 and not blockers else "BUILDING" if total >= 55 else "DATA_GAPS"
    return {
        "total": total,
        "max": max_score,
        "grade": grade,
        "readiness": readiness,
        "parts": parts,
        "blockers": blockers,
    }


def _audit_learning_files() -> Dict[str, Any]:
    live = _read_json("live_eligibility.json")
    tune = _read_json("option_strike_autotune.json")
    auto = _read_json("autonomous_learning_report.json")
    ok = bool(live) and bool(tune)
    payload = {
        "live_ready_count": live.get("live_ready_count", 0),
        "total_strategies": live.get("total_strategies", 0),
        "autotune_selected": tune.get("labelled_selected", 0),
        "autotune_shadow": tune.get("labelled_shadow", 0),
        "autonomous_report_exists": bool(auto),
    }
    if ok:
        return _ok("learning_files", **payload)
    return _fail("learning_files", "missing_live_eligibility_or_autotune", **payload)


def _fetch_sample(limit: int) -> Dict[str, Any]:
    if limit <= 0:
        return _ok("fetch_sample", skipped=True)
    try:
        from data_fetcher import DataFetcher
        angel = None
        try:
            import os
            from dotenv import load_dotenv
            from angel import AngelOne

            load_dotenv(".env")
            angel = AngelOne(
                api_key=os.getenv("API_KEY", ""),
                client_id=os.getenv("CLIENT_ID", ""),
                password=os.getenv("PASSWORD", ""),
                totp_secret=os.getenv("TOTP_SECRET", ""),
            )
        except Exception:
            angel = None
        dfetch = DataFetcher(
            angel=angel,
            paper_trade=False,
            symbols_csv="nifty200.csv" if Path("nifty200.csv").exists() else None,
        )
        symbols = dfetch.get_ordered_symbols()[:limit]
        results = []
        required = {"open", "high", "low", "close"}
        for symbol in symbols:
            started = time.time()
            row: Dict[str, Any] = {"symbol": symbol}
            try:
                df = dfetch.get_market_data(symbol, interval="5m", days=5)
                cols = {str(c).lower() for c in getattr(df, "columns", [])} if df is not None else set()
                row.update({
                    "ok": bool(df is not None and len(df) >= 5 and required.issubset(cols)),
                    "bars": int(len(df) if df is not None else 0),
                    "columns": sorted(cols),
                    "duration_sec": round(time.time() - started, 3),
                })
                try:
                    idx = getattr(df, "index", None)
                    if idx is not None and len(idx) > 0:
                        row["last_index"] = str(idx[-1])
                except Exception:
                    pass
            except Exception as exc:
                row.update({"ok": False, "reason": str(exc), "duration_sec": round(time.time() - started, 3)})
            results.append(row)
        ok_count = sum(1 for r in results if r.get("ok"))
        payload = {"requested": len(symbols), "ok_count": ok_count, "results": results}
        if ok_count == len(results):
            return _ok("fetch_sample", **payload)
        return _fail("fetch_sample", "one_or_more_sample_fetches_failed", **payload)
    except Exception as exc:
        return _fail("fetch_sample", str(exc))


def _audit_market_hours_freshness(limit: int = 3) -> Dict[str, Any]:
    from datetime import datetime, time as dtime

    now = datetime.now()
    in_market = now.weekday() < 5 and dtime(9, 15) <= now.time() <= dtime(15, 30)
    if not in_market:
        return _ok("market_hours_freshness", skipped=True, reason="outside_market_hours")
    try:
        from data_fetcher import DataFetcher

        dfetch = DataFetcher(symbols_csv="nifty200.csv" if Path("nifty200.csv").exists() else None)
        symbols = dfetch.get_ordered_symbols()[: max(1, int(limit or 3))]
        rows = []
        for symbol in symbols:
            row: Dict[str, Any] = {"symbol": symbol}
            try:
                df = dfetch.get_market_data(symbol, interval="5m", days=1)
                idx = getattr(df, "index", None)
                if df is None or len(df) == 0 or idx is None or len(idx) == 0:
                    row.update({"ok": False, "reason": "empty"})
                else:
                    import pandas as pd
                    last_ts = pd.Timestamp(idx[-1])
                    if last_ts.tzinfo is not None:
                        last_ts = last_ts.tz_convert("Asia/Kolkata").tz_localize(None)
                    age_min = (pd.Timestamp.now() - last_ts).total_seconds() / 60.0
                    row.update({
                        "ok": age_min <= 20,
                        "last_index": str(idx[-1]),
                        "age_min": round(age_min, 1),
                        "bars": int(len(df)),
                    })
            except Exception as exc:
                row.update({"ok": False, "reason": str(exc)})
            rows.append(row)
        ok_count = sum(1 for r in rows if r.get("ok"))
        payload = {"requested": len(rows), "ok_count": ok_count, "results": rows}
        if ok_count == len(rows):
            return _ok("market_hours_freshness", **payload)
        return _fail("market_hours_freshness", "stale_or_empty_5m_bars_during_market_hours", **payload)
    except Exception as exc:
        return _fail("market_hours_freshness", str(exc))


def run_audit(fetch_sample: int = 0, internet: bool = False) -> Dict[str, Any]:
    checks = [
        _module_check("config"),
        _module_check("universe_manager"),
        _module_check("data_fetcher"),
        _module_check("live_signal_engine"),
        _module_check("option_chain_fetcher"),
        _audit_source_config(),
        _audit_internet_source_catalog(),
        _audit_fetcher_fallback_wiring(),
        _audit_runtime_broker_wiring(),
        _audit_symbol_csv(),
        _audit_universe(),
        _audit_data_fetcher(),
        _audit_option_fetchers(),
        _audit_storage(),
        _audit_learning_files(),
        _audit_labelled_dataset(),
        _fetch_sample(fetch_sample),
        _audit_market_hours_freshness(),
    ]
    if internet:
        checks.append(_audit_internet_reachability())
    score = _score_data_pipeline(checks)
    institutional = _score_institutional_readiness(checks)
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "fetch_sample": fetch_sample,
        "internet_probe": bool(internet),
        "ok": all(c.get("ok") for c in checks),
        "score": score,
        "institutional_readiness": institutional,
        "checks": checks,
    }
    return report


def render_markdown(report: Dict[str, Any]) -> str:
    lines = [
        "# Data Pipeline Audit",
        "",
        f"- Generated: `{report.get('generated_at')}`",
        f"- Status: `{'PASS' if report.get('ok') else 'WARN'}`",
        f"- Fetch sample: `{report.get('fetch_sample', 0)}`",
        f"- Internet probe: `{report.get('internet_probe', False)}`",
        f"- Audit score: `{report.get('score', {}).get('total', 0)}/{report.get('score', {}).get('max', 100)}` "
        f"grade `{report.get('score', {}).get('grade', '?')}` "
        f"readiness `{report.get('score', {}).get('readiness', '?')}`",
        f"- Institutional readiness: `{report.get('institutional_readiness', {}).get('total', 0)}/"
        f"{report.get('institutional_readiness', {}).get('max', 100)}` "
        f"grade `{report.get('institutional_readiness', {}).get('grade', '?')}` "
        f"state `{report.get('institutional_readiness', {}).get('readiness', '?')}`",
        "",
        "## Score",
        "",
    ]
    score = report.get("score", {}) or {}
    for key, part in (score.get("parts", {}) or {}).items():
        lines.append(
            f"- `{key}` `{part.get('score', 0)}/{part.get('max', 0)}` - {part.get('detail', '')}"
        )
    improvements = score.get("top_improvements", []) or []
    if improvements:
        lines.extend(["", "## Improvement Priorities", ""])
        for i, item in enumerate(improvements[:8], 1):
            lines.append(f"{i}. {item}")
    inst = report.get("institutional_readiness", {}) or {}
    lines.extend(["", "## Institutional Readiness", ""])
    for key, part in (inst.get("parts", {}) or {}).items():
        lines.append(
            f"- `{key}` `{part.get('score', 0)}/{part.get('max', 0)}` - {part.get('detail', '')}"
        )
    blockers = inst.get("blockers", []) or []
    if blockers:
        lines.extend(["", "## Institutional Blockers", ""])
        for i, item in enumerate(blockers[:8], 1):
            lines.append(f"{i}. {item}")
    lines.extend([
        "",
        "## Checks",
        "",
    ])
    for check in report.get("checks", []):
        status = "PASS" if check.get("ok") else "WARN"
        name = check.get("name", "unknown")
        reason = check.get("reason", "")
        lines.append(f"- `{status}` `{name}`" + (f" - {reason}" if reason else ""))
        if name == "universe":
            lines.append(
                f"  - learning `{check.get('learning_count', 0)}` "
                f"mode `{check.get('learning_mode', '')}`, "
                f"probation `{','.join(check.get('probation_universe', []) or [])}`"
            )
        if name == "data_fetcher_wiring":
            lines.append(
                f"  - ordered symbols `{check.get('ordered_symbols', 0)}`, "
                f"full tier `{check.get('full_ordered_symbols', 0)}`, "
                f"angel attached `{check.get('angel_attached')}`"
            )
        if name == "source_config":
            backups = check.get("backup_credentials", {}) or {}
            configured = ",".join(k for k, v in backups.items() if v) or "none"
            lines.append(
                f"  - angel primary `{check.get('angel_primary_configured')}`, "
                f"configured backups `{configured}`, "
                f"disable yfinance `{check.get('disable_yfinance')}`"
            )
        if name == "internet_source_catalog":
            gaps = check.get("critical_gaps", []) or []
            optional = check.get("optional_gaps", []) or []
            lines.append(
                f"  - covered `{check.get('covered_count', 0)}/{check.get('source_count', 0)}`, "
                f"critical gaps `{','.join(gaps) or 'none'}`, "
                f"optional gaps `{','.join(optional) or 'none'}`"
            )
            for row in (check.get("sources", []) or [])[:12]:
                state = "covered" if row.get("covered") else "gap"
                lines.append(
                    f"  - `{row.get('domain')}` {state}: {row.get('preferred')} "
                    f"(local `{len(row.get('local_present', []) or [])}`/{len((row.get('local_present', []) or []) + (row.get('local_missing', []) or []))}`)"
                )
            recs = check.get("recommendations", []) or []
            if recs:
                lines.append("  - recommendations:")
                for rec in recs[:5]:
                    lines.append(f"    - {rec}")
        if name == "runtime_broker_wiring":
            lines.append(
                f"  - brokers `{','.join(check.get('brokers', []) or [])}`, "
                f"angel attached `{check.get('angel_attached')}`, "
                f"connected `{check.get('angel_obj_connected')}`, "
                f"paper_trade `{check.get('angel_paper_trade')}`"
            )
        if name == "fetcher_fallback_wiring":
            methods_ok = sum(1 for v in (check.get("methods", {}) or {}).values() if v)
            modules_ok = sum(1 for v in (check.get("modules", {}) or {}).values() if v)
            lines.append(f"  - fetcher methods `{methods_ok}`, fallback modules `{modules_ok}`")
        if name == "storage":
            lines.append(
                f"  - trades `{check.get('trades_db', {}).get('count', 0)}`, "
                f"signals `{check.get('signal_log_db', {}).get('count', 0)}`, "
                f"candles `{check.get('candle_cache_db', {}).get('count', 0)}`, "
                f"candle meta `{check.get('candle_cache_meta', {}).get('count', 0)}`, "
                f"option snaps `{check.get('option_chain_snapshots_db', {}).get('count', 0)}`, "
                f"profile snaps `{check.get('market_profile_snapshots_db', {}).get('count', 0)}`, "
                f"historical options `{check.get('historical_options_db', {}).get('count', 0)}`, "
                f"market snaps `{check.get('market_snapshots_db', {}).get('count', 0)}`, "
                f"confluence `{check.get('confluence_features_db', {}).get('count', 0)}`, "
                f"nse hub `{check.get('nse_data_hub', {}).get('ok_count', 0)}/{check.get('nse_data_hub', {}).get('dataset_count', 0)}`, "
                f"journal exists `{check.get('option_journal_exists')}`"
            )
        if name == "labelled_dataset":
            lines.append(
                f"  - labelled `{check.get('labelled', 0)}/{check.get('total', 0)}`, "
                f"days `{check.get('distinct_days', 0)}`, "
                f"executed `{check.get('executed', 0)}`"
            )
        if name == "learning_files":
            lines.append(
                f"  - live-ready `{check.get('live_ready_count', 0)}/{check.get('total_strategies', 0)}`, "
                f"autotune selected `{check.get('autotune_selected', 0)}`, "
                f"shadow `{check.get('autotune_shadow', 0)}`"
            )
        if name == "fetch_sample":
            lines.append(
                f"  - ok `{check.get('ok_count', 0)}/{check.get('requested', 0)}`"
            )
        if name == "market_hours_freshness":
            if check.get("skipped"):
                lines.append(f"  - skipped `{check.get('reason', '')}`")
            else:
                lines.append(
                    f"  - fresh `{check.get('ok_count', 0)}/{check.get('requested', 0)}`"
                )
        if name == "internet_source_reachability":
            lines.append(
                f"  - reachable `{check.get('ok_count', 0)}/{check.get('requested', 0)}`"
            )
            failed = [r for r in (check.get("results", []) or []) if not r.get("ok")]
            for row in failed[:5]:
                lines.append(
                    f"  - failed `{row.get('url')}` "
                    f"{row.get('status', row.get('reason', ''))}"
                )
    lines.append("")
    return "\n".join(lines)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fetch-sample", type=int, default=0)
    parser.add_argument("--internet", action="store_true", help="Probe official source pages/endpoints")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    report = run_audit(fetch_sample=max(0, int(args.fetch_sample or 0)), internet=bool(args.internet))
    Path(REPORT_JSON).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    Path(REPORT_MD).write_text(render_markdown(report), encoding="utf-8")
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(render_markdown(report))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
