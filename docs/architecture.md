# architecture.md — архитектура решения

## 1) Цель системы

- Автоматизировать обработку части тикетов поддержки: классификация, риск-оценка, маршрутизация, подготовка черновика ответа.
- Сохранить безопасность и качество: рискованные/неуверенные случаи — только через оператора.
- Обеспечить аудит: каждое автоматическое решение воспроизводимо и логируется.

## 2) Основные компоненты (как в целевом продукте)

- Ingestion (каналы): чат / email / web / mobile → нормализация в единый формат Ticket.
- PII/Safety фильтр: детект и маскирование PII, policy-валидация, защита от prompt-injection (на уровне контента).
- Fast Path Classifier (≤500 ms): тема/категория + risk_level + confidence.
- Retrieval: поиск похожих тикетов и/или фрагментов базы знаний (KB).
- Decision Engine: правила принятия решения (auto-suggest / route-to-human / auto-close safe).
- Async Worker: генерация черновика ответа, суммаризация, дедупликация при инцидентах, пересчёт embeddings.
- Storage:
  - Ticket Store (сырьё и статусы)
  - KB Store (контент + версии)
  - Vector Store (эмбеддинги KB/тикетов)
  - Audit Log (неизменяемые логи решений)
- Human-in-the-loop UI/Queue: очередь операторов, интерфейс подтверждения/редактирования.
- Monitoring/Analytics: метрики, алерты, дашборды, контроль LLM cost.

## 2.1) Что реализовано в этом репозитории (PoC as-built)

- Backend service: FastAPI-приложение, принимающее тикеты по REST и раздающее события по SSE.
- Worker: фоновая обработка тикетов (topic classification → risk policy → routing → авто-завершение или эскалация).
- Storage: SQLite (`data/tickets.db`) для тикетов и истории событий.
- Model inference:
  - по умолчанию: локальная NB-модель (`model_service/model.json`)
  - опционально: внешний OpenAI-compatible LLM для topic classification (с деградацией на локальную модель)
- Desktop client (Tkinter): генератор тикетов из `client/sample_tickets.json` + офлайн очередь (`pending`) + допередача + UI-статистика.
- Supervisor: простой “watchdog” для автоперезапуска backend-процесса.

## 3) Поток данных (end-to-end)

### 3.1 Синхронный путь (hot path)

1. Принять тикет из канала и нормализовать.
2. Прогнать через PII/Safety фильтр (минимум: маскирование и policy).
3. Быстрая классификация: topic + risk + confidence.
4. Retrieval (упрощённо/кэшируемо) для получения evidence.
5. Decision Engine:
   - safe + high-confidence → можно сформировать предложение (draft) или авто-маршрут.
   - risky или low-confidence → route-to-human.
6. Записать Audit Log.
7. Ответить upstream-системе: статус + маршрут (и при необходимости “черновик готовится”).

### 3.2 Асинхронный путь (slow path)

- Если разрешено политикой: сформировать draft-ответ через LLM/RAG.
- Сохранить draft + источники (evidence) + версии промпта/модели в Audit Log.
- Доставить draft оператору (suggest-mode), либо пользователю (только для safe категорий и только при выполнении политики качества).

## 4) Human-in-the-loop и запасные пути

- Human-in-the-loop:
  - risky категории (оплата, аккаунт, юридические угрозы, безопасность)
  - low-confidence (ниже порога)
  - подозрение на prompt injection / PII policy violation
- Fallback при недоступности LLM:
  - не генерировать ответ; оставить только классификацию+маршрутизацию
  - использовать шаблонные ответы без персонализации
  - деградировать retrieval до простого keyword search

## 5) Хранилища и аудит

- Audit Log должен содержать:
  - вход (обезличенный/маскированный), метаданные
  - результат классификации и confidence
  - источники retrieval (ID документов/тикетов, score)
  - решение (route/auto/suggest) и причина (policy)
  - версии моделей/правил/промптов/конфигов

## 6) Диаграммы (Mermaid)

```mermaid
flowchart LR
  A[Desktop client / API caller] --> B[REST: POST /tickets]
  B --> C[(SQLite: tickets + events)]
  B --> D[Worker loop]
  D --> E[PII mask + risk signals]
  E --> F[Topic classifier (NB / LLM fallback)]
  F --> G[Routing policy]
  G -->|safe| H[Auto-resolve]
  G -->|risky/low conf| I[Escalate (PENDING_REVIEW)]
  H --> C
  I --> C
  C --> J[SSE: GET /events]
  J --> A
```

## 7) Assumptions и границы PoC

- Реально реализовано:
  - REST API + состояние тикета (`NEW/PROCESSING/RESOLVED/PENDING_REVIEW`)
  - topic classification (локальная NB, опционально внешний LLM) с confidence
  - risk policy (правила), деградация в `PENDING_REVIEW` при risk/low-confidence
  - журнал событий (SSE) и персистентность в SQLite
  - desktop client с генерацией и офлайн-очередью
- Только дизайн (не реализовано полностью):
  - полноценный retrieval/RAG по базе знаний и векторному индексу
  - полноценная MLOps-инфраструктура (registry, canary, drift detection в проде)
  - интеграция с промышленным HelpDesk/CRM (webhooks, IAM, RBAC)
- Пороги и SLO (PoC):
  - цель hot path: p95 < 500 ms (в PoC оценивается по логам и локальному запуску)
  - `confidence_threshold`: конфигурируемый порог, ниже которого тикет эскалируется оператору
