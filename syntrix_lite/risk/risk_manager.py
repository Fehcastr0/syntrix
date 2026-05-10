"""
Risk Manager — simples e eficiente.
Stop gain, stop loss, max consecutive losses, cooldown, single position.
Sem sistemas ultra complexos.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class RiskConfig:
    stop_gain: float = 30.0
    stop_loss: float = -15.0
    max_consecutive_losses: int = 3
    cooldown_after_loss_sec: float = 60.0
    cooldown_after_trade_sec: float = 15.0
    max_trades_per_session: int = 50


class RiskManager:
    """Simple risk control."""

    def __init__(self, config: RiskConfig | None = None) -> None:
        self.config = config or RiskConfig()
        self.pnl: float = 0.0
        self.total_trades: int = 0
        self.wins: int = 0
        self.losses: int = 0
        self.consecutive_losses: int = 0
        self.last_trade_time: float = 0.0
        self.last_loss_time: float = 0.0
        self.locked: bool = False
        self.lock_reason: str = ""
        self._in_position: bool = False

    @property
    def in_position(self) -> bool:
        return self._in_position

    def can_trade(self) -> tuple[bool, str]:
        """Check if a trade is allowed."""
        if self.locked:
            return False, f"LOCKED: {self.lock_reason}"

        if self._in_position:
            return False, "IN_POSITION"

        # Stop gain
        if self.pnl >= self.config.stop_gain:
            self.locked = True
            self.lock_reason = "STOP_GAIN"
            return False, "STOP_GAIN"

        # Stop loss
        if self.pnl <= self.config.stop_loss:
            self.locked = True
            self.lock_reason = "STOP_LOSS"
            return False, "STOP_LOSS"

        # Max consecutive losses
        if self.consecutive_losses >= self.config.max_consecutive_losses:
            now = time.time()
            wait = self.config.cooldown_after_loss_sec
            if now - self.last_loss_time < wait:
                remaining = wait - (now - self.last_loss_time)
                return False, f"LOSS_COOLDOWN ({remaining:.0f}s)"
            self.consecutive_losses = 0

        # Cooldown after trade
        now = time.time()
        if self.last_trade_time > 0:
            elapsed = now - self.last_trade_time
            if elapsed < self.config.cooldown_after_trade_sec:
                remaining = self.config.cooldown_after_trade_sec - elapsed
                return False, f"TRADE_COOLDOWN ({remaining:.0f}s)"

        # Max trades per session
        if self.total_trades >= self.config.max_trades_per_session:
            self.locked = True
            self.lock_reason = "MAX_TRADES"
            return False, "MAX_TRADES"

        return True, "OK"

    def enter_position(self) -> None:
        self._in_position = True

    def record_result(self, profit: float) -> None:
        """Record trade result."""
        self._in_position = False
        self.pnl += profit
        self.total_trades += 1
        self.last_trade_time = time.time()

        if profit > 0:
            self.wins += 1
            self.consecutive_losses = 0
        else:
            self.losses += 1
            self.consecutive_losses += 1
            self.last_loss_time = time.time()

    def get_stats(self) -> dict:
        wr = self.wins / self.total_trades if self.total_trades > 0 else 0
        return {
            "pnl": round(self.pnl, 2),
            "total_trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
            "winrate": round(wr * 100, 1),
            "consecutive_losses": self.consecutive_losses,
            "locked": self.locked,
            "lock_reason": self.lock_reason,
        }
