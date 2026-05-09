"""
strategies/scoring.py — Contextual scoring engine for Syntrix.

Adjusts raw strategy scores based on contextual factors:
- Market regime compatibility
- Payout quality
- Volatility conditions
- Historical strategy reliability
- Overall context quality
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from context.market_regime import MarketRegime
from strategies.base import StrategySignal

logger = logging.getLogger("syntrix.scoring")


class ScoringEngine:
    """
    Contextual scoring engine — adjusts strategy signals based on context.

    Does NOT generate signals. Only re-scores them.
    """

    def __init__(
        self,
        regime_bonus: float = 0.15,
        regime_penalty: float = -0.30,
        high_payout_bonus: float = 0.10,
        low_payout_penalty: float = -0.15,
        min_final_score: float = 0.55,
    ) -> None:
        self._regime_bonus = regime_bonus
        self._regime_penalty = regime_penalty
        self._high_payout_bonus = high_payout_bonus
        self._low_payout_penalty = low_payout_penalty
        self._min_final_score = min_final_score
        self._strategy_history: Dict[str, List[bool]] = {}

    def update_history(self, strategy_name: str, won: bool) -> None:
        """Track strategy win/loss for reliability scoring."""
        history = self._strategy_history.setdefault(strategy_name, [])
        history.append(won)
        if len(history) > 100:
            self._strategy_history[strategy_name] = history[-50:]

    def score(
        self,
        signal: StrategySignal,
        regime: MarketRegime,
        payout: float,
        volatility_percentile: float = 0.5,
        context_quality: float = 1.0,
    ) -> Dict[str, Any]:
        """
        Re-score a strategy signal with contextual adjustments.

        Args:
            signal: Raw strategy signal
            regime: Current market regime
            payout: Current payout (0.0 - 1.0)
            volatility_percentile: Current volatility relative to history (0-1)
            context_quality: Overall context quality (0-1)

        Returns:
            Dict with: final_score, adjustments, passed, reason
        """
        raw_score = signal.score
        adjustments: Dict[str, float] = {}

        regime_adj = 0.0
        if regime in signal.ideal_regimes:
            regime_adj = self._regime_bonus
        elif signal.ideal_regimes:
            regime_adj = self._regime_penalty
        adjustments["regime"] = round(regime_adj, 3)

        payout_adj = 0.0
        if payout >= 0.85:
            payout_adj = self._high_payout_bonus
        elif payout < 0.72:
            payout_adj = self._low_payout_penalty
        adjustments["payout"] = round(payout_adj, 3)

        reliability = self._get_reliability(signal.strategy_name)
        reliability_adj = (reliability - 0.5) * 0.2  # -0.1 to +0.1
        adjustments["reliability"] = round(reliability_adj, 3)

        context_adj = (context_quality - 0.5) * 0.1  # -0.05 to +0.05
        adjustments["context"] = round(context_adj, 3)

        vol_adj = 0.0
        if volatility_percentile > 0.9:
            vol_adj = -0.10
        elif volatility_percentile < 0.1:
            vol_adj = -0.05
        adjustments["volatility"] = round(vol_adj, 3)

        total_adj = sum(adjustments.values())
        final_score = max(0.0, min(1.0, raw_score + total_adj))

        passed = final_score >= self._min_final_score

        result = {
            "raw_score": round(raw_score, 3),
            "final_score": round(final_score, 3),
            "total_adjustment": round(total_adj, 3),
            "adjustments": adjustments,
            "passed": passed,
            "min_required": self._min_final_score,
            "strategy": signal.strategy_name,
            "reason": f"Score {final_score:.3f} {'≥' if passed else '<'} {self._min_final_score}",
        }

        logger.debug(
            "Scoring %s: %.3f -> %.3f (adj=%.3f, %s)",
            signal.strategy_name,
            raw_score,
            final_score,
            total_adj,
            "PASS" if passed else "REJECT",
        )

        return result

    def _get_reliability(self, strategy_name: str) -> float:
        """Get historical reliability of a strategy (0-1)."""
        history = self._strategy_history.get(strategy_name, [])
        if len(history) < 5:
            return 0.5  # neutral
        wins = sum(1 for w in history if w)
        return wins / len(history)
