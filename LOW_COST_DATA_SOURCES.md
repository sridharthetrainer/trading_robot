# Low-Cost Data Source Setup

## Recommended order

1. **Angel One SmartAPI**: keep as the primary execution, quote and WebSocket
   source. The current account is already connected, so this adds no new data
   subscription. It now supplies correctly scaled tick prices and quantity-
   weighted tick-flow observations.
2. **Upstox Analytics Token**: recommended option-chain backup. It is read-only,
   lasts one year, and supports option-chain requests without granting order
   permissions. Add only `UPSTOX_ANALYTICS_TOKEN` to `.env`.
3. **DhanHQ**: optional third source. It provides full chains with OI, volume,
   bid/ask and Greeks, but Dhan states that Data APIs may have additional
   charges. Ordinary web-generated access tokens last 24 hours.
4. **Public NSE website**: retained as a free best-effort fallback. It is not an
   institutional feed and may block an IP; do not pay for a proxy before trying
   the authenticated broker sources above.

## Optional `.env` settings

```dotenv
# Preferred read-only option-chain backup
UPSTOX_ANALYTICS_TOKEN=

# Optional Dhan fallback
DHAN_CLIENT_CODE=
DHAN_TOKEN_ID=

# Provider priority; missing credentials are skipped automatically
OPTION_CHAIN_PROVIDER_ORDER=upstox,dhan

# Existing Angel WebSocket: collect ticks for the signal universe
WS_SUBSCRIBE_SIGNAL_UNIVERSE=true
```

No optional credential being absent stops the bot. Every snapshot stores its
actual provider, live/stale status, fetch time and request correlation ID.

## Not yet replaceable cheaply

True exchange footprint requires aggressor-side trades or order-book events.
The current system has quantity-weighted tick-rule flow and best bid/ask chain
snapshots, which are useful proxies but are not NSE tick-by-tick order-book data.
NSE's official tick-by-tick feed is a professional paid product delivered by
NSE Data & Analytics or authorized vendors.
