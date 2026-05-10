"""
core/events.py — Event-driven architecture backbone for Syntrix.

Implements:
- EventType enum with all system event categories
- EventPriority for priority-based dispatch
- Event dataclass with correlation/causation tracking
- AsyncEventBus with priority queues, worker threads, dead letter queue,
  throughput metrics, graceful shutdown, and retry handling
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from enum import Enum, IntEnum, auto
from queue import Empty, PriorityQueue
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger("syntrix.events")


class EventType(Enum):
    """All event types in the Syntrix system."""

    # Core lifecycle
    SYSTEM_START = "system.start"
    SYSTEM_STOP = "system.stop"
    SYSTEM_ERROR = "system.error"
    SYSTEM_HEALTH = "system.health"

    # State machine
    STATE_ENTER = "state.enter"
    STATE_EXIT = "state.exit"
    STATE_CHANGED = "state.changed"

    # Market data
    CANDLE_RECEIVED = "market.candle_received"
    TICK_RECEIVED = "market.tick_received"
    PAYOUT_UPDATED = "market.payout_updated"

    # Context
    CONTEXT_EVALUATED = "context.evaluated"
    CONTEXT_BLOCKED = "context.blocked"
    CONTEXT_ALLOWED = "context.allowed"
    REGIME_CHANGED = "context.regime_changed"
    SESSION_CHANGED = "context.session_changed"
    SPIKE_DETECTED = "context.spike_detected"
    NEWS_ALERT = "context.news_alert"

    # Strategy
    SIGNAL_GENERATED = "strategy.signal_generated"
    SIGNAL_SCORED = "strategy.signal_scored"
    SIGNAL_REJECTED = "strategy.signal_rejected"

    # Risk
    RISK_CHECK_PASSED = "risk.check_passed"
    RISK_CHECK_FAILED = "risk.check_failed"
    RISK_LOCK = "risk.lock"
    SAFE_MODE_ENTER = "risk.safe_mode_enter"
    SAFE_MODE_EXIT = "risk.safe_mode_exit"
    DRAWDOWN_ALERT = "risk.drawdown_alert"
    STOP_GAIN_HIT = "risk.stop_gain_hit"
    STOP_LOSS_HIT = "risk.stop_loss_hit"

    # Execution
    TRADE_REQUESTED = "execution.trade_requested"
    TRADE_EXECUTED = "execution.trade_executed"
    TRADE_RESULT = "execution.trade_result"
    TRADE_ERROR = "execution.trade_error"
    TRADE_BLOCKED = "execution.trade_blocked"

    # Broker
    BROKER_CONNECTED = "broker.connected"
    BROKER_DISCONNECTED = "broker.disconnected"
    BROKER_RECONNECTING = "broker.reconnecting"
    BROKER_ERROR = "broker.error"
    BROKER_HEARTBEAT = "broker.heartbeat"

    # Analytics
    ANALYTICS_UPDATED = "analytics.updated"
    DEGRADATION_DETECTED = "analytics.degradation_detected"

    # Watchdog
    WATCHDOG_ALERT = "watchdog.alert"
    WATCHDOG_THREAD_STUCK = "watchdog.thread_stuck"
    WATCHDOG_QUEUE_OVERFLOW = "watchdog.queue_overflow"

    # Replay
    REPLAY_START = "replay.start"
    REPLAY_EVENT = "replay.event"
    REPLAY_END = "replay.end"


class EventPriority(IntEnum):
    """Event priorities — lower value = higher priority."""

    CRITICAL = 0
    HIGH = 10
    NORMAL = 20
    LOW = 30
    BACKGROUND = 40


EVENT_VERSION = "1.0"


@dataclass(order=False)
class Event:
    """Immutable event with full tracing metadata."""

    event_type: EventType
    data: Dict[str, Any] = field(default_factory=dict)
    priority: EventPriority = EventPriority.NORMAL
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    correlation_id: Optional[str] = None
    causation_id: Optional[str] = None
    parent_event_id: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    version: str = EVENT_VERSION
    source: str = ""
    retry_count: int = 0
    max_retries: int = 3

    def __lt__(self, other: Event) -> bool:
        if self.priority != other.priority:
            return self.priority < other.priority
        return self.timestamp < other.timestamp

    def __le__(self, other: Event) -> bool:
        return self == other or self < other

    def child(
        self,
        event_type: EventType,
        data: Optional[Dict[str, Any]] = None,
        priority: Optional[EventPriority] = None,
    ) -> Event:
        """Create a child event inheriting correlation chain."""
        return Event(
            event_type=event_type,
            data=data or {},
            priority=priority or self.priority,
            correlation_id=self.correlation_id or self.event_id,
            causation_id=self.event_id,
            parent_event_id=self.event_id,
            source=self.source,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize event to dict for storage/transport."""
        d = asdict(self)
        d["event_type"] = self.event_type.value
        d["priority"] = int(self.priority)
        return d

    def to_json(self) -> str:
        """Serialize event to JSON string."""
        return json.dumps(self.to_dict(), default=str)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> Event:
        """Deserialize event from dict."""
        d = d.copy()
        d["event_type"] = EventType(d["event_type"])
        d["priority"] = EventPriority(d["priority"])
        return cls(**d)


class EventBusMetrics:
    """Thread-safe metrics collector for EventBus."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.events_published: int = 0
        self.events_processed: int = 0
        self.events_dropped: int = 0
        self.events_retried: int = 0
        self.events_dead_lettered: int = 0
        self.handler_errors: int = 0
        self._throughput_window: List[float] = []
        self._processing_times: List[float] = []

    def record_published(self) -> None:
        with self._lock:
            self.events_published += 1

    def record_processed(self, processing_time: float) -> None:
        with self._lock:
            self.events_processed += 1
            now = time.time()
            self._throughput_window.append(now)
            self._processing_times.append(processing_time)
            cutoff = now - 60.0
            self._throughput_window = [
                t for t in self._throughput_window if t > cutoff
            ]
            if len(self._processing_times) > 1000:
                self._processing_times = self._processing_times[-500:]

    def record_dropped(self) -> None:
        with self._lock:
            self.events_dropped += 1

    def record_retry(self) -> None:
        with self._lock:
            self.events_retried += 1

    def record_dead_letter(self) -> None:
        with self._lock:
            self.events_dead_lettered += 1

    def record_handler_error(self) -> None:
        with self._lock:
            self.handler_errors += 1

    @property
    def throughput_per_minute(self) -> float:
        with self._lock:
            now = time.time()
            cutoff = now - 60.0
            self._throughput_window = [
                t for t in self._throughput_window if t > cutoff
            ]
            return float(len(self._throughput_window))

    @property
    def avg_processing_time_ms(self) -> float:
        with self._lock:
            if not self._processing_times:
                return 0.0
            return (sum(self._processing_times) / len(self._processing_times)) * 1000

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "events_published": self.events_published,
                "events_processed": self.events_processed,
                "events_dropped": self.events_dropped,
                "events_retried": self.events_retried,
                "events_dead_lettered": self.events_dead_lettered,
                "handler_errors": self.handler_errors,
                "throughput_per_minute": self.throughput_per_minute,
                "avg_processing_time_ms": self.avg_processing_time_ms,
            }


HandlerFn = Callable[[Event], None]


class EventBus:
    """
    Asynchronous, priority-based event bus.

    Features:
    - Priority queue dispatch
    - Worker thread pool
    - Dead letter queue for failed events
    - Retry handling with backoff
    - Correlation/causation tracking
    - Throughput and latency metrics
    - Graceful shutdown
    - Thread-safe subscribe/unsubscribe
    """

    DEFAULT_MAX_QUEUE = 10_000
    DEFAULT_WORKERS = 4

    def __init__(
        self,
        max_queue_size: int = DEFAULT_MAX_QUEUE,
        num_workers: int = DEFAULT_WORKERS,
    ) -> None:
        self._queue: PriorityQueue[Event] = PriorityQueue(maxsize=max_queue_size)
        self._handlers: Dict[EventType, List[HandlerFn]] = {}
        self._wildcard_handlers: List[HandlerFn] = []
        self._handler_lock = threading.RLock()
        self._dead_letter_queue: List[Event] = []
        self._dlq_lock = threading.Lock()
        self._max_dlq_size = 1000
        self._metrics = EventBusMetrics()
        self._running = False
        self._shutdown_event = threading.Event()
        self._num_workers = num_workers
        self._executor: Optional[ThreadPoolExecutor] = None
        self._worker_threads: List[threading.Thread] = []

    @property
    def metrics(self) -> EventBusMetrics:
        return self._metrics

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    @property
    def dead_letter_count(self) -> int:
        with self._dlq_lock:
            return len(self._dead_letter_queue)

    @property
    def is_running(self) -> bool:
        return self._running

    def subscribe(self, event_type: EventType, handler: HandlerFn) -> None:
        """Subscribe a handler to a specific event type."""
        with self._handler_lock:
            if event_type not in self._handlers:
                self._handlers[event_type] = []
            if handler not in self._handlers[event_type]:
                self._handlers[event_type].append(handler)
                logger.debug("Handler %s subscribed to %s", handler.__name__, event_type.value)

    def subscribe_all(self, handler: HandlerFn) -> None:
        """Subscribe a handler to ALL event types (wildcard)."""
        with self._handler_lock:
            if handler not in self._wildcard_handlers:
                self._wildcard_handlers.append(handler)

    def unsubscribe(self, event_type: EventType, handler: HandlerFn) -> None:
        """Unsubscribe a handler from a specific event type."""
        with self._handler_lock:
            if event_type in self._handlers:
                try:
                    self._handlers[event_type].remove(handler)
                except ValueError:
                    pass

    def unsubscribe_all(self, handler: HandlerFn) -> None:
        """Unsubscribe a wildcard handler."""
        with self._handler_lock:
            try:
                self._wildcard_handlers.remove(handler)
            except ValueError:
                pass

    def publish(self, event: Event) -> bool:
        """
        Publish an event to the bus. Non-blocking.
        Returns True if enqueued, False if queue full (event dropped).
        """
        if not self._running:
            logger.warning("EventBus not running; event %s dropped", event.event_type.value)
            self._metrics.record_dropped()
            return False

        try:
            self._queue.put_nowait(event)
            self._metrics.record_published()
            return True
        except Exception:
            logger.warning(
                "Queue full; dropping event %s (id=%s)", event.event_type.value, event.event_id
            )
            self._metrics.record_dropped()
            return False

    def emit(self, event_type: EventType, data: Optional[Dict[str, Any]] = None, **kwargs: Any) -> bool:
        """Convenience: create and publish an event in one call."""
        event = Event(event_type=event_type, data=data or {}, **kwargs)
        return self.publish(event)

    def start(self) -> None:
        """Start the EventBus worker threads."""
        if self._running:
            return
        self._running = True
        self._shutdown_event.clear()
        self._executor = ThreadPoolExecutor(
            max_workers=self._num_workers, thread_name_prefix="eventbus-worker"
        )
        for i in range(self._num_workers):
            t = threading.Thread(target=self._worker_loop, name=f"eventbus-worker-{i}", daemon=True)
            t.start()
            self._worker_threads.append(t)
        logger.info("EventBus started with %d workers", self._num_workers)

    def stop(self, timeout: float = 5.0) -> None:
        """Graceful shutdown: drain queue then stop workers."""
        if not self._running:
            return
        logger.info("EventBus shutting down (timeout=%.1fs)...", timeout)
        self._running = False
        self._shutdown_event.set()

        for t in self._worker_threads:
            t.join(timeout=timeout)

        if self._executor:
            self._executor.shutdown(wait=True, cancel_futures=False)
            self._executor = None

        self._worker_threads.clear()
        remaining = self._queue.qsize()
        if remaining > 0:
            logger.warning("EventBus stopped with %d events still in queue", remaining)
        else:
            logger.info("EventBus stopped cleanly")

    def _worker_loop(self) -> None:
        """Worker thread: pull events from the priority queue and dispatch."""
        while True:
            if self._shutdown_event.is_set() and self._queue.empty():
                break
            try:
                event = self._queue.get(timeout=0.2)
            except Empty:
                if self._shutdown_event.is_set():
                    break
                continue

            start_time = time.time()
            try:
                self._dispatch(event)
                elapsed = time.time() - start_time
                self._metrics.record_processed(elapsed)
            except Exception as exc:
                logger.error(
                    "Unhandled error dispatching event %s: %s",
                    event.event_type.value,
                    exc,
                    exc_info=True,
                )
                self._handle_failure(event)
            finally:
                self._queue.task_done()

    def _dispatch(self, event: Event) -> None:
        """Dispatch an event to all registered handlers."""
        with self._handler_lock:
            handlers = list(self._handlers.get(event.event_type, []))
            wildcards = list(self._wildcard_handlers)

        failed = False
        for handler in handlers + wildcards:
            try:
                handler(event)
            except Exception as exc:
                self._metrics.record_handler_error()
                logger.error(
                    "Handler %s failed for event %s: %s",
                    handler.__name__,
                    event.event_type.value,
                    exc,
                    exc_info=True,
                )
                failed = True
        if failed:
            raise RuntimeError("One or more handlers failed")

    def _handle_failure(self, event: Event) -> None:
        """Retry or dead-letter a failed event."""
        if event.retry_count < event.max_retries:
            event.retry_count += 1
            self._metrics.record_retry()
            logger.info(
                "Retrying event %s (attempt %d/%d)",
                event.event_id,
                event.retry_count,
                event.max_retries,
            )
            time.sleep(0.1 * event.retry_count)
            try:
                self._queue.put_nowait(event)
            except Exception:
                self._send_to_dlq(event)
        else:
            self._send_to_dlq(event)

    def _send_to_dlq(self, event: Event) -> None:
        """Send a permanently failed event to the dead letter queue."""
        with self._dlq_lock:
            self._dead_letter_queue.append(event)
            if len(self._dead_letter_queue) > self._max_dlq_size:
                self._dead_letter_queue = self._dead_letter_queue[-self._max_dlq_size:]
        self._metrics.record_dead_letter()
        logger.warning(
            "Event %s (type=%s) sent to dead letter queue after %d retries",
            event.event_id,
            event.event_type.value,
            event.retry_count,
        )

    def get_dead_letters(self) -> List[Event]:
        """Return a copy of the dead letter queue."""
        with self._dlq_lock:
            return list(self._dead_letter_queue)

    def clear_dead_letters(self) -> int:
        """Clear dead letter queue. Returns count of cleared events."""
        with self._dlq_lock:
            count = len(self._dead_letter_queue)
            self._dead_letter_queue.clear()
            return count

    def get_registered_types(self) -> Set[EventType]:
        """Return all event types that have at least one handler."""
        with self._handler_lock:
            return set(self._handlers.keys())
