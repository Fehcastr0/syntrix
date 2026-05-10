"""Tests for risk manager."""

import time
import pytest
from risk.risk_manager import RiskConfig, RiskManager


class TestRiskManager:
    def test_initial_can_trade(self):
        rm = RiskManager()
        ok, reason = rm.can_trade()
        assert ok
        assert reason == "OK"

    def test_stop_gain(self):
        rm = RiskManager(RiskConfig(stop_gain=10.0))
        rm.record_result(12.0)
        ok, reason = rm.can_trade()
        assert not ok
        assert "STOP_GAIN" in reason

    def test_stop_loss(self):
        rm = RiskManager(RiskConfig(stop_loss=-5.0))
        rm.record_result(-6.0)
        ok, reason = rm.can_trade()
        assert not ok
        assert "STOP_LOSS" in reason

    def test_consecutive_losses_cooldown(self):
        rm = RiskManager(RiskConfig(
            max_consecutive_losses=2,
            cooldown_after_loss_sec=60,
            cooldown_after_trade_sec=0,
        ))
        rm.record_result(-1.0)
        rm.record_result(-1.0)
        ok, reason = rm.can_trade()
        assert not ok
        assert "LOSS_COOLDOWN" in reason

    def test_win_resets_streak(self):
        rm = RiskManager(RiskConfig(
            max_consecutive_losses=2,
            cooldown_after_trade_sec=0,
        ))
        rm.record_result(-1.0)
        rm.record_result(2.0)  # Win resets streak
        ok, _ = rm.can_trade()
        assert ok

    def test_trade_cooldown(self):
        rm = RiskManager(RiskConfig(cooldown_after_trade_sec=60))
        rm.record_result(1.0)
        ok, reason = rm.can_trade()
        assert not ok
        assert "TRADE_COOLDOWN" in reason

    def test_position_lock(self):
        rm = RiskManager()
        rm.enter_position()
        ok, reason = rm.can_trade()
        assert not ok
        assert reason == "IN_POSITION"

    def test_pnl_tracking(self):
        rm = RiskManager()
        rm.record_result(5.0)
        rm.record_result(-2.0)
        rm.record_result(3.0)
        assert rm.pnl == 6.0
        assert rm.total_trades == 3
        assert rm.wins == 2
        assert rm.losses == 1

    def test_max_trades(self):
        rm = RiskManager(RiskConfig(
            max_trades_per_session=3,
            cooldown_after_trade_sec=0,
        ))
        rm.record_result(1.0)
        rm.record_result(1.0)
        rm.record_result(1.0)
        ok, reason = rm.can_trade()
        assert not ok
        assert "MAX_TRADES" in reason

    def test_stats(self):
        rm = RiskManager()
        rm.record_result(5.0)
        rm.record_result(-2.0)
        stats = rm.get_stats()
        assert stats["total_trades"] == 2
        assert stats["wins"] == 1
        assert stats["losses"] == 1
        assert stats["pnl"] == 3.0
        assert stats["winrate"] == 50.0
