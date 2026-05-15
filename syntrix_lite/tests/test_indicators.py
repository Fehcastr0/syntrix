"""Tests for indicators."""

import pytest
from scanner.indicators import Candle, atr, ema, rsi


class TestEMA:
    def test_basic(self):
        values = [10, 11, 12, 13, 14, 15]
        result = ema(values, 3)
        assert len(result) == 6
        assert result[0] == 10

    def test_single_value(self):
        assert ema([5.0], 3) == [5.0]

    def test_empty(self):
        assert ema([], 3) == []

    def test_trend_up(self):
        values = list(range(1, 21))
        result = ema(values, 5)
        assert result[-1] > result[0]

    def test_period_1(self):
        values = [10, 20, 30]
        result = ema(values, 1)
        assert result == [10, 20, 30]


class TestRSI:
    def test_basic(self):
        closes = list(range(50, 80))
        result = rsi(closes, 14)
        assert len(result) == len(closes)
        assert result[-1] > 50  # Uptrend should have RSI > 50

    def test_downtrend(self):
        closes = list(range(80, 50, -1))
        result = rsi(closes, 14)
        assert result[-1] < 50

    def test_short_data(self):
        closes = [10, 11, 12]
        result = rsi(closes, 14)
        assert all(r == 50.0 for r in result)

    def test_range_bounds(self):
        closes = [50 + i * 0.5 for i in range(30)]
        result = rsi(closes, 14)
        for r in result:
            assert 0 <= r <= 100


class TestATR:
    def test_basic(self):
        candles = [
            Candle(open=10, high=11, low=9, close=10.5),
            Candle(open=10.5, high=12, low=10, close=11),
            Candle(open=11, high=11.5, low=10.5, close=11.2),
        ]
        result = atr(candles, 2)
        assert len(result) == 3
        assert all(r >= 0 for r in result)

    def test_single_candle(self):
        candles = [Candle(open=10, high=12, low=9, close=11)]
        result = atr(candles, 14)
        assert len(result) == 1
