"""
strategies/scoring.py — Contextual scoring engine for Syntrix v2.

Now supports:
- Ensemble scoring: multiple strategies contribute to final score
- Adaptive strategy weights based on recent performance
- Per-strategy cooldown tracking
- Regime-aware weight adjustments
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from context.market_regime import MarketRegime
from strategies.base import StrategySignal

logger = logging.getLogger("syntrix.scoring")


class ScoringEngine:
    """
    Contextual scoring engine with ensemble support.

    Supports single signal scoring (backward compatible) and
    ensemble scoring from multiple strategies.
    """

    def __init__(
        self,
        regime_bonus: float = 0.15,
        regime_penalty: float = -0.20,
        high_payout_bonus: float = 0.10,
        low_payout_penalty: float = -0.10,
        min_final_score: float = 0.50,
    ) -> None:
        self._regime_bonus = regime_bonus
        self._regime_penalty = regime_penalty
        self._high_payout_bonus = high_payout_bonus
        self._low_payout_penalty = low_payout_penalty
        self._min_final_score = min_final_score
        self._strategy_history: Dict[str, List[bool]] = {}
        self._strategy_weights: Dict[str, float] = {}
        self._strategy_cooldowns: Dict[str, float] = {}  # per-strategy
        self._direction_cooldowns: Dict[str, float] = {}  # per-direction

    def update_history(self, strategy_name: str, won: bool) -> None:
        """Track strategy win/loss and update adaptive weight."""
        history = self._strategy_history.setdefault(strategy_name, [])
        history.append(won)
        if len(history) > 100:
            self._strategy_history[strategy_name] = history[-50:]
        self._update_weight(strategy_name)

    def record_strategy_trade(self, strategy_name: str, direction: str = "") -> None:
        """Record that a strategy executed a trade (for cooldown)."""
        now = time.time()
        self._strategy_cooldowns[strategy_name] = now
        if direction:
            self._direction_cooldowns[direction] = now

    def is_strategy_on_cooldown(self, strategy_name: str, cooldown_sec: float = 60.0) -> bool:
        """Check if a strategy is on cooldown."""
        last = self._strategy_cooldowns.get(strategy_name, 0)
        return (time.time() - last) < cooldown_sec

    def _update_weight(self, strategy_name: str) -> None:
        """Update strategy weight based on recent performance."""
        history = self._strategy_history.get(strategy_name, [])
        if len(history) < 5:
            self._strategy_weights[strategy_name] = 1.0
            return
        recent = history[-20:]
        winrate = sum(1 for w in recent if w) / len(recent)
        # Weight ranges from 0.5 to 1.5 based on winrate
        weight = 0.5 + winrate
        self._strategy_weights[strategy_name] = round(weight, 3)

    def get_weight(self, strategy_name: str) -> float:
        """Get current weight for a strategy."""
        return self._strategy_weights.get(strategy_name, 1.0)

    def score(
        self,
        signal: StrategySignal,
        regime: MarketRegime,
        payout: float,
        volatility_percentile: float = 0.5,
        context_quality: float = 1.0,
    ) -> Dict[str, Any]:
        """
        Score a single strategy signal with contextual adjustments.

        Args:
            signal: Raw strategy signal
            regime: Current market regime
            payout: Current payout (0.0 - 1.0)
            volatility_percentile: Current volatility (0-1)
            context_quality: Overall context quality (0-1)

        Returns:
            Dict with: final_score, adjustments, passed, reason
        """
        raw_score = signal.score
        adjustments: Dict[str, float] = {}

        # Regime adjustment
        regime_adj = 0.0
        if regime in signal.ideal_regimes:
            regime_adj = self._regime_bonus
        elif signal.ideal_regimes:
            regime_adj = self._regime_penalty
        adjustments["regime"] = round(regime_adj, 3)

        # Payout adjustment
        payout_adj = 0.0
        if payout >= 0.85:
            payout_adj = self._high_payout_bonus
        elif payout < 0.72:
            payout_adj = self._low_payout_penalty
        adjustments["payout"] = round(payout_adj, 3)

        # Reliability
        reliability = self._get_reliability(signal.strategy_name)
        reliability_adj = (reliability - 0.5) * 0.2
        adjustments["reliability"] = round(reliability_adj, 3)

        # Context quality
        context_adj = (context_quality - 0.5) * 0.1
        adjustments["context"] = round(context_adj, 3)

        # Volatility
        vol_adj = 0.0
        if volatility_percentile > 0.9:
            vol_adj = -0.08
        elif volatility_percentile < 0.1:
            vol_adj = -0.03
        adjustments["volatility"] = round(vol_adj, 3)

        # Strategy weight
        weight = self.get_weight(signal.strategy_name)
        weight_adj = (weight - 1.0) * 0.1
        adjustments["strategy_weight"] = round(weight_adj, 3)

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
            "strategy_weight": weight,
            "reason": f"Score {final_score:.3f} {'≥' if passed else '<'} {self._min_final_score}",
        }

        logger.debug(
            "Scoring %s: %.3f -> %.3f (adj=%.3f, weight=%.2f, %s)",
            signal.strategy_name,
            raw_score,
            final_score,
            total_adj,
            weight,
            "PASS" if passed else "REJECT",
        )

        return result

    def score_ensemble(
        self,
        signals: List[StrategySignal],
        regime: MarketRegime,
        payout: float,
        context_quality: float = 1.0,
    ) -> Dict[str, Any]:
        """
        Ensemble scoring: multiple strategies contribute to final score.

        The final score is a weighted average of individual scores.
        """
        if not signals:
            return {
                "final_score": 0.0,
                "passed": False,
                "reason": "No signals",
                "individual_scores": {},
                "best_strategy": "",
                "best_direction": "",
            }

        individual_scores: Dict[str, float] = {}
        weighted_sum = 0.0
        weight_total = 0.0
        best_score = 0.0
        best_signal: Optional[StrategySignal] = None

        for signal in signals:
            result = self.score(signal, regime, payout, context_quality=context_quality)
            score = result["final_score"]
            weight = self.get_weight(signal.strategy_name)
            individual_scores[signal.strategy_name] = score
            weighted_sum += score * weight
            weight_total += weight

            if score > best_score:
                best_score = score
                best_signal = signal

        ensemble_score = weighted_sum / weight_total if weight_total > 0 else 0.0
        ensemble_score = round(max(0.0, min(1.0, ensemble_score)), 3)
        passed = ensemble_score >= self._min_final_score

        return {
            "final_score": ensemble_score,
            "passed": passed,
            "reason": f"Ensemble {ensemble_score:.3f} {'≥' if passed else '<'} {self._min_final_score}",
            "individual_scores": individual_scores,
            "best_strategy": best_signal.strategy_name if best_signal else "",
            "best_direction": best_signal.direction.value if best_signal else "",
            "best_signal": best_signal,
            "strategy_count": len(signals),
        }

    def _get_reliability(self, strategy_name: str) -> float:
        """Get historical reliability of a strategy (0-1)."""
        history = self._strategy_history.get(strategy_name, [])
        if len(history) < 5:
            return 0.5
        wins = sum(1 for w in history if w)
        return wins / len(history)

    def get_strategy_stats(self) -> Dict[str, Dict[str, Any]]:
        """Get stats for all tracked strategies."""
        stats: Dict[str, Dict[str, Any]] = {}
        for name, history in self._strategy_history.items():
            wins = sum(1 for w in history if w)
            total = len(history)
            stats[name] = {
                "total_trades": total,
                "wins": wins,
                "winrate": round(wins / total * 100, 1) if total > 0 else 0,
                "weight": self.get_weight(name),
                "reliability": self._get_reliability(name),
            }
        return stats
