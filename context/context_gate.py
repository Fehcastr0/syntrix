"""
context/context_gate.py — Central context gate for Syntrix v2.

The ContextGate is the main decision filter.
Default state is BLOCK but uses a confidence model instead of binary blocking.

HARD BLOCKS: broker offline, websocket dead, critical drawdown, safe mode
SOFT BLOCKS: reduce confidence instead of blocking immediately

Each filter returns a confidence modifier (0.0-1.0).
Final confidence = product of all modifiers.
If confidence >= adaptive threshold -> ALLOW.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

from brokers.base import Candle
from context.decision_trace import (
    BlockReason,
    BlockRecord,
    BlockSeverity,
    DecisionTrace,
    HARD_BLOCK_REASONS,
    TraceStage,
)
from context.market_regime import MarketRegime, MarketRegimeDetector
from context.news_filter import NewsFilter
from context.otc_analyzer import OTCBehaviorAnalyzer
from context.session_filter import SessionFilter
from context.spike_detector import SpikeDetector

logger = logging.getLogger("syntrix.context.gate")


class ContextDecision:
    """Backward-compatible decision enum."""
    ALLOW = "allow"
    BLOCK = "block"
    CAUTION = "caution"


class ContextGate:
    """
    Central context gate — weighted confidence model.

    Instead of binary ALLOW/BLOCK, each check returns a confidence modifier.
    Hard blocks still immediately block. Soft blocks reduce confidence.
    """

    def __init__(
        self,
        session_filter: SessionFilter,
        news_filter: NewsFilter,
        regime_detector: MarketRegimeDetector,
        spike_detector: SpikeDetector,
        otc_analyzer: Optional[OTCBehaviorAnalyzer] = None,
        min_payout: float = 0.70,
        max_latency_ms: float = 500.0,
        max_trades_per_hour: int = 10,
        max_drawdown_pct: float = 10.0,
        cooldown_seconds: float = 120.0,
        blocked_regimes: Optional[List[MarketRegime]] = None,
        base_threshold: float = 0.50,
    ) -> None:
        self._session_filter = session_filter
        self._news_filter = news_filter
        self._regime_detector = regime_detector
        self._spike_detector = spike_detector
        self._otc_analyzer = otc_analyzer or OTCBehaviorAnalyzer()
        self._min_payout = min_payout
        self._max_latency_ms = max_latency_ms
        self._max_trades_per_hour = max_trades_per_hour
        self._max_drawdown_pct = max_drawdown_pct
        self._cooldown_seconds = cooldown_seconds
        self._blocked_regimes = blocked_regimes or [MarketRegime.UNSTABLE]
        self._base_threshold = base_threshold
        self._last_trade_time: float = 0.0
        self._last_trade_times: Dict[str, float] = {}  # per-asset cooldown
        self._trades_this_hour: int = 0
        self._hour_start: float = time.time()
        self._custom_checks: List[Callable[..., Dict[str, Any]]] = []

    def add_custom_check(self, check_fn: Callable[..., Dict[str, Any]]) -> None:
        """Register a custom context check function."""
        self._custom_checks.append(check_fn)

    def record_trade(self, asset: str = "") -> None:
        """Record that a trade was executed (updates rate limits)."""
        now = time.time()
        if now - self._hour_start > 3600:
            self._trades_this_hour = 0
            self._hour_start = now
        self._trades_this_hour += 1
        self._last_trade_time = now
        if asset:
            self._last_trade_times[asset] = now

    def get_adaptive_threshold(
        self, asset: str = "", regime: str = "", hour_utc: int = -1
    ) -> float:
        """
        Calculate adaptive threshold based on context.

        Better conditions -> lower threshold (more trades).
        Worse conditions -> higher threshold (fewer trades).
        """
        threshold = self._base_threshold

        # OTC assets get slightly lower threshold (they're always available)
        if asset.endswith("-OTC"):
            threshold -= 0.05

        # Overlap hours (13-16 UTC) get lower threshold
        if 13 <= hour_utc <= 16:
            threshold -= 0.05
        # Dead zone (22-06 UTC) for non-OTC gets higher threshold
        elif (hour_utc >= 22 or hour_utc < 6) and not asset.endswith("-OTC"):
            threshold += 0.10

        # Favorable regimes get lower threshold
        if regime in ("trend", "range"):
            threshold -= 0.03
        elif regime in ("unstable", "high_volatility"):
            threshold += 0.05

        return max(0.30, min(0.80, round(threshold, 3)))

    def evaluate(
        self,
        asset: str,
        payout: float,
        candles: List[Candle],
        broker_latency_ms: float = 0.0,
        current_drawdown_pct: float = 0.0,
        broker_healthy: bool = True,
        correlation_id: str = "",
    ) -> Dict[str, Any]:
        """
        Run all context checks with weighted confidence model.

        Returns dict compatible with previous API plus new trace data.
        """
        trace = DecisionTrace(
            asset=asset,
            correlation_id=correlation_id,
            timestamp=time.time(),
        )

        start_time = time.time()
        confidence = 1.0
        block_reasons: List[str] = []
        caution_reasons: List[str] = []
        checks: List[Dict[str, Any]] = []
        hard_blocked = False

        # ── 1. Broker Health (HARD BLOCK) ──
        t0 = time.time()
        health_check = self._check_broker_health(broker_healthy)
        checks.append(health_check)
        stage = TraceStage(name="BROKER", timestamp=t0, duration_ms=(time.time()-t0)*1000,
                           decision="PASS" if not health_check["blocked"] else "BLOCK",
                           reason=health_check["reason"])
        trace.add_stage(stage)
        if health_check["blocked"]:
            hard_blocked = True
            block_reasons.append(health_check["reason"])
            trace.add_block(BlockRecord(
                reason_code=BlockReason.BROKER_UNHEALTHY,
                severity=BlockSeverity.HARD,
                details=health_check["reason"], module="broker",
                correlation_id=correlation_id,
            ))

        # ── 2. Drawdown (HARD BLOCK) ──
        t0 = time.time()
        dd_check = self._check_drawdown(current_drawdown_pct)
        checks.append(dd_check)
        stage = TraceStage(name="DRAWDOWN", timestamp=t0, duration_ms=(time.time()-t0)*1000,
                           decision="PASS" if not dd_check["blocked"] else "BLOCK",
                           score=current_drawdown_pct, threshold=self._max_drawdown_pct,
                           reason=dd_check["reason"])
        trace.add_stage(stage)
        if dd_check["blocked"]:
            hard_blocked = True
            block_reasons.append(dd_check["reason"])
            trace.add_block(BlockRecord(
                reason_code=BlockReason.DRAWDOWN_LIMIT,
                severity=BlockSeverity.HARD,
                details=dd_check["reason"], module="risk",
                correlation_id=correlation_id,
            ))

        # ── 3. Rate Limit (HARD BLOCK) ──
        t0 = time.time()
        rate_check = self._check_rate_limit()
        checks.append(rate_check)
        stage = TraceStage(name="RATE", timestamp=t0, duration_ms=(time.time()-t0)*1000,
                           decision="PASS" if not rate_check["blocked"] else "BLOCK",
                           reason=rate_check["reason"])
        trace.add_stage(stage)
        if rate_check["blocked"]:
            hard_blocked = True
            block_reasons.append(rate_check["reason"])
            trace.add_block(BlockRecord(
                reason_code=BlockReason.MAX_TRADES,
                severity=BlockSeverity.HARD,
                details=rate_check["reason"], module="rate",
                correlation_id=correlation_id,
            ))

        # Short-circuit on hard blocks
        if hard_blocked:
            trace.final_decision = "BLOCK"
            trace.final_confidence = 0.0
            trace.total_duration_ms = (time.time() - start_time) * 1000
            logger.info(
                "Context gate: BLOCK [HARD] (%s) — %s",
                asset, ", ".join(block_reasons),
            )
            return self._build_result(trace, checks, block_reasons, caution_reasons)

        # ── 4. Payout (SOFT BLOCK) ──
        t0 = time.time()
        payout_check = self._check_payout(payout)
        checks.append(payout_check)
        payout_mod = payout_check.get("modifier", 1.0)
        confidence *= payout_mod
        stage = TraceStage(name="PAYOUT", timestamp=t0, duration_ms=(time.time()-t0)*1000,
                           decision="PASS" if not payout_check["blocked"] else "SOFT",
                           score=payout, threshold=self._min_payout,
                           reason=payout_check["reason"])
        trace.add_stage(stage)
        if payout_check["blocked"]:
            caution_reasons.append(payout_check["reason"])
            trace.add_block(BlockRecord(
                reason_code=BlockReason.LOW_PAYOUT,
                severity=BlockSeverity.SOFT,
                details=payout_check["reason"], module="payout",
                modifier=payout_mod, correlation_id=correlation_id,
            ))

        # ── 5. Session (SOFT BLOCK) ──
        t0 = time.time()
        session_check = self._check_session(asset=asset)
        checks.append(session_check)
        session_mod = session_check.get("modifier", 1.0)
        confidence *= session_mod
        stage = TraceStage(name="SESSION", timestamp=t0, duration_ms=(time.time()-t0)*1000,
                           decision="PASS" if not session_check["blocked"] else "SOFT",
                           reason=session_check["reason"])
        trace.add_stage(stage)
        if session_check["blocked"]:
            caution_reasons.append(session_check["reason"])
            trace.add_block(BlockRecord(
                reason_code=BlockReason.SESSION_BLOCKED,
                severity=BlockSeverity.SOFT,
                details=session_check["reason"], module="session",
                modifier=session_mod, correlation_id=correlation_id,
            ))

        # ── 6. News (SOFT BLOCK) ──
        t0 = time.time()
        news_check = self._check_news(asset)
        checks.append(news_check)
        news_mod = news_check.get("modifier", 1.0)
        confidence *= news_mod
        stage = TraceStage(name="NEWS", timestamp=t0, duration_ms=(time.time()-t0)*1000,
                           decision="PASS" if not news_check["blocked"] else "SOFT",
                           reason=news_check["reason"])
        trace.add_stage(stage)
        if news_check["blocked"] or news_check.get("caution"):
            caution_reasons.append(news_check["reason"])
            trace.add_block(BlockRecord(
                reason_code=BlockReason.NEWS_BLOCKED,
                severity=BlockSeverity.SOFT,
                details=news_check["reason"], module="news",
                modifier=news_mod, correlation_id=correlation_id,
            ))

        # ── 7. Regime (SOFT BLOCK) ──
        t0 = time.time()
        regime_check = self._check_regime(candles)
        checks.append(regime_check)
        regime_mod = regime_check.get("modifier", 1.0)
        confidence *= regime_mod
        stage = TraceStage(name="REGIME", timestamp=t0, duration_ms=(time.time()-t0)*1000,
                           decision="PASS" if not regime_check["blocked"] else "SOFT",
                           reason=regime_check["reason"],
                           details={"regime": regime_check.get("regime", "")})
        trace.add_stage(stage)
        if regime_check["blocked"]:
            caution_reasons.append(regime_check["reason"])
            trace.add_block(BlockRecord(
                reason_code=BlockReason.REGIME_UNSTABLE,
                severity=BlockSeverity.SOFT,
                details=regime_check["reason"], module="regime",
                modifier=regime_mod, correlation_id=correlation_id,
            ))

        # ── 8. Spike Detection (SOFT BLOCK) ──
        t0 = time.time()
        spike_check = self._check_spike(candles)
        checks.append(spike_check)
        spike_mod = spike_check.get("modifier", 1.0)
        confidence *= spike_mod
        stage = TraceStage(name="SPIKE", timestamp=t0, duration_ms=(time.time()-t0)*1000,
                           decision="PASS" if not spike_check["blocked"] else "SOFT",
                           reason=spike_check["reason"])
        trace.add_stage(stage)
        if spike_check["blocked"]:
            caution_reasons.append(spike_check["reason"])
            trace.add_block(BlockRecord(
                reason_code=BlockReason.SPIKE_DETECTED,
                severity=BlockSeverity.SOFT,
                details=spike_check["reason"], module="spike",
                modifier=spike_mod, correlation_id=correlation_id,
            ))

        # ── 9. Latency (SOFT BLOCK) ──
        t0 = time.time()
        latency_check = self._check_latency(broker_latency_ms)
        checks.append(latency_check)
        lat_mod = latency_check.get("modifier", 1.0)
        confidence *= lat_mod
        stage = TraceStage(name="LATENCY", timestamp=t0, duration_ms=(time.time()-t0)*1000,
                           decision="PASS" if not latency_check["blocked"] else "SOFT",
                           score=broker_latency_ms, threshold=self._max_latency_ms,
                           reason=latency_check["reason"])
        trace.add_stage(stage)
        if latency_check["blocked"]:
            caution_reasons.append(latency_check["reason"])
            trace.add_block(BlockRecord(
                reason_code=BlockReason.LATENCY_HIGH,
                severity=BlockSeverity.SOFT,
                details=latency_check["reason"], module="latency",
                modifier=lat_mod, correlation_id=correlation_id,
            ))

        # ── 10. Cooldown (SOFT BLOCK — per-asset) ──
        t0 = time.time()
        cooldown_check = self._check_cooldown(asset)
        checks.append(cooldown_check)
        cd_mod = cooldown_check.get("modifier", 1.0)
        confidence *= cd_mod
        stage = TraceStage(name="COOLDOWN", timestamp=t0, duration_ms=(time.time()-t0)*1000,
                           decision="PASS" if not cooldown_check["blocked"] else "SOFT",
                           reason=cooldown_check["reason"])
        trace.add_stage(stage)
        if cooldown_check["blocked"]:
            caution_reasons.append(cooldown_check["reason"])
            trace.add_block(BlockRecord(
                reason_code=BlockReason.COOLDOWN_ACTIVE,
                severity=BlockSeverity.SOFT,
                details=cooldown_check["reason"], module="cooldown",
                modifier=cd_mod, correlation_id=correlation_id,
            ))

        # ── 11. OTC Analysis (SOFT BLOCK) ──
        if asset.endswith("-OTC"):
            t0 = time.time()
            otc_result = self._otc_analyzer.analyze(candles, asset)
            otc_mod = otc_result.modifier
            confidence *= otc_mod
            otc_decision = "PASS" if otc_mod >= 0.9 else "SOFT"
            stage = TraceStage(name="OTC", timestamp=t0, duration_ms=(time.time()-t0)*1000,
                               decision=otc_decision, score=otc_result.quality_score,
                               reason="; ".join(otc_result.issues) if otc_result.issues else "Clean OTC")
            trace.add_stage(stage)
            if otc_result.issues:
                caution_reasons.append(f"OTC quality: {otc_result.quality_score:.0%}")
                trace.add_block(BlockRecord(
                    reason_code=BlockReason.OTC_TOXIC,
                    severity=BlockSeverity.SOFT,
                    details="; ".join(otc_result.issues), module="otc",
                    modifier=otc_mod, correlation_id=correlation_id,
                ))
            checks.append({
                "name": "otc_quality",
                "blocked": otc_mod < 0.5,
                "modifier": otc_mod,
                "reason": "; ".join(otc_result.issues) if otc_result.issues else "OK",
                "quality_score": otc_result.quality_score,
            })

        # ── Custom checks ──
        for custom_fn in self._custom_checks:
            try:
                result = custom_fn(asset=asset, payout=payout, candles=candles)
                checks.append(result)
                custom_mod = result.get("modifier", 1.0 if not result.get("blocked") else 0.5)
                confidence *= custom_mod
            except Exception as exc:
                logger.error("Custom check error: %s", exc)

        # ── Adaptive threshold ──
        regime_val = regime_check.get("regime", "")
        import datetime
        hour_utc = datetime.datetime.now(datetime.timezone.utc).hour
        threshold = self.get_adaptive_threshold(asset, regime_val, hour_utc)

        confidence = round(max(0.0, min(1.0, confidence)), 3)
        trace.final_confidence = confidence

        # Decision — be more permissive to increase healthy operation frequency
        if confidence >= threshold:
            decision = "allow"
            trace.final_decision = "ALLOW"
        elif confidence >= threshold * 0.80:
            decision = "caution"
            trace.final_decision = "ALLOW"  # CAUTION still allows
        else:
            decision = "block"
            trace.final_decision = "BLOCK"
            block_reasons = [r for r in caution_reasons]

        trace.total_duration_ms = (time.time() - start_time) * 1000

        logger.info(
            "Context gate: %s (confidence=%.2f, threshold=%.2f, soft=%d) — %s",
            decision.upper(), confidence, threshold,
            len(trace.soft_blocks), asset,
        )

        if caution_reasons:
            logger.info("[%s] Issues: %s", asset, " | ".join(caution_reasons[:5]))

        return self._build_result(trace, checks, block_reasons, caution_reasons, confidence, threshold)

    def _build_result(
        self,
        trace: DecisionTrace,
        checks: List[Dict[str, Any]],
        block_reasons: List[str],
        caution_reasons: List[str],
        confidence: float = 0.0,
        threshold: float = 0.50,
    ) -> Dict[str, Any]:
        """Build result dict compatible with existing API."""
        return {
            "decision": trace.final_decision.lower(),
            "blocked": trace.final_decision == "BLOCK",
            "block_reasons": block_reasons,
            "caution_reasons": caution_reasons,
            "checks": checks,
            "timestamp": trace.timestamp,
            "confidence": confidence,
            "threshold": threshold,
            "trace": trace,
        }

    # ── Individual Checks (now return modifier) ──

    def _check_payout(self, payout: float) -> Dict[str, Any]:
        if payout >= self._min_payout:
            modifier = 1.0
        elif payout >= self._min_payout * 0.85:
            modifier = 0.95
        else:
            modifier = 0.85
        blocked = payout < self._min_payout * 0.60
        return {
            "name": "payout",
            "blocked": blocked,
            "modifier": modifier,
            "reason": f"Payout {payout:.0%} (min={self._min_payout:.0%}, mod={modifier:.2f})",
            "value": payout,
            "threshold": self._min_payout,
        }

    def _check_session(self, asset: str = "") -> Dict[str, Any]:
        if asset.endswith("-OTC"):
            return {
                "name": "session",
                "blocked": False,
                "modifier": 1.0,
                "reason": "OTC — always available",
            }
        result = self._session_filter.evaluate()
        if result["valid"]:
            modifier = 1.0
        else:
            modifier = 0.85
        return {
            "name": "session",
            "blocked": not result["valid"],
            "modifier": modifier,
            "reason": result["reason"],
            "session": result.get("session", ""),
        }

    def _check_news(self, asset: str) -> Dict[str, Any]:
        result = self._news_filter.evaluate(asset=asset)
        if result["blocked"]:
            modifier = 0.70
        elif result.get("caution"):
            modifier = 0.90
        else:
            modifier = 1.0
        return {
            "name": "news",
            "blocked": result["blocked"],
            "caution": result.get("caution", False),
            "modifier": modifier,
            "reason": result["reason"],
        }

    def _check_regime(self, candles: List[Candle]) -> Dict[str, Any]:
        regime = self._regime_detector.detect(candles)
        if regime in self._blocked_regimes:
            modifier = 0.65
        elif regime == MarketRegime.HIGH_VOLATILITY:
            modifier = 0.90
        else:
            modifier = 1.0
        return {
            "name": "regime",
            "blocked": regime in self._blocked_regimes,
            "modifier": modifier,
            "reason": f"Regime: {regime.value} (mod={modifier:.2f})",
            "regime": regime.value,
        }

    def _check_spike(self, candles: List[Candle]) -> Dict[str, Any]:
        result = self._spike_detector.detect(candles)
        if result.spike_detected:
            if result.severity == "severe":
                modifier = 0.60
            elif result.severity == "moderate":
                modifier = 0.85
            else:
                modifier = 0.95
        else:
            modifier = 1.0
        return {
            "name": "spike",
            "blocked": result.spike_detected and result.severity == "severe",
            "modifier": modifier,
            "reason": "; ".join(result.reasons) if result.spike_detected else "No spike",
            "severity": result.severity,
        }

    def _check_latency(self, latency_ms: float) -> Dict[str, Any]:
        if latency_ms <= self._max_latency_ms * 0.5:
            modifier = 1.0
        elif latency_ms <= self._max_latency_ms:
            modifier = 0.95
        elif latency_ms <= self._max_latency_ms * 1.5:
            modifier = 0.85
        else:
            modifier = 0.70
        blocked = latency_ms > self._max_latency_ms * 2
        return {
            "name": "latency",
            "blocked": blocked,
            "modifier": modifier,
            "reason": f"Latency {latency_ms:.0f}ms (max={self._max_latency_ms:.0f}ms, mod={modifier:.2f})",
            "value_ms": latency_ms,
            "threshold_ms": self._max_latency_ms,
        }

    def _check_cooldown(self, asset: str = "") -> Dict[str, Any]:
        # Per-asset cooldown
        last_time = self._last_trade_times.get(asset, self._last_trade_time)
        if last_time == 0:
            return {"name": "cooldown", "blocked": False, "modifier": 1.0,
                    "reason": "No previous trade"}
        elapsed = time.time() - last_time
        if elapsed >= self._cooldown_seconds:
            modifier = 1.0
        elif elapsed >= self._cooldown_seconds * 0.5:
            modifier = 0.95
        else:
            modifier = 0.80
        blocked = elapsed < self._cooldown_seconds * 0.2
        remaining = max(0, self._cooldown_seconds - elapsed)
        return {
            "name": "cooldown",
            "blocked": blocked,
            "modifier": modifier,
            "reason": f"Cooldown: {remaining:.0f}s remaining (mod={modifier:.2f})" if remaining > 0 else "OK",
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
            "modifier": 1.0 if not blocked else 0.0,
            "reason": f"Trades: {self._trades_this_hour}/{self._max_trades_per_hour}/hr",
            "trades_this_hour": self._trades_this_hour,
            "max_per_hour": self._max_trades_per_hour,
        }

    def _check_drawdown(self, current_pct: float) -> Dict[str, Any]:
        blocked = current_pct >= self._max_drawdown_pct
        return {
            "name": "drawdown",
            "blocked": blocked,
            "modifier": 1.0 if not blocked else 0.0,
            "reason": f"Drawdown {current_pct:.1f}% (max={self._max_drawdown_pct:.1f}%)",
            "current_pct": current_pct,
            "max_pct": self._max_drawdown_pct,
        }

    def _check_broker_health(self, healthy: bool) -> Dict[str, Any]:
        return {
            "name": "broker_health",
            "blocked": not healthy,
            "modifier": 1.0 if healthy else 0.0,
            "reason": "OK" if healthy else "Broker unhealthy",
        }
