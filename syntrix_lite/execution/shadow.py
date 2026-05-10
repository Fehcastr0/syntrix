"""
Shadow Mode — executa lógica completa, NÃO envia ordem real.
Registra tudo para validação estatística.
"""

from __future__ import annotations

import csv
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List


@dataclass
class ShadowRecord:
    timestamp: float = 0.0
    asset: str = ""
    direction: str = ""
    strategy: str = ""
    score: float = 0.0
    payout: float = 0.0
    rsi: float = 0.0
    atr: float = 0.0
    hour_utc: int = 0
    decision: str = ""  # "EXECUTE", "BLOCK", "NO_SIGNAL"
    block_reason: str = ""
    # Filled after candle closes (if simulated)
    simulated_result: str = ""  # "win", "loss", ""
    simulated_profit: float = 0.0


class ShadowMode:
    """Records all decisions without executing real trades."""

    def __init__(self, data_dir: str = "data/shadow") -> None:
        self._data_dir = data_dir
        self._records: List[ShadowRecord] = []
        os.makedirs(data_dir, exist_ok=True)
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        self._csv_path = os.path.join(data_dir, f"shadow_{today}.csv")
        self._init_csv()

    def _init_csv(self) -> None:
        if not os.path.exists(self._csv_path):
            with open(self._csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "timestamp", "datetime", "asset", "direction", "strategy",
                    "score", "payout", "rsi", "atr", "hour_utc",
                    "decision", "block_reason",
                    "simulated_result", "simulated_profit",
                ])

    def record(self, rec: ShadowRecord) -> None:
        self._records.append(rec)
        dt = datetime.fromtimestamp(rec.timestamp, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        with open(self._csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                rec.timestamp, dt, rec.asset, rec.direction, rec.strategy,
                f"{rec.score:.4f}", f"{rec.payout:.2f}", f"{rec.rsi:.1f}",
                f"{rec.atr:.6f}", rec.hour_utc,
                rec.decision, rec.block_reason,
                rec.simulated_result, f"{rec.simulated_profit:.2f}",
            ])

    def get_stats(self) -> Dict:
        total = len(self._records)
        executed = sum(1 for r in self._records if r.decision == "EXECUTE")
        blocked = sum(1 for r in self._records if r.decision == "BLOCK")
        no_signal = sum(1 for r in self._records if r.decision == "NO_SIGNAL")

        # Block reasons
        block_reasons: Dict[str, int] = {}
        for r in self._records:
            if r.block_reason:
                for reason in r.block_reason.split(", "):
                    block_reasons[reason] = block_reasons.get(reason, 0) + 1

        # Allow rate
        signals = executed + blocked
        allow_rate = executed / signals if signals > 0 else 0

        return {
            "total_records": total,
            "executed": executed,
            "blocked": blocked,
            "no_signal": no_signal,
            "allow_rate": round(allow_rate * 100, 1),
            "block_reasons": dict(sorted(block_reasons.items(), key=lambda x: -x[1])),
        }

    def get_report(self) -> str:
        stats = self.get_stats()
        lines = [
            "",
            "=" * 50,
            "  SHADOW MODE REPORT",
            "=" * 50,
            f"  Total records: {stats['total_records']}",
            f"  Executed (shadow): {stats['executed']}",
            f"  Blocked: {stats['blocked']}",
            f"  No signal: {stats['no_signal']}",
            f"  Allow rate: {stats['allow_rate']}%",
            "",
            "  Block reasons:",
        ]
        for reason, count in stats["block_reasons"].items():
            lines.append(f"    {reason}: {count}")
        lines.append(f"\n  CSV saved: {self._csv_path}")
        lines.append("=" * 50)
        return "\n".join(lines)
