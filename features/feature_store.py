"""
features/feature_store.py — Feature store for Syntrix.

Stores operational features for each decision point:
- score, context, regime, payout, volatility, hour, asset, result, latency

Future use: ML/meta-scoring, adaptive thresholds, strategy weight tuning.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("syntrix.features")


@dataclass
class FeatureRecord:
    """Single feature snapshot for a decision point."""

    timestamp: float = 0.0
    asset: str = ""
    regime: str = ""
    session: str = ""
    payout: float = 0.0
    volatility: float = 0.0
    hour_utc: int = 0
    score: float = 0.0
    confidence: float = 0.0
    threshold_used: float = 0.0
    strategy: str = ""
    direction: str = ""
    context_decision: str = ""
    block_reasons: List[str] = field(default_factory=list)
    soft_blocks: int = 0
    hard_blocks: int = 0
    otc_quality: float = 1.0
    latency_ms: float = 0.0
    result: str = ""  # win/loss/blocked/skipped
    profit: float = 0.0
    ensemble_scores: Dict[str, float] = field(default_factory=dict)
    market_phase: str = ""
    meta_score: float = 0.0
    asset_quality: float = 0.0
    microstructure_quality: float = 0.0
    rank_score: float = 0.0
    rank_position: int = 0
    total_candidates: int = 0
    strategy_weight: float = 1.0
    strategy_health: str = ""
    execution_latency_ms: float = 0.0


class FeatureStore:
    """
    Persistent feature store for operational data.

    Stores features as JSONL files for analysis and future ML.
    """

    def __init__(self, store_dir: str = "data/features") -> None:
        self._store_dir = Path(store_dir)
        self._store_dir.mkdir(parents=True, exist_ok=True)
        self._buffer: List[FeatureRecord] = []
        self._buffer_limit = 50
        self._memory: List[FeatureRecord] = []
        self._memory_limit = 1000

    def record(self, feature: FeatureRecord) -> None:
        """Add a feature record."""
        if feature.timestamp == 0:
            feature.timestamp = time.time()
        self._buffer.append(feature)
        self._memory.append(feature)

        if len(self._memory) > self._memory_limit:
            self._memory = self._memory[-self._memory_limit // 2:]

        if len(self._buffer) >= self._buffer_limit:
            self.flush()

    def flush(self) -> None:
        """Write buffered records to disk."""
        if not self._buffer:
            return
        date_str = time.strftime("%Y-%m-%d")
        filepath = self._store_dir / f"features_{date_str}.jsonl"
        try:
            with open(filepath, "a", encoding="utf-8") as f:
                for rec in self._buffer:
                    f.write(json.dumps(asdict(rec), default=str) + "\n")
            self._buffer.clear()
        except Exception as exc:
            logger.error("Failed to flush features: %s", exc)

    def close(self) -> None:
        """Flush and close."""
        self.flush()

    def recent(self, n: int = 50) -> List[FeatureRecord]:
        """Get recent feature records from memory."""
        return self._memory[-n:]

    def recent_for_asset(self, asset: str, n: int = 20) -> List[FeatureRecord]:
        """Get recent features for a specific asset."""
        return [f for f in self._memory if f.asset == asset][-n:]

    def recent_winrate(self, n: int = 20) -> float:
        """Calculate winrate from recent completed trades."""
        decided = [f for f in self._memory if f.result in ("win", "loss")][-n:]
        if not decided:
            return 0.5
        wins = sum(1 for f in decided if f.result == "win")
        return wins / len(decided)

    def avg_score_by_regime(self, regime: str) -> float:
        """Average score for trades in a given regime."""
        matching = [f for f in self._memory if f.regime == regime and f.score > 0]
        if not matching:
            return 0.5
        return sum(f.score for f in matching) / len(matching)

    def block_rate_by_hour(self) -> Dict[int, float]:
        """Block rate by hour of day."""
        hours: Dict[int, List[bool]] = {}
        for f in self._memory:
            h = f.hour_utc
            blocked = f.context_decision == "block"
            hours.setdefault(h, []).append(blocked)
        return {
            h: sum(1 for b in vals if b) / len(vals)
            for h, vals in sorted(hours.items())
        }
