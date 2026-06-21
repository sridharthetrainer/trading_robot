"""
websocket_tracker.py — Real-time position tracking via Angel SmartWebSocketV2

After a signal generates a trade:
- Subscribes to real-time tick data (every 200ms)
- Trailing stop loss — moves SL up as price moves
- Partial profit booking — 50% at T1, trail rest
- Break-even SL — after +1% move
- Instant exit on VIX spike or drawdown breach
- Live P&L updated every tick

Usage:
    from websocket_tracker import WebSocketTracker
    tracker = WebSocketTracker(angel_obj)
    tracker.start()
    tracker.add_position(symbol, entry_price, sl, target, qty, side)
"""
from __future__ import annotations
import logging
import threading
import time
import os
import json
from datetime import datetime
from typing import Dict, Optional, List, Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class TrackedPosition:
    """A position being monitored in real-time."""
    symbol: str
    token: str
    exchange: str
    entry_price: float
    stop_loss: float
    target_1: float
    target_2: float = 0.0
    qty: int = 1
    side: str = "BUY"  # BUY or SELL
    
    # Dynamic state
    current_price: float = 0.0
    highest_since_entry: float = 0.0
    lowest_since_entry: float = 999999.0
    trailing_sl: float = 0.0
    breakeven_activated: bool = False
    t1_hit: bool = False
    t1_qty_exited: int = 0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    
    # Timestamps
    entry_time: str = ""
    last_tick_time: str = ""
    
    # Config
    trail_pct: float = 0.5          # trail SL by 0.5% from high
    breakeven_trigger_pct: float = 1.0  # move SL to entry after +1%
    partial_exit_pct: float = 50.0  # exit 50% at T1
    max_loss_pct: float = 3.0       # emergency exit at -3%
    max_hold_minutes: int = 360     # exit after 6 hours (full day)


class WebSocketTracker:
    """Real-time position tracker using Angel SmartWebSocketV2."""
    
    def __init__(self, angel=None, alerts=None):
        self.angel = angel
        self.alerts = alerts
        self._positions: Dict[str, TrackedPosition] = {}
        self._ws = None
        self._running = False
        self._lock = threading.Lock()
        self._callbacks: List[Callable] = []
        
        # VIX monitoring
        self._vix_token = "99926019"  # India VIX token on Angel
        self._current_vix = 0.0
        self._vix_threshold = 22.0  # alert if VIX > 22
        
        # Stats
        self._tick_count = 0
        self._last_tick_time = 0.0
        self._reconnect_count = 0
    
    def start(self) -> bool:
        """Start websocket connection and begin tracking."""
        if self._running:
            return True
        
        if not self.angel or not self.angel.obj:
            logger.error("WebSocket: Angel not connected")
            return False
        
        try:
            from SmartApi.smartWebSocketV2 import SmartWebSocketV2
            
            auth_token = self.angel.obj.access_token
            feed_token = self.angel.obj.feed_token
            api_key = os.getenv("API_KEY", "")
            client_code = os.getenv("CLIENT_ID", "")
            
            if not all([auth_token, feed_token, api_key, client_code]):
                logger.error("WebSocket: missing auth tokens")
                return False
            
            self._ws = SmartWebSocketV2(
                auth_token, api_key, client_code, feed_token
            )
            
            # Set callbacks
            self._ws.on_data = self._on_tick
            self._ws.on_open = self._on_open
            self._ws.on_error = self._on_error
            self._ws.on_close = self._on_close
            
            # Start in background thread
            self._running = True
            t = threading.Thread(target=self._ws_thread, daemon=True, name="ws-tracker")
            t.start()
            
            logger.info("WebSocket tracker started")
            return True
        except ImportError:
            logger.warning("SmartWebSocketV2 not installed — pip install smartapi-python")
            return False
        except Exception as e:
            logger.error("WebSocket start failed: %s", e)
            return False
    
    def _ws_thread(self):
        """Websocket connection thread with auto-reconnect."""
        while self._running:
            try:
                self._ws.connect()
            except Exception as e:
                logger.warning("WebSocket disconnected: %s — reconnecting in 5s", e)
                self._reconnect_count += 1
                time.sleep(5)
                
                # Re-create websocket after disconnect
                if self._running:
                    try:
                        from SmartApi.smartWebSocketV2 import SmartWebSocketV2
                        auth_token = self.angel.obj.access_token
                        feed_token = self.angel.obj.feed_token
                        api_key = os.getenv("API_KEY", "")
                        client_code = os.getenv("CLIENT_ID", "")
                        self._ws = SmartWebSocketV2(
                            auth_token, api_key, client_code, feed_token
                        )
                        self._ws.on_data = self._on_tick
                        self._ws.on_open = self._on_open
                        self._ws.on_error = self._on_error
                        self._ws.on_close = self._on_close
                    except Exception:
                        pass
    
    def _on_open(self, wsapp):
        """Called when websocket connects — subscribe to all tracked symbols."""
        logger.info("WebSocket connected — subscribing to %d positions", len(self._positions))
        self._subscribe_all()
    
    def _on_error(self, wsapp, error):
        logger.warning("WebSocket error: %s", error)
    
    def _on_close(self, wsapp, close_status, close_msg):
        logger.info("WebSocket closed: %s", close_msg)
    
    def _on_tick(self, wsapp, tick_data):
        """Process every incoming tick — the core real-time engine."""
        try:
            self._tick_count += 1
            self._last_tick_time = time.time()
            
            if not isinstance(tick_data, dict):
                return
            
            token = str(tick_data.get("token", ""))
            ltp = float(tick_data.get("last_traded_price", 0) or 0)
            
            if ltp <= 0:
                return
            
            # Angel sends prices × 100 for some instruments
            # Normalize if needed
            if ltp > 100000 and token.startswith("999"):
                ltp = ltp / 100
            
            # VIX tick
            if token == self._vix_token:
                self._current_vix = ltp / 100 if ltp > 100 else ltp
                return
            
            # Find matching position
            with self._lock:
                pos = None
                for sym, p in self._positions.items():
                    if p.token == token:
                        pos = p
                        break
                
                if pos is None:
                    return
                
                # Update position with new tick
                self._process_tick(pos, ltp)
        except Exception as e:
            logger.debug("Tick error: %s", e)
    
    def _process_tick(self, pos: TrackedPosition, ltp: float):
        """Core logic: trailing SL, breakeven, partial exit, emergency exit."""
        pos.current_price = ltp
        pos.last_tick_time = datetime.now().isoformat()
        
        is_long = pos.side == "BUY"
        
        # Update high/low since entry
        if ltp > pos.highest_since_entry:
            pos.highest_since_entry = ltp
        if ltp < pos.lowest_since_entry:
            pos.lowest_since_entry = ltp
        
        # Calculate P&L
        if is_long:
            pos.pnl = (ltp - pos.entry_price) * pos.qty
            pos.pnl_pct = (ltp - pos.entry_price) / pos.entry_price * 100
        else:
            pos.pnl = (pos.entry_price - ltp) * pos.qty
            pos.pnl_pct = (pos.entry_price - ltp) / pos.entry_price * 100
        
        # ── 1. Emergency exit: max loss breached ─────────────────────
        if pos.pnl_pct <= -pos.max_loss_pct:
            self._emergency_exit(pos, f"Max loss {pos.max_loss_pct}% breached")
            return
        
        # ── 2. News sentiment exit ───────────────────────────────────
        try:
            from market_intelligence_hub import get_composite_sentiment
            _sent = get_composite_sentiment()
            if _sent and _sent.get("score", 50) < 20:  # extreme bearish
                if is_long:  # news_exit: exit longs on extreme bearish
                    self._exit_position(pos, ltp, f"Extreme bearish sentiment ({_sent['score']}/100)")
                    return
        except Exception: pass

        # ── 3. VIX spike exit (options only) ─────────────────────────
        if self._current_vix > self._vix_threshold:
            if "CE" in pos.symbol or "PE" in pos.symbol:
                self._emergency_exit(pos, f"VIX {self._current_vix:.1f} > {self._vix_threshold}")
                return
        
        # ── 3. Break-even activation ─────────────────────────────────
        if not pos.breakeven_activated:
            if pos.pnl_pct >= pos.breakeven_trigger_pct:
                pos.breakeven_activated = True
                old_sl = pos.trailing_sl or pos.stop_loss
                pos.trailing_sl = pos.entry_price
                logger.info("BREAKEVEN ✅ %s: SL moved to entry ₹%.2f (was ₹%.2f)",
                           pos.symbol, pos.entry_price, old_sl)
                self._send_alert(
                    f"🔒 <b>BREAKEVEN</b> {pos.symbol}\n"
                    f"  SL moved to entry: ₹{pos.entry_price:,.2f}\n"
                    f"  Current: ₹{ltp:,.2f} ({pos.pnl_pct:+.1f}%)"
                )
        
        # ── 4. Trailing stop loss ────────────────────────────────────
        if pos.breakeven_activated:
            if is_long:
                new_trail = pos.highest_since_entry * (1 - pos.trail_pct / 100)
                if new_trail > (pos.trailing_sl or pos.stop_loss):
                    pos.trailing_sl = new_trail
                    logger.debug("TRAIL ▲ %s: SL → ₹%.2f (high=%.2f)", 
                               pos.symbol, new_trail, pos.highest_since_entry)
            else:
                new_trail = pos.lowest_since_entry * (1 + pos.trail_pct / 100)
                if new_trail < (pos.trailing_sl or pos.stop_loss):
                    pos.trailing_sl = new_trail
        
        # ── 5. Check trailing SL hit ─────────────────────────────────
        active_sl = pos.trailing_sl or pos.stop_loss
        if is_long and ltp <= active_sl:
            self._exit_position(pos, ltp, f"Trailing SL hit ₹{active_sl:.2f}")
            return
        elif not is_long and ltp >= active_sl:
            self._exit_position(pos, ltp, f"Trailing SL hit ₹{active_sl:.2f}")
            return
        
        # ── 6. Target 1 hit — partial exit ───────────────────────────
        if not pos.t1_hit and pos.target_1 > 0:
            t1_hit = (is_long and ltp >= pos.target_1) or (not is_long and ltp <= pos.target_1)
            if t1_hit:
                pos.t1_hit = True
                exit_qty = int(pos.qty * pos.partial_exit_pct / 100)
                if exit_qty > 0:
                    self._partial_exit(pos, ltp, exit_qty, "T1")
                    pos.t1_qty_exited = exit_qty
                    pos.qty -= exit_qty
        
        # ── 7. Time-based exit — max hold duration ──────────────────
        if pos.entry_time and pos.max_hold_minutes > 0:
            try:
                entry_dt = datetime.fromisoformat(pos.entry_time)
                hold_min = (datetime.now() - entry_dt).total_seconds() / 60
                if hold_min >= pos.max_hold_minutes:
                    self._exit_position(pos, ltp, f"Max hold {pos.max_hold_minutes}min exceeded")
                    return
            except Exception: pass

        # ── 8. Target 2 hit — full exit ──────────────────────────────
        if pos.target_2 > 0:
            t2_hit = (is_long and ltp >= pos.target_2) or (not is_long and ltp <= pos.target_2)
            if t2_hit:
                self._exit_position(pos, ltp, "Target 2 hit")
                return
    
    def add_position(
        self,
        symbol: str,
        entry_price: float,
        stop_loss: float,
        target: float,
        qty: int = 1,
        side: str = "BUY",
        target_2: float = 0.0,
        trail_pct: float = 0.5,
    ) -> bool:
        """Add a position to track in real-time."""
        try:
            # Get Angel token for this symbol
            token = ""
            exchange = "NSE"
            if self.angel:
                token = str(self.angel.get_token(symbol) or "")
                if "CE" in symbol or "PE" in symbol:
                    exchange = "NFO"
            
            if not token:
                logger.warning("WebSocket: no token for %s — using poll-based tracking", symbol)
                return False
            
            # Calculate T2 if not given
            if target_2 <= 0 and target > 0 and entry_price > 0:
                move = abs(target - entry_price)
                target_2 = entry_price + (move * 1.5 if side == "BUY" else -move * 1.5)
            
            pos = TrackedPosition(
                symbol=symbol,
                token=token,
                exchange=exchange,
                entry_price=entry_price,
                stop_loss=stop_loss,
                target_1=target,
                target_2=target_2,
                qty=qty,
                side=side,
                trailing_sl=stop_loss,
                entry_time=datetime.now().isoformat(),
                trail_pct=trail_pct,
            )
            
            with self._lock:
                self._positions[symbol] = pos
            
            # Subscribe to ticks
            self._subscribe_symbol(token, exchange)
            
            logger.info("TRACKING ✅ %s: entry=₹%.2f SL=₹%.2f T1=₹%.2f T2=₹%.2f qty=%d",
                        symbol, entry_price, stop_loss, target, target_2, qty)
            
            self._send_alert(
                f"👁 <b>TRACKING STARTED</b> {symbol}\n"
                f"  Entry: ₹{entry_price:,.2f}\n"
                f"  SL: ₹{stop_loss:,.2f}  T1: ₹{target:,.2f}\n"
                f"  Qty: {qty}  Side: {side}\n"
                f"  Trail: {trail_pct}%  Breakeven: +{pos.breakeven_trigger_pct}%"
            )
            return True
        except Exception as e:
            logger.error("Add position failed: %s", e)
            return False
    
    def remove_position(self, symbol: str):
        """Stop tracking a position."""
        with self._lock:
            if symbol in self._positions:
                del self._positions[symbol]
                logger.info("Stopped tracking %s", symbol)
    
    def get_live_pnl(self) -> Dict[str, dict]:
        """Get live P&L for all tracked positions."""
        result = {}
        with self._lock:
            for sym, pos in self._positions.items():
                result[sym] = {
                    "entry": pos.entry_price,
                    "current": pos.current_price,
                    "pnl": pos.pnl,
                    "pnl_pct": pos.pnl_pct,
                    "sl": pos.trailing_sl or pos.stop_loss,
                    "target": pos.target_1,
                    "breakeven": pos.breakeven_activated,
                    "t1_hit": pos.t1_hit,
                    "side": pos.side,
                    "qty": pos.qty,
                    "high": pos.highest_since_entry,
                    "low": pos.lowest_since_entry,
                }
        return result
    
    def get_stats(self) -> dict:
        """Get websocket statistics for /health."""
        return {
            "running": self._running,
            "positions": len(self._positions),
            "ticks": self._tick_count,
            "vix": self._current_vix,
            "reconnects": self._reconnect_count,
            "last_tick": self._last_tick_time,
        }
    
    def _subscribe_all(self):
        """Subscribe to all tracked symbols + VIX."""
        tokens = []
        with self._lock:
            for sym, pos in self._positions.items():
                if pos.token:
                    tokens.append({
                        "exchangeType": "NSE" if pos.exchange == "NSE" else "NFO",
                        "tokens": [pos.token],
                    })
        
        # Always subscribe to India VIX
        tokens.append({"exchangeType": "NSE", "tokens": [self._vix_token]})
        
        if tokens and self._ws:
            try:
                # Mode 2 = LTP only, Mode 3 = full quote
                self._ws.subscribe("abc123", 2, tokens)
                logger.info("Subscribed to %d tokens", len(tokens))
            except Exception as e:
                logger.warning("Subscribe failed: %s", e)
    
    def _subscribe_symbol(self, token: str, exchange: str):
        """Subscribe to a single new symbol."""
        if self._ws and self._running:
            try:
                self._ws.subscribe("abc123", 2, [
                    {"exchangeType": exchange, "tokens": [token]}
                ])
            except Exception as e:
                logger.debug("Subscribe %s failed: %s", token, e)
    
    def _exit_position(self, pos: TrackedPosition, price: float, reason: str):
        """Full exit of a position."""
        logger.info("EXIT %s at ₹%.2f — %s (P&L: ₹%.2f / %.1f%%)",
                    pos.symbol, price, reason, pos.pnl, pos.pnl_pct)
        
        # Place actual exit order if not paper mode
        try:
            if self.angel and not getattr(self.angel, 'block_real_orders', False):
                exit_side = "SELL" if pos.side == "BUY" else "BUY"
                self.angel.place_order(
                    symbol=pos.symbol,
                    side=exit_side,
                    qty=pos.qty,
                    order_type="MARKET",
                    exchange=pos.exchange,
                )
                logger.info("Exit order placed: %s %s %d", exit_side, pos.symbol, pos.qty)
        except Exception as e:
            logger.error("Exit order failed: %s", e)
        
        self._send_alert(
            f"{'🟢' if pos.pnl > 0 else '🔴'} <b>EXIT {pos.symbol}</b>\n"
            f"  Reason: {reason}\n"
            f"  Entry: ₹{pos.entry_price:,.2f} → Exit: ₹{price:,.2f}\n"
            f"  P&L: ₹{pos.pnl:+,.2f} ({pos.pnl_pct:+.1f}%)\n"
            f"  Duration: {self._duration(pos.entry_time)}"
        )
        
        self.remove_position(pos.symbol)
    
    def _partial_exit(self, pos: TrackedPosition, price: float, qty: int, label: str):
        """Partial exit — book profits on portion."""
        partial_pnl = (price - pos.entry_price) * qty if pos.side == "BUY" else (pos.entry_price - price) * qty
        
        try:
            if self.angel and not getattr(self.angel, 'block_real_orders', False):
                exit_side = "SELL" if pos.side == "BUY" else "BUY"
                self.angel.place_order(
                    symbol=pos.symbol,
                    side=exit_side,
                    qty=qty,
                    order_type="MARKET",
                    exchange=pos.exchange,
                )
        except Exception as e:
            logger.error("Partial exit order failed: %s", e)
        
        self._send_alert(
            f"💰 <b>PARTIAL EXIT {pos.symbol}</b> ({label})\n"
            f"  Exited {qty} of {pos.qty + qty} @ ₹{price:,.2f}\n"
            f"  Booked: ₹{partial_pnl:+,.2f}\n"
            f"  Remaining: {pos.qty} — trailing SL active"
        )
    
    def _emergency_exit(self, pos: TrackedPosition, reason: str):
        """Emergency exit — max loss or VIX spike."""
        self._exit_position(pos, pos.current_price, f"🚨 EMERGENCY: {reason}")
    
    def _send_alert(self, text: str):
        """Send alert via Telegram."""
        if self.alerts:
            try:
                self.alerts.send(text)
            except Exception:
                pass
    
    def _duration(self, entry_time: str) -> str:
        """Calculate duration since entry."""
        try:
            start = datetime.fromisoformat(entry_time)
            delta = datetime.now() - start
            mins = int(delta.total_seconds() / 60)
            if mins < 60:
                return f"{mins}m"
            return f"{mins // 60}h {mins % 60}m"
        except Exception:
            return "?"
    
    def stop(self):
        """Stop websocket and all tracking."""
        self._running = False
        if self._ws:
            try:
                self._ws.close_connection()
            except Exception:
                pass
        logger.info("WebSocket tracker stopped")
