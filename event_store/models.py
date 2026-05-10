"""
event_store/models.py — Data models for event sourcing persistence.

Defines the schema for stored events, states, trades, blocks, and metrics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class StoredEvent:
    """Persisted event record."""

    id: int = 0
    event_id: str = ""
    event_type: str = ""
    data: str = ""  # JSON
    priority: int = 20
    correlation_id: Optional[str] = None
    causation_id: Optional[str] = None
    parent_event_id: Optional[str] = None
    timestamp: float = 0.0
    version: str = "1.0"
    source: str = ""
    retry_count: int = 0
    session_id: str = ""


@dataclass
class StoredState:
    """Persisted state transition."""

    id: int = 0
    from_state: str = ""
    to_state: str = ""
    timestamp: float = 0.0
    duration_in_previous: float = 0.0
    reason: str = ""
    session_id: str = ""


@dataclass
class StoredTrade:
    """Persisted trade record with full context snapshot."""

    id: int = 0
    trade_id: str = ""
    asset: str = ""
    direction: str = ""
    amount: float = 0.0
    payout: float = 0.0
    duration: int = 0
    strategy: str = ""
    score: float = 0.0
    confidence: float = 0.0
    regime: str = ""
    session_name: str = ""
    profile: str = ""
    mode: str = ""
    context_status: str = ""
    risk_status: str = ""
    state_at_entry: str = ""
    latency_ms: float = 0.0
    result: str = ""  # win/loss/pending
    profit: float = 0.0
    block_reason: str = ""
    timestamp_requested: float = 0.0
    timestamp_executed: float = 0.0
    timestamp_result: float = 0.0
    correlation_id: str = ""
    session_id: str = ""
    extra: str = ""  # JSON for extensibility


@dataclass
class StoredBlock:
    """Record of a blocked trade/signal."""

    id: int = 0
    reason: str = ""
    source: str = ""
    details: str = ""  # JSON
    timestamp: float = 0.0
    session_id: str = ""


@dataclass
class StoredMetric:
    """Time-series metric snapshot."""

    id: int = 0
    metric_name: str = ""
    value: float = 0.0
    tags: str = ""  # JSON
    timestamp: float = 0.0
    session_id: str = ""
