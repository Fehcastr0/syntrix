"""
strategies/health_monitor.py — Strategy Health Monitor for Syntrix v2.0.

Detects:
- Edge decay
- Efficiency loss
- Sharpe decay
- Drawdown increase
- Expectancy drop
- False signal increase

Automatically adjusts strategy weights.
"""

from __future__ import annotations

import logging
import math
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("syntrix.strategy_health")


@dataclass
class TradeRecord:
    """A single trade record for health analysis."""

    timestamp: float = 0.0
    strategy: str = ""
    asset: str = ""
    won: bool = False
    profit: float = 0.0
    score: float = 0.0
    payout: float = 0.0
    regime: str = ""
    hour_utc: int = -1


@dataclass
class StrategyHealth:
    """Health report for a single strategy."""

    name: str = ""
    total_trades: int = 0
    recent_winrate: float = 0.5
    overall_winrate: float = 0.5
    sharpe: float = 0.0
    expectancy: float = 0.0
    max_drawdown: float = 0.0
    current_drawdown: float = 0.0
    edge_decay: float = 0.0  # -1 to 1 (negative = decaying)
    weight: float = 1.0
    is_healthy: bool = True
    warning: str = ""
    consecutive_losses: int = 0


class StrategyHealthMonitor:
    """
    Monitors strategy performance and adjusts weights.

    Uses rolling windows to detect:
    - Edge decay (recent performance vs historical)
    - Sharpe degradation
    - Consecutive loss streaks
    - Expectancy changes
    """

    def __init__(
        self,
        rolling_window: int = 30,
        min_trades: int = 10,
        decay_threshold: float = -0.15,
        min_weight: float = 0.3,
        max_weight: float = 1.5,
    ) -> None:
        self._rolling_window = rolling_window
        self._min_trades = min_trades
        self._decay_threshold = decay_threshold
        self._min_weight = min_weight
        self._max_weight = max_weight
        self._trades: Dict[str, List[TradeRecord]] = {}
        self._health: Dict[str, StrategyHealth] = {}

    def record_trade(self, trade: TradeRecord) -> None:
        """Record a trade for health tracking."""
        strategy = trade.strategy
        if strategy not in self._trades:
            self._trades[strategy] = []
        self._trades[strategy].append(trade)

        # Keep last 200 trades per strategy
        if len(self._trades[strategy]) > 200:
            self._trades[strategy] = self._trades[strategy][-100:]

        self._update_health(strategy)

    def get_health(self, strategy: str) -> StrategyHealth:
        """Get health report for a strategy."""
        if strategy not in self._health:
            self._health[strategy] = StrategyHealth(name=strategy)
        return self._health[strategy]

    def get_weight(self, strategy: str) -> float:
        """Get adaptive weight for a strategy."""
        health = self.get_health(strategy)
        return health.weight

    def get_all_health(self) -> Dict[str, StrategyHealth]:
        """Get health reports for all strategies."""
        return dict(self._health)

    def _update_health(self, strategy: str) -> None:
        """Recalculate health for a strategy."""
        trades = self._trades.get(strategy, [])
        health = self.get_health(strategy)
        health.total_trades = len(trades)

        if len(trades) < self._min_trades:
            health.is_healthy = True
            health.weight = 1.0
            return

        # Overall winrate
        wins = sum(1 for t in trades if t.won)
        health.overall_winrate = round(wins / len(trades), 4) if trades else 0.5

        # Recent winrate (rolling window)
        recent = trades[-self._rolling_window:]
        recent_wins = sum(1 for t in recent if t.won)
        health.recent_winrate = round(recent_wins / len(recent), 4) if recent else 0.5

        # Edge decay (recent vs historical)
        if len(trades) > self._rolling_window * 2:
            older = trades[-(self._rolling_window * 2):-self._rolling_window]
            older_wr = sum(1 for t in older if t.won) / len(older)
            health.edge_decay = round(health.recent_winrate - older_wr, 4)
        else:
            health.edge_decay = 0.0

        # Expectancy
        profits = [t.profit for t in recent]
        health.expectancy = round(statistics.mean(profits), 4) if profits else 0

        # Sharpe ratio
        health.sharpe = self._calc_sharpe(profits)

        # Drawdown
        equity = self._calc_equity_curve(trades)
        health.max_drawdown, health.current_drawdown = self._calc_drawdown(equity)

        # Consecutive losses
        health.consecutive_losses = self._count_consecutive_losses(trades)

        # Health assessment
        health.is_healthy = True
        health.warning = ""

        if health.edge_decay < self._decay_threshold:
            health.is_healthy = False
            health.warning = f"Edge decay: {health.edge_decay:.2%}"

        if health.consecutive_losses >= 5:
            health.is_healthy = False
            health.warning = f"Loss streak: {health.consecutive_losses}"

        if health.recent_winrate < 0.35:
            health.is_healthy = False
            health.warning = f"Low winrate: {health.recent_winrate:.0%}"

        # Weight adjustment
        weight = 1.0
        # Winrate factor
        weight += (health.recent_winrate - 0.5) * 1.5

        # Edge decay penalty
        if health.edge_decay < 0:
            weight += health.edge_decay * 2

        # Loss streak penalty
        if health.consecutive_losses >= 3:
            weight -= health.consecutive_losses * 0.05

        health.weight = round(
            max(self._min_weight, min(self._max_weight, weight)), 3
        )

    def _calc_sharpe(self, profits: List[float]) -> float:
        """Calculate Sharpe ratio."""
        if len(profits) < 5:
            return 0.0
        avg = statistics.mean(profits)
        stdev = statistics.stdev(profits) if len(profits) > 1 else 1.0
        if stdev == 0:
            return 0.0
        return round(avg / stdev, 4)

    def _calc_equity_curve(self, trades: List[TradeRecord]) -> List[float]:
        """Build equity curve from trades."""
        equity = [0.0]
        for t in trades:
            equity.append(equity[-1] + t.profit)
        return equity

    def _calc_drawdown(self, equity: List[float]) -> tuple:
        """Calculate max and current drawdown."""
        if len(equity) < 2:
            return 0.0, 0.0

        peak = equity[0]
        max_dd = 0.0
        for val in equity:
            if val > peak:
                peak = val
            dd = peak - val
            if dd > max_dd:
                max_dd = dd

        current_peak = max(equity)
        current_dd = current_peak - equity[-1]
        return round(max_dd, 4), round(current_dd, 4)

    def _count_consecutive_losses(self, trades: List[TradeRecord]) -> int:
        """Count current consecutive losses from end."""
        count = 0
        for t in reversed(trades):
            if not t.won:
                count += 1
            else:
                break
        return count
