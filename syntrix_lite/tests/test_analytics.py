"""Tests for analytics."""

import pytest
from analytics.tracker import Analytics, TradeLog


class TestAnalytics:
    def test_empty(self):
        a = Analytics()
        assert a.total == 0
        assert a.winrate() == 0.0
        assert a.expectancy() == 0.0

    def test_winrate(self):
        a = Analytics()
        a.record(TradeLog(result="win", profit=1.8))
        a.record(TradeLog(result="loss", profit=-2.0))
        a.record(TradeLog(result="win", profit=1.8))
        assert a.winrate() == pytest.approx(2 / 3, abs=0.01)

    def test_expectancy(self):
        a = Analytics()
        a.record(TradeLog(result="win", profit=1.8))
        a.record(TradeLog(result="loss", profit=-2.0))
        assert a.expectancy() == pytest.approx(-0.1, abs=0.01)

    def test_pnl(self):
        a = Analytics()
        a.record(TradeLog(result="win", profit=5.0))
        a.record(TradeLog(result="loss", profit=-2.0))
        assert a.total_pnl() == 3.0

    def test_drawdown(self):
        a = Analytics()
        a.record(TradeLog(result="win", profit=10.0))
        a.record(TradeLog(result="loss", profit=-5.0))
        a.record(TradeLog(result="loss", profit=-3.0))
        a.record(TradeLog(result="win", profit=2.0))
        assert a.max_drawdown() == 8.0

    def test_by_asset(self):
        a = Analytics()
        a.record(TradeLog(asset="EURUSD-OTC", result="win", profit=1.0))
        a.record(TradeLog(asset="EURUSD-OTC", result="loss", profit=-1.0))
        a.record(TradeLog(asset="GBPUSD-OTC", result="win", profit=1.0))
        by_a = a.by_asset()
        assert "EURUSD-OTC" in by_a
        assert by_a["EURUSD-OTC"]["trades"] == 2
        assert by_a["GBPUSD-OTC"]["trades"] == 1

    def test_by_strategy(self):
        a = Analytics()
        a.record(TradeLog(strategy="trend_pullback", result="win", profit=1.0))
        a.record(TradeLog(strategy="momentum", result="loss", profit=-1.0))
        by_s = a.by_strategy()
        assert "trend_pullback" in by_s
        assert "momentum" in by_s

    def test_by_hour(self):
        a = Analytics()
        a.record(TradeLog(hour_utc=14, result="win", profit=1.0))
        a.record(TradeLog(hour_utc=14, result="win", profit=1.0))
        a.record(TradeLog(hour_utc=20, result="loss", profit=-1.0))
        by_h = a.by_hour()
        assert 14 in by_h
        assert by_h[14]["trades"] == 2
        assert by_h[14]["winrate"] == 100.0

    def test_sharpe(self):
        a = Analytics()
        a.record(TradeLog(result="win", profit=1.0))
        a.record(TradeLog(result="win", profit=1.0))
        a.record(TradeLog(result="loss", profit=-0.5))
        s = a.sharpe()
        assert s > 0  # Positive returns should give positive Sharpe

    def test_format_report(self):
        a = Analytics()
        a.record(TradeLog(asset="EUR", strategy="tp", result="win", profit=1.0, hour_utc=10))
        report = a.format_report()
        assert "ANALYTICS REPORT" in report
        assert "Winrate" in report
