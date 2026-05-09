"""
event_store/sqlite_store.py — SQLite-backed event store for Syntrix.

Persists all events, state transitions, trades, blocks, and metrics
for full replay and auditability.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

from core.events import Event, EventBus, EventType
from event_store.models import (
    StoredBlock,
    StoredEvent,
    StoredMetric,
    StoredState,
    StoredTrade,
)

logger = logging.getLogger("syntrix.event_store")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    data TEXT DEFAULT '{}',
    priority INTEGER DEFAULT 20,
    correlation_id TEXT,
    causation_id TEXT,
    parent_event_id TEXT,
    timestamp REAL NOT NULL,
    version TEXT DEFAULT '1.0',
    source TEXT DEFAULT '',
    retry_count INTEGER DEFAULT 0,
    session_id TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_correlation ON events(correlation_id);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);

CREATE TABLE IF NOT EXISTS state_transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    timestamp REAL NOT NULL,
    duration_in_previous REAL DEFAULT 0,
    reason TEXT DEFAULT '',
    session_id TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_states_timestamp ON state_transitions(timestamp);
CREATE INDEX IF NOT EXISTS idx_states_session ON state_transitions(session_id);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT NOT NULL UNIQUE,
    asset TEXT DEFAULT '',
    direction TEXT DEFAULT '',
    amount REAL DEFAULT 0,
    payout REAL DEFAULT 0,
    duration INTEGER DEFAULT 0,
    strategy TEXT DEFAULT '',
    score REAL DEFAULT 0,
    confidence REAL DEFAULT 0,
    regime TEXT DEFAULT '',
    session_name TEXT DEFAULT '',
    profile TEXT DEFAULT '',
    mode TEXT DEFAULT '',
    context_status TEXT DEFAULT '',
    risk_status TEXT DEFAULT '',
    state_at_entry TEXT DEFAULT '',
    latency_ms REAL DEFAULT 0,
    result TEXT DEFAULT 'pending',
    profit REAL DEFAULT 0,
    block_reason TEXT DEFAULT '',
    timestamp_requested REAL DEFAULT 0,
    timestamp_executed REAL DEFAULT 0,
    timestamp_result REAL DEFAULT 0,
    correlation_id TEXT DEFAULT '',
    session_id TEXT DEFAULT '',
    extra TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_trades_asset ON trades(asset);
CREATE INDEX IF NOT EXISTS idx_trades_result ON trades(result);
CREATE INDEX IF NOT EXISTS idx_trades_session ON trades(session_id);

CREATE TABLE IF NOT EXISTS blocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reason TEXT NOT NULL,
    source TEXT DEFAULT '',
    details TEXT DEFAULT '{}',
    timestamp REAL NOT NULL,
    session_id TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_blocks_timestamp ON blocks(timestamp);

CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_name TEXT NOT NULL,
    value REAL DEFAULT 0,
    tags TEXT DEFAULT '{}',
    timestamp REAL NOT NULL,
    session_id TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_metrics_name ON metrics(metric_name);
CREATE INDEX IF NOT EXISTS idx_metrics_timestamp ON metrics(timestamp);
"""


class SQLiteEventStore:
    """
    Thread-safe SQLite event store.

    Persists events, state transitions, trades, blocks, and metrics.
    Can optionally subscribe to the EventBus for automatic persistence.
    """

    def __init__(self, db_path: str = "data/syntrix.db", session_id: Optional[str] = None) -> None:
        self._db_path = db_path
        self._session_id = session_id or str(uuid.uuid4())[:8]
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA_SQL)
        logger.info("Event store initialized at %s (session=%s)", self._db_path, self._session_id)

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def store_event(self, event: Event) -> None:
        """Persist a single event."""
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO events
                   (event_id, event_type, data, priority, correlation_id,
                    causation_id, parent_event_id, timestamp, version,
                    source, retry_count, session_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.event_id,
                    event.event_type.value,
                    json.dumps(event.data, default=str),
                    int(event.priority),
                    event.correlation_id,
                    event.causation_id,
                    event.parent_event_id,
                    event.timestamp,
                    event.version,
                    event.source,
                    event.retry_count,
                    self._session_id,
                ),
            )

    def store_state_transition(
        self,
        from_state: str,
        to_state: str,
        timestamp: float,
        duration: float = 0.0,
        reason: str = "",
    ) -> None:
        """Persist a state transition."""
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO state_transitions
                   (from_state, to_state, timestamp, duration_in_previous, reason, session_id)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (from_state, to_state, timestamp, duration, reason, self._session_id),
            )

    def store_trade(self, trade: StoredTrade) -> None:
        """Persist a trade record."""
        trade.session_id = self._session_id
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO trades
                   (trade_id, asset, direction, amount, payout, duration,
                    strategy, score, confidence, regime, session_name, profile,
                    mode, context_status, risk_status, state_at_entry,
                    latency_ms, result, profit, block_reason,
                    timestamp_requested, timestamp_executed, timestamp_result,
                    correlation_id, session_id, extra)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    trade.trade_id,
                    trade.asset,
                    trade.direction,
                    trade.amount,
                    trade.payout,
                    trade.duration,
                    trade.strategy,
                    trade.score,
                    trade.confidence,
                    trade.regime,
                    trade.session_name,
                    trade.profile,
                    trade.mode,
                    trade.context_status,
                    trade.risk_status,
                    trade.state_at_entry,
                    trade.latency_ms,
                    trade.result,
                    trade.profit,
                    trade.block_reason,
                    trade.timestamp_requested,
                    trade.timestamp_executed,
                    trade.timestamp_result,
                    trade.correlation_id,
                    trade.session_id,
                    trade.extra,
                ),
            )

    def store_block(self, reason: str, source: str = "", details: Optional[Dict[str, Any]] = None) -> None:
        """Persist a block event."""
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO blocks (reason, source, details, timestamp, session_id)
                   VALUES (?, ?, ?, ?, ?)""",
                (reason, source, json.dumps(details or {}, default=str), time.time(), self._session_id),
            )

    def store_metric(self, name: str, value: float, tags: Optional[Dict[str, str]] = None) -> None:
        """Persist a metric data point."""
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO metrics (metric_name, value, tags, timestamp, session_id)
                   VALUES (?, ?, ?, ?, ?)""",
                (name, value, json.dumps(tags or {}), time.time(), self._session_id),
            )

    # ── Query methods ──

    def get_events(
        self,
        event_type: Optional[str] = None,
        correlation_id: Optional[str] = None,
        session_id: Optional[str] = None,
        since: Optional[float] = None,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """Query stored events with filters."""
        query = "SELECT * FROM events WHERE 1=1"
        params: list[Any] = []

        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)
        if correlation_id:
            query += " AND correlation_id = ?"
            params.append(correlation_id)
        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)
        if since:
            query += " AND timestamp >= ?"
            params.append(since)

        query += " ORDER BY timestamp ASC LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]

    def get_trades(
        self,
        session_id: Optional[str] = None,
        result: Optional[str] = None,
        asset: Optional[str] = None,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        """Query stored trades."""
        query = "SELECT * FROM trades WHERE 1=1"
        params: list[Any] = []

        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)
        if result:
            query += " AND result = ?"
            params.append(result)
        if asset:
            query += " AND asset = ?"
            params.append(asset)

        query += " ORDER BY timestamp_requested DESC LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]

    def get_state_history(self, session_id: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
        """Query state transition history."""
        query = "SELECT * FROM state_transitions WHERE 1=1"
        params: list[Any] = []

        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]

    def get_blocks(self, session_id: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
        """Query block records."""
        query = "SELECT * FROM blocks WHERE 1=1"
        params: list[Any] = []

        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]

    def get_metrics(
        self, metric_name: Optional[str] = None, since: Optional[float] = None, limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """Query metric data points."""
        query = "SELECT * FROM metrics WHERE 1=1"
        params: list[Any] = []

        if metric_name:
            query += " AND metric_name = ?"
            params.append(metric_name)
        if since:
            query += " AND timestamp >= ?"
            params.append(since)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]

    def get_event_timeline(self, correlation_id: str) -> List[Dict[str, Any]]:
        """Get full causal chain for a correlation ID."""
        return self.get_events(correlation_id=correlation_id, limit=10000)

    def attach_to_bus(self, event_bus: EventBus) -> None:
        """Subscribe to all events on the bus for automatic persistence."""
        event_bus.subscribe_all(self._on_event)
        logger.info("Event store attached to EventBus")

    def _on_event(self, event: Event) -> None:
        """Handler that persists every event from the bus."""
        try:
            self.store_event(event)
            if event.event_type == EventType.STATE_CHANGED:
                self.store_state_transition(
                    from_state=event.data.get("from", ""),
                    to_state=event.data.get("to", ""),
                    timestamp=event.timestamp,
                    duration=event.data.get("duration_in_previous", 0.0),
                    reason=event.data.get("reason", ""),
                )
        except Exception as exc:
            logger.error("Failed to persist event %s: %s", event.event_id, exc)
