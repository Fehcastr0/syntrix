"""
analytics/trade_analytics.py — Trade analytics engine for Syntrix.

Computes:
- Winrate
- Sharpe ratio
- Maximum drawdown
- Profit factor
- Expected edge (real vs theoretical)
- Performance by regime, session, asset
- Statistical degradation detection
- Post-loss behavior analysis
- Per-profile metrics

Analytics NEVER blocks execution. All computations are on-demand.
"""

from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("syntrix.analytics")


@dataclass
class TradeRecord:
    """Minimal trade record for analytics."""

    trade_id: str = ""
    asset: str = ""
    direction: str = ""
    strategy: str = ""
    regime: str = ""
    session: str = ""
    profile: str = ""
    payout: float = 0.0
    amount: float = 0.0
    result: str = ""  # "win" / "loss" / "tie"
    profit: float = 0.0
    timestamp: float = 0.0
    score: float = 0.0


class TradeAnalytics:
    """
    Non-blocking analytics engine.

    Call update() to feed trade data. Call methods to compute metrics.
    """

    def __init__(self) -> None:
        self._trades: List[TradeRecord] = []

    @property
    def trade_count(self) -> int:
        return len(self._trades)

    def add_trade(self, trade: TradeRecord) -> None:
        """Add a trade record for analysis."""
        self._trades.append(trade)

    def add_trades(self, trades: List[TradeRecord]) -> None:
        """Add multiple trade records."""
        self._trades.extend(trades)

    def clear(self) -> None:
        """Clear all trade data."""
        self._trades.clear()

    # ── Core Metrics ──

    def winrate(self, trades: Optional[List[TradeRecord]] = None) -> float:
        """Calculate win rate as percentage."""
        t = trades or self._trades
        decided = [x for x in t if x.result in ("win", "loss")]
        if not decided:
            return 0.0
        wins = sum(1 for x in decided if x.result == "win")
        return round(wins / len(decided) * 100, 2)

    def total_profit(self, trades: Optional[List[TradeRecord]] = None) -> float:
        """Calculate total profit/loss."""
        t = trades or self._trades
        return round(sum(x.profit for x in t), 2)

    def profit_factor(self, trades: Optional[List[TradeRecord]] = None) -> float:
        """Calculate profit factor (gross profits / gross losses)."""
        t = trades or self._trades
        gross_profit = sum(x.profit for x in t if x.profit > 0)
        gross_loss = abs(sum(x.profit for x in t if x.profit < 0))
        if gross_loss == 0:
            return float("inf") if gross_profit > 0 else 0.0
        return round(gross_profit / gross_loss, 3)

    def sharpe_ratio(
        self, trades: Optional[List[TradeRecord]] = None, risk_free: float = 0.0
    ) -> float:
        """
        Calculate simplified Sharpe ratio from trade returns.

        Uses per-trade returns, not annualized.
        """
        t = trades or self._trades
        returns = [x.profit for x in t if x.result in ("win", "loss")]
        if len(returns) < 2:
            return 0.0
        mean_ret = statistics.mean(returns) - risk_free
        std_ret = statistics.stdev(returns)
        if std_ret == 0:
            return 0.0
        return round(mean_ret / std_ret, 3)

    def max_drawdown(self, trades: Optional[List[TradeRecord]] = None) -> Dict[str, float]:
        """Calculate maximum drawdown from trade sequence."""
        t = trades or self._trades
        if not t:
            return {"max_drawdown": 0.0, "max_drawdown_pct": 0.0}

        equity = 0.0
        peak = 0.0
        max_dd = 0.0

        for trade in t:
            equity += trade.profit
            if equity > peak:
                peak = equity
            dd = peak - equity
            if dd > max_dd:
                max_dd = dd

        max_dd_pct = (max_dd / peak * 100) if peak > 0 else 0.0

        return {
            "max_drawdown": round(max_dd, 2),
            "max_drawdown_pct": round(max_dd_pct, 2),
        }

    def expected_edge(self, trades: Optional[List[TradeRecord]] = None) -> Dict[str, float]:
        """
        Calculate expected edge: actual vs theoretical.

        Theoretical = (winrate * avg_payout) - ((1-winrate) * 1)
        Actual = avg profit per trade
        """
        t = trades or self._trades
        decided = [x for x in t if x.result in ("win", "loss")]
        if not decided:
            return {"theoretical_edge": 0.0, "actual_edge": 0.0, "edge_gap": 0.0}

        wr = self.winrate(decided) / 100
        avg_payout = statistics.mean(x.payout for x in decided) if decided else 0
        theoretical = (wr * avg_payout) - ((1 - wr) * 1)
        actual = statistics.mean(x.profit for x in decided)

        return {
            "theoretical_edge": round(theoretical, 4),
            "actual_edge": round(actual, 4),
            "edge_gap": round(actual - theoretical, 4),
        }

    # ── Breakdown Metrics ──

    def by_regime(self) -> Dict[str, Dict[str, Any]]:
        """Performance breakdown by market regime."""
        return self._breakdown_by("regime")

    def by_session(self) -> Dict[str, Dict[str, Any]]:
        """Performance breakdown by trading session."""
        return self._breakdown_by("session")

    def by_asset(self) -> Dict[str, Dict[str, Any]]:
        """Performance breakdown by asset."""
        return self._breakdown_by("asset")

    def by_strategy(self) -> Dict[str, Dict[str, Any]]:
        """Performance breakdown by strategy."""
        return self._breakdown_by("strategy")

    def by_profile(self) -> Dict[str, Dict[str, Any]]:
        """Performance breakdown by profile."""
        return self._breakdown_by("profile")

    def _breakdown_by(self, key: str) -> Dict[str, Dict[str, Any]]:
        """Generic breakdown by any trade attribute."""
        groups: Dict[str, List[TradeRecord]] = {}
        for t in self._trades:
            val = getattr(t, key, "unknown")
            if not val:
                val = "unknown"
            groups.setdefault(val, []).append(t)

        result: Dict[str, Dict[str, Any]] = {}
        for group_name, trades in groups.items():
            result[group_name] = {
                "count": len(trades),
                "winrate": self.winrate(trades),
                "profit": self.total_profit(trades),
                "profit_factor": self.profit_factor(trades),
                "sharpe": self.sharpe_ratio(trades),
                "max_drawdown": self.max_drawdown(trades)["max_drawdown"],
            }
        return result

    # ── Degradation Detection ──

    def detect_degradation(self, window: int = 20) -> Dict[str, Any]:
        """
        Detect statistical degradation by comparing recent vs historical performance.

        Compares last `window` trades vs all previous trades.
        """
        if len(self._trades) < window * 2:
            return {"degraded": False, "reason": "Insufficient data"}

        recent = self._trades[-window:]
        historical = self._trades[:-window]

        recent_wr = self.winrate(recent)
        hist_wr = self.winrate(historical)
        recent_pf = self.profit_factor(recent)
        hist_pf = self.profit_factor(historical)

        wr_drop = hist_wr - recent_wr
        degraded = wr_drop > 15.0 or (recent_pf < 1.0 and hist_pf >= 1.0)

        return {
            "degraded": degraded,
            "recent_winrate": recent_wr,
            "historical_winrate": hist_wr,
            "winrate_drop": round(wr_drop, 2),
            "recent_profit_factor": recent_pf,
            "historical_profit_factor": hist_pf,
            "window": window,
            "reason": f"WR dropped {wr_drop:.1f}pp" if degraded else "Stable",
        }

    # ── Post-Loss Analysis ──

    def post_loss_behavior(self) -> Dict[str, Any]:
        """Analyze trading behavior immediately after losses."""
        decided = [t for t in self._trades if t.result in ("win", "loss")]
        if len(decided) < 3:
            return {"sufficient_data": False}

        after_loss_results: List[str] = []
        for i in range(1, len(decided)):
            if decided[i - 1].result == "loss":
                after_loss_results.append(decided[i].result)

        if not after_loss_results:
            return {"sufficient_data": False, "losses_followed": 0}

        wins_after_loss = sum(1 for r in after_loss_results if r == "win")
        wr_after_loss = wins_after_loss / len(after_loss_results) * 100

        return {
            "sufficient_data": True,
            "trades_after_loss": len(after_loss_results),
            "winrate_after_loss": round(wr_after_loss, 2),
            "overall_winrate": self.winrate(),
            "tilt_indicator": round(self.winrate() - wr_after_loss, 2),
        }

    # ── Full Report ──

    def full_report(self) -> Dict[str, Any]:
        """Generate comprehensive analytics report."""
        return {
            "total_trades": self.trade_count,
            "winrate": self.winrate(),
            "total_profit": self.total_profit(),
            "profit_factor": self.profit_factor(),
            "sharpe_ratio": self.sharpe_ratio(),
            "max_drawdown": self.max_drawdown(),
            "expected_edge": self.expected_edge(),
            "by_regime": self.by_regime(),
            "by_session": self.by_session(),
            "by_asset": self.by_asset(),
            "by_strategy": self.by_strategy(),
            "by_profile": self.by_profile(),
            "degradation": self.detect_degradation(),
            "post_loss": self.post_loss_behavior(),
        }
