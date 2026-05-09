"""Tests for context/context_gate.py — ContextGate."""

import time
import unittest
from datetime import datetime, timezone

from brokers.base import Candle
from context.context_gate import ContextDecision, ContextGate
from context.market_regime import MarketRegime, MarketRegimeDetector
from context.news_filter import NewsFilter
from context.session_filter import SessionFilter
from context.spike_detector import SpikeDetector


def make_candles(count: int = 30, base: float = 1.1000, step: float = 0.0001) -> list[Candle]:
    """Generate synthetic candles for testing."""
    candles = []
    for i in range(count):
        close = base + step * i
        candles.append(Candle(
            timestamp=time.time() - (count - i) * 60,
            open=close - 0.0002,
            high=close + 0.0003,
            low=close - 0.0004,
            close=close,
            volume=100,
        ))
    return candles


class TestContextGate(unittest.TestCase):
    def setUp(self):
        self.session_filter = SessionFilter()
        self.news_filter = NewsFilter()
        self.regime_detector = MarketRegimeDetector()
        self.spike_detector = SpikeDetector()
        self.gate = ContextGate(
            session_filter=self.session_filter,
            news_filter=self.news_filter,
            regime_detector=self.regime_detector,
            spike_detector=self.spike_detector,
            min_payout=0.70,
            max_latency_ms=500,
            max_trades_per_hour=10,
            cooldown_seconds=0,  # disable for testing
        )

    def test_low_payout_blocks(self):
        candles = make_candles()
        result = self.gate.evaluate(
            asset="EURUSD", payout=0.50, candles=candles,
        )
        self.assertTrue(result["blocked"])
        self.assertTrue(any("payout" in r.lower() for r in result["block_reasons"]))

    def test_high_latency_blocks(self):
        candles = make_candles()
        result = self.gate.evaluate(
            asset="EURUSD", payout=0.80, candles=candles,
            broker_latency_ms=1000,
        )
        self.assertTrue(result["blocked"])
        self.assertTrue(any("latency" in r.lower() for r in result["block_reasons"]))

    def test_high_drawdown_blocks(self):
        candles = make_candles()
        result = self.gate.evaluate(
            asset="EURUSD", payout=0.80, candles=candles,
            current_drawdown_pct=15.0,
        )
        self.assertTrue(result["blocked"])
        self.assertTrue(any("drawdown" in r.lower() for r in result["block_reasons"]))

    def test_unhealthy_broker_blocks(self):
        candles = make_candles()
        result = self.gate.evaluate(
            asset="EURUSD", payout=0.80, candles=candles,
            broker_healthy=False,
        )
        self.assertTrue(result["blocked"])

    def test_result_has_checks(self):
        candles = make_candles()
        result = self.gate.evaluate(
            asset="EURUSD", payout=0.80, candles=candles,
        )
        self.assertIn("checks", result)
        self.assertGreater(len(result["checks"]), 5)

    def test_record_trade_updates_rate(self):
        self.gate.record_trade()
        self.gate._last_trade_time = time.time() - 1000  # reset cooldown
        candles = make_candles()
        result = self.gate.evaluate(asset="EURUSD", payout=0.80, candles=candles)
        rate_check = [c for c in result["checks"] if c["name"] == "rate_limit"][0]
        self.assertEqual(rate_check["trades_this_hour"], 1)


class TestSpikeDetector(unittest.TestCase):
    def test_no_spike_normal_candles(self):
        candles = make_candles(30)
        detector = SpikeDetector()
        result = detector.detect(candles)
        self.assertFalse(result.spike_detected)

    def test_spike_large_candle(self):
        candles = make_candles(30)
        # Add anomalous candle
        candles.append(Candle(
            timestamp=time.time(),
            open=1.1030,
            high=1.1100,
            low=1.0900,
            close=1.1080,
            volume=500,
        ))
        detector = SpikeDetector(body_mult_threshold=2.0)
        result = detector.detect(candles)
        self.assertTrue(result.spike_detected)

    def test_corrupted_data(self):
        candles = make_candles(15)
        candles.append(Candle(
            timestamp=time.time(),
            open=0, high=0, low=0, close=0,
        ))
        detector = SpikeDetector()
        result = detector.detect(candles)
        self.assertTrue(result.spike_detected)


class TestMarketRegimeDetector(unittest.TestCase):
    def test_unknown_insufficient_data(self):
        detector = MarketRegimeDetector(min_candles=20)
        candles = make_candles(5)
        regime = detector.detect(candles)
        self.assertEqual(regime, MarketRegime.UNKNOWN)

    def test_detection_returns_valid_regime(self):
        detector = MarketRegimeDetector(min_candles=20)
        candles = make_candles(30)
        regime = detector.detect(candles)
        self.assertIn(regime, list(MarketRegime))


if __name__ == "__main__":
    unittest.main()
