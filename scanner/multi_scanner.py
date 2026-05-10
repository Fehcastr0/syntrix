"""
scanner/multi_scanner.py — Multi-asset scanner for Syntrix.

Scans multiple assets in parallel, generates trade candidates,
ranks them globally, and selects the best opportunity.

Flow:
MULTIPLE ASSETS -> MULTIPLE ANALYSES -> MULTIPLE CANDIDATES
-> GLOBAL RANKING -> BEST OPPORTUNITY -> SINGLE EXECUTION
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from brokers.base import BaseBroker, Candle, TradeDirection
from context.context_gate import ContextGate
from context.decision_trace import DecisionTrace, TraceCollector
from context.market_regime import MarketRegime, MarketRegimeDetector
from context.otc_analyzer import OTCBehaviorAnalyzer
from scanner.asset_registry import AssetRegistry
from scanner.quality_analyzer import AssetQuality, AssetQualityAnalyzer
from strategies.base import BaseStrategy, StrategySignal
from strategies.scoring import ScoringEngine

logger = logging.getLogger("syntrix.scanner")


@dataclass
class TradeCandidate:
    """A trade candidate generated from scanning one asset."""

    asset: str = ""
    direction: str = ""  # CALL / PUT
    strategy: str = ""
    base_score: float = 0.0
    final_score: float = 0.0
    ensemble_score: float = 0.0
    confidence: float = 0.0
    payout: float = 0.0
    regime: str = ""
    volatility: float = 0.0
    asset_quality: float = 0.0
    otc_quality: float = 1.0
    latency_ms: float = 0.0
    timestamp: float = 0.0
    signal: Optional[StrategySignal] = None
    trace: Optional[DecisionTrace] = None
    rank_score: float = 0.0
    individual_scores: Dict[str, float] = field(default_factory=dict)


@dataclass
class ScanResult:
    """Result of scanning one asset."""

    asset: str = ""
    scanned: bool = False
    duration_ms: float = 0.0
    payout: float = 0.0
    regime: str = ""
    quality: float = 0.0
    context_decision: str = ""
    confidence: float = 0.0
    candidates: List[TradeCandidate] = field(default_factory=list)
    blocked: bool = False
    block_reasons: List[str] = field(default_factory=list)
    trace: Optional[DecisionTrace] = None
    error: str = ""


class MultiAssetScanner:
    """
    Scans multiple assets and generates ranked trade candidates.

    Uses thread pool for parallel asset scanning.
    """

    def __init__(
        self,
        broker: BaseBroker,
        context_gate: ContextGate,
        regime_detector: MarketRegimeDetector,
        scoring: ScoringEngine,
        strategies: List[BaseStrategy],
        registry: AssetRegistry,
        quality_analyzer: Optional[AssetQualityAnalyzer] = None,
        trace_collector: Optional[TraceCollector] = None,
        max_workers: int = 4,
        candle_count: int = 50,
        candle_timeframe: int = 60,
    ) -> None:
        self._broker = broker
        self._context_gate = context_gate
        self._regime_detector = regime_detector
        self._scoring = scoring
        self._strategies = strategies
        self._registry = registry
        self._quality_analyzer = quality_analyzer or AssetQualityAnalyzer()
        self._trace_collector = trace_collector or TraceCollector()
        self._max_workers = max_workers
        self._candle_count = candle_count
        self._candle_timeframe = candle_timeframe
        self._last_scan_results: List[ScanResult] = []

    def scan_all(self, correlation_id: str = "") -> List[TradeCandidate]:
        """
        Scan all enabled assets and return ranked candidates.

        Returns list of candidates sorted by rank (best first).
        """
        assets = self._registry.get_scan_order()

        if not assets:
            logger.warning("No assets to scan")
            return []

        start_time = time.time()
        results: List[ScanResult] = []

        # Scan assets (sequential for broker API safety)
        for asset in assets:
            try:
                result = self._scan_asset(asset, correlation_id)
                results.append(result)
            except Exception as exc:
                logger.error("[%s] Scan error: %s", asset, exc)
                results.append(ScanResult(asset=asset, error=str(exc)))

        self._last_scan_results = results

        # Collect all candidates
        all_candidates: List[TradeCandidate] = []
        for r in results:
            all_candidates.extend(r.candidates)

        # Rank candidates
        ranked = self._rank_candidates(all_candidates)

        scan_ms = (time.time() - start_time) * 1000
        logger.info(
            "Multi-scan: %d assets, %d candidates, %.0fms",
            len(assets), len(ranked), scan_ms,
        )

        if ranked:
            best = ranked[0]
            logger.info(
                "  TOP: %s %s (%s) rank=%.3f score=%.3f payout=%.0f%%",
                best.asset, best.direction, best.strategy,
                best.rank_score, best.final_score, best.payout * 100,
            )

        return ranked

    def _scan_asset(self, asset: str, correlation_id: str = "") -> ScanResult:
        """Scan a single asset."""
        start = time.time()
        result = ScanResult(asset=asset)
        self._registry.record_scan(asset)

        # Get market data
        if not self._broker.is_connected:
            result.error = "Broker not connected"
            return result

        candles = self._broker.get_candles(asset, self._candle_timeframe, self._candle_count)
        payout = self._broker.get_payout(asset)
        result.payout = payout

        if not candles:
            result.error = "No candles"
            return result

        # Quality analysis
        quality = self._quality_analyzer.analyze(asset, candles, payout)
        result.quality = quality.quality_score
        self._registry.update_quality(asset, quality.quality_score)

        # Context evaluation
        ctx_result = self._context_gate.evaluate(
            asset=asset,
            payout=payout,
            candles=candles,
            broker_latency_ms=self._broker.avg_latency_ms,
            current_drawdown_pct=0,  # will be checked at execution time
            broker_healthy=self._broker.is_connected,
            correlation_id=correlation_id,
        )

        result.context_decision = ctx_result["decision"]
        result.confidence = ctx_result.get("confidence", 0)
        result.trace = ctx_result.get("trace")

        if result.trace:
            self._trace_collector.add(result.trace)

        if ctx_result["blocked"]:
            result.blocked = True
            result.block_reasons = ctx_result.get("block_reasons", [])
            self._registry.record_block(asset)
            result.scanned = True
            result.duration_ms = (time.time() - start) * 1000
            return result

        # Detect regime
        regime = self._regime_detector.detect(candles)
        result.regime = regime.value

        # Evaluate strategies (ensemble)
        compatible_signals: List[StrategySignal] = []
        for strategy in self._strategies:
            if not strategy.is_regime_compatible(regime):
                continue
            if self._scoring.is_strategy_on_cooldown(strategy.name):
                continue
            signal = strategy.evaluate(candles, regime)
            if signal is not None:
                compatible_signals.append(signal)

        if not compatible_signals:
            result.scanned = True
            result.duration_ms = (time.time() - start) * 1000
            return result

        self._registry.record_signal(asset)

        # Ensemble scoring
        ensemble_result = self._scoring.score_ensemble(
            signals=compatible_signals,
            regime=regime,
            payout=payout,
            context_quality=result.confidence,
        )

        if not ensemble_result["passed"]:
            result.scanned = True
            result.duration_ms = (time.time() - start) * 1000
            return result

        # Generate candidate
        best_signal = ensemble_result.get("best_signal")
        if best_signal:
            candidate = TradeCandidate(
                asset=asset,
                direction=best_signal.direction.value,
                strategy=best_signal.strategy_name,
                base_score=best_signal.score,
                final_score=ensemble_result["final_score"],
                ensemble_score=ensemble_result["final_score"],
                confidence=result.confidence,
                payout=payout,
                regime=regime.value,
                asset_quality=quality.quality_score,
                latency_ms=self._broker.avg_latency_ms,
                timestamp=time.time(),
                signal=best_signal,
                trace=result.trace,
                individual_scores=ensemble_result.get("individual_scores", {}),
            )
            result.candidates.append(candidate)

        result.scanned = True
        result.duration_ms = (time.time() - start) * 1000
        return result

    def _rank_candidates(self, candidates: List[TradeCandidate]) -> List[TradeCandidate]:
        """
        Rank all candidates globally.

        Rank score considers:
        - Strategy score (40%)
        - Payout (20%)
        - Asset quality (15%)
        - Context confidence (15%)
        - Historical performance (10%)
        """
        for c in candidates:
            # Get asset profile for historical data
            profile = self._registry.get_profile(c.asset)

            score_component = c.final_score * 0.40
            payout_component = min(1.0, c.payout / 0.90) * 0.20
            quality_component = c.asset_quality * 0.15
            confidence_component = c.confidence * 0.15

            # Historical performance
            hist_component = 0.5  # neutral default
            if profile and profile.total_trades >= 5:
                hist_component = profile.winrate
            hist_weighted = hist_component * 0.10

            c.rank_score = round(
                score_component + payout_component + quality_component
                + confidence_component + hist_weighted,
                4,
            )

        # Sort by rank (highest first)
        candidates.sort(key=lambda x: x.rank_score, reverse=True)
        return candidates

    def get_best_candidate(self, correlation_id: str = "") -> Optional[TradeCandidate]:
        """Scan all assets and return the single best candidate."""
        ranked = self.scan_all(correlation_id)
        return ranked[0] if ranked else None

    @property
    def last_scan_results(self) -> List[ScanResult]:
        return self._last_scan_results

    def get_scan_summary(self) -> Dict[str, Any]:
        """Get summary of last scan."""
        if not self._last_scan_results:
            return {"scanned": 0, "candidates": 0}

        scanned = sum(1 for r in self._last_scan_results if r.scanned)
        blocked = sum(1 for r in self._last_scan_results if r.blocked)
        candidates = sum(len(r.candidates) for r in self._last_scan_results)
        errors = sum(1 for r in self._last_scan_results if r.error)

        return {
            "total_assets": len(self._last_scan_results),
            "scanned": scanned,
            "blocked": blocked,
            "candidates": candidates,
            "errors": errors,
            "block_rate": round(blocked / max(1, scanned) * 100, 1),
        }
