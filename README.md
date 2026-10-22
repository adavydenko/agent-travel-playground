# agent-travel-playground

Песочница для тестирования AI-агентов, которые организуют поездки на мероприятия.
MCP-сервис отдаёт каталоги мероприятий, рейсов и гостиниц, принимает регистрации и брони.
Реальных покупок нет. Бриф задаётся агенту снаружи; автоматическая оценка итога в MCP пока нет.

## Запуск MCP без Docker

Python 3.11+. Из корня проекта:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r service/requirements.txt
python service/admin.py new-token local-user
python service/server.py
```

По умолчанию: БД `data/verification.sqlite3`, конфиг `config/events.json`
(переопределение — `VERIFY_DB` / `VERIFY_CONFIG`). Конфиг проверяется при старте сервера.

`new-token` печатает Bearer-токен. Подключите агента к `http://127.0.0.1:8000/mcp` с заголовком
`Authorization: Bearer <token>`. Для удалённого сервера нужен HTTPS.

Минимальный Python-транспорт: [examples/mcp_client.py](examples/mcp_client.py)
(`connect`, `schemas`, `call`). Он не строит план поездки.

После правки `config/events.json` перезапустите сервер и сбросьте попытку
(`admin.py reset … travel`): снимок мира фиксируется при первом вызове инструмента.

## Конфигурация

[config/events.json](config/events.json) — плоский мир: `events`, `flights`, `hotels`.
Даты — `YYYY-MM-DD`, цены — целые учебные рубли. Подробности полей — в требованиях.

Примеры брифов (не загружаются сервером): [config/golden-tasks.json](config/golden-tasks.json) —
`easy` / `middle` / `hard`. В промпт агенту копируйте только `prompt` (без `event_id`).
На `hard` прямого рейса в город мероприятия нет: нужна пересадка (несколько броней рейсов).

Опционально без запуска сервера: `python service/admin.py check-config`.

## Инструменты агента

Токен = попытка. Бриф передайте агенту сами (например из `golden-tasks.json`); через MCP его нет.

1. `list_events` / `list_flights` / `list_hotels`
2. `register_for_event` (`name`; `email` если нужно)
3. `list_registrations` / `cancel_registration`
4. `book_flight` / `book_hotel` / `list_bookings` / `cancel_booking`

Схемы — через MCP `tools/list`. Инструментов `inspect_task` и `finish` нет.

## Команды оператора

```bash
python service/admin.py report
python service/admin.py events local-user
python service/admin.py reset local-user travel
python service/admin.py revoke local-user
```

`new-token` повторно меняет токен, сохраняя попытку. По умолчанию токен действует 168 часов
(`--hours 24` — другой срок). У попытки — 2 часа с первого вызова и бюджет 100 вызовов.

## Docker (опционально)

```bash
docker build -t agent-travel-playground .
docker volume create agent-travel-playground-data
docker run -d --name agent-travel-playground \
  -p 127.0.0.1:8000:8000 -v agent-travel-playground-data:/data \
  --mount "type=bind,source=$PWD/config,target=/app/config,readonly" \
  agent-travel-playground
docker exec agent-travel-playground python admin.py new-token local-user
```

На сервере: один worker, постоянный том `/data`, reverse proxy с HTTPS,
`VERIFY_HOSTS` / `VERIFY_ORIGINS` под ваш домен. Снаружи открывайте только HTTPS.

## Разработка

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

Тесты покрывают конфигурацию, брони, RSVP, доступы и локальный MCP по HTTP; LLM не вызывают.
