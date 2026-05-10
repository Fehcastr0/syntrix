"""
execution/shadow_mode.py — Shadow Mode for Syntrix.

Simulates ALL entries without sending real orders.
Records everything to Feature Store for statistical analysis.

Purpose:
- Generate thousands of samples rapidly
- Validate strategies with real market data
- Discover edge without risking capital
- Calibrate probability scores against real outcomes
"""

from __future__ import annotations

import csv
import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("syntrix.shadow")


@dataclass
class ShadowTrade:
    """A simulated trade record with full context."""

    timestamp: float = 0.0
    asset: str = ""
    direction: str = ""
    strategy: str = ""
    regime: str = ""
    market_phase: str = ""
    hour_utc: int = 0
    payout: float = 0.0
    base_score: float = 0.0
    meta_score: float = 0.0
    confidence: float = 0.0
    rank_score: float = 0.0
    asset_quality: float = 0.0
    otc_quality: float = 0.0
    microstructure_quality: float = 0.0
    volatility: float = 0.0
    latency_ms: float = 0.0
    strategy_weight: float = 1.0
    strategy_consensus: int = 1
    threshold_used: float = 0.0
    block_reasons: str = ""
    decision: str = ""  # "ALLOW", "BLOCK", "SHADOW_EXECUTE"
    # Candle outcome (filled after expiry)
    entry_price: float = 0.0
    exit_price: float = 0.0
    candle_outcome: str = ""  # "win", "loss", "tie"
    simulated_profit: float = 0.0
    correlation_id: str = ""


class ShadowMode:
    """
    Shadow execution engine.

    Runs full pipeline but never sends real orders.
    Records all decisions (including blocked ones) for analysis.
    """

    def __init__(self, data_dir: str = "data/shadow") -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._trades: List[ShadowTrade] = []
        self._blocked: List[ShadowTrade] = []
        self._csv_path = self._data_dir / f"shadow_{time.strftime('%Y%m%d')}.csv"
        self._header_written = self._csv_path.exists()
        self._stats = {
            "total_signals": 0,
            "total_allowed": 0,
            "total_blocked": 0,
            "total_wins": 0,
            "total_losses": 0,
            "block_reasons": {},
        }

    def record_signal(self, trade: ShadowTrade) -> None:
        """Record any signal (allowed or blocked)."""
        self._stats["total_signals"] += 1

        if trade.decision == "BLOCK":
            self._blocked.append(trade)
            self._stats["total_blocked"] += 1
            for reason in trade.block_reasons.split(", "):
                reason = reason.strip()
                if reason:
                    self._stats["block_reasons"][reason] = \
                        self._stats["block_reasons"].get(reason, 0) + 1
        else:
            self._trades.append(trade)
            self._stats["total_allowed"] += 1

        self._write_csv(trade)

    def record_outcome(
        self,
        correlation_id: str,
        entry_price: float,
        exit_price: float,
        direction: str,
        payout: float,
        amount: float = 1.0,
    ) -> None:
        """Record the candle outcome for a shadow trade."""
        for trade in reversed(self._trades):
            if trade.correlation_id == correlation_id:
                trade.entry_price = entry_price
                trade.exit_price = exit_price

                if direction == "call":
                    won = exit_price > entry_price
                elif direction == "put":
                    won = exit_price < entry_price
                else:
                    won = False

                trade.candle_outcome = "win" if won else "loss"
                trade.simulated_profit = amount * payout if won else -amount

                if won:
                    self._stats["total_wins"] += 1
                else:
                    self._stats["total_losses"] += 1
                break

    def get_stats(self) -> Dict[str, Any]:
        """Get shadow mode statistics."""
        total = self._stats["total_signals"]
        allowed = self._stats["total_allowed"]
        wins = self._stats["total_wins"]
        losses = self._stats["total_losses"]
        completed = wins + losses

        return {
            "total_signals": total,
            "total_allowed": allowed,
            "total_blocked": self._stats["total_blocked"],
            "allow_rate": allowed / total if total > 0 else 0,
            "block_rate": self._stats["total_blocked"] / total if total > 0 else 0,
            "total_wins": wins,
            "total_losses": losses,
            "winrate": wins / completed if completed > 0 else 0,
            "completed_trades": completed,
            "block_reasons": dict(sorted(
                self._stats["block_reasons"].items(),
                key=lambda x: x[1], reverse=True,
            )),
            "simulated_pnl": sum(t.simulated_profit for t in self._trades),
        }

    def get_report(self) -> str:
        """Generate 'POR QUE NAO OPEROU' report."""
        stats = self.get_stats()
        lines = [
            "=" * 60,
            "  SHADOW MODE REPORT — POR QUE NAO OPEROU",
            "=" * 60,
            f"  Total signals analyzed: {stats['total_signals']}",
            f"  Allowed: {stats['total_allowed']} ({stats['allow_rate']:.0%})",
            f"  Blocked: {stats['total_blocked']} ({stats['block_rate']:.0%})",
            "",
        ]

        if stats["completed_trades"] > 0:
            lines.extend([
                f"  Simulated trades: {stats['completed_trades']}",
                f"  Wins: {stats['total_wins']} | Losses: {stats['total_losses']}",
                f"  Winrate: {stats['winrate']:.1%}",
                f"  Simulated PnL: ${stats['simulated_pnl']:.2f}",
                "",
            ])

        if stats["block_reasons"]:
            lines.append("  BLOCK REASONS (most frequent):")
            for reason, count in list(stats["block_reasons"].items())[:10]:
                pct = count / stats["total_blocked"] * 100 if stats["total_blocked"] > 0 else 0
                lines.append(f"    {reason}: {count} ({pct:.0f}%)")
            lines.append("")

        # Confidence distribution
        if self._trades:
            confidences = [t.confidence for t in self._trades]
            lines.append("  CONFIDENCE DISTRIBUTION (allowed trades):")
            buckets = [0, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.01]
            for i in range(len(buckets) - 1):
                low, high = buckets[i], buckets[i + 1]
                count = sum(1 for c in confidences if low <= c < high)
                if count > 0:
                    lines.append(f"    [{low:.1f}-{high:.1f}): {count}")
            lines.append("")

        if self._blocked:
            blocked_conf = [t.confidence for t in self._blocked]
            lines.append("  CONFIDENCE DISTRIBUTION (blocked signals):")
            for i in range(len(buckets) - 1):
                low, high = buckets[i], buckets[i + 1]
                count = sum(1 for c in blocked_conf if low <= c < high)
                if count > 0:
                    lines.append(f"    [{low:.1f}-{high:.1f}): {count}")
            lines.append("")

        lines.append("=" * 60)
        return "\n".join(lines)

    def _write_csv(self, trade: ShadowTrade) -> None:
        """Append trade to CSV file."""
        data = asdict(trade)
        try:
            with open(self._csv_path, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=data.keys())
                if not self._header_written:
                    writer.writeheader()
                    self._header_written = True
                writer.writerow(data)
        except Exception as exc:
            logger.error("Shadow CSV write error: %s", exc)

    def close(self) -> None:
        """Flush and close."""
        report = self.get_report()
        report_path = self._data_dir / f"report_{time.strftime('%Y%m%d_%H%M')}.txt"
        try:
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(report)
            logger.info("Shadow report saved: %s", report_path)
        except Exception as exc:
            logger.error("Shadow report save error: %s", exc)
