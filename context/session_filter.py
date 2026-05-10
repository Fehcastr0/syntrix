"""
context/session_filter.py — Trading session validator for Syntrix.

Validates whether the current time falls within an acceptable trading session.
Supports: London, New York, Overlap, and dead zones.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("syntrix.context.session")


class TradingSession(Enum):
    """Known trading sessions."""

    LONDON = "london"
    NEW_YORK = "new_york"
    OVERLAP = "overlap"
    TOKYO = "tokyo"
    DEAD_ZONE = "dead_zone"
    CLOSE_PROXIMITY = "close_proximity"


SESSION_WINDOWS: Dict[TradingSession, Tuple[int, int]] = {
    TradingSession.TOKYO: (0, 9),       # 00:00 - 09:00 UTC
    TradingSession.LONDON: (7, 16),     # 07:00 - 16:00 UTC
    TradingSession.NEW_YORK: (13, 22),  # 13:00 - 22:00 UTC
    TradingSession.OVERLAP: (13, 16),   # 13:00 - 16:00 UTC (London + NY)
}

DEAD_ZONE_HOURS = {22, 23, 0, 1, 2, 3, 4, 5}

CLOSE_PROXIMITY_MINUTES = 10


class SessionFilter:
    """
    Validates current time against trading sessions.

    Rules:
    - Allows trading only during valid sessions
    - Blocks during dead zones
    - Warns near session close
    - Returns session name and validity
    """

    def __init__(
        self,
        allowed_sessions: Optional[list[TradingSession]] = None,
        block_dead_zone: bool = True,
        block_close_proximity: bool = True,
    ) -> None:
        self._allowed = allowed_sessions or [
            TradingSession.LONDON,
            TradingSession.NEW_YORK,
            TradingSession.OVERLAP,
        ]
        self._block_dead_zone = block_dead_zone
        self._block_close_proximity = block_close_proximity

    def evaluate(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Evaluate current session validity.

        Returns:
            Dict with keys: valid, session, reason, details
        """
        if now is None:
            now = datetime.now(timezone.utc)

        hour = now.hour
        minute = now.minute

        if self._block_dead_zone and hour in DEAD_ZONE_HOURS:
            return {
                "valid": False,
                "session": TradingSession.DEAD_ZONE.value,
                "reason": "Dead zone — low liquidity period",
                "hour_utc": hour,
            }

        current_sessions = self._get_active_sessions(hour)

        if not current_sessions:
            return {
                "valid": False,
                "session": TradingSession.DEAD_ZONE.value,
                "reason": "No active trading session",
                "hour_utc": hour,
            }

        allowed_active = [s for s in current_sessions if s in self._allowed]

        if not allowed_active:
            return {
                "valid": False,
                "session": current_sessions[0].value,
                "reason": f"Session {current_sessions[0].value} not in allowed list",
                "hour_utc": hour,
            }

        primary = self._get_primary_session(allowed_active)

        if self._block_close_proximity:
            session_end = SESSION_WINDOWS.get(primary, (0, 0))[1]
            if hour == session_end - 1 and minute >= (60 - CLOSE_PROXIMITY_MINUTES):
                return {
                    "valid": False,
                    "session": primary.value,
                    "reason": "Too close to session close",
                    "minutes_to_close": 60 - minute,
                    "hour_utc": hour,
                }

        return {
            "valid": True,
            "session": primary.value,
            "reason": "Session active",
            "all_active": [s.value for s in allowed_active],
            "hour_utc": hour,
        }

    @staticmethod
    def _get_active_sessions(hour: int) -> list[TradingSession]:
        """Get all sessions active at the given hour."""
        active = []
        for session, (start, end) in SESSION_WINDOWS.items():
            if start <= hour < end:
                active.append(session)
        return active

    @staticmethod
    def _get_primary_session(sessions: list[TradingSession]) -> TradingSession:
        """Pick the most relevant session from active ones."""
        if TradingSession.OVERLAP in sessions:
            return TradingSession.OVERLAP
        if TradingSession.NEW_YORK in sessions:
            return TradingSession.NEW_YORK
        if TradingSession.LONDON in sessions:
            return TradingSession.LONDON
        return sessions[0]
