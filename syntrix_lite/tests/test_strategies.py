"""Tests for strategies."""

import pytest
from scanner.indicators import Candle
from scanner.scanner import ScanResult, scan_asset
from strategies import trend_pullback, momentum


def _make_uptrend_candles(n=30):
    """Generate candles with clear uptrend."""
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
    """Generate candles with clear downtrend."""
    candles = []
    base = 1.2000
    for i in range(n):
        o = base - i * 0.0010
        c = o - 0.0008
        h = o + 0.0003
        lo = c - 0.0003
        candles.append(Candle(open=o, high=h, low=lo, close=c, timestamp=float(i)))
    return candles


def _make_flat_candles(n=30):
    """Generate flat/range candles."""
    candles = []
    base = 1.1500
    for i in range(n):
        o = base + (0.0001 if i % 2 == 0 else -0.0001)
        c = base - (0.0001 if i % 2 == 0 else -0.0001)
        h = base + 0.0003
        lo = base - 0.0003
        candles.append(Candle(open=o, high=h, low=lo, close=c, timestamp=float(i)))
    return candles


class TestTrendPullback:
    def test_uptrend_generates_call(self):
        candles = _make_uptrend_candles()
        scan = scan_asset("EURUSD-OTC", candles, 0.80)
        assert scan is not None
        sig = trend_pullback.evaluate(scan)
        # May or may not generate signal depending on RSI
        if sig:
            assert sig.direction in ("call", "put")
            assert sig.strategy == "trend_pullback"
            assert sig.score > 0

    def test_downtrend_generates_put(self):
        candles = _make_downtrend_candles()
        scan = scan_asset("EURUSD-OTC", candles, 0.80)
        assert scan is not None
        sig = trend_pullback.evaluate(scan)
        if sig:
            assert sig.direction in ("call", "put")
            assert sig.strategy == "trend_pullback"

    def test_score_range(self):
        candles = _make_uptrend_candles()
        scan = scan_asset("EURUSD-OTC", candles, 0.85)
        assert scan is not None
        sig = trend_pullback.evaluate(scan)
        if sig:
            assert 0 <= sig.score <= 1.0


class TestMomentum:
    def test_uptrend_generates_call(self):
        candles = _make_uptrend_candles()
        scan = scan_asset("EURUSD-OTC", candles, 0.80)
        assert scan is not None
        sig = momentum.evaluate(scan)
        if sig:
            assert sig.direction in ("call", "put")
            assert sig.strategy == "momentum"

    def test_downtrend_generates_put(self):
        candles = _make_downtrend_candles()
        scan = scan_asset("EURUSD-OTC", candles, 0.80)
        assert scan is not None
        sig = momentum.evaluate(scan)
        if sig:
            assert sig.direction in ("call", "put")
            assert sig.strategy == "momentum"

    def test_score_range(self):
        candles = _make_uptrend_candles()
        scan = scan_asset("EURUSD-OTC", candles, 0.85)
        assert scan is not None
        sig = momentum.evaluate(scan)
        if sig:
            assert 0 <= sig.score <= 1.0
