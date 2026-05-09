"""
strategies/range_reversal.py — Range reversal strategy for Syntrix.

Trades reversals at range boundaries using Bollinger Bands and RSI.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from brokers.base import Candle, TradeDirection
from context.market_regime import MarketRegime
from indicators.technical import bollinger_bands, rsi, stochastic
from strategies.base import BaseStrategy, StrategySignal


class RangeReversalStrategy(BaseStrategy):
    """
    Range reversal: enters at extremes of a ranging market.

    Logic:
    1. Price touches or pierces Bollinger Band
    2. RSI at extreme (oversold/overbought)
    3. Stochastic confirms reversal
    """

    def __init__(
        self,
        bb_period: int = 20,
        bb_std: float = 2.0,
        rsi_period: int = 14,
        rsi_oversold: float = 30.0,
        rsi_overbought: float = 70.0,
        stoch_period: int = 14,
    ) -> None:
        super().__init__(name="range_reversal")
        self._bb_period = bb_period
        self._bb_std = bb_std
        self._rsi_period = rsi_period
        self._rsi_os = rsi_oversold
        self._rsi_ob = rsi_overbought
        self._stoch_period = stoch_period

    def ideal_regimes(self) -> List[MarketRegime]:
        return [MarketRegime.RANGE, MarketRegime.LOW_VOLATILITY]

    def evaluate(
        self,
        candles: List[Candle],
        regime: MarketRegime,
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[StrategySignal]:
        min_needed = max(self._bb_period, self._rsi_period, self._stoch_period) + 5
        if len(candles) < min_needed:
            return None

        bb = bollinger_bands(candles, self._bb_period, self._bb_std)
        rsi_vals = rsi(candles, self._rsi_period)
        stoch = stochastic(candles, self._stoch_period)

        if not bb["upper"] or not rsi_vals or not stoch["k"]:
            return None

        price = candles[-1].close
        upper = bb["upper"][-1]
        lower = bb["lower"][-1]
        middle = bb["middle"][-1]
        current_rsi = rsi_vals[-1]
        current_k = stoch["k"][-1]
        prev_k = stoch["k"][-2] if len(stoch["k"]) > 1 else current_k

        band_width = upper - lower
        if band_width <= 0:
            return None

        # Oversold reversal (CALL)
        if price <= lower and current_rsi <= self._rsi_os:
            stoch_turning = current_k > prev_k and current_k < 30
            if stoch_turning or candles[-1].is_bullish:
                position = (price - lower) / band_width
                score = min(1.0, (self._rsi_os - current_rsi) / 30 + abs(position) * 0.3)
                return StrategySignal(
                    strategy_name=self.name,
                    direction=TradeDirection.CALL,
                    score=round(score, 3),
                    confidence=round(min(1.0, score * 0.75), 3),
                    ideal_regimes=self.ideal_regimes(),
                    context={
                        "price": round(price, 6),
                        "bb_lower": round(lower, 6),
                        "bb_upper": round(upper, 6),
                        "rsi": round(current_rsi, 2),
                        "stoch_k": round(current_k, 2),
                    },
                    reason="Oversold at lower BB — reversal signal",
                )

        # Overbought reversal (PUT)
        if price >= upper and current_rsi >= self._rsi_ob:
            stoch_turning = current_k < prev_k and current_k > 70
            if stoch_turning or candles[-1].is_bearish:
                position = (price - upper) / band_width
                score = min(1.0, (current_rsi - self._rsi_ob) / 30 + abs(position) * 0.3)
                return StrategySignal(
                    strategy_name=self.name,
                    direction=TradeDirection.PUT,
                    score=round(score, 3),
                    confidence=round(min(1.0, score * 0.75), 3),
                    ideal_regimes=self.ideal_regimes(),
                    context={
                        "price": round(price, 6),
                        "bb_lower": round(lower, 6),
                        "bb_upper": round(upper, 6),
                        "rsi": round(current_rsi, 2),
                        "stoch_k": round(current_k, 2),
                    },
                    reason="Overbought at upper BB — reversal signal",
                )

        return None
