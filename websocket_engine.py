"""
websocket_engine.py

Real-time LTP streaming via Angel One SmartWebSocketV2.

This solves the critical gap where trailing stops and live P&L were only
checked every 30 seconds via REST polling. With WebSocket streaming,
price ticks arrive in < 50ms and stops are checked on every tick.

Architecture
-----------
                ┌─────────────────────┐
                │  SmartWebSocketV2   │  (Angel One)
                │  tick feed          │
                └────────┬────────────┘
                         │ on_data callback
                         ▼
                ┌─────────────────────┐
                │  WebSocketEngine    │
                │  _on_tick()         │
                └────────┬────────────┘
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
    ┌──────────────────┐   ┌────────────────┐
    │  LTP Cache       │   │  TrailingStop  │
    │  (thread-safe)   │   │  check_exit()  │
    └──────────────────┘   └────────────────┘
              │                     │
              ▼                     ▼
    ┌──────────────────┐   ┌────────────────┐
    │  live_status.json│   │  trade_manager │
    │  unrealized PnL  │   │  close_trade() │
    └──────────────────┘   └────────────────┘

Usage
-----
    engine = WebSocketEngine(
        angel_obj      = angel_instance,
        trade_manager  = trade_manager,
        trailing       = trailing_instance,
    )
    engine.start()                    # call at market open
    engine.subscribe(["NIFTY", ...])  # subscribe to open positions
    engine.stop()                     # call at market close
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Subscription mode for SmartWebSocketV2
# 1 = LTP only (fastest, lowest bandwidth)
# 2 = Quote (bid/ask + LTP)
# 3 = SnapQuote (full depth)
WS_SUBSCRIBE_MODE   = 1
WS_EXCHANGE_NFO     = 2   # Angel One exchange code for NFO
WS_EXCHANGE_NSE     = 1   # Angel One exchange code for NSE
RECONNECT_DELAY_SEC = 5
MAX_RECONNECT       = 10
LIVE_STATUS_FILE    = "live_status.json"


class LTPCache:
    """Thread-safe LTP cache updated on every WebSocket tick."""

    def __init__(self) -> None:
        self._data: Dict[str, float] = {}
        self._ts:   Dict[str, float] = {}
        self._lock  = threading.Lock()

    def set(self, symbol: str, ltp: float) -> None:
        with self._lock:
            self._data[symbol] = float(ltp)
            self._ts[symbol]   = time.time()

    def get(self, symbol: str) -> Optional[float]:
        with self._lock:
            return self._data.get(symbol)

    def age(self, symbol: str) -> float:
        """Return seconds since last update for symbol. inf if never updated."""
        with self._lock:
            ts = self._ts.get(symbol)
            return (time.time() - ts) if ts else float("inf")

    def all_ltps(self) -> Dict[str, float]:
        with self._lock:
            return dict(self._data)


class WebSocketEngine:
    """
    Manages SmartWebSocketV2 connection for real-time price streaming.

    Key behaviours
    ─────────────
    1. Connects on start(), reconnects automatically on drop
    2. Subscribes to tokens for all currently open positions
    3. On every tick: updates LTPCache + checks trailing stops
    4. Writes unrealized P&L to live_status.json every tick
    5. Gracefully falls back if WebSocket library not installed
    """

    def __init__(
        self,
        angel_obj        = None,   # AngelOne instance
        trade_manager    = None,   # TradeManager instance
        trailing         = None,   # TrailingStopManager instance
        alerts           = None,   # AlertManager instance
        live_status_file: str = LIVE_STATUS_FILE,
    ) -> None:
        self.angel          = angel_obj
        self.trade_manager  = trade_manager
        self.trailing       = trailing
        self.alerts         = alerts
        self.ltp_cache      = LTPCache()
        self.live_status_file = Path(live_status_file)

        self._ws            = None      # SmartWebSocketV2 instance
        self._running       = False
        self._thread        = None
        self._reconnect_count = 0
        self._subscribed_tokens: Set[str] = set()
        self._token_symbol_map: Dict[str, str] = {}   # token → symbol
        self._symbol_token_map: Dict[str, str] = {}   # symbol → token
        self._lock          = threading.Lock()
        self._ws_available  = self._check_ws_available()

        if not self._ws_available:
            logger.warning(
                "WebSocketEngine: SmartWebSocketV2 not available — "
                "falling back to REST polling for stop-loss checks"
            )

    def _check_ws_available(self) -> bool:
        try:
            from SmartApi import SmartWebSocketV2 as _WS  # noqa
            return True
        except ImportError:
            return False

    # ── Token lookup ─────────────────────────────────────────────────────────

    def _get_token(self, symbol: str, exchange: str = "NFO") -> Optional[str]:
        """Get SmartAPI token for a symbol."""
        if symbol in self._symbol_token_map:
            return self._symbol_token_map[symbol]

        if self.angel is None:
            return None
        try:
            token = self.angel._get_token_no_lock(symbol, exchange)
            if token:
                self._symbol_token_map[symbol] = token
                self._token_symbol_map[token]  = symbol
            return token
        except Exception:
            return None

    # ── Connection ───────────────────────────────────────────────────────────

    def start(self) -> bool:
        """
        Start WebSocket in a background thread.
        Returns True if started, False if WebSocket not available.
        """
        if not self._ws_available:
            return False
        if self._running:
            return True

        self._running = True
        self._thread  = threading.Thread(
            target=self._run_loop, daemon=True, name="websocket_engine"
        )
        self._thread.start()
        logger.info("WebSocketEngine started")
        return True

    def stop(self) -> None:
        """Stop WebSocket connection."""
        self._running = False
        self._close_ws()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("WebSocketEngine stopped")

    def _close_ws(self) -> None:
        try:
            if self._ws:
                self._ws.close_connection()
        except Exception:
            pass
        self._ws = None

    def _run_loop(self) -> None:
        """Main loop — connects and reconnects on failure."""
        while self._running and self._reconnect_count < MAX_RECONNECT:
            try:
                self._connect_and_stream()
            except Exception as exc:
                logger.warning(
                    "WebSocket loop error (reconnect %d/%d): %s",
                    self._reconnect_count, MAX_RECONNECT, exc,
                )
                self._reconnect_count += 1
                if self._running:
                    time.sleep(RECONNECT_DELAY_SEC * self._reconnect_count)

        if self._reconnect_count >= MAX_RECONNECT:
            logger.error(
                "WebSocket failed after %d reconnects — "
                "system will use REST polling for stop checks",
                MAX_RECONNECT,
            )
            self._running = False

    def _connect_and_stream(self) -> None:
        """Establish WebSocket connection and subscribe."""
        try:
            from SmartApi import SmartWebSocketV2
        except ImportError:
            self._ws_available = False
            return

        if not self.angel or not hasattr(self.angel, "obj") or not self.angel.obj:
            logger.warning("WebSocket: angel instance not ready")
            time.sleep(10)
            return

        try:
            feed_token  = self.angel.obj.getfeedToken()
            client_id   = getattr(self.angel, "client_id", "")
            api_key     = getattr(self.angel, "api_key",   "")
        except Exception as exc:
            logger.warning("WebSocket: could not get feed token: %s", exc)
            time.sleep(10)
            return

        self._ws = SmartWebSocketV2(
            auth_token  = feed_token,
            api_key     = api_key,
            client_code = client_id,
            feed_token  = feed_token,
        )

        # Bind callbacks
        self._ws.on_open    = self._on_open
        self._ws.on_data    = self._on_data
        self._ws.on_error   = self._on_error
        self._ws.on_close   = self._on_close

        logger.info("WebSocket connecting...")
        self._ws.connect()

    def _on_open(self, ws) -> None:
        """Connection established — subscribe to all open positions."""
        logger.info("WebSocket connection opened")
        self._reconnect_count = 0
        # Re-subscribe to any tokens we had before reconnect
        if self._subscribed_tokens:
            self._do_subscribe(list(self._subscribed_tokens))

    def _on_error(self, ws, error) -> None:
        logger.warning("WebSocket error: %s", error)

    def _on_close(self, ws) -> None:
        logger.info("WebSocket connection closed")

    # ── Data handler — hot path ───────────────────────────────────────────────

    def _on_data(self, ws, message) -> None:
        """
        Called on every price tick.
        GA-7: Use JSON path only. SmartWebSocketV2 returns JSON by default.
        Binary format varies by SDK version and is unreliable.
        """
        try:
            if isinstance(message, str):
                data = json.loads(message)
                self._process_json_tick(data)
            elif isinstance(message, (bytes, bytearray)):
                # Try JSON decode first (some SDK versions send JSON as bytes)
                try:
                    data = json.loads(message.decode('utf-8'))
                    self._process_json_tick(data)
                except Exception:
                    # If not decodable as JSON, log and skip (safer than parsing binary)
                    logger.debug('WS binary message skipped (len=%d)', len(message))
            elif isinstance(message, dict):
                self._process_json_tick(message)
        except Exception as exc:
            logger.debug("_on_data error: %s", exc)

    def _process_json_tick(self, data: Dict) -> None:
        """Process JSON tick format."""
        token = str(data.get("token", data.get("symbolToken", "")))
        ltp   = float(data.get("ltp", data.get("last_traded_price", 0)) or 0)
        if ltp <= 0 or not token:
            return
        symbol = self._token_symbol_map.get(token)
        if symbol:
            self.ltp_cache.set(symbol, ltp)
            self._check_trailing_stop(symbol, ltp)

    def _process_binary_tick(self, data: bytes) -> None:
        """
        Angel One binary WebSocket format:
        Byte 0:    subscription mode
        Bytes 1-4: token (int32)
        Byte 5:    exchange type
        Bytes 6+:  varies by mode
        For mode=1 (LTP): bytes 11-14 = last traded price (int32, paise)
        """
        try:
            if len(data) < 12:
                return
            import struct
            token    = str(struct.unpack(">I", data[1:5])[0])
            ltp_raw  = struct.unpack(">I", data[8:12])[0]
            ltp      = ltp_raw / 100.0   # paise → rupees
            if ltp <= 0:
                return
            symbol   = self._token_symbol_map.get(token)
            if symbol:
                self.ltp_cache.set(symbol, ltp)
                self._check_trailing_stop(symbol, ltp)
        except Exception:
            pass

    # ── Trailing stop check on tick ──────────────────────────────────────────

    def _check_trailing_stop(self, symbol: str, ltp: float) -> None:
        """
        Check trailing stop for all open positions with this symbol.
        Called on every tick — must be fast and never raise.
        """
        if not self.trade_manager or not self.trailing:
            return

        try:
            for trade_id, trade in list(self.trade_manager.open_trades.items()):
                if trade.symbol != symbol:
                    continue
                if trade.status != "OPEN":
                    continue

                pos = self.trailing.positions.get(trade_id)
                if pos is None:
                    continue

                try:
                    exit_now, exit_price, exit_qty, reason = self.trailing.check_exit(
                        trade_id=trade_id,
                        current_price=ltp,
                        bar_index=int(time.time() // 300),
                    )
                    if exit_now:
                        logger.info(
                            "WebSocket trailing stop hit | trade_id=%s symbol=%s "
                            "ltp=%.2f stop=%.2f reason=%s",
                            trade_id, symbol, ltp,
                            pos.get("stop_price", 0), reason,
                        )
                        # Cancel broker SL order (will be redundant but safe)
                        self.trade_manager._cancel_broker_sl_order(trade)
                        # Close via trade_manager
                        self.trade_manager._close_trade_internal(
                            trade_id   = trade_id,
                            exit_price = ltp,
                            exit_reason = f"ws_trail_{reason}",
                        )
                    else:
                        # GA-3: update broker SL-M order when trail improves
                        new_stop = pos.get("stop_price")
                        if new_stop and abs(new_stop - float(trade.stop_loss or 0)) > 0.50:
                            if trade.sl_order_id:
                                self.trade_manager._update_broker_sl_order(
                                    trade, new_stop
                                )
                            trade.stop_loss = new_stop
                            logger.debug(
                                'WS trail improved | trade_id=%s new_stop=%.2f',
                                trade_id, new_stop,
                            )

                except Exception as exc:
                    logger.debug(
                        "WebSocket trailing check error trade_id=%s: %s",
                        trade_id, exc,
                    )

            # Update live_status.json with unrealized P&L
            self._update_live_pnl()

        except Exception as exc:
            logger.debug("_check_trailing_stop outer error: %s", exc)

    def _update_live_pnl(self) -> None:
        """Write current unrealized P&L to live_status.json every tick."""
        try:
            if not self.trade_manager:
                return
            all_ltps   = self.ltp_cache.all_ltps()
            unrealized = 0.0
            pos_detail = []

            for trade_id, trade in self.trade_manager.open_trades.items():
                ltp = all_ltps.get(trade.symbol)
                if ltp and trade.entry_price:
                    if trade.side == "BUY":
                        unr = (ltp - float(trade.entry_price)) * int(trade.qty)
                    else:
                        unr = (float(trade.entry_price) - ltp) * int(trade.qty)
                    unrealized += unr
                    pos_detail.append({
                        "trade_id":     trade_id,
                        "symbol":       trade.symbol,
                        "side":         trade.side,
                        "ltp":          round(ltp, 2),
                        "entry":        float(trade.entry_price),
                        "unrealized":   round(unr, 2),
                        "stop_loss":    trade.stop_loss,
                        "sl_order_id":  trade.sl_order_id,
                    })

            if not self.live_status_file.exists():
                return
            try:
                status = json.loads(self.live_status_file.read_text())
            except Exception:
                return

            status["unrealized_pnl"]  = round(unrealized, 2)
            status["open_positions"]  = pos_detail
            status["ws_connected"]    = True
            status["ws_last_tick"]    = datetime.now().isoformat()

            tmp = self.live_status_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(status, indent=2, default=str))
            tmp.replace(self.live_status_file)

        except Exception:
            pass

    # ── Subscription management ──────────────────────────────────────────────

    def subscribe(self, symbols: List[str], exchange: str = "NFO") -> None:
        """Subscribe to LTP stream for given symbols."""
        tokens = []
        for sym in symbols:
            token = self._get_token(sym, exchange)
            if token and token not in self._subscribed_tokens:
                tokens.append(token)
                self._subscribed_tokens.add(token)

        if tokens and self._ws:
            self._do_subscribe(tokens, exchange)

    def unsubscribe(self, symbols: List[str], exchange: str = "NFO") -> None:
        """Unsubscribe from symbols (e.g. after position closed)."""
        tokens = [self._symbol_token_map.get(s) for s in symbols]
        tokens = [t for t in tokens if t]
        if tokens and self._ws:
            try:
                token_list = [{"exchangeType": WS_EXCHANGE_NFO
                               if exchange == "NFO" else WS_EXCHANGE_NSE,
                               "tokens": [t]} for t in tokens]
                self._ws.unsubscribe(WS_SUBSCRIBE_MODE, token_list)
            except Exception as exc:
                logger.debug("unsubscribe error: %s", exc)
        for sym in symbols:
            token = self._symbol_token_map.pop(sym, None)
            if token:
                self._token_symbol_map.pop(token, None)
                self._subscribed_tokens.discard(token)

    def _do_subscribe(self, tokens: List[str], exchange: str = "NFO") -> None:
        """Actually send subscribe request to WebSocket."""
        try:
            exc_type = WS_EXCHANGE_NFO if exchange == "NFO" else WS_EXCHANGE_NSE
            token_list = [{"exchangeType": exc_type, "tokens": tokens}]
            self._ws.subscribe(
                correlation_id = "nifty_positions",
                mode           = WS_SUBSCRIBE_MODE,
                token_list     = token_list,
            )
            logger.info("WebSocket subscribed to %d tokens", len(tokens))
        except Exception as exc:
            logger.warning("WebSocket subscribe error: %s", exc)

    def subscribe_open_positions(self) -> None:
        """Subscribe to all currently open positions. Call after market open."""
        if not self.trade_manager:
            return
        symbols = [
            trade.symbol
            for trade in self.trade_manager.open_trades.values()
            if trade.status == "OPEN"
        ]
        if symbols:
            self.subscribe(symbols)
            logger.info("WebSocket auto-subscribed to %d open positions", len(symbols))

    def get_ltp(self, symbol: str) -> Optional[float]:
        """Get latest LTP from cache (sub-millisecond, thread-safe)."""
        return self.ltp_cache.get(symbol)

    def is_connected(self) -> bool:
        return self._ws is not None and self._running

    def status(self) -> Dict[str, Any]:
        return {
            "connected":          self.is_connected(),
            "subscribed_symbols": len(self._subscribed_tokens),
            "reconnect_count":    self._reconnect_count,
            "ws_available":       self._ws_available,
            "cached_symbols":     list(self._token_symbol_map.values()),
        }
