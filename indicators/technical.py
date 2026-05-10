"""
indicators/technical.py — Lightweight technical indicators for Syntrix.

Pure Python implementations — no heavy dependencies.
All indicators operate on lists of Candle objects.
"""

from __future__ import annotations

import statistics
from typing import List, Optional

from brokers.base import Candle


def sma(candles: List[Candle], period: int, source: str = "close") -> List[float]:
    """Simple Moving Average."""
    values = _extract(candles, source)
    if len(values) < period:
        return []
    result: List[float] = []
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        result.append(sum(window) / period)
    return result


def ema(candles: List[Candle], period: int, source: str = "close") -> List[float]:
    """Exponential Moving Average."""
    values = _extract(candles, source)
    if len(values) < period:
        return []
    k = 2.0 / (period + 1)
    result: List[float] = [sum(values[:period]) / period]
    for i in range(period, len(values)):
        result.append(values[i] * k + result[-1] * (1 - k))
    return result


def rsi(candles: List[Candle], period: int = 14) -> List[float]:
    """Relative Strength Index."""
    values = _extract(candles, "close")
    if len(values) < period + 1:
        return []

    deltas = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(d, 0) for d in deltas]
    losses = [abs(min(d, 0)) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    result: List[float] = []
    if avg_loss == 0:
        result.append(100.0)
    else:
        rs = avg_gain / avg_loss
        result.append(100 - (100 / (1 + rs)))

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            result.append(100.0)
        else:
            rs = avg_gain / avg_loss
            result.append(100 - (100 / (1 + rs)))

    return result


def bollinger_bands(
    candles: List[Candle], period: int = 20, std_dev: float = 2.0
) -> dict[str, List[float]]:
    """Bollinger Bands — returns upper, middle, lower."""
    values = _extract(candles, "close")
    if len(values) < period:
        return {"upper": [], "middle": [], "lower": []}

    upper: List[float] = []
    middle: List[float] = []
    lower: List[float] = []

    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        mean = statistics.mean(window)
        std = statistics.stdev(window) if len(window) > 1 else 0
        middle.append(mean)
        upper.append(mean + std_dev * std)
        lower.append(mean - std_dev * std)

    return {"upper": upper, "middle": middle, "lower": lower}


def atr(candles: List[Candle], period: int = 14) -> List[float]:
    """Average True Range."""
    if len(candles) < period + 1:
        return []

    trs: List[float] = []
    for i in range(1, len(candles)):
        hl = candles[i].high - candles[i].low
        hc = abs(candles[i].high - candles[i - 1].close)
        lc = abs(candles[i].low - candles[i - 1].close)
        trs.append(max(hl, hc, lc))

    result: List[float] = []
    if len(trs) < period:
        return []

    first_atr = sum(trs[:period]) / period
    result.append(first_atr)

    for i in range(period, len(trs)):
        new_atr = (result[-1] * (period - 1) + trs[i]) / period
        result.append(new_atr)

    return result


def macd(
    candles: List[Candle],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> dict[str, List[float]]:
    """MACD — returns macd_line, signal_line, histogram."""
    fast_ema = ema(candles, fast)
    slow_ema = ema(candles, slow)

    if not fast_ema or not slow_ema:
        return {"macd": [], "signal": [], "histogram": []}

    diff = len(fast_ema) - len(slow_ema)
    if diff > 0:
        fast_ema = fast_ema[diff:]

    macd_line = [f - s for f, s in zip(fast_ema, slow_ema)]

    if len(macd_line) < signal:
        return {"macd": macd_line, "signal": [], "histogram": []}

    k = 2.0 / (signal + 1)
    sig: List[float] = [sum(macd_line[:signal]) / signal]
    for i in range(signal, len(macd_line)):
        sig.append(macd_line[i] * k + sig[-1] * (1 - k))

    macd_trimmed = macd_line[signal - 1 :]
    hist = [m - s for m, s in zip(macd_trimmed, sig)]

    return {"macd": macd_trimmed, "signal": sig, "histogram": hist}


def stochastic(
    candles: List[Candle], k_period: int = 14, d_period: int = 3
) -> dict[str, List[float]]:
    """Stochastic Oscillator — returns %K and %D."""
    if len(candles) < k_period:
        return {"k": [], "d": []}

    k_values: List[float] = []
    for i in range(k_period - 1, len(candles)):
        window = candles[i - k_period + 1 : i + 1]
        highest = max(c.high for c in window)
        lowest = min(c.low for c in window)
        rng = highest - lowest
        if rng == 0:
            k_values.append(50.0)
        else:
            k_values.append((candles[i].close - lowest) / rng * 100)

    d_values: List[float] = []
    for i in range(d_period - 1, len(k_values)):
        d_values.append(sum(k_values[i - d_period + 1 : i + 1]) / d_period)

    return {"k": k_values, "d": d_values}


def adx(candles: List[Candle], period: int = 14) -> List[float]:
    """Average Directional Index (simplified)."""
    if len(candles) < period * 2:
        return []

    plus_dm: List[float] = []
    minus_dm: List[float] = []
    tr_list: List[float] = []

    for i in range(1, len(candles)):
        high_diff = candles[i].high - candles[i - 1].high
        low_diff = candles[i - 1].low - candles[i].low
        plus_dm.append(max(high_diff, 0) if high_diff > low_diff else 0)
        minus_dm.append(max(low_diff, 0) if low_diff > high_diff else 0)

        hl = candles[i].high - candles[i].low
        hc = abs(candles[i].high - candles[i - 1].close)
        lc = abs(candles[i].low - candles[i - 1].close)
        tr_list.append(max(hl, hc, lc))

    if len(tr_list) < period:
        return []

    atr_val = sum(tr_list[:period]) / period
    plus_di_sum = sum(plus_dm[:period]) / period
    minus_di_sum = sum(minus_dm[:period]) / period

    dx_values: List[float] = []

    for i in range(period, len(tr_list)):
        atr_val = (atr_val * (period - 1) + tr_list[i]) / period
        plus_di_sum = (plus_di_sum * (period - 1) + plus_dm[i]) / period
        minus_di_sum = (minus_di_sum * (period - 1) + minus_dm[i]) / period

        if atr_val > 0:
            plus_di = (plus_di_sum / atr_val) * 100
            minus_di = (minus_di_sum / atr_val) * 100
        else:
            plus_di = minus_di = 0

        di_sum = plus_di + minus_di
        if di_sum > 0:
            dx_values.append(abs(plus_di - minus_di) / di_sum * 100)
        else:
            dx_values.append(0)

    if len(dx_values) < period:
        return []

    adx_val = sum(dx_values[:period]) / period
    result = [adx_val]
    for i in range(period, len(dx_values)):
        adx_val = (adx_val * (period - 1) + dx_values[i]) / period
        result.append(adx_val)

    return result


def _extract(candles: List[Candle], source: str) -> List[float]:
    """Extract a price series from candles."""
    mapping = {
        "close": lambda c: c.close,
        "open": lambda c: c.open,
        "high": lambda c: c.high,
        "low": lambda c: c.low,
        "hl2": lambda c: (c.high + c.low) / 2,
        "hlc3": lambda c: (c.high + c.low + c.close) / 3,
    }
    fn = mapping.get(source, mapping["close"])
    return [fn(c) for c in candles]
