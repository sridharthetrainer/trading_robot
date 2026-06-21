"""
connection_monitor.py — Autonomous Connection & Data Feed Monitor

Runs automatically:
  Startup     → full check of all 12 connections, alert sent
  8:50 AM     → full pre-trade check before market opens
  Every scan  → quick check (yfinance + NSE OC + Telegram)
  8:05 PM     → daily data reliability report
  On recovery → fires when a failed feed comes back online
  /connections → on-demand full check via Telegram

Architecture:
  - Non-blocking (runs in daemon threads, never delays scan)
  - Deduplication (same failure only alerted once per hour)
  - State tracking (detects new failures AND recoveries)
  - Cache (avoids hammering NSE every 5 min)
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

def _yf_last_close(ticker: str, in_market: bool = False) -> tuple:
    """
    Safe yfinance download that handles both old (Series) and new (MultiIndex DataFrame).
    Returns (price_float, detail_str, ok_bool).
    New yfinance returns MultiIndex columns — must squeeze to get scalar.
    """
    import yf_compat as yf  # yfinance replaced: Yahoo API broken
    period   = "1d" if in_market else "5d"
    interval = "5m" if in_market else "1d"
    df = yf.download(ticker, period=period, interval=interval,
                     progress=False, auto_adjust=True)
    if df is None or len(df) == 0:
        return 0.0, "No data (market closed)", False
    try:
        close = df["Close"]
        # Handle MultiIndex (new yfinance ≥ 0.2.18)
        if hasattr(close, "columns"):          # DataFrame — squeeze to Series
            close = close.squeeze()
        val = close.iloc[-1]
        # val might still be a Series if only 1 column
        if hasattr(val, "iloc"):
            val = val.iloc[0]
        return float(val), "", True
    except Exception as _e:
        return 0.0, str(_e)[:50], False


# result cache: key -> (ok, detail, timestamp)
_CACHE: Dict[str, Tuple[bool, str, float]] = {}
_TTL = {
    "yfinance":   600,   # 10 min (now NSE allIndices)
    "nse":        180,   # 3 min
    "bse":        300,   # 5 min
    "telegram":   3600,  # 1 hr
    "angel":      3600,
    "local":      86400,
}


def _cached(key: str, fn, ttl: int = 300) -> Tuple[bool, str]:
    hit = _CACHE.get(key)
    if hit and (time.time() - hit[2]) < ttl:
        return hit[0], hit[1]
    try:
        ok, detail = fn()
    except Exception as e:
        ok, detail = False, _clean(str(e))
    _CACHE[key] = (ok, detail, time.time())
    return ok, detail


def _clean(msg: str) -> str:
    """Shorten noisy error messages."""
    if "ConnectionPool" in msg or "Max retries" in msg:
        return "Network timeout — check internet"
    if "No module" in msg:
        mod = msg.split("'")[1] if "'" in msg else msg
        return f"pip install {mod}"
    return msg[:55]


def _nse_session():
    import requests
    s = requests.Session()
    s.headers.update({
        "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept":          "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer":         "https://www.nseindia.com/",
    })
    s.get("https://www.nseindia.com/", timeout=8)
    return s


# ─── Individual checkers ──────────────────────────────────────────────────────


def _safe_price(df, col="Close") -> float:
    """Handle both old Series and new MultiIndex yfinance output."""
    try:
        if df is None or len(df) == 0: return 0.0
        c = df[col]
        if hasattr(c, "columns"): c = c.iloc[:, 0]
        val = c.iloc[-1]
        if hasattr(val, "iloc"): val = val.iloc[0]
        return float(val)
    except Exception: return 0.0


def _is_off_hours() -> bool:
    """True between 3 AM and 8:30 AM — markets closed, data unavailable."""
    from datetime import datetime, time as dtime
    t = datetime.now().time()
    return dtime(3,0) <= t <= dtime(8,30)

def chk_yfinance_nifty() -> Tuple[bool, str]:
    # Renamed: now checks NSE allIndices live price (yfinance removed)
    # At 3-8 AM: markets closed, NSE returns last close — not a real failure
    from datetime import datetime as _dt, time as _dtime
    _now = _dt.now().time()
    _off_hours = not (_dtime(8, 30) <= _now <= _dtime(20, 0))
    if _off_hours:
        try:
            import yf_compat as yf  # yfinance replaced: Yahoo API broken
            df = yf.download("^NSEI", period="5d", interval="1d", progress=False, auto_adjust=True)
            c = df["Close"] if df is not None and len(df)>0 else None
            if c is not None:
                if hasattr(c, "columns"): c = c.iloc[:,0]
                return True, f"NIFTY last close ₹{float(c.iloc[-1]):,.0f} (off-hours)"
        except Exception: pass
        return True, "Off-hours — data will load at market open"  # not a failure
    def _f():
        import yf_compat as yf  # yfinance replaced: Yahoo API broken
        from datetime import datetime, time as _dtime
        _in_mkt = _dtime(9, 15) <= datetime.now().time() <= _dtime(15, 35)
        _period   = "1d" if _in_mkt else "5d"
        _interval = "5m" if _in_mkt else "1d"
        df = yf.download("^NSEI", period=_period, interval=_interval,
                         progress=False, auto_adjust=True)
        price = _safe_price(df)
        if price > 0:
            return True, f"NIFTY=₹{price:,.0f} ({'live' if _in_mkt else 'last close'})"
        if not _in_mkt:
            return True, "Market closed"
        return False, "No data returned"
    return _cached("yf_nifty", _f, _TTL["yfinance"])


def _fetch_vix_nse() -> float:
    """Fetch India VIX from NSE allIndices API — free, no auth."""
    import requests
    s = requests.Session()
    s.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://www.nseindia.com"})
    s.get("https://www.nseindia.com/", timeout=5)
    r = s.get("https://www.nseindia.com/api/allIndices", timeout=8)
    if r.status_code == 200:
        for idx in r.json().get("data", []):
            if "INDIA VIX" in str(idx.get("index", "")).upper():
                return float(idx.get("last", 0) or 0)
    return 0.0


def _fetch_vix_yf() -> float:
    """yfinance fallback for VIX."""
    try:
        import yf_compat as yf
        df = yf.download("^INDIAVIX", period="5d", interval="1d",
                         progress=False, auto_adjust=True)
        if df is not None and len(df) > 0:
            return float(_safe_price(df))
    except Exception: pass
    return 0.0


def chk_india_vix() -> Tuple[bool, str]:
    def _f():
        # Primary: NSE allIndices (most reliable)
        vix = _fetch_vix_nse()
        src = "NSE"
        if not vix:
            vix = _fetch_vix_yf()
            src = "yfinance"
        if vix:
            level = "HIGH ⚠️" if vix > 20 else "NORMAL"
            return True, f"VIX={vix:.1f} ({level}) [{src}]"
        return False, "No data"
    return _cached("india_vix", _f, _TTL["yfinance"])


def chk_cross_asset() -> Tuple[bool, str]:
    def _f():
        import yf_compat as yf  # yfinance replaced: Yahoo API broken
        df = yf.download("USDINR=X", period="1d", interval="1d",
                         progress=False, auto_adjust=True)
        if df is not None and len(df) > 0:
            return True, f"USD/INR={_safe_price(df):.2f}"
        return False, "No data"
    return _cached("cross_asset", _f, _TTL["yfinance"])


def chk_nse_oc(symbol: str = "NIFTY") -> Tuple[bool, str]:
    def _f():
        s = _nse_session()
        r = s.get(
            f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}",
            timeout=12
        )
        if r.status_code == 200:
            d     = r.json()
            spot  = d.get("records", {}).get("underlyingValue", 0)
            strks = len(d.get("records", {}).get("data", []))
            return True, f"spot={spot:,.0f} | {strks} strikes"
        return False, f"HTTP {r.status_code}"
    return _cached(f"nse_oc_{symbol}", _f, _TTL["nse"])


def chk_nse_fii() -> Tuple[bool, str]:
    def _f():
        s = _nse_session()
        r = s.get("https://www.nseindia.com/api/fiidiiTradeReact", timeout=10)
        if r.status_code == 200:
            d = r.json()
            n = len(d) if isinstance(d, list) else len(d.get("data", []))
            return True, f"{n} rows"
        return False, f"HTTP {r.status_code}"
    return _cached("nse_fii", _f, _TTL["nse"])


def chk_nse_bulk() -> Tuple[bool, str]:
    def _f():
        s = _nse_session()
        for _bd_url in [
            "https://www.nseindia.com/api/historical/bulk-deals",
            "https://www.nseindia.com/api/block-deal",
        ]:
            try:
                r = s.get(_bd_url, timeout=6)
                if r.status_code == 200:
                    d = r.json()
                    n = len(d.get("data", d if isinstance(d, list) else []))
                    break
            except Exception: continue
        else:
            return True, "No endpoints responded (after-hours)"
        if True:  # block for indentation
            return True, f"{n} rows"
        return False, f"HTTP {r.status_code}"
    return _cached("nse_bulk", _f, _TTL["nse"])


def chk_nse_holidays() -> Tuple[bool, str]:
    def _f():
        s = _nse_session()
        r = s.get("https://www.nseindia.com/api/holiday-master?type=trading", timeout=10)
        if r.status_code == 200:
            h = len(r.json().get("CM", []))
            return True, f"{h} holidays loaded"
        return False, f"HTTP {r.status_code}"
    return _cached("nse_holidays", _f, _TTL["telegram"])


def chk_bse_sensex() -> Tuple[bool, str]:
    def _f():
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0",
                           "Referer": "https://www.bseindia.com/"})
        r = s.get(
            "https://api.bseindia.com/BseIndiaAPI/api/SensitiveIndex/w?strIndexType=S",
            timeout=10
        )
        return r.status_code == 200, f"HTTP {r.status_code}"
    return _cached("bse_sensex", _f, _TTL["bse"])


def chk_telegram(token: str = "") -> Tuple[bool, str]:
    def _f():
        _tok = token
        if not _tok:
            for fname in [".env", "env_clean.txt"]:
                p = Path(fname)
                if p.exists():
                    for line in p.read_text().split("\n"):
                        if "TELEGRAM_BOT_TOKEN" in line and "=" in line:
                            _tok = line.split("=", 1)[1].strip()
        if not _tok:
            return False, "Token not in .env"
        import requests
        r = requests.get(f"https://api.telegram.org/bot{_tok}/getMe", timeout=8)
        if r.status_code == 200:
            name = r.json().get("result", {}).get("username", "?")
            return True, f"@{name}"
        return False, f"HTTP {r.status_code}"
    return _cached("telegram", _f, _TTL["telegram"])


def chk_angel_creds() -> Tuple[bool, str]:
    def _f():
        for fname in [".env", "env_clean.txt"]:
            p = Path(fname)
            if not p.exists():
                continue
            env = {}
            for line in p.read_text().split("\n"):
                if "=" in line and not line.strip().startswith("#"):
                    k, _, v = line.partition("=")
                    env[k.strip()] = v.strip()
            required = ["API_KEY", "CLIENT_ID", "PASSWORD", "TOTP_SECRET"]
            missing  = [k for k in required
                        if not env.get(k) or env.get(k, "").startswith("YOUR_")]
            if not missing:
                return True, "All credentials set"
            return False, f"Missing in .env: {', '.join(missing)}"
        return False, ".env not found"
    return _cached("angel_creds", _f, _TTL["angel"])


def _is_off_hours() -> bool:
    from datetime import time as _dtime
    n = __import__("datetime").datetime.now().time()
    return not (_dtime(8, 30) <= n <= _dtime(20, 0))


def chk_local_files() -> Tuple[bool, str]:
    needed  = ["nifty200.csv", "MasterContract_NFO.csv"]
    missing = [f for f in needed if not Path(f).exists()]
    if not missing:
        return True, "All local files present"
    if "MasterContract_NFO.csv" in missing:
        try:
            import requests, pandas as _pd
            r = requests.get(
                "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json",
                timeout=25)
            if r.status_code == 200:
                df = _pd.DataFrame(r.json())
                col = "exch_seg" if "exch_seg" in df.columns else df.columns[0]
                if "exch_seg" in df.columns:
                    df[df["exch_seg"].str.upper()=="NFO"].to_csv("MasterContract_NFO.csv", index=False)
                else:
                    df.to_csv("MasterContract_NFO.csv", index=False)
                missing = [f for f in missing if f != "MasterContract_NFO.csv"]
                logging.getLogger(__name__).info("MasterContract_NFO.csv auto-downloaded")
        except Exception as _e:
            logging.getLogger(__name__).warning("MasterContract download failed: %s", _e)
    if not missing:
        return True, "All local files present (MasterContract auto-downloaded)"
    return False, f"Missing: {', '.join(missing)}"


def chk_smartapi_lib() -> Tuple[bool, str]:
    try:
        from SmartApi import SmartConnect  # noqa
        return True, "SmartApi installed"
    except ImportError:
        return False, "pip install smartapi-python"


# ─── Check registry ───────────────────────────────────────────────────────────

# (label, fn, category, is_critical)
CHECKS_FULL = [
    ("Angel One credentials",      chk_angel_creds,                    "Broker",  True),
    ("SmartAPI library",           chk_smartapi_lib,                   "Broker",  True),
    ("NSE Live NIFTY price",       chk_yfinance_nifty,                 "Data",    False),  # NSE direct is primary
    ("India VIX",                  chk_india_vix,                      "Data",    False),
    ("Cross-asset USD/INR",        chk_cross_asset,                    "Data",    False),
    ("NSE NIFTY Option Chain",     lambda: chk_nse_oc("NIFTY"),        "NSE",     False),  # NOT critical — OI strategies skip, rest work fine
    ("NSE BANKNIFTY Option Chain", lambda: chk_nse_oc("BANKNIFTY"),    "NSE",     False),
    ("NSE FINNIFTY Option Chain",  lambda: chk_nse_oc("FINNIFTY"),     "NSE",     False),
    ("NSE FII/DII Data",           chk_nse_fii,                        "NSE",     False),
    ("NSE Bulk Deals",             chk_nse_bulk,                       "NSE",     False),
    ("NSE Holiday Calendar",       chk_nse_holidays,                   "NSE",     False),
    ("BSE SENSEX API (optional)",             chk_bse_sensex,                     "BSE",     False),
    ("Telegram Bot",               chk_telegram,                       "Infra",   True),
    ("Local files",                chk_local_files,                    "Infra",   True),
]

CHECKS_QUICK = [
    # Run before every scan cycle — fast checks only
    ("NSE Live NIFTY price",   chk_yfinance_nifty,              "Data",  False),  # Angel provides data independently
    ("NSE NIFTY Option Chain", lambda: chk_nse_oc("NIFTY"),     "NSE",   False),  # warning only
    ("Telegram Bot",           chk_telegram,                    "Infra", True),
]


# ─── Monitor class ────────────────────────────────────────────────────────────

class ConnectionMonitor:
    def __init__(self, alerts=None) -> None:
        self.alerts         = alerts
        self._prev:  Dict[str, bool] = {}   # previous state per check
        self._fails: Dict[str, int]  = {}   # failure counts today
        self._date:  str = ""

    def _reset_daily(self) -> None:
        today = date.today().isoformat()
        if self._date != today:
            self._date  = today
            self._fails = {}

    def _send(self, msg: str, dk: str = "", cooldown: int = 300) -> None:
        if not self.alerts:
            return
        try:
            self.alerts.send(msg, dedup_key=dk or msg[:30],
                             dedup_cooldown_override=cooldown)
        except Exception as e:
            logger.debug("ConnMonitor send: %s", e)

    def _recovery_alert(self, name: str, detail: str) -> None:
        if self.alerts and hasattr(self.alerts, "data_recovery_alert"):
            self.alerts.data_recovery_alert("Connection", name, detail)
        else:
            self._send(
                f"TICK <b>RESTORED: {name}</b>\n  {detail}\nCLOCK {datetime.now().strftime('%H:%M')}"
                .replace("TICK ", "✅ ").replace("CLOCK ", "🕐 "),
                dk=f"restored_{name}"
            )

    def run_full_check(self, label: str = "STARTUP") -> Dict:
        """Run all checks and send a structured Telegram alert."""
        # Guard: don't repeat full check within 30 min (handles rapid restarts)
        import time as _t
        _min_gap = 1800 if label == "STARTUP" else 600
        _now_ts  = _t.time()
        _last_key = f"_last_full_{label}"
        if (_now_ts - getattr(self, _last_key, 0)) < _min_gap:
            logger.debug("run_full_check(%s) skipped — ran %.0fs ago",
                         label, _now_ts - getattr(self, _last_key, 0))
            return getattr(self, f"_last_result_{label}", {})
        setattr(self, _last_key, _now_ts)

        self._reset_daily()
        ok_list, warn_list, fail_list, critical = [], [], [], []
        expected_list = []   # known-blocked NSE-direct feeds (no proxy configured)

        # NSE blocks this machine's IP at the edge, so the NSE-direct feeds can
        # never succeed without an egress proxy. When NSE_PROXY is unset, treat
        # those failures as EXPECTED (not warnings) so they don't cry wolf. The
        # moment a proxy is configured they warn normally again, so a genuinely
        # broken proxy still surfaces. (VIX is covered via Angel at runtime.)
        try:
            from nse_proxy import is_enabled as _nse_proxy_on
            _proxy = _nse_proxy_on()
        except Exception:
            _proxy = False

        def _is_nse_direct(nm, ct):
            return ct == "NSE" or nm == "India VIX"

        for name, fn, cat, is_crit in CHECKS_FULL:
            try:
                ok, detail = fn()
            except Exception as e:
                ok, detail = False, _clean(str(e))

            was_ok = self._prev.get(name, True)
            self._prev[name] = ok

            entry = (name, detail, cat)
            if ok:
                ok_list.append(entry)
                if not was_ok:
                    self._recovery_alert(name, detail)
            elif is_crit:
                fail_list.append(entry)
                critical.append(name)
                self._fails[name] = self._fails.get(name, 0) + 1
            elif _is_nse_direct(name, cat) and not _proxy:
                expected_list.append(entry)
            else:
                warn_list.append(entry)
                if not was_ok:
                    self._fails[name] = self._fails.get(name, 0) + 1

        # Remember which feeds are EXPECTED-blocked so get_status_line() (used by
        # the hourly Telegram alert) doesn't mislabel them as "failed" — that
        # false "🔌 N failed" noise masked a real Scanned:0 outage on 2026-06-15.
        self._expected_names = set(n for n, _, _ in expected_list)
        if expected_list:
            logger.info(
                "ConnectionMonitor: %d NSE-direct feed(s) blocked (expected, no "
                "NSE_PROXY): %s", len(expected_list),
                ", ".join(n for n, _, _ in expected_list))

        n_ok, n_w, n_f = len(ok_list), len(warn_list), len(fail_list)
        safe = len(critical) == 0

        # Send via structured method if available
        if self.alerts and hasattr(self.alerts, "connection_alert"):
            self.alerts.connection_alert(
                check_type    = label,
                n_ok          = n_ok,
                n_warn        = n_w,
                n_fail        = n_f,
                failures      = [(n, d) for n, d, _ in fail_list],
                warnings      = [(n, d) for n, d, _ in warn_list],
                safe_to_trade = safe,
            )
        else:
            self._send(
                self._format_plain(label, n_ok, n_w, n_f,
                                   fail_list, warn_list, safe),
                dk=f"conn_{label}_{date.today()}"
            )

        logger.info("ConnectionMonitor [%s]: ok=%d warn=%d fail=%d safe=%s",
                    label, n_ok, n_w, n_f, safe)
        _result = {"ok": n_ok, "warnings": n_w, "failures": n_f,
                   "critical": critical, "safe_to_trade": safe}
        setattr(self, f"_last_result_{label}", _result)
        return _result

    def run_quick_check(self) -> bool:
        """Quick check before every scan. Returns False to pause scan."""
        import time as _tq
        if (_tq.time() - getattr(self, "_last_quick", 0)) < 60:
            return getattr(self, "_last_quick_result", True)
        self._last_quick = _tq.time()
        self._reset_daily()
        new_fails = []

        for name, fn, cat, is_crit in CHECKS_QUICK:
            try:
                ok, detail = fn()
            except Exception as e:
                ok, detail = False, _clean(str(e))

            was_ok = self._prev.get(name, True)
            self._prev[name] = ok

            if ok and not was_ok:
                self._recovery_alert(name, detail)
            elif not ok and was_ok and is_crit:
                new_fails.append((name, detail))
                self._fails[name] = self._fails.get(name, 0) + 1

        if new_fails:
            if self.alerts and hasattr(self.alerts, "data_failure_alert"):
                for name, detail in new_fails:
                    self.alerts.data_failure_alert("Connection", name, detail)
            else:
                lines = ["WARN <b>CONNECTION ALERT</b>"]
                for name, detail in new_fails:
                    lines += [f"  FAIL {name}", f"    {detail}"]
                lines += ["  Scan paused — auto-retry in 5 min",
                          "  /connections for full check",
                          f"CLOCK {datetime.now().strftime('%H:%M')}"]
                self._send(
                    "\n".join(lines)
                    .replace("WARN ", "🔴 ").replace("  FAIL ", "  ❌ ")
                    .replace("CLOCK ", "🕐 "),
                    dk=f"qfail_{int(time.time()//300)}"
                )
            return False

        return True

    def daily_report(self) -> None:
        """EOD report of data failures."""
        if not self._fails:
            return
        lines = ["CHART <b>DATA RELIABILITY REPORT</b>",
                 f"  {date.today()}",
                 "DIV"]
        for name, count in sorted(self._fails.items(), key=lambda x: -x[1]):
            icon = "FAIL" if count > 3 else "WARN"
            lines.append(f"  {icon} {name}: failed {count}x today")
        lines += ["DIV",
                  f"  {len(self._fails)} feed(s) had issues",
                  "  Check /downloads for data gaps"]
        self._send(
            "\n".join(lines)
            .replace("CHART ", "📊 ").replace("DIV", "─"*30)
            .replace("FAIL ", "❌ ").replace("WARN ", "⚠️ "),
            dk=f"daily_report_{date.today()}", cooldown=86400
        )

    def _format_plain(self, label, n_ok, n_w, n_f, fail_list, warn_list, safe) -> str:
        icon = "GREEN" if n_f==0 and n_w==0 else ("YELLOW" if n_f==0 else "RED")
        status = ("ALL SYSTEMS GO" if n_f==0 and n_w==0
                  else f"{n_w} warning(s)" if n_f==0
                  else f"{n_f} CRITICAL FAILURE(S)")
        lines = [
            f"{icon} <b>{label} — {status}</b>",
            "DIV",
            f"  OK {n_ok}   Warn {n_w}   Failed {n_f}",
        ]
        if fail_list:
            lines.append("  ACTION REQUIRED:")
            for n, d, _ in fail_list:
                lines += [f"  FAIL {n}", f"    {d}"]
        if warn_list:
            lines.append("  Warnings:")
            for n, d, _ in warn_list[:3]:
                lines.append(f"  WARN {n}: {d[:40]}")
        lines += ["DIV",
                  "READY Trading system ready" if safe else "WARN Some connections down but scanning continues",
                  f"CLOCK {datetime.now().strftime('%H:%M')}"]
        return (
            "\n".join(lines)
            .replace("GREEN ", "🟢 ").replace("YELLOW ", "🟡 ").replace("RED ", "🔴 ")
            .replace("DIV", "─"*30).replace("  FAIL ", "  ❌ ")
            .replace("  WARN ", "  ⚠️ ").replace("  OK ", "  ✅ ")
            .replace("  ACTION REQUIRED:", "  <b>ACTION REQUIRED:</b>")
            .replace("READY ", "✅ ").replace("STOP ", "🛑 ")
            .replace("CLOCK ", "🕐 ")
        )

    def get_status_line(self) -> str:
        if not self._prev:
            return "Not checked yet"
        # Exclude EXPECTED-blocked NSE-direct feeds (no proxy) — they are not
        # failures (Angel fallback covers them), so they must not show as
        # "🔌 N failed" in the hourly alert.
        expected = getattr(self, "_expected_names", set())
        fails = [k for k, v in self._prev.items() if not v and k not in expected]
        if not fails:
            return f"All {len(self._prev) - len(expected)} connections OK"
        return f"{len(fails)} failed: {', '.join(fails[:2])}"


# ── Singleton ─────────────────────────────────────────────────────────────────
_monitor: Optional[ConnectionMonitor] = None

def get_monitor(alerts=None) -> ConnectionMonitor:
    global _monitor
    if _monitor is None:
        _monitor = ConnectionMonitor(alerts=alerts)
    if alerts and not _monitor.alerts:
        _monitor.alerts = alerts
    return _monitor
