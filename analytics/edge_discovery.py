"""
analytics/edge_discovery.py — Edge Discovery Engine for Syntrix.

Statistically discovers:
- Best assets
- Best hours
- Best market phases
- Best strategies
- Best contextual combinations

Purpose: find repeatable statistical asymmetries.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("syntrix.edge")


@dataclass
class EdgeProfile:
    """Statistical profile for an edge condition."""

    condition: str = ""
    total: int = 0
    wins: int = 0
    winrate: float = 0.0
    expectancy: float = 0.0
    avg_payout: float = 0.0
    sharpe: float = 0.0
    confidence: float = 0.0  # statistical confidence (based on sample size)


@dataclass
class EdgeReport:
    """Full edge discovery report."""

    by_asset: List[EdgeProfile] = field(default_factory=list)
    by_hour: List[EdgeProfile] = field(default_factory=list)
    by_phase: List[EdgeProfile] = field(default_factory=list)
    by_strategy: List[EdgeProfile] = field(default_factory=list)
    by_regime: List[EdgeProfile] = field(default_factory=list)
    top_combinations: List[EdgeProfile] = field(default_factory=list)
    total_trades: int = 0


class EdgeDiscoveryEngine:
    """
    Discovers edges from trade history.

    Analyzes feature store data to find statistically significant patterns.
    """

    def __init__(self, min_samples: int = 20) -> None:
        self._min_samples = min_samples
        self._trades: List[Dict[str, Any]] = []

    def record_trade(
        self,
        asset: str,
        strategy: str,
        regime: str,
        market_phase: str,
        hour_utc: int,
        won: bool,
        payout: float,
        meta_score: float = 0.0,
        confidence: float = 0.0,
        otc_quality: float = 0.0,
        volatility: float = 0.0,
    ) -> None:
        """Record a trade for edge analysis."""
        self._trades.append({
            "asset": asset,
            "strategy": strategy,
            "regime": regime,
            "market_phase": market_phase,
            "hour_utc": hour_utc,
            "won": won,
            "payout": payout,
            "meta_score": meta_score,
            "confidence": confidence,
            "otc_quality": otc_quality,
            "volatility": volatility,
        })

    def discover(self) -> EdgeReport:
        """Run full edge discovery analysis."""
        if len(self._trades) < self._min_samples:
            return EdgeReport(total_trades=len(self._trades))

        report = EdgeReport(total_trades=len(self._trades))

        report.by_asset = self._analyze_dimension("asset")
        report.by_strategy = self._analyze_dimension("strategy")
        report.by_regime = self._analyze_dimension("regime")
        report.by_phase = self._analyze_dimension("market_phase")
        report.by_hour = self._analyze_dimension("hour_utc")
        report.top_combinations = self._analyze_combinations()

        return report

    def _analyze_dimension(self, key: str) -> List[EdgeProfile]:
        """Analyze edge by a single dimension."""
        groups: Dict[str, List[Dict]] = defaultdict(list)
        for trade in self._trades:
            val = str(trade.get(key, ""))
            if val:
                groups[val].append(trade)

        profiles: List[EdgeProfile] = []
        for value, trades in groups.items():
            if len(trades) < max(5, self._min_samples // 3):
                continue

            wins = sum(1 for t in trades if t["won"])
            wr = wins / len(trades)
            avg_payout = sum(t["payout"] for t in trades) / len(trades)
            exp = (wr * avg_payout) - ((1 - wr) * 1.0)

            # Statistical confidence based on sample size
            import math
            stat_conf = min(1.0, math.sqrt(len(trades) / 100))

            profiles.append(EdgeProfile(
                condition=f"{key}={value}",
                total=len(trades),
                wins=wins,
                winrate=round(wr, 3),
                expectancy=round(exp, 4),
                avg_payout=round(avg_payout, 3),
                confidence=round(stat_conf, 2),
            ))

        return sorted(profiles, key=lambda p: p.expectancy, reverse=True)

    def _analyze_combinations(self) -> List[EdgeProfile]:
        """Analyze 2-dimension combinations for hidden edges."""
        combos: Dict[str, List[Dict]] = defaultdict(list)

        for trade in self._trades:
            # Asset + regime
            key1 = f"{trade['asset']}+{trade['regime']}"
            combos[key1].append(trade)

            # Strategy + phase
            key2 = f"{trade['strategy']}+{trade['market_phase']}"
            combos[key2].append(trade)

            # Asset + hour
            key3 = f"{trade['asset']}+h{trade['hour_utc']}"
            combos[key3].append(trade)

        profiles: List[EdgeProfile] = []
        for combo, trades in combos.items():
            if len(trades) < max(5, self._min_samples // 2):
                continue

            wins = sum(1 for t in trades if t["won"])
            wr = wins / len(trades)
            avg_payout = sum(t["payout"] for t in trades) / len(trades)
            exp = (wr * avg_payout) - ((1 - wr) * 1.0)

            import math
            stat_conf = min(1.0, math.sqrt(len(trades) / 50))

            profiles.append(EdgeProfile(
                condition=combo,
                total=len(trades),
                wins=wins,
                winrate=round(wr, 3),
                expectancy=round(exp, 4),
                avg_payout=round(avg_payout, 3),
                confidence=round(stat_conf, 2),
            ))

        return sorted(profiles, key=lambda p: p.expectancy, reverse=True)[:20]

    def get_best_assets(self, n: int = 5) -> List[str]:
        """Get top N assets by expectancy."""
        report = self.discover()
        return [p.condition.split("=")[1] for p in report.by_asset[:n] if p.expectancy > 0]

    def get_best_hours(self) -> List[int]:
        """Get hours with positive expectancy."""
        report = self.discover()
        return [int(p.condition.split("=")[1]) for p in report.by_hour if p.expectancy > 0]

    def format_report(self) -> str:
        """Format edge discovery report."""
        report = self.discover()
        lines = [
            "=" * 60,
            "  EDGE DISCOVERY REPORT",
            f"  Total trades analyzed: {report.total_trades}",
            "=" * 60,
        ]

        for section_name, profiles in [
            ("ASSETS", report.by_asset),
            ("STRATEGIES", report.by_strategy),
            ("REGIMES", report.by_regime),
            ("MARKET PHASES", report.by_phase),
            ("HOURS (UTC)", report.by_hour),
        ]:
            if profiles:
                lines.append(f"\n  {section_name}:")
                lines.append("  " + "-" * 55)
                for p in profiles[:8]:
                    marker = "+" if p.expectancy > 0 else "-"
                    lines.append(
                        f"  {marker} {p.condition:30s} | "
                        f"N={p.total:4d} | WR={p.winrate:5.1%} | "
                        f"EXP={p.expectancy:+.4f}"
                    )

        if report.top_combinations:
            lines.append("\n  TOP COMBINATIONS:")
            lines.append("  " + "-" * 55)
            for p in report.top_combinations[:10]:
                if p.expectancy > 0:
                    lines.append(
                        f"  + {p.condition:35s} | "
                        f"N={p.total:4d} | WR={p.winrate:5.1%} | "
                        f"EXP={p.expectancy:+.4f}"
                    )

        lines.append("\n" + "=" * 60)
        return "\n".join(lines)
