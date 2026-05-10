"""
replay/engine.py — Replay engine for Syntrix.

Implements:
- Full session replay from event store
- Per-trade replay with context reconstruction
- Temporal timeline reconstruction
- Causal tracing via correlation_id
- Event-by-event playback with timing
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from core.events import Event, EventBus, EventType

logger = logging.getLogger("syntrix.replay")


@dataclass
class ReplayEvent:
    """Single event in a replay timeline."""

    event_id: str
    event_type: str
    data: Dict[str, Any]
    timestamp: float
    correlation_id: Optional[str] = None
    causation_id: Optional[str] = None
    relative_time_ms: float = 0.0


@dataclass
class ReplaySession:
    """Reconstructed replay session."""

    session_id: str
    events: List[ReplayEvent] = field(default_factory=list)
    start_time: float = 0.0
    end_time: float = 0.0
    duration_sec: float = 0.0
    trade_count: int = 0
    state_changes: int = 0
    summary: Dict[str, Any] = field(default_factory=dict)


ReplayCallback = Callable[[ReplayEvent, int, int], None]


class ReplayEngine:
    """
    Replay engine — reconstructs and replays event streams.

    Can replay:
    - Full sessions
    - Individual trade lifecycles
    - Causal chains
    - Filtered event streams
    """

    def __init__(self, event_bus: Optional[EventBus] = None) -> None:
        self._event_bus = event_bus
        self._current_replay: Optional[ReplaySession] = None
        self._replaying = False

    @property
    def is_replaying(self) -> bool:
        return self._replaying

    def build_session_replay(
        self, events: List[Dict[str, Any]], session_id: str = ""
    ) -> ReplaySession:
        """
        Build a replay session from stored event dicts.

        Args:
            events: List of event dicts from event store (ordered by timestamp)
            session_id: Session identifier

        Returns:
            ReplaySession with timeline and summary
        """
        if not events:
            return ReplaySession(session_id=session_id)

        replay_events: List[ReplayEvent] = []
        start_time = events[0].get("timestamp", 0.0)
        trade_count = 0
        state_changes = 0

        for evt in events:
            ts = evt.get("timestamp", 0.0)
            event_type = evt.get("event_type", "")

            replay_events.append(
                ReplayEvent(
                    event_id=evt.get("event_id", ""),
                    event_type=event_type,
                    data=self._parse_data(evt.get("data", "{}")),
                    timestamp=ts,
                    correlation_id=evt.get("correlation_id"),
                    causation_id=evt.get("causation_id"),
                    relative_time_ms=round((ts - start_time) * 1000, 1),
                )
            )

            if event_type == EventType.TRADE_EXECUTED.value:
                trade_count += 1
            if event_type == EventType.STATE_CHANGED.value:
                state_changes += 1

        end_time = events[-1].get("timestamp", 0.0)

        session = ReplaySession(
            session_id=session_id,
            events=replay_events,
            start_time=start_time,
            end_time=end_time,
            duration_sec=round(end_time - start_time, 2),
            trade_count=trade_count,
            state_changes=state_changes,
            summary=self._build_summary(replay_events),
        )

        self._current_replay = session
        logger.info(
            "Replay session built: %d events, %.1fs duration, %d trades",
            len(replay_events),
            session.duration_sec,
            trade_count,
        )
        return session

    def build_trade_replay(
        self, events: List[Dict[str, Any]], correlation_id: str
    ) -> ReplaySession:
        """Build a replay for a single trade lifecycle."""
        filtered = [e for e in events if e.get("correlation_id") == correlation_id]
        return self.build_session_replay(filtered, session_id=f"trade-{correlation_id}")

    def build_causal_chain(
        self, events: List[Dict[str, Any]], root_event_id: str
    ) -> List[ReplayEvent]:
        """
        Trace the causal chain from a root event.

        Follows causation_id links to build a directed graph.
        """
        by_id: Dict[str, Dict[str, Any]] = {}
        children: Dict[str, List[str]] = {}

        for evt in events:
            eid = evt.get("event_id", "")
            by_id[eid] = evt
            cid = evt.get("causation_id")
            if cid:
                children.setdefault(cid, []).append(eid)

        chain: List[ReplayEvent] = []
        visited: set[str] = set()

        def traverse(eid: str) -> None:
            if eid in visited or eid not in by_id:
                return
            visited.add(eid)
            evt = by_id[eid]
            chain.append(
                ReplayEvent(
                    event_id=eid,
                    event_type=evt.get("event_type", ""),
                    data=self._parse_data(evt.get("data", "{}")),
                    timestamp=evt.get("timestamp", 0.0),
                    correlation_id=evt.get("correlation_id"),
                    causation_id=evt.get("causation_id"),
                )
            )
            for child_id in children.get(eid, []):
                traverse(child_id)

        traverse(root_event_id)
        chain.sort(key=lambda e: e.timestamp)

        logger.info("Causal chain from %s: %d events", root_event_id, len(chain))
        return chain

    def replay_with_callback(
        self,
        session: ReplaySession,
        callback: ReplayCallback,
        speed: float = 1.0,
        real_time: bool = False,
    ) -> None:
        """
        Replay a session, calling callback for each event.

        Args:
            session: ReplaySession to replay
            callback: Called with (event, index, total) for each event
            speed: Playback speed multiplier (1.0 = real time)
            real_time: If True, sleep between events to match original timing
        """
        if not session.events:
            return

        self._replaying = True
        total = len(session.events)

        if self._event_bus:
            self._event_bus.emit(
                EventType.REPLAY_START,
                data={"session_id": session.session_id, "event_count": total},
            )

        try:
            for i, event in enumerate(session.events):
                if not self._replaying:
                    logger.info("Replay stopped at event %d/%d", i, total)
                    break

                callback(event, i, total)

                if self._event_bus:
                    self._event_bus.emit(
                        EventType.REPLAY_EVENT,
                        data={
                            "index": i,
                            "total": total,
                            "event_type": event.event_type,
                            "relative_time_ms": event.relative_time_ms,
                        },
                    )

                if real_time and i < total - 1:
                    next_evt = session.events[i + 1]
                    delay = (next_evt.timestamp - event.timestamp) / speed
                    if delay > 0:
                        time.sleep(min(delay, 5.0))

        finally:
            self._replaying = False
            if self._event_bus:
                self._event_bus.emit(
                    EventType.REPLAY_END,
                    data={"session_id": session.session_id, "events_replayed": i + 1},
                )

    def stop_replay(self) -> None:
        """Stop the current replay."""
        self._replaying = False

    def get_event_type_distribution(self, session: ReplaySession) -> Dict[str, int]:
        """Get distribution of event types in a session."""
        dist: Dict[str, int] = {}
        for evt in session.events:
            dist[evt.event_type] = dist.get(evt.event_type, 0) + 1
        return dict(sorted(dist.items(), key=lambda x: x[1], reverse=True))

    def get_timeline(self, session: ReplaySession) -> List[Dict[str, Any]]:
        """Get simplified timeline for display."""
        return [
            {
                "time_ms": evt.relative_time_ms,
                "type": evt.event_type,
                "data_summary": {k: v for k, v in list(evt.data.items())[:5]},
            }
            for evt in session.events
        ]

    @staticmethod
    def _parse_data(data: Any) -> Dict[str, Any]:
        """Parse data field — handle both str and dict."""
        if isinstance(data, dict):
            return data
        if isinstance(data, str):
            import json
            try:
                return json.loads(data)
            except (json.JSONDecodeError, TypeError):
                return {"raw": data}
        return {}

    @staticmethod
    def _build_summary(events: List[ReplayEvent]) -> Dict[str, Any]:
        """Build a summary of the replay events."""
        types: Dict[str, int] = {}
        for evt in events:
            types[evt.event_type] = types.get(evt.event_type, 0) + 1
        return {
            "total_events": len(events),
            "event_types": len(types),
            "type_distribution": types,
        }
