"""
main.py — Syntrix v2.0 entry point.

"Selecionar melhor é mais importante do que prever melhor."

Quantitative Adaptive Operating System.

Pipeline:
MULTI-ASSET SCAN -> RAW SIGNALS -> META SCORE -> OPPORTUNITY RANKING
-> DECISION ENGINE -> RISK ENGINE -> EXECUTION -> RESULT

Supports: --headless, --diagnostic, --profile, --interval, --config, --shadow
"""

from __future__ import annotations

import argparse
import datetime
import logging
import os
import signal
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from analytics.calibration import ProbabilityCalibrationEngine
from analytics.edge_discovery import EdgeDiscoveryEngine
from analytics.learning import AdaptiveLearningLayer
from analytics.operation_report import CycleRecord, OperationReport
from analytics.trade_analytics import TradeAnalytics, TradeRecord
from assets.asset_dna import AssetDNAManager
from brokers.base import TradeDirection
from brokers.iqoption.adapter import IQOptionAdapter
from config.loader import ConfigLoader
from context.context_gate import ContextGate
from context.decision_trace import DecisionTrace, TraceCollector
from context.market_phase import MarketPhaseDetector
from context.market_regime import MarketRegimeDetector
from context.microstructure import MicrostructureEngine
from context.news_filter import NewsFilter
from context.otc_analyzer import OTCBehaviorAnalyzer
from context.session_filter import SessionFilter
from context.spike_detector import SpikeDetector
from core.events import EventBus, EventPriority, EventType
from core.states import OperationalState, StateMachine
from decision.engine import DecisionEngine, Opportunity
from event_store.sqlite_store import SQLiteEventStore
from execution.engine import ExecutionEngine
from execution.position_lock import PositionInfo, PositionLockManager, PositionState
from execution.shadow_mode import ShadowMode, ShadowTrade
from features.feature_store import FeatureRecord, FeatureStore
from trade_logging.trade_logger import TradeLogger, TradeSnapshot
from replay.engine import ReplayEngine
from risk.risk_engine import RiskConfig, RiskEngine
from scanner.asset_registry import AssetRegistry
from scanner.quality_analyzer import AssetQualityAnalyzer
from strategies.base import StrategySignal
from strategies.breakout import BreakoutStrategy
from strategies.health_monitor import StrategyHealthMonitor
from strategies.health_monitor import TradeRecord as HealthTradeRecord
from strategies.meta_scoring import MetaScoringEngine
from strategies.momentum import MomentumStrategy
from strategies.range_reversal import RangeReversalStrategy
from strategies.scoring import ScoringEngine
from strategies.trend_pullback import TrendPullbackStrategy
from watchdog.watchdog import Watchdog

logger = logging.getLogger("syntrix")


def load_env(env_path: str = ".env") -> None:
    """Load environment variables from .env file."""
    path = Path(env_path)
    if not path.exists():
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def setup_logging(level: str = "INFO") -> None:
    """Configure structured logging."""
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=fmt,
        datefmt="%Y-%m-%d %H:%M:%S",
    )


class Syntrix:
    """
    Syntrix v2.0 — Quantitative Adaptive Operating System.

    Multi-asset probabilistic opportunity engine.
    Scans dozens of assets, ranks opportunities, executes only the best.
    """

    def __init__(
        self,
        config_path: Optional[str] = None,
        profile: Optional[str] = None,
        diagnostic: bool = False,
        shadow: bool = False,
    ) -> None:
        self._config = ConfigLoader(config_path) if config_path else ConfigLoader()
        self._profile_name = profile or self._config.get_default_profile_name()
        self._mode = self._config.get_mode()
        self._session_id = str(uuid.uuid4())[:8]
        self._running = False
        self._diagnostic = diagnostic
        self._shadow = shadow

        # Core
        self._event_bus = EventBus(num_workers=4)
        self._state_machine = StateMachine(self._event_bus)

        # Persistence
        self._event_store = SQLiteEventStore(
            db_path=self._config.get_db_path(),
            session_id=self._session_id,
        )

        # Broker
        self._broker = IQOptionAdapter(
            email=os.environ.get("IQ_EMAIL", ""),
            password=os.environ.get("IQ_PASSWORD", ""),
            practice=os.environ.get("IQ_PRACTICE", "true").lower() == "true",
        )

        # Context modules
        self._session_filter = SessionFilter()
        self._news_filter = NewsFilter()
        self._regime_detector = MarketRegimeDetector()
        self._spike_detector = SpikeDetector()
        self._otc_analyzer = OTCBehaviorAnalyzer()
        self._phase_detector = MarketPhaseDetector()
        self._microstructure = MicrostructureEngine()

        profile_data = self._config.get_profile(self._profile_name)
        ctx_cfg = profile_data.get("context", {})

        self._context_gate = ContextGate(
            session_filter=self._session_filter,
            news_filter=self._news_filter,
            regime_detector=self._regime_detector,
            spike_detector=self._spike_detector,
            otc_analyzer=self._otc_analyzer,
            min_payout=ctx_cfg.get("min_payout", 0.65),
            max_latency_ms=ctx_cfg.get("max_latency_ms", 500.0),
            max_trades_per_hour=ctx_cfg.get("max_trades_per_hour", 10),
            max_drawdown_pct=ctx_cfg.get("max_drawdown_pct", 10.0),
            cooldown_seconds=ctx_cfg.get("cooldown_seconds", 30),
            base_threshold=profile_data.get("scoring", {}).get("min_final_score", 0.40),
        )

        # Risk
        risk_config = self._config.get_risk_config(self._profile_name)
        self._risk_engine = RiskEngine(self._event_bus, risk_config)

        # Execution
        exec_config = self._config.get_execution_config(self._profile_name)
        self._execution_engine = ExecutionEngine(self._broker, self._event_bus, exec_config)

        # Position lock (single position at a time)
        self._position_lock = PositionLockManager(
            cooldown_sec=ctx_cfg.get("cooldown_seconds", 30),
        )

        # Strategies
        self._strategies = [
            TrendPullbackStrategy(),
            RangeReversalStrategy(),
            BreakoutStrategy(),
            MomentumStrategy(),
        ]
        self._scoring = ScoringEngine(
            min_final_score=self._config.get_scoring_min_score(self._profile_name)
        )

        # Meta scoring engine
        self._meta_scoring = MetaScoringEngine(
            base_threshold=profile_data.get("scoring", {}).get("min_final_score", 0.40),
        )

        # Decision engine
        self._decision_engine = DecisionEngine(
            min_confidence=0.30,
            min_meta_score=0.35,
        )

        # Strategy health monitor
        self._health_monitor = StrategyHealthMonitor()

        # Asset DNA
        self._asset_dna = AssetDNAManager()

        # Asset registry
        assets_from_config = self._config.get_assets()
        self._asset_registry = AssetRegistry(
            include_otc=True,
            include_forex=True,
            custom_assets=assets_from_config,
        )

        # Asset quality analyzer
        self._quality_analyzer = AssetQualityAnalyzer()

        # Analytics
        self._analytics = TradeAnalytics()

        # Logging
        self._trade_logger = TradeLogger(
            log_dir=self._config.get_trade_log_dir()
        )

        # Replay
        self._replay = ReplayEngine(self._event_bus)

        # Watchdog
        self._watchdog = Watchdog(self._event_bus)

        # Decision trace collector
        self._trace_collector = TraceCollector()

        # Feature store
        self._feature_store = FeatureStore()

        # Shadow mode
        self._shadow_mode = ShadowMode() if shadow else None

        # Operation report
        self._op_report = OperationReport()

        # Probability calibration
        self._calibration = ProbabilityCalibrationEngine()

        # Edge discovery
        self._edge_discovery = EdgeDiscoveryEngine()

        # Adaptive learning
        self._learning = AdaptiveLearningLayer()

        # UI
        self._ui = None

    def start(self, headless: bool = False) -> None:
        """Start the Syntrix system."""
        setup_logging(self._config.get_log_level())

        logger.info("=" * 50)
        logger.info("  SYNTRIX v2.0 — Quantitative Adaptive System")
        logger.info("  Session: %s", self._session_id)
        logger.info("  Profile: %s", self._profile_name)
        logger.info("  Mode: %s", self._mode)
        logger.info("  Assets: %d registered", len(self._asset_registry.all_assets))
        if self._diagnostic:
            logger.info("  DIAGNOSTIC MODE ACTIVE")
        if self._shadow:
            logger.info("  SHADOW MODE ACTIVE (no real orders)")
        logger.info("=" * 50)

        # Start event bus
        self._event_bus.start()
        self._event_store.attach_to_bus(self._event_bus)

        # Start watchdog
        self._watchdog.start()

        # Emit system start
        self._event_bus.emit(
            EventType.SYSTEM_START,
            data={
                "session_id": self._session_id,
                "profile": self._profile_name,
                "mode": self._mode,
                "assets": len(self._asset_registry.all_assets),
            },
            priority=EventPriority.CRITICAL,
        )

        # Connect broker (only in demo/live mode)
        if self._mode != "dry-run":
            logger.info("Connecting to broker...")
            if self._broker.connect():
                balance = self._broker.get_balance()
                self._risk_engine.set_starting_balance(balance)
                logger.info("Balance: $%.2f", balance)
                self._watchdog.set_broker_health_fn(
                    lambda: vars(self._broker.health_check())
                )
                # Discover open assets from broker
                discovered = self._asset_registry.discover_from_broker(self._broker)
                if discovered:
                    logger.info("Discovered %d additional assets from broker", discovered)
            else:
                logger.error("Broker connection failed — switching to dry-run")
                self._mode = "dry-run"
        else:
            self._risk_engine.set_starting_balance(1000.0)
            logger.info("Dry-run mode — no broker connection")

        # Start UI
        if not headless:
            try:
                from ui.app import SyntrixUI, UILogHandler
                self._ui = SyntrixUI()
                ui_handler = UILogHandler(self._ui)
                ui_handler.setFormatter(
                    logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
                )
                logging.getLogger("syntrix").addHandler(ui_handler)
                self._ui.start(on_stop=self.stop)
            except Exception as exc:
                logger.warning("UI not available: %s — running headless", exc)

        self._running = True
        self._state_machine.transition(OperationalState.SCANNING, reason="System start")

        logger.info("Syntrix started successfully")

    def run_cycle(self) -> None:
        """
        Run one scanning cycle.

        Multi-asset pipeline:
        1. Scan all assets -> raw signals
        2. Apply meta scoring -> confidence
        3. Rank opportunities -> global ranking
        4. Decision engine -> select best
        5. Risk check -> validate
        6. Execute -> single position
        """
        if not self._running:
            return

        if self._risk_engine.is_locked or self._risk_engine.is_safe_mode:
            return

        # Check position lock
        if self._position_lock.is_locked:
            lock_state = self._position_lock.state
            if lock_state == PositionState.COOLDOWN:
                logger.debug("Position in cooldown")
            return

        correlation_id = str(uuid.uuid4())[:8]
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        hour_utc = now_utc.hour

        # Get assets to scan
        assets = self._asset_registry.get_scan_order()
        if not assets:
            return

        self._state_machine.transition(OperationalState.SCANNING, reason=f"Scanning {len(assets)} assets")

        # Phase 1: Scan all assets and generate opportunities
        opportunities: List[Opportunity] = []
        scan_start = time.time()

        for asset in assets:
            if not self._running:
                break

            opp = self._scan_asset(asset, correlation_id, hour_utc)
            if opp:
                opportunities.append(opp)

        scan_ms = (time.time() - scan_start) * 1000

        if self._diagnostic:
            print(f"\n  SCAN: {len(assets)} assets, {len(opportunities)} candidates, {scan_ms:.0f}ms")

        if not opportunities:
            if self._running:
                self._state_machine.transition(OperationalState.SCANNING, reason="Cycle complete")
                self._print_diagnostic_summary()
            return

        # Phase 2: Decision engine selects the best
        self._state_machine.transition(OperationalState.READY, reason="Evaluating opportunities")

        risk_snap = self._risk_engine.get_snapshot()
        risk_budget = 1.0 - (risk_snap["drawdown_pct"] / 100.0)

        decision = self._decision_engine.evaluate(
            opportunities=opportunities,
            risk_budget=risk_budget,
            correlation_id=correlation_id,
        )

        if not decision.selected:
            logger.info(
                "Decision: no selection (%d candidates, %d rejected) — %s",
                decision.total_candidates, decision.total_rejected, decision.reason,
            )
            if self._diagnostic:
                print(f"  DECISION: NONE — {decision.reason}")
                for rej in decision.rejected[:5]:
                    print(f"    REJECT: {rej.asset} {rej.direction} — {', '.join(rej.block_reasons)}")
            self._state_machine.transition(OperationalState.SCANNING, reason="Cycle complete")
            self._print_diagnostic_summary()
            return

        best = decision.selected
        logger.info(
            "SELECTED: %s %s %s (rank=%.3f meta=%.3f conf=%.2f payout=%.0f%%)",
            best.asset, best.direction, best.strategy,
            best.rank_score, best.meta_score, best.confidence, best.payout * 100,
        )

        if self._diagnostic:
            print(f"\n  SELECTED: {best.asset} {best.direction}")
            print(f"    Strategy: {best.strategy}")
            print(f"    Meta Score: {best.meta_score:.4f}")
            print(f"    Confidence: {best.confidence:.3f}")
            print(f"    Rank Score: {best.rank_score:.4f}")
            print(f"    Payout: {best.payout:.0%}")
            print(f"    Regime: {best.regime} | Phase: {best.market_phase}")

        # Phase 3: Risk check
        risk_result = self._risk_engine.check_trade_allowed(payout=best.payout)
        if not risk_result["allowed"]:
            logger.info("Risk blocked: %s", risk_result["reasons"])
            self._feature_store.record(FeatureRecord(
                asset=best.asset, regime=best.regime, payout=best.payout,
                hour_utc=hour_utc, score=best.meta_score,
                strategy=best.strategy, direction=best.direction,
                context_decision="allow", confidence=best.confidence,
                meta_score=best.meta_score, market_phase=best.market_phase,
                rank_score=best.rank_score, result="risk_blocked",
            ))
            self._state_machine.transition(OperationalState.SCANNING, reason="Cycle complete")
            return

        # Phase 4: Acquire position lock and execute
        trade_direction = TradeDirection.CALL if best.direction == "call" else TradeDirection.PUT
        amount = self._config.get_trade_amount(self._profile_name)
        duration = self._config.get_trade_duration(self._profile_name)

        # Shadow mode: record everything but don't execute
        if self._shadow:
            shadow_trade = ShadowTrade(
                timestamp=time.time(), asset=best.asset,
                direction=best.direction, strategy=best.strategy,
                regime=best.regime, market_phase=best.market_phase,
                hour_utc=hour_utc, payout=best.payout,
                base_score=best.meta_score, meta_score=best.meta_score,
                confidence=best.confidence, rank_score=best.rank_score,
                asset_quality=best.asset_quality, otc_quality=best.otc_quality,
                volatility=best.volatility, latency_ms=best.latency_ms,
                strategy_consensus=len(best.individual_scores),
                threshold_used=0, decision="SHADOW_EXECUTE",
                correlation_id=correlation_id,
            )
            self._shadow_mode.record_signal(shadow_trade)
            logger.info(
                "SHADOW: %s %s %s meta=%.3f rank=%.3f payout=%.0f%%",
                best.direction, best.asset, best.strategy,
                best.meta_score, best.rank_score, best.payout * 100,
            )
            self._state_machine.transition(OperationalState.SCANNING, reason="Shadow cycle complete")
            return

        pos_info = PositionInfo(
            asset=best.asset,
            direction=best.direction,
            strategy=best.strategy,
            amount=amount,
            duration_sec=duration,
            payout=best.payout,
            meta_score=best.meta_score,
            correlation_id=correlation_id,
        )

        if not self._position_lock.try_acquire(pos_info):
            logger.info("Position lock denied — already in position")
            self._state_machine.transition(OperationalState.SCANNING, reason="Cycle complete")
            return

        self._state_machine.transition(OperationalState.EXECUTING, reason="Executing trade")

        snapshot = TradeSnapshot(
            asset=best.asset,
            direction=best.direction,
            amount=amount,
            duration=duration,
            payout=best.payout,
            strategy=best.strategy,
            score=best.meta_score,
            confidence=best.confidence,
            regime=best.regime,
            profile=self._profile_name,
            mode=self._mode,
            context_decision="allow",
            risk_check="passed",
            state_at_entry=self._state_machine.state.value,
            pnl_at_entry=self._risk_engine.state.pnl,
            drawdown_at_entry=risk_snap["drawdown_pct"],
            timestamp_signal=time.time(),
        )

        exec_record = self._execution_engine.execute(
            asset=best.asset,
            direction=trade_direction,
            amount=amount,
            duration=duration,
            correlation_id=correlation_id,
        )

        snapshot.executed = exec_record.success
        snapshot.trade_id = exec_record.trade_id
        snapshot.broker_id = exec_record.broker_id
        snapshot.latency_ms = exec_record.latency_ms
        snapshot.jitter_ms = exec_record.jitter_ms
        snapshot.correlation_id = correlation_id
        snapshot.timestamp_execution = time.time()

        if exec_record.success:
            self._position_lock.confirm_open(exec_record.broker_id)
            self._context_gate.record_trade(best.asset)
            self._scoring.record_strategy_trade(best.strategy, best.direction)

            # Wait for result
            trade_result = self._execution_engine.wait_for_result(
                broker_id=exec_record.broker_id,
                trade_id=exec_record.trade_id,
                correlation_id=correlation_id,
            )

            snapshot.result = trade_result.result
            snapshot.profit = trade_result.profit
            snapshot.timestamp_result = time.time()

            # Close position and enter cooldown
            self._position_lock.close_position(trade_result.result)

            won = trade_result.result == "win"

            # Update risk engine
            self._risk_engine.record_trade_result(trade_result.profit)

            # Update scoring
            self._scoring.update_history(best.strategy, won)

            # Update strategy health
            self._health_monitor.record_trade(HealthTradeRecord(
                timestamp=time.time(),
                strategy=best.strategy,
                asset=best.asset,
                won=won,
                profit=trade_result.profit,
                score=best.meta_score,
                payout=best.payout,
                regime=best.regime,
                hour_utc=hour_utc,
            ))

            # Update asset DNA
            self._asset_dna.record_trade(
                asset=best.asset, won=won,
                hour_utc=hour_utc, strategy=best.strategy,
                score=best.meta_score,
            )
            self._asset_dna.update_adaptive_params(best.asset)

            # Update asset registry
            self._asset_registry.record_trade_result(best.asset, won, best.payout)

            # Update analytics
            self._analytics.add_trade(TradeRecord(
                trade_id=exec_record.trade_id,
                asset=best.asset,
                direction=best.direction,
                strategy=best.strategy,
                regime=best.regime,
                profile=self._profile_name,
                payout=best.payout,
                amount=amount,
                result=trade_result.result,
                profit=trade_result.profit,
                timestamp=time.time(),
                score=best.meta_score,
            ))

            # Record feature
            self._feature_store.record(FeatureRecord(
                asset=best.asset, regime=best.regime, payout=best.payout,
                hour_utc=hour_utc, score=best.meta_score,
                confidence=best.confidence,
                strategy=best.strategy, direction=best.direction,
                context_decision="allow",
                latency_ms=exec_record.latency_ms,
                meta_score=best.meta_score,
                market_phase=best.market_phase,
                asset_quality=best.asset_quality,
                rank_score=best.rank_score,
                rank_position=best.rank,
                total_candidates=decision.total_candidates,
                strategy_weight=self._health_monitor.get_weight(best.strategy),
                strategy_health="healthy" if self._health_monitor.get_health(best.strategy).is_healthy else "degraded",
                execution_latency_ms=exec_record.latency_ms,
                result=trade_result.result,
                profit=trade_result.profit,
            ))

            # Feed calibration + edge discovery + learning
            self._calibration.record(
                confidence=best.confidence, won=won, payout=best.payout,
                asset=best.asset, strategy=best.strategy,
                regime=best.regime, hour_utc=hour_utc,
                meta_score=best.meta_score,
            )
            self._edge_discovery.record_trade(
                asset=best.asset, strategy=best.strategy,
                regime=best.regime, market_phase=best.market_phase,
                hour_utc=hour_utc, won=won, payout=best.payout,
                meta_score=best.meta_score, confidence=best.confidence,
            )
            self._learning.record_outcome(
                strategy=best.strategy, asset=best.asset, won=won,
                confidence=best.confidence, meta_score=best.meta_score,
                payout=best.payout, regime=best.regime, hour_utc=hour_utc,
            )

            logger.info(
                "TRADE %s %s %s $%.2f → %s ($%.2f) latency=%.0fms",
                best.direction, best.asset, best.strategy,
                amount, trade_result.result, trade_result.profit,
                exec_record.latency_ms,
            )
        else:
            self._position_lock.force_release("execution_failed")
            logger.error("Execution failed: %s", exec_record.error)

        self._trade_logger.log_trade(snapshot)

        # Cooldown
        self._state_machine.transition(OperationalState.COOLDOWN, reason="Post-trade cooldown")

        # Return to scanning
        if self._running:
            self._state_machine.transition(OperationalState.SCANNING, reason="Cycle complete")
            self._print_diagnostic_summary()

    def _scan_asset(
        self, asset: str, correlation_id: str, hour_utc: int,
    ) -> Optional[Opportunity]:
        """
        Scan a single asset and return an Opportunity if qualified.

        Pipeline: candles -> context -> regime -> phase -> microstructure
                  -> strategies -> ensemble -> meta score -> opportunity
        """
        self._asset_registry.record_scan(asset)

        # Get market data
        candles = []
        payout = 0.0
        if self._mode != "dry-run" and self._broker.is_connected:
            candles = self._broker.get_candles(asset, 60, 50)
            payout = self._broker.get_payout(asset)
        else:
            return None

        if not candles:
            return None

        # Asset quality
        quality = self._quality_analyzer.analyze(asset, candles, payout)
        self._asset_registry.update_quality(asset, quality.quality_score)

        # Context evaluation
        ctx_result = self._context_gate.evaluate(
            asset=asset,
            payout=payout,
            candles=candles,
            broker_latency_ms=self._broker.avg_latency_ms,
            current_drawdown_pct=self._risk_engine.get_snapshot()["drawdown_pct"],
            broker_healthy=self._broker.is_connected,
            correlation_id=correlation_id,
        )

        # Store trace
        trace = ctx_result.get("trace")
        if trace:
            self._trace_collector.add(trace)

        if self._diagnostic and trace:
            print(trace.format_diagnostic())

        if ctx_result["blocked"]:
            self._asset_registry.record_block(asset)
            block_reasons = ctx_result.get("block_reasons", [])
            block_str = ", ".join(block_reasons) if block_reasons else "confidence_too_low"
            # Report blocked signal
            self._op_report.record_signal(
                allowed=False,
                confidence=ctx_result.get("confidence", 0),
                block_reason=block_str,
            )
            # Shadow mode: record blocked signals too
            if self._shadow_mode:
                shadow_trade = ShadowTrade(
                    timestamp=time.time(), asset=asset,
                    direction="", payout=payout,
                    confidence=ctx_result.get("confidence", 0),
                    block_reasons=block_str,
                    decision="BLOCK", correlation_id=correlation_id,
                    hour_utc=hour_utc,
                )
                self._shadow_mode.record_signal(shadow_trade)
            # Record feature for blocked
            self._feature_store.record(FeatureRecord(
                asset=asset, regime="", payout=payout,
                hour_utc=hour_utc, context_decision="block",
                confidence=ctx_result.get("confidence", 0),
                threshold_used=ctx_result.get("threshold", 0),
                block_reasons=block_reasons,
                result="blocked",
            ))
            return None

        context_confidence = ctx_result.get("confidence", 1.0)

        # Regime + Phase detection
        regime = self._regime_detector.detect(candles)
        phase_analysis = self._phase_detector.detect(candles, asset)

        # Microstructure
        micro = self._microstructure.analyze(candles)

        # OTC quality
        otc_quality = 1.0
        if asset.endswith("-OTC"):
            otc_result = self._otc_analyzer.analyze(candles, asset)
            otc_quality = otc_result.quality_score

        # Update asset DNA
        self._asset_dna.record_scan(
            asset=asset, volatility=phase_analysis.volatility,
            payout=payout, score=0, spike_detected=False,
            otc_noise=phase_analysis.volatility if asset.endswith("-OTC") else 0,
        )

        # Evaluate strategies
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
            self._asset_registry.record_signal(asset)  # scanned but no signal
            return None

        self._asset_registry.record_signal(asset)

        # Ensemble scoring
        ensemble_result = self._scoring.score_ensemble(
            signals=compatible_signals,
            regime=regime,
            payout=payout,
            context_quality=context_confidence,
        )

        # In shadow mode, don't filter by ensemble pass (we want ALL signals)
        if not ensemble_result["passed"] and not self._shadow:
            return None

        best_signal = ensemble_result.get("best_signal")
        if not best_signal:
            return None

        # Meta scoring
        dna = self._asset_dna.get_or_create(asset)
        strategy_weight = self._health_monitor.get_weight(best_signal.strategy_name)

        meta_result = self._meta_scoring.calculate(
            base_score=ensemble_result["final_score"],
            regime=regime.value,
            market_phase=phase_analysis.phase.value,
            payout=payout,
            volatility=phase_analysis.volatility,
            otc_quality=otc_quality,
            asset_quality=quality.quality_score,
            hour_utc=hour_utc,
            latency_ms=self._broker.avg_latency_ms,
            strategy_consensus=len(compatible_signals),
            historical_winrate=dna.winrate,
            asset=asset,
            strategy=best_signal.strategy_name,
            adaptive_threshold=self._context_gate.get_adaptive_threshold(
                asset, regime.value, hour_utc,
            ) + self._asset_dna.get_adaptive_threshold(asset),
        )

        if not meta_result.passed and not self._shadow:
            if self._diagnostic:
                print(f"    [{asset}] META REJECT: {meta_result.reason}")
            return None

        # Build opportunity
        opp = Opportunity(
            asset=asset,
            direction=best_signal.direction.value,
            strategy=best_signal.strategy_name,
            meta_score=meta_result.meta_score,
            confidence=context_confidence,
            payout=payout,
            regime=regime.value,
            market_phase=phase_analysis.phase.value,
            asset_quality=quality.quality_score,
            otc_quality=otc_quality,
            latency_ms=self._broker.avg_latency_ms,
            volatility=phase_analysis.volatility,
            timestamp=time.time(),
            signal=best_signal,
            trace=trace,
            individual_scores=ensemble_result.get("individual_scores", {}),
        )

        # Compute rank score
        profile = self._asset_registry.get_profile(asset)
        hist_wr = profile.winrate if profile and profile.total_trades >= 5 else 0.5

        opp.rank_score = round(
            meta_result.meta_score * 0.35
            + min(1.0, payout / 0.90) * 0.20
            + quality.quality_score * 0.15
            + context_confidence * 0.15
            + micro.quality_score * 0.05
            + hist_wr * 0.10,
            4,
        )

        # Record allowed signal
        self._op_report.record_signal(
            allowed=True, confidence=context_confidence,
        )

        if self._diagnostic:
            print(f"    [{asset}] {best_signal.direction.value} meta={meta_result.meta_score:.4f} rank={opp.rank_score:.4f} payout={payout:.0%}")

        return opp

    def _print_diagnostic_summary(self) -> None:
        """Print diagnostic summary at end of cycle."""
        if not self._diagnostic:
            return

        # Update UI
        if self._ui:
            risk_snap = self._risk_engine.get_snapshot()
            self._ui.update_data({
                "state": self._state_machine.state.value,
                "profile": self._profile_name,
                "mode": self._mode,
                "pnl": risk_snap["pnl"],
                "total_trades": risk_snap["total_trades"],
                "drawdown_pct": risk_snap["drawdown_pct"],
                "winrate": self._analytics.winrate(),
                "allow_rate": f"{self._trace_collector.allow_rate():.0%}",
                "avg_confidence": f"{self._trace_collector.avg_confidence():.2f}",
            })

        block_rates = self._trace_collector.block_rate_by_reason()
        if block_rates:
            print("\n  Block frequency:", " | ".join(f"{k}={v}" for k, v in list(block_rates.items())[:5]))
        print(f"  Allow rate: {self._trace_collector.allow_rate():.0%}  Avg confidence: {self._trace_collector.avg_confidence():.2f}")

        # Position lock status
        lock_stats = self._position_lock.get_stats()
        print(f"  Position: {lock_stats['state']} | Trades: {lock_stats['total_trades']} | Blocked: {lock_stats['total_blocked']}")

        # Decision stats
        dec_stats = self._decision_engine.get_decision_stats()
        if dec_stats.get("total_decisions", 0) > 0:
            print(f"  Decisions: {dec_stats['total_decisions']} | Selection rate: {dec_stats['recent_selection_rate']:.0f}%")

        # Strategy health
        health = self._health_monitor.get_all_health()
        if health:
            health_str = " | ".join(f"{n}={h.weight:.2f}" for n, h in health.items())
            print(f"  Strategy weights: {health_str}")

        print()

    def run(self, interval: float = 60.0) -> None:
        """Run the main loop."""
        logger.info("Main loop started (interval=%.0fs)", interval)

        # Update UI on each cycle (non-diagnostic too)
        try:
            while self._running:
                self.run_cycle()

                # Periodic updates
                self._asset_registry.update_priorities()

                # Update UI
                if self._ui and not self._diagnostic:
                    risk_snap = self._risk_engine.get_snapshot()
                    self._ui.update_data({
                        "state": self._state_machine.state.value,
                        "profile": self._profile_name,
                        "mode": self._mode,
                        "pnl": risk_snap["pnl"],
                        "total_trades": risk_snap["total_trades"],
                        "drawdown_pct": risk_snap["drawdown_pct"],
                        "winrate": self._analytics.winrate(),
                        "allow_rate": f"{self._trace_collector.allow_rate():.0%}",
                        "avg_confidence": f"{self._trace_collector.avg_confidence():.2f}",
                    })

                time.sleep(interval)
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received")
        finally:
            self.stop()

    def stop(self) -> None:
        """Gracefully stop the system."""
        if not self._running:
            return
        self._running = False

        logger.info("Stopping Syntrix...")

        self._state_machine.transition(OperationalState.IDLE, reason="System stop")

        self._event_bus.emit(
            EventType.SYSTEM_STOP,
            data={"session_id": self._session_id},
            priority=EventPriority.CRITICAL,
        )

        # Save asset DNA
        self._asset_dna.save()

        # Flush feature store
        self._feature_store.close()

        # Print operation report
        print(self._op_report.format_report())

        # Print shadow report if shadow mode
        if self._shadow_mode:
            print(self._shadow_mode.get_report())
            self._shadow_mode.close()

        # Print calibration report if enough data
        cal = self._calibration.calibrate()
        if cal.total_trades >= 10:
            print(self._calibration.format_report())

        # Print edge discovery if enough data
        edge = self._edge_discovery.discover()
        if edge.total_trades >= 20:
            print(self._edge_discovery.format_report())

        # Print learning state
        if self._learning.get_state().total_updates > 0:
            print(self._learning.format_report())

        # Flush trade logs
        self._trade_logger.close()

        # Stop watchdog
        self._watchdog.stop()

        # Release position lock
        if self._position_lock.is_locked:
            self._position_lock.force_release("system_stop")

        # Stop UI
        if self._ui:
            self._ui.stop()

        # Disconnect broker
        if self._broker.is_connected:
            self._broker.disconnect()

        # Stop event bus (last — let final events flush)
        time.sleep(0.5)
        self._event_bus.stop()

        # Print session summary
        summary = self._trade_logger.get_summary()
        analytics = self._analytics.full_report()

        logger.info("=" * 50)
        logger.info("  SESSION SUMMARY")
        logger.info("  Trades: %d (executed: %d, blocked: %d)",
                     summary["total_signals"], summary["executed"], summary["blocked"])
        logger.info("  Wins: %d | Losses: %d | Winrate: %.1f%%",
                     summary["wins"], summary["losses"], summary["winrate"])
        logger.info("  PnL: $%.2f", summary["total_profit"])
        logger.info("  Sharpe: %.3f", analytics.get("sharpe_ratio", 0))
        logger.info("  Allow Rate: %.0f%%", self._trace_collector.allow_rate() * 100)
        logger.info("  Avg Confidence: %.2f", self._trace_collector.avg_confidence())

        # Asset registry stats
        reg_stats = self._asset_registry.get_stats()
        logger.info("  Assets: %d open / %d registered", reg_stats["total_open"], reg_stats["total_registered"])

        # Position lock stats
        lock_stats = self._position_lock.get_stats()
        logger.info("  Position Lock: %d trades, %d blocked", lock_stats["total_trades"], lock_stats["total_blocked"])

        # Strategy health
        for name, health in self._health_monitor.get_all_health().items():
            logger.info("  Strategy %s: weight=%.2f wr=%.0f%% %s",
                        name, health.weight, health.recent_winrate * 100,
                        f"⚠ {health.warning}" if health.warning else "")

        block_rates = self._trace_collector.block_rate_by_reason()
        if block_rates:
            logger.info("  Top blocks: %s",
                        " | ".join(f"{k}={v}" for k, v in list(block_rates.items())[:5]))

        logger.info("=" * 50)

    def get_analytics(self) -> dict:
        """Get full analytics report."""
        return self._analytics.full_report()

    def get_health(self) -> dict:
        """Get system health report."""
        return self._watchdog.get_health_report()


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Syntrix v2.0 — Quantitative Adaptive System")
    parser.add_argument("--config", type=str, help="Path to profiles.yaml")
    parser.add_argument("--profile", type=str, help="Profile name (leve, moderado, etc)")
    parser.add_argument("--headless", action="store_true", help="Run without UI")
    parser.add_argument("--diagnostic", action="store_true",
                        help="Enable diagnostic mode (detailed trace output)")
    parser.add_argument("--interval", type=float, default=60.0, help="Scan interval in seconds")
    parser.add_argument("--shadow", action="store_true",
                        help="Shadow mode — simulate all trades without real orders")
    args = parser.parse_args()

    load_env()
    syntrix = Syntrix(
        config_path=args.config, profile=args.profile,
        diagnostic=args.diagnostic, shadow=args.shadow,
    )

    def signal_handler(sig, frame):
        syntrix.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    syntrix.start(headless=args.headless)
    syntrix.run(interval=args.interval)


if __name__ == "__main__":
    main()
