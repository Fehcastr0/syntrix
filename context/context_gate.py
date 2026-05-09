"""
context/context_gate.py — Central context gate for Syntrix.

The ContextGate is the main decision filter. Default state is BLOCK.
Only allows trading when ALL contextual checks pass.

Returns: ALLOW, CAUTION, or BLOCK

Checks:
- Minimum payout
- Valid session
- News proximity
- Market regime
- Volatility
- Latency
- Spike detection
- Cooldown
- Trades per hour
- Current drawdown
- Broker health
"""

from __future__ import annotations

import logging
import time
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional

from brokers.base import BaseBroker, Candle
from context.market_regime import MarketRegime, MarketRegimeDetector
from context.news_filter import NewsFilter
from context.session_filter import SessionFilter
from context.spike_detector import SpikeDetector

logger = logging.getLogger("syntrix.context.gate")


class ContextDecision(Enum):
    """Context gate decision."""

    ALLOW = "allow"
    CAUTION = "caution"
    BLOCK = "block"


class ContextGate:
    """
    Central context gate — aggregates all context checks.

    Default: BLOCK. Only returns ALLOW when all checks pass.
    Returns CAUTION when some checks warn but none hard-block.
    """

    def __init__(
        self,
        session_filter: SessionFilter,
        news_filter: NewsFilter,
        regime_detector: MarketRegimeDetector,
        spike_detector: SpikeDetector,
        min_payout: float = 0.70,
        max_latency_ms: float = 500.0,
        max_trades_per_hour: int = 10,
        max_drawdown_pct: float = 10.0,
        cooldown_seconds: float = 120.0,
        blocked_regimes: Optional[List[MarketRegime]] = None,
    ) -> None:
        self._session_filter = session_filter
        self._news_filter = news_filter
        self._regime_detector = regime_detector
        self._spike_detector = spike_detector
        self._min_payout = min_payout
        self._max_latency_ms = max_latency_ms
        self._max_trades_per_hour = max_trades_per_hour
        self._max_drawdown_pct = max_drawdown_pct
        self._cooldown_seconds = cooldown_seconds
        self._blocked_regimes = blocked_regimes or [
            MarketRegime.UNSTABLE,
            MarketRegime.HIGH_VOLATILITY,
        ]
        self._last_trade_time: float = 0.0
        self._trades_this_hour: int = 0
        self._hour_start: float = time.time()
        self._custom_checks: List[Callable[..., Dict[str, Any]]] = []

    def add_custom_check(self, check_fn: Callable[..., Dict[str, Any]]) -> None:
        """Register a custom context check function."""
        self._custom_checks.append(check_fn)

    def record_trade(self) -> None:
        """Record that a trade was executed (updates rate limits)."""
        now = time.time()
        if now - self._hour_start > 3600:
            self._trades_this_hour = 0
            self._hour_start = now
        self._trades_this_hour += 1
        self._last_trade_time = now

    def evaluate(
        self,
        asset: str,
        payout: float,
        candles: List[Candle],
        broker_latency_ms: float = 0.0,
        current_drawdown_pct: float = 0.0,
        broker_healthy: bool = True,
    ) -> Dict[str, Any]:
        """
        Run all context checks and return aggregated decision.

        Returns:
            Dict with: decision, checks (list of individual results), block_reasons
        """
        checks: List[Dict[str, Any]] = []
        block_reasons: List[str] = []
        caution_reasons: List[str] = []

        payout_check = self._check_payout(payout)
        checks.append(payout_check)
        if payout_check["blocked"]:
            block_reasons.append(payout_check["reason"])

        session_check = self._check_session(asset=asset)
        checks.append(session_check)
        if session_check["blocked"]:
            block_reasons.append(session_check["reason"])

        news_check = self._check_news(asset)
        checks.append(news_check)
        if news_check["blocked"]:
            block_reasons.append(news_check["reason"])
        elif news_check.get("caution"):
            caution_reasons.append(news_check["reason"])

        regime_check = self._check_regime(candles)
        checks.append(regime_check)
        if regime_check["blocked"]:
            block_reasons.append(regime_check["reason"])

        spike_check = self._check_spike(candles)
        checks.append(spike_check)
        if spike_check["blocked"]:
            block_reasons.append(spike_check["reason"])

        latency_check = self._check_latency(broker_latency_ms)
        checks.append(latency_check)
        if latency_check["blocked"]:
            block_reasons.append(latency_check["reason"])

        cooldown_check = self._check_cooldown()
        checks.append(cooldown_check)
        if cooldown_check["blocked"]:
            block_reasons.append(cooldown_check["reason"])

        rate_check = self._check_rate_limit()
        checks.append(rate_check)
        if rate_check["blocked"]:
            block_reasons.append(rate_check["reason"])

        dd_check = self._check_drawdown(current_drawdown_pct)
        checks.append(dd_check)
        if dd_check["blocked"]:
            block_reasons.append(dd_check["reason"])

        health_check = self._check_broker_health(broker_healthy)
        checks.append(health_check)
        if health_check["blocked"]:
            block_reasons.append(health_check["reason"])

        for custom_fn in self._custom_checks:
            try:
                result = custom_fn(asset=asset, payout=payout, candles=candles)
                checks.append(result)
                if result.get("blocked"):
                    block_reasons.append(result.get("reason", "Custom check failed"))
            except Exception as exc:
                logger.error("Custom check error: %s", exc)
                block_reasons.append(f"Custom check error: {exc}")

        if block_reasons:
            decision = ContextDecision.BLOCK
        elif caution_reasons:
            decision = ContextDecision.CAUTION
        else:
            decision = ContextDecision.ALLOW

        logger.info(
            "Context gate: %s (blocks=%d, cautions=%d)",
            decision.value,
            len(block_reasons),
            len(caution_reasons),
        )

        return {
            "decision": decision.value,
            "blocked": decision == ContextDecision.BLOCK,
            "block_reasons": block_reasons,
            "caution_reasons": caution_reasons,
            "checks": checks,
            "timestamp": time.time(),
        }

    def _check_payout(self, payout: float) -> Dict[str, Any]:
        blocked = payout < self._min_payout
        return {
            "name": "payout",
            "blocked": blocked,
            "reason": f"Payout {payout:.0%} below minimum {self._min_payout:.0%}" if blocked else "OK",
            "value": payout,
            "threshold": self._min_payout,
        }

    def _check_session(self, asset: str = "") -> Dict[str, Any]:
        if asset.endswith("-OTC"):
            return {
                "name": "session",
                "blocked": False,
                "reason": "OTC asset — always available",
            }
        result = self._session_filter.evaluate()
        return {
            "name": "session",
            "blocked": not result["valid"],
            "reason": result["reason"],
            "session": result.get("session", ""),
        }

    def _check_news(self, asset: str) -> Dict[str, Any]:
        result = self._news_filter.evaluate(asset=asset)
        return {
            "name": "news",
            "blocked": result["blocked"],
            "caution": result.get("caution", False),
            "reason": result["reason"],
            "nearby_events": result.get("nearby_events", []),
        }

    def _check_regime(self, candles: List[Candle]) -> Dict[str, Any]:
        regime = self._regime_detector.detect(candles)
        blocked = regime in self._blocked_regimes
        return {
            "name": "regime",
            "blocked": blocked,
            "reason": f"Market regime {regime.value} is blocked" if blocked else f"Regime: {regime.value}",
            "regime": regime.value,
        }

    def _check_spike(self, candles: List[Candle]) -> Dict[str, Any]:
        result = self._spike_detector.detect(candles)
        return {
            "name": "spike",
            "blocked": result.spike_detected and result.severity == "severe",
            "reason": "; ".join(result.reasons) if result.spike_detected else "No spike",
            "severity": result.severity,
            "details": result.details,
        }

    def _check_latency(self, latency_ms: float) -> Dict[str, Any]:
        blocked = latency_ms > self._max_latency_ms
        return {
            "name": "latency",
            "blocked": blocked,
            "reason": f"Latency {latency_ms:.0f}ms exceeds {self._max_latency_ms:.0f}ms" if blocked else "OK",
            "value_ms": latency_ms,
            "threshold_ms": self._max_latency_ms,
        }

    def _check_cooldown(self) -> Dict[str, Any]:
        if self._last_trade_time == 0:
            return {"name": "cooldown", "blocked": False, "reason": "No previous trade"}
        elapsed = time.time() - self._last_trade_time
        blocked = elapsed < self._cooldown_seconds
        remaining = max(0, self._cooldown_seconds - elapsed)
        return {
            "name": "cooldown",
            "blocked": blocked,
            "reason": f"Cooldown active ({remaining:.0f}s remaining)" if blocked else "OK",
            "elapsed": round(elapsed, 1),
            "cooldown": self._cooldown_seconds,
        }

    def _check_rate_limit(self) -> Dict[str, Any]:
        now = time.time()
        if now - self._hour_start > 3600:
            self._trades_this_hour = 0
            self._hour_start = now
        blocked = self._trades_this_hour >= self._max_trades_per_hour
        return {
            "name": "rate_limit",
            "blocked": blocked,
            "reason": f"Trade limit reached ({self._trades_this_hour}/{self._max_trades_per_hour}/hr)" if blocked else "OK",
            "trades_this_hour": self._trades_this_hour,
            "max_per_hour": self._max_trades_per_hour,
        }

    def _check_drawdown(self, current_pct: float) -> Dict[str, Any]:
        blocked = current_pct >= self._max_drawdown_pct
        return {
            "name": "drawdown",
            "blocked": blocked,
            "reason": f"Drawdown {current_pct:.1f}% exceeds max {self._max_drawdown_pct:.1f}%" if blocked else "OK",
            "current_pct": current_pct,
            "max_pct": self._max_drawdown_pct,
        }

    def _check_broker_health(self, healthy: bool) -> Dict[str, Any]:
        return {
            "name": "broker_health",
            "blocked": not healthy,
            "reason": "Broker unhealthy" if not healthy else "OK",
        }
