"""
Trend Pullback (Reversão) — M1 binary options.

Lógica para M1:
Em timeframes curtos (1 minuto), o preço tende a CORRIGIR após movimentos esticados.
NÃO seguimos a tendência — entramos na CORREÇÃO.

REGRA FUNDAMENTAL:
- Mercado SUBIU demais (RSI alto + tendência de alta) → PUT (vai corrigir para baixo)
- Mercado CAIU demais (RSI baixo + tendência de baixa) → CALL (vai corrigir para cima)

Confirmações:
1. RSI em zona de exaustão (>65 para PUT, <35 para CALL)
2. RSI começando a VIRAR (prev_rsi mostra início da reversão)
3. Último candle mostra REJEIÇÃO (pavio contra a tendência ou candle de reversão)
4. Tendência existiu (EMAs separadas = houve movimento a ser corrigido)
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
    PUT: Mercado subiu (tendência alta + RSI > 65) → correção para baixo
    CALL: Mercado caiu (tendência baixa + RSI < 35) → correção para cima
    """
    r = scan.current_rsi
    prev_r = scan.prev_rsi

    direction = ""
    rsi_quality = 0.0

    # PUT — mercado subiu demais, vai corrigir para baixo
    # RSI alto (>65) + tendência de alta + RSI começando a cair + candle bearish
    if (scan.trend_up
            and r > 65
            and r < prev_r
            and scan.last_candle_bearish):
        direction = "put"
        # Quanto mais alto o RSI, melhor o sinal de reversão
        rsi_quality = min(1.0, (r - 60) / 25.0)

    # CALL — mercado caiu demais, vai corrigir para cima
    # RSI baixo (<35) + tendência de baixa + RSI começando a subir + candle bullish
    elif (scan.trend_down
              and r < 35
              and r > prev_r
              and scan.last_candle_bullish):
        direction = "call"
        rsi_quality = min(1.0, (40 - r) / 25.0)

    if not direction:
        return None

    # Scoring
    score = 0.0

    # RSI exaustão (+0.35 max) — quanto mais extremo, melhor
    score += rsi_quality * 0.35

    # Trend strength (+0.25 max) — houve movimento forte a ser corrigido
    trend_str = scan.trend_strength
    if trend_str < 0.0001:
        return None  # Sem tendência = sem movimento para corrigir
    trend_score = min(1.0, trend_str / 0.003)
    score += trend_score * 0.25

    # Candle de reversão (+0.20 max) — corpo forte na direção da correção
    body_score = min(1.0, scan.last_candle_body_ratio / 0.5)
    score += body_score * 0.20

    # Payout (+0.10 max)
    if scan.payout >= 0.80:
        score += 0.10
    elif scan.payout >= 0.70:
        score += 0.07
    elif scan.payout >= 0.60:
        score += 0.05

    # Wick rejection bonus (+0.10 max) — pavio longo contra a tendência = rejeição
    if scan.candles:
        last = scan.candles[-1]
        candle_range = last.high - last.low
        if candle_range > 0:
            if direction == "put":
                # Pavio superior longo = rejeição de alta
                upper_wick = last.high - max(last.open, last.close)
                wick_ratio = upper_wick / candle_range
                score += min(0.10, wick_ratio * 0.20)
            else:
                # Pavio inferior longo = rejeição de baixa
                lower_wick = min(last.open, last.close) - last.low
                wick_ratio = lower_wick / candle_range
                score += min(0.10, wick_ratio * 0.20)

    return Signal(
        asset=scan.asset,
        direction=direction,
        strategy="trend_pullback",
        score=round(score, 4),
        payout=scan.payout,
        rsi=r,
        atr=scan.current_atr,
    )
