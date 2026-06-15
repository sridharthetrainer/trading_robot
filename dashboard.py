from __future__ import annotations

import json
import logging
import sqlite3
import statistics
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class TradingDashboard:
    """
    HTML dashboard generator for:
    - trade journal analytics
    - strategy performance
    - regime performance
    - confidence / AI score analysis
    - skip/no-signal diagnostics
    - RL / strategy-state visibility
    """

    def __init__(
        self,
        trades_db_path: str = "trades.db",
        strategy_state_file: str = "strategy_state.json",
        rl_state_file: str = "rl_state.json",
        no_signal_log_file: str = "no_signal.log",
        diagnostics_log_file: str = "diagnostics.log",
        output_file: str = "dashboard.html",
        **kwargs,
    ):
        self.trades_db_path = trades_db_path
        self.strategy_state_file = strategy_state_file
        self.rl_state_file = rl_state_file
        self.no_signal_log_file = no_signal_log_file
        self.diagnostics_log_file = diagnostics_log_file
        self.output_file = output_file
        self.extra_kwargs = kwargs

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------
    def generate(self) -> str:
        closed_trades = self._load_closed_trades()
        open_trades = self._load_open_trades()
        strategy_state = self._load_json_file(self.strategy_state_file, default={})
        rl_state = self._load_json_file(self.rl_state_file, default={})
        no_signal_events = self._load_jsonl(self.no_signal_log_file, limit=200)
        diagnostics = self._load_jsonl(self.diagnostics_log_file, limit=200)

        summary = self._build_summary(closed_trades, open_trades)
        strategy_stats = self._group_trade_stats(closed_trades, "strategy")
        regime_stats = self._group_trade_stats(closed_trades, "regime")
        hour_stats = self._build_hour_stats(closed_trades)
        confidence_stats = self._build_confidence_stats(closed_trades)
        recent_closed = list(reversed(closed_trades[-25:]))
        recent_open = open_trades[-25:]
        no_signal_reason_counts = self._count_key(no_signal_events, "reason")
        diagnostic_reason_counts = self._count_key(diagnostics, "reason")
        equity_curve = self._build_equity_curve(closed_trades)

        html = self._render_html(
            summary=summary,
            strategy_stats=strategy_stats,
            regime_stats=regime_stats,
            hour_stats=hour_stats,
            confidence_stats=confidence_stats,
            recent_closed=recent_closed,
            recent_open=recent_open,
            strategy_state=strategy_state,
            rl_state=rl_state,
            no_signal_reason_counts=no_signal_reason_counts,
            diagnostic_reason_counts=diagnostic_reason_counts,
            no_signal_events=no_signal_events[-20:],
            diagnostics=diagnostics[-20:],
            equity_curve=equity_curve,
        )

        output_path = Path(self.output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(html, encoding="utf-8")
        logger.info("Dashboard written to %s", output_path)
        return str(output_path)

    # ------------------------------------------------------------------
    # Loaders
    # ------------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.trades_db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _load_closed_trades(self) -> List[Dict[str, Any]]:
        if not Path(self.trades_db_path).exists():
            return []

        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT *
                FROM trades
                WHERE status = 'CLOSED'
                ORDER BY COALESCE(exit_time, entry_time) ASC
                """
            )
            rows = [dict(r) for r in cur.fetchall()]
            for row in rows:
                row["metadata"] = self._safe_json_parse(row.get("metadata"))
            return rows
        finally:
            conn.close()

    def _load_open_trades(self) -> List[Dict[str, Any]]:
        if not Path(self.trades_db_path).exists():
            return []

        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT *
                FROM trades
                WHERE status = 'OPEN'
                ORDER BY entry_time ASC
                """
            )
            rows = [dict(r) for r in cur.fetchall()]
            for row in rows:
                row["metadata"] = self._safe_json_parse(row.get("metadata"))
            return rows
        finally:
            conn.close()

    def _load_json_file(self, path: str, default: Any) -> Any:
        p = Path(path)
        if not p.exists():
            return default
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("Failed to load JSON file: %s", path)
            return default

    def _load_jsonl(self, path: str, limit: int = 200) -> List[Dict[str, Any]]:
        p = Path(path)
        if not p.exists():
            return []
        rows: List[Dict[str, Any]] = []
        try:
            with p.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        continue
            return rows[-limit:]
        except Exception:
            logger.exception("Failed to load JSONL file: %s", path)
            return []

    def _safe_json_parse(self, value: Any) -> Any:
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                return json.loads(value)
            except Exception:
                return {"raw": value}
        return value

    # ------------------------------------------------------------------
    # Analytics
    # ------------------------------------------------------------------
    def _build_summary(self, closed_trades: List[Dict[str, Any]], open_trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        pnls = [float(t.get("realized_pnl") or 0.0) for t in closed_trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        total_trades = len(closed_trades)
        total_pnl = round(sum(pnls), 2)
        win_rate = round((len(wins) / total_trades) * 100.0, 2) if total_trades else 0.0
        avg_win = round(sum(wins) / len(wins), 2) if wins else 0.0
        avg_loss = round(sum(losses) / len(losses), 2) if losses else 0.0
        best_trade = round(max(pnls), 2) if pnls else 0.0
        worst_trade = round(min(pnls), 2) if pnls else 0.0

        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        for pnl in pnls:
            equity += pnl
            peak = max(peak, equity)
            dd = peak - equity
            max_dd = max(max_dd, dd)

        return {
            "total_trades": total_trades,
            "open_trades": len(open_trades),
            "total_pnl": total_pnl,
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "best_trade": best_trade,
            "worst_trade": worst_trade,
            "max_drawdown": round(max_dd, 2),
        }

    def _group_trade_stats(self, trades: List[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
        grouped: Dict[str, List[float]] = defaultdict(list)

        for t in trades:
            group_value = str(t.get(key) or "UNKNOWN")
            grouped[group_value].append(float(t.get("realized_pnl") or 0.0))

        rows: List[Dict[str, Any]] = []
        for group_value, pnls in grouped.items():
            wins = [p for p in pnls if p > 0]
            rows.append(
                {
                    key: group_value,
                    "trades": len(pnls),
                    "total_pnl": round(sum(pnls), 2),
                    "avg_pnl": round(sum(pnls) / len(pnls), 2) if pnls else 0.0,
                    "win_rate": round((len(wins) / len(pnls)) * 100.0, 2) if pnls else 0.0,
                    "best": round(max(pnls), 2) if pnls else 0.0,
                    "worst": round(min(pnls), 2) if pnls else 0.0,
                }
            )

        rows.sort(key=lambda x: x["total_pnl"], reverse=True)
        return rows

    def _build_hour_stats(self, trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        grouped: Dict[str, List[float]] = defaultdict(list)

        for t in trades:
            entry_time = t.get("entry_time")
            if not entry_time:
                continue
            hour = time.strftime("%H:00", time.localtime(float(entry_time)))
            grouped[hour].append(float(t.get("realized_pnl") or 0.0))

        rows = []
        for hour, pnls in sorted(grouped.items()):
            rows.append(
                {
                    "hour": hour,
                    "trades": len(pnls),
                    "total_pnl": round(sum(pnls), 2),
                    "avg_pnl": round(sum(pnls) / len(pnls), 2),
                }
            )
        return rows

    def _build_confidence_stats(self, trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        confidence_rows = []
        score_rows = []

        for t in trades:
            pnl = float(t.get("realized_pnl") or 0.0)
            conf = t.get("confidence")
            score = t.get("score")

            if conf is not None:
                confidence_rows.append((float(conf), pnl))
            if score is not None:
                score_rows.append((float(score), pnl))

        def summarize(rows: List[Any]) -> Dict[str, Any]:
            if not rows:
                return {"count": 0, "avg_metric": 0.0, "avg_pnl": 0.0, "corr_like": 0.0}
            xs = [r[0] for r in rows]
            ys = [r[1] for r in rows]
            avg_x = statistics.mean(xs)
            avg_y = statistics.mean(ys)
            corr_like = self._simple_corr(xs, ys)
            return {
                "count": len(rows),
                "avg_metric": round(avg_x, 4),
                "avg_pnl": round(avg_y, 2),
                "corr_like": round(corr_like, 4),
            }

        return {
            "confidence": summarize(confidence_rows),
            "score": summarize(score_rows),
        }

    def _simple_corr(self, xs: List[float], ys: List[float]) -> float:
        if len(xs) < 2 or len(ys) < 2:
            return 0.0
        mean_x = statistics.mean(xs)
        mean_y = statistics.mean(ys)
        num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
        den_x = sum((x - mean_x) ** 2 for x in xs) ** 0.5
        den_y = sum((y - mean_y) ** 2 for y in ys) ** 0.5
        if den_x == 0 or den_y == 0:
            return 0.0
        return num / (den_x * den_y)

    def _build_equity_curve(self, trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        equity = 0.0
        curve = []

        for idx, t in enumerate(trades, start=1):
            pnl = float(t.get("realized_pnl") or 0.0)
            equity += pnl
            curve.append(
                {
                    "idx": idx,
                    "equity": round(equity, 2),
                    "pnl": round(pnl, 2),
                    "trade_id": t.get("trade_id"),
                    "strategy": t.get("strategy"),
                    "regime": t.get("regime"),
                }
            )
        return curve

    def _count_key(self, rows: List[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
        counts: Dict[str, int] = defaultdict(int)
        for row in rows:
            counts[str(row.get(key) or "UNKNOWN")] += 1
        result = [{"name": k, "count": v} for k, v in counts.items()]
        result.sort(key=lambda x: x["count"], reverse=True)
        return result

    # ------------------------------------------------------------------
    # HTML render
    # ------------------------------------------------------------------
    def _render_html(
        self,
        *,
        summary: Dict[str, Any],
        strategy_stats: List[Dict[str, Any]],
        regime_stats: List[Dict[str, Any]],
        hour_stats: List[Dict[str, Any]],
        confidence_stats: Dict[str, Any],
        recent_closed: List[Dict[str, Any]],
        recent_open: List[Dict[str, Any]],
        strategy_state: Dict[str, Any],
        rl_state: Dict[str, Any],
        no_signal_reason_counts: List[Dict[str, Any]],
        diagnostic_reason_counts: List[Dict[str, Any]],
        no_signal_events: List[Dict[str, Any]],
        diagnostics: List[Dict[str, Any]],
        equity_curve: List[Dict[str, Any]],
    ) -> str:
        def cards_html() -> str:
            items = [
                ("Closed Trades", summary["total_trades"]),
                ("Open Trades", summary["open_trades"]),
                ("Total P&L", f"₹{summary['total_pnl']:.2f}"),
                ("Win Rate", f"{summary['win_rate']:.2f}%"),
                ("Avg Win", f"₹{summary['avg_win']:.2f}"),
                ("Avg Loss", f"₹{summary['avg_loss']:.2f}"),
                ("Best Trade", f"₹{summary['best_trade']:.2f}"),
                ("Worst Trade", f"₹{summary['worst_trade']:.2f}"),
                ("Max Drawdown", f"₹{summary['max_drawdown']:.2f}"),
            ]
            return "".join(
                f'<div class="card"><div class="label">{label}</div><div class="value">{value}</div></div>'
                for label, value in items
            )

        def table_html(rows: List[Dict[str, Any]], columns: List[str]) -> str:
            if not rows:
                return '<div class="empty">No data</div>'

            header = "".join(f"<th>{c}</th>" for c in columns)
            body_rows = []
            for row in rows:
                body_rows.append(
                    "<tr>" + "".join(f"<td>{row.get(c, '')}</td>" for c in columns) + "</tr>"
                )
            return f"<table><thead><tr>{header}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"

        def pre_json(obj: Any) -> str:
            return json.dumps(obj, indent=2, default=str)

        equity_js = json.dumps(equity_curve, default=str)

        return f"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Autonomous Trading Dashboard</title>
<meta http-equiv="refresh" content="60"/>
<style>
    body {{
        font-family: Arial, sans-serif;
        margin: 0;
        padding: 20px;
        background: #f5f7fb;
        color: #1f2937;
    }}
    h1, h2 {{
        margin: 0 0 14px 0;
    }}
    .section {{
        background: white;
        border-radius: 16px;
        padding: 18px;
        margin-bottom: 18px;
        box-shadow: 0 3px 12px rgba(0,0,0,0.06);
    }}
    .grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
        gap: 12px;
    }}
    .card {{
        border: 1px solid #e5e7eb;
        border-radius: 12px;
        padding: 14px;
        background: #fafafa;
    }}
    .label {{
        font-size: 12px;
        color: #6b7280;
        margin-bottom: 6px;
    }}
    .value {{
        font-size: 22px;
        font-weight: bold;
    }}
    table {{
        width: 100%;
        border-collapse: collapse;
        font-size: 13px;
    }}
    th, td {{
        border-bottom: 1px solid #e5e7eb;
        padding: 8px 10px;
        text-align: left;
        vertical-align: top;
    }}
    th {{
        background: #f9fafb;
    }}
    .two-col {{
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 18px;
    }}
    .three-col {{
        display: grid;
        grid-template-columns: 1fr 1fr 1fr;
        gap: 18px;
    }}
    .empty {{
        color: #6b7280;
        padding: 12px 0;
    }}
    pre {{
        background: #111827;
        color: #f3f4f6;
        padding: 14px;
        border-radius: 12px;
        overflow-x: auto;
        font-size: 12px;
    }}
    canvas {{
        width: 100%;
        max-width: 100%;
        height: 320px;
        border: 1px solid #e5e7eb;
        border-radius: 12px;
        background: white;
    }}
    @media (max-width: 900px) {{
        .two-col, .three-col {{
            grid-template-columns: 1fr;
        }}
    }}
</style>
</head>
<body>
    <div class="section">
        <h1>Autonomous Trading Dashboard</h1>
        <div class="grid">
            {cards_html()}
        </div>
    </div>

    <div class="section">
        <h2>Equity Curve</h2>
        <canvas id="equityCanvas" width="1200" height="320"></canvas>
    </div>

    <div class="two-col">
        <div class="section">
            <h2>Strategy Performance</h2>
            {table_html(strategy_stats, ["strategy", "trades", "total_pnl", "avg_pnl", "win_rate", "best", "worst"])}
        </div>
        <div class="section">
            <h2>Regime Performance</h2>
            {table_html(regime_stats, ["regime", "trades", "total_pnl", "avg_pnl", "win_rate", "best", "worst"])}
        </div>
    </div>

    <div class="three-col">
        <div class="section">
            <h2>Hour Analysis</h2>
            {table_html(hour_stats, ["hour", "trades", "total_pnl", "avg_pnl"])}
        </div>
        <div class="section">
            <h2>No Signal Reasons</h2>
            {table_html(no_signal_reason_counts, ["name", "count"])}
        </div>
        <div class="section">
            <h2>Diagnostic Reasons</h2>
            {table_html(diagnostic_reason_counts, ["name", "count"])}
        </div>
    </div>

    <div class="three-col">
        <div class="section">
            <h2>Confidence Analytics</h2>
            <pre>{pre_json(confidence_stats.get("confidence", {}))}</pre>
        </div>
        <div class="section">
            <h2>Score Analytics</h2>
            <pre>{pre_json(confidence_stats.get("score", {}))}</pre>
        </div>
        <div class="section">
            <h2>RL State</h2>
            <pre>{pre_json(rl_state)}</pre>
        </div>
    </div>

    <div class="two-col">
        <div class="section">
            <h2>Strategy State</h2>
            <pre>{pre_json(strategy_state)}</pre>
        </div>
        <div class="section">
            <h2>Recent No Signal Events</h2>
            <pre>{pre_json(no_signal_events)}</pre>
        </div>
    </div>

    <div class="two-col">
        <div class="section">
            <h2>Recent Diagnostics</h2>
            <pre>{pre_json(diagnostics)}</pre>
        </div>
        <div class="section">
            <h2>Open Trades</h2>
            {table_html(recent_open, ["trade_id", "symbol", "side", "qty", "strategy", "entry_price", "stop_loss", "target_price", "trail_stop", "confidence", "regime", "score"])}
        </div>
    </div>

    <div class="section">
        <h2>Recent Closed Trades</h2>
        {table_html(recent_closed, ["trade_id", "symbol", "side", "qty", "strategy", "entry_price", "exit_price", "realized_pnl", "exit_reason", "confidence", "regime", "score"])}
    </div>

<script>
(function() {{
    const data = {equity_js};
    const canvas = document.getElementById("equityCanvas");
    const ctx = canvas.getContext("2d");

    function draw() {{
        ctx.clearRect(0, 0, canvas.width, canvas.height);

        if (!data || data.length === 0) {{
            ctx.font = "16px Arial";
            ctx.fillText("No equity data yet", 20, 40);
            return;
        }}

        const padding = 40;
        const values = data.map(d => Number(d.equity || 0));
        const minVal = Math.min(...values);
        const maxVal = Math.max(...values);
        const range = Math.max(maxVal - minVal, 1);

        const width = canvas.width - padding * 2;
        const height = canvas.height - padding * 2;

        ctx.strokeStyle = "#d1d5db";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(padding, padding);
        ctx.lineTo(padding, canvas.height - padding);
        ctx.lineTo(canvas.width - padding, canvas.height - padding);
        ctx.stroke();

        ctx.strokeStyle = "#2563eb";
        ctx.lineWidth = 2;
        ctx.beginPath();

        data.forEach((point, i) => {{
            const x = padding + (i / Math.max(data.length - 1, 1)) * width;
            const y = canvas.height - padding - ((Number(point.equity || 0) - minVal) / range) * height;
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        }});

        ctx.stroke();

        ctx.fillStyle = "#111827";
        ctx.font = "12px Arial";
        ctx.fillText("Min: " + minVal.toFixed(2), padding, 18);
        ctx.fillText("Max: " + maxVal.toFixed(2), 140, 18);
        ctx.fillText("Trades: " + data.length, 280, 18);
    }}

    draw();
}})();
</script>
</body>
</html>
"""

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    dashboard = TradingDashboard()
    path = dashboard.generate()
    print(f"Dashboard generated: {path}")


if __name__ == "__main__":
    main()
