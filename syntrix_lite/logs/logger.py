"""
Trade logger — salva trades em CSV para análise posterior.
"""

from __future__ import annotations

import csv
import os
from datetime import datetime, timezone
from typing import Dict


class TradeLogger:
    """Logs trades to CSV."""

    def __init__(self, log_dir: str = "data/trades") -> None:
        self._log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        self._csv_path = os.path.join(log_dir, f"trades_{today}.csv")
        self._init_csv()

    def _init_csv(self) -> None:
        if not os.path.exists(self._csv_path):
            with open(self._csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "timestamp", "datetime", "asset", "direction", "strategy",
                    "score", "payout", "amount", "result", "profit",
                    "rsi", "atr", "hour_utc",
                ])

    def log(self, data: Dict) -> None:
        ts = data.get("timestamp", 0)
        dt = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if ts else ""
        with open(self._csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                ts, dt,
                data.get("asset", ""),
                data.get("direction", ""),
                data.get("strategy", ""),
                f"{data.get('score', 0):.4f}",
                f"{data.get('payout', 0):.2f}",
                f"{data.get('amount', 0):.2f}",
                data.get("result", ""),
                f"{data.get('profit', 0):.2f}",
                f"{data.get('rsi', 0):.1f}",
                f"{data.get('atr', 0):.6f}",
                data.get("hour_utc", 0),
            ])
