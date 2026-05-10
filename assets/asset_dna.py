"""
assets/asset_dna.py — Asset DNA System for Syntrix v2.0.

Each asset gets a unique behavioral profile (DNA) that evolves over time:
- Average volatility
- Typical payout
- Best hours
- Spike frequency
- Best/worst strategy
- Historical winrate
- Average score
- OTC behavior
- Stability

The system adapts thresholds, priority, scan rate, and strategy weights per asset.
"""

from __future__ import annotations

import json
import logging
import os
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("syntrix.asset_dna")


@dataclass
class AssetDNA:
    """Behavioral profile for a single asset."""

    name: str = ""
    asset_type: str = "otc"

    # Performance
    total_trades: int = 0
    wins: int = 0
    losses: int = 0

    # Volatility
    avg_volatility: float = 0.0
    volatility_history: List[float] = field(default_factory=list)

    # Payout
    avg_payout: float = 0.0
    payout_history: List[float] = field(default_factory=list)

    # Timing
    best_hours: List[int] = field(default_factory=list)
    worst_hours: List[int] = field(default_factory=list)
    performance_by_hour: Dict[int, Dict[str, int]] = field(default_factory=dict)

    # Strategies
    strategy_performance: Dict[str, Dict[str, int]] = field(default_factory=dict)
    best_strategy: str = ""
    worst_strategy: str = ""

    # Quality
    spike_frequency: float = 0.0
    otc_noise_level: float = 0.0
    stability_score: float = 0.5
    avg_score: float = 0.0
    score_history: List[float] = field(default_factory=list)

    # Adaptive
    threshold_adjustment: float = 0.0
    priority: float = 1.0
    scan_interval_factor: float = 1.0

    # Timestamps
    first_seen: float = 0.0
    last_updated: float = 0.0

    @property
    def winrate(self) -> float:
        total = self.wins + self.losses
        if total == 0:
            return 0.5
        return round(self.wins / total, 4)

    @property
    def profitability_score(self) -> float:
        """Combined profitability metric."""
        if self.total_trades < 5:
            return 0.5
        wr_factor = self.winrate
        payout_factor = min(1.0, self.avg_payout / 0.85)
        return round(wr_factor * 0.6 + payout_factor * 0.4, 3)


class AssetDNAManager:
    """
    Manages DNA profiles for all tracked assets.

    Learns and adapts asset behavior over time.
    """

    def __init__(self, persist_path: str = "data/asset_dna.json") -> None:
        self._profiles: Dict[str, AssetDNA] = {}
        self._persist_path = persist_path
        self._load()

    def get_or_create(self, asset: str) -> AssetDNA:
        """Get or create DNA for an asset."""
        if asset not in self._profiles:
            atype = "otc" if asset.endswith("-OTC") else "forex"
            self._profiles[asset] = AssetDNA(
                name=asset,
                asset_type=atype,
                first_seen=time.time(),
            )
        return self._profiles[asset]

    def record_scan(
        self,
        asset: str,
        volatility: float = 0.0,
        payout: float = 0.0,
        score: float = 0.0,
        spike_detected: bool = False,
        otc_noise: float = 0.0,
    ) -> None:
        """Record a scan result for an asset."""
        dna = self.get_or_create(asset)

        # Update volatility
        dna.volatility_history.append(volatility)
        if len(dna.volatility_history) > 200:
            dna.volatility_history = dna.volatility_history[-100:]
        dna.avg_volatility = statistics.mean(dna.volatility_history) if dna.volatility_history else 0

        # Update payout
        if payout > 0:
            dna.payout_history.append(payout)
            if len(dna.payout_history) > 200:
                dna.payout_history = dna.payout_history[-100:]
            dna.avg_payout = statistics.mean(dna.payout_history)

        # Update score
        if score > 0:
            dna.score_history.append(score)
            if len(dna.score_history) > 200:
                dna.score_history = dna.score_history[-100:]
            dna.avg_score = statistics.mean(dna.score_history)

        # Update spike frequency (rolling average)
        dna.spike_frequency = dna.spike_frequency * 0.95 + (1.0 if spike_detected else 0.0) * 0.05

        # OTC noise
        dna.otc_noise_level = dna.otc_noise_level * 0.9 + otc_noise * 0.1

        dna.last_updated = time.time()

    def record_trade(
        self,
        asset: str,
        won: bool,
        hour_utc: int = -1,
        strategy: str = "",
        score: float = 0.0,
    ) -> None:
        """Record a trade result."""
        dna = self.get_or_create(asset)
        dna.total_trades += 1
        if won:
            dna.wins += 1
        else:
            dna.losses += 1

        # Hour performance
        if hour_utc >= 0:
            if hour_utc not in dna.performance_by_hour:
                dna.performance_by_hour[hour_utc] = {"wins": 0, "losses": 0}
            if won:
                dna.performance_by_hour[hour_utc]["wins"] += 1
            else:
                dna.performance_by_hour[hour_utc]["losses"] += 1

        # Strategy performance
        if strategy:
            if strategy not in dna.strategy_performance:
                dna.strategy_performance[strategy] = {"wins": 0, "losses": 0}
            if won:
                dna.strategy_performance[strategy]["wins"] += 1
            else:
                dna.strategy_performance[strategy]["losses"] += 1

        # Recalculate best/worst strategy
        self._update_strategy_ranking(dna)

        # Recalculate best/worst hours
        self._update_hour_ranking(dna)

        dna.last_updated = time.time()

    def update_adaptive_params(self, asset: str) -> None:
        """Update adaptive parameters for an asset."""
        dna = self.get_or_create(asset)

        # Threshold adjustment based on performance
        if dna.total_trades >= 10:
            if dna.winrate > 0.60:
                dna.threshold_adjustment = -0.03  # lower threshold
            elif dna.winrate < 0.40:
                dna.threshold_adjustment = +0.05  # raise threshold
            else:
                dna.threshold_adjustment = 0.0

        # Priority based on profitability
        dna.priority = max(0.2, min(2.0, 0.5 + dna.profitability_score * 1.5))

        # Scan interval factor
        if dna.profitability_score > 0.6:
            dna.scan_interval_factor = 0.7  # scan more often
        elif dna.profitability_score < 0.3:
            dna.scan_interval_factor = 1.5  # scan less often
        else:
            dna.scan_interval_factor = 1.0

        # Stability score
        if dna.total_trades >= 10:
            wr_stability = 1.0 - abs(dna.winrate - 0.5) * 2
            spike_penalty = dna.spike_frequency * 2
            noise_penalty = dna.otc_noise_level
            dna.stability_score = max(0.0, min(1.0,
                0.5 + wr_stability * 0.3 - spike_penalty * 0.1 - noise_penalty * 0.1
            ))

    def _update_strategy_ranking(self, dna: AssetDNA) -> None:
        """Update best/worst strategy for an asset."""
        if not dna.strategy_performance:
            return

        best_wr = -1.0
        worst_wr = 2.0
        for strat, perf in dna.strategy_performance.items():
            total = perf["wins"] + perf["losses"]
            if total < 3:
                continue
            wr = perf["wins"] / total
            if wr > best_wr:
                best_wr = wr
                dna.best_strategy = strat
            if wr < worst_wr:
                worst_wr = wr
                dna.worst_strategy = strat

    def _update_hour_ranking(self, dna: AssetDNA) -> None:
        """Update best/worst hours."""
        if not dna.performance_by_hour:
            return

        hour_wr: Dict[int, float] = {}
        for h, perf in dna.performance_by_hour.items():
            total = perf["wins"] + perf["losses"]
            if total >= 3:
                hour_wr[h] = perf["wins"] / total

        if not hour_wr:
            return

        sorted_hours = sorted(hour_wr.items(), key=lambda x: x[1], reverse=True)
        dna.best_hours = [h for h, _ in sorted_hours[:3]]
        dna.worst_hours = [h for h, _ in sorted_hours[-3:]]

    def get_all_dna(self) -> Dict[str, AssetDNA]:
        return dict(self._profiles)

    def get_adaptive_threshold(self, asset: str) -> float:
        """Get threshold adjustment for an asset."""
        dna = self._profiles.get(asset)
        if dna:
            return dna.threshold_adjustment
        return 0.0

    def get_scan_factor(self, asset: str) -> float:
        """Get scan interval factor for an asset."""
        dna = self._profiles.get(asset)
        if dna:
            return dna.scan_interval_factor
        return 1.0

    def save(self) -> None:
        """Persist DNA profiles to disk."""
        try:
            os.makedirs(os.path.dirname(self._persist_path), exist_ok=True)
            data = {}
            for name, dna in self._profiles.items():
                data[name] = {
                    "name": dna.name,
                    "asset_type": dna.asset_type,
                    "total_trades": dna.total_trades,
                    "wins": dna.wins,
                    "losses": dna.losses,
                    "avg_volatility": dna.avg_volatility,
                    "avg_payout": dna.avg_payout,
                    "avg_score": dna.avg_score,
                    "spike_frequency": dna.spike_frequency,
                    "otc_noise_level": dna.otc_noise_level,
                    "stability_score": dna.stability_score,
                    "best_strategy": dna.best_strategy,
                    "worst_strategy": dna.worst_strategy,
                    "best_hours": dna.best_hours,
                    "worst_hours": dna.worst_hours,
                    "threshold_adjustment": dna.threshold_adjustment,
                    "priority": dna.priority,
                    "scan_interval_factor": dna.scan_interval_factor,
                    "strategy_performance": dna.strategy_performance,
                    "performance_by_hour": {str(k): v for k, v in dna.performance_by_hour.items()},
                    "first_seen": dna.first_seen,
                    "last_updated": dna.last_updated,
                }
            with open(self._persist_path, "w") as f:
                json.dump(data, f, indent=2)
            logger.debug("Saved DNA for %d assets", len(data))
        except Exception as exc:
            logger.error("Failed to save DNA: %s", exc)

    def _load(self) -> None:
        """Load DNA profiles from disk."""
        if not os.path.exists(self._persist_path):
            return
        try:
            with open(self._persist_path) as f:
                data = json.load(f)
            for name, d in data.items():
                dna = AssetDNA(
                    name=d.get("name", name),
                    asset_type=d.get("asset_type", "otc"),
                    total_trades=d.get("total_trades", 0),
                    wins=d.get("wins", 0),
                    losses=d.get("losses", 0),
                    avg_volatility=d.get("avg_volatility", 0),
                    avg_payout=d.get("avg_payout", 0),
                    avg_score=d.get("avg_score", 0),
                    spike_frequency=d.get("spike_frequency", 0),
                    otc_noise_level=d.get("otc_noise_level", 0),
                    stability_score=d.get("stability_score", 0.5),
                    best_strategy=d.get("best_strategy", ""),
                    worst_strategy=d.get("worst_strategy", ""),
                    best_hours=d.get("best_hours", []),
                    worst_hours=d.get("worst_hours", []),
                    threshold_adjustment=d.get("threshold_adjustment", 0),
                    priority=d.get("priority", 1.0),
                    scan_interval_factor=d.get("scan_interval_factor", 1.0),
                    strategy_performance=d.get("strategy_performance", {}),
                    performance_by_hour={int(k): v for k, v in d.get("performance_by_hour", {}).items()},
                    first_seen=d.get("first_seen", 0),
                    last_updated=d.get("last_updated", 0),
                )
                self._profiles[name] = dna
            logger.info("Loaded DNA for %d assets", len(self._profiles))
        except Exception as exc:
            logger.warning("Failed to load DNA: %s", exc)
