"""
Momentum — entra quando RSI mostra impulso forte na direção da tendência.
Simples. Rápida. Estatisticamente repetível.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from scanner.scanner import ScanResult
from strategies.trend_pullback import Signal


def evaluate(scan: ScanResult) -> Optional[Signal]:
    """
    CALL: EMA fast > EMA slow + RSI > 55 (strong upward momentum)
    PUT:  EMA fast < EMA slow + RSI < 45 (strong downward momentum)
    """
    r = scan.current_rsi

    direction = ""
    momentum_strength = 0.0

    if scan.trend_up and r > 55:
        direction = "call"
        # Stronger momentum = higher score, capped at RSI 75
        momentum_strength = min(1.0, (r - 55) / 20.0)

    elif scan.trend_down and r < 45:
        direction = "put"
        momentum_strength = min(1.0, (45 - r) / 20.0)

    if not direction:
        return None

    # Simple additive scoring
    score = 0.0

    # Trend alignment (+0.40 max)
    if scan.ema_fast and scan.ema_slow and scan.candles:
        ema_diff = abs(scan.ema_fast[-1] - scan.ema_slow[-1])
        ema_ratio = ema_diff / scan.candles[-1].close if scan.candles[-1].close > 0 else 0
        trend_score = min(1.0, ema_ratio / 0.002)
        score += trend_score * 0.40

    # Momentum strength (+0.30 max)
    score += momentum_strength * 0.30

    # Volatility healthy (+0.20 max)
    if scan.volatility_ok:
        score += 0.20

    # Payout good (+0.10 max)
    if scan.payout >= 0.80:
        score += 0.10
    elif scan.payout >= 0.70:
        score += 0.05

    return Signal(
        asset=scan.asset,
        direction=direction,
        strategy="momentum",
        score=round(score, 4),
        payout=scan.payout,
        rsi=r,
        atr=scan.current_atr,
    )
