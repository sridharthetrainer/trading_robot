"""
manual_journal.py — forward journal for the user's own chart-read trades.

Purpose (2026-10-17): log the plan (entry, exit, reason) BEFORE the outcome is
known, then the result, so discretionary reads become clean forward data that
a backtest cannot provide. Observations only: never used to place orders, never
counted as backtest tests, separate from rules.json.

Telegram (owner-only):
  /journal plan <entry, exit and reason in your words>
  /journal result <ID> <what happened, e.g. +45 pts win / -20 loss / skipped>
  /journal               (or /journal list)  -> latest entries

Storage: trade_journal/journal.jsonl (append-only, gitignored).
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

JOURNAL = Path(__file__).resolve().parent / "trade_journal" / "journal.jsonl"
MAX_TEXT = 500


def _load(path: Path = None) -> List[Dict]:
    path = path or JOURNAL
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _append(rec: Dict, path: Path = None) -> None:
    path = path or JOURNAL
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def add_plan(text: str, user: str = "", now: Optional[datetime] = None, path: Path = None) -> Dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("plan text is empty")
    now = now or datetime.now()
    existing = {r.get("id") for r in _load(path)}
    base = f"J-{now:%Y%m%d-%H%M}"
    jid, n = base, 1
    while jid in existing:
        n += 1
        jid = f"{base}-{n}"
    rec = {"id": jid, "type": "plan", "logged_at": now.isoformat(timespec="seconds"), "user": str(user),
           "text": text[:MAX_TEXT]}
    _append(rec, path)
    return rec


def add_result(jid: str, text: str, user: str = "", now: Optional[datetime] = None, path: Path = None) -> Dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("result text is empty")
    recs = _load(path)
    plans = {r["id"]: r for r in recs if r.get("type") == "plan"}
    if jid not in plans:
        raise KeyError(f"no plan with id {jid}")
    if any(r.get("type") == "result" and r.get("id") == jid for r in recs):
        raise ValueError(f"{jid} already has a result (append-only)")
    now = now or datetime.now()
    rec = {"id": jid, "type": "result", "logged_at": now.isoformat(timespec="seconds"), "user": str(user),
           "text": text[:MAX_TEXT]}
    _append(rec, path)
    return rec


def recent(n: int = 10, path: Path = None) -> List[Dict]:
    recs = _load(path)
    results = {r["id"]: r for r in recs if r.get("type") == "result"}
    plans = [r for r in recs if r.get("type") == "plan"][-n:]
    return [{**p, "result": results.get(p["id"], {}).get("text")} for p in plans]


def handle(text: str, user: str = "", path: Path = None) -> str:
    """Parse '/journal ...' and return the Telegram reply."""
    body = re.sub(r"^/journal(@\S+)?", "", (text or "").strip(), flags=re.I).strip()
    sub, _, rest = body.partition(" ")
    sub = sub.lower()
    try:
        if sub == "plan":
            rec = add_plan(rest, user, path=path)
            return (f"📝 Logged plan <b>{rec['id']}</b> at {rec['logged_at'][11:16]} (before the result).\n"
                    f"Later: <code>/journal result {rec['id']} what happened</code>")
        if sub == "result":
            jid, _, res = rest.strip().partition(" ")
            rec = add_result(jid.strip(), res, user, path=path)
            return f"✅ Result logged for <b>{rec['id']}</b>."
        if sub in ("", "list"):
            rows = recent(10, path=path)
            if not rows:
                return ("Journal is empty. Log a trade BEFORE the result:\n"
                        "<code>/journal plan short below VWAP, exit close above 1m 200 EMA, reason PDL break</code>")
            lines = ["📓 <b>Trade journal</b> (latest 10; observations only)"]
            for r in rows:
                lines.append(f"• <b>{r['id']}</b> {r['text'][:120]}\n   → {r['result'] or '<i>no result yet</i>'}")
            return "\n".join(lines)
        return "Use /journal plan &lt;text&gt;, /journal result &lt;ID&gt; &lt;text&gt;, or /journal list"
    except (ValueError, KeyError) as exc:
        return f"⚠️ {exc}"
