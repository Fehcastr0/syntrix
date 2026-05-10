"""
execution/engine.py — Execution engine for Syntrix.

Implements:
- Timing engine with precise entry timing
- Controlled jitter to avoid pattern detection
- Rate limiting
- Spacing between orders
- Safe retry with backoff
- Execution confirmation
- Latency tracking
"""

from __future__ import annotations

import logging
import random
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional  # noqa: F811

from brokers.base import BaseBroker, TradeDirection, TradeRequest, TradeResult
from core.events import EventBus, EventPriority, EventType

logger = logging.getLogger("syntrix.execution")


@dataclass
class ExecutionConfig:
    """Execution engine configuration."""

    jitter_range_ms: tuple[int, int] = (50, 300)
    min_spacing_sec: float = 5.0
    max_retries: int = 2
    retry_delay_sec: float = 2.0
    max_latency_ms: float = 500.0
    execution_timeout_sec: float = 30.0
    confirm_execution: bool = True
    stale_signal_sec: float = 10.0
    double_order_window_sec: float = 5.0


@dataclass
class ExecutionRecord:
    """Record of an execution attempt."""

    trade_id: str
    asset: str
    direction: str
    amount: float
    duration: int
    timestamp_requested: float
    timestamp_executed: float = 0.0
    timestamp_confirmed: float = 0.0
    latency_ms: float = 0.0
    jitter_ms: float = 0.0
    retries: int = 0
    success: bool = False
    broker_id: str = ""
    error: str = ""


class ExecutionEngine:
    """
    Manages trade execution with timing control, jitter, and safety.

    The execution engine sits between the decision layer and the broker.
    It does NOT make trading decisions — only executes them safely.
    """

    def __init__(
        self,
        broker: BaseBroker,
        event_bus: EventBus,
        config: Optional[ExecutionConfig] = None,
    ) -> None:
        self._broker = broker
        self._event_bus = event_bus
        self._config = config or ExecutionConfig()
        self._lock = threading.Lock()
        self._last_execution_time: float = 0.0
        self._latency_history: List[float] = []
        self._execution_log: List[ExecutionRecord] = []
        self._executing = False
        self._recent_orders: Dict[str, float] = {}  # asset+direction -> timestamp

    @property
    def avg_latency_ms(self) -> float:
        if not self._latency_history:
            return 0.0
        return sum(self._latency_history) / len(self._latency_history)

    @property
    def execution_count(self) -> int:
        return len(self._execution_log)

    @property
    def is_executing(self) -> bool:
        return self._executing

    def execute(
        self,
        asset: str,
        direction: TradeDirection,
        amount: float,
        duration: int,
        trade_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> ExecutionRecord:
        """
        Execute a trade with timing control, jitter, and retry.

        Args:
            asset: Asset to trade
            direction: CALL or PUT
            amount: Trade amount
            duration: Trade duration in seconds
            trade_id: Optional trade identifier
            correlation_id: Optional correlation ID for tracing

        Returns:
            ExecutionRecord with full execution details
        """
        trade_id = trade_id or str(uuid.uuid4())[:8]
        record = ExecutionRecord(
            trade_id=trade_id,
            asset=asset,
            direction=direction.value,
            amount=amount,
            duration=duration,
            timestamp_requested=time.time(),
        )

        self._event_bus.emit(
            EventType.TRADE_REQUESTED,
            data={
                "trade_id": trade_id,
                "asset": asset,
                "direction": direction.value,
                "amount": amount,
                "duration": duration,
            },
            correlation_id=correlation_id,
            priority=EventPriority.HIGH,
        )

        with self._lock:
            if self._executing:
                record.error = "Execution already in progress (double order protection)"
                self._emit_error(record, correlation_id)
                return record
            self._executing = True

        try:
            # Stale signal check
            signal_age = time.time() - record.timestamp_requested
            if signal_age > self._config.stale_signal_sec:
                record.error = f"Stale signal ({signal_age:.1f}s old)"
                self._emit_error(record, correlation_id)
                return record

            # Double order protection
            order_key = f"{asset}_{direction.value}"
            last_order = self._recent_orders.get(order_key, 0)
            if (time.time() - last_order) < self._config.double_order_window_sec:
                record.error = "Double order protection triggered"
                self._emit_error(record, correlation_id)
                return record
            self._recent_orders[order_key] = time.time()
            spacing_ok = self._wait_spacing()
            if not spacing_ok:
                record.error = "Spacing timeout"
                self._emit_error(record, correlation_id)
                return record

            jitter_ms = self._apply_jitter()
            record.jitter_ms = jitter_ms

            request = TradeRequest(
                asset=asset,
                direction=direction,
                amount=amount,
                duration=duration,
                trade_id=trade_id,
            )

            result = self._execute_with_retry(request, record)

            if result.success:
                record.success = True
                record.broker_id = result.broker_id
                record.timestamp_executed = time.time()
                record.latency_ms = result.latency_ms
                self._record_latency(result.latency_ms)

                self._event_bus.emit(
                    EventType.TRADE_EXECUTED,
                    data={
                        "trade_id": trade_id,
                        "broker_id": result.broker_id,
                        "latency_ms": result.latency_ms,
                        "jitter_ms": jitter_ms,
                    },
                    correlation_id=correlation_id,
                    priority=EventPriority.HIGH,
                )

                logger.info(
                    "Trade executed: %s %s %s $%.2f (latency=%.0fms, jitter=%.0fms)",
                    direction.value,
                    asset,
                    trade_id,
                    amount,
                    result.latency_ms,
                    jitter_ms,
                )
            else:
                record.error = result.error
                self._emit_error(record, correlation_id)

        finally:
            with self._lock:
                self._executing = False
                self._last_execution_time = time.time()
                self._execution_log.append(record)
                if len(self._execution_log) > 500:
                    self._execution_log = self._execution_log[-250:]

        return record

    def wait_for_result(
        self,
        broker_id: str,
        trade_id: str = "",
        correlation_id: Optional[str] = None,
        timeout: float = 120.0,
    ) -> TradeResult:
        """Wait for and return the result of an executed trade."""
        result = self._broker.check_trade_result(broker_id)

        self._event_bus.emit(
            EventType.TRADE_RESULT,
            data={
                "trade_id": trade_id,
                "broker_id": broker_id,
                "result": result.result,
                "profit": result.profit,
            },
            correlation_id=correlation_id,
            priority=EventPriority.HIGH,
        )

        return result

    def _execute_with_retry(self, request: TradeRequest, record: ExecutionRecord) -> TradeResult:
        """Execute with retry logic."""
        last_error = ""
        for attempt in range(self._config.max_retries + 1):
            try:
                result = self._broker.execute_trade(request)
                if result.success:
                    record.retries = attempt
                    return result
                last_error = result.error
            except Exception as exc:
                last_error = str(exc)
                logger.warning("Execution attempt %d failed: %s", attempt + 1, exc)

            if attempt < self._config.max_retries:
                delay = self._config.retry_delay_sec * (attempt + 1)
                logger.info("Retry in %.1fs...", delay)
                time.sleep(delay)

        record.retries = self._config.max_retries
        return TradeResult(
            trade_id=request.trade_id,
            success=False,
            result="error",
            error=f"All retries failed: {last_error}",
        )

    def _wait_spacing(self) -> bool:
        """Enforce minimum spacing between trades."""
        if self._last_execution_time == 0:
            return True
        elapsed = time.time() - self._last_execution_time
        remaining = self._config.min_spacing_sec - elapsed
        if remaining > 0:
            logger.debug("Spacing wait: %.1fs", remaining)
            time.sleep(remaining)
        return True

    def _apply_jitter(self) -> float:
        """Apply random jitter to avoid pattern detection."""
        lo, hi = self._config.jitter_range_ms
        jitter_ms = random.randint(lo, hi)
        time.sleep(jitter_ms / 1000.0)
        return float(jitter_ms)

    def _record_latency(self, latency_ms: float) -> None:
        self._latency_history.append(latency_ms)
        if len(self._latency_history) > 200:
            self._latency_history = self._latency_history[-100:]

    def _emit_error(self, record: ExecutionRecord, correlation_id: Optional[str]) -> None:
        self._event_bus.emit(
            EventType.TRADE_ERROR,
            data={
                "trade_id": record.trade_id,
                "asset": record.asset,
                "error": record.error,
            },
            correlation_id=correlation_id,
            priority=EventPriority.HIGH,
        )
        logger.error("Trade execution failed: %s (%s)", record.trade_id, record.error)

    def get_execution_stats(self) -> Dict[str, Any]:
        """Get execution statistics."""
        successful = [r for r in self._execution_log if r.success]
        failed = [r for r in self._execution_log if not r.success]

        # Execution analytics
        queue_latencies = []
        for r in successful:
            if r.timestamp_executed > 0 and r.timestamp_requested > 0:
                queue_latencies.append((r.timestamp_executed - r.timestamp_requested) * 1000)

        error_breakdown: Dict[str, int] = {}
        for r in failed:
            key = r.error.split("(")[0].strip() if r.error else "unknown"
            error_breakdown[key] = error_breakdown.get(key, 0) + 1

        return {
            "total_executions": len(self._execution_log),
            "successful": len(successful),
            "failed": len(failed),
            "success_rate": round(len(successful) / max(1, len(self._execution_log)) * 100, 1),
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "avg_jitter_ms": round(
                sum(r.jitter_ms for r in successful) / len(successful) if successful else 0, 1
            ),
            "avg_queue_latency_ms": round(
                sum(queue_latencies) / len(queue_latencies) if queue_latencies else 0, 1
            ),
            "max_latency_ms": round(max(self._latency_history) if self._latency_history else 0, 1),
            "error_breakdown": error_breakdown,
        }
