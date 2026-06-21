"""
channel_config.py — Telegram Channel Configuration

Free channel:    @NiftyAlgoSignals    ID: -1003830079189
Premium channel: @NiftyAlgoSignalsPro ID: -1003993110321

Import anywhere:
    from channel_config import FREE_CHANNEL_ID, PREMIUM_CHANNEL_ID, send_to_free, send_to_premium
"""
from __future__ import annotations
import os
import logging

logger = logging.getLogger(__name__)

# Channel IDs — read from .env, with hardcoded fallback
FREE_CHANNEL_ID    = os.getenv("TELEGRAM_FREE_CHANNEL_ID",    "-1003830079189")
PREMIUM_CHANNEL_ID = os.getenv("TELEGRAM_PREMIUM_CHANNEL_ID", "-1003993110321")


def send_to_free(alerts, message: str, parse_mode: str = "HTML") -> bool:
    """Send a message to the free public channel."""
    try:
        if not FREE_CHANNEL_ID:
            return False
        alerts.send_to_channel(FREE_CHANNEL_ID, message)
        return True
    except Exception as e:
        logger.debug("send_to_free: %s", e)
        return False


def send_to_premium(alerts, message: str, parse_mode: str = "HTML") -> bool:
    """Send a message to the premium private channel."""
    try:
        if not PREMIUM_CHANNEL_ID:
            return False
        alerts.send_to_channel(PREMIUM_CHANNEL_ID, message)
        return True
    except Exception as e:
        logger.debug("send_to_premium: %s", e)
        return False


def send_to_both(alerts, message: str, premium_message: str = "") -> None:
    """Send to both channels. Premium gets richer content if provided."""
    send_to_free(alerts, message)
    send_to_premium(alerts, premium_message or message)


def get_channel_ids() -> dict:
    return {
        "free":    FREE_CHANNEL_ID,
        "premium": PREMIUM_CHANNEL_ID,
    }
