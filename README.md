# Syntrix

**Sistema Operacional Quantitativo Modular Orientado a Eventos**

> "Selecionar melhor é mais importante do que prever melhor."

## O que é

O Syntrix **não é** apenas um bot de trading. É um **kernel quantitativo modular** — um framework reutilizável e production-ready para operações financeiras contextuais.

## Características

- **Event-driven** — toda comunicação via EventBus assíncrono com prioridades
- **Event sourcing** — todos os eventos persistidos em SQLite para replay e auditoria
- **State machine** — ciclo de vida operacional com validação de transições
- **Context Gate** — filtragem agressiva: payout, sessão, notícias, regime, spikes
- **Risk Engine** — stop gain/loss, drawdown, cooldown, safe mode, risk lock
- **Modular** — estratégias, brokers, analytics completamente desacoplados
- **Observável** — watchdog, métricas, structured logs, tracing completo
- **Reproduzível** — replay engine com reconstrução temporal e causal tracing

## Instalação

```bash
# Clone o repositório
cd projeto

# Instalar dependências
pip install -r requirements.txt

# (Opcional) Instalar IQ Option API
pip install iqoptionapi
```

## Uso

```bash
# Dry-run (sem broker)
python main.py --headless

# Com UI
python main.py

# Perfil específico
python main.py --profile moderado

# Intervalo customizado
python main.py --interval 30 --headless
```

## Estrutura

```
projeto/
├── core/           # EventBus, StateMachine
├── brokers/        # Abstração de broker + IQ Option
├── context/        # ContextGate, MarketRegime, SessionFilter, NewsFilter, SpikeDetector
├── risk/           # RiskEngine (stop gain/loss, drawdown, safe mode)
├── execution/      # ExecutionEngine (timing, jitter, retry)
├── strategies/     # TrendPullback, RangeReversal, Breakout, Momentum, Scoring
├── indicators/     # Indicadores técnicos (SMA, EMA, RSI, BB, MACD, ATR, Stoch, ADX)
├── analytics/      # TradeAnalytics (winrate, sharpe, profit factor, degradação)
├── event_store/    # SQLite event sourcing
├── replay/         # Replay engine (sessão, trade, causal chain)
├── logging/        # TradeLogger (snapshots completos)
├── watchdog/       # Monitoramento de threads, conexões, filas
├── ui/             # Tkinter UI leve
├── config/         # profiles.yaml + loader
├── tests/          # Testes unitários
├── scripts/        # Scripts utilitários
├── docs/           # Documentação
└── data/           # SQLite DB + trade logs
```

## Testes

```bash
python -m pytest tests/ -v
```

## Perfis

| Perfil | Risco | Status |
|--------|-------|--------|
| `leve` | Conservador | ✅ Ativo |
| `moderado` | Balanceado | ✅ Ativo |
| `agressivo` | Alto risco | ⚠️ Inativo |
| `extremo` | Máximo | ⚠️ Inativo |

## Filosofia

- Operar pouco, mas com alta qualidade
- Evitar contexto ruim ao invés de prever resultado
- Priorizar sobrevivência estatística
- Bloquear operações de baixa qualidade
- Registrar tudo, reproduzir qualquer sessão
- Preparado para múltiplos mercados futuramente

## Documentação

Veja [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) para a arquitetura completa.
