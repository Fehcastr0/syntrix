"""
Trend Pullback — entra na direção da tendência quando RSI recua.
Simples. Objetiva. Estatisticamente repetível.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from scanner.scanner import ScanResult


@dataclass
class Signal:
    asset: str = ""
    direction: str = ""  # "call" or "put"
    strategy: str = ""
    score: float = 0.0
    payout: float = 0.0
    rsi: float = 50.0
    atr: float = 0.0


def evaluate(scan: ScanResult) -> Optional[Signal]:
    """
    CALL: EMA fast > EMA slow + RSI 30-70 (wide range to generate signals)
    PUT:  EMA fast < EMA slow + RSI 30-70 (wide range to generate signals)
    """
    r = scan.current_rsi

    direction = ""
    rsi_score = 0.0

    if scan.trend_up and 30 <= r <= 70:
        direction = "call"
        # Best score when RSI is near 50 (pullback zone)
        rsi_score = 1.0 - abs(r - 52.5) / 30.0

    elif scan.trend_down and 30 <= r <= 70:
        direction = "put"
        rsi_score = 1.0 - abs(r - 47.5) / 30.0

    if not direction:
        return None

    # Simple additive scoring
    score = 0.0

    # Trend strength (+0.40 max) — how far EMAs are apart
    if scan.ema_fast and scan.ema_slow and scan.candles:
        ema_diff = abs(scan.ema_fast[-1] - scan.ema_slow[-1])
        ema_ratio = ema_diff / scan.candles[-1].close if scan.candles[-1].close > 0 else 0
        trend_score = min(1.0, ema_ratio / 0.002)  # normalize to ~0.2% move
        score += trend_score * 0.40

    # RSI favorable (+0.30 max)
    score += max(0, rsi_score) * 0.30

    # Volatility healthy (+0.20 max)
    if scan.volatility_ok:
        score += 0.20

    # Payout good (+0.10 max)
    if scan.payout >= 0.80:
        score += 0.10
    elif scan.payout >= 0.70:
        score += 0.07
    elif scan.payout >= 0.50:
        score += 0.03

    return Signal(
        asset=scan.asset,
        direction=direction,
        strategy="trend_pullback",
        score=round(score, 4),
        payout=scan.payout,
        rsi=r,
        atr=scan.current_atr,
    )
