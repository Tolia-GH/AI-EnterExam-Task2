# Развёртывание и запуск (backend + модель + desktop client)

## 1) Установка зависимостей

```bash
python -m pip install -r requirements.txt
```

## 2) (Опционально) Обучить локальную модель классификации тем

По умолчанию backend использует локальную NB-модель, загружая артефакт `model_service/model.json`. Если файла нет — сначала обучите модель:

```bash
python model_training/train.py --log-level INFO
```

Артефакты:
- `model_service/model.json`
- `model_training/training_report.json`

## 3) Запуск backend-сервиса тикетов (режим демона + автоперезапуск)

Рекомендуемый способ — запуск через supervisor (если uvicorn-процесс падает, он будет поднят снова):

```bash
python service/supervisor.py --host 127.0.0.1 --port 18000
```

Основные REST/SSE endpoints:
- `POST /tickets` — создать тикет
- `GET /tickets/{ticket_id}` — получить тикет
- `GET /tickets` — список тикетов
- `GET /metrics` — агрегированные метрики (throughput, доли статусов, доля эскалаций)
- `GET /events` — SSE-стрим событий (HTTP long connection) для клиентского UI

Хранилище:
- SQLite база: `data/tickets.db`

## 4) Запуск desktop client (Windows/macOS)

```bash
python client/desktop_client.py --api-base http://127.0.0.1:18000 --gen-per-min 30
```

Что делает клиент:
- Генерирует тикеты из источника `client/sample_tickets.json` (раздел `templates`)
- Отправляет тикеты на backend по HTTP
- При недоступности backend складывает тикеты в `client/sample_tickets.json` (раздел `pending`) и автоматически доправляет после восстановления
- Подписывается на `/events` (SSE) и обновляет статистику по статусам (auto-resolved / escalated / processing)

## 5) Нагрузочная проверка (эмуляция высоких объёмов)

```bash
python scripts/load_test.py --api-base http://127.0.0.1:18000 --threads 10 --tickets-per-thread 50
```

## 6) (Опционально) Подключение внешнего LLM для topic classification

Backend умеет использовать OpenAI-compatible API в роли классификатора тем. Для этого задайте переменные окружения:
- `LLM_BASE_URL` (например, `https://api.openai.com`)
- `LLM_API_KEY`
- `LLM_MODEL`

Если переменные не заданы или LLM недоступен, система автоматически деградирует на локальную NB-модель.
