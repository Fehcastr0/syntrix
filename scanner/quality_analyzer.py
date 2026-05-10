"""
scanner/quality_analyzer.py — Asset quality analyzer for Syntrix.

Analyzes asset quality based on:
- Payout
- Candle stability
- Spike frequency
- Implied spread
- OTC noise
- Useful volatility
- Candle consistency

Returns a quality score (0.0-1.0) per asset.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from brokers.base import Candle

logger = logging.getLogger("syntrix.scanner.quality")


@dataclass
class AssetQuality:
    """Quality analysis result for one asset."""

    asset: str = ""
    quality_score: float = 0.5
    payout_score: float = 0.5
    stability_score: float = 0.5
    spike_score: float = 1.0
    spread_score: float = 0.5
    noise_score: float = 0.5
    volatility_score: float = 0.5
    consistency_score: float = 0.5
    issues: List[str] = field(default_factory=list)


class AssetQualityAnalyzer:
    """
    Analyzes asset quality for trading suitability.

    Higher quality = better conditions for consistent trading.
    """

    def __init__(
        self,
        min_payout: float = 0.60,
        ideal_payout: float = 0.80,
        max_spike_ratio: float = 0.3,
    ) -> None:
        self._min_payout = min_payout
        self._ideal_payout = ideal_payout
        self._max_spike_ratio = max_spike_ratio

    def analyze(self, asset: str, candles: List[Candle], payout: float = 0.0) -> AssetQuality:
        """Analyze asset quality from candles and payout."""
        result = AssetQuality(asset=asset)

        if len(candles) < 10:
            result.issues.append("Insufficient candles")
            return result

        # Payout score
        result.payout_score = self._score_payout(payout)

        # Stability (how consistent are candle sizes)
        result.stability_score = self._score_stability(candles)

        # Spike frequency
        result.spike_score = self._score_spikes(candles)

        # Implied spread
        result.spread_score = self._score_spread(candles)

        # Noise (OTC noise detection)
        result.noise_score = self._score_noise(candles, asset)

        # Useful volatility
        result.volatility_score = self._score_volatility(candles)

        # Candle consistency
        result.consistency_score = self._score_consistency(candles)

        # Weighted quality score
        weights = {
            "payout": 0.25,
            "stability": 0.15,
            "spike": 0.15,
            "spread": 0.10,
            "noise": 0.10,
            "volatility": 0.15,
            "consistency": 0.10,
        }

        total = (
            result.payout_score * weights["payout"]
            + result.stability_score * weights["stability"]
            + result.spike_score * weights["spike"]
            + result.spread_score * weights["spread"]
            + result.noise_score * weights["noise"]
            + result.volatility_score * weights["volatility"]
            + result.consistency_score * weights["consistency"]
        )

        result.quality_score = round(max(0.0, min(1.0, total)), 3)

        if result.quality_score < 0.3:
            result.issues.append("Low overall quality")
        if result.payout_score < 0.3:
            result.issues.append(f"Low payout: {payout:.0%}")
        if result.spike_score < 0.5:
            result.issues.append("Frequent spikes")

        return result

    def _score_payout(self, payout: float) -> float:
        if payout >= self._ideal_payout:
            return 1.0
        if payout >= self._min_payout:
            return 0.5 + 0.5 * (payout - self._min_payout) / (self._ideal_payout - self._min_payout)
        if payout > 0:
            return max(0.0, payout / self._min_payout * 0.5)
        return 0.0

    def _score_stability(self, candles: List[Candle]) -> float:
        """Score based on body size consistency."""
        bodies = [abs(c.close - c.open) for c in candles[-20:]]
        if not bodies or max(bodies) == 0:
            return 0.5
        avg = statistics.mean(bodies)
        if avg == 0:
            return 0.5
        stdev = statistics.stdev(bodies) if len(bodies) > 1 else 0
        cv = stdev / avg  # coefficient of variation
        # Lower CV = more stable
        if cv < 0.5:
            return 1.0
        if cv < 1.0:
            return 0.7
        if cv < 2.0:
            return 0.4
        return 0.2

    def _score_spikes(self, candles: List[Candle]) -> float:
        """Score based on spike frequency."""
        spike_count = 0
        for c in candles[-20:]:
            body = abs(c.close - c.open)
            if body == 0:
                body = 0.00001
            wick = (c.high - max(c.open, c.close)) + (min(c.open, c.close) - c.low)
            if wick / body > 5:
                spike_count += 1

        ratio = spike_count / min(20, len(candles))
        if ratio <= 0.05:
            return 1.0
        if ratio <= 0.15:
            return 0.7
        if ratio <= 0.30:
            return 0.4
        return 0.2

    def _score_spread(self, candles: List[Candle]) -> float:
        """Score based on implied spread (gap between candles)."""
        gaps = []
        for i in range(1, min(20, len(candles))):
            gap = abs(candles[i].open - candles[i - 1].close)
            gaps.append(gap)

        if not gaps:
            return 0.5

        avg_gap = statistics.mean(gaps)
        avg_body = statistics.mean([abs(c.close - c.open) for c in candles[-20:]]) or 0.00001

        # Gap relative to body size
        gap_ratio = avg_gap / avg_body
        if gap_ratio < 0.1:
            return 1.0
        if gap_ratio < 0.3:
            return 0.7
        if gap_ratio < 0.5:
            return 0.4
        return 0.2

    def _score_noise(self, candles: List[Candle], asset: str) -> float:
        """Score based on noise level (especially for OTC)."""
        if not asset.endswith("-OTC"):
            return 0.8  # Forex regular is cleaner

        # Check micro-reversals
        reversals = 0
        recent = candles[-20:]
        for i in range(2, len(recent)):
            d1 = recent[i - 1].close - recent[i - 2].close
            d2 = recent[i].close - recent[i - 1].close
            if d1 * d2 < 0:
                reversals += 1

        reversal_rate = reversals / max(1, len(recent) - 2)
        if reversal_rate < 0.3:
            return 0.9
        if reversal_rate < 0.5:
            return 0.6
        if reversal_rate < 0.7:
            return 0.3
        return 0.1

    def _score_volatility(self, candles: List[Candle]) -> float:
        """Score useful volatility (not too much, not too little)."""
        bodies = [abs(c.close - c.open) for c in candles[-20:]]
        if not bodies:
            return 0.5
        avg_body = statistics.mean(bodies)
        price = candles[-1].close if candles[-1].close > 0 else 1.0
        rel_volatility = avg_body / price

        # Sweet spot: 0.0001 to 0.001 (for forex)
        if 0.00005 <= rel_volatility <= 0.002:
            return 0.9
        if 0.00002 <= rel_volatility <= 0.005:
            return 0.6
        return 0.3

    def _score_consistency(self, candles: List[Candle]) -> float:
        """Score candle data consistency (no gaps, proper OHLC)."""
        issues = 0
        recent = candles[-20:]
        for c in recent:
            if c.high < c.low:
                issues += 1
            if c.high < max(c.open, c.close):
                issues += 1
            if c.low > min(c.open, c.close):
                issues += 1
            if c.close == 0 and c.open == 0:
                issues += 1

        issue_rate = issues / (len(recent) * 4)
        return max(0.0, 1.0 - issue_rate * 5)
