"""
advisory_engine.selftest — offline end-to-end exercise of all 4 layers.

Run:  python -m advisory_engine.selftest

Uses a synthetic broker (no network, no credentials, nothing placed anywhere)
whose bar data is engineered so each archetype has one PASS case, plus abort
cases proving the gates actually reject. Exits non-zero on any assertion
failure, so it can sit in CI.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from .models import ExecutionMode, OrderState, StrategyTag, Segment
from .data_core import TokenResolver
from .engine import AdvisoryEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    datefmt="%H:%M:%S")
log = logging.getLogger("advisory.selftest")


# ── Synthetic data construction ────────────────────────────────────────────────

def _bars(closes, volumes, index) -> pd.DataFrame:
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame({
        "open": np.r_[closes[0], closes[:-1]],
        "high": closes * 1.002,
        "low": closes * 0.998,
        "close": closes,
        "volume": np.asarray(volumes, dtype=float),
    }, index=pd.DatetimeIndex(index, name="date"))


def make_daily_breakout(n=320, base=4000.0) -> pd.DataFrame:
    """Alternating +1/-0.6 steps (scaled): steady uptrend ending on an up day.
    RSI(14) ~ 62 (inside 55-68), close > EMA5 > EMA20, last close > prior 14."""
    step = base * 0.0008
    closes = [base]
    for i in range(1, n):
        closes.append(closes[-1] + (step if i % 2 == 1 else -0.6 * step))
    end = datetime.now().date() - timedelta(days=1)
    idx = pd.bdate_range(end=end, periods=n)
    return _bars(closes, [1_000_000] * n, idx)


def make_daily_choppy(n=320, base=128.0, up=0.03, dn=0.02) -> pd.DataFrame:
    """High-realised-vol alternator (HV30 ~ 40%) for the IV-filter pass case."""
    closes = [base]
    for i in range(1, n):
        closes.append(closes[-1] * (1 + up if i % 2 else 1 - dn))
    end = datetime.now().date() - timedelta(days=1)
    idx = pd.bdate_range(end=end, periods=n)
    return _bars(closes, [2_000_000] * n, idx)


def make_1h_macd_cross(n_decline=100, n_rally=2, base=665.0) -> pd.DataFrame:
    """Accelerating decline (keeps MACD pinned below signal), then a sharp
    2-bar rally — bullish MACD cross lands within the trailing 2 closed bars,
    close back above the BB basis, RSI > 50."""
    closes = [base - 0.05 * i - 0.0015 * i * i for i in range(n_decline)]
    for _ in range(n_rally):
        closes.append(closes[-1] + 3.5)
    n = len(closes)
    end = datetime.now() - timedelta(hours=2)
    idx = [end - timedelta(hours=n - i) for i in range(n)]
    return _bars(closes, [500_000] * n, idx)


def make_15m_vwap_spike(spike=True) -> pd.DataFrame:
    """Two 25-bar sessions, flat at 100; final bar detaches +2% on 5x volume
    (or stays flat when spike=False, to prove the abort path)."""
    frames = []
    for d in (3, 2):                       # two past days, all bars closed
        day = datetime.now().date() - timedelta(days=d)
        idx = [datetime.combine(day, datetime.min.time())
               + timedelta(hours=9, minutes=15) + timedelta(minutes=15 * i)
               for i in range(25)]
        closes = [100.0] * 25
        vols = [1000.0] * 25
        if d == 2 and spike:
            closes[-1] = 102.0
            vols[-1] = 5000.0
        frames.append(_bars(closes, vols, idx))
    return pd.concat(frames)


class FakeBroker:
    """AngelOne-compatible data surface, deterministic and offline."""

    def __init__(self):
        self.frames = {
            ("TCS", "1d"): make_daily_breakout(base=4000.0),
            ("MOTHERSON", "1d"): make_daily_choppy(base=128.0),
            ("MOTHERSON", "15m"): make_15m_vwap_spike(spike=True),
            ("WIPRO", "1d"): make_daily_choppy(base=250.0),
            ("WIPRO", "15m"): make_15m_vwap_spike(spike=False),
            ("HDFCLIFE", "1d"): make_daily_breakout(base=650.0),
            ("HDFCLIFE", "1h"): make_1h_macd_cross(),
        }
        breakout_close = float(self.frames[("TCS", "1d")]["close"].iloc[-1])
        self.ltps = {
            "TCS": round(breakout_close - 5, 2),       # below zone -> peg at max
            "MOTHERSON": 128.0,
            "MOTHERSON26JUN130CE": 5.3,
            "WIPRO": 250.0,
            "WIPRO26JUN255CE": 4.0,
            "HDFCLIFE": float(self.frames[("HDFCLIFE", "1h")]["close"].iloc[-1]),
        }
        self.tcs_entry = round(breakout_close, 2)

    def get_historical_data(self, symbol, interval, from_date, to_date,
                            exchange="NSE"):
        return self.frames.get((symbol, interval))

    def get_ltp(self, symbol, exchange=None):
        return self.ltps.get(symbol)


# ── The run ────────────────────────────────────────────────────────────────────

def main() -> int:
    broker = FakeBroker()
    engine = AdvisoryEngine(
        broker=broker,
        mode=ExecutionMode.PAPER,
        equity_provider=lambda: 1_000_000.0,
        lot_size_provider=lambda sym: 1400,
        days_to_expiry_provider=lambda sig: 30.0,
        oi_provider=lambda sym, strike, seg, exp: [500_000, 480_000, 460_000,
                                                   430_000],
        token_resolver=TokenResolver({"MOTHERSON": "1", "HDFCLIFE": "2",
                                      "TCS": "3", "WIPRO": "4"}),
    )
    failures = []

    def check(label, cond, detail=""):
        status = "PASS" if cond else "FAIL"
        log.info("CHECK %-52s %s %s", label, status, detail)
        if not cond:
            failures.append(label)

    # 1) Parser fidelity on a derivative message
    sig = engine.parser.parse(
        "10-06-2026 10:42 AM Buy MOTHERSON 26 Jun 130 CE between 5-5.5, "
        "SL 4, target 8 — price detaching cleanly from the VWAP with a "
        "volume spike and reducing Open Interest")
    check("L1 derivative parse complete", sig.is_complete, str(sig.parse_errors))
    check("L1 segment CE", sig.segment is Segment.DERIVATIVE_CALL)
    check("L1 strike/expiry", sig.strike == 130.0 and sig.expiry == "26 Jun")
    check("L1 entry split", (sig.entry_min, sig.entry_max) == (5.0, 5.5))
    check("L1 sl/target", (sig.stop_loss, sig.target) == (4.0, 8.0))
    check("L1 tag VWAP_OI", sig.strategy_tag is StrategyTag.VWAP_OI)
    check("L1 timestamp", sig.timestamp == "10-06-2026 10:42 AM")

    # 2) Archetype B end-to-end PASS (option, OI squeeze, IV filter, sizing)
    pos_b = engine.process(
        "Buy MOTHERSON 26 Jun 130 CE between 5-5.5, SL 4, target 8 — "
        "detaching cleanly from the VWAP, volume spike, reducing Open Interest")
    check("B: VWAP_OI option pipeline fills (paper)",
          pos_b.state is OrderState.EXITS_PLACED,
          pos_b.history[-1]["note"] if pos_b.history else "")
    check("B: lot-rounded qty", pos_b.plan.quantity % 1400 == 0
          and pos_b.plan.quantity > 0, f"qty={pos_b.plan.quantity}")
    check("B: risk <= 1% of equity", pos_b.plan.capital_at_risk <= 10_000,
          f"risk=₹{pos_b.plan.capital_at_risk:,.0f}")

    # OCO lifecycle: target hit cancels SL
    key_b = next(k for k, v in engine.gateway.positions.items() if v is pos_b)
    engine.gateway.poll(key_b, ltp=8.2)
    check("B: OCO closes at target", pos_b.state is OrderState.CLOSED_TARGET,
          pos_b.history[-1]["note"])

    # 3) Archetype B ABORT — no volume spike
    pos_w = engine.process(
        "Buy WIPRO 26 Jun 255 CE between 3.8-4.2, SL 3, target 6 — "
        "detaching cleanly from the VWAP with reducing Open Interest")
    check("B-abort: flat volume rejected",
          pos_w.state is OrderState.REJECTED
          and "B1" in (pos_w.plan.rejection_reason or "")
          or "B2" in (pos_w.plan.rejection_reason or ""),
          str(pos_w.plan.rejection_reason)[:90])

    # 4) Archetype A end-to-end PASS (1H Bollinger/MACD reversal, equity)
    hl = broker.ltps["HDFCLIFE"]
    pos_a = engine.process(
        f"BUY HDFC Life Ins. Company between {hl - 2:.0f}-{hl + 2:.0f}, "
        f"SL {hl - 12:.0f}, target {hl + 20:.0f} — closed above the middle "
        "bollinger band with a bullish MACD crossover out of oversold zones")
    check("A: BOLLINGER_MACD equity pipeline fills",
          pos_a.state is OrderState.EXITS_PLACED,
          (pos_a.plan.rejection_reason or
           (pos_a.history[-1]["note"] if pos_a.history else "")))

    # 5) Archetype C end-to-end PASS (daily breakout, gap-peg path)
    e = broker.tcs_entry
    pos_c = engine.process(
        f"Buy TCS above {e:.2f}, stoploss {e - 60:.2f}, target {e + 150:.2f} "
        "— base formation breakout, reclaimed short term EMAs on a rising "
        "trendline")
    check("C: TRENDLINE_EMA pipeline fills",
          pos_c.state is OrderState.EXITS_PLACED,
          (pos_c.plan.rejection_reason or
           (pos_c.history[-1]["note"] if pos_c.history else "")))
    check("C: LTP below zone pegs LIMIT at entry_max",
          pos_c.plan.accepted and pos_c.plan.limit_price == e,
          f"limit={pos_c.plan.limit_price}")
    check("C: notional cap respected",
          pos_c.plan.quantity * pos_c.plan.limit_price <= 250_000.5,
          f"notional=₹{pos_c.plan.quantity * pos_c.plan.limit_price:,.0f}")

    # 6) Gap control ABORT — LTP gapped above entry_max
    broker.ltps["TCS"] = e + 50
    pos_gap = engine.process(
        f"Buy TCS above {e:.2f}, stoploss {e - 60:.2f}, target {e + 150:.2f} "
        "— base formation breakout, reclaimed short term EMAs")
    check("Gap-abort: LTP > entry_max cancelled",
          pos_gap.state is OrderState.REJECTED
          and "gap control" in (pos_gap.plan.rejection_reason or ""),
          str(pos_gap.plan.rejection_reason)[:90])

    # 7) Garbage in -> rejected, never traded
    pos_junk = engine.process("gm folks, market looking spicy today 🚀")
    check("Junk text rejected at L1", pos_junk.state is OrderState.REJECTED)

    # 8) Unknown instrument -> rejected at L2
    pos_unk = engine.process(
        "Buy SOMERANDOMCO between 100-101, SL 95, target 110 — breakout")
    check("Unknown symbol rejected at L2", pos_unk.state is OrderState.REJECTED)

    print()
    if failures:
        log.error("SELFTEST FAILED — %d check(s): %s", len(failures), failures)
        return 1
    log.info("SELFTEST PASSED — all checks green (paper mode, nothing placed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
