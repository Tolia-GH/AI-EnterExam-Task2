# PoC: примеры входов и ожидаемое поведение

В репозитории есть два “уровня” демонстрации:
- **PoC-пайплайн** (консольный): `python main.py` обрабатывает небольшой набор тикетов из `data/sample_tickets.json` и пишет аудит в `logs/audit.jsonl`.
- **Сервис + desktop client**: клиент генерирует тикеты из `client/sample_tickets.json` и отправляет их в backend (REST), получая статусы через SSE (`/events`).

## 1) Входы для консольного PoC-пайплайна

Файл: [data/sample_tickets.json](file:///c:/Users/Tolia/Documents/GitHub/AI-EnterExam-Task2/data/sample_tickets.json)

Пример 1 (happy / safe):
- Пользователь просит подсказать, как действовать при задержке доставки.
- Ожидание: `topic=order_delivery`, `risk=safe`, действие `AUTO_SUGGEST` или “подсказка оператору”.

Пример 2 (risky / эскалация):
- Пользователь описывает проблему с оплатой/возвратом и/или содержит PII.
- Ожидание: `topic=payment`, `risk=risky`, действие `ROUTE_TO_HUMAN_PAYMENT`.

## 2) Входы для desktop client (генератор)

Файл: [client/sample_tickets.json](file:///c:/Users/Tolia/Documents/GitHub/AI-EnterExam-Task2/client/sample_tickets.json)

Разделы:
- `templates` — шаблоны, из которых клиент генерирует тикеты
- `tickets` — история отправленных тикетов (локальная)
- `pending` — очередь тикетов “на допередачу” при недоступности backend

## 3) Ожидаемые “выходы” (так как CLI не печатает JSON)

### 3.1 Стандартизованные workflow-логи

Программа печатает только строки вида:

`[timestamp] [LEVEL] [module] message`

Ключевые точки, которые должны присутствовать:
- старт процесса и параметры (`api_base`, пути к данным)
- создание/обработка тикета (ticket_id, topic, risk, confidence)
- решение маршрутизации (`route_action`) и причина (`reason`)
- деградация (fallback) при ошибках модели/сетевых таймаутах

### 3.2 Аудит (для консольного PoC)

Файл: `logs/audit.jsonl`
- 1 строка JSON на тикет
- обязательные поля: `ticket_id`, `topic`, `risk_level`, `confidence`, `action`, `reason`, `evidence`, `versions`
- PII должен быть замаскирован/удалён в `input_text_masked`

### 3.3 Статусы в backend + клиенте

Backend хранит тикеты в SQLite (`data/tickets.db`) и меняет статус:
- `NEW` → `PROCESSING` → `RESOLVED` (автообработка safe)
- `NEW` → `PROCESSING` → `PENDING_REVIEW` (эскалация для risky/low-confidence)

Desktop client показывает агрегаты:
- Submitted (сколько тикетов отправлено)
- Pending upload (локальная очередь при сбоях)
- Auto resolved (RESOLVED)
- Escalated (PENDING_REVIEW)
