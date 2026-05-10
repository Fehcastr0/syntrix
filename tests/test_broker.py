"""Tests for brokers/base.py — BaseBroker interface."""

import unittest
from typing import List

from brokers.base import (
    BaseBroker,
    BrokerStatus,
    Candle,
    HealthStatus,
    TradeDirection,
    TradeRequest,
    TradeResult,
)


class MockBroker(BaseBroker):
    """Mock broker for testing the base interface."""

    def __init__(self):
        super().__init__(name="mock")
        self._balance = 1000.0
        self._connected = False

    def connect(self) -> bool:
        self._connected = True
        self._status = BrokerStatus.CONNECTED
        return True

    def disconnect(self) -> None:
        self._connected = False
        self._status = BrokerStatus.DISCONNECTED

    def reconnect(self) -> bool:
        self.disconnect()
        return self.connect()

    def get_balance(self) -> float:
        return self._balance

    def get_candles(self, asset: str, timeframe: int, count: int) -> List[Candle]:
        return [
            Candle(timestamp=i, open=1.0, high=1.1, low=0.9, close=1.05)
            for i in range(count)
        ]

    def get_payout(self, asset: str) -> float:
        return 0.80

    def is_asset_open(self, asset: str) -> bool:
        return True

    def execute_trade(self, request: TradeRequest) -> TradeResult:
        return TradeResult(
            trade_id=request.trade_id,
            success=True,
            result="pending",
            broker_id="mock-123",
            latency_ms=50.0,
        )

    def check_trade_result(self, broker_id: str) -> TradeResult:
        return TradeResult(
            trade_id="",
            success=True,
            result="win",
            profit=8.0,
            broker_id=broker_id,
        )


class TestBaseBroker(unittest.TestCase):
    def setUp(self):
        self.broker = MockBroker()

    def test_initial_status(self):
        self.assertEqual(self.broker.status, BrokerStatus.DISCONNECTED)
        self.assertFalse(self.broker.is_connected)

    def test_connect(self):
        self.assertTrue(self.broker.connect())
        self.assertTrue(self.broker.is_connected)

    def test_disconnect(self):
        self.broker.connect()
        self.broker.disconnect()
        self.assertFalse(self.broker.is_connected)

    def test_get_balance(self):
        self.assertEqual(self.broker.get_balance(), 1000.0)

    def test_get_candles(self):
        candles = self.broker.get_candles("EURUSD", 60, 5)
        self.assertEqual(len(candles), 5)
        self.assertIsInstance(candles[0], Candle)

    def test_get_payout(self):
        self.assertEqual(self.broker.get_payout("EURUSD"), 0.80)

    def test_execute_trade(self):
        request = TradeRequest(
            asset="EURUSD",
            direction=TradeDirection.CALL,
            amount=10.0,
            duration=60,
            trade_id="test-1",
        )
        result = self.broker.execute_trade(request)
        self.assertTrue(result.success)
        self.assertEqual(result.broker_id, "mock-123")

    def test_check_result(self):
        result = self.broker.check_trade_result("mock-123")
        self.assertEqual(result.result, "win")
        self.assertEqual(result.profit, 8.0)

    def test_health_check(self):
        health = self.broker.health_check()
        self.assertIsInstance(health, HealthStatus)
        self.assertFalse(health.connected)

    def test_candle_properties(self):
        c = Candle(timestamp=0, open=1.0, high=1.1, low=0.9, close=1.05)
        self.assertTrue(c.is_bullish)
        self.assertFalse(c.is_bearish)
        self.assertAlmostEqual(c.body_size, 0.05)
        self.assertAlmostEqual(c.upper_wick, 0.05)
        self.assertAlmostEqual(c.lower_wick, 0.1)
        self.assertAlmostEqual(c.range, 0.2)

    def test_latency_tracking(self):
        self.broker._record_latency(50.0)
        self.broker._record_latency(100.0)
        self.assertAlmostEqual(self.broker.avg_latency_ms, 75.0)


if __name__ == "__main__":
    unittest.main()
