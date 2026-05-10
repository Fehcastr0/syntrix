"""
context/news_filter.py — News-aware trade filter for Syntrix.

Blocks trading around high-impact economic news events.
Uses lightweight in-memory cache with safe fallback.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("syntrix.context.news")


@dataclass
class NewsEvent:
    """Scheduled economic news event."""

    title: str
    currency: str
    impact: str  # "high", "medium", "low"
    timestamp: float  # UTC epoch
    source: str = ""


class NewsFilter:
    """
    Blocks trading near high-impact news events.

    Features:
    - Configurable block window (minutes before/after)
    - In-memory event cache
    - Fallback: if no news data, returns CAUTION (not BLOCK)
    - Manual event injection for testing
    """

    DEFAULT_BLOCK_BEFORE_MIN = 15
    DEFAULT_BLOCK_AFTER_MIN = 10

    def __init__(
        self,
        block_before_minutes: int = DEFAULT_BLOCK_BEFORE_MIN,
        block_after_minutes: int = DEFAULT_BLOCK_AFTER_MIN,
        block_impacts: Optional[List[str]] = None,
    ) -> None:
        self._block_before = block_before_minutes * 60
        self._block_after = block_after_minutes * 60
        self._block_impacts = block_impacts or ["high"]
        self._events: List[NewsEvent] = []
        self._last_update: float = 0.0
        self._cache_ttl = 3600.0  # 1 hour

    @property
    def event_count(self) -> int:
        return len(self._events)

    def add_events(self, events: List[NewsEvent]) -> None:
        """Manually inject news events (for config or testing)."""
        self._events.extend(events)
        self._events.sort(key=lambda e: e.timestamp)
        self._last_update = time.time()
        logger.info("Added %d news events (total: %d)", len(events), len(self._events))

    def clear_events(self) -> None:
        """Clear all cached events."""
        self._events.clear()
        logger.info("News events cleared")

    def set_events_from_schedule(self, schedule: List[Dict[str, Any]]) -> None:
        """
        Load events from a schedule list of dicts.

        Expected dict keys: title, currency, impact, timestamp
        """
        events = []
        for item in schedule:
            try:
                events.append(
                    NewsEvent(
                        title=item.get("title", "Unknown"),
                        currency=item.get("currency", ""),
                        impact=item.get("impact", "low"),
                        timestamp=float(item["timestamp"]),
                        source=item.get("source", "schedule"),
                    )
                )
            except (KeyError, ValueError) as exc:
                logger.warning("Skipping invalid news event: %s", exc)
        self.add_events(events)

    def evaluate(
        self,
        asset: str = "",
        now: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Evaluate whether trading should be blocked due to news proximity.

        Returns:
            Dict with keys: blocked, reason, nearby_events, details
        """
        now = now or time.time()

        if not self._events:
            return {
                "blocked": False,
                "caution": True,
                "reason": "No news data available — proceed with caution",
                "nearby_events": [],
            }

        self._cleanup_old_events(now)

        nearby: List[Dict[str, Any]] = []
        blocking = False
        block_reason = ""

        for event in self._events:
            delta = event.timestamp - now
            abs_delta = abs(delta)

            is_before = delta > 0 and abs_delta <= self._block_before
            is_after = delta <= 0 and abs_delta <= self._block_after

            if is_before or is_after:
                if asset and event.currency and event.currency.upper() not in asset.upper():
                    continue

                nearby.append({
                    "title": event.title,
                    "currency": event.currency,
                    "impact": event.impact,
                    "delta_seconds": round(delta),
                    "position": "before" if delta > 0 else "after",
                })

                if event.impact in self._block_impacts:
                    blocking = True
                    position = "in" if delta <= 0 else "before"
                    minutes = round(abs_delta / 60)
                    block_reason = (
                        f"High-impact news: {event.title} ({event.currency}) "
                        f"— {minutes}min {position}"
                    )

        return {
            "blocked": blocking,
            "caution": len(nearby) > 0,
            "reason": block_reason if blocking else ("Nearby news — caution" if nearby else "Clear"),
            "nearby_events": nearby,
        }

    def _cleanup_old_events(self, now: float) -> None:
        """Remove events that are too old to matter."""
        cutoff = now - self._block_after - 300
        before = len(self._events)
        self._events = [e for e in self._events if e.timestamp > cutoff]
        removed = before - len(self._events)
        if removed > 0:
            logger.debug("Cleaned up %d old news events", removed)
