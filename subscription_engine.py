"""
subscription_engine.py — Complete subscriber lifecycle management

Features:
  - 7-day free trial auto-conversion
  - Subscription expiry notifications (3 days + 1 day before)
  - Auto-downgrade on expiry
  - Razorpay payment link generation
  - Subscriber onboarding welcome sequence
  - Win rate proof posts for conversion
"""
from __future__ import annotations
import json, logging, os, time
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)
_SUB_FILE  = Path("subscribers.json")
_TRIAL_DAYS = 7
_RAZORPAY_KEY = os.getenv("RAZORPAY_KEY_ID", "")  # add to .env


def _load() -> dict:
    try:
        return json.loads(_SUB_FILE.read_text()) if _SUB_FILE.exists() else {"subscribers": {}}
    except Exception:
        return {"subscribers": {}}


def _save(data: dict):
    try: _SUB_FILE.write_text(json.dumps(data, indent=2))
    except Exception as e: logger.debug("sub_save: %s", e)


# ── TRIAL MANAGEMENT ─────────────────────────────────────────────
def start_trial(chat_id: str, name: str = "") -> str:
    """Start 7-day free trial for a new subscriber. IMPROVEMENT: Auto-trial"""
    data = _load()
    chat_id = str(chat_id)
    trial_end = (date.today() + timedelta(days=_TRIAL_DAYS)).isoformat()
    now = date.today().isoformat()

    if chat_id in data["subscribers"]:
        sub = data["subscribers"][chat_id]
        if sub.get("trial_used"):
            return "❌ Trial already used for this account"
        sub.update({"tier": "premium", "trial_used": True,
                    "trial_end": trial_end, "active": True})
    else:
        data["subscribers"][chat_id] = {
            "name": name or f"User_{chat_id[-4:]}",
            "tier": "premium",
            "joined": now,
            "trial_used": True,
            "trial_end": trial_end,
            "active": True,
            "signals_today": 0,
            "last_reset": now,
        }
    _save(data)
    return (
        f"🎉 <b>7-DAY PREMIUM TRIAL STARTED!</b>\n\n"
        f"  You now get:\n"
        f"  ✅ All signals in real-time\n"
        f"  ✅ Full WHY reasons + lot sizes\n"
        f"  ✅ Morning video brief\n"
        f"  ✅ EOD performance report\n"
        f"  ✅ WOW factor analysis\n\n"
        f"  Trial ends: {trial_end}\n"
        f"  After trial: ₹2,999/month\n\n"
        f"  ⚠️ Educational only | Not SEBI advice"
    )


def check_expiring_subscriptions(alerts=None) -> list:
    """
    Check for subscriptions expiring in 3 days or 1 day.
    Send renewal reminders automatically.
    GAP 16 fix: Subscription expiry notifications.
    """
    data = _load()
    expiring = []
    today = date.today()

    for chat_id, sub in data["subscribers"].items():
        if not sub.get("active", True):
            continue
        tier = sub.get("tier", "free")
        if tier == "free":
            continue

        # Check trial expiry
        trial_end = sub.get("trial_end")
        sub_end   = sub.get("subscription_end")
        end_date_str = sub_end or trial_end

        if not end_date_str:
            continue

        try:
            end = date.fromisoformat(end_date_str)
            days_left = (end - today).days

            if days_left in (3, 1):
                expiring.append({"chat_id": chat_id, "days_left": days_left,
                                 "name": sub.get("name","?"), "tier": tier,
                                 "is_trial": bool(trial_end and not sub_end)})

                if alerts:
                    pay_link = get_payment_link(chat_id, tier)
                    is_trial = bool(trial_end and not sub_end)
                    msg = (
                        f"⏰ <b>{'TRIAL' if is_trial else 'SUBSCRIPTION'} EXPIRING</b>\n\n"
                        f"  Hi {sub.get('name','!')} — your {tier} access\n"
                        f"  expires in <b>{days_left} day{'s' if days_left>1 else ''}</b>\n\n"
                        f"  To continue: {pay_link or 'Contact @YourUsername'}\n\n"
                        f"  Monthly P&L: Use /compare to see performance\n"
                        f"  30-day accuracy: Use /weekly to see win rate"
                    )
                    try:
                        import requests
                        bot_token = os.getenv("TELEGRAM_BOT_TOKEN","")
                        if bot_token:
                            requests.post(
                                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                                json={"chat_id": chat_id, "text": msg,
                                      "parse_mode": "HTML"}, timeout=10
                            )
                    except Exception: pass

            elif days_left <= 0:
                # Expired — auto-downgrade
                sub["tier"] = "free"
                sub["active"] = True
                sub["expired_on"] = today.isoformat()
                logger.info("Subscription expired: %s %s", chat_id, sub.get("name","?"))

        except Exception:
            pass

    _save(data)
    return expiring


def get_payment_link(chat_id: str, tier: str = "premium") -> str:
    """
    GAP 17: Generate Razorpay payment link.
    Add RAZORPAY_KEY_ID to .env and this auto-generates.
    """
    prices = {"basic": 99900, "premium": 299900}  # in paise
    amount = prices.get(tier, 299900)
    names  = {"basic": "Basic Plan ₹999/month",
              "premium": "Premium Plan ₹2,999/month"}

    if _RAZORPAY_KEY:
        try:
            import requests
            r = requests.post(
                "https://api.razorpay.com/v1/payment_links",
                auth=(_RAZORPAY_KEY, os.getenv("RAZORPAY_KEY_SECRET","")),
                json={
                    "amount": amount,
                    "currency": "INR",
                    "description": names.get(tier, "Signal Service"),
                    "customer": {"contact": chat_id},
                    "notes": {"chat_id": chat_id, "tier": tier},
                    "callback_url": "",
                    "callback_method": "get",
                },
                timeout=10
            )
            if r.status_code == 200:
                return r.json().get("short_url", "")
        except Exception as e:
            logger.debug("razorpay: %s", e)

    # Fallback: manual UPI link
    upi_id = os.getenv("UPI_ID", "your_upi@paytm")
    return f"Pay via UPI: {upi_id} | Amount: ₹{amount//100} | Note: {tier}_{chat_id[-4:]}"


def get_onboarding_message(name: str = "") -> str:
    """
    GAP 15: New subscriber onboarding message.
    Sent when someone joins the free channel or starts trial.
    """
    return (
        f"👋 <b>WELCOME TO NIFTY ALGO SIGNALS!</b>\n\n"
        f"  I scan 196 NSE symbols every 5 minutes\n"
        f"  using 60 AI strategies + 15 WOW factors.\n\n"
        f"  <b>📡 WHAT YOU'LL GET</b>\n"
        f"  • Real-time BUY/SELL signals with entry, target, SL\n"
        f"  • WHY the signal was generated (RSI + FII + regime)\n"
        f"  • Lot size guidance for your capital\n"
        f"  • Daily accuracy report (proof of performance)\n\n"
        f"  <b>📋 HOW TO USE A SIGNAL</b>\n"
        f"  1. Get signal: BUY RELIANCE ₹1,315 | T:₹1,340 | SL:₹1,300\n"
        f"  2. Open your broker app (Zerodha/Upstox/Angel)\n"
        f"  3. Place BUY order at ₹1,315 with SL at ₹1,300\n"
        f"  4. Wait for target hit — you get a notification\n\n"
        f"  <b>💡 QUICK COMMANDS</b>\n"
        f"  /calculate NIFTY 100000 → Position size for ₹1L capital\n"
        f"  /today → All signals sent today\n"
        f"  /compare → Our accuracy vs NIFTY\n"
        f"  /paper → Track signals virtually (no real money)\n\n"
        f"  <b>🆓 START 7-DAY FREE TRIAL</b>\n"
        f"  Type /trial to get full premium access free for 7 days\n\n"
        f"  ⚠️ Educational signals only | Not SEBI registered advice\n"
        f"  Always set stop loss before entering any trade"
    )


def format_subscription_status(chat_id: str) -> str:
    """Show subscriber's current plan and expiry."""
    data = _load()
    sub  = data["subscribers"].get(str(chat_id))
    if not sub:
        return "❌ Not subscribed\nType /trial for 7-day free trial"

    tier  = sub.get("tier", "free")
    end   = sub.get("subscription_end") or sub.get("trial_end", "ongoing")
    views = sub.get("view_count", 0)
    tier_names = {"free": "Free", "basic": "Basic ₹999/mo",
                  "premium": "Premium ₹2,999/mo", "owner": "Owner"}

    return (
        f"📋 <b>YOUR SUBSCRIPTION</b>\n\n"
        f"  Plan:    {tier_names.get(tier, tier)}\n"
        f"  Status:  {'✅ Active' if sub.get('active',True) else '❌ Inactive'}\n"
        f"  Expires: {end}\n"
        f"  Signals viewed: {views}\n\n"
        f"  Upgrade: /upgrade\n"
        f"  Cancel:  /cancel"
    )
