"""
strategies/momentum.py — Momentum strategy for Syntrix.

Trades in the direction of strong short-term momentum
confirmed by multiple indicators.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from brokers.base import Candle, TradeDirection
from context.market_regime import MarketRegime
from indicators.technical import ema, macd, rsi
from strategies.base import BaseStrategy, StrategySignal


class MomentumStrategy(BaseStrategy):
    """
    Momentum: enters when multiple momentum indicators align.

    Logic:
    1. MACD histogram positive and growing (bullish) or negative and declining (bearish)
    2. RSI confirms direction (not extreme)
    3. Short EMA slope confirms momentum direction
    """

    def __init__(
        self,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        rsi_period: int = 14,
        ema_period: int = 9,
        rsi_min_bull: float = 50.0,
        rsi_max_bear: float = 50.0,
        min_hist_change: float = 0.0,
    ) -> None:
        super().__init__(name="momentum")
        self._macd_fast = macd_fast
        self._macd_slow = macd_slow
        self._macd_signal = macd_signal
        self._rsi_period = rsi_period
        self._ema_period = ema_period
        self._rsi_min_bull = rsi_min_bull
        self._rsi_max_bear = rsi_max_bear
        self._min_hist_change = min_hist_change

    def ideal_regimes(self) -> List[MarketRegime]:
        return [MarketRegime.TREND, MarketRegime.HIGH_VOLATILITY]

    def evaluate(
        self,
        candles: List[Candle],
        regime: MarketRegime,
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[StrategySignal]:
        min_needed = self._macd_slow + self._macd_signal + 5
        if len(candles) < min_needed:
            return None

        macd_data = macd(candles, self._macd_fast, self._macd_slow, self._macd_signal)
        rsi_vals = rsi(candles, self._rsi_period)
        ema_vals = ema(candles, self._ema_period)

        if not macd_data["histogram"] or len(macd_data["histogram"]) < 3:
            return None
        if not rsi_vals or not ema_vals or len(ema_vals) < 3:
            return None

        hist = macd_data["histogram"]
        current_hist = hist[-1]
        prev_hist = hist[-2]
        hist_change = current_hist - prev_hist

        current_rsi = rsi_vals[-1]

        ema_slope = (ema_vals[-1] - ema_vals[-3]) / ema_vals[-3] if ema_vals[-3] > 0 else 0

        # Bullish momentum
        bullish_hist = current_hist > 0 and hist_change > self._min_hist_change
        bullish_rsi = current_rsi > self._rsi_min_bull and current_rsi < 80
        bullish_ema = ema_slope > 0

        if bullish_hist and bullish_rsi and bullish_ema:
            score = min(
                1.0,
                abs(hist_change) * 100 + (current_rsi - 50) / 100 + abs(ema_slope) * 50,
            )
            score = min(1.0, max(0.1, score))

            return StrategySignal(
                strategy_name=self.name,
                direction=TradeDirection.CALL,
                score=round(score, 3),
                confidence=round(min(1.0, score * 0.7), 3),
                ideal_regimes=self.ideal_regimes(),
                context={
                    "macd_hist": round(current_hist, 6),
                    "hist_change": round(hist_change, 6),
                    "rsi": round(current_rsi, 2),
                    "ema_slope": round(ema_slope * 100, 4),
                },
                reason="Bullish momentum — MACD+RSI+EMA aligned",
            )

        # Bearish momentum
        bearish_hist = current_hist < 0 and hist_change < -self._min_hist_change
        bearish_rsi = current_rsi < self._rsi_max_bear and current_rsi > 20
        bearish_ema = ema_slope < 0

        if bearish_hist and bearish_rsi and bearish_ema:
            score = min(
                1.0,
                abs(hist_change) * 100 + (50 - current_rsi) / 100 + abs(ema_slope) * 50,
            )
            score = min(1.0, max(0.1, score))

            return StrategySignal(
                strategy_name=self.name,
                direction=TradeDirection.PUT,
                score=round(score, 3),
                confidence=round(min(1.0, score * 0.7), 3),
                ideal_regimes=self.ideal_regimes(),
                context={
                    "macd_hist": round(current_hist, 6),
                    "hist_change": round(hist_change, 6),
                    "rsi": round(current_rsi, 2),
                    "ema_slope": round(ema_slope * 100, 4),
                },
                reason="Bearish momentum — MACD+RSI+EMA aligned",
            )

        return None
