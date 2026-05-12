"""
Momentum Reversal — M1 binary options.

Lógica para M1:
Detecta quando um impulso forte está ESGOTANDO e entra na reversão.
Diferente do Trend Pullback, aqui o RSI está em ZONA EXTREMA (>70 ou <30).

REGRA FUNDAMENTAL:
- RSI > 70 (sobrecomprado extremo) + candle de rejeição → PUT
- RSI < 30 (sobrevendido extremo) + candle de rejeição → CALL

Não precisa de tendência consistente — o próprio RSI extremo indica exaustão.
O candle de reversão CONFIRMA que a virada começou.
"""

from __future__ import annotations

from typing import Optional

from scanner.scanner import ScanResult
from strategies.trend_pullback import Signal


def evaluate(scan: ScanResult) -> Optional[Signal]:
    """
    PUT: RSI > 70 (sobrecomprado) + RSI virando + candle bearish → reversão para baixo
    CALL: RSI < 30 (sobrevendido) + RSI virando + candle bullish → reversão para cima
    """
    r = scan.current_rsi
    prev_r = scan.prev_rsi

    direction = ""
    exhaustion_strength = 0.0

    # PUT — sobrecomprado extremo, vai reverter para baixo
    if (r > 70
            and r < prev_r
            and scan.last_candle_bearish):
        direction = "put"
        exhaustion_strength = min(1.0, (r - 65) / 25.0)

    # CALL — sobrevendido extremo, vai reverter para cima
    elif (r < 30
              and r > prev_r
              and scan.last_candle_bullish):
        direction = "call"
        exhaustion_strength = min(1.0, (35 - r) / 25.0)

    if not direction:
        return None

    # Scoring
    score = 0.0

    # RSI extremo (+0.35 max) — quanto mais extremo, maior a chance de reversão
    score += exhaustion_strength * 0.35

    # RSI turning speed (+0.20 max) — quão rápido o RSI está virando
    rsi_turn = abs(r - prev_r)
    turn_score = min(1.0, rsi_turn / 5.0)
    score += turn_score * 0.20

    # Candle de reversão (+0.25 max) — corpo forte na direção da reversão
    body_score = min(1.0, scan.last_candle_body_ratio / 0.5)
    score += body_score * 0.25

    # Payout (+0.10 max)
    if scan.payout >= 0.80:
        score += 0.10
    elif scan.payout >= 0.70:
        score += 0.07
    elif scan.payout >= 0.60:
        score += 0.05

    # Wick rejection bonus (+0.10 max)
    if scan.candles:
        last = scan.candles[-1]
        candle_range = last.high - last.low
        if candle_range > 0:
            if direction == "put":
                upper_wick = last.high - max(last.open, last.close)
                wick_ratio = upper_wick / candle_range
                score += min(0.10, wick_ratio * 0.20)
            else:
                lower_wick = min(last.open, last.close) - last.low
                wick_ratio = lower_wick / candle_range
                score += min(0.10, wick_ratio * 0.20)

    return Signal(
        asset=scan.asset,
        direction=direction,
        strategy="momentum_reversal",
        score=round(score, 4),
        payout=scan.payout,
        rsi=r,
        atr=scan.current_atr,
    )
