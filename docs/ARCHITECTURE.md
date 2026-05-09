# Syntrix — Arquitetura

## Visão Geral

O Syntrix é um **Sistema Operacional Quantitativo Modular Orientado a Eventos** para operações financeiras.

**Filosofia central:** "Selecionar melhor é mais importante do que prever melhor."

## Princípios

- **Operar pouco** — filtrar agressivamente contextos ruins
- **Sobrevivência estatística** — preservar capital como prioridade
- **Observabilidade total** — tudo é registrado e reproduzível
- **Desacoplamento** — cada módulo é independente
- **Event-driven** — comunicação 100% via eventos

## Arquitetura de Módulos

```
┌─────────────────────────────────────────────────────────────┐
│                        SYNTRIX                               │
│                                                              │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐                │
│  │  Config   │   │   UI     │   │ Watchdog │                │
│  │ Profiles  │   │ (Tkinter)│   │ Monitor  │                │
│  └──────────┘   └──────────┘   └──────────┘                │
│        │               │               │                     │
│  ══════╪═══════════════╪═══════════════╪═════════════════   │
│        │          EVENT BUS (async, priority)                │
│  ══════╪═══════════════╪═══════════════╪═════════════════   │
│        │               │               │                     │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐                │
│  │  Context  │   │   Core   │   │   Risk   │                │
│  │   Gate    │   │  States  │   │  Engine  │                │
│  └──────────┘   └──────────┘   └──────────┘                │
│        │               │               │                     │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐                │
│  │Strategies│   │Execution │   │Analytics │                │
│  │ Scoring  │   │  Engine  │   │  Engine  │                │
│  └──────────┘   └──────────┘   └──────────┘                │
│        │               │               │                     │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐                │
│  │ Brokers  │   │  Event   │   │  Replay  │                │
│  │ (IQ Opt) │   │  Store   │   │  Engine  │                │
│  └──────────┘   └──────────┘   └──────────┘                │
└─────────────────────────────────────────────────────────────┘
```

## Fluxo de Decisão

```
Candle → Context Gate → Strategy → Scoring → Risk → Execution → Result
           ↓                                                    ↓
         BLOCK?                                             Analytics
           ↓                                                    ↓
        Trade Log                                           Event Store
```

1. **Candle recebido** via broker
2. **Context Gate** avalia: sessão, payout, regime, volatilidade, notícias, spikes
3. Se ALLOW → **Estratégias** avaliam sinais
4. **Scoring Engine** ajusta score contextualmente
5. **Risk Engine** valida limites de risco
6. **Execution Engine** executa com jitter e timing controlado
7. **Resultado** atualiza analytics, risk, scoring
8. **Tudo** é persistido via Event Store para replay

## State Machine

```
IDLE → SCANNING → WAITING_CONTEXT → READY → EXECUTING → COOLDOWN → SCANNING
  ↓        ↓            ↓              ↓         ↓          ↓
  └────────┴────────────┴──────────────┴─────────┴──────────┘
                              ↓
                    RISK_LOCK ↔ SAFE_MODE
```

## Event Types

| Categoria | Eventos |
|-----------|---------|
| Core | SYSTEM_START, SYSTEM_STOP, SYSTEM_ERROR |
| State | STATE_ENTER, STATE_EXIT, STATE_CHANGED |
| Market | CANDLE_RECEIVED, PAYOUT_UPDATED |
| Context | CONTEXT_EVALUATED, CONTEXT_BLOCKED, REGIME_CHANGED |
| Strategy | SIGNAL_GENERATED, SIGNAL_SCORED, SIGNAL_REJECTED |
| Risk | RISK_CHECK_PASSED/FAILED, RISK_LOCK, SAFE_MODE |
| Execution | TRADE_REQUESTED, TRADE_EXECUTED, TRADE_RESULT |
| Broker | BROKER_CONNECTED, BROKER_DISCONNECTED, HEARTBEAT |
| Analytics | ANALYTICS_UPDATED, DEGRADATION_DETECTED |
| Watchdog | WATCHDOG_ALERT, THREAD_STUCK, QUEUE_OVERFLOW |

## Profiles

| Perfil | Stop Gain | Stop Loss | Max DD | Trades/h | Score Min |
|--------|-----------|-----------|--------|----------|-----------|
| Leve | $30 | -$15 | 5% | 5 | 0.65 |
| Moderado | $50 | -$30 | 10% | 10 | 0.55 |
| Agressivo* | $100 | -$50 | 15% | 15 | 0.45 |
| Extremo* | $200 | -$100 | 25% | 20 | 0.35 |

*\* Inativos por padrão*

## Tecnologias

- **Python 3.10+** — linguagem principal
- **SQLite (WAL)** — persistência de eventos
- **Tkinter** — UI leve
- **Threading + ThreadPoolExecutor** — concorrência
- **PriorityQueue** — dispatch de eventos
- **YAML** — configuração

**NÃO usa:** TensorFlow, PyTorch, LSTM, RL, CNN, Streamlit, dashboards pesados.
