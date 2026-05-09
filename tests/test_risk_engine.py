"""Tests for risk/risk_engine.py — RiskEngine."""

import unittest
from unittest.mock import MagicMock

from core.events import EventBus
from risk.risk_engine import RiskConfig, RiskEngine


class TestRiskEngine(unittest.TestCase):
    def setUp(self):
        self.bus = EventBus()
        self.bus.start()
        self.config = RiskConfig(
            stop_gain=50.0,
            stop_loss=-30.0,
            max_drawdown_pct=10.0,
            max_trades_per_hour=5,
            max_consecutive_losses=3,
            cooldown_after_loss_sec=0.1,
            cooldown_after_trade_sec=0.1,
            min_payout_threshold=0.70,
            session_end_hour_utc=23,
            enable_safe_mode=True,
            safe_mode_after_losses=3,
        )
        self.engine = RiskEngine(self.bus, self.config)
        self.engine.set_starting_balance(1000.0)

    def tearDown(self):
        self.bus.stop()

    def test_initial_state(self):
        self.assertFalse(self.engine.is_locked)
        self.assertFalse(self.engine.is_safe_mode)

    def test_trade_allowed_initially(self):
        result = self.engine.check_trade_allowed(payout=0.80)
        self.assertTrue(result["allowed"])

    def test_stop_gain(self):
        self.engine._state.pnl = 55.0
        result = self.engine.check_trade_allowed()
        self.assertFalse(result["allowed"])
        self.assertTrue(any("Stop gain" in r for r in result["reasons"]))

    def test_stop_loss(self):
        self.engine._state.pnl = -35.0
        result = self.engine.check_trade_allowed()
        self.assertFalse(result["allowed"])
        self.assertTrue(any("Stop loss" in r for r in result["reasons"]))

    def test_consecutive_losses(self):
        for _ in range(3):
            self.engine.record_trade_result(-5.0)
        import time; time.sleep(0.2)
        result = self.engine.check_trade_allowed()
        self.assertFalse(result["allowed"])

    def test_win_resets_consecutive(self):
        self.engine.record_trade_result(-5.0)
        self.engine.record_trade_result(-5.0)
        self.engine.record_trade_result(10.0)
        self.assertEqual(self.engine.state.consecutive_losses, 0)

    def test_safe_mode_activation(self):
        for _ in range(3):
            self.engine.record_trade_result(-5.0)
        self.assertTrue(self.engine.is_safe_mode)

    def test_exit_safe_mode(self):
        for _ in range(3):
            self.engine.record_trade_result(-5.0)
        self.assertTrue(self.engine.is_safe_mode)
        self.engine.exit_safe_mode()
        self.assertFalse(self.engine.is_safe_mode)

    def test_rate_limit(self):
        self.engine._state.trades_this_hour = 5
        result = self.engine.check_trade_allowed()
        self.assertFalse(result["allowed"])

    def test_low_payout_blocked(self):
        result = self.engine.check_trade_allowed(payout=0.60)
        self.assertFalse(result["allowed"])

    def test_snapshot(self):
        self.engine.record_trade_result(10.0)
        self.engine.record_trade_result(-5.0)
        snap = self.engine.get_snapshot()
        self.assertEqual(snap["total_trades"], 2)
        self.assertEqual(snap["total_wins"], 1)
        self.assertEqual(snap["total_losses"], 1)
        self.assertEqual(snap["pnl"], 5.0)

    def test_reset_session(self):
        self.engine.record_trade_result(10.0)
        self.engine.reset_session()
        self.assertEqual(self.engine.state.total_trades, 0)
        self.assertEqual(self.engine.state.pnl, 0.0)

    def test_unlock(self):
        self.engine._state.locked = True
        self.engine._state.lock_reason = "test"
        self.engine.unlock()
        self.assertFalse(self.engine.is_locked)

    def test_drawdown_calculation(self):
        self.engine.record_trade_result(100.0)  # peak = 1100
        self.engine.record_trade_result(-50.0)  # current = 1050
        snap = self.engine.get_snapshot()
        self.assertGreater(snap["drawdown_pct"], 0)


if __name__ == "__main__":
    unittest.main()
