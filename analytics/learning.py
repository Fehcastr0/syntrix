"""
analytics/learning.py — Adaptive Learning Layer for Syntrix.

Continuously adapts:
- Strategy weights based on real performance
- Threshold adjustments based on calibration
- Asset priorities based on edge discovery
- Score adjustments based on recent outcomes

The system learns from every trade and adapts accordingly.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("syntrix.learning")


@dataclass
class LearningState:
    """Current state of the learning system."""

    strategy_weights: Dict[str, float] = field(default_factory=dict)
    asset_adjustments: Dict[str, float] = field(default_factory=dict)
    threshold_adjustment: float = 0.0
    score_bias: float = 0.0
    total_updates: int = 0
    last_update: float = 0.0


class AdaptiveLearningLayer:
    """
    Learns from trade outcomes and adjusts system parameters.

    Key adaptations:
    1. Strategy weights: increase for winners, decrease for losers
    2. Threshold: lower if too many blocks, raise if too many losses
    3. Asset priority: boost profitable assets
    4. Score bias: correct systematic over/under prediction
    """

    def __init__(
        self,
        learning_rate: float = 0.05,
        min_weight: float = 0.3,
        max_weight: float = 1.5,
        window_size: int = 50,
        recalibrate_every: int = 20,
    ) -> None:
        self._lr = learning_rate
        self._min_weight = min_weight
        self._max_weight = max_weight
        self._window_size = window_size
        self._recalibrate_every = recalibrate_every

        self._state = LearningState()
        self._history: List[Dict[str, Any]] = []
        self._strategy_history: Dict[str, List[bool]] = defaultdict(list)
        self._asset_history: Dict[str, List[bool]] = defaultdict(list)
        self._updates_since_recal = 0

    def record_outcome(
        self,
        strategy: str,
        asset: str,
        won: bool,
        confidence: float,
        meta_score: float,
        payout: float,
        regime: str = "",
        hour_utc: int = -1,
    ) -> None:
        """Record a trade outcome and update learning state."""
        self._history.append({
            "strategy": strategy,
            "asset": asset,
            "won": won,
            "confidence": confidence,
            "meta_score": meta_score,
            "payout": payout,
            "regime": regime,
            "hour_utc": hour_utc,
            "timestamp": time.time(),
        })

        # Keep window
        if len(self._history) > self._window_size * 5:
            self._history = self._history[-self._window_size * 5:]

        self._strategy_history[strategy].append(won)
        if len(self._strategy_history[strategy]) > self._window_size:
            self._strategy_history[strategy] = self._strategy_history[strategy][-self._window_size:]

        self._asset_history[asset].append(won)
        if len(self._asset_history[asset]) > self._window_size:
            self._asset_history[asset] = self._asset_history[asset][-self._window_size:]

        self._updates_since_recal += 1
        self._state.total_updates += 1
        self._state.last_update = time.time()

        # Update strategy weight
        self._update_strategy_weight(strategy)

        # Update asset adjustment
        self._update_asset_adjustment(asset)

        # Periodic recalibration
        if self._updates_since_recal >= self._recalibrate_every:
            self._recalibrate()
            self._updates_since_recal = 0

    def _update_strategy_weight(self, strategy: str) -> None:
        """Adjust strategy weight based on recent performance."""
        history = self._strategy_history.get(strategy, [])
        if len(history) < 5:
            return

        recent = history[-min(len(history), self._window_size):]
        wr = sum(1 for w in recent if w) / len(recent)

        current = self._state.strategy_weights.get(strategy, 1.0)

        # Move toward performance
        if wr > 0.55:
            target = min(self._max_weight, 1.0 + (wr - 0.55) * 2)
        elif wr < 0.45:
            target = max(self._min_weight, 1.0 - (0.45 - wr) * 2)
        else:
            target = 1.0

        new_weight = current + self._lr * (target - current)
        new_weight = max(self._min_weight, min(self._max_weight, new_weight))
        self._state.strategy_weights[strategy] = round(new_weight, 3)

    def _update_asset_adjustment(self, asset: str) -> None:
        """Adjust asset threshold/priority based on performance."""
        history = self._asset_history.get(asset, [])
        if len(history) < 5:
            return

        recent = history[-min(len(history), self._window_size):]
        wr = sum(1 for w in recent if w) / len(recent)

        # Threshold adjustment: lower for good assets, higher for bad
        if wr > 0.55:
            adj = -0.03 * (wr - 0.55) / 0.10
        elif wr < 0.45:
            adj = 0.05 * (0.45 - wr) / 0.10
        else:
            adj = 0.0

        current = self._state.asset_adjustments.get(asset, 0.0)
        new_adj = current + self._lr * (adj - current)
        self._state.asset_adjustments[asset] = round(new_adj, 4)

    def _recalibrate(self) -> None:
        """Periodic recalibration of global parameters."""
        if len(self._history) < 10:
            return

        recent = self._history[-self._window_size:]

        # Check if scores are calibrated
        avg_confidence = sum(h["confidence"] for h in recent) / len(recent)
        actual_wr = sum(1 for h in recent if h["won"]) / len(recent)

        # Score bias: if we predict 0.65 but actual is 0.55, bias = -0.10
        self._state.score_bias = round(actual_wr - avg_confidence, 3)

        # Threshold adjustment
        # If too many losses -> raise threshold
        # If very few trades -> lower threshold
        if actual_wr < 0.45 and len(recent) >= 20:
            self._state.threshold_adjustment = min(0.10, self._state.threshold_adjustment + 0.01)
        elif actual_wr > 0.55:
            self._state.threshold_adjustment = max(-0.10, self._state.threshold_adjustment - 0.01)
        else:
            # Slowly return to 0
            self._state.threshold_adjustment *= 0.95

        self._state.threshold_adjustment = round(self._state.threshold_adjustment, 4)

        logger.info(
            "Learning recalibration: bias=%.3f threshold_adj=%.3f wr=%.1f%% (n=%d)",
            self._state.score_bias, self._state.threshold_adjustment,
            actual_wr * 100, len(recent),
        )

    def get_strategy_weight(self, strategy: str) -> float:
        """Get current adaptive weight for a strategy."""
        return self._state.strategy_weights.get(strategy, 1.0)

    def get_asset_threshold_adj(self, asset: str) -> float:
        """Get threshold adjustment for an asset."""
        return self._state.asset_adjustments.get(asset, 0.0)

    def get_threshold_adjustment(self) -> float:
        """Get global threshold adjustment."""
        return self._state.threshold_adjustment

    def get_score_bias(self) -> float:
        """Get score bias correction."""
        return self._state.score_bias

    def get_state(self) -> LearningState:
        """Get current learning state."""
        return self._state

    def format_report(self) -> str:
        """Format learning state report."""
        lines = [
            "=" * 60,
            "  ADAPTIVE LEARNING STATE",
            "=" * 60,
            f"  Total updates: {self._state.total_updates}",
            f"  Score bias: {self._state.score_bias:+.3f}",
            f"  Threshold adj: {self._state.threshold_adjustment:+.4f}",
            "",
        ]

        if self._state.strategy_weights:
            lines.append("  STRATEGY WEIGHTS:")
            for name, weight in sorted(self._state.strategy_weights.items()):
                marker = "+" if weight > 1.0 else ("-" if weight < 1.0 else " ")
                lines.append(f"    {marker} {name:25s}: {weight:.3f}")
            lines.append("")

        if self._state.asset_adjustments:
            active = {k: v for k, v in self._state.asset_adjustments.items() if abs(v) > 0.001}
            if active:
                lines.append("  ASSET ADJUSTMENTS:")
                for name, adj in sorted(active.items(), key=lambda x: x[1]):
                    lines.append(f"    {name:20s}: {adj:+.4f}")

        lines.append("=" * 60)
        return "\n".join(lines)
