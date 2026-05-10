"""
strategies/trend_pullback.py — Trend pullback strategy for Syntrix.

Identifies pullbacks within established trends and generates
entry signals in the trend direction.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from brokers.base import Candle, TradeDirection
from context.market_regime import MarketRegime
from indicators.technical import ema, rsi
from strategies.base import BaseStrategy, StrategySignal


class TrendPullbackStrategy(BaseStrategy):
    """
    Trend pullback: enters after a short pullback in a strong trend.

    Logic:
    1. Confirm trend via EMA alignment (fast > slow = uptrend)
    2. Detect pullback via RSI dipping into neutral zone
    3. Confirm pullback reversal with price action
    """

    def __init__(
        self,
        ema_fast: int = 9,
        ema_slow: int = 21,
        rsi_period: int = 14,
        rsi_pullback_zone: tuple[float, float] = (40, 60),
        min_trend_strength: float = 0.3,
    ) -> None:
        super().__init__(name="trend_pullback")
        self._ema_fast = ema_fast
        self._ema_slow = ema_slow
        self._rsi_period = rsi_period
        self._rsi_zone = rsi_pullback_zone
        self._min_strength = min_trend_strength

    def ideal_regimes(self) -> List[MarketRegime]:
        return [MarketRegime.TREND]

    def evaluate(
        self,
        candles: List[Candle],
        regime: MarketRegime,
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[StrategySignal]:
        if len(candles) < self._ema_slow + 5:
            return None

        fast_ema = ema(candles, self._ema_fast)
        slow_ema = ema(candles, self._ema_slow)
        rsi_vals = rsi(candles, self._rsi_period)

        if not fast_ema or not slow_ema or not rsi_vals:
            return None

        current_fast = fast_ema[-1]
        current_slow = slow_ema[-1]
        current_rsi = rsi_vals[-1]
        prev_rsi = rsi_vals[-2] if len(rsi_vals) > 1 else current_rsi

        ema_diff = (current_fast - current_slow) / current_slow if current_slow > 0 else 0

        uptrend = ema_diff > self._min_strength / 100
        downtrend = ema_diff < -self._min_strength / 100

        if not uptrend and not downtrend:
            return None

        if uptrend:
            in_pullback = self._rsi_zone[0] <= current_rsi <= self._rsi_zone[1]
            recovering = current_rsi > prev_rsi
            last_candle_bullish = candles[-1].is_bullish

            if in_pullback and recovering and last_candle_bullish:
                score = min(1.0, abs(ema_diff) * 50 + (current_rsi - self._rsi_zone[0]) / 20)
                return StrategySignal(
                    strategy_name=self.name,
                    direction=TradeDirection.CALL,
                    score=round(score, 3),
                    confidence=round(min(1.0, score * 0.8), 3),
                    ideal_regimes=self.ideal_regimes(),
                    context={
                        "ema_fast": round(current_fast, 6),
                        "ema_slow": round(current_slow, 6),
                        "ema_diff_pct": round(ema_diff * 100, 4),
                        "rsi": round(current_rsi, 2),
                        "trend": "up",
                    },
                    reason="Bullish pullback in uptrend — RSI recovering",
                )

        if downtrend:
            in_pullback = self._rsi_zone[0] <= current_rsi <= self._rsi_zone[1]
            declining = current_rsi < prev_rsi
            last_candle_bearish = candles[-1].is_bearish

            if in_pullback and declining and last_candle_bearish:
                score = min(1.0, abs(ema_diff) * 50 + (self._rsi_zone[1] - current_rsi) / 20)
                return StrategySignal(
                    strategy_name=self.name,
                    direction=TradeDirection.PUT,
                    score=round(score, 3),
                    confidence=round(min(1.0, score * 0.8), 3),
                    ideal_regimes=self.ideal_regimes(),
                    context={
                        "ema_fast": round(current_fast, 6),
                        "ema_slow": round(current_slow, 6),
                        "ema_diff_pct": round(ema_diff * 100, 4),
                        "rsi": round(current_rsi, 2),
                        "trend": "down",
                    },
                    reason="Bearish pullback in downtrend — RSI declining",
                )

        return None
