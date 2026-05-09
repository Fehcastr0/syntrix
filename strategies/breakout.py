"""
strategies/breakout.py — Breakout strategy for Syntrix.

Detects price breakouts from consolidation zones with volume confirmation.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from brokers.base import Candle, TradeDirection
from context.market_regime import MarketRegime
from indicators.technical import atr, bollinger_bands
from strategies.base import BaseStrategy, StrategySignal


class BreakoutStrategy(BaseStrategy):
    """
    Breakout: enters when price breaks out of a tight consolidation.

    Logic:
    1. Detect consolidation (narrow Bollinger Bands)
    2. Price breaks above/below band with strong candle
    3. ATR confirms expanding volatility
    """

    def __init__(
        self,
        bb_period: int = 20,
        bb_std: float = 2.0,
        atr_period: int = 14,
        squeeze_threshold: float = 0.5,
        breakout_body_mult: float = 1.5,
        lookback: int = 5,
    ) -> None:
        super().__init__(name="breakout")
        self._bb_period = bb_period
        self._bb_std = bb_std
        self._atr_period = atr_period
        self._squeeze_threshold = squeeze_threshold
        self._body_mult = breakout_body_mult
        self._lookback = lookback

    def ideal_regimes(self) -> List[MarketRegime]:
        return [MarketRegime.RANGE, MarketRegime.LOW_VOLATILITY]

    def evaluate(
        self,
        candles: List[Candle],
        regime: MarketRegime,
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[StrategySignal]:
        min_needed = max(self._bb_period, self._atr_period) + self._lookback + 5
        if len(candles) < min_needed:
            return None

        bb = bollinger_bands(candles, self._bb_period, self._bb_std)
        atr_vals = atr(candles, self._atr_period)

        if not bb["upper"] or len(bb["upper"]) < self._lookback or not atr_vals:
            return None

        current_upper = bb["upper"][-1]
        current_lower = bb["lower"][-1]
        band_width = current_upper - current_lower

        recent_widths = [
            bb["upper"][-i] - bb["lower"][-i]
            for i in range(1, min(self._lookback + 1, len(bb["upper"])))
        ]
        avg_width = sum(recent_widths) / len(recent_widths) if recent_widths else band_width

        current_atr = atr_vals[-1]
        prev_atr = atr_vals[-2] if len(atr_vals) > 1 else current_atr

        current = candles[-1]
        previous = candles[-2]

        avg_body = sum(c.body_size for c in candles[-10:-1]) / 9 if len(candles) > 10 else current.body_size

        was_squeezed = avg_width > 0 and band_width / avg_width < self._squeeze_threshold
        strong_candle = current.body_size > avg_body * self._body_mult
        atr_expanding = current_atr > prev_atr

        if not (was_squeezed or (strong_candle and atr_expanding)):
            return None

        # Bullish breakout
        if current.close > current_upper and current.is_bullish and strong_candle:
            score = min(1.0, (current.body_size / avg_body - 1) * 0.3 + 0.5)
            return StrategySignal(
                strategy_name=self.name,
                direction=TradeDirection.CALL,
                score=round(score, 3),
                confidence=round(min(1.0, score * 0.7), 3),
                ideal_regimes=self.ideal_regimes(),
                context={
                    "price": round(current.close, 6),
                    "upper_band": round(current_upper, 6),
                    "band_width": round(band_width, 6),
                    "atr": round(current_atr, 6),
                    "body_ratio": round(current.body_size / avg_body, 2),
                    "squeezed": was_squeezed,
                },
                reason="Bullish breakout above upper BB",
            )

        # Bearish breakout
        if current.close < current_lower and current.is_bearish and strong_candle:
            score = min(1.0, (current.body_size / avg_body - 1) * 0.3 + 0.5)
            return StrategySignal(
                strategy_name=self.name,
                direction=TradeDirection.PUT,
                score=round(score, 3),
                confidence=round(min(1.0, score * 0.7), 3),
                ideal_regimes=self.ideal_regimes(),
                context={
                    "price": round(current.close, 6),
                    "lower_band": round(current_lower, 6),
                    "band_width": round(band_width, 6),
                    "atr": round(current_atr, 6),
                    "body_ratio": round(current.body_size / avg_body, 2),
                    "squeezed": was_squeezed,
                },
                reason="Bearish breakout below lower BB",
            )

        return None
