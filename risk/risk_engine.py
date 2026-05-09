"""
risk/risk_engine.py — Risk management engine for Syntrix.

Implements:
- Stop gain / stop loss
- Maximum drawdown control
- Trades per hour limit
- Cooldown after losses
- Pause after bad payout
- Pause after extreme volatility
- Automatic session termination by schedule
- Risk lock
- Safe mode
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.events import EventBus, EventPriority, EventType

logger = logging.getLogger("syntrix.risk")


@dataclass
class RiskConfig:
    """Risk engine configuration (loaded from profile)."""

    stop_gain: float = 50.0            # $ profit to stop
    stop_loss: float = -30.0           # $ loss to stop
    max_drawdown_pct: float = 10.0     # % of starting balance
    max_trades_per_hour: int = 10
    max_consecutive_losses: int = 3
    cooldown_after_loss_sec: float = 180.0
    cooldown_after_trade_sec: float = 120.0
    min_payout_threshold: float = 0.70
    session_end_hour_utc: int = 21     # force stop at this hour
    enable_safe_mode: bool = True
    safe_mode_after_losses: int = 5    # enter safe mode after N total losses


@dataclass
class RiskState:
    """Current risk engine state."""

    pnl: float = 0.0
    starting_balance: float = 0.0
    peak_balance: float = 0.0
    total_trades: int = 0
    total_wins: int = 0
    total_losses: int = 0
    consecutive_losses: int = 0
    trades_this_hour: int = 0
    hour_start: float = field(default_factory=time.time)
    last_trade_time: float = 0.0
    last_loss_time: float = 0.0
    locked: bool = False
    lock_reason: str = ""
    safe_mode: bool = False
    session_terminated: bool = False


class RiskEngine:
    """
    Central risk management engine.

    All trade decisions must pass through risk checks.
    The engine emits events for risk state changes and can
    lock the system or enter safe mode.
    """

    def __init__(self, event_bus: EventBus, config: Optional[RiskConfig] = None) -> None:
        self._event_bus = event_bus
        self._config = config or RiskConfig()
        self._state = RiskState()
        self._lock = threading.Lock()
        self._history: List[Dict[str, Any]] = []

    @property
    def config(self) -> RiskConfig:
        return self._config

    @property
    def state(self) -> RiskState:
        return self._state

    @property
    def is_locked(self) -> bool:
        with self._lock:
            return self._state.locked

    @property
    def is_safe_mode(self) -> bool:
        with self._lock:
            return self._state.safe_mode

    def set_starting_balance(self, balance: float) -> None:
        """Set starting balance for drawdown calculations."""
        with self._lock:
            self._state.starting_balance = balance
            self._state.peak_balance = balance
        logger.info("Risk engine: starting balance = $%.2f", balance)

    def update_config(self, config: RiskConfig) -> None:
        """Update risk configuration (from profile change)."""
        with self._lock:
            self._config = config
        logger.info("Risk config updated")

    def check_trade_allowed(self, payout: float = 0.0) -> Dict[str, Any]:
        """
        Check whether a new trade is allowed under current risk constraints.

        Returns:
            Dict with: allowed, reasons (list of block reasons)
        """
        with self._lock:
            reasons: List[str] = []

            if self._state.session_terminated:
                reasons.append("Session terminated")

            if self._state.locked:
                reasons.append(f"Risk locked: {self._state.lock_reason}")

            if self._state.safe_mode:
                reasons.append("Safe mode active")

            if self._state.pnl >= self._config.stop_gain:
                reasons.append(f"Stop gain reached (PnL=${self._state.pnl:.2f})")
                self._lock_system("Stop gain hit")
                self._event_bus.emit(
                    EventType.STOP_GAIN_HIT,
                    data={"pnl": self._state.pnl},
                    priority=EventPriority.CRITICAL,
                )

            if self._state.pnl <= self._config.stop_loss:
                reasons.append(f"Stop loss reached (PnL=${self._state.pnl:.2f})")
                self._lock_system("Stop loss hit")
                self._event_bus.emit(
                    EventType.STOP_LOSS_HIT,
                    data={"pnl": self._state.pnl},
                    priority=EventPriority.CRITICAL,
                )

            drawdown = self._calc_drawdown_pct()
            if drawdown >= self._config.max_drawdown_pct:
                reasons.append(f"Max drawdown {drawdown:.1f}% >= {self._config.max_drawdown_pct:.1f}%")
                self._event_bus.emit(
                    EventType.DRAWDOWN_ALERT,
                    data={"drawdown_pct": drawdown},
                    priority=EventPriority.HIGH,
                )

            now = time.time()
            if now - self._state.hour_start > 3600:
                self._state.trades_this_hour = 0
                self._state.hour_start = now

            if self._state.trades_this_hour >= self._config.max_trades_per_hour:
                reasons.append(
                    f"Trades/hour limit ({self._state.trades_this_hour}/{self._config.max_trades_per_hour})"
                )

            if self._state.consecutive_losses >= self._config.max_consecutive_losses:
                reasons.append(
                    f"Consecutive losses ({self._state.consecutive_losses}) >= max ({self._config.max_consecutive_losses})"
                )

            if self._state.last_loss_time > 0:
                since_loss = now - self._state.last_loss_time
                if since_loss < self._config.cooldown_after_loss_sec:
                    remaining = self._config.cooldown_after_loss_sec - since_loss
                    reasons.append(f"Loss cooldown ({remaining:.0f}s remaining)")

            if self._state.last_trade_time > 0:
                since_trade = now - self._state.last_trade_time
                if since_trade < self._config.cooldown_after_trade_sec:
                    remaining = self._config.cooldown_after_trade_sec - since_trade
                    reasons.append(f"Trade cooldown ({remaining:.0f}s remaining)")

            if payout > 0 and payout < self._config.min_payout_threshold:
                reasons.append(
                    f"Payout {payout:.0%} below risk threshold {self._config.min_payout_threshold:.0%}"
                )

            from datetime import datetime, timezone
            current_hour = datetime.now(timezone.utc).hour
            if current_hour >= self._config.session_end_hour_utc:
                reasons.append(f"Past session end ({self._config.session_end_hour_utc}:00 UTC)")
                self._state.session_terminated = True

            allowed = len(reasons) == 0

            if not allowed:
                self._event_bus.emit(
                    EventType.RISK_CHECK_FAILED,
                    data={"reasons": reasons},
                    priority=EventPriority.HIGH,
                )
            else:
                self._event_bus.emit(
                    EventType.RISK_CHECK_PASSED,
                    data={"pnl": self._state.pnl, "trades": self._state.total_trades},
                )

            return {"allowed": allowed, "reasons": reasons}

    def record_trade_result(self, profit: float) -> None:
        """Update risk state after a trade result."""
        with self._lock:
            self._state.pnl += profit
            self._state.total_trades += 1
            self._state.trades_this_hour += 1
            self._state.last_trade_time = time.time()

            current_balance = self._state.starting_balance + self._state.pnl
            if current_balance > self._state.peak_balance:
                self._state.peak_balance = current_balance

            if profit > 0:
                self._state.total_wins += 1
                self._state.consecutive_losses = 0
            else:
                self._state.total_losses += 1
                self._state.consecutive_losses += 1
                self._state.last_loss_time = time.time()

            self._history.append({
                "profit": profit,
                "pnl": self._state.pnl,
                "timestamp": time.time(),
                "consecutive_losses": self._state.consecutive_losses,
            })

            if (
                self._config.enable_safe_mode
                and self._state.total_losses >= self._config.safe_mode_after_losses
                and not self._state.safe_mode
            ):
                self._enter_safe_mode()

            logger.info(
                "Trade result: $%.2f | PnL: $%.2f | W/L: %d/%d | Consec losses: %d",
                profit,
                self._state.pnl,
                self._state.total_wins,
                self._state.total_losses,
                self._state.consecutive_losses,
            )

    def reset_session(self) -> None:
        """Reset risk state for a new session."""
        with self._lock:
            self._state = RiskState()
            self._history.clear()
        logger.info("Risk engine session reset")

    def unlock(self) -> None:
        """Manually unlock the risk engine."""
        with self._lock:
            self._state.locked = False
            self._state.lock_reason = ""
        logger.info("Risk engine unlocked")

    def exit_safe_mode(self) -> None:
        """Exit safe mode."""
        with self._lock:
            self._state.safe_mode = False
        self._event_bus.emit(
            EventType.SAFE_MODE_EXIT,
            priority=EventPriority.HIGH,
        )
        logger.info("Exited safe mode")

    def get_snapshot(self) -> Dict[str, Any]:
        """Get full risk state snapshot."""
        with self._lock:
            drawdown = self._calc_drawdown_pct()
            return {
                "pnl": round(self._state.pnl, 2),
                "starting_balance": self._state.starting_balance,
                "peak_balance": self._state.peak_balance,
                "drawdown_pct": round(drawdown, 2),
                "total_trades": self._state.total_trades,
                "total_wins": self._state.total_wins,
                "total_losses": self._state.total_losses,
                "consecutive_losses": self._state.consecutive_losses,
                "trades_this_hour": self._state.trades_this_hour,
                "locked": self._state.locked,
                "lock_reason": self._state.lock_reason,
                "safe_mode": self._state.safe_mode,
                "session_terminated": self._state.session_terminated,
            }

    def _lock_system(self, reason: str) -> None:
        """Lock the system (must hold self._lock)."""
        if not self._state.locked:
            self._state.locked = True
            self._state.lock_reason = reason
            self._event_bus.emit(
                EventType.RISK_LOCK,
                data={"reason": reason},
                priority=EventPriority.CRITICAL,
            )
            logger.warning("RISK LOCK: %s", reason)

    def _enter_safe_mode(self) -> None:
        """Enter safe mode (must hold self._lock)."""
        self._state.safe_mode = True
        self._event_bus.emit(
            EventType.SAFE_MODE_ENTER,
            data={"total_losses": self._state.total_losses},
            priority=EventPriority.CRITICAL,
        )
        logger.warning("SAFE MODE entered after %d losses", self._state.total_losses)

    def _calc_drawdown_pct(self) -> float:
        """Calculate current drawdown as percentage of peak balance."""
        if self._state.peak_balance <= 0:
            return 0.0
        current = self._state.starting_balance + self._state.pnl
        dd = (self._state.peak_balance - current) / self._state.peak_balance * 100
        return max(0.0, dd)
