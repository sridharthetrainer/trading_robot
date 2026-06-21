"""
nse_proxy.py — single source of truth for routing NSE-direct requests
through an optional proxy.

NSE blocks this machine's IP at the network edge (every HTTP client gets 403
on nseindia.com, so sessions never obtain the required cookies). Routing the
NSE-direct fetchers through a residential/rotating proxy restores option-chain,
India VIX and FII/DII data.

Configure ONE env var (e.g. in .env):

    NSE_PROXY=http://user:pass@host:port

Only the NSE-direct fetchers call this — Angel One, Telegram and broker traffic
are deliberately NOT proxied (different reliability/cost profile). If NSE_PROXY
is unset, every helper here is a no-op and behaviour is unchanged.
"""

from __future__ import annotations

import os
from typing import Optional, Dict


def get_nse_proxies() -> Optional[Dict[str, str]]:
    """Return a requests-style proxies dict from NSE_PROXY, or None if unset."""
    p = os.getenv("NSE_PROXY", "").strip()
    if not p:
        return None
    return {"http": p, "https": p}


def apply(session) -> object:
    """Apply the NSE proxy to a requests.Session in place. No-op if unset."""
    proxies = get_nse_proxies()
    if proxies:
        try:
            session.proxies.update(proxies)
        except Exception:
            pass
    return session


def is_enabled() -> bool:
    """True if an NSE proxy is configured."""
    return get_nse_proxies() is not None
