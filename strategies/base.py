"""
strategies/base.py — Base strategy interface for Syntrix.

All strategies must implement this interface.
Strategies are pure signal generators — they NEVER execute trades directly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from brokers.base import Candle, TradeDirection
from context.market_regime import MarketRegime


@dataclass
class StrategySignal:
    """Signal emitted by a strategy."""

    strategy_name: str
    direction: TradeDirection
    score: float  # 0.0 to 1.0
    confidence: float  # 0.0 to 1.0
    ideal_regimes: List[MarketRegime] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""


class BaseStrategy(ABC):
    """
    Abstract base strategy.

    Each strategy:
    - Receives candles + regime + context
    - Returns Optional[StrategySignal]
    - NEVER executes trades
    - NEVER interacts with the broker
    """

    def __init__(self, name: str) -> None:
        self.name = name

    @abstractmethod
    def evaluate(
        self,
        candles: List[Candle],
        regime: MarketRegime,
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[StrategySignal]:
        """
        Evaluate candles and return a signal if conditions are met.

        Returns None if no signal.
        """
        ...

    @abstractmethod
    def ideal_regimes(self) -> List[MarketRegime]:
        """Return list of market regimes this strategy works best in."""
        ...

    def is_regime_compatible(self, regime: MarketRegime) -> bool:
        """Check if current regime is compatible with this strategy."""
        ideals = self.ideal_regimes()
        if not ideals:
            return True
        return regime in ideals
