"""
analytics/operation_report.py — 'POR QUE NAO OPEROU' automatic report.

Tracks ALL decisions and generates detailed block analysis:
- block_rate_by_reason
- allow_rate
- confidence_distribution
- threshold_hit_rate
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("syntrix.report")


@dataclass
class CycleRecord:
    """Record of a single scan cycle."""

    timestamp: float = 0.0
    assets_scanned: int = 0
    signals_generated: int = 0
    signals_allowed: int = 0
    signals_blocked: int = 0
    trade_executed: bool = False
    best_confidence: float = 0.0
    best_meta_score: float = 0.0
    best_asset: str = ""
    block_reasons: List[str] = field(default_factory=list)


class OperationReport:
    """
    Tracks operation metrics and generates block analysis.

    Answers: WHY is the bot not trading?
    """

    def __init__(self) -> None:
        self._cycles: List[CycleRecord] = []
        self._block_counts: Dict[str, int] = defaultdict(int)
        self._allow_count = 0
        self._total_signals = 0
        self._confidences_allowed: List[float] = []
        self._confidences_blocked: List[float] = []
        self._meta_scores: List[float] = []
        self._start_time = time.time()

    def record_cycle(self, cycle: CycleRecord) -> None:
        """Record a complete scan cycle."""
        self._cycles.append(cycle)

        self._total_signals += cycle.signals_generated
        self._allow_count += cycle.signals_allowed

        for reason in cycle.block_reasons:
            self._block_counts[reason] += 1

        if cycle.best_confidence > 0:
            if cycle.signals_allowed > 0:
                self._confidences_allowed.append(cycle.best_confidence)
            else:
                self._confidences_blocked.append(cycle.best_confidence)

        if cycle.best_meta_score > 0:
            self._meta_scores.append(cycle.best_meta_score)

    def record_signal(
        self, allowed: bool, confidence: float, block_reason: str = ""
    ) -> None:
        """Record a single signal evaluation."""
        self._total_signals += 1
        if allowed:
            self._allow_count += 1
            self._confidences_allowed.append(confidence)
        else:
            self._confidences_blocked.append(confidence)
            if block_reason:
                for reason in block_reason.split(", "):
                    r = reason.strip()
                    if r:
                        self._block_counts[r] += 1

    def get_stats(self) -> Dict[str, Any]:
        """Get operation statistics."""
        total = self._total_signals
        blocked = total - self._allow_count
        runtime_min = (time.time() - self._start_time) / 60

        return {
            "runtime_minutes": round(runtime_min, 1),
            "total_cycles": len(self._cycles),
            "total_signals": total,
            "signals_allowed": self._allow_count,
            "signals_blocked": blocked,
            "allow_rate": self._allow_count / total if total > 0 else 0,
            "block_rate": blocked / total if total > 0 else 0,
            "trades_executed": sum(1 for c in self._cycles if c.trade_executed),
            "avg_confidence_allowed": (
                sum(self._confidences_allowed) / len(self._confidences_allowed)
                if self._confidences_allowed else 0
            ),
            "avg_confidence_blocked": (
                sum(self._confidences_blocked) / len(self._confidences_blocked)
                if self._confidences_blocked else 0
            ),
            "block_reasons": dict(sorted(
                self._block_counts.items(), key=lambda x: x[1], reverse=True
            )),
        }

    def format_report(self) -> str:
        """Generate formatted operation report."""
        stats = self.get_stats()
        lines = [
            "=" * 60,
            "  OPERATION REPORT — POR QUE NAO OPEROU",
            "=" * 60,
            f"  Runtime: {stats['runtime_minutes']:.0f} minutes",
            f"  Cycles: {stats['total_cycles']}",
            f"  Total signals: {stats['total_signals']}",
            f"  Allowed: {stats['signals_allowed']} ({stats['allow_rate']:.0%})",
            f"  Blocked: {stats['signals_blocked']} ({stats['block_rate']:.0%})",
            f"  Trades executed: {stats['trades_executed']}",
            "",
        ]

        if stats["avg_confidence_allowed"] > 0:
            lines.append(
                f"  Avg confidence (allowed): {stats['avg_confidence_allowed']:.3f}"
            )
        if stats["avg_confidence_blocked"] > 0:
            lines.append(
                f"  Avg confidence (blocked): {stats['avg_confidence_blocked']:.3f}"
            )

        if stats["block_reasons"]:
            lines.append("")
            lines.append("  BLOCK REASONS (most frequent):")
            lines.append("  " + "-" * 45)
            total_blocks = stats["signals_blocked"]
            for reason, count in list(stats["block_reasons"].items())[:15]:
                pct = count / total_blocks * 100 if total_blocks > 0 else 0
                bar = "#" * min(30, int(pct / 3))
                lines.append(f"    {reason:25s} {count:4d} ({pct:4.0f}%) {bar}")

        # Recommendations
        lines.append("")
        lines.append("  RECOMMENDATIONS:")
        if stats["allow_rate"] < 0.05:
            lines.append("    ! Allow rate < 5% — system is too restrictive")
        if stats["allow_rate"] < 0.10:
            lines.append("    ! Consider lowering thresholds")
        if stats.get("avg_confidence_blocked", 0) > 0.3:
            lines.append("    ! Many blocked signals have decent confidence — check soft blocks")

        if stats["block_reasons"]:
            top_reason = list(stats["block_reasons"].keys())[0]
            top_count = list(stats["block_reasons"].values())[0]
            if total_blocks > 0 and top_count / total_blocks > 0.3:
                lines.append(f"    ! '{top_reason}' causes {top_count/total_blocks:.0%} of blocks")

        lines.append("")
        lines.append("=" * 60)
        return "\n".join(lines)
