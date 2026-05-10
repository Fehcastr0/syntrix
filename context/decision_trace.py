"""
context/decision_trace.py — Decision trace pipeline for Syntrix.

Records the full pipeline of each operational attempt:
SCAN -> REGIME -> STRATEGY -> SCORE -> CONTEXT -> RISK -> EXECUTION -> RESULT

Each stage records: timestamp, duration, score, confidence, threshold, reason, decision.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("syntrix.trace")


class BlockReason(Enum):
    """Standardized block reason codes."""

    LOW_SCORE = "LOW_SCORE"
    REGIME_UNSTABLE = "REGIME_UNSTABLE"
    SESSION_BLOCKED = "SESSION_BLOCKED"
    SPIKE_DETECTED = "SPIKE_DETECTED"
    LATENCY_HIGH = "LATENCY_HIGH"
    COOLDOWN_ACTIVE = "COOLDOWN_ACTIVE"
    SAFE_MODE = "SAFE_MODE"
    MAX_TRADES = "MAX_TRADES"
    DRAWDOWN_LIMIT = "DRAWDOWN_LIMIT"
    LOW_PAYOUT = "LOW_PAYOUT"
    BROKER_UNHEALTHY = "BROKER_UNHEALTHY"
    NEWS_BLOCKED = "NEWS_BLOCKED"
    RISK_FAILED = "RISK_FAILED"
    NO_SIGNAL = "NO_SIGNAL"
    OTC_TOXIC = "OTC_TOXIC"


class BlockSeverity(Enum):
    """Severity of a block."""

    HARD = "hard"
    SOFT = "soft"


# Hard blocks: cannot be overridden by confidence
HARD_BLOCK_REASONS = {
    BlockReason.BROKER_UNHEALTHY,
    BlockReason.DRAWDOWN_LIMIT,
    BlockReason.SAFE_MODE,
    BlockReason.MAX_TRADES,
}


@dataclass
class TraceStage:
    """Single stage in the decision pipeline."""

    name: str
    timestamp: float = 0.0
    duration_ms: float = 0.0
    decision: str = ""
    score: float = 0.0
    confidence: float = 0.0
    threshold: float = 0.0
    reason: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BlockRecord:
    """Structured block record."""

    reason_code: BlockReason
    severity: BlockSeverity
    details: str = ""
    module: str = ""
    correlation_id: str = ""
    modifier: float = 1.0


@dataclass
class DecisionTrace:
    """Full pipeline trace for one asset evaluation."""

    asset: str = ""
    correlation_id: str = ""
    timestamp: float = 0.0
    stages: List[TraceStage] = field(default_factory=list)
    blocks: List[BlockRecord] = field(default_factory=list)
    final_decision: str = "BLOCK"
    final_confidence: float = 0.0
    total_duration_ms: float = 0.0

    def add_stage(self, stage: TraceStage) -> None:
        self.stages.append(stage)

    def add_block(self, block: BlockRecord) -> None:
        self.blocks.append(block)

    @property
    def hard_blocks(self) -> List[BlockRecord]:
        return [b for b in self.blocks if b.severity == BlockSeverity.HARD]

    @property
    def soft_blocks(self) -> List[BlockRecord]:
        return [b for b in self.blocks if b.severity == BlockSeverity.SOFT]

    @property
    def is_hard_blocked(self) -> bool:
        return len(self.hard_blocks) > 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "asset": self.asset,
            "correlation_id": self.correlation_id,
            "timestamp": self.timestamp,
            "final_decision": self.final_decision,
            "final_confidence": round(self.final_confidence, 3),
            "total_duration_ms": round(self.total_duration_ms, 1),
            "hard_blocks": len(self.hard_blocks),
            "soft_blocks": len(self.soft_blocks),
            "stages": [
                {
                    "name": s.name,
                    "decision": s.decision,
                    "score": round(s.score, 3),
                    "threshold": round(s.threshold, 3),
                    "reason": s.reason,
                    "duration_ms": round(s.duration_ms, 1),
                }
                for s in self.stages
            ],
            "blocks": [
                {
                    "reason_code": b.reason_code.value,
                    "severity": b.severity.value,
                    "details": b.details,
                    "module": b.module,
                    "modifier": round(b.modifier, 3),
                }
                for b in self.blocks
            ],
        }

    def format_diagnostic(self) -> str:
        """Format trace as human-readable diagnostic output."""
        lines = [
            f"\n{'='*50}",
            f"  {self.asset}  |  {self.final_decision}  |  confidence={self.final_confidence:.2f}",
            f"{'='*50}",
        ]
        for stage in self.stages:
            marker = "PASS" if stage.decision in ("ALLOW", "PASS", "OK") else stage.decision
            lines.append(
                f"  {stage.name:<12} {marker:<8} "
                f"score={stage.score:.2f}  thresh={stage.threshold:.2f}  "
                f"({stage.duration_ms:.0f}ms)  {stage.reason}"
            )
        if self.blocks:
            lines.append(f"  {'─'*46}")
            for b in self.blocks:
                sev = "HARD" if b.severity == BlockSeverity.HARD else "SOFT"
                lines.append(
                    f"  [{sev}] {b.reason_code.value}: {b.details} (mod={b.modifier:.2f})"
                )
        lines.append("")
        return "\n".join(lines)


class TraceCollector:
    """Collects and stores decision traces for analysis."""

    def __init__(self, max_traces: int = 500) -> None:
        self._traces: List[DecisionTrace] = []
        self._max = max_traces

    def add(self, trace: DecisionTrace) -> None:
        self._traces.append(trace)
        if len(self._traces) > self._max:
            self._traces = self._traces[-self._max // 2:]

    @property
    def traces(self) -> List[DecisionTrace]:
        return list(self._traces)

    def recent(self, n: int = 10) -> List[DecisionTrace]:
        return self._traces[-n:]

    def block_rate_by_reason(self) -> Dict[str, int]:
        """Count blocks by reason code."""
        counts: Dict[str, int] = {}
        for trace in self._traces:
            for block in trace.blocks:
                key = block.reason_code.value
                counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items(), key=lambda x: -x[1]))

    def avg_confidence(self) -> float:
        if not self._traces:
            return 0.0
        return sum(t.final_confidence for t in self._traces) / len(self._traces)

    def allow_rate(self) -> float:
        if not self._traces:
            return 0.0
        allowed = sum(1 for t in self._traces if t.final_decision == "ALLOW")
        return allowed / len(self._traces)
