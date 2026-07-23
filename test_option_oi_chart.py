#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from option_oi_chart import generate_option_oi_chart, generate_oi_strike_profile_chart


def _make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE option_chain_snapshots (
            ts REAL,
            snapshot_time TEXT,
            underlying TEXT,
            spot REAL,
            expiry TEXT,
            atm_strike REAL,
            pcr_oi REAL,
            pcr_change_oi REAL,
            max_pain REAL,
            ok INTEGER,
            reason TEXT,
            rows_json TEXT,
            summary_json TEXT
        )
    """)
    rows_a = [
        {
            "strikePrice": 23500,
            "CE_openInterest": 1000,
            "PE_openInterest": 1500,
            "CE_changeinOpenInterest": 100,
            "PE_changeinOpenInterest": 250,
            "CE_totalTradedVolume": 100,
            "PE_totalTradedVolume": 300,
        },
        {
            "strikePrice": 23550,
            "CE_openInterest": 2200,
            "PE_openInterest": 1200,
            "CE_changeinOpenInterest": 380,
            "PE_changeinOpenInterest": 120,
            "CE_totalTradedVolume": 400,
            "PE_totalTradedVolume": 120,
        },
        {
            "strikePrice": 23600,
            "CE_openInterest": 700,
            "PE_openInterest": 900,
            "CE_changeinOpenInterest": 60,
            "PE_changeinOpenInterest": 90,
            "CE_totalTradedVolume": 70,
            "PE_totalTradedVolume": 80,
        },
    ]
    rows_b = [
        {
            "strikePrice": 23500,
            "CE_openInterest": 1200,
            "PE_openInterest": 1800,
            "CE_changeinOpenInterest": 180,
            "PE_changeinOpenInterest": 320,
            "CE_totalTradedVolume": 180,
            "PE_totalTradedVolume": 450,
        },
        {
            "strikePrice": 23550,
            "CE_openInterest": 2600,
            "PE_openInterest": 1100,
            "CE_changeinOpenInterest": 460,
            "PE_changeinOpenInterest": 50,
            "CE_totalTradedVolume": 520,
            "PE_totalTradedVolume": 90,
        },
        {
            "strikePrice": 23600,
            "CE_openInterest": 900,
            "PE_openInterest": 950,
            "CE_changeinOpenInterest": 90,
            "PE_changeinOpenInterest": 110,
            "CE_totalTradedVolume": 90,
            "PE_totalTradedVolume": 100,
        },
    ]
    for ts, hhmm, spot, rows in [
        (1.0, "09:20:00", 23520, rows_a),
        (2.0, "09:25:00", 23540, rows_b),
    ]:
        conn.execute(
            """
            INSERT INTO option_chain_snapshots
            (ts, snapshot_time, underlying, spot, expiry, atm_strike, pcr_oi,
             pcr_change_oi, max_pain, ok, reason, rows_json, summary_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                ts,
                f"2026-06-18T{hhmm}+0530",
                "NIFTY",
                spot,
                "2026-06-23",
                23500,
                1.2,
                1.4,
                23500,
                1,
                "",
                json.dumps(rows),
                "{}",
            ),
        )
    conn.commit()
    conn.close()


def test_aggregate_chart() -> None:
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "snapshots.db"
        _make_db(db)
        result = generate_option_oi_chart(
            underlying="NIFTY",
            day="2026-06-18",
            db_path=str(db),
            output_dir=td,
        )
        assert result.ok
        assert Path(result.path).exists()
        assert result.points == 2


def test_strike_chart() -> None:
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "snapshots.db"
        _make_db(db)
        result = generate_option_oi_chart(
            underlying="NIFTY",
            day="2026-06-18",
            strike=23500,
            db_path=str(db),
            output_dir=td,
        )
        assert result.ok
        assert Path(result.path).exists()
        assert "Strike 23500" in result.caption


def test_top_multi_strike_chart() -> None:
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "snapshots.db"
        _make_db(db)
        result = generate_option_oi_chart(
            underlying="NIFTY",
            day="2026-06-18",
            compare_top=3,
            db_path=str(db),
            output_dir=td,
        )
        assert result.ok
        assert Path(result.path).exists()
        assert "multi-strike" in result.caption
        assert "Key support" in result.caption
        assert "Key resistance" in result.caption


def _fake_chain_row(strike, ce_oi, ce_chg, pe_oi, pe_chg, expiry="2026-06-25"):
    return {
        "strikePrice": strike, "expiryDate": expiry,
        "CE": {"openInterest": ce_oi, "changeinOpenInterest": ce_chg},
        "PE": {"openInterest": pe_oi, "changeinOpenInterest": pe_chg},
    }


class _FakeChainFetcher:
    """Stands in for NSEOptionChainFetcher: fixed spot=23500, one expiry,
    5 strikes with a mix of BUILDUP/UNWINDING/FLAT OI on both sides, and a
    known chain-wide PCR so the computation can be checked exactly."""

    def __init__(self, underlying: str = "NIFTY") -> None:
        self.underlying = underlying

    def fetch(self):
        rows = [
            _fake_chain_row(23300, ce_oi=1_000_000, ce_chg=5_000,     # CE FLAT
                             pe_oi=2_000_000, pe_chg=100_000),        # PE BUILDUP
            _fake_chain_row(23400, ce_oi=1_200_000, ce_chg=-50_000,   # CE UNWINDING
                             pe_oi=1_500_000, pe_chg=10_000),         # PE FLAT
            _fake_chain_row(23500, ce_oi=1_800_000, ce_chg=200_000,   # CE BUILDUP
                             pe_oi=1_800_000, pe_chg=200_000),        # PE BUILDUP
            _fake_chain_row(23600, ce_oi=2_500_000, ce_chg=300_000,   # CE BUILDUP (resistance)
                             pe_oi=900_000, pe_chg=5_000),            # PE FLAT
            _fake_chain_row(23700, ce_oi=1_100_000, ce_chg=10_000,    # CE FLAT
                             pe_oi=700_000, pe_chg=-80_000),          # PE UNWINDING
        ]
        return {"records": {"underlyingValue": 23500.0,
                             "expiryDates": ["2026-06-25"], "data": rows}}


def test_strike_profile_chart_shows_pcr_and_oi_direction(monkeypatch) -> None:
    monkeypatch.setattr(
        "option_chain_fetcher.NSEOptionChainFetcher", _FakeChainFetcher)

    with tempfile.TemporaryDirectory() as td:
        result = generate_oi_strike_profile_chart(
            underlying="NIFTY", n_strikes=4, output_dir=td)
        assert result.ok, result.reason
        assert Path(result.path).exists()

        # PCR = total PE OI / total CE OI over the 5 fake strikes.
        total_ce = 1_000_000 + 1_200_000 + 1_800_000 + 2_500_000 + 1_100_000
        total_pe = 2_000_000 + 1_500_000 + 1_800_000 + 900_000 + 700_000
        expected_pcr = round(total_pe / total_ce, 2)
        assert f"PCR (chain-wide): {expected_pcr:.2f}" in result.caption

        # Resistance strike (max CE OI) is 23600; support (max PE OI) is 23300.
        assert "Resistance 23600" in result.caption
        assert "Support 23300" in result.caption

        # OI-direction legend/emoji present in the caption.
        assert "buildup" in result.caption and "unwinding" in result.caption
        assert "🟢" in result.caption and "🔴" in result.caption


def main() -> int:
    tests = [
        ("aggregate OI chart", test_aggregate_chart),
        ("single strike OI chart", test_strike_chart),
        ("top multi-strike OI chart", test_top_multi_strike_chart),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            ok = True
        except Exception as exc:
            ok = False
            print(f"FAIL {name}: {exc}")
        if ok:
            print(f"PASS {name}")
        else:
            failed += 1
            print(f"FAIL {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
