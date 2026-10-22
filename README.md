# agent-travel-playground

Песочница для тестирования AI-агентов, которые организуют поездки на мероприятия.
MCP-сервис отдаёт каталоги мероприятий, рейсов и гостиниц, принимает регистрации (RSVP)
и брони. Реальных покупок и платежей нет. Автоматическая проверка итога (grader) пока
не входит в MCP агента: бриф задаётся снаружи, оценку делает оператор.

Проект работает самостоятельно: ему не нужны репозиторий курса, Jupyter или доступ к LLM.
Можно подключить собственного агента через MCP Streamable HTTP. Встроенного агента,
решающего задачу за пользователя, нет.

Требования и границы сценария: [docs/requirements.md](docs/requirements.md).
Правила работы с кодом: [AGENTS.md](AGENTS.md).

## Прототип интерфейса

Интерактивный прототип диспетчерской находится в
[_dev/ui-prototype/](_dev/ui-prototype/README.md): обзор мероприятий, рейсов и ночей
гостиниц, связанные сессии и брони, журнал, проверки и доступы агентов.
В демо можно задать вместимость и заранее занятую ночь, чтобы увидеть выбор другой гостиницы.
Запуск из корня проекта:

```bash
python3 -m http.server 8765 --bind 127.0.0.1 --directory _dev/ui-prototype
```

Откройте [локальный прототип](http://127.0.0.1:8765/). Он работает с демоданными
в браузере, не подключён к MCP и не выдаёт действующие токены.
Предлагаемая модель нескольких сессий одного агента описана в
[_dev/ui-prototype/DESIGN.md](_dev/ui-prototype/DESIGN.md) и пока не меняет контракт сервиса.
Для сравнения сохранены [исходный интерфейс и варианты](_dev/ui-prototype/alternatives/index.html).
Новая [операторская на shadcn/ui](_dev/interaction-lab/operator-ui/README.md) связывает
среду, сессию, нарушение, бронь и вызов; это отдельный прототип, основной A сохранён.

## Быстрый старт с Docker

Из корня этого проекта, при работающем Docker:

```bash
docker build -t agent-travel-playground .
docker volume create agent-travel-playground-data
docker run -d --name agent-travel-playground \
  -p 127.0.0.1:8000:8000 -v agent-travel-playground-data:/data \
  --mount "type=bind,source=$PWD/config,target=/app/config,readonly" \
  agent-travel-playground
docker exec agent-travel-playground python admin.py check-config
docker exec agent-travel-playground python admin.py issue local-user
```

Последняя команда выводит личный API-токен. Подключите агента к
`http://127.0.0.1:8000/mcp` с заголовком `Authorization: Bearer <token>`.
Для Python есть [пример транспорта](examples/mcp_client.py): `connect`, `schemas`, `call`.
Он подключает MCP, но не строит план поездки. Для удалённого сервера используйте HTTPS.

## Конфигурация мира

[config/events.json](config/events.json) — плоский мир: `events`, `flights`, `hotels`.
Два примера мероприятий: конференция во Владивостоке и форум в Казани.

| Список | Поля |
|---|---|
| `events` | `id`, `name`, `description`, `location`, `start_date`, `end_date`, `email_required` |
| `flights` | `id`, `origin`, `destination`, `depart_date`, `arrive_date`, `price_rub` |
| `hotels` | `id`, `name`, `location`, `nightly_price_rub`, `available_nights` |

Даты — `YYYY-MM-DD`, деньги — целые неотрицательные числа в учебных рублях.
Рейс в каталоге можно бронировать. Отсутствующая дата в `available_nights` означает
занятую ночь. ID уникальны внутри каждого списка. `location` события и гостиницы
сравниваются как строки; агент сам связывает их с `origin`/`destination` рейсов.

После правки JSON:

```bash
docker exec agent-travel-playground python admin.py check-config
docker restart agent-travel-playground
docker exec agent-travel-playground python admin.py reset local-user travel
```

Конфигурация читается при запуске сервиса. Начатая попытка сохраняет снимок мира при
первом вызове инструмента, поэтому для теста новых данных нужен `reset`. Журнал старых
попыток сохраняется. `check-config` проверяет структуру, типы, даты, цены и ID.

## Контракт для агента

Токен = попытка. Отдельный `start_task` / `task_id` не нужны. Бриф пользователь передаёт
агенту сам (не через MCP).

1. `list_events` / `list_flights` / `list_hotels` — просмотр каталогов.
2. `register_for_event` — одна регистрация на одного участника (`name`; `email` если нужно).
3. `list_registrations` / `cancel_registration` — свои регистрации.
4. `book_flight` / `book_hotel` — брони; `list_bookings` / `cancel_booking` — просмотр и отмена.

Актуальные схемы — через MCP `tools/list`.

Инструментов `inspect_task` и `finish` нет: сервис не сообщает агенту «поездка принята».
Ориентир для ручной оценки: прибытие до начала мероприятия, отель в `event.location`,
ночёвки покрывают событие, рейсы обрамляют пребывание; check-in не раньше прибытия,
checkout не позже отъезда. Время 14:00 / 12:00 — умолчание для повествования.

Брони и регистрации принадлежат доступу. Отмена бесплатна; одинаковый повтор брони не
создаёт дубликат. Несколько регистраций API допускает (оценка по брифу — снаружи).

## Запуск без Docker (из корня проекта)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r service/requirements.txt
export VERIFY_DB="$PWD/data/verification.sqlite3"
export VERIFY_CONFIG="$PWD/config/events.json"
python service/admin.py check-config
python service/admin.py issue demo-017
python -m uvicorn server:app --app-dir service --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

Адрес MCP — `http://127.0.0.1:8000/mcp`. Токен — в `Authorization: Bearer <token>`.
Во втором терминале с теми же переменными:

```bash
python service/admin.py report > data/results.csv
python service/admin.py events demo-017 > data/demo-017.jsonl
python service/admin.py reset demo-017 travel
python service/admin.py revoke demo-017
```

`issue` повторно меняет токен, сохраняя попытку. Токен действует 168 часов по умолчанию
(`--hours 24` — другой срок). `reset` открывает новую попытку и сохраняет старый журнал.
У попытки срок 2 часа с первого вызова и бюджет 100 вызовов.

## Размещение на сервере

На Linux VM с Docker соберите контейнер из `Dockerfile`, подключите
постоянный том `/data`, задайте точные `VERIFY_HOSTS` и `VERIFY_ORIGINS`. Пример:

```bash
docker build -t agent-travel-playground .
docker volume create agent-travel-playground-data
docker run -d --name agent-travel-playground --restart unless-stopped \
  -p 127.0.0.1:8000:8000 -v agent-travel-playground-data:/data \
  --mount "type=bind,source=$PWD/config,target=/app/config,readonly" \
  -e VERIFY_HOSTS=verify.example.edu \
  -e VERIFY_ORIGINS=https://verify.example.edu agent-travel-playground
docker exec agent-travel-playground python admin.py check-config
docker exec agent-travel-playground python admin.py issue group1-017
docker exec agent-travel-playground python admin.py report
```

Поставьте перед портом 8000 HTTPS reverse proxy (например, Caddy):

```text
verify.example.edu {
    reverse_proxy 127.0.0.1:8000
}
```

Proxy должен сохранять исходный Host и Authorization. Откройте снаружи только HTTPS.
Удалённый адрес клиента — `https://verify.example.edu/mcp`.

Это **одна реплика / один worker**, SQLite на постоянном диске и персональные Bearer-токены.
Rate limit — 120 HTTP-запросов в минуту на доступ в памяти процесса. Размер тела — 16 KiB.
Без постоянного тома потеряются результаты.

Здесь нет полноценного OAuth discovery: заранее выданный токен для клиентов с Streamable HTTP
и Bearer. Совместимость со всеми desktop MCP-клиентами не обещается.

## Разработка и проверка

Python 3.11+; сервер в Docker использует Python 3.13. Зависимости сервиса —
в `service/requirements.txt`. После создания окружения:

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

Тесты проверяют конфигурацию, бронирование, RSVP, доступы и локальный MCP по HTTP.
Они не вызывают LLM.

- `service/` — MCP, состояние SQLite, правила операций и команды оператора.
- `config/` — редактируемый мир мероприятий, рейсов и гостиниц.
- `examples/` — минимальный Python-транспорт для подключения агента.
- `tests/` — автономные проверки сервиса.

Оператор выдаёт токены и сверяет журнал через `admin.py report` и `admin.py events`.
Сервер подтверждает операции под доступом; журнал не доказывает авторство кода или участие LLM.
