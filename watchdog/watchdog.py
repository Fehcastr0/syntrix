"""
watchdog/watchdog.py — System watchdog for Syntrix.

Monitors:
- Thread health (stuck threads)
- Broker connection status
- Event bus queue depth
- Handler latency
- UI responsiveness
- WebSocket status
- System-wide health metrics
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from core.events import EventBus, EventPriority, EventType

logger = logging.getLogger("syntrix.watchdog")


@dataclass
class ThreadMonitor:
    """Monitor entry for a tracked thread."""

    name: str
    thread: threading.Thread
    last_heartbeat: float = 0.0
    stuck_threshold_sec: float = 30.0


@dataclass
class WatchdogConfig:
    """Watchdog configuration."""

    check_interval_sec: float = 10.0
    thread_stuck_threshold_sec: float = 30.0
    queue_overflow_threshold: int = 5000
    handler_slow_threshold_ms: float = 1000.0
    max_reconnect_failures: int = 3


class Watchdog:
    """
    System watchdog — monitors health of all Syntrix components.

    Runs as a background daemon thread. Emits WATCHDOG_ALERT events
    when issues are detected.
    """

    def __init__(
        self,
        event_bus: EventBus,
        config: Optional[WatchdogConfig] = None,
    ) -> None:
        self._event_bus = event_bus
        self._config = config or WatchdogConfig()
        self._thread_monitors: Dict[str, ThreadMonitor] = {}
        self._lock = threading.Lock()
        self._running = False
        self._watchdog_thread: Optional[threading.Thread] = None
        self._health_checks: List[Callable[[], Dict[str, Any]]] = []
        self._alerts: List[Dict[str, Any]] = []
        self._broker_health_fn: Optional[Callable[[], Dict[str, Any]]] = None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def alert_count(self) -> int:
        with self._lock:
            return len(self._alerts)

    def register_thread(
        self,
        name: str,
        thread: threading.Thread,
        stuck_threshold: Optional[float] = None,
    ) -> None:
        """Register a thread for monitoring."""
        with self._lock:
            self._thread_monitors[name] = ThreadMonitor(
                name=name,
                thread=thread,
                last_heartbeat=time.time(),
                stuck_threshold_sec=stuck_threshold or self._config.thread_stuck_threshold_sec,
            )
        logger.debug("Registered thread for monitoring: %s", name)

    def heartbeat(self, thread_name: str) -> None:
        """Update heartbeat for a monitored thread."""
        with self._lock:
            monitor = self._thread_monitors.get(thread_name)
            if monitor:
                monitor.last_heartbeat = time.time()

    def register_health_check(self, check_fn: Callable[[], Dict[str, Any]]) -> None:
        """Register a custom health check function."""
        self._health_checks.append(check_fn)

    def set_broker_health_fn(self, fn: Callable[[], Dict[str, Any]]) -> None:
        """Set broker health check function."""
        self._broker_health_fn = fn

    def start(self) -> None:
        """Start the watchdog daemon."""
        if self._running:
            return
        self._running = True
        self._watchdog_thread = threading.Thread(
            target=self._monitor_loop, name="watchdog", daemon=True
        )
        self._watchdog_thread.start()
        logger.info("Watchdog started (interval=%.1fs)", self._config.check_interval_sec)

    def stop(self) -> None:
        """Stop the watchdog."""
        self._running = False
        if self._watchdog_thread:
            self._watchdog_thread.join(timeout=5.0)
        logger.info("Watchdog stopped")

    def get_health_report(self) -> Dict[str, Any]:
        """Get comprehensive health report."""
        with self._lock:
            thread_status = {}
            for name, monitor in self._thread_monitors.items():
                alive = monitor.thread.is_alive()
                elapsed = time.time() - monitor.last_heartbeat
                stuck = elapsed > monitor.stuck_threshold_sec
                thread_status[name] = {
                    "alive": alive,
                    "last_heartbeat_ago_sec": round(elapsed, 1),
                    "stuck": stuck,
                }

            event_bus_status = {
                "running": self._event_bus.is_running,
                "queue_depth": self._event_bus.queue_depth,
                "dead_letters": self._event_bus.dead_letter_count,
                "metrics": self._event_bus.metrics.snapshot(),
            }

            broker_status = {}
            if self._broker_health_fn:
                try:
                    broker_status = self._broker_health_fn()
                except Exception as exc:
                    broker_status = {"error": str(exc)}

            custom_checks = {}
            for i, check in enumerate(self._health_checks):
                try:
                    custom_checks[f"check_{i}"] = check()
                except Exception as exc:
                    custom_checks[f"check_{i}"] = {"error": str(exc)}

            return {
                "timestamp": time.time(),
                "watchdog_running": self._running,
                "threads": thread_status,
                "event_bus": event_bus_status,
                "broker": broker_status,
                "custom_checks": custom_checks,
                "alert_count": len(self._alerts),
                "recent_alerts": self._alerts[-10:],
            }

    def _monitor_loop(self) -> None:
        """Main watchdog monitoring loop."""
        while self._running:
            try:
                self._check_threads()
                self._check_event_bus()
                self._check_broker()
                self._run_custom_checks()
            except Exception as exc:
                logger.error("Watchdog error: %s", exc, exc_info=True)
            time.sleep(self._config.check_interval_sec)

    def _check_threads(self) -> None:
        """Check for stuck or dead threads."""
        with self._lock:
            now = time.time()
            for name, monitor in self._thread_monitors.items():
                if not monitor.thread.is_alive():
                    self._emit_alert(
                        "thread_dead",
                        f"Thread '{name}' is dead",
                        {"thread": name},
                    )
                    continue

                elapsed = now - monitor.last_heartbeat
                if elapsed > monitor.stuck_threshold_sec:
                    self._emit_alert(
                        "thread_stuck",
                        f"Thread '{name}' stuck ({elapsed:.0f}s since heartbeat)",
                        {"thread": name, "elapsed_sec": round(elapsed, 1)},
                    )

    def _check_event_bus(self) -> None:
        """Check event bus health."""
        depth = self._event_bus.queue_depth
        if depth > self._config.queue_overflow_threshold:
            self._emit_alert(
                "queue_overflow",
                f"Event bus queue depth ({depth}) exceeds threshold ({self._config.queue_overflow_threshold})",
                {"queue_depth": depth},
            )

        metrics = self._event_bus.metrics.snapshot()
        avg_time = metrics.get("avg_processing_time_ms", 0)
        if avg_time > self._config.handler_slow_threshold_ms:
            self._emit_alert(
                "slow_handlers",
                f"Average handler time ({avg_time:.0f}ms) exceeds threshold",
                {"avg_processing_time_ms": avg_time},
            )

    def _check_broker(self) -> None:
        """Check broker health."""
        if not self._broker_health_fn:
            return
        try:
            health = self._broker_health_fn()
            if not health.get("connected", True):
                self._emit_alert(
                    "broker_disconnected",
                    "Broker is disconnected",
                    health,
                )
            if not health.get("websocket_alive", True):
                self._emit_alert(
                    "websocket_down",
                    "Broker WebSocket is not alive",
                    health,
                )
        except Exception as exc:
            self._emit_alert("broker_check_error", f"Broker health check failed: {exc}", {})

    def _run_custom_checks(self) -> None:
        """Run registered custom health checks."""
        for check in self._health_checks:
            try:
                result = check()
                if result.get("alert"):
                    self._emit_alert(
                        "custom",
                        result.get("message", "Custom check alert"),
                        result,
                    )
            except Exception as exc:
                logger.warning("Custom health check error: %s", exc)

    def _emit_alert(self, alert_type: str, message: str, details: Dict[str, Any]) -> None:
        """Emit a watchdog alert."""
        alert = {
            "type": alert_type,
            "message": message,
            "details": details,
            "timestamp": time.time(),
        }
        with self._lock:
            self._alerts.append(alert)
            if len(self._alerts) > 100:
                self._alerts = self._alerts[-50:]

        logger.warning("WATCHDOG ALERT [%s]: %s", alert_type, message)

        self._event_bus.emit(
            EventType.WATCHDOG_ALERT,
            data=alert,
            priority=EventPriority.HIGH,
        )
