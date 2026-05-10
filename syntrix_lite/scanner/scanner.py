"""
Scanner — escaneia ativos e gera contexto para estratégias.
Simples. Sem asset DNA, sem microstructure, sem OTC intelligence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from scanner.indicators import Candle, atr, ema, rsi

# All OTC assets available
DEFAULT_ASSETS = [
    "EURUSD-OTC", "GBPUSD-OTC", "EURJPY-OTC", "USDJPY-OTC",
    "AUDCAD-OTC", "AUDUSD-OTC", "EURGBP-OTC", "GBPJPY-OTC",
    "NZDUSD-OTC", "USDCAD-OTC", "USDCHF-OTC",
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "EURJPY",
]


@dataclass
class ScanResult:
    """Result of scanning one asset."""
    asset: str = ""
    candles: List[Candle] = field(default_factory=list)
    payout: float = 0.0
    ema_fast: List[float] = field(default_factory=list)
    ema_slow: List[float] = field(default_factory=list)
    rsi_values: List[float] = field(default_factory=list)
    atr_values: List[float] = field(default_factory=list)
    trend_up: bool = False
    trend_down: bool = False
    current_rsi: float = 50.0
    current_atr: float = 0.0
    volatility_ok: bool = True
    payout_ok: bool = True


def scan_asset(
    asset: str,
    candles: List[Candle],
    payout: float,
    min_payout: float = 0.60,
    ema_fast_period: int = 9,
    ema_slow_period: int = 21,
    rsi_period: int = 14,
    atr_period: int = 14,
    min_atr_ratio: float = 0.0001,
) -> Optional[ScanResult]:
    """Scan an asset and compute indicators."""
    if len(candles) < max(ema_slow_period, rsi_period, atr_period) + 2:
        return None

    closes = [c.close for c in candles]

    ema_f = ema(closes, ema_fast_period)
    ema_s = ema(closes, ema_slow_period)
    rsi_vals = rsi(closes, rsi_period)
    atr_vals = atr(candles, atr_period)

    current_rsi = rsi_vals[-1] if rsi_vals else 50.0
    current_atr = atr_vals[-1] if atr_vals else 0.0
    last_close = closes[-1] if closes else 0

    # Trend direction
    trend_up = ema_f[-1] > ema_s[-1] if ema_f and ema_s else False
    trend_down = ema_f[-1] < ema_s[-1] if ema_f and ema_s else False

    # Volatility check — ATR must be meaningful
    atr_ratio = current_atr / last_close if last_close > 0 else 0
    volatility_ok = atr_ratio >= min_atr_ratio

    # Payout check
    payout_ok = payout >= min_payout

    return ScanResult(
        asset=asset,
        candles=candles,
        payout=payout,
        ema_fast=ema_f,
        ema_slow=ema_s,
        rsi_values=rsi_vals,
        atr_values=atr_vals,
        trend_up=trend_up,
        trend_down=trend_down,
        current_rsi=current_rsi,
        current_atr=current_atr,
        volatility_ok=volatility_ok,
        payout_ok=payout_ok,
    )
