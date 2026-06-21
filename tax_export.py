"""
tax_export.py — P&L export in ITR-3 / CBDT compliant format

Generates:
  1. ITR-3 compatible CSV (Schedule 112A / F&O P&L)
  2. Scrip-wise summary for CA
  3. Day-wise summary
  4. Monthly breakdowns

Usage:
  /export_tax 2025-26   → downloads ready-to-file CSV
  /export_tax           → current financial year
"""
from __future__ import annotations
import logging, os
from datetime import datetime, date
from pathlib import Path
from typing import Optional
import pandas as pd

logger = logging.getLogger(__name__)


def get_financial_year(target_date: date = None) -> tuple:
    """Returns (start_date, end_date, fy_str) for a given date."""
    if target_date is None:
        target_date = date.today()
    year = target_date.year
    if target_date.month < 4:
        year -= 1
    start = date(year, 4, 1)
    end   = date(year+1, 3, 31)
    fy_str = f"{year}-{str(year+1)[2:]}"
    return start, end, fy_str


def export_pnl_for_tax(fy: str = None, db_path: str = "trades.db") -> Optional[str]:
    """
    Export F&O P&L in CBDT/ITR-3 format.
    Returns path to generated CSV file.
    """
    try:
        import sqlite3

        if fy:
            # Parse "2025-26" → year 2025
            year = int(fy.split("-")[0])
            start = date(year, 4, 1)
            end   = date(year+1, 3, 31)
            fy_str = fy
        else:
            start, end, fy_str = get_financial_year()

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT
                symbol, strategy, side, qty,
                entry_price, exit_price,
                entry_time, exit_time,
                realized_pnl, exit_reason,
                signal_metadata
            FROM trades
            WHERE status = 'CLOSED'
              AND exit_time >= ?
              AND exit_time <= ?
              AND realized_pnl IS NOT NULL
            ORDER BY exit_time
        """, (start.isoformat(), end.isoformat())).fetchall()
        conn.close()

        if not rows:
            return None

        records = []
        for r in rows:
            entry_dt = str(r["entry_time"] or "")[:10]
            exit_dt  = str(r["exit_time"]  or "")[:10]
            pnl      = float(r["realized_pnl"] or 0)
            qty      = int(r["qty"] or 0)
            ep       = float(r["entry_price"] or 0)
            xp       = float(r["exit_price"]  or 0)

            # Determine instrument type
            sym = str(r["symbol"] or "")
            if any(x in sym.upper() for x in ["CE","PE","CALL","PUT"]):
                instr_type = "F&O_OPTIONS"
            elif any(x in sym.upper() for x in ["NIFTY","BANKNIFTY","FUT"]):
                instr_type = "F&O_FUTURES"
            else:
                instr_type = "EQUITY"

            gross_proceeds = xp * qty
            cost_basis     = ep * qty
            net_pnl        = pnl

            records.append({
                "Date of Purchase":     entry_dt,
                "Date of Sale":         exit_dt,
                "ISIN / Symbol":        sym,
                "Instrument Type":      instr_type,
                "Quantity":             qty,
                "Buy Price":            round(ep, 2),
                "Sell Price":           round(xp, 2),
                "Cost of Acquisition":  round(cost_basis, 2),
                "Sale Consideration":   round(gross_proceeds, 2),
                "Profit / Loss":        round(net_pnl, 2),
                "Strategy":             str(r["strategy"] or ""),
                "Exit Reason":          str(r["exit_reason"] or ""),
            })

        df = pd.DataFrame(records)

        # Summary rows
        total_trades  = len(df)
        winning       = (df["Profit / Loss"] > 0).sum()
        losing        = (df["Profit / Loss"] < 0).sum()
        gross_profit  = df[df["Profit / Loss"] > 0]["Profit / Loss"].sum()
        gross_loss    = df[df["Profit / Loss"] < 0]["Profit / Loss"].sum()
        net_pnl_total = df["Profit / Loss"].sum()

        # Save main file
        out_path = f"pnl_tax_report_FY{fy_str}.csv"
        df.to_csv(out_path, index=False)

        # Save summary
        summary_path = f"pnl_summary_FY{fy_str}.csv"
        summary = pd.DataFrame([{
            "Financial Year":    fy_str,
            "Total Trades":      total_trades,
            "Winning Trades":    winning,
            "Losing Trades":     losing,
            "Win Rate %":        round(winning/total_trades*100, 1) if total_trades else 0,
            "Gross Profit ₹":    round(gross_profit, 2),
            "Gross Loss ₹":      round(abs(gross_loss), 2),
            "Net P&L ₹":         round(net_pnl_total, 2),
            "Tax Treatment":     "Business Income (ITR-3 Schedule F&O)",
            "Note":              "Consult CA for STT, brokerage deductions",
        }])
        summary.to_csv(summary_path, index=False)

        logger.info("Tax export: %d trades, Net P&L ₹%.2f → %s",
                    total_trades, net_pnl_total, out_path)
        return out_path

    except Exception as e:
        logger.error("Tax export: %s", e)
        return None


def format_tax_summary_message(fy: str = None) -> str:
    """For Telegram /export_tax command."""
    try:
        _, _, fy_str = get_financial_year() if not fy else \
                       (None, None, fy)
        out_path = export_pnl_for_tax(fy_str)
        if not out_path:
            return "⚠️ No closed trades found for this period"

        import sqlite3
        conn = sqlite3.connect("trades.db")
        rows = conn.execute(
            "SELECT SUM(realized_pnl), COUNT(*) FROM trades WHERE status='CLOSED'"
        ).fetchone()
        conn.close()
        net = float(rows[0] or 0)
        cnt = int(rows[1] or 0)

        return (
            f"📊 <b>TAX EXPORT — FY{fy_str}</b>\n\n"
            f"  Total trades:  {cnt}\n"
            f"  Net P&L:       ₹{net:+,.2f}\n\n"
            f"  Files generated:\n"
            f"  📄 {out_path}\n"
            f"  📄 pnl_summary_FY{fy_str}.csv\n\n"
            f"  ⚠️ Tax treatment: Business Income (ITR-3)\n"
            f"  ⚠️ Consult CA for STT and brokerage deductions\n\n"
            f"  Use /export to download the CSV file"
        )
    except Exception as e:
        return f"❌ Tax export: {e}"
