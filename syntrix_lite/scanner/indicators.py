"""
Indicadores técnicos — apenas EMA, RSI, ATR.
Sem redundância. Sem complexidade estética.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass
class Candle:
    open: float
    high: float
    low: float
    close: float
    timestamp: float = 0.0


def ema(values: List[float], period: int) -> List[float]:
    """Exponential Moving Average."""
    if not values or period < 1:
        return []
    result: List[float] = []
    k = 2.0 / (period + 1)
    prev = values[0]
    result.append(prev)
    for v in values[1:]:
        prev = v * k + prev * (1 - k)
        result.append(prev)
    return result


def rsi(closes: List[float], period: int = 14) -> List[float]:
    """Relative Strength Index."""
    if len(closes) < period + 1:
        return [50.0] * len(closes)
    result: List[float] = [50.0] * period
    gains: List[float] = []
    losses: List[float] = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    if avg_loss == 0:
        result.append(100.0)
    else:
        rs = avg_gain / avg_loss
        result.append(100 - 100 / (1 + rs))

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            result.append(100.0)
        else:
            rs = avg_gain / avg_loss
            result.append(100 - 100 / (1 + rs))

    return result


def atr(candles: List[Candle], period: int = 14) -> List[float]:
    """Average True Range."""
    if len(candles) < 2:
        return [0.0] * len(candles)
    trs: List[float] = [candles[0].high - candles[0].low]
    for i in range(1, len(candles)):
        c = candles[i]
        prev_close = candles[i - 1].close
        tr = max(c.high - c.low, abs(c.high - prev_close), abs(c.low - prev_close))
        trs.append(tr)
    return ema(trs, period)
