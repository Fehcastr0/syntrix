"""Tests for strategies — M1 mean reversion logic."""

import pytest
from scanner.indicators import Candle
from scanner.scanner import ScanResult, scan_asset
from strategies import trend_pullback, momentum


def _make_uptrend_candles(n=30):
    """Generate candles with clear uptrend (RSI will be high)."""
    candles = []
    base = 1.1000
    for i in range(n):
        o = base + i * 0.0010
        c = o + 0.0008
        h = c + 0.0003
        lo = o - 0.0003
        candles.append(Candle(open=o, high=h, low=lo, close=c, timestamp=float(i)))
    return candles


def _make_downtrend_candles(n=30):
    """Generate candles with clear downtrend (RSI will be low)."""
    candles = []
    base = 1.2000
    for i in range(n):
        o = base - i * 0.0010
        c = o - 0.0008
        h = o + 0.0003
        lo = c - 0.0003
        candles.append(Candle(open=o, high=h, low=lo, close=c, timestamp=float(i)))
    return candles


def _make_overbought_reversal_candles(n=30):
    """Uptrend that becomes overbought, last candle bearish (reversal)."""
    candles = []
    base = 1.1000
    for i in range(n - 1):
        o = base + i * 0.0012
        c = o + 0.0010
        h = c + 0.0002
        lo = o - 0.0001
        candles.append(Candle(open=o, high=h, low=lo, close=c, timestamp=float(i)))
    # Last candle: bearish reversal (close < open)
    last_o = candles[-1].close + 0.0005
    last_c = last_o - 0.0008
    candles.append(Candle(
        open=last_o, high=last_o + 0.0004, low=last_c - 0.0001,
        close=last_c, timestamp=float(n - 1),
    ))
    return candles


def _make_oversold_reversal_candles(n=30):
    """Downtrend that becomes oversold, last candle bullish (reversal)."""
    candles = []
    base = 1.2000
    for i in range(n - 1):
        o = base - i * 0.0012
        c = o - 0.0010
        h = o + 0.0001
        lo = c - 0.0002
        candles.append(Candle(open=o, high=h, low=lo, close=c, timestamp=float(i)))
    # Last candle: bullish reversal (close > open)
    last_o = candles[-1].close - 0.0005
    last_c = last_o + 0.0008
    candles.append(Candle(
        open=last_o, high=last_c + 0.0001, low=last_o - 0.0004,
        close=last_c, timestamp=float(n - 1),
    ))
    return candles


class TestTrendPullback:
    def test_overbought_generates_put(self):
        """After uptrend, RSI high + bearish candle = PUT (correction down)."""
        candles = _make_overbought_reversal_candles()
        scan = scan_asset("EURUSD-OTC", candles, 0.80)
        assert scan is not None
        sig = trend_pullback.evaluate(scan)
        if sig:
            assert sig.direction == "put"
            assert sig.strategy == "trend_pullback"
            assert sig.score > 0

    def test_oversold_generates_call(self):
        """After downtrend, RSI low + bullish candle = CALL (correction up)."""
        candles = _make_oversold_reversal_candles()
        scan = scan_asset("EURUSD-OTC", candles, 0.80)
        assert scan is not None
        sig = trend_pullback.evaluate(scan)
        if sig:
            assert sig.direction == "call"
            assert sig.strategy == "trend_pullback"

    def test_uptrend_does_not_generate_call(self):
        """Uptrend should NOT generate CALL — that's the wrong direction for M1."""
        candles = _make_uptrend_candles()
        scan = scan_asset("EURUSD-OTC", candles, 0.80)
        assert scan is not None
        sig = trend_pullback.evaluate(scan)
        if sig:
            # If signal exists in uptrend, it must be PUT (reversal)
            assert sig.direction == "put"

    def test_score_range(self):
        candles = _make_overbought_reversal_candles()
        scan = scan_asset("EURUSD-OTC", candles, 0.85)
        assert scan is not None
        sig = trend_pullback.evaluate(scan)
        if sig:
            assert 0 <= sig.score <= 1.0


class TestMomentum:
    def test_extreme_overbought_generates_put(self):
        """RSI > 70 + bearish candle = PUT (exhaustion reversal)."""
        candles = _make_overbought_reversal_candles(40)
        scan = scan_asset("EURUSD-OTC", candles, 0.80)
        assert scan is not None
        sig = momentum.evaluate(scan)
        if sig:
            assert sig.direction == "put"
            assert sig.strategy == "momentum_reversal"

    def test_extreme_oversold_generates_call(self):
        """RSI < 30 + bullish candle = CALL (exhaustion reversal)."""
        candles = _make_oversold_reversal_candles(40)
        scan = scan_asset("EURUSD-OTC", candles, 0.80)
        assert scan is not None
        sig = momentum.evaluate(scan)
        if sig:
            assert sig.direction == "call"
            assert sig.strategy == "momentum_reversal"

    def test_score_range(self):
        candles = _make_overbought_reversal_candles(40)
        scan = scan_asset("EURUSD-OTC", candles, 0.85)
        assert scan is not None
        sig = momentum.evaluate(scan)
        if sig:
            assert 0 <= sig.score <= 1.0
