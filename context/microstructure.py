"""
context/microstructure.py — Microstructure Engine for Syntrix v2.0.

Analyzes candle-level market microstructure:
- Wick ratio
- Candle efficiency
- Compression detection
- Fake breakout detection
- Micro range detection
- Impulsiveness
- ATR expansion
- Candle noise level

Used to add granularity to context decisions.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, List

from brokers.base import Candle

logger = logging.getLogger("syntrix.microstructure")


@dataclass
class MicrostructureAnalysis:
    """Result of microstructure analysis."""

    wick_ratio: float = 0.0
    candle_efficiency: float = 0.0
    compression_level: float = 0.0
    impulsiveness: float = 0.0
    atr_expansion: float = 1.0
    noise_level: float = 0.0
    micro_range: bool = False
    fake_breakout: bool = False
    quality_score: float = 0.5
    details: Dict[str, Any] = field(default_factory=dict)


class MicrostructureEngine:
    """
    Analyzes candle-level microstructure for trading quality assessment.

    Higher quality = cleaner price action = better execution environment.
    """

    def __init__(
        self,
        lookback: int = 20,
        wick_threshold: float = 3.0,
        compression_threshold: float = 0.40,
        noise_threshold: float = 0.60,
    ) -> None:
        self._lookback = lookback
        self._wick_threshold = wick_threshold
        self._compression_threshold = compression_threshold
        self._noise_threshold = noise_threshold

    def analyze(self, candles: List[Candle]) -> MicrostructureAnalysis:
        """Analyze microstructure of recent candles."""
        result = MicrostructureAnalysis()

        if len(candles) < 10:
            return result

        recent = candles[-self._lookback:]

        result.wick_ratio = self._calc_wick_ratio(recent)
        result.candle_efficiency = self._calc_candle_efficiency(recent)
        result.compression_level = self._calc_compression(recent)
        result.impulsiveness = self._calc_impulsiveness(recent)
        result.atr_expansion = self._calc_atr_expansion(candles)
        result.noise_level = self._calc_noise(recent)
        result.micro_range = self._detect_micro_range(recent)
        result.fake_breakout = self._detect_fake_breakout(recent)

        # Quality score (weighted combination)
        quality = 0.5
        # Good efficiency boosts quality
        quality += (result.candle_efficiency - 0.5) * 0.3
        # Low noise boosts quality
        quality += (1.0 - result.noise_level) * 0.2
        # Low wick ratio boosts quality
        wick_factor = max(0, 1.0 - result.wick_ratio / 5.0)
        quality += wick_factor * 0.15
        # Fake breakout penalizes
        if result.fake_breakout:
            quality -= 0.15
        # Micro range penalizes
        if result.micro_range:
            quality -= 0.10
        # ATR expansion (moderate is best)
        if 0.8 <= result.atr_expansion <= 1.5:
            quality += 0.05

        result.quality_score = round(max(0.0, min(1.0, quality)), 3)

        result.details = {
            "wick_ratio": round(result.wick_ratio, 3),
            "efficiency": round(result.candle_efficiency, 3),
            "compression": round(result.compression_level, 3),
            "impulse": round(result.impulsiveness, 3),
            "atr_exp": round(result.atr_expansion, 3),
            "noise": round(result.noise_level, 3),
        }

        return result

    def _calc_wick_ratio(self, candles: List[Candle]) -> float:
        """Average wick to body ratio."""
        ratios = []
        for c in candles:
            body = abs(c.close - c.open)
            if body < 0.000001:
                body = 0.000001
            upper_wick = c.high - max(c.open, c.close)
            lower_wick = min(c.open, c.close) - c.low
            total_wick = upper_wick + lower_wick
            ratios.append(total_wick / body)
        return statistics.mean(ratios) if ratios else 0.0

    def _calc_candle_efficiency(self, candles: List[Candle]) -> float:
        """Candle efficiency = body / range (0=doji, 1=marubozu)."""
        effs = []
        for c in candles:
            total = c.high - c.low
            if total < 0.000001:
                effs.append(0.0)
                continue
            body = abs(c.close - c.open)
            effs.append(body / total)
        return statistics.mean(effs) if effs else 0.0

    def _calc_compression(self, candles: List[Candle]) -> float:
        """Measure range compression (0=wide, 1=very compressed)."""
        ranges = [c.high - c.low for c in candles]
        if len(ranges) < 5:
            return 0.0

        recent_avg = statistics.mean(ranges[-5:])
        overall_avg = statistics.mean(ranges)

        if overall_avg == 0:
            return 0.0

        ratio = recent_avg / overall_avg
        # ratio < 1 = compressing
        compression = max(0.0, min(1.0, 1.0 - ratio))
        return compression

    def _calc_impulsiveness(self, candles: List[Candle]) -> float:
        """Measure impulsive movement (0=calm, 1=very impulsive)."""
        if len(candles) < 5:
            return 0.0

        # Compare last 3 candles to average
        bodies = [abs(c.close - c.open) for c in candles]
        avg_body = statistics.mean(bodies)
        if avg_body == 0:
            return 0.0

        recent_bodies = bodies[-3:]
        max_recent = max(recent_bodies)
        return min(1.0, max_recent / (avg_body * 3))

    def _calc_atr_expansion(self, candles: List[Candle]) -> float:
        """ATR expansion ratio (recent ATR / older ATR)."""
        if len(candles) < 20:
            return 1.0

        recent_atr = statistics.mean([c.high - c.low for c in candles[-10:]])
        older_atr = statistics.mean([c.high - c.low for c in candles[-20:-10]])

        if older_atr == 0:
            return 1.0

        return recent_atr / older_atr

    def _calc_noise(self, candles: List[Candle]) -> float:
        """Calculate noise level (micro-reversals / total)."""
        if len(candles) < 5:
            return 0.0

        reversals = 0
        for i in range(2, len(candles)):
            d1 = candles[i - 1].close - candles[i - 2].close
            d2 = candles[i].close - candles[i - 1].close
            if d1 * d2 < 0:
                reversals += 1

        return reversals / (len(candles) - 2)

    def _detect_micro_range(self, candles: List[Candle]) -> bool:
        """Detect very tight range (no tradable movement)."""
        if len(candles) < 5:
            return False

        recent_5 = candles[-5:]
        high = max(c.high for c in recent_5)
        low = min(c.low for c in recent_5)
        range_size = high - low

        avg_body = statistics.mean([abs(c.close - c.open) for c in recent_5])
        price = candles[-1].close if candles[-1].close > 0 else 1.0

        # Micro range if total range is less than 2x average body
        return range_size < avg_body * 2 and (range_size / price) < 0.0003

    def _detect_fake_breakout(self, candles: List[Candle]) -> bool:
        """Detect recent fake breakout pattern."""
        if len(candles) < 10:
            return False

        prior = candles[-10:-3]
        recent = candles[-3:]

        prior_high = max(c.high for c in prior)
        prior_low = min(c.low for c in prior)

        # Check if recent candles broke out then came back
        broke_high = any(c.high > prior_high * 1.001 for c in recent)
        broke_low = any(c.low < prior_low * 0.999 for c in recent)
        came_back = prior_low <= recent[-1].close <= prior_high

        return (broke_high or broke_low) and came_back
