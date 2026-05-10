"""
brokers/base.py — Abstract broker interface for Syntrix.

Defines the contract that all broker adapters must implement.
Supports multiple brokers (IQ Option initially, extensible to others).
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional

logger = logging.getLogger("syntrix.brokers")


class BrokerStatus(Enum):
    """Connection status of a broker."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    ERROR = "error"


class TradeDirection(Enum):
    """Trade direction."""

    CALL = "call"
    PUT = "put"


@dataclass
class Candle:
    """OHLCV candle data."""

    timestamp: float
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def body_size(self) -> float:
        return abs(self.close - self.open)

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open

    @property
    def range(self) -> float:
        return self.high - self.low


@dataclass
class TradeRequest:
    """Request to execute a trade."""

    asset: str
    direction: TradeDirection
    amount: float
    duration: int  # seconds
    trade_id: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class TradeResult:
    """Result of an executed trade."""

    trade_id: str
    success: bool
    result: str = ""  # "win" / "loss" / "tie" / "error"
    profit: float = 0.0
    broker_id: str = ""
    latency_ms: float = 0.0
    error: str = ""
    raw_response: Optional[Dict[str, Any]] = None


@dataclass
class HealthStatus:
    """Broker health status snapshot."""

    connected: bool = False
    latency_ms: float = 0.0
    last_heartbeat: float = 0.0
    reconnect_count: int = 0
    errors_last_hour: int = 0
    uptime_seconds: float = 0.0
    websocket_alive: bool = False


class BaseBroker(ABC):
    """
    Abstract base broker interface.

    All broker adapters must implement this contract.
    """

    def __init__(self, name: str = "base") -> None:
        self.name = name
        self._status = BrokerStatus.DISCONNECTED
        self._connected_at: float = 0.0
        self._reconnect_count: int = 0
        self._last_heartbeat: float = 0.0
        self._latency_samples: List[float] = []

    @property
    def status(self) -> BrokerStatus:
        return self._status

    @property
    def is_connected(self) -> bool:
        return self._status == BrokerStatus.CONNECTED

    @property
    def avg_latency_ms(self) -> float:
        if not self._latency_samples:
            return 0.0
        return sum(self._latency_samples) / len(self._latency_samples)

    def _record_latency(self, latency_ms: float) -> None:
        self._latency_samples.append(latency_ms)
        if len(self._latency_samples) > 100:
            self._latency_samples = self._latency_samples[-50:]

    @abstractmethod
    def connect(self) -> bool:
        """Connect to the broker. Returns True on success."""
        ...

    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect from the broker."""
        ...

    @abstractmethod
    def reconnect(self) -> bool:
        """Attempt to reconnect. Returns True on success."""
        ...

    @abstractmethod
    def get_balance(self) -> float:
        """Get current account balance."""
        ...

    @abstractmethod
    def get_candles(self, asset: str, timeframe: int, count: int) -> List[Candle]:
        """
        Get historical candles.

        Args:
            asset: Asset identifier (e.g., "EURUSD-OTC")
            timeframe: Candle timeframe in seconds (e.g., 60)
            count: Number of candles to retrieve
        """
        ...

    @abstractmethod
    def get_payout(self, asset: str) -> float:
        """Get current payout percentage for an asset (0.0 - 1.0)."""
        ...

    @abstractmethod
    def is_asset_open(self, asset: str) -> bool:
        """Check if an asset is currently available for trading."""
        ...

    @abstractmethod
    def execute_trade(self, request: TradeRequest) -> TradeResult:
        """Execute a trade order."""
        ...

    @abstractmethod
    def check_trade_result(self, broker_id: str) -> TradeResult:
        """Check the result of a previously executed trade."""
        ...

    def get_all_open_assets(self) -> List[str]:
        """Get all currently open/tradeable assets. Override in adapter."""
        return []

    def health_check(self) -> HealthStatus:
        """Get broker health status."""
        return HealthStatus(
            connected=self.is_connected,
            latency_ms=self.avg_latency_ms,
            last_heartbeat=self._last_heartbeat,
            reconnect_count=self._reconnect_count,
            uptime_seconds=time.time() - self._connected_at if self._connected_at else 0,
        )
