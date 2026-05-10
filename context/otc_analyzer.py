"""
context/otc_analyzer.py — OTC behavior analyzer for Syntrix.

OTC markets have unique characteristics:
- Artificial noise and spikes
- Fake breakouts
- Micro reversals
- Toxic lateralization
- Artificial impulses

This analyzer detects these patterns and returns quality modifiers
instead of hard blocking.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from brokers.base import Candle

logger = logging.getLogger("syntrix.context.otc")


@dataclass
class OTCAnalysis:
    """Result of OTC behavior analysis."""

    quality_score: float = 1.0  # 0.0 (toxic) to 1.0 (clean)
    fake_breakout: bool = False
    toxic_lateralization: bool = False
    anomalous_wicks: bool = False
    excessive_compression: bool = False
    artificial_impulse: bool = False
    issues: List[str] = field(default_factory=list)
    modifier: float = 1.0  # confidence modifier


class OTCBehaviorAnalyzer:
    """
    Analyzes OTC-specific behavior patterns.

    Returns quality scores and confidence modifiers instead of hard blocks.
    """

    def __init__(
        self,
        wick_ratio_threshold: float = 12.0,
        compression_threshold: float = 0.3,
        impulse_threshold: float = 4.0,
        lateralization_min_candles: int = 8,
        lateralization_range_pct: float = 0.15,
    ) -> None:
        self._wick_ratio_threshold = wick_ratio_threshold
        self._compression_threshold = compression_threshold
        self._impulse_threshold = impulse_threshold
        self._lat_min_candles = lateralization_min_candles
        self._lat_range_pct = lateralization_range_pct

    def analyze(self, candles: List[Candle], asset: str = "") -> OTCAnalysis:
        """Analyze candles for OTC-specific behavior."""
        result = OTCAnalysis()

        if len(candles) < 10:
            return result

        issues: List[str] = []
        modifiers: List[float] = []

        # Check fake breakout
        fb = self._check_fake_breakout(candles)
        if fb:
            result.fake_breakout = True
            issues.append(f"Fake breakout detected: {fb}")
            modifiers.append(0.75)

        # Check toxic lateralization
        tl = self._check_toxic_lateralization(candles)
        if tl:
            result.toxic_lateralization = True
            issues.append(f"Toxic lateralization: {tl}")
            modifiers.append(0.70)

        # Check anomalous wicks
        aw = self._check_anomalous_wicks(candles)
        if aw:
            result.anomalous_wicks = True
            issues.append(f"Anomalous wicks: {aw}")
            modifiers.append(0.85)

        # Check excessive compression
        ec = self._check_compression(candles)
        if ec:
            result.excessive_compression = True
            issues.append(f"Excessive compression: {ec}")
            modifiers.append(0.80)

        # Check artificial impulse
        ai = self._check_artificial_impulse(candles)
        if ai:
            result.artificial_impulse = True
            issues.append(f"Artificial impulse: {ai}")
            modifiers.append(0.70)

        result.issues = issues

        if modifiers:
            combined = 1.0
            for m in modifiers:
                combined *= m
            result.modifier = round(max(0.3, combined), 3)
            result.quality_score = round(result.modifier, 3)
        else:
            result.quality_score = 1.0
            result.modifier = 1.0

        if issues:
            logger.debug("[%s] OTC issues: %s (modifier=%.2f)", asset, "; ".join(issues), result.modifier)

        return result

    def _check_fake_breakout(self, candles: List[Candle]) -> str:
        """Detect fake breakout: price breaks level then reverses quickly."""
        if len(candles) < 5:
            return ""

        recent = candles[-5:]
        highs = [c.high for c in recent]
        lows = [c.low for c in recent]
        closes = [c.close for c in recent]

        # Check if price spiked above recent high then closed below
        prev_high = max(c.high for c in candles[-15:-5]) if len(candles) >= 15 else max(highs)
        prev_low = min(c.low for c in candles[-15:-5]) if len(candles) >= 15 else min(lows)

        last = candles[-1]
        prev = candles[-2]

        # Breakout up then reversal
        if prev.high > prev_high and last.close < prev.open:
            return f"broke high {prev_high:.5f} then reversed"

        # Breakout down then reversal
        if prev.low < prev_low and last.close > prev.open:
            return f"broke low {prev_low:.5f} then reversed"

        return ""

    def _check_toxic_lateralization(self, candles: List[Candle]) -> str:
        """Detect toxic lateralization: tight range with no direction."""
        if len(candles) < self._lat_min_candles:
            return ""

        recent = candles[-self._lat_min_candles:]
        closes = [c.close for c in recent]
        high = max(c.high for c in recent)
        low = min(c.low for c in recent)

        if low == 0:
            return ""

        range_pct = (high - low) / low
        if range_pct < self._lat_range_pct / 100:
            return f"range={range_pct:.4%} over {self._lat_min_candles} candles"

        # Also check if closes are clustered
        if len(set(round(c, 5) for c in closes)) <= 3:
            return f"closes clustered in {len(set(round(c, 5) for c in closes))} levels"

        return ""

    def _check_anomalous_wicks(self, candles: List[Candle]) -> str:
        """Detect candles with excessively large wicks relative to body."""
        if len(candles) < 5:
            return ""

        anomalous = 0
        for c in candles[-5:]:
            body = abs(c.close - c.open)
            if body == 0:
                body = 0.00001
            upper_wick = c.high - max(c.open, c.close)
            lower_wick = min(c.open, c.close) - c.low
            total_wick = upper_wick + lower_wick
            if total_wick / body > self._wick_ratio_threshold:
                anomalous += 1

        if anomalous >= 2:
            return f"{anomalous}/5 candles with extreme wicks"
        return ""

    def _check_compression(self, candles: List[Candle]) -> str:
        """Detect excessive compression (very small candles)."""
        if len(candles) < 10:
            return ""

        bodies = [abs(c.close - c.open) for c in candles[-10:]]
        avg_body = statistics.mean(bodies) if bodies else 0

        # Compare to earlier candles
        if len(candles) >= 30:
            earlier_bodies = [abs(c.close - c.open) for c in candles[-30:-10]]
            earlier_avg = statistics.mean(earlier_bodies) if earlier_bodies else 0
            if earlier_avg > 0 and avg_body / earlier_avg < self._compression_threshold:
                return f"body size {avg_body/earlier_avg:.0%} of normal"
        return ""

    def _check_artificial_impulse(self, candles: List[Candle]) -> str:
        """Detect artificial impulse: sudden large candle inconsistent with context."""
        if len(candles) < 10:
            return ""

        bodies = [abs(c.close - c.open) for c in candles[-10:-1]]
        if not bodies:
            return ""
        avg_body = statistics.mean(bodies)
        if avg_body == 0:
            return ""

        last_body = abs(candles[-1].close - candles[-1].open)
        ratio = last_body / avg_body

        if ratio > self._impulse_threshold:
            return f"last candle {ratio:.1f}x average body"
        return ""
