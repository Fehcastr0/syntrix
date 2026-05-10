"""
scanner/asset_registry.py — Asset registry and auto-discovery for Syntrix.

Manages the list of assets to monitor:
- Static list of known OTC and Forex assets
- Dynamic discovery via broker API
- Asset quality tracking
- Adaptive scan priority
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("syntrix.scanner.registry")

# All known OTC assets
OTC_ASSETS = [
    "EURUSD-OTC",
    "GBPUSD-OTC",
    "EURJPY-OTC",
    "USDJPY-OTC",
    "AUDCAD-OTC",
    "AUDUSD-OTC",
    "EURGBP-OTC",
    "GBPJPY-OTC",
    "NZDUSD-OTC",
    "USDCAD-OTC",
    "USDCHF-OTC",
    "EURAUD-OTC",
    "GBPAUD-OTC",
    "CADJPY-OTC",
    "CHFJPY-OTC",
]

# All known Forex regular assets
FOREX_ASSETS = [
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "AUDUSD",
    "EURJPY",
    "GBPJPY",
    "USDCAD",
    "USDCHF",
    "EURGBP",
    "NZDUSD",
]


@dataclass
class AssetProfile:
    """Profile for a single asset with performance tracking."""

    name: str
    asset_type: str = "otc"  # otc / forex
    enabled: bool = True
    priority: float = 1.0  # higher = scan more often
    quality_score: float = 0.5
    avg_payout: float = 0.0
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_signals: int = 0
    total_blocks: int = 0
    last_scan_time: float = 0.0
    last_trade_time: float = 0.0
    scan_count: int = 0
    consecutive_blocks: int = 0
    spike_count: int = 0
    is_open: bool = True

    @property
    def winrate(self) -> float:
        decided = self.wins + self.losses
        if decided == 0:
            return 0.5
        return self.wins / decided

    @property
    def block_rate(self) -> float:
        if self.scan_count == 0:
            return 0.0
        return self.total_blocks / self.scan_count

    @property
    def signal_rate(self) -> float:
        if self.scan_count == 0:
            return 0.0
        return self.total_signals / self.scan_count


class AssetRegistry:
    """
    Manages the universe of assets to monitor.

    Handles:
    - Static and dynamic asset lists
    - Per-asset performance tracking
    - Adaptive priority based on history
    - Discovery of open assets via broker
    """

    def __init__(
        self,
        include_otc: bool = True,
        include_forex: bool = True,
        custom_assets: Optional[List[str]] = None,
    ) -> None:
        self._profiles: Dict[str, AssetProfile] = {}
        self._include_otc = include_otc
        self._include_forex = include_forex

        # Register static assets
        if include_otc:
            for name in OTC_ASSETS:
                self._register(name, "otc")
        if include_forex:
            for name in FOREX_ASSETS:
                self._register(name, "forex")
        if custom_assets:
            for name in custom_assets:
                atype = "otc" if name.endswith("-OTC") else "forex"
                self._register(name, atype)

    def _register(self, name: str, asset_type: str) -> None:
        if name not in self._profiles:
            self._profiles[name] = AssetProfile(name=name, asset_type=asset_type)

    def discover_from_broker(self, broker: Any) -> int:
        """Discover open assets from broker API."""
        discovered = 0
        try:
            if hasattr(broker, "get_all_open_assets"):
                open_assets = broker.get_all_open_assets()
                for name in open_assets:
                    atype = "otc" if name.endswith("-OTC") else "forex"
                    if name not in self._profiles:
                        self._register(name, atype)
                        discovered += 1
                    self._profiles[name].is_open = True

                # Mark closed assets
                for name, profile in self._profiles.items():
                    if name not in open_assets:
                        profile.is_open = False

                logger.info("Discovered %d new assets from broker (total open: %d)",
                            discovered, len(open_assets))
        except Exception as exc:
            logger.warning("Asset discovery failed: %s", exc)
        return discovered

    @property
    def all_assets(self) -> List[str]:
        return list(self._profiles.keys())

    @property
    def enabled_assets(self) -> List[str]:
        return [n for n, p in self._profiles.items() if p.enabled and p.is_open]

    def get_profile(self, name: str) -> Optional[AssetProfile]:
        return self._profiles.get(name)

    def get_scan_order(self) -> List[str]:
        """Get assets ordered by priority (highest first)."""
        enabled = [(n, p) for n, p in self._profiles.items() if p.enabled and p.is_open]
        enabled.sort(key=lambda x: x[1].priority, reverse=True)
        return [n for n, _ in enabled]

    def record_scan(self, asset: str) -> None:
        """Record a scan attempt."""
        p = self._profiles.get(asset)
        if p:
            p.scan_count += 1
            p.last_scan_time = time.time()

    def record_block(self, asset: str) -> None:
        """Record a blocked attempt."""
        p = self._profiles.get(asset)
        if p:
            p.total_blocks += 1
            p.consecutive_blocks += 1

    def record_signal(self, asset: str) -> None:
        """Record a signal generated."""
        p = self._profiles.get(asset)
        if p:
            p.total_signals += 1
            p.consecutive_blocks = 0

    def record_trade_result(self, asset: str, won: bool, payout: float = 0.0) -> None:
        """Record a trade result."""
        p = self._profiles.get(asset)
        if p:
            p.total_trades += 1
            if won:
                p.wins += 1
            else:
                p.losses += 1
            p.last_trade_time = time.time()
            # Update rolling payout average
            if payout > 0:
                if p.avg_payout == 0:
                    p.avg_payout = payout
                else:
                    p.avg_payout = p.avg_payout * 0.9 + payout * 0.1

    def update_priorities(self) -> None:
        """Update scan priorities based on performance."""
        for name, p in self._profiles.items():
            priority = 1.0

            # Boost assets with good winrate
            if p.total_trades >= 5:
                wr = p.winrate
                if wr > 0.6:
                    priority += 0.3
                elif wr < 0.4:
                    priority -= 0.3

            # Boost assets with good signal rate
            if p.scan_count >= 10:
                sr = p.signal_rate
                if sr > 0.3:
                    priority += 0.2
                elif sr < 0.05:
                    priority -= 0.2

            # Penalize consistently blocked assets
            if p.consecutive_blocks > 10:
                priority -= 0.3

            # Boost assets with higher payout
            if p.avg_payout > 0.80:
                priority += 0.2

            p.priority = max(0.1, min(2.0, round(priority, 2)))

    def update_quality(self, asset: str, quality_score: float) -> None:
        """Update quality score for an asset."""
        p = self._profiles.get(asset)
        if p:
            p.quality_score = round(quality_score, 3)

    def get_stats(self) -> Dict[str, Any]:
        """Get summary stats for all assets."""
        enabled = [p for p in self._profiles.values() if p.enabled]
        return {
            "total_registered": len(self._profiles),
            "total_enabled": len(enabled),
            "total_open": sum(1 for p in enabled if p.is_open),
            "total_trades": sum(p.total_trades for p in enabled),
            "avg_priority": round(sum(p.priority for p in enabled) / max(1, len(enabled)), 2),
        }
