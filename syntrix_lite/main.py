"""
Syntrix Lite — Validar edge real com simplicidade.

"Menos complexidade. Mais execução. Mais validação estatística."

Pipeline:
  SCAN ASSETS -> INDICATORS -> STRATEGY -> SCORE -> RISK -> EXECUTE

Supports: --shadow, --interval, --amount, --duration, --force, --min-payout
"""

from __future__ import annotations

import argparse
import datetime
import logging
import os
import signal as signal_mod
import sys
import time
import traceback
from pathlib import Path
from typing import List, Optional

from analytics.tracker import Analytics, TradeLog
from execution.broker import BrokerAdapter
from execution.shadow import ShadowMode, ShadowRecord
from logs.logger import TradeLogger
from risk.risk_manager import RiskConfig, RiskManager
from scanner.indicators import Candle
from scanner.scanner import DEFAULT_ASSETS, ScanResult, scan_asset
from strategies.trend_pullback import Signal
from strategies import trend_pullback, momentum

logger = logging.getLogger("syntrix-lite")


# Thresholds — reduced to actually trade
MIN_SCORE = 0.15         # Was 0.35 — now much lower to allow entries
MIN_PAYOUT = 0.50        # Was 0.60 — accept lower payouts
MIN_ATR_RATIO = 0.00001  # Was 0.0001 — accept almost any volatility
COOLDOWN_TRADE = 5.0     # Was 15s — faster cycling
COOLDOWN_LOSS = 30.0     # Was 60s — faster recovery


def load_env(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


class OperationalMetrics:
    """Counters for every pipeline stage."""

    def __init__(self) -> None:
        self.cycles = 0
        self.scans_total = 0
        self.no_candles = 0
        self.scan_failed = 0
        self.low_payout = 0
        self.low_volatility = 0
        self.no_trend = 0
        self.strategy_matches = 0
        self.scores_above_threshold = 0
        self.scores_below_threshold = 0
        self.blocked_by_risk = 0
        self.execution_attempts = 0
        self.execution_success = 0
        self.execution_failures = 0
        self.shadow_executes = 0
        self._last_report = time.time()

    def should_report(self, interval: float = 300) -> bool:
        now = time.time()
        if now - self._last_report >= interval:
            self._last_report = now
            return True
        return False

    def format_report(self) -> str:
        signals = self.strategy_matches
        return (
            "\n" + "=" * 50 +
            "\n  SYNTRIX LITE — OPERATIONAL REPORT"
            "\n" + "=" * 50 +
            f"\n  cycles:                  {self.cycles}"
            f"\n  scans_total:             {self.scans_total}"
            f"\n  no_candles:              {self.no_candles}"
            f"\n  scan_failed:             {self.scan_failed}"
            f"\n  low_payout:              {self.low_payout}"
            f"\n  low_volatility:          {self.low_volatility}"
            f"\n  no_trend/rsi_out:        {self.no_trend}"
            f"\n  strategy_matches:        {self.strategy_matches}"
            f"\n  scores_above_threshold:  {self.scores_above_threshold}"
            f"\n  scores_below_threshold:  {self.scores_below_threshold}"
            f"\n  blocked_by_risk:         {self.blocked_by_risk}"
            f"\n  execution_attempts:      {self.execution_attempts}"
            f"\n  execution_success:       {self.execution_success}"
            f"\n  execution_failures:      {self.execution_failures}"
            f"\n  shadow_executes:         {self.shadow_executes}"
            "\n" + "=" * 50
        )


class SyntrixLite:
    """
    Syntrix Lite — minimal quantitative trading system.
    Scans assets, evaluates 2 strategies, executes best signal.
    """

    def __init__(
        self,
        shadow: bool = False,
        force: bool = False,
        amount: float = 2.0,
        duration: int = 1,
        min_payout: float = MIN_PAYOUT,
        target: float | None = None,
    ) -> None:
        self._shadow = shadow
        self._force = force
        self._amount = amount
        self._duration = duration
        self._min_payout = min_payout
        self._target = target
        self._running = False

        # Score threshold
        self._min_score = 0.10 if force else MIN_SCORE

        # Broker
        self._broker = BrokerAdapter(
            email=os.environ.get("IQ_EMAIL", ""),
            password=os.environ.get("IQ_PASSWORD", ""),
            practice=os.environ.get("IQ_PRACTICE", "true").lower() == "true",
        )

        # If target set and higher than stop_gain, raise stop_gain
        stop_gain = float(os.environ.get("STOP_GAIN", "30"))
        if target is not None and target > stop_gain:
            stop_gain = target + 10  # allow room above target

        # Risk — relaxed cooldowns
        self._risk = RiskManager(
            config=RiskConfig(
                stop_gain=stop_gain,
                stop_loss=float(os.environ.get("STOP_LOSS", "-15")),
                max_consecutive_losses=5,
                cooldown_after_loss_sec=COOLDOWN_LOSS,
                cooldown_after_trade_sec=COOLDOWN_TRADE,
                max_trades_per_session=500,
            ),
            profit_target=target,
        )

        # Analytics
        self._analytics = Analytics()

        # Logger
        self._logger = TradeLogger()

        # Shadow
        self._shadow_mode = ShadowMode() if shadow else None

        # Metrics
        self._metrics = OperationalMetrics()

        # Assets
        self._assets = list(DEFAULT_ASSETS)

    def start(self) -> bool:
        """Connect to broker and start."""
        mode = "SHADOW" if self._shadow else "LIVE"
        if self._force:
            mode += " + FORCE_ENTRY"
        if self._target is not None:
            mode += f" + TARGET ${self._target:.2f}"

        logger.info("=" * 50)
        logger.info("  SYNTRIX LITE")
        logger.info("  Mode: %s", mode)
        logger.info("  Amount: $%.2f | Duration: %dmin (M%d)", self._amount, self._duration, self._duration)
        logger.info("  Min Score: %.2f | Min Payout: %.0f%%",
                     self._min_score, self._min_payout * 100)
        if self._target is not None:
            logger.info("  META DE LUCRO: $%.2f", self._target)
        logger.info("  Assets: %d", len(self._assets))
        logger.info("=" * 50)

        try:
            if not self._broker.connect():
                logger.error("Failed to connect to broker")
                return False
        except Exception:
            logger.error("Broker connect exception:\n%s", traceback.format_exc())
            return False

        try:
            balance = self._broker.get_balance()
            logger.info("Balance: $%.2f", balance)
        except Exception:
            logger.warning("Could not get balance: %s", traceback.format_exc())

        # Discover open assets — keep only our DEFAULT list that are open
        # Don't add ALL 159 OTC assets (would take 3+ min per cycle)
        try:
            open_assets = self._broker.get_open_assets()
            if open_assets:
                self._assets = [a for a in self._assets if a in open_assets]
                logger.info("Open assets: %d — %s", len(self._assets),
                           ", ".join(self._assets))
            else:
                logger.warning("Could not discover open assets, using defaults")
        except Exception:
            logger.warning("Asset discovery failed: %s", traceback.format_exc())

        self._running = True
        return True

    def run_cycle(self) -> None:
        """One scan cycle with FULL debug logging."""
        if not self._running:
            return

        self._metrics.cycles += 1
        cycle_num = self._metrics.cycles

        logger.info("--- CYCLE %d ---", cycle_num)

        # Risk check
        can_trade, reason = self._risk.can_trade()
        if not can_trade:
            logger.info("[RISK] BLOCKED: %s", reason)
            self._metrics.blocked_by_risk += 1
            return

        logger.info("[RISK] OK — can trade")

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        hour_utc = now_utc.hour

        # Scan all assets and collect signals
        all_signals: List[Signal] = []
        cycle_scanned = 0
        cycle_signals = 0

        for asset in self._assets:
            if not self._running:
                break

            self._metrics.scans_total += 1
            cycle_scanned += 1

            try:
                candles = self._broker.get_candles(asset, 60, 50)
            except Exception:
                logger.warning("[SCAN] %s — candle error: %s", asset, traceback.format_exc())
                candles = []

            if not candles:
                self._metrics.no_candles += 1
                continue

            try:
                payout = self._broker.get_payout(asset)
            except Exception:
                payout = 0.0

            # Scan with reduced ATR minimum
            scan = scan_asset(
                asset, candles, payout,
                min_payout=self._min_payout,
                min_atr_ratio=MIN_ATR_RATIO,
            )
            if not scan:
                self._metrics.scan_failed += 1
                logger.debug("[SCAN] %s — scan failed (not enough candles?)", asset)
                continue

            # Log indicators for every asset
            logger.info(
                "[SCAN] %s | EMA_F=%.5f EMA_S=%.5f | RSI=%.1f | ATR=%.6f | "
                "payout=%.0f%% | trend=%s | vol_ok=%s | pay_ok=%s",
                asset,
                scan.ema_fast[-1] if scan.ema_fast else 0,
                scan.ema_slow[-1] if scan.ema_slow else 0,
                scan.current_rsi, scan.current_atr,
                payout * 100,
                "UP" if scan.trend_up else ("DOWN" if scan.trend_down else "FLAT"),
                scan.volatility_ok, scan.payout_ok,
            )

            # Payout check
            if not scan.payout_ok:
                self._metrics.low_payout += 1
                if self._shadow_mode:
                    self._shadow_mode.record(ShadowRecord(
                        timestamp=time.time(), asset=asset,
                        payout=payout, hour_utc=hour_utc,
                        decision="BLOCK", block_reason="LOW_PAYOUT",
                    ))
                continue

            # Volatility check — in force mode, skip this
            if not scan.volatility_ok and not self._force:
                self._metrics.low_volatility += 1
                if self._shadow_mode:
                    self._shadow_mode.record(ShadowRecord(
                        timestamp=time.time(), asset=asset,
                        payout=payout, hour_utc=hour_utc,
                        decision="BLOCK", block_reason="LOW_VOLATILITY",
                    ))
                continue

            # Evaluate strategies — with WIDER RSI ranges
            sig_tp = trend_pullback.evaluate(scan)
            sig_mom = momentum.evaluate(scan)

            # Log strategy results
            tp_str = f"score={sig_tp.score:.3f} dir={sig_tp.direction}" if sig_tp else "NO_SIGNAL"
            mom_str = f"score={sig_mom.score:.3f} dir={sig_mom.direction}" if sig_mom else "NO_SIGNAL"
            logger.info("[STRATEGY] %s | TP=%s | MOM=%s", asset, tp_str, mom_str)

            for sig in [sig_tp, sig_mom]:
                if sig:
                    self._metrics.strategy_matches += 1
                    cycle_signals += 1
                    if sig.score >= self._min_score:
                        all_signals.append(sig)
                        self._metrics.scores_above_threshold += 1
                        logger.info(
                            "[SCORE] %s %s %s score=%.3f >= %.2f PASS",
                            sig.direction.upper(), asset, sig.strategy,
                            sig.score, self._min_score,
                        )
                    else:
                        self._metrics.scores_below_threshold += 1
                        logger.info(
                            "[SCORE] %s %s %s score=%.3f < %.2f REJECT",
                            sig.direction.upper(), asset, sig.strategy,
                            sig.score, self._min_score,
                        )
                        if self._shadow_mode:
                            self._shadow_mode.record(ShadowRecord(
                                timestamp=time.time(), asset=sig.asset,
                                direction=sig.direction, strategy=sig.strategy,
                                score=sig.score, payout=sig.payout,
                                rsi=sig.rsi, atr=sig.atr, hour_utc=hour_utc,
                                decision="BLOCK", block_reason="LOW_SCORE",
                            ))
                else:
                    self._metrics.no_trend += 1

        logger.info("[CYCLE %d] Scanned=%d Signals=%d Passed=%d",
                     cycle_num, cycle_scanned, cycle_signals, len(all_signals))

        if not all_signals:
            logger.info("[DECISION] NO VALID SIGNALS this cycle")
            return

        # Select best signal
        best = max(all_signals, key=lambda s: s.score)

        logger.info(
            "[DECISION] BEST: %s %s %s score=%.3f payout=%.0f%% rsi=%.1f atr=%.6f",
            best.direction.upper(), best.asset, best.strategy,
            best.score, best.payout * 100, best.rsi, best.atr,
        )

        # Shadow mode: record and skip execution
        if self._shadow:
            self._metrics.shadow_executes += 1
            self._shadow_mode.record(ShadowRecord(
                timestamp=time.time(), asset=best.asset,
                direction=best.direction, strategy=best.strategy,
                score=best.score, payout=best.payout,
                rsi=best.rsi, atr=best.atr, hour_utc=hour_utc,
                decision="EXECUTE",
            ))
            logger.info("[SHADOW] Recorded: %s %s %s score=%.3f",
                        best.direction.upper(), best.asset, best.strategy, best.score)
            return

        # Real execution
        self._metrics.execution_attempts += 1
        logger.info("[EXECUTION] Sending order: %s %s $%.2f %ds",
                     best.direction.upper(), best.asset, self._amount, self._duration)

        self._risk.enter_position()

        try:
            result = self._broker.buy(
                asset=best.asset,
                amount=self._amount,
                direction=best.direction,
                duration=self._duration,
            )
        except Exception:
            logger.error("[EXECUTION] Exception:\n%s", traceback.format_exc())
            self._risk._in_position = False
            self._metrics.execution_failures += 1
            return

        if not result.success:
            logger.error("[EXECUTION] FAILED: %s", result.error)
            self._risk._in_position = False
            self._metrics.execution_failures += 1
            return

        self._metrics.execution_success += 1
        logger.info("[EXECUTION] SUCCESS: %s id=%s latency=%.0fms",
                     best.asset, result.trade_id, result.latency_ms)

        # Wait for result
        try:
            outcome = self._broker.check_result(result.broker_id)
        except Exception:
            logger.error("[RESULT] check_result exception:\n%s", traceback.format_exc())
            self._risk._in_position = False
            return

        # Record
        self._risk.record_result(outcome.profit)

        trade_log = TradeLog(
            asset=best.asset, direction=best.direction,
            strategy=best.strategy, score=best.score,
            payout=best.payout, result=outcome.result,
            profit=outcome.profit, hour_utc=hour_utc,
            timestamp=time.time(),
        )
        self._analytics.record(trade_log)

        self._logger.log({
            "timestamp": time.time(), "asset": best.asset,
            "direction": best.direction, "strategy": best.strategy,
            "score": best.score, "payout": best.payout,
            "amount": self._amount, "result": outcome.result,
            "profit": outcome.profit, "rsi": best.rsi,
            "atr": best.atr, "hour_utc": hour_utc,
        })

        logger.info(
            "[RESULT] %s %s -> %s ($%.2f) | PnL: $%.2f | WR: %.0f%% | Trades: %d",
            best.direction.upper(), best.asset, outcome.result,
            outcome.profit, self._risk.pnl,
            self._analytics.winrate() * 100,
            self._analytics.total,
        )

    def run(self, interval: float = 30.0) -> None:
        """Main loop."""
        logger.info("Main loop started (interval=%.0fs)", interval)
        try:
            while self._running:
                try:
                    self.run_cycle()
                except Exception:
                    logger.error("Cycle error:\n%s", traceback.format_exc())

                # Print metrics every 5 minutes
                if self._metrics.should_report(300):
                    print(self._metrics.format_report())

                # Print stats every cycle
                stats = self._risk.get_stats()
                if stats["total_trades"] > 0:
                    target_info = ""
                    if self._target is not None:
                        progress = stats.get("target_progress", 0)
                        target_info = f" | Meta: {progress:.0f}% (${stats['pnl']:.2f}/${self._target:.2f})"
                    logger.info(
                        "[STATS] trades=%d WR=%.0f%% PnL=$%.2f streak=%d%s",
                        stats["total_trades"], stats["winrate"],
                        stats["pnl"], stats["consecutive_losses"],
                        target_info,
                    )

                # Check if profit target reached
                if self._risk.locked and self._risk.lock_reason == "PROFIT_TARGET":
                    logger.info("")
                    logger.info("=" * 50)
                    logger.info("  META DE LUCRO ATINGIDA!")
                    logger.info("  Target: $%.2f | PnL: $%.2f", self._target, self._risk.pnl)
                    logger.info("  Trades: %d | WR: %.0f%%",
                                stats["total_trades"], stats["winrate"])
                    logger.info("=" * 50)
                    break

                time.sleep(interval)
        except KeyboardInterrupt:
            logger.info("Interrupted")
        finally:
            self.stop()

    def stop(self) -> None:
        """Stop and print reports."""
        if not self._running:
            return
        self._running = False
        logger.info("Stopping Syntrix Lite...")

        # Print metrics
        print(self._metrics.format_report())

        # Print reports
        if self._analytics.total > 0:
            print(self._analytics.format_report())

        if self._shadow_mode:
            print(self._shadow_mode.get_report())

        # Risk summary
        stats = self._risk.get_stats()
        target_str = ""
        if self._target is not None:
            progress = stats.get("target_progress", 0)
            target_str = f" | Meta: ${self._target:.2f} ({progress:.0f}%)"
        print(f"\n  Risk: PnL=${stats['pnl']} | Trades={stats['total_trades']} | "
              f"WR={stats['winrate']}% | Locked={stats['locked']}{target_str}")

        self._broker.disconnect()
        logger.info("Syntrix Lite stopped")


def main() -> None:
    parser = argparse.ArgumentParser(description="Syntrix Lite — Simple Quantitative Trading")
    parser.add_argument("--shadow", action="store_true",
                        help="Shadow mode — simulate all trades, no real orders")
    parser.add_argument("--force", action="store_true",
                        help="Force entry mode — score > 0.10 = execute (debug)")
    parser.add_argument("--interval", type=float, default=30.0,
                        help="Scan interval in seconds (default 30)")
    parser.add_argument("--amount", type=float, default=2.0,
                        help="Trade amount in $ (default 2.0)")
    parser.add_argument("--duration", type=int, default=1,
                        help="Trade duration in MINUTES (default 1 = M1)")
    parser.add_argument("--min-payout", type=float, default=MIN_PAYOUT,
                        help=f"Minimum payout to accept (default {MIN_PAYOUT})")
    parser.add_argument("--target", type=float, default=None,
                        help="Profit target — bot stops when PnL >= target (e.g. --target 100)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    load_env()

    # Override amount/duration from env if set
    amount = float(os.environ.get("IQ_AMOUNT", str(args.amount)))
    duration = int(os.environ.get("IQ_DURATION", str(args.duration)))

    target = args.target
    if target is None:
        env_target = os.environ.get("PROFIT_TARGET")
        if env_target:
            target = float(env_target)

    lite = SyntrixLite(
        shadow=args.shadow,
        force=args.force,
        amount=amount,
        duration=duration,
        min_payout=args.min_payout,
        target=target,
    )

    def on_signal(sig, frame):
        lite.stop()
        sys.exit(0)

    signal_mod.signal(signal_mod.SIGINT, on_signal)
    signal_mod.signal(signal_mod.SIGTERM, on_signal)

    if lite.start():
        lite.run(interval=args.interval)
    else:
        logger.error("Failed to start — check broker credentials in .env")


if __name__ == "__main__":
    main()
