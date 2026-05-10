"""
execution/position_lock.py — Single Position Lock Manager for Syntrix v2.0.

Ensures only ONE position is open at any time.

States:
- NO_POSITION: ready to execute
- EXECUTING: sending order
- OPEN: position active
- CLOSING: position expiring
- COOLDOWN: post-trade cooldown

Thread-safe via threading.Lock.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger("syntrix.position_lock")


class PositionState(Enum):
    """Position lifecycle states."""

    NO_POSITION = "no_position"
    EXECUTING = "executing"
    OPEN = "open"
    CLOSING = "closing"
    COOLDOWN = "cooldown"


@dataclass
class PositionInfo:
    """Information about the current position."""

    asset: str = ""
    direction: str = ""
    strategy: str = ""
    amount: float = 0.0
    entry_time: float = 0.0
    duration_sec: float = 60.0
    payout: float = 0.0
    order_id: str = ""
    meta_score: float = 0.0
    correlation_id: str = ""


class PositionLockManager:
    """
    Thread-safe single position lock.

    Prevents:
    - Multiple simultaneous orders
    - Double execution
    - Race conditions
    - Overtrading
    """

    def __init__(
        self,
        cooldown_sec: float = 30.0,
        max_execution_time_sec: float = 15.0,
        max_position_time_sec: float = 300.0,
    ) -> None:
        self._lock = threading.Lock()
        self._state = PositionState.NO_POSITION
        self._current_position: Optional[PositionInfo] = None
        self._state_entered_at = time.time()
        self._cooldown_sec = cooldown_sec
        self._max_execution_time = max_execution_time_sec
        self._max_position_time = max_position_time_sec
        self._total_trades = 0
        self._total_blocked = 0

    @property
    def state(self) -> PositionState:
        self._check_timeouts()
        return self._state

    @property
    def is_locked(self) -> bool:
        """Check if position lock is active (cannot trade)."""
        self._check_timeouts()
        return self._state != PositionState.NO_POSITION

    @property
    def current_position(self) -> Optional[PositionInfo]:
        return self._current_position

    def try_acquire(self, position: PositionInfo) -> bool:
        """
        Try to acquire the position lock for a new trade.

        Returns True if lock acquired, False if locked.
        Thread-safe.
        """
        with self._lock:
            self._check_timeouts()

            if self._state != PositionState.NO_POSITION:
                self._total_blocked += 1
                logger.debug(
                    "Position lock blocked: state=%s, asset=%s",
                    self._state.value,
                    self._current_position.asset if self._current_position else "none",
                )
                return False

            self._state = PositionState.EXECUTING
            self._current_position = position
            self._state_entered_at = time.time()
            self._total_trades += 1

            logger.info(
                "Position lock acquired: %s %s (%s)",
                position.asset, position.direction, position.strategy,
            )
            return True

    def confirm_open(self, order_id: str = "") -> None:
        """Confirm that the order was filled and position is open."""
        with self._lock:
            if self._state == PositionState.EXECUTING:
                self._state = PositionState.OPEN
                self._state_entered_at = time.time()
                if self._current_position:
                    self._current_position.order_id = order_id
                logger.info("Position confirmed open: %s", order_id)

    def close_position(self, result: str = "") -> None:
        """Close the current position and enter cooldown."""
        with self._lock:
            if self._state in (PositionState.OPEN, PositionState.EXECUTING, PositionState.CLOSING):
                asset = self._current_position.asset if self._current_position else ""
                self._state = PositionState.COOLDOWN
                self._state_entered_at = time.time()
                logger.info("Position closed: %s (%s), entering cooldown", asset, result)

    def force_release(self, reason: str = "forced") -> None:
        """Force release the lock (emergency / timeout)."""
        with self._lock:
            old = self._state
            self._state = PositionState.NO_POSITION
            self._current_position = None
            self._state_entered_at = time.time()
            logger.warning("Position lock force-released: %s -> NO_POSITION (%s)", old.value, reason)

    def _check_timeouts(self) -> None:
        """Auto-transition on timeouts (NOT under lock — caller must hold)."""
        elapsed = time.time() - self._state_entered_at

        if self._state == PositionState.EXECUTING:
            if elapsed > self._max_execution_time:
                logger.warning("Execution timeout (%.1fs), force-releasing", elapsed)
                self._state = PositionState.NO_POSITION
                self._current_position = None
                self._state_entered_at = time.time()

        elif self._state == PositionState.OPEN:
            if elapsed > self._max_position_time:
                logger.warning("Position timeout (%.1fs), closing", elapsed)
                self._state = PositionState.COOLDOWN
                self._state_entered_at = time.time()

        elif self._state == PositionState.COOLDOWN:
            if elapsed > self._cooldown_sec:
                self._state = PositionState.NO_POSITION
                self._current_position = None
                self._state_entered_at = time.time()

    def get_stats(self) -> Dict[str, Any]:
        """Get position lock statistics."""
        return {
            "state": self.state.value,
            "is_locked": self.is_locked,
            "total_trades": self._total_trades,
            "total_blocked": self._total_blocked,
            "current_asset": self._current_position.asset if self._current_position else None,
            "time_in_state_sec": round(time.time() - self._state_entered_at, 1),
        }
