"""
logging/trade_logger.py — Structured trade logger for Syntrix.

Saves complete snapshots of every trade decision (executed or blocked)
with full context for auditability and replay.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("syntrix.trade_logger")


@dataclass
class TradeSnapshot:
    """Complete snapshot of a trade decision."""

    snapshot_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    timestamp: float = field(default_factory=time.time)

    # Trade details
    asset: str = ""
    direction: str = ""
    amount: float = 0.0
    duration: int = 0
    payout: float = 0.0

    # Strategy
    strategy: str = ""
    score: float = 0.0
    confidence: float = 0.0

    # Context
    regime: str = ""
    session: str = ""
    profile: str = ""
    mode: str = ""  # live/demo/dry-run
    context_decision: str = ""  # allow/caution/block

    # Risk
    risk_check: str = ""  # passed/failed
    risk_reasons: List[str] = field(default_factory=list)

    # State
    state_at_entry: str = ""
    pnl_at_entry: float = 0.0
    drawdown_at_entry: float = 0.0

    # Execution
    executed: bool = False
    block_reason: str = ""
    trade_id: str = ""
    broker_id: str = ""
    latency_ms: float = 0.0
    jitter_ms: float = 0.0

    # Result (filled later)
    result: str = ""  # win/loss/tie/pending
    profit: float = 0.0

    # Timing
    timestamp_signal: float = 0.0
    timestamp_context: float = 0.0
    timestamp_risk: float = 0.0
    timestamp_execution: float = 0.0
    timestamp_result: float = 0.0

    # Tracing
    correlation_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)


class TradeLogger:
    """
    Structured trade logger — persists trade snapshots to JSONL files.

    Features:
    - One line per trade in JSONL format
    - Daily rotation
    - In-memory buffer with batch flush
    - Query by session
    """

    def __init__(self, log_dir: str = "data/trade_logs", buffer_size: int = 10) -> None:
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._buffer: List[TradeSnapshot] = []
        self._buffer_size = buffer_size
        self._session_log: List[TradeSnapshot] = []

    @property
    def session_trades(self) -> List[TradeSnapshot]:
        return list(self._session_log)

    @property
    def session_count(self) -> int:
        return len(self._session_log)

    def log_trade(self, snapshot: TradeSnapshot) -> None:
        """Log a trade snapshot."""
        self._session_log.append(snapshot)
        self._buffer.append(snapshot)

        status = "EXECUTED" if snapshot.executed else f"BLOCKED ({snapshot.block_reason})"
        logger.info(
            "[%s] %s %s %s | score=%.2f | payout=%.0f%% | %s",
            snapshot.snapshot_id,
            snapshot.direction,
            snapshot.asset,
            snapshot.strategy,
            snapshot.score,
            snapshot.payout * 100,
            status,
        )

        if len(self._buffer) >= self._buffer_size:
            self.flush()

    def update_result(self, trade_id: str, result: str, profit: float) -> None:
        """Update a trade with its result (called when result arrives)."""
        for snap in reversed(self._session_log):
            if snap.trade_id == trade_id:
                snap.result = result
                snap.profit = profit
                snap.timestamp_result = time.time()
                logger.info(
                    "[%s] Result: %s ($%.2f)",
                    snap.snapshot_id,
                    result,
                    profit,
                )
                self._buffer.append(snap)
                if len(self._buffer) >= self._buffer_size:
                    self.flush()
                return
        logger.warning("Trade %s not found for result update", trade_id)

    def flush(self) -> None:
        """Flush buffer to disk."""
        if not self._buffer:
            return

        date_str = time.strftime("%Y-%m-%d")
        filepath = self._log_dir / f"trades_{date_str}.jsonl"

        try:
            with open(filepath, "a", encoding="utf-8") as f:
                for snap in self._buffer:
                    f.write(snap.to_json() + "\n")
            logger.debug("Flushed %d trade snapshots to %s", len(self._buffer), filepath)
            self._buffer.clear()
        except Exception as exc:
            logger.error("Failed to flush trade logs: %s", exc)

    def get_last_n(self, n: int = 10) -> List[TradeSnapshot]:
        """Get last N trade snapshots from this session."""
        return self._session_log[-n:]

    def get_summary(self) -> Dict[str, Any]:
        """Get session trade summary."""
        executed = [s for s in self._session_log if s.executed]
        blocked = [s for s in self._session_log if not s.executed]
        wins = [s for s in executed if s.result == "win"]
        losses = [s for s in executed if s.result == "loss"]
        total_profit = sum(s.profit for s in executed)

        return {
            "total_signals": len(self._session_log),
            "executed": len(executed),
            "blocked": len(blocked),
            "wins": len(wins),
            "losses": len(losses),
            "total_profit": round(total_profit, 2),
            "winrate": round(len(wins) / len(executed) * 100, 1) if executed else 0.0,
        }

    def close(self) -> None:
        """Flush remaining buffer and close."""
        self.flush()
        logger.info("Trade logger closed (%d trades this session)", len(self._session_log))
