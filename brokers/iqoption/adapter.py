"""
brokers/iqoption/adapter.py — IQ Option broker adapter for Syntrix.

Implements BaseBroker using iqoptionapi with:
- Automatic reconnection with exponential backoff
- Heartbeat monitoring
- Latency tracking
- WebSocket health monitoring
- Timeout handling
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from brokers.base import (
    BaseBroker,
    BrokerStatus,
    Candle,
    HealthStatus,
    TradeDirection,
    TradeRequest,
    TradeResult,
)

logger = logging.getLogger("syntrix.brokers.iqoption")


class IQOptionAdapter(BaseBroker):
    """
    IQ Option broker adapter.

    Uses iqoptionapi for connectivity. Falls back gracefully
    if the library is not available.
    """

    MAX_RECONNECT_ATTEMPTS = 5
    RECONNECT_BASE_DELAY = 2.0
    HEARTBEAT_INTERVAL = 30.0
    TRADE_TIMEOUT = 30.0
    RESULT_POLL_INTERVAL = 1.0
    RESULT_POLL_TIMEOUT = 120.0

    def __init__(self, email: str = "", password: str = "", practice: bool = True) -> None:
        super().__init__(name="iqoption")
        self._email = email
        self._password = password
        self._practice = practice
        self._api: Any = None
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._heartbeat_active = False
        self._ws_alive = False
        self._errors: List[float] = []

    def connect(self) -> bool:
        """Connect to IQ Option API."""
        self._status = BrokerStatus.CONNECTING
        try:
            from iq_api import get_iq_option_class

            IQ_Option, error = get_iq_option_class()
            if IQ_Option is None:
                logger.warning("iqbroker/iqoptionapi not available: %s", error)
                self._status = BrokerStatus.ERROR
                return False

            self._api = IQ_Option(self._email, self._password)
            check, reason = self._api.connect()

            if check:
                self._status = BrokerStatus.CONNECTED
                self._connected_at = time.time()
                self._ws_alive = True
                if self._practice:
                    self._api.change_balance("PRACTICE")
                else:
                    self._api.change_balance("REAL")
                self._start_heartbeat()
                logger.info("Connected to IQ Option (practice=%s)", self._practice)
                return True
            else:
                self._status = BrokerStatus.ERROR
                self._record_error()
                logger.error("IQ Option connection failed: %s", reason)
                return False
        except Exception as exc:
            self._status = BrokerStatus.ERROR
            self._record_error()
            logger.error("IQ Option connection error: %s", exc)
            return False

    def disconnect(self) -> None:
        """Disconnect from IQ Option."""
        self._heartbeat_active = False
        if self._api:
            try:
                self._api.disconnect()
            except Exception as exc:
                logger.warning("Error during disconnect: %s", exc)
        self._status = BrokerStatus.DISCONNECTED
        self._ws_alive = False
        logger.info("Disconnected from IQ Option")

    def reconnect(self) -> bool:
        """Reconnect with exponential backoff."""
        self._status = BrokerStatus.RECONNECTING
        self._heartbeat_active = False

        for attempt in range(1, self.MAX_RECONNECT_ATTEMPTS + 1):
            delay = self.RECONNECT_BASE_DELAY * (2 ** (attempt - 1))
            logger.info("Reconnect attempt %d/%d (delay=%.1fs)", attempt, self.MAX_RECONNECT_ATTEMPTS, delay)
            time.sleep(delay)

            self.disconnect()
            if self.connect():
                self._reconnect_count += 1
                logger.info("Reconnected successfully (total reconnects: %d)", self._reconnect_count)
                return True

        self._status = BrokerStatus.ERROR
        logger.error("Failed to reconnect after %d attempts", self.MAX_RECONNECT_ATTEMPTS)
        return False

    def get_balance(self) -> float:
        """Get current account balance."""
        if not self._api or not self.is_connected:
            return 0.0
        try:
            start = time.time()
            balance = self._api.get_balance()
            self._record_latency((time.time() - start) * 1000)
            return float(balance)
        except Exception as exc:
            logger.error("Error getting balance: %s", exc)
            self._record_error()
            return 0.0

    def get_candles(self, asset: str, timeframe: int, count: int) -> List[Candle]:
        """Get historical candles from IQ Option."""
        if not self._api or not self.is_connected:
            return []
        try:
            start = time.time()
            raw = self._api.get_candles(asset, timeframe, count, time.time())
            self._record_latency((time.time() - start) * 1000)

            candles: List[Candle] = []
            for c in raw:
                candles.append(
                    Candle(
                        timestamp=c.get("from", 0),
                        open=c.get("open", 0),
                        high=c.get("max", 0),
                        low=c.get("min", 0),
                        close=c.get("close", 0),
                        volume=c.get("volume", 0),
                    )
                )
            return candles
        except Exception as exc:
            logger.error("Error getting candles for %s: %s", asset, exc)
            self._record_error()
            return []

    def get_payout(self, asset: str) -> float:
        """Get current payout for asset (0.0 - 1.0)."""
        if not self._api or not self.is_connected:
            return 0.0
        try:
            start = time.time()
            all_data = self._api.get_all_profit()
            self._record_latency((time.time() - start) * 1000)

            if asset in all_data:
                return float(all_data[asset].get("turbo", 0)) / 100.0
            return 0.0
        except Exception as exc:
            logger.error("Error getting payout for %s: %s", asset, exc)
            self._record_error()
            return 0.0

    def is_asset_open(self, asset: str) -> bool:
        """Check if asset is open for trading."""
        if not self._api or not self.is_connected:
            return False
        try:
            start = time.time()
            data = self._api.get_all_open_time()
            self._record_latency((time.time() - start) * 1000)

            for mode in ["turbo", "binary"]:
                if asset in data.get(mode, {}):
                    if data[mode][asset].get("open"):
                        return True
            return False
        except Exception as exc:
            logger.error("Error checking asset %s: %s", asset, exc)
            self._record_error()
            return False

    def execute_trade(self, request: TradeRequest) -> TradeResult:
        """Execute a binary options trade on IQ Option."""
        trade_id = request.trade_id or str(uuid.uuid4())[:8]

        if not self._api or not self.is_connected:
            return TradeResult(
                trade_id=trade_id, success=False, result="error", error="Not connected"
            )

        try:
            direction = "call" if request.direction == TradeDirection.CALL else "put"
            duration_min = max(1, request.duration // 60)

            start = time.time()
            check, order_id = self._api.buy(
                request.amount, request.asset, direction, duration_min
            )
            latency = (time.time() - start) * 1000
            self._record_latency(latency)

            if check:
                logger.info(
                    "Trade executed: %s %s %s $%.2f (id=%s, latency=%.0fms)",
                    direction,
                    request.asset,
                    trade_id,
                    request.amount,
                    order_id,
                    latency,
                )
                return TradeResult(
                    trade_id=trade_id,
                    success=True,
                    result="pending",
                    broker_id=str(order_id),
                    latency_ms=latency,
                )
            else:
                self._record_error()
                return TradeResult(
                    trade_id=trade_id,
                    success=False,
                    result="error",
                    error="Order rejected by broker",
                    latency_ms=latency,
                )
        except Exception as exc:
            self._record_error()
            logger.error("Trade execution error: %s", exc)
            return TradeResult(
                trade_id=trade_id,
                success=False,
                result="error",
                error=str(exc),
            )

    def check_trade_result(self, broker_id: str) -> TradeResult:
        """Poll IQ Option for trade result."""
        if not self._api or not self.is_connected:
            return TradeResult(
                trade_id="", success=False, result="error", error="Not connected"
            )

        try:
            start = time.time()
            while time.time() - start < self.RESULT_POLL_TIMEOUT:
                result = self._api.check_win_v4(int(broker_id))
                if result is not None:
                    profit = float(result)
                    outcome = "win" if profit > 0 else ("tie" if profit == 0 else "loss")
                    return TradeResult(
                        trade_id="",
                        success=True,
                        result=outcome,
                        profit=profit,
                        broker_id=broker_id,
                    )
                time.sleep(self.RESULT_POLL_INTERVAL)

            return TradeResult(
                trade_id="",
                success=False,
                result="error",
                error="Result poll timeout",
                broker_id=broker_id,
            )
        except Exception as exc:
            logger.error("Error checking trade result: %s", exc)
            return TradeResult(
                trade_id="", success=False, result="error", error=str(exc), broker_id=broker_id
            )

    def health_check(self) -> HealthStatus:
        """Extended health check for IQ Option."""
        base = super().health_check()
        base.websocket_alive = self._ws_alive
        now = time.time()
        cutoff = now - 3600
        base.errors_last_hour = sum(1 for t in self._errors if t > cutoff)
        return base

    def _start_heartbeat(self) -> None:
        """Start background heartbeat monitoring."""
        self._heartbeat_active = True
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, name="iq-heartbeat", daemon=True
        )
        self._heartbeat_thread.start()

    def _heartbeat_loop(self) -> None:
        """Periodically check broker connectivity."""
        while self._heartbeat_active:
            try:
                if self._api and self.is_connected:
                    start = time.time()
                    self._api.get_balance()
                    latency = (time.time() - start) * 1000
                    self._record_latency(latency)
                    self._last_heartbeat = time.time()
                    self._ws_alive = True
                else:
                    self._ws_alive = False
            except Exception as exc:
                self._ws_alive = False
                logger.warning("Heartbeat failed: %s", exc)
                self._record_error()
            time.sleep(self.HEARTBEAT_INTERVAL)

    def _record_error(self) -> None:
        self._errors.append(time.time())
        if len(self._errors) > 500:
            self._errors = self._errors[-250:]
