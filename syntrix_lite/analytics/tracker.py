"""
Analytics — winrate, expectancy, drawdown, sharpe simples.
Por ativo, por horário, por estratégia.
Descobrir rapidamente o que funciona e o que não funciona.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class TradeLog:
    asset: str = ""
    direction: str = ""
    strategy: str = ""
    score: float = 0.0
    payout: float = 0.0
    result: str = ""  # "win", "loss"
    profit: float = 0.0
    hour_utc: int = 0
    timestamp: float = 0.0


class Analytics:
    """Simple trade analytics."""

    def __init__(self) -> None:
        self._trades: List[TradeLog] = []

    def record(self, trade: TradeLog) -> None:
        self._trades.append(trade)

    @property
    def total(self) -> int:
        return len(self._trades)

    def winrate(self) -> float:
        if not self._trades:
            return 0.0
        wins = sum(1 for t in self._trades if t.result == "win")
        return wins / len(self._trades)

    def expectancy(self) -> float:
        """Expected profit per trade."""
        if not self._trades:
            return 0.0
        return sum(t.profit for t in self._trades) / len(self._trades)

    def total_pnl(self) -> float:
        return sum(t.profit for t in self._trades)

    def max_drawdown(self) -> float:
        if not self._trades:
            return 0.0
        peak = 0.0
        equity = 0.0
        max_dd = 0.0
        for t in self._trades:
            equity += t.profit
            if equity > peak:
                peak = equity
            dd = peak - equity
            if dd > max_dd:
                max_dd = dd
        return max_dd

    def sharpe(self) -> float:
        """Simple Sharpe ratio (returns / std)."""
        if len(self._trades) < 2:
            return 0.0
        returns = [t.profit for t in self._trades]
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / len(returns)
        std = math.sqrt(variance) if variance > 0 else 0
        return mean / std if std > 0 else 0.0

    def by_asset(self) -> Dict[str, Dict]:
        return self._group_by(lambda t: t.asset)

    def by_strategy(self) -> Dict[str, Dict]:
        return self._group_by(lambda t: t.strategy)

    def by_hour(self) -> Dict[int, Dict]:
        groups: Dict[int, List[TradeLog]] = {}
        for t in self._trades:
            groups.setdefault(t.hour_utc, []).append(t)
        result = {}
        for hour in sorted(groups.keys()):
            trades = groups[hour]
            wins = sum(1 for t in trades if t.result == "win")
            wr = wins / len(trades) if trades else 0
            pnl = sum(t.profit for t in trades)
            exp = pnl / len(trades) if trades else 0
            result[hour] = {
                "trades": len(trades), "wins": wins,
                "winrate": round(wr * 100, 1),
                "pnl": round(pnl, 2),
                "expectancy": round(exp, 4),
            }
        return result

    def _group_by(self, key_fn) -> Dict:
        groups: Dict[str, List[TradeLog]] = {}
        for t in self._trades:
            k = key_fn(t)
            groups.setdefault(k, []).append(t)
        result = {}
        for name in sorted(groups.keys()):
            trades = groups[name]
            wins = sum(1 for t in trades if t.result == "win")
            wr = wins / len(trades) if trades else 0
            pnl = sum(t.profit for t in trades)
            exp = pnl / len(trades) if trades else 0
            result[name] = {
                "trades": len(trades), "wins": wins,
                "winrate": round(wr * 100, 1),
                "pnl": round(pnl, 2),
                "expectancy": round(exp, 4),
            }
        return result

    def format_report(self) -> str:
        lines = [
            "",
            "=" * 50,
            "  ANALYTICS REPORT",
            "=" * 50,
            f"  Total trades: {self.total}",
            f"  Winrate: {self.winrate() * 100:.1f}%",
            f"  PnL: ${self.total_pnl():.2f}",
            f"  Expectancy: ${self.expectancy():.4f}",
            f"  Max Drawdown: ${self.max_drawdown():.2f}",
            f"  Sharpe: {self.sharpe():.3f}",
        ]

        # By strategy
        by_strat = self.by_strategy()
        if by_strat:
            lines.append("\n  By Strategy:")
            for name, stats in by_strat.items():
                lines.append(
                    f"    {name}: {stats['trades']} trades, "
                    f"WR={stats['winrate']}%, "
                    f"PnL=${stats['pnl']}, "
                    f"Exp=${stats['expectancy']}"
                )

        # By asset (top 5)
        by_asset = self.by_asset()
        if by_asset:
            lines.append("\n  By Asset (top 5):")
            sorted_assets = sorted(by_asset.items(), key=lambda x: -x[1]["trades"])[:5]
            for name, stats in sorted_assets:
                lines.append(
                    f"    {name}: {stats['trades']} trades, "
                    f"WR={stats['winrate']}%, "
                    f"PnL=${stats['pnl']}"
                )

        # By hour (top 5)
        by_h = self.by_hour()
        if by_h:
            lines.append("\n  By Hour (top 5 by trades):")
            sorted_hours = sorted(by_h.items(), key=lambda x: -x[1]["trades"])[:5]
            for hour, stats in sorted_hours:
                lines.append(
                    f"    {hour:02d}h UTC: {stats['trades']} trades, "
                    f"WR={stats['winrate']}%, "
                    f"PnL=${stats['pnl']}"
                )

        lines.append("=" * 50)
        return "\n".join(lines)
