"""
subscriber_manager.py — Multi-Client Signal Service

Architecture:
  - One bot generates signals (your Angel One account)
  - Signals broadcast to unlimited subscribers
  - Each subscriber gets same signal, manages their own capital
  - SEBI compliant: educational signals only, no portfolio management

Subscriber tiers:
  FREE    — 2 signals/day, delayed 5 min, no analytics
  BASIC   — 8 signals/day, real-time, basic analytics (₹999/month)
  PREMIUM — Unlimited signals, real-time + Greeks + brief (₹2999/month)

Telegram channels:
  Free:    t.me/YourBotFreeSignals
  Premium: t.me/YourBotPremiumSignals (invite only)
"""
from __future__ import annotations
import json, logging
from datetime import date
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

_SUB_FILE = Path("subscribers.json")

# Tier definitions
TIERS = {
    "free":    {"signals_per_day": 2,  "delay_sec": 300, "analytics": False, "price": 0},
    "basic":   {"signals_per_day": 8,  "delay_sec": 0,   "analytics": True,  "price": 999},
    "premium": {"signals_per_day": 99, "delay_sec": 0,   "analytics": True,  "price": 2999},
    "owner":   {"signals_per_day": 99, "delay_sec": 0,   "analytics": True,  "price": 0},
}

# Telegram channel IDs (configure in .env)
# TELEGRAM_FREE_CHANNEL_ID=-100xxxxxxxxxx
# TELEGRAM_PREMIUM_CHANNEL_ID=-100xxxxxxxxxx


def _load() -> dict:
    if not _SUB_FILE.exists():
        return {"subscribers": {}, "stats": {}}
    try:
        return json.loads(_SUB_FILE.read_text())
    except Exception:
        return {"subscribers": {}, "stats": {}}


def _save(data: dict):
    try:
        _SUB_FILE.write_text(json.dumps(data, indent=2))
    except Exception as e:
        logger.debug("subscriber save: %s", e)


def add_subscriber(chat_id: str, name: str = "", tier: str = "free") -> str:
    data = _load()
    chat_id = str(chat_id)
    if chat_id in data["subscribers"]:
        return f"Already subscribed ({data['subscribers'][chat_id].get('tier','free')} tier)"
    data["subscribers"][chat_id] = {
        "name":    name or f"User_{chat_id[-4:]}",
        "tier":    tier,
        "joined":  date.today().isoformat(),
        "active":  True,
        "signals_today": 0,
        "last_reset": date.today().isoformat(),
    }
    _save(data)
    return f"✅ Subscribed ({tier} tier)"


def get_active_subscribers(min_tier: str = "free") -> List[dict]:
    data = _load()
    tier_order = {"free": 0, "basic": 1, "premium": 2, "owner": 3}
    min_level = tier_order.get(min_tier, 0)
    result = []
    today = date.today().isoformat()
    for chat_id, sub in data["subscribers"].items():
        if not sub.get("active", True):
            continue
        sub_level = tier_order.get(sub.get("tier", "free"), 0)
        if sub_level >= min_level:
            # Reset daily counter
            if sub.get("last_reset") != today:
                sub["signals_today"] = 0
                sub["last_reset"] = today
            sub["chat_id"] = chat_id
            result.append(sub)
    _save(data)
    return result


def get_stats() -> dict:
    data = _load()
    subs = data["subscribers"]
    total = len(subs)
    active = sum(1 for s in subs.values() if s.get("active", True))
    by_tier = {}
    for s in subs.values():
        t = s.get("tier", "free")
        by_tier[t] = by_tier.get(t, 0) + 1
    mrr = (by_tier.get("basic", 0) * 999 +
           by_tier.get("premium", 0) * 2999)
    return {
        "total": total, "active": active,
        "by_tier": by_tier, "mrr": mrr
    }


def format_stats_report() -> str:
    st = get_stats()
    bt = st["by_tier"]
    return (
        f"👥 <b>SUBSCRIBER STATS</b>\n\n"
        f"  Total:    {st['total']}\n"
        f"  Active:   {st['active']}\n"
        f"  Free:     {bt.get('free',0)}\n"
        f"  Basic:    {bt.get('basic',0)} × ₹999\n"
        f"  Premium:  {bt.get('premium',0)} × ₹2,999\n"
        f"  ─────────────────\n"
        f"  MRR:      ₹{st['mrr']:,}/month\n"
        f"  ARR:      ₹{st['mrr']*12:,}/year\n"
    )


def track_signal_view(chat_id: str) -> None:
    """IMPROVEMENT 9: Track last signal view for churn detection."""
    from datetime import datetime
    data = _load()
    chat_id = str(chat_id)
    if chat_id in data["subscribers"]:
        data["subscribers"][chat_id]["last_viewed"] = datetime.now().isoformat()
        data["subscribers"][chat_id]["view_count"] =             data["subscribers"][chat_id].get("view_count", 0) + 1
        _save(data)


def get_churn_risk_subscribers() -> list:
    """Return paid subscribers inactive for 14+ days."""
    from datetime import datetime, timedelta
    data = _load()
    at_risk = []
    cutoff  = (datetime.now() - timedelta(days=14)).isoformat()
    for chat_id, sub in data["subscribers"].items():
        if sub.get("tier") in ("basic","premium") and sub.get("active", True):
            last = sub.get("last_viewed", sub.get("joined", "2024-01-01"))
            if last < cutoff:
                at_risk.append({
                    "chat_id": chat_id,
                    "name":    sub.get("name","?"),
                    "tier":    sub.get("tier","?"),
                    "inactive_since": last[:10],
                })
    return at_risk


def format_churn_report() -> str:
    at_risk = get_churn_risk_subscribers()
    if not at_risk:
        return "✅ No churn risk — all subscribers active"
    lines = [f"⚠️ <b>CHURN RISK ({len(at_risk)} subscribers)</b>", ""]
    for s in at_risk:
        lines.append(f"  {s['name']:20} [{s['tier']:8}] — inactive since {s['inactive_since']}")
    lines += ["", "  Action: Send re-engagement message or downgrade"]
    return "\n".join(lines)


def add_trial_subscriber(chat_id: str, name: str = "") -> str:
    """Add a 7-day free trial subscriber."""
    from datetime import datetime, timedelta
    data = _load()
    chat_id = str(chat_id)
    if chat_id in data["subscribers"]:
        return "Already subscribed"
    trial_ends = (datetime.now() + timedelta(days=7)).isoformat()
    data["subscribers"][chat_id] = {
        "name":         name or f"Trial_{chat_id[-4:]}",
        "tier":         "trial",
        "joined":       datetime.now().isoformat(),
        "trial_ends":   trial_ends,
        "active":       True,
        "signals_today": 0,
        "last_reset":   datetime.now().date().isoformat(),
    }
    _save(data)
    return (f"✅ 7-day free trial started!\n"
            f"  Trial ends: {trial_ends[:10]}\n"
            f"  You get: real-time signals + morning brief\n"
            f"  After trial: upgrade at /subscribe premium")


def check_and_expire_subscriptions(alerts=None) -> list:
    """
    Check all subscriptions for expiry. Send reminders.
    Called daily at 9 AM.
    Returns list of expired chat_ids.
    """
    from datetime import datetime
    data  = _load()
    now   = datetime.now()
    expired = []
    reminded_3d = []
    reminded_1d = []

    for chat_id, sub in data["subscribers"].items():
        tier = sub.get("tier", "free")

        # Trial expiry
        trial_end = sub.get("trial_ends")
        if trial_end and tier == "trial":
            te = datetime.fromisoformat(trial_end)
            days_left = (te - now).days
            if days_left <= 0:
                sub["tier"]   = "free"
                sub["active"] = True
                expired.append(chat_id)
                if alerts:
                    try:
                        alerts.send(
                            f"⏰ Trial ended for {sub.get('name','?')}\n"
                            f"  Upgrade: /subscribe premium for ₹2,999/mo\n"
                            f"  Or stay on free tier (2 signals/day)"
                        )
                    except Exception: pass
            elif days_left <= 3:
                reminded_3d.append(chat_id)
                if alerts:
                    try:
                        alerts.send(
                            f"⚠️ Trial expires in {days_left} days!\n"
                            f"  Use /subscribe premium to keep full access"
                        )
                    except Exception: pass

        # Paid subscription expiry (manual renewal check)
        sub_end = sub.get("subscription_ends")
        if sub_end and tier in ("basic", "premium"):
            se_dt = datetime.fromisoformat(sub_end)
            days_left = (se_dt - now).days
            if days_left <= 0:
                sub["tier"]   = "free"
                sub["active"] = True
                expired.append(chat_id)
                if alerts:
                    try:
                        alerts.send(
                            f"⏰ {tier.title()} subscription expired for {sub.get('name','?')}\n"
                            f"  Renew at same rate — contact admin"
                        )
                    except Exception: pass
            elif days_left in (1, 3, 7):
                if alerts:
                    try:
                        alerts.send(
                            f"📅 {sub.get('name','?')} — {tier} expires in {days_left} day{'s' if days_left>1 else ''}\n"
                            f"  Renew to avoid interruption"
                        )
                    except Exception: pass

    _save(data)
    return expired


def add_paid_subscriber(chat_id: str, tier: str, name: str = "",
                        months: int = 1) -> str:
    """Add/upgrade a paid subscriber with expiry date."""
    from datetime import datetime, timedelta
    data    = _load()
    chat_id = str(chat_id)
    ends    = (datetime.now() + timedelta(days=30*months)).isoformat()
    price   = {"basic": 999, "premium": 2999}.get(tier, 0) * months

    if chat_id not in data["subscribers"]:
        data["subscribers"][chat_id] = {
            "name": name or f"User_{chat_id[-4:]}",
            "joined": datetime.now().isoformat(),
        }

    data["subscribers"][chat_id].update({
        "tier":               tier,
        "active":             True,
        "subscription_ends":  ends,
        "last_payment":       datetime.now().isoformat(),
        "paid_amount":        price,
        "signals_today":      0,
        "last_reset":         datetime.now().date().isoformat(),
    })
    _save(data)
    return (f"✅ {tier.title()} subscription activated\n"
            f"  {name or chat_id} | {months} month(s)\n"
            f"  Expires: {ends[:10]}\n"
            f"  Amount: ₹{price:,}")

