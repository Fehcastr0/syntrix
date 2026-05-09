"""
core/states.py — Finite State Machine for Syntrix operational states.

Implements:
- OperationalState enum
- Valid transition map
- StateMachine with validation, history, metrics, and event emission
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Set

from core.events import Event, EventBus, EventPriority, EventType

logger = logging.getLogger("syntrix.states")


class OperationalState(Enum):
    """All operational states of the Syntrix system."""

    IDLE = "idle"
    SCANNING = "scanning"
    WAITING_CONTEXT = "waiting_context"
    READY = "ready"
    EXECUTING = "executing"
    COOLDOWN = "cooldown"
    RISK_LOCK = "risk_lock"
    SAFE_MODE = "safe_mode"


VALID_TRANSITIONS: Dict[OperationalState, Set[OperationalState]] = {
    OperationalState.IDLE: {
        OperationalState.SCANNING,
        OperationalState.SAFE_MODE,
    },
    OperationalState.SCANNING: {
        OperationalState.WAITING_CONTEXT,
        OperationalState.IDLE,
        OperationalState.RISK_LOCK,
        OperationalState.SAFE_MODE,
    },
    OperationalState.WAITING_CONTEXT: {
        OperationalState.READY,
        OperationalState.SCANNING,
        OperationalState.IDLE,
        OperationalState.RISK_LOCK,
        OperationalState.SAFE_MODE,
    },
    OperationalState.READY: {
        OperationalState.EXECUTING,
        OperationalState.SCANNING,
        OperationalState.IDLE,
        OperationalState.RISK_LOCK,
        OperationalState.SAFE_MODE,
    },
    OperationalState.EXECUTING: {
        OperationalState.COOLDOWN,
        OperationalState.SCANNING,
        OperationalState.IDLE,
        OperationalState.RISK_LOCK,
        OperationalState.SAFE_MODE,
    },
    OperationalState.COOLDOWN: {
        OperationalState.SCANNING,
        OperationalState.IDLE,
        OperationalState.RISK_LOCK,
        OperationalState.SAFE_MODE,
    },
    OperationalState.RISK_LOCK: {
        OperationalState.IDLE,
        OperationalState.SAFE_MODE,
    },
    OperationalState.SAFE_MODE: {
        OperationalState.IDLE,
    },
}


@dataclass
class StateTransitionRecord:
    """Record of a single state transition."""

    from_state: OperationalState
    to_state: OperationalState
    timestamp: float
    duration_in_previous: float
    reason: str = ""


class StateMetrics:
    """Tracks time spent in each state and transition counts."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._time_in_state: Dict[OperationalState, float] = {
            s: 0.0 for s in OperationalState
        }
        self._transition_count: Dict[str, int] = {}
        self._enter_count: Dict[OperationalState, int] = {
            s: 0 for s in OperationalState
        }

    def record_time(self, state: OperationalState, duration: float) -> None:
        with self._lock:
            self._time_in_state[state] += duration

    def record_transition(
        self, from_state: OperationalState, to_state: OperationalState
    ) -> None:
        key = f"{from_state.value}->{to_state.value}"
        with self._lock:
            self._transition_count[key] = self._transition_count.get(key, 0) + 1
            self._enter_count[to_state] += 1

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "time_in_state": {
                    s.value: round(t, 3)
                    for s, t in self._time_in_state.items()
                },
                "transition_counts": dict(self._transition_count),
                "enter_counts": {
                    s.value: c for s, c in self._enter_count.items()
                },
            }


ConditionFn = Callable[[OperationalState, OperationalState], bool]


class StateMachine:
    """
    Thread-safe finite state machine with event emission.

    Features:
    - Valid transition enforcement
    - Conditional transition rules
    - Full transition history
    - Per-state time tracking
    - Event emission on every transition (STATE_ENTER, STATE_EXIT, STATE_CHANGED)
    """

    MAX_HISTORY = 500

    def __init__(
        self,
        event_bus: EventBus,
        initial_state: OperationalState = OperationalState.IDLE,
    ) -> None:
        self._event_bus = event_bus
        self._state = initial_state
        self._lock = threading.RLock()
        self._state_entered_at = time.time()
        self._history: List[StateTransitionRecord] = []
        self._metrics = StateMetrics()
        self._conditions: List[ConditionFn] = []
        self._metrics.record_transition(initial_state, initial_state)
        logger.info("StateMachine initialized in %s", initial_state.value)

    @property
    def state(self) -> OperationalState:
        with self._lock:
            return self._state

    @property
    def time_in_current_state(self) -> float:
        with self._lock:
            return time.time() - self._state_entered_at

    @property
    def history(self) -> List[StateTransitionRecord]:
        with self._lock:
            return list(self._history)

    @property
    def metrics(self) -> StateMetrics:
        return self._metrics

    def add_condition(self, condition: ConditionFn) -> None:
        """Add a conditional check that must pass for any transition."""
        with self._lock:
            self._conditions.append(condition)

    def can_transition(self, target: OperationalState) -> bool:
        """Check if a transition to target state is valid."""
        with self._lock:
            if target not in VALID_TRANSITIONS.get(self._state, set()):
                return False
            for cond in self._conditions:
                try:
                    if not cond(self._state, target):
                        return False
                except Exception as exc:
                    logger.error("Condition check failed: %s", exc)
                    return False
            return True

    def transition(self, target: OperationalState, reason: str = "") -> bool:
        """
        Attempt a state transition. Returns True on success.

        Emits STATE_EXIT, STATE_ENTER, and STATE_CHANGED events.
        """
        with self._lock:
            if target == self._state:
                return True

            if not self.can_transition(target):
                logger.warning(
                    "Invalid transition %s -> %s (reason: %s)",
                    self._state.value,
                    target.value,
                    reason,
                )
                return False

            now = time.time()
            old_state = self._state
            duration = now - self._state_entered_at

            self._metrics.record_time(old_state, duration)
            self._metrics.record_transition(old_state, target)

            record = StateTransitionRecord(
                from_state=old_state,
                to_state=target,
                timestamp=now,
                duration_in_previous=duration,
                reason=reason,
            )
            self._history.append(record)
            if len(self._history) > self.MAX_HISTORY:
                self._history = self._history[-self.MAX_HISTORY:]

            self._state = target
            self._state_entered_at = now

        self._event_bus.emit(
            EventType.STATE_EXIT,
            data={"state": old_state.value, "duration": round(duration, 4), "reason": reason},
            priority=EventPriority.HIGH,
        )
        self._event_bus.emit(
            EventType.STATE_ENTER,
            data={"state": target.value, "reason": reason},
            priority=EventPriority.HIGH,
        )
        self._event_bus.emit(
            EventType.STATE_CHANGED,
            data={
                "from": old_state.value,
                "to": target.value,
                "duration_in_previous": round(duration, 4),
                "reason": reason,
            },
            priority=EventPriority.HIGH,
        )

        logger.info(
            "State transition: %s -> %s (reason: %s, duration_in_prev: %.2fs)",
            old_state.value,
            target.value,
            reason,
            duration,
        )
        return True

    def force_state(self, target: OperationalState, reason: str = "") -> None:
        """Force a state change bypassing validation (emergency use only)."""
        with self._lock:
            old_state = self._state
            now = time.time()
            duration = now - self._state_entered_at
            self._metrics.record_time(old_state, duration)
            self._state = target
            self._state_entered_at = now

        logger.warning(
            "FORCED state: %s -> %s (reason: %s)",
            old_state.value,
            target.value,
            reason,
        )
        self._event_bus.emit(
            EventType.STATE_CHANGED,
            data={"from": old_state.value, "to": target.value, "forced": True, "reason": reason},
            priority=EventPriority.CRITICAL,
        )
