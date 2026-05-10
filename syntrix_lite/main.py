"""
Syntrix Lite — Validar edge real com simplicidade.

"Menos complexidade. Mais execução. Mais validação estatística."

Pipeline:
  SCAN ASSETS -> STRATEGIES -> SCORE -> BEST SIGNAL -> RISK CHECK -> EXECUTE

Supports: --shadow, --headless, --interval, --amount, --duration
"""

from __future__ import annotations

import argparse
import datetime
import logging
import os
import signal as signal_mod
import sys
import time
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

MIN_SCORE = 0.35  # Simple threshold — if score > 0.35, trade is valid


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


class SyntrixLite:
    """
    Syntrix Lite — minimal quantitative trading system.
    Scans assets, evaluates 2 strategies, executes best signal.
    """

    def __init__(
        self,
        shadow: bool = False,
        amount: float = 2.0,
        duration: int = 60,
        min_payout: float = 0.60,
    ) -> None:
        self._shadow = shadow
        self._amount = amount
        self._duration = duration
        self._min_payout = min_payout
        self._running = False

        # Broker
        self._broker = BrokerAdapter(
            email=os.environ.get("IQ_EMAIL", ""),
            password=os.environ.get("IQ_PASSWORD", ""),
            practice=os.environ.get("IQ_PRACTICE", "true").lower() == "true",
        )

        # Risk
        self._risk = RiskManager(RiskConfig(
            stop_gain=float(os.environ.get("STOP_GAIN", "30")),
            stop_loss=float(os.environ.get("STOP_LOSS", "-15")),
            max_consecutive_losses=3,
            cooldown_after_loss_sec=60,
            cooldown_after_trade_sec=15,
        ))

        # Analytics
        self._analytics = Analytics()

        # Logger
        self._logger = TradeLogger()

        # Shadow
        self._shadow_mode = ShadowMode() if shadow else None

        # Assets
        self._assets = list(DEFAULT_ASSETS)

    def start(self) -> bool:
        """Connect to broker and start."""
        logger.info("=" * 50)
        logger.info("  SYNTRIX LITE")
        logger.info("  %s", "SHADOW MODE" if self._shadow else "LIVE MODE")
        logger.info("  Amount: $%.2f | Duration: %ds", self._amount, self._duration)
        logger.info("  Assets: %d", len(self._assets))
        logger.info("=" * 50)

        if not self._broker.connect():
            logger.error("Failed to connect to broker")
            return False

        balance = self._broker.get_balance()
        logger.info("Balance: $%.2f", balance)

        # Discover open assets
        open_assets = self._broker.get_open_assets()
        if open_assets:
            self._assets = [a for a in self._assets if a in open_assets]
            # Add any open OTC assets not in our list
            for a in open_assets:
                if a.endswith("-OTC") and a not in self._assets:
                    self._assets.append(a)
            logger.info("Open assets: %d", len(self._assets))

        self._running = True
        return True

    def run_cycle(self) -> None:
        """One scan cycle."""
        if not self._running:
            return

        # Risk check
        can_trade, reason = self._risk.can_trade()
        if not can_trade:
            logger.debug("Risk: %s", reason)
            return

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        hour_utc = now_utc.hour

        # Scan all assets and collect signals
        all_signals: List[Signal] = []

        for asset in self._assets:
            if not self._running:
                break

            candles = self._broker.get_candles(asset, 60, 50)
            payout = self._broker.get_payout(asset)

            if not candles:
                continue

            scan = scan_asset(asset, candles, payout, min_payout=self._min_payout)
            if not scan:
                continue

            if not scan.payout_ok:
                if self._shadow_mode:
                    self._shadow_mode.record(ShadowRecord(
                        timestamp=time.time(), asset=asset,
                        payout=payout, hour_utc=hour_utc,
                        decision="BLOCK", block_reason="LOW_PAYOUT",
                    ))
                continue

            if not scan.volatility_ok:
                if self._shadow_mode:
                    self._shadow_mode.record(ShadowRecord(
                        timestamp=time.time(), asset=asset,
                        payout=payout, hour_utc=hour_utc,
                        decision="BLOCK", block_reason="LOW_VOLATILITY",
                    ))
                continue

            # Evaluate strategies
            sig_tp = trend_pullback.evaluate(scan)
            sig_mom = momentum.evaluate(scan)

            for sig in [sig_tp, sig_mom]:
                if sig and sig.score >= MIN_SCORE:
                    all_signals.append(sig)

            # Record signals below threshold in shadow
            for sig in [sig_tp, sig_mom]:
                if sig and sig.score < MIN_SCORE and self._shadow_mode:
                    self._shadow_mode.record(ShadowRecord(
                        timestamp=time.time(), asset=sig.asset,
                        direction=sig.direction, strategy=sig.strategy,
                        score=sig.score, payout=sig.payout,
                        rsi=sig.rsi, atr=sig.atr, hour_utc=hour_utc,
                        decision="BLOCK", block_reason="LOW_SCORE",
                    ))

        if not all_signals:
            return

        # Select best signal
        best = max(all_signals, key=lambda s: s.score)

        logger.info(
            "SIGNAL: %s %s %s score=%.3f payout=%.0f%% rsi=%.1f",
            best.direction.upper(), best.asset, best.strategy,
            best.score, best.payout * 100, best.rsi,
        )

        # Shadow mode: record and skip execution
        if self._shadow:
            self._shadow_mode.record(ShadowRecord(
                timestamp=time.time(), asset=best.asset,
                direction=best.direction, strategy=best.strategy,
                score=best.score, payout=best.payout,
                rsi=best.rsi, atr=best.atr, hour_utc=hour_utc,
                decision="EXECUTE",
            ))
            return

        # Real execution
        self._risk.enter_position()
        result = self._broker.buy(
            asset=best.asset,
            amount=self._amount,
            direction=best.direction,
            duration=self._duration,
        )

        if not result.success:
            logger.error("Execution failed: %s", result.error)
            self._risk._in_position = False
            return

        logger.info("Trade executed: %s (id=%s, latency=%.0fms)",
                     best.asset, result.trade_id, result.latency_ms)

        # Wait for result
        outcome = self._broker.check_result(result.broker_id)

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
            "RESULT: %s %s → %s ($%.2f) | PnL: $%.2f | WR: %.0f%%",
            best.direction.upper(), best.asset, outcome.result,
            outcome.profit, self._risk.pnl,
            self._analytics.winrate() * 100,
        )

    def run(self, interval: float = 30.0) -> None:
        """Main loop."""
        logger.info("Main loop started (interval=%.0fs)", interval)
        try:
            while self._running:
                self.run_cycle()

                # Print stats every cycle
                stats = self._risk.get_stats()
                if stats["total_trades"] > 0 or self._shadow:
                    logger.info(
                        "Stats: trades=%d WR=%.0f%% PnL=$%.2f losses_streak=%d",
                        stats["total_trades"], stats["winrate"],
                        stats["pnl"], stats["consecutive_losses"],
                    )

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

        # Print reports
        if self._analytics.total > 0:
            print(self._analytics.format_report())

        if self._shadow_mode:
            print(self._shadow_mode.get_report())

        # Risk summary
        stats = self._risk.get_stats()
        print(f"\n  Risk: PnL=${stats['pnl']} | Trades={stats['total_trades']} | "
              f"WR={stats['winrate']}% | Locked={stats['locked']}")

        self._broker.disconnect()
        logger.info("Syntrix Lite stopped")


def main() -> None:
    parser = argparse.ArgumentParser(description="Syntrix Lite — Simple Quantitative Trading")
    parser.add_argument("--shadow", action="store_true",
                        help="Shadow mode — simulate all trades, no real orders")
    parser.add_argument("--interval", type=float, default=30.0,
                        help="Scan interval in seconds (default 30)")
    parser.add_argument("--amount", type=float, default=2.0,
                        help="Trade amount in $ (default 2.0)")
    parser.add_argument("--duration", type=int, default=60,
                        help="Trade duration in seconds (default 60)")
    parser.add_argument("--min-payout", type=float, default=0.60,
                        help="Minimum payout to accept (default 0.60)")
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

    lite = SyntrixLite(
        shadow=args.shadow,
        amount=amount,
        duration=duration,
        min_payout=args.min_payout,
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
