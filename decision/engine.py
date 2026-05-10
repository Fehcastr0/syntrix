"""
decision/engine.py — Decision Engine for Syntrix v2.0.

The DecisionEngine is the central intelligence layer.
It does NOT execute trades directly.

Responsibilities:
- Compare opportunities across all assets
- Weight context factors
- Apply adaptive weights
- Resolve strategy conflicts
- Choose the single best trade
- Block low-quality signals
- Prioritize strong assets
- Apply contextual intelligence

Pipeline:
MULTI-ASSET SCAN -> RAW SIGNALS -> META SCORE -> OPPORTUNITY RANKING
-> DECISION ENGINE -> RISK ENGINE -> EXECUTION -> RESULT
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from context.decision_trace import (
    BlockReason, BlockRecord, BlockSeverity,
    DecisionTrace, TraceCollector, TraceStage,
)

logger = logging.getLogger("syntrix.decision")


@dataclass
class Opportunity:
    """A ranked trading opportunity."""

    asset: str = ""
    direction: str = ""
    strategy: str = ""
    meta_score: float = 0.0
    confidence: float = 0.0
    payout: float = 0.0
    regime: str = ""
    market_phase: str = ""
    asset_quality: float = 0.0
    otc_quality: float = 1.0
    latency_ms: float = 0.0
    volatility: float = 0.0
    timestamp: float = 0.0
    signal: Any = None
    trace: Optional[DecisionTrace] = None
    rank: int = 0
    rank_score: float = 0.0
    individual_scores: Dict[str, float] = field(default_factory=dict)
    block_reasons: List[str] = field(default_factory=list)
    decision: str = "pending"  # pending / execute / reject


@dataclass
class DecisionResult:
    """Result of the decision engine evaluation."""

    selected: Optional[Opportunity] = None
    candidates: List[Opportunity] = field(default_factory=list)
    rejected: List[Opportunity] = field(default_factory=list)
    total_scanned: int = 0
    total_candidates: int = 0
    total_rejected: int = 0
    decision_time_ms: float = 0.0
    correlation_id: str = ""
    reason: str = ""


class DecisionEngine:
    """
    Central decision intelligence.

    Compares, ranks, and selects the single best opportunity
    from all available candidates across all assets.
    """

    def __init__(
        self,
        min_confidence: float = 0.40,
        min_meta_score: float = 0.45,
        max_candidates: int = 50,
        consensus_bonus: float = 0.10,
    ) -> None:
        self._min_confidence = min_confidence
        self._min_meta_score = min_meta_score
        self._max_candidates = max_candidates
        self._consensus_bonus = consensus_bonus
        self._decision_history: List[DecisionResult] = []
        self._trace_collector = TraceCollector()

    def evaluate(
        self,
        opportunities: List[Opportunity],
        risk_budget: float = 1.0,
        correlation_id: str = "",
    ) -> DecisionResult:
        """
        Evaluate all opportunities and select the best one.

        Args:
            opportunities: List of scored opportunities from all assets
            risk_budget: Available risk budget (0-1, 1=full budget)
            correlation_id: Trace correlation ID

        Returns:
            DecisionResult with selected opportunity or None
        """
        start = time.time()
        correlation_id = correlation_id or str(uuid.uuid4())[:8]

        result = DecisionResult(
            correlation_id=correlation_id,
            total_scanned=len(opportunities),
        )

        if not opportunities:
            result.reason = "No opportunities available"
            result.decision_time_ms = (time.time() - start) * 1000
            return result

        # Phase 1: Filter out unqualified
        qualified: List[Opportunity] = []
        for opp in opportunities:
            reasons = self._check_minimum_quality(opp, risk_budget)
            if reasons:
                opp.decision = "reject"
                opp.block_reasons = reasons
                result.rejected.append(opp)
            else:
                qualified.append(opp)

        if not qualified:
            result.reason = f"All {len(opportunities)} opportunities rejected"
            result.total_rejected = len(result.rejected)
            result.decision_time_ms = (time.time() - start) * 1000
            logger.info("Decision: no qualified opportunities (%d rejected)", len(result.rejected))
            return result

        # Phase 2: Check for strategy consensus
        self._apply_consensus_bonus(qualified)

        # Phase 3: Rank by composite score
        qualified.sort(key=lambda x: x.rank_score, reverse=True)
        for i, opp in enumerate(qualified):
            opp.rank = i + 1

        result.candidates = qualified
        result.total_candidates = len(qualified)
        result.total_rejected = len(result.rejected)

        # Phase 4: Select TOP 1
        best = qualified[0]
        best.decision = "execute"
        result.selected = best
        result.reason = (
            f"Selected {best.asset} {best.direction} "
            f"(rank_score={best.rank_score:.3f}, meta={best.meta_score:.3f}, "
            f"confidence={best.confidence:.2f})"
        )

        result.decision_time_ms = (time.time() - start) * 1000

        # Store decision
        if len(self._decision_history) > 500:
            self._decision_history = self._decision_history[-250:]
        self._decision_history.append(result)

        logger.info(
            "Decision: %s %s %s (rank=%.3f, meta=%.3f, payout=%.0f%%) "
            "from %d candidates, %d rejected",
            best.asset, best.direction, best.strategy,
            best.rank_score, best.meta_score, best.payout * 100,
            len(qualified), len(result.rejected),
        )

        return result

    def _check_minimum_quality(self, opp: Opportunity, risk_budget: float) -> List[str]:
        """Check if opportunity meets minimum requirements."""
        reasons: List[str] = []

        if opp.meta_score < self._min_meta_score:
            reasons.append(f"LOW_META_SCORE ({opp.meta_score:.3f} < {self._min_meta_score})")

        if opp.confidence < self._min_confidence:
            reasons.append(f"LOW_CONFIDENCE ({opp.confidence:.2f} < {self._min_confidence})")

        if opp.payout < 0.50:
            reasons.append(f"LOW_PAYOUT ({opp.payout:.0%})")

        if risk_budget <= 0:
            reasons.append("NO_RISK_BUDGET")

        return reasons

    def _apply_consensus_bonus(self, opportunities: List[Opportunity]) -> None:
        """Apply bonus when multiple strategies agree on same asset+direction."""
        groups: Dict[str, List[Opportunity]] = {}
        for opp in opportunities:
            key = f"{opp.asset}_{opp.direction}"
            groups.setdefault(key, []).append(opp)

        for key, group in groups.items():
            if len(group) > 1:
                bonus = self._consensus_bonus * min(3, len(group) - 1)
                for opp in group:
                    opp.rank_score += bonus
                    opp.rank_score = round(opp.rank_score, 4)

    @property
    def recent_decisions(self) -> List[DecisionResult]:
        return self._decision_history[-20:]

    def get_decision_stats(self) -> Dict[str, Any]:
        """Get statistics about recent decisions."""
        if not self._decision_history:
            return {"total_decisions": 0}

        recent = self._decision_history[-50:]
        selected_count = sum(1 for d in recent if d.selected is not None)
        avg_candidates = sum(d.total_candidates for d in recent) / len(recent)
        avg_rejected = sum(d.total_rejected for d in recent) / len(recent)

        return {
            "total_decisions": len(self._decision_history),
            "recent_selection_rate": round(selected_count / len(recent) * 100, 1),
            "avg_candidates": round(avg_candidates, 1),
            "avg_rejected": round(avg_rejected, 1),
            "avg_decision_time_ms": round(
                sum(d.decision_time_ms for d in recent) / len(recent), 1
            ),
        }
