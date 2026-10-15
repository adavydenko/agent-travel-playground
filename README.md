# agent-travel-playground

Песочница для тестирования AI-агентов, которые организуют поездки на мероприятия.
MCP-сервис имитирует рейсы и гостиницы, хранит брони и проверяет согласованность поездки
и соблюдение бюджета. Реальных покупок и платежей нет.

Проект работает самостоятельно: ему не нужны репозиторий курса, Jupyter или доступ к LLM.
Можно подключить собственного агента через MCP Streamable HTTP. Встроенного агента,
решающего задачу за пользователя, нет.

Требования и границы сценария: [docs/requirements.md](docs/requirements.md).
Правила работы с кодом: [AGENTS.md](AGENTS.md).

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
docker exec agent-travel-playground python admin.py issue local-user --event vladivostok-ai
```

Последняя команда выводит личный API-токен. Подключите агента к
`http://127.0.0.1:8000/mcp` с заголовком `Authorization: Bearer <token>`.
Для Python есть [пример транспорта](examples/mcp_client.py): `connect`, `schemas`, `call`.
Он подключает MCP, но не строит план поездки. Для удалённого сервера используйте HTTPS.

## Мероприятия и повторные попытки

[config/events.json](config/events.json) содержит два примера — конференцию во Владивостоке
и форум в Казани. Редактируйте файл или добавляйте мероприятия с новым `id`.

| Поле мероприятия | Смысл |
|---|---|
| `id`, `name` | Уникальный ID и название |
| `start_date`, `end_date` | Первый и последний дни мероприятия включительно |
| `request` | Города отправления/назначения, фиксированные даты вылетов, бюджет и один пассажир |
| `flights` | Рейсы: ID, направление, города, даты вылета/прибытия, цена и доступность |
| `hotels` | Гостиницы: ID, город, цена за ночь и даты начала доступных ночей |

Даты имеют формат `YYYY-MM-DD`, деньги — целые неотрицательные числа в учебных рублях.
`direction` рейса — `outbound` или `return`, `available` — `true` или `false`.
Отсутствующая дата в `available_nights` означает занятую ночь: например, список
`["2027-03-11", "2027-03-13"]` не позволяет проживание в ночь с 12 на 13 марта.
ID рейсов и гостиниц уникальны внутри соответствующего списка мероприятия.
`request.passengers` всегда 1, поездка длится не больше 14 дней.

После правки JSON:

```bash
docker exec agent-travel-playground python admin.py check-config
docker restart agent-travel-playground
docker exec agent-travel-playground python admin.py reset local-user travel
```

Конфигурация читается при запуске сервиса. Начатая попытка сохраняет свои исходные условия,
поэтому для теста новых данных нужен `reset`. Журнал старых попыток сохраняется, токен остаётся
прежним. Для другого мероприятия после reset выполните:

```bash
docker exec agent-travel-playground python admin.py issue local-user --event kazan-engineering
```

Эта команда выдаст новый токен. `check-config` проверяет структуру, типы, даты, цены и ID,
но не гарантирует существование решения. Оба поставляемых примера выполнимы. Можно намеренно
создать невозможную поездку, чтобы проверить остановку агента без ложного успеха.

## Контракт для агента

1. `start_task` с `{"kind":"travel"}` создаёт или возвращает попытку, назначенную доступу.
2. `inspect_task` с `task_id` возвращает словесный запрос, мероприятие и каталоги.
3. `book_flight` бронирует рейс по `flight_id`; `book_hotel` — номер по `hotel_id`,
   `check_in` и `check_out`. Все вызовы после старта включают `task_id`.
4. `list_bookings` показывает брони и сумму; `cancel_booking` отменяет свою бронь по `booking_id`.
5. `finish` проверяет результат: только `passed=true` с `receipt` подтверждает успешную поездку.

Актуальные схемы аргументов доступны через MCP `tools/list`.

Прибытие должно быть до первого дня мероприятия, обратный вылет — после последнего.
Гостиница в городе назначения должна покрывать все ночи от прибытия до обратного вылета;
день выезда не добавляет ночь. Нужны ровно два рейса и одна гостиница в пределах общего бюджета.
Даты пользователя менять нельзя. Время суток, пересадки, трансферы и визы не моделируются.

Каталоги общие для мероприятия, доступность фиксирована: счётчик оставшихся мест не моделируется.
Брони принадлежат доступу. Отмена бесплатна; одинаковый повтор не создаёт дубликат, другая
гостиница или другие даты создают дополнительную бронь. Лишние брони надо отменить.
При `passed=false` можно исправить поездку. После успеха попытка закрыта, повтор finish
возвращает ту же квитанцию, пока действуют доступ и попытка. Журнал и ведомость принадлежат серверу, а не агенту.

## Запуск без Docker (из корня проекта)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r service/requirements.txt
export VERIFY_DB="$PWD/data/verification.sqlite3"
export VERIFY_CONFIG="$PWD/config/events.json"
python service/admin.py check-config
python service/admin.py issue demo-017 --event vladivostok-ai
python -m uvicorn server:app --app-dir service --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

Последняя команда остаётся в терминале. Адрес MCP — `http://127.0.0.1:8000/mcp`.
Передавайте выданный токен в заголовке `Authorization: Bearer <token>`. Примеры ниже запускайте во втором терминале с теми же
`VERIFY_DB`, `VERIFY_CONFIG` и активированным окружением:

```bash
python service/admin.py report > data/results.csv
python service/admin.py events demo-017 > data/demo-017.jsonl
python service/admin.py reset demo-017 travel
python service/admin.py revoke demo-017
```

`issue` повторно меняет токен, сохраняя попытки и назначенное мероприятие, если `--event` не указан.
Для нового доступа без `--event` выбирается первое мероприятие файла; на сдаче задавайте ID явно.
Чтобы сменить мероприятие при существующей попытке, сначала выполните `reset`, затем `issue --event ID`. Токен действует 168 часов по умолчанию;
`--hours 24` задаёт другой срок. `reset` освобождает одну работу для новой попытки и сохраняет
старые события; старый receipt больше не считается результатом текущей попытки. У каждой
попытки срок 2 часа с создания и бюджет 100 вызовов. `finish` после успеха повторно возвращает
ту же квитанцию в пределах срока доступа и попытки; закрытую попытку менять нельзя.
Сохранённый результат остаётся в ведомости оператора после истечения срока.

## Размещение на сервере

На Linux VM с Docker соберите контейнер из `Dockerfile`, подключите
постоянный том `/data`, задайте точные `VERIFY_HOSTS` и `VERIFY_ORIGINS`. Пример, где
`verify.example.edu` нужно заменить своим доменом:

```bash
docker build -t agent-travel-playground .
docker volume create agent-travel-playground-data
docker run -d --name agent-travel-playground --restart unless-stopped \
  -p 127.0.0.1:8000:8000 -v agent-travel-playground-data:/data \
  --mount "type=bind,source=$PWD/config,target=/app/config,readonly" \
  -e VERIFY_HOSTS=verify.example.edu \
  -e VERIFY_ORIGINS=https://verify.example.edu agent-travel-playground
docker exec agent-travel-playground python admin.py check-config
docker exec agent-travel-playground python admin.py issue group1-017 --event kazan-engineering
docker exec agent-travel-playground python admin.py report
```

Поставьте перед портом 8000 HTTPS reverse proxy (например, Caddy):

```text
verify.example.edu {
    reverse_proxy 127.0.0.1:8000
}
```

Proxy должен сохранять исходный Host и Authorization. Откройте снаружи только HTTPS,
обеспечьте DNS и TLS для вашего домена; настройки облака зависят от выбранной площадки.
Удалённый адрес клиента — `https://verify.example.edu/mcp`. Сделайте пробный запуск
с отдельным demo-псевдонимом извне VM до выдачи задания группе.

Это **одна реплика / один worker**, SQLite на постоянном диске и персональные Bearer-токены.
Rate limit — 120 HTTP-запросов в минуту на доступ в памяти процесса; после рестарта счётчик
сбрасывается. Размер тела ограничен 16 KiB. Хост и Origin проверяет SDK. Дополнительно
ограничьте публичный трафик на proxy и делайте резервную копию SQLite штатной backup-командой
или при остановленном контейнере (при WAL нельзя копировать только основной файл на ходу).
Без постоянного тома потеряются результаты. Нагрузочная проверка всей группы ещё нужна.

Здесь нет полноценного OAuth discovery: это сервис с заранее выданным токеном для клиентов, поддерживающих
Streamable HTTP и Bearer-заголовок. Пользователь выбирает или реализует клиент самостоятельно. Совместимость со всеми desktop MCP-клиентами
не обещается. Для интеграции с университетским SSO потребуются отдельные issuer/audience,
OAuth и проверка клиентов. Docker/TLS/облако — инструкции к размещению, не заявление о
выполненном облачном развёртывании.


## Разработка и проверка

Python 3.11+; сервер в Docker использует Python 3.13. Зависимости сервиса закреплены
в `service/requirements.txt`. После создания окружения по инструкции выше:

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

Тесты проверяют конфигурацию, бронирование, бюджет, отмену, доступы и настоящий локальный
MCP по HTTP. Для HTTP-тестов нужны локальные сокеты. Тесты используют временные БД и токены.
Они не вызывают LLM. Проверка собственного агента с реальной моделью — отдельный запуск.

- `service/` — MCP, состояние SQLite, правила поездки и команды оператора.
- `config/` — редактируемые мероприятия.
- `examples/` — минимальный Python-транспорт для подключения агента.
- `tests/` — автономные проверки сервиса.

Оператор может применять песочницу для учебной проверки: выдать личные токены и мероприятия,
затем сверить результаты через `admin.py report` и `admin.py events`. Сервер подтверждает
операции под доступом; сам по себе журнал не доказывает авторство кода или участие LLM.
