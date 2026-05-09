"""
main.py — Syntrix entry point.

"Selecionar melhor é mais importante do que prever melhor."

Boots up all subsystems, wires the event bus, and starts the main loop.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

from analytics.trade_analytics import TradeAnalytics, TradeRecord
from brokers.base import TradeDirection
from brokers.iqoption.adapter import IQOptionAdapter
from config.loader import ConfigLoader
from context.context_gate import ContextDecision, ContextGate
from context.market_regime import MarketRegimeDetector
from context.news_filter import NewsFilter
from context.session_filter import SessionFilter
from context.spike_detector import SpikeDetector
from core.events import EventBus, EventPriority, EventType
from core.states import OperationalState, StateMachine
from event_store.sqlite_store import SQLiteEventStore
from execution.engine import ExecutionEngine
from trade_logging.trade_logger import TradeLogger, TradeSnapshot
from replay.engine import ReplayEngine
from risk.risk_engine import RiskConfig, RiskEngine
from strategies.breakout import BreakoutStrategy
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
    Syntrix — Modular Quantitative Operating System.

    Orchestrates all subsystems through the event bus.
    """

    def __init__(self, config_path: Optional[str] = None, profile: Optional[str] = None) -> None:
        self._config = ConfigLoader(config_path) if config_path else ConfigLoader()
        self._profile_name = profile or self._config.get_default_profile_name()
        self._mode = self._config.get_mode()
        self._session_id = str(uuid.uuid4())[:8]
        self._running = False

        # Core
        self._event_bus = EventBus(num_workers=4)
        self._state_machine = StateMachine(self._event_bus)

        # Persistence
        self._event_store = SQLiteEventStore(
            db_path=self._config.get_db_path(),
            session_id=self._session_id,
        )

        # Broker — reads credentials from .env file or environment variables
        self._broker = IQOptionAdapter(
            email=os.environ.get("IQ_EMAIL", ""),
            password=os.environ.get("IQ_PASSWORD", ""),
            practice=os.environ.get("IQ_PRACTICE", "true").lower() == "true",
        )

        # Context
        self._session_filter = SessionFilter()
        self._news_filter = NewsFilter()
        self._regime_detector = MarketRegimeDetector()
        self._spike_detector = SpikeDetector()
        self._context_gate = ContextGate(
            session_filter=self._session_filter,
            news_filter=self._news_filter,
            regime_detector=self._regime_detector,
            spike_detector=self._spike_detector,
        )

        # Risk
        risk_config = self._config.get_risk_config(self._profile_name)
        self._risk_engine = RiskEngine(self._event_bus, risk_config)

        # Execution
        exec_config = self._config.get_execution_config(self._profile_name)
        self._execution_engine = ExecutionEngine(self._broker, self._event_bus, exec_config)

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

        # UI
        self._ui = None

    def start(self, headless: bool = False) -> None:
        """Start the Syntrix system."""
        setup_logging(self._config.get_log_level())

        logger.info("═" * 50)
        logger.info("  SYNTRIX — Quantitative Operating System")
        logger.info("  Session: %s", self._session_id)
        logger.info("  Profile: %s", self._profile_name)
        logger.info("  Mode: %s", self._mode)
        logger.info("═" * 50)

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
        """Run one scanning cycle."""
        if not self._running:
            return

        if self._risk_engine.is_locked or self._risk_engine.is_safe_mode:
            return

        assets = self._config.get_assets()

        for asset in assets:
            if not self._running:
                break

            self._state_machine.transition(OperationalState.WAITING_CONTEXT, reason=f"Evaluating {asset}")

            # Get candles (dry-run uses empty list)
            candles = []
            payout = 0.0
            if self._mode != "dry-run" and self._broker.is_connected:
                candles = self._broker.get_candles(asset, 60, 50)
                payout = self._broker.get_payout(asset)
            else:
                # Dry-run: skip execution
                continue

            if not candles:
                continue

            # Context evaluation
            ctx_result = self._context_gate.evaluate(
                asset=asset,
                payout=payout,
                candles=candles,
                broker_latency_ms=self._broker.avg_latency_ms,
                current_drawdown_pct=self._risk_engine.get_snapshot()["drawdown_pct"],
                broker_healthy=self._broker.is_connected,
            )

            self._event_bus.emit(
                EventType.CONTEXT_EVALUATED,
                data={"asset": asset, "decision": ctx_result["decision"]},
            )

            if ctx_result["blocked"]:
                self._event_bus.emit(
                    EventType.CONTEXT_BLOCKED,
                    data={"asset": asset, "reasons": ctx_result["block_reasons"]},
                )
                continue

            # Detect regime
            regime = self._regime_detector.detect(candles)

            # Evaluate strategies
            self._state_machine.transition(OperationalState.READY, reason="Evaluating strategies")

            for strategy in self._strategies:
                if not strategy.is_regime_compatible(regime):
                    continue

                signal = strategy.evaluate(candles, regime)
                if signal is None:
                    continue

                # Score signal
                score_result = self._scoring.score(
                    signal=signal,
                    regime=regime,
                    payout=payout,
                )

                self._event_bus.emit(
                    EventType.SIGNAL_SCORED,
                    data={
                        "strategy": signal.strategy_name,
                        "raw_score": score_result["raw_score"],
                        "final_score": score_result["final_score"],
                        "passed": score_result["passed"],
                    },
                )

                if not score_result["passed"]:
                    self._event_bus.emit(
                        EventType.SIGNAL_REJECTED,
                        data={"strategy": signal.strategy_name, "reason": score_result["reason"]},
                    )
                    continue

                # Risk check
                risk_result = self._risk_engine.check_trade_allowed(payout=payout)

                snapshot = TradeSnapshot(
                    asset=asset,
                    direction=signal.direction.value,
                    amount=self._config.get_trade_amount(self._profile_name),
                    duration=self._config.get_trade_duration(self._profile_name),
                    payout=payout,
                    strategy=signal.strategy_name,
                    score=score_result["final_score"],
                    confidence=signal.confidence,
                    regime=regime.value,
                    profile=self._profile_name,
                    mode=self._mode,
                    context_decision=ctx_result["decision"],
                    risk_check="passed" if risk_result["allowed"] else "failed",
                    risk_reasons=risk_result.get("reasons", []),
                    state_at_entry=self._state_machine.state.value,
                    pnl_at_entry=self._risk_engine.state.pnl,
                    drawdown_at_entry=self._risk_engine.get_snapshot()["drawdown_pct"],
                    timestamp_signal=time.time(),
                )

                if not risk_result["allowed"]:
                    snapshot.block_reason = "; ".join(risk_result["reasons"])
                    self._trade_logger.log_trade(snapshot)
                    continue

                # Execute trade
                self._state_machine.transition(OperationalState.EXECUTING, reason="Executing trade")
                correlation_id = str(uuid.uuid4())[:8]

                exec_record = self._execution_engine.execute(
                    asset=asset,
                    direction=signal.direction,
                    amount=snapshot.amount,
                    duration=snapshot.duration,
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
                    self._context_gate.record_trade()

                    # Wait for result
                    trade_result = self._execution_engine.wait_for_result(
                        broker_id=exec_record.broker_id,
                        trade_id=exec_record.trade_id,
                        correlation_id=correlation_id,
                    )

                    snapshot.result = trade_result.result
                    snapshot.profit = trade_result.profit
                    snapshot.timestamp_result = time.time()

                    # Update risk engine
                    self._risk_engine.record_trade_result(trade_result.profit)

                    # Update scoring history
                    self._scoring.update_history(
                        signal.strategy_name, trade_result.result == "win"
                    )

                    # Update analytics
                    self._analytics.add_trade(TradeRecord(
                        trade_id=exec_record.trade_id,
                        asset=asset,
                        direction=signal.direction.value,
                        strategy=signal.strategy_name,
                        regime=regime.value,
                        profile=self._profile_name,
                        payout=payout,
                        amount=snapshot.amount,
                        result=trade_result.result,
                        profit=trade_result.profit,
                        timestamp=time.time(),
                        score=score_result["final_score"],
                    ))

                self._trade_logger.log_trade(snapshot)

                # Cooldown after trade
                self._state_machine.transition(OperationalState.COOLDOWN, reason="Post-trade cooldown")
                break  # One trade per cycle max

        # Return to scanning
        if self._running:
            self._state_machine.transition(OperationalState.SCANNING, reason="Cycle complete")

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
                    "last_trade": self._trade_logger.get_last_n(1)[0].asset if self._trade_logger.session_count > 0 else "—",
                })

    def run(self, interval: float = 60.0) -> None:
        """Run the main loop."""
        logger.info("Main loop started (interval=%.0fs)", interval)
        try:
            while self._running:
                self.run_cycle()
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

        # Flush trade logs
        self._trade_logger.close()

        # Stop watchdog
        self._watchdog.stop()

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

        logger.info("═" * 50)
        logger.info("  SESSION SUMMARY")
        logger.info("  Trades: %d (executed: %d, blocked: %d)", summary["total_signals"], summary["executed"], summary["blocked"])
        logger.info("  Wins: %d | Losses: %d | Winrate: %.1f%%", summary["wins"], summary["losses"], summary["winrate"])
        logger.info("  PnL: $%.2f", summary["total_profit"])
        logger.info("  Sharpe: %.3f", analytics.get("sharpe_ratio", 0))
        logger.info("═" * 50)

    def get_analytics(self) -> dict:
        """Get full analytics report."""
        return self._analytics.full_report()

    def get_health(self) -> dict:
        """Get system health report."""
        return self._watchdog.get_health_report()


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Syntrix — Quantitative Operating System")
    parser.add_argument("--config", type=str, help="Path to profiles.yaml")
    parser.add_argument("--profile", type=str, help="Profile name (leve, moderado, etc)")
    parser.add_argument("--headless", action="store_true", help="Run without UI")
    parser.add_argument("--interval", type=float, default=60.0, help="Scan interval in seconds")
    args = parser.parse_args()

    load_env()
    syntrix = Syntrix(config_path=args.config, profile=args.profile)

    def signal_handler(sig, frame):
        syntrix.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    syntrix.start(headless=args.headless)
    syntrix.run(interval=args.interval)


if __name__ == "__main__":
    main()
