"""
strategies/meta_scoring.py — Meta Scoring Engine for Syntrix v2.0.

Probabilistic contextual scoring that replaces fixed/binary scoring.

Meta Score evaluates:
- Signal score
- Regime factor
- Payout factor
- Volatility factor
- OTC quality factor
- Hour/session factor
- Historical performance factor
- Asset quality factor
- Latency factor
- Strategy consensus factor

confidence_final = signal_score * regime * payout * asset_quality
                   * volatility * latency * ...
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("syntrix.meta_scoring")


@dataclass
class MetaScoreResult:
    """Result of meta scoring calculation."""

    raw_score: float = 0.0
    meta_score: float = 0.0
    confidence: float = 0.0
    factors: Dict[str, float] = field(default_factory=dict)
    passed: bool = False
    threshold: float = 0.0
    reason: str = ""
    strategy: str = ""
    asset: str = ""


class MetaScoringEngine:
    """
    Probabilistic contextual scoring engine.

    Instead of fixed score + threshold, uses multiplicative factor model:
    confidence = base_score * factor1 * factor2 * ... * factorN

    Each factor is in range [0.0, 1.5]:
    - 1.0 = neutral (no effect)
    - > 1.0 = positive (boosts score)
    - < 1.0 = negative (reduces score)
    """

    def __init__(
        self,
        base_threshold: float = 0.45,
        regime_weight: float = 1.0,
        payout_weight: float = 1.0,
        quality_weight: float = 1.0,
    ) -> None:
        self._base_threshold = base_threshold
        self._regime_weight = regime_weight
        self._payout_weight = payout_weight
        self._quality_weight = quality_weight
        self._asset_history: Dict[str, List[bool]] = {}

    def calculate(
        self,
        base_score: float,
        regime: str = "",
        market_phase: str = "",
        payout: float = 0.0,
        volatility: float = 0.5,
        otc_quality: float = 1.0,
        asset_quality: float = 0.5,
        hour_utc: int = -1,
        latency_ms: float = 0.0,
        strategy_consensus: int = 1,
        historical_winrate: float = 0.5,
        asset: str = "",
        strategy: str = "",
        adaptive_threshold: float = -1.0,
    ) -> MetaScoreResult:
        """
        Calculate probabilistic meta score.

        All factors multiply together to produce final confidence.
        """
        factors: Dict[str, float] = {}

        # Base signal score (0-1)
        factors["signal"] = base_score

        # Regime factor
        factors["regime"] = self._regime_factor(regime, market_phase)

        # Payout factor
        factors["payout"] = self._payout_factor(payout)

        # Asset quality factor
        factors["asset_quality"] = self._quality_factor(asset_quality)

        # OTC quality factor
        factors["otc_quality"] = self._otc_factor(otc_quality, asset)

        # Volatility factor
        factors["volatility"] = self._volatility_factor(volatility)

        # Hour/session factor
        factors["hour"] = self._hour_factor(hour_utc, asset)

        # Latency factor
        factors["latency"] = self._latency_factor(latency_ms)

        # Consensus factor
        factors["consensus"] = self._consensus_factor(strategy_consensus)

        # Historical performance factor
        factors["history"] = self._history_factor(historical_winrate)

        # Calculate meta score (multiplicative)
        meta_score = 1.0
        for name, factor in factors.items():
            meta_score *= factor
        meta_score = round(max(0.0, min(1.0, meta_score)), 4)

        # Apply adaptive threshold
        threshold = adaptive_threshold if adaptive_threshold >= 0 else self._base_threshold

        passed = meta_score >= threshold

        result = MetaScoreResult(
            raw_score=round(base_score, 4),
            meta_score=meta_score,
            confidence=meta_score,
            factors=factors,
            passed=passed,
            threshold=threshold,
            reason=f"Meta {meta_score:.4f} {'≥' if passed else '<'} {threshold:.3f}",
            strategy=strategy,
            asset=asset,
        )

        logger.debug(
            "MetaScore %s/%s: %.3f -> %.4f (threshold=%.3f, %s) factors=%s",
            asset, strategy, base_score, meta_score, threshold,
            "PASS" if passed else "REJECT",
            {k: f"{v:.3f}" for k, v in factors.items()},
        )

        return result

    def _regime_factor(self, regime: str, market_phase: str = "") -> float:
        """Regime and market phase factor."""
        factor = 1.0

        favorable_regimes = {"trend", "range"}
        unfavorable_regimes = {"unstable", "unknown"}
        if regime in favorable_regimes:
            factor = 1.15
        elif regime in unfavorable_regimes:
            factor = 0.70
        elif regime == "high_volatility":
            factor = 0.85

        # Market phase adjustments
        favorable_phases = {"expansion", "trend"}
        risky_phases = {"exhaustion", "panic", "fake_breakout", "otc_noise"}
        neutral_phases = {"accumulation", "compression", "distribution"}

        if market_phase in favorable_phases:
            factor *= 1.10
        elif market_phase in risky_phases:
            factor *= 0.65
        elif market_phase in neutral_phases:
            factor *= 0.90

        return round(max(0.3, min(1.5, factor)), 3)

    def _payout_factor(self, payout: float) -> float:
        """Payout quality factor."""
        if payout >= 0.85:
            return 1.20
        if payout >= 0.75:
            return 1.05
        if payout >= 0.65:
            return 0.90
        if payout >= 0.55:
            return 0.75
        return 0.50

    def _quality_factor(self, quality: float) -> float:
        """Asset quality factor."""
        if quality >= 0.8:
            return 1.15
        if quality >= 0.6:
            return 1.00
        if quality >= 0.4:
            return 0.85
        return 0.65

    def _otc_factor(self, otc_quality: float, asset: str = "") -> float:
        """OTC quality factor."""
        if not asset.endswith("-OTC"):
            return 1.0
        if otc_quality >= 0.9:
            return 1.05
        if otc_quality >= 0.7:
            return 0.95
        if otc_quality >= 0.5:
            return 0.80
        return 0.60

    def _volatility_factor(self, volatility: float) -> float:
        """Volatility factor (sweet spot = moderate)."""
        if 0.3 <= volatility <= 0.7:
            return 1.05
        if 0.2 <= volatility <= 0.8:
            return 0.95
        if 0.1 <= volatility <= 0.9:
            return 0.85
        return 0.70

    def _hour_factor(self, hour_utc: int, asset: str = "") -> float:
        """Time-of-day factor."""
        if hour_utc < 0:
            return 1.0

        # London/NY overlap (13-16 UTC) = best
        if 13 <= hour_utc <= 16:
            return 1.15
        # London (7-12 UTC) = good
        if 7 <= hour_utc <= 12:
            return 1.05
        # NY afternoon (17-20 UTC) = moderate
        if 17 <= hour_utc <= 20:
            return 0.95
        # Dead zone for regular forex
        if not asset.endswith("-OTC"):
            if hour_utc >= 22 or hour_utc < 6:
                return 0.70
        # OTC is always available
        return 0.90

    def _latency_factor(self, latency_ms: float) -> float:
        """Broker latency factor."""
        if latency_ms <= 100:
            return 1.05
        if latency_ms <= 300:
            return 1.0
        if latency_ms <= 500:
            return 0.90
        if latency_ms <= 800:
            return 0.75
        return 0.55

    def _consensus_factor(self, consensus: int) -> float:
        """Strategy consensus factor (more strategies agreeing = better)."""
        if consensus >= 3:
            return 1.20
        if consensus >= 2:
            return 1.10
        return 1.0

    def _history_factor(self, winrate: float) -> float:
        """Historical performance factor."""
        if winrate >= 0.65:
            return 1.15
        if winrate >= 0.55:
            return 1.05
        if winrate >= 0.45:
            return 0.95
        if winrate >= 0.35:
            return 0.80
        return 0.65
