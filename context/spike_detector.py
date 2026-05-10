"""
context/spike_detector.py — Anomalous candle and spike detector for Syntrix.

Detects:
- Abnormally large candles
- Excessive wicks
- Volatility spikes outside the normal curve
- Corrupted/invalid price data
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from brokers.base import Candle

logger = logging.getLogger("syntrix.context.spike")


@dataclass
class SpikeResult:
    """Result of spike detection analysis."""

    spike_detected: bool
    severity: str  # "none", "mild", "severe"
    reasons: List[str]
    details: Dict[str, Any]


class SpikeDetector:
    """
    Detects anomalous price action that may corrupt signals.

    Checks:
    1. Candle body size vs recent average
    2. Wick proportion anomalies
    3. Volatility z-score
    4. Corrupted data (zero values, negative prices)
    """

    def __init__(
        self,
        body_mult_threshold: float = 3.0,
        wick_ratio_threshold: float = 4.0,
        zscore_threshold: float = 2.5,
        min_candles: int = 10,
    ) -> None:
        self._body_mult = body_mult_threshold
        self._wick_ratio = wick_ratio_threshold
        self._zscore = zscore_threshold
        self._min_candles = min_candles
        self._spike_count = 0

    @property
    def total_spikes_detected(self) -> int:
        return self._spike_count

    def detect(self, candles: List[Candle]) -> SpikeResult:
        """
        Analyze the most recent candle against historical context.

        Args:
            candles: List of candles (oldest first). At least min_candles required.

        Returns:
            SpikeResult with detection details.
        """
        if len(candles) < self._min_candles:
            return SpikeResult(
                spike_detected=False,
                severity="none",
                reasons=["Insufficient candle data"],
                details={"candle_count": len(candles)},
            )

        current = candles[-1]
        history = candles[:-1]
        reasons: List[str] = []
        details: Dict[str, Any] = {}

        corrupted = self._check_corrupted(current)
        if corrupted:
            reasons.append(corrupted)

        body_spike, body_info = self._check_body_spike(current, history)
        if body_spike:
            reasons.append(body_spike)
            details.update(body_info)

        wick_spike, wick_info = self._check_wick_anomaly(current, history)
        if wick_spike:
            reasons.append(wick_spike)
            details.update(wick_info)

        vol_spike, vol_info = self._check_volatility_zscore(current, history)
        if vol_spike:
            reasons.append(vol_spike)
            details.update(vol_info)

        spike_detected = len(reasons) > 0
        severity = "none"
        if len(reasons) >= 3:
            severity = "severe"
        elif len(reasons) >= 1:
            severity = "mild" if len(reasons) == 1 else "severe"

        if spike_detected:
            self._spike_count += 1
            logger.warning("Spike detected (%s): %s", severity, "; ".join(reasons))

        return SpikeResult(
            spike_detected=spike_detected,
            severity=severity,
            reasons=reasons,
            details=details,
        )

    @staticmethod
    def _check_corrupted(candle: Candle) -> Optional[str]:
        """Check for corrupted/invalid candle data."""
        if candle.open <= 0 or candle.close <= 0 or candle.high <= 0 or candle.low <= 0:
            return "Corrupted data: zero or negative price"
        if candle.low > candle.high:
            return "Corrupted data: low > high"
        if candle.open > candle.high or candle.open < candle.low:
            return "Corrupted data: open outside high/low range"
        if candle.close > candle.high or candle.close < candle.low:
            return "Corrupted data: close outside high/low range"
        return None

    def _check_body_spike(
        self, current: Candle, history: List[Candle]
    ) -> tuple[Optional[str], Dict[str, Any]]:
        """Check if current candle body is abnormally large."""
        bodies = [c.body_size for c in history if c.body_size > 0]
        if not bodies:
            return None, {}

        avg_body = statistics.mean(bodies)
        if avg_body <= 0:
            return None, {}

        ratio = current.body_size / avg_body
        info = {"body_ratio": round(ratio, 2), "avg_body": round(avg_body, 6)}

        if ratio > self._body_mult:
            return f"Anomalous candle body ({ratio:.1f}x average)", info
        return None, info

    def _check_wick_anomaly(
        self, current: Candle, history: List[Candle]
    ) -> tuple[Optional[str], Dict[str, Any]]:
        """Check if wicks are disproportionately large."""
        total_wick = current.upper_wick + current.lower_wick
        body = current.body_size

        if body <= 0:
            if total_wick > 0:
                return "Doji with significant wick", {"wick_body_ratio": float("inf")}
            return None, {}

        ratio = total_wick / body
        info = {"wick_body_ratio": round(ratio, 2)}

        if ratio > self._wick_ratio:
            return f"Excessive wick ({ratio:.1f}x body size)", info
        return None, info

    def _check_volatility_zscore(
        self, current: Candle, history: List[Candle]
    ) -> tuple[Optional[str], Dict[str, Any]]:
        """Check if current candle range is a volatility outlier."""
        ranges = [c.range for c in history if c.range > 0]
        if len(ranges) < 5:
            return None, {}

        mean_range = statistics.mean(ranges)
        stdev_range = statistics.stdev(ranges)

        if stdev_range <= 0:
            return None, {}

        zscore = (current.range - mean_range) / stdev_range
        info = {"volatility_zscore": round(zscore, 2), "mean_range": round(mean_range, 6)}

        if zscore > self._zscore:
            return f"Volatility spike (z-score={zscore:.2f})", info
        return None, info
