"""
context/market_regime.py — Market regime detector for Syntrix.

Classifies current market conditions into regimes:
- TREND (strong directional movement)
- RANGE (sideways consolidation)
- HIGH_VOLATILITY (large swings, unstable)
- LOW_VOLATILITY (tight range, low movement)
- UNSTABLE (conflicting signals, unreliable)

Strategies depend on the detected regime to decide whether to operate.
"""

from __future__ import annotations

import logging
import statistics
from enum import Enum, auto
from typing import Any, Dict, List, Optional

from brokers.base import Candle

logger = logging.getLogger("syntrix.context.regime")


class MarketRegime(Enum):
    """Detected market regime classification."""

    TREND = "trend"
    RANGE = "range"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"
    UNSTABLE = "unstable"
    UNKNOWN = "unknown"


class MarketRegimeDetector:
    """
    Detects the current market regime from candle data.

    Uses a combination of:
    - ADX-like directional strength (via price slope)
    - ATR relative to price
    - Consecutive directional candle count
    - Range width relative to ATR
    """

    def __init__(
        self,
        trend_threshold: float = 0.6,
        volatility_high_mult: float = 1.5,
        volatility_low_mult: float = 0.5,
        min_candles: int = 20,
    ) -> None:
        self._trend_threshold = trend_threshold
        self._vol_high_mult = volatility_high_mult
        self._vol_low_mult = volatility_low_mult
        self._min_candles = min_candles
        self._last_regime = MarketRegime.UNKNOWN
        self._regime_history: List[MarketRegime] = []

    @property
    def current_regime(self) -> MarketRegime:
        return self._last_regime

    @property
    def history(self) -> List[MarketRegime]:
        return list(self._regime_history)

    def detect(self, candles: List[Candle]) -> MarketRegime:
        """
        Analyze candles and classify market regime.

        Args:
            candles: List of recent candles (oldest first)

        Returns:
            Detected MarketRegime
        """
        if len(candles) < self._min_candles:
            logger.debug("Not enough candles (%d < %d)", len(candles), self._min_candles)
            return MarketRegime.UNKNOWN

        atr = self._calc_atr(candles)
        if atr <= 0:
            return MarketRegime.UNKNOWN

        avg_price = statistics.mean(c.close for c in candles)
        relative_vol = atr / avg_price if avg_price > 0 else 0
        historical_vol = self._calc_historical_volatility(candles)

        directional_strength = self._calc_directional_strength(candles)
        range_ratio = self._calc_range_ratio(candles, atr)

        regime = self._classify(
            directional_strength=directional_strength,
            relative_vol=relative_vol,
            historical_vol=historical_vol,
            range_ratio=range_ratio,
            atr=atr,
        )

        self._last_regime = regime
        self._regime_history.append(regime)
        if len(self._regime_history) > 200:
            self._regime_history = self._regime_history[-100:]

        logger.debug(
            "Regime: %s (dir_str=%.3f, rel_vol=%.5f, range_ratio=%.3f)",
            regime.value,
            directional_strength,
            relative_vol,
            range_ratio,
        )
        return regime

    def get_context(self, candles: List[Candle]) -> Dict[str, Any]:
        """Return full regime context dict for logging/tracing."""
        if len(candles) < self._min_candles:
            return {"regime": MarketRegime.UNKNOWN.value, "confidence": 0.0}

        atr = self._calc_atr(candles)
        avg_price = statistics.mean(c.close for c in candles)
        dir_str = self._calc_directional_strength(candles)
        regime = self.detect(candles)

        return {
            "regime": regime.value,
            "atr": round(atr, 6),
            "avg_price": round(avg_price, 6),
            "directional_strength": round(dir_str, 4),
            "relative_volatility": round(atr / avg_price if avg_price else 0, 6),
            "candles_analyzed": len(candles),
        }

    def _classify(
        self,
        directional_strength: float,
        relative_vol: float,
        historical_vol: float,
        range_ratio: float,
        atr: float,
    ) -> MarketRegime:
        """Core classification logic."""
        if historical_vol > 0 and relative_vol > 0:
            vol_ratio = relative_vol / historical_vol if historical_vol else 1.0
        else:
            vol_ratio = 1.0

        if vol_ratio > self._vol_high_mult:
            if directional_strength < 0.3:
                return MarketRegime.UNSTABLE
            return MarketRegime.HIGH_VOLATILITY

        if vol_ratio < self._vol_low_mult:
            return MarketRegime.LOW_VOLATILITY

        if directional_strength >= self._trend_threshold:
            return MarketRegime.TREND

        if range_ratio < 2.0 and directional_strength < 0.3:
            return MarketRegime.RANGE

        if directional_strength < 0.2 and vol_ratio > 1.2:
            return MarketRegime.UNSTABLE

        return MarketRegime.RANGE

    @staticmethod
    def _calc_atr(candles: List[Candle], period: int = 14) -> float:
        """Calculate Average True Range."""
        if len(candles) < 2:
            return 0.0
        trs: List[float] = []
        for i in range(1, len(candles)):
            high_low = candles[i].high - candles[i].low
            high_prev_close = abs(candles[i].high - candles[i - 1].close)
            low_prev_close = abs(candles[i].low - candles[i - 1].close)
            trs.append(max(high_low, high_prev_close, low_prev_close))

        if not trs:
            return 0.0

        window = trs[-period:]
        return statistics.mean(window)

    @staticmethod
    def _calc_directional_strength(candles: List[Candle]) -> float:
        """
        Calculate directional strength (0.0 = no direction, 1.0 = strong trend).
        Uses ratio of net movement to total movement.
        """
        if len(candles) < 2:
            return 0.0

        total_movement = sum(abs(candles[i].close - candles[i - 1].close) for i in range(1, len(candles)))
        net_movement = abs(candles[-1].close - candles[0].close)

        if total_movement == 0:
            return 0.0
        return net_movement / total_movement

    @staticmethod
    def _calc_range_ratio(candles: List[Candle], atr: float) -> float:
        """Calculate price range relative to ATR."""
        if atr <= 0:
            return 0.0
        highest = max(c.high for c in candles)
        lowest = min(c.low for c in candles)
        return (highest - lowest) / atr

    @staticmethod
    def _calc_historical_volatility(candles: List[Candle], period: int = 20) -> float:
        """Calculate historical volatility as stdev of returns."""
        if len(candles) < period + 1:
            return 0.0
        returns = []
        for i in range(1, len(candles)):
            if candles[i - 1].close > 0:
                returns.append((candles[i].close - candles[i - 1].close) / candles[i - 1].close)
        if len(returns) < 2:
            return 0.0
        return statistics.stdev(returns)
