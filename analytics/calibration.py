"""
analytics/calibration.py — Probability Calibration Engine for Syntrix.

Converts heuristic scores into real probability estimates.

Purpose:
- confidence=0.72 → "historically signals at 0.72 have 64% real winrate"
- Calibration curves
- Rolling expectancy
- Confidence buckets
- Probability reliability analysis
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("syntrix.calibration")


@dataclass
class CalibrationBucket:
    """Stats for a confidence bucket."""

    bucket_low: float = 0.0
    bucket_high: float = 0.0
    total: int = 0
    wins: int = 0
    losses: int = 0
    avg_confidence: float = 0.0
    real_winrate: float = 0.0
    expectancy: float = 0.0
    avg_payout: float = 0.0
    calibration_error: float = 0.0  # |predicted - actual|


@dataclass
class CalibrationResult:
    """Full calibration analysis."""

    buckets: List[CalibrationBucket] = field(default_factory=list)
    total_trades: int = 0
    overall_winrate: float = 0.0
    overall_expectancy: float = 0.0
    mean_calibration_error: float = 0.0
    reliability_score: float = 0.0  # 1.0 = perfectly calibrated
    is_overconfident: bool = False
    is_underconfident: bool = False
    optimal_threshold: float = 0.0


class ProbabilityCalibrationEngine:
    """
    Calibrates confidence scores against real outcomes.

    Maintains rolling history and produces calibration curves.
    """

    def __init__(
        self,
        bucket_size: float = 0.10,
        min_samples: int = 10,
        max_history: int = 5000,
    ) -> None:
        self._bucket_size = bucket_size
        self._min_samples = min_samples
        self._max_history = max_history
        self._history: List[Dict[str, Any]] = []

    def record(
        self,
        confidence: float,
        won: bool,
        payout: float,
        asset: str = "",
        strategy: str = "",
        regime: str = "",
        hour_utc: int = -1,
        meta_score: float = 0.0,
    ) -> None:
        """Record a trade outcome for calibration."""
        self._history.append({
            "confidence": confidence,
            "won": won,
            "payout": payout,
            "asset": asset,
            "strategy": strategy,
            "regime": regime,
            "hour_utc": hour_utc,
            "meta_score": meta_score,
        })

        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

    def calibrate(self) -> CalibrationResult:
        """Run full calibration analysis."""
        if len(self._history) < self._min_samples:
            return CalibrationResult(
                total_trades=len(self._history),
                reliability_score=0.0,
            )

        # Build buckets
        buckets: Dict[Tuple[float, float], List[Dict]] = defaultdict(list)
        step = self._bucket_size
        b = 0.0
        while b < 1.0:
            low = round(b, 2)
            high = round(b + step, 2)
            buckets[(low, high)] = []
            b += step

        for entry in self._history:
            conf = entry["confidence"]
            for (low, high) in buckets:
                if low <= conf < high:
                    buckets[(low, high)].append(entry)
                    break

        # Analyze each bucket
        result_buckets: List[CalibrationBucket] = []
        calibration_errors: List[float] = []
        total_wins = 0
        total_losses = 0
        total_expectancy = 0.0

        for (low, high), entries in sorted(buckets.items()):
            if not entries:
                continue

            wins = sum(1 for e in entries if e["won"])
            losses = len(entries) - wins
            total_wins += wins
            total_losses += losses

            avg_conf = sum(e["confidence"] for e in entries) / len(entries)
            real_wr = wins / len(entries)
            avg_payout = sum(e["payout"] for e in entries) / len(entries)

            # Expectancy = (WR * avg_payout) - (LR * 1.0)
            exp = (real_wr * avg_payout) - ((1 - real_wr) * 1.0)

            cal_error = abs(avg_conf - real_wr)
            calibration_errors.append(cal_error)

            bucket = CalibrationBucket(
                bucket_low=low,
                bucket_high=high,
                total=len(entries),
                wins=wins,
                losses=losses,
                avg_confidence=round(avg_conf, 3),
                real_winrate=round(real_wr, 3),
                expectancy=round(exp, 4),
                avg_payout=round(avg_payout, 3),
                calibration_error=round(cal_error, 3),
            )
            result_buckets.append(bucket)

        total_trades = total_wins + total_losses
        overall_wr = total_wins / total_trades if total_trades > 0 else 0
        avg_payout_all = sum(e["payout"] for e in self._history) / len(self._history)
        overall_exp = (overall_wr * avg_payout_all) - ((1 - overall_wr) * 1.0)

        mean_cal_error = (
            sum(calibration_errors) / len(calibration_errors)
            if calibration_errors else 0
        )
        reliability = max(0, 1.0 - mean_cal_error * 2)

        # Check over/under confidence
        avg_predicted = sum(e["confidence"] for e in self._history) / len(self._history)
        is_overconfident = avg_predicted > overall_wr + 0.05
        is_underconfident = avg_predicted < overall_wr - 0.05

        # Find optimal threshold (where expectancy turns positive)
        optimal_threshold = 0.0
        for bucket in result_buckets:
            if bucket.total >= self._min_samples and bucket.expectancy > 0:
                optimal_threshold = bucket.bucket_low
                break

        return CalibrationResult(
            buckets=result_buckets,
            total_trades=total_trades,
            overall_winrate=round(overall_wr, 3),
            overall_expectancy=round(overall_exp, 4),
            mean_calibration_error=round(mean_cal_error, 3),
            reliability_score=round(reliability, 3),
            is_overconfident=is_overconfident,
            is_underconfident=is_underconfident,
            optimal_threshold=round(optimal_threshold, 3),
        )

    def get_real_probability(self, confidence: float) -> float:
        """Convert a confidence score to estimated real winrate."""
        step = self._bucket_size
        bucket_low = math.floor(confidence / step) * step

        entries = [
            e for e in self._history
            if bucket_low <= e["confidence"] < bucket_low + step
        ]

        if len(entries) < self._min_samples:
            return confidence  # not enough data, return raw

        wins = sum(1 for e in entries if e["won"])
        return wins / len(entries)

    def get_expectancy(self, confidence: float) -> float:
        """Get expected value for a given confidence level."""
        step = self._bucket_size
        bucket_low = math.floor(confidence / step) * step

        entries = [
            e for e in self._history
            if bucket_low <= e["confidence"] < bucket_low + step
        ]

        if len(entries) < self._min_samples:
            return 0.0

        wins = sum(1 for e in entries if e["won"])
        wr = wins / len(entries)
        avg_payout = sum(e["payout"] for e in entries) / len(entries)
        return (wr * avg_payout) - ((1 - wr) * 1.0)

    def format_report(self) -> str:
        """Format calibration report."""
        cal = self.calibrate()
        lines = [
            "=" * 60,
            "  PROBABILITY CALIBRATION REPORT",
            "=" * 60,
            f"  Total trades: {cal.total_trades}",
            f"  Overall winrate: {cal.overall_winrate:.1%}",
            f"  Overall expectancy: {cal.overall_expectancy:.4f}",
            f"  Calibration error: {cal.mean_calibration_error:.3f}",
            f"  Reliability: {cal.reliability_score:.1%}",
            f"  Optimal threshold: {cal.optimal_threshold:.2f}",
            "",
        ]

        if cal.is_overconfident:
            lines.append("  WARNING: System is OVERCONFIDENT")
        elif cal.is_underconfident:
            lines.append("  NOTE: System is underconfident (good sign)")

        lines.append("")
        lines.append("  BUCKET | TRADES | WINRATE | EXPECTANCY | CAL.ERROR")
        lines.append("  " + "-" * 55)

        for b in cal.buckets:
            if b.total > 0:
                lines.append(
                    f"  [{b.bucket_low:.1f}-{b.bucket_high:.1f}) | "
                    f"{b.total:5d} | {b.real_winrate:6.1%} | "
                    f"{b.expectancy:+.4f} | {b.calibration_error:.3f}"
                )

        lines.append("=" * 60)
        return "\n".join(lines)
