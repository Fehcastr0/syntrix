"""
context/market_phase.py — Market Phase Detection for Syntrix v2.0.

Extends market regime with granular phase detection:
- accumulation
- expansion
- distribution
- exhaustion
- panic
- compression
- fake_breakout
- low_liquidity
- otc_noise

Each phase affects trading strategy selection and scoring.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

from brokers.base import Candle

logger = logging.getLogger("syntrix.market_phase")


class MarketPhase(Enum):
    """Granular market phase classification."""

    UNKNOWN = "unknown"
    ACCUMULATION = "accumulation"
    EXPANSION = "expansion"
    DISTRIBUTION = "distribution"
    EXHAUSTION = "exhaustion"
    PANIC = "panic"
    COMPRESSION = "compression"
    FAKE_BREAKOUT = "fake_breakout"
    LOW_LIQUIDITY = "low_liquidity"
    OTC_NOISE = "otc_noise"
    TREND = "trend"
    RANGE = "range"


@dataclass
class PhaseAnalysis:
    """Result of market phase analysis."""

    phase: MarketPhase = MarketPhase.UNKNOWN
    confidence: float = 0.0
    volatility: float = 0.0
    momentum: float = 0.0
    range_tightness: float = 0.0
    trend_strength: float = 0.0
    volume_profile: str = "normal"
    description: str = ""


class MarketPhaseDetector:
    """
    Detects the current market phase from candle data.

    Uses multiple analysis techniques:
    - Volatility compression/expansion
    - ATR analysis
    - Momentum direction and strength
    - Range tightness
    - Candle pattern analysis
    """

    def __init__(
        self,
        min_candles: int = 20,
        compression_threshold: float = 0.50,
        expansion_threshold: float = 2.0,
        momentum_threshold: float = 0.6,
    ) -> None:
        self._min_candles = min_candles
        self._compression_threshold = compression_threshold
        self._expansion_threshold = expansion_threshold
        self._momentum_threshold = momentum_threshold

    def detect(self, candles: List[Candle], asset: str = "") -> PhaseAnalysis:
        """Detect current market phase."""
        result = PhaseAnalysis()

        if len(candles) < self._min_candles:
            result.description = "Insufficient data"
            return result

        recent = candles[-self._min_candles:]
        older = candles[-(self._min_candles * 2):-self._min_candles] if len(candles) >= self._min_candles * 2 else recent

        # Core metrics
        volatility = self._calc_volatility(recent)
        momentum = self._calc_momentum(recent)
        range_tight = self._calc_range_tightness(recent)
        trend_str = self._calc_trend_strength(recent)
        vol_ratio = self._calc_volatility_ratio(recent, older)

        result.volatility = round(volatility, 4)
        result.momentum = round(momentum, 4)
        result.range_tightness = round(range_tight, 4)
        result.trend_strength = round(trend_str, 4)

        # OTC noise detection
        if asset.endswith("-OTC"):
            noise_level = self._detect_otc_noise(recent)
            if noise_level > 0.7:
                result.phase = MarketPhase.OTC_NOISE
                result.confidence = noise_level
                result.description = f"OTC noise detected ({noise_level:.0%})"
                return result

        # Phase classification (priority order)

        # Panic: very high volatility + strong momentum
        if vol_ratio > 3.0 and abs(momentum) > 0.8:
            result.phase = MarketPhase.PANIC
            result.confidence = min(1.0, vol_ratio / 5.0)
            result.description = f"Panic: vol_ratio={vol_ratio:.1f}, momentum={momentum:.2f}"
            return result

        # Fake breakout: expansion followed by reversal
        fake_breakout = self._detect_fake_breakout(recent)
        if fake_breakout > 0.6:
            result.phase = MarketPhase.FAKE_BREAKOUT
            result.confidence = fake_breakout
            result.description = f"Fake breakout ({fake_breakout:.0%})"
            return result

        # Exhaustion: high volatility + weakening momentum
        if vol_ratio > 1.5 and self._detect_exhaustion(recent):
            result.phase = MarketPhase.EXHAUSTION
            result.confidence = min(1.0, vol_ratio / 3.0)
            result.description = "Trend exhaustion detected"
            return result

        # Compression: volatility contracting
        if vol_ratio < self._compression_threshold and range_tight > 0.7:
            result.phase = MarketPhase.COMPRESSION
            result.confidence = range_tight
            result.description = f"Compression: vol_ratio={vol_ratio:.2f}"
            return result

        # Expansion: volatility increasing + strong momentum
        if vol_ratio > self._expansion_threshold and abs(momentum) > self._momentum_threshold:
            result.phase = MarketPhase.EXPANSION
            result.confidence = min(1.0, vol_ratio / 3.0)
            result.description = f"Expansion: vol_ratio={vol_ratio:.1f}, momentum={momentum:.2f}"
            return result

        # Low liquidity
        if self._detect_low_liquidity(recent):
            result.phase = MarketPhase.LOW_LIQUIDITY
            result.confidence = 0.6
            result.description = "Low liquidity conditions"
            return result

        # Distribution: declining momentum at highs
        if trend_str < 0.3 and self._detect_distribution(recent):
            result.phase = MarketPhase.DISTRIBUTION
            result.confidence = 0.6
            result.description = "Distribution pattern"
            return result

        # Accumulation: range with rising lows
        if range_tight > 0.5 and self._detect_accumulation(recent):
            result.phase = MarketPhase.ACCUMULATION
            result.confidence = 0.6
            result.description = "Accumulation pattern"
            return result

        # Trend: strong directional movement
        if trend_str > 0.5 and abs(momentum) > 0.4:
            result.phase = MarketPhase.TREND
            result.confidence = trend_str
            result.description = f"Trend: strength={trend_str:.2f}"
            return result

        # Range: bounded movement
        if range_tight > 0.4 and trend_str < 0.3:
            result.phase = MarketPhase.RANGE
            result.confidence = range_tight
            result.description = f"Range: tightness={range_tight:.2f}"
            return result

        result.phase = MarketPhase.UNKNOWN
        result.confidence = 0.3
        result.description = "Ambiguous phase"
        return result

    def _calc_volatility(self, candles: List[Candle]) -> float:
        """Calculate average volatility (ATR-like)."""
        ranges = [c.high - c.low for c in candles]
        return statistics.mean(ranges) if ranges else 0

    def _calc_volatility_ratio(self, recent: List[Candle], older: List[Candle]) -> float:
        """Ratio of recent volatility to older volatility."""
        recent_vol = self._calc_volatility(recent[-10:])
        older_vol = self._calc_volatility(older[-10:])
        if older_vol == 0:
            return 1.0
        return recent_vol / older_vol

    def _calc_momentum(self, candles: List[Candle]) -> float:
        """Calculate momentum (-1 to 1)."""
        if len(candles) < 5:
            return 0.0
        price_change = candles[-1].close - candles[-10].close if len(candles) >= 10 else candles[-1].close - candles[0].close
        avg_range = self._calc_volatility(candles)
        if avg_range == 0:
            return 0.0
        momentum = price_change / (avg_range * len(candles) * 0.1)
        return max(-1.0, min(1.0, momentum))

    def _calc_range_tightness(self, candles: List[Candle]) -> float:
        """Calculate how tight the range is (0=wide, 1=very tight)."""
        highs = [c.high for c in candles[-10:]]
        lows = [c.low for c in candles[-10:]]
        if not highs or not lows:
            return 0.0
        range_size = max(highs) - min(lows)
        avg_body = statistics.mean([abs(c.close - c.open) for c in candles[-10:]])
        if range_size == 0:
            return 1.0
        return max(0.0, min(1.0, 1.0 - (range_size / (avg_body * 20 + 0.00001))))

    def _calc_trend_strength(self, candles: List[Candle]) -> float:
        """Calculate trend strength (0-1)."""
        if len(candles) < 5:
            return 0.0
        closes = [c.close for c in candles[-10:]]
        if len(closes) < 3:
            return 0.0

        # Count consecutive same-direction candles
        same_dir = 0
        for i in range(1, len(closes)):
            if (closes[i] - closes[i - 1]) * (closes[-1] - closes[0]) > 0:
                same_dir += 1

        return min(1.0, same_dir / (len(closes) - 1))

    def _detect_fake_breakout(self, candles: List[Candle]) -> float:
        """Detect fake breakout pattern."""
        if len(candles) < 10:
            return 0.0

        recent_5 = candles[-5:]
        prior_10 = candles[-15:-5] if len(candles) >= 15 else candles[:10]

        if not prior_10:
            return 0.0

        prior_high = max(c.high for c in prior_10)
        prior_low = min(c.low for c in prior_10)

        # Check if recent candles broke above/below then reversed
        broke_high = any(c.high > prior_high for c in recent_5[:3])
        broke_low = any(c.low < prior_low for c in recent_5[:3])
        reversed_in = recent_5[-1].close < prior_high and broke_high
        reversed_out = recent_5[-1].close > prior_low and broke_low

        if reversed_in or reversed_out:
            return 0.7
        return 0.0

    def _detect_exhaustion(self, candles: List[Candle]) -> bool:
        """Detect trend exhaustion."""
        if len(candles) < 10:
            return False
        bodies = [abs(c.close - c.open) for c in candles]
        recent_avg = statistics.mean(bodies[-5:])
        older_avg = statistics.mean(bodies[-10:-5])
        return recent_avg < older_avg * 0.5 if older_avg > 0 else False

    def _detect_low_liquidity(self, candles: List[Candle]) -> bool:
        """Detect low liquidity conditions."""
        bodies = [abs(c.close - c.open) for c in candles[-10:]]
        if not bodies:
            return False
        avg_body = statistics.mean(bodies)
        price = candles[-1].close if candles[-1].close > 0 else 1.0
        return (avg_body / price) < 0.00002

    def _detect_distribution(self, candles: List[Candle]) -> bool:
        """Detect distribution pattern (highs flattening, bears appearing)."""
        if len(candles) < 10:
            return False
        highs = [c.high for c in candles[-10:]]
        first_half_high = max(highs[:5])
        second_half_high = max(highs[5:])
        bearish = sum(1 for c in candles[-5:] if c.close < c.open)
        return second_half_high <= first_half_high and bearish >= 3

    def _detect_accumulation(self, candles: List[Candle]) -> bool:
        """Detect accumulation pattern (rising lows in range)."""
        if len(candles) < 10:
            return False
        lows = [c.low for c in candles[-10:]]
        first_half_low = min(lows[:5])
        second_half_low = min(lows[5:])
        return second_half_low > first_half_low

    def _detect_otc_noise(self, candles: List[Candle]) -> float:
        """Detect OTC-specific noise level."""
        if len(candles) < 10:
            return 0.0

        recent = candles[-10:]
        reversals = 0
        for i in range(2, len(recent)):
            d1 = recent[i - 1].close - recent[i - 2].close
            d2 = recent[i].close - recent[i - 1].close
            if d1 * d2 < 0:
                reversals += 1

        reversal_rate = reversals / (len(recent) - 2)

        # Check wick anomalies
        wick_anomalies = 0
        for c in recent:
            body = abs(c.close - c.open)
            if body == 0:
                body = 0.00001
            total_wick = (c.high - max(c.open, c.close)) + (min(c.open, c.close) - c.low)
            if total_wick / body > 8:
                wick_anomalies += 1

        anomaly_rate = wick_anomalies / len(recent)

        return min(1.0, reversal_rate * 0.7 + anomaly_rate * 0.3)
