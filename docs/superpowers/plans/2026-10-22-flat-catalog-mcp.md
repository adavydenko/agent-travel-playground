# Flat Catalog and Student MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reshape config into a flat `{events, flights, hotels}` world and expose a student MCP of browse + RSVP + book only (no inspect/finish oracle); grader deferred.

**Architecture:** `catalog.py` validates and loads one shared world. Each operator-issued token owns one `travel` attempt whose state snapshots that world plus `bookings` and `registrations`. `store.call` auto-creates the attempt on first tool use (no `start_task`, no `task_id` in args). `travel.apply` implements list/register/book tools only. MCP wrappers in `server.py` match. Docs/requirements describe the new contract; automated grade stays out.

**Tech Stack:** Python 3, Pydantic, FastMCP, SQLite, pytest, httpx

## Global Constraints

- Follow [docs/superpowers/specs/2026-10-22-flat-catalog-mcp-design.md](../specs/2026-10-22-flat-catalog-mcp-design.md)
- Stay independent of course/notebook/LLM/agent framework ([AGENTS.md](../../../AGENTS.md))
- Sync requirements, README, MCP descriptions, examples, and tests with behavior changes
- Do not delete user journal data for test convenience; tests use temp DB/config
- No grader / `finish` / receipt issuance in this pass
- Prefer short explicit code; no new architectural layers

## File map

| File | Responsibility |
|---|---|
| `service/catalog.py` | Pydantic models + `load_catalog` → world dict |
| `config/events.json` | Flat example world (2 events, shared flights/hotels) |
| `service/travel.py` | Attempt snapshot, list/RSVP/book apply logic |
| `service/store.py` | Issue without event pin; auto attempt; tool dispatch without `task_id` |
| `service/server.py` | MCP tool surface |
| `service/admin.py` | `check-config` / `issue` without required `--event` |
| `docs/requirements.md` | Product contract |
| `README.md` | Operator and agent usage |
| `tests/test_config.py` | Catalog validation + snapshot reload |
| `tests/test_store.py` | Bookings, RSVP, quotas, isolation |
| `tests/test_http.py` | Live MCP tool set and two principals |

---

### Task 1: Flat catalog models and example config

**Files:**
- Modify: `service/catalog.py`
- Modify: `config/events.json`
- Modify: `tests/test_config.py`
- Modify: `service/admin.py` (`check-config` print loop only as needed for new return shape)

**Interfaces:**
- Produces: `load_catalog(path=None) -> dict` with keys `events`, `flights`, `hotels` (lists of dicts from `model_dump()`)
- Produces models: `Event`, `Flight`, `Hotel`, `Catalog` as in the spec (no `Request`, no nested catalogs, no flight `available`/`direction`)

- [ ] **Step 1: Rewrite failing config tests for the flat world**

Replace `tests/test_config.py` with tests that assume flat lists. Keep a `config` fixture that copies `config/events.json` to `tmp_path`.

```python
"""Flat world config: validation, uniqueness, snapshot independence."""
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / 'service'
sys.path.insert(0, str(SERVICE))
from catalog import load_catalog
from store import Store
from travel import apply, new_attempt


@pytest.fixture
def config(tmp_path):
    data = json.loads((ROOT / 'config/events.json').read_text())
    path = tmp_path / 'events.json'
    path.write_text(json.dumps(data))
    return path, data


def test_load_catalog_returns_flat_world():
    world = load_catalog()
    assert set(world) == {'events', 'flights', 'hotels'}
    assert {e['id'] for e in world['events']} >= {'vladivostok-ai', 'kazan-engineering'}
    assert all('request' not in e for e in world['events'])
    assert all('available' not in f and 'direction' not in f for f in world['flights'])
    assert all('location' in h for h in world['hotels'])


@pytest.mark.parametrize('mistake', [
    'duplicate_event', 'duplicate_flight', 'bad_date', 'date_order',
    'negative_price', 'typo', 'empty_events',
])
def test_invalid_config_rejected(config, mistake):
    path, data = config
    if mistake == 'duplicate_event':
        data['events'].append(copy.deepcopy(data['events'][0]))
    if mistake == 'duplicate_flight':
        data['flights'].append(copy.deepcopy(data['flights'][0]))
    if mistake == 'bad_date':
        data['hotels'][0]['available_nights'][0] = '2027-02-30'
    if mistake == 'date_order':
        data['events'][0]['end_date'] = '2026-01-01'
        data['events'][0]['start_date'] = '2027-01-01'
    if mistake == 'negative_price':
        data['flights'][0]['price_rub'] = -1
    if mistake == 'typo':
        data['events'][0]['lokation'] = 'X'
    if mistake == 'empty_events':
        data['events'] = []
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_catalog(path)


def test_cli_check_config(config, tmp_path):
    path, _ = config
    db = tmp_path / 'should-not-exist.sqlite3'
    result = subprocess.run(
        [sys.executable, str(SERVICE / 'admin.py'), '--config', str(path),
         '--db', str(db), 'check-config'],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'kazan-engineering' in result.stdout and not db.exists()
```

(Leave snapshot/store tests for Task 3; this file can grow later.)

- [ ] **Step 2: Run tests — expect fail**

Run: `cd /Users/adavydenko/Projects/agent-travel-playground && python -m pytest -q tests/test_config.py::test_load_catalog_returns_flat_world`

Expected: FAIL (nested shape / missing fields)

- [ ] **Step 3: Implement `catalog.py`**

Replace models with:

```python
class Flight(Record):
    id: Text
    origin: Text
    destination: Text
    depart_date: Date
    arrive_date: Date
    price_rub: Money

    @model_validator(mode='after')
    def chronological(self):
        if self.arrive_date < self.depart_date:
            raise ValueError('Прибытие не может быть раньше вылета')
        return self


class Hotel(Record):
    id: Text
    name: Text
    location: Text
    nightly_price_rub: Money
    available_nights: list[Date]


class Event(Record):
    id: Text
    name: Text
    description: Text
    location: Text
    start_date: Date
    end_date: Date
    email_required: bool = False

    @model_validator(mode='after')
    def chronological(self):
        if self.end_date < self.start_date:
            raise ValueError('Конец мероприятия раньше начала')
        return self


class Catalog(Record):
    events: Annotated[list[Event], Field(min_length=1)]
    flights: list[Flight]
    hotels: list[Hotel]

    @model_validator(mode='after')
    def unique_lists(self):
        unique_ids(self.events)
        unique_ids(self.flights)
        unique_ids(self.hotels)
        return self


def load_catalog(path=None):
    default = Path(__file__).resolve().parents[1] / 'config/events.json'
    path = Path(path or os.environ.get('VERIFY_CONFIG', default))
    catalog = Catalog.model_validate(json.loads(path.read_text(encoding='utf-8')))
    return catalog.model_dump()
```

Keep `Record`, `Date`, `Text`, `Money`, `unique_ids`, `iso_date` helpers.

- [ ] **Step 4: Rewrite `config/events.json`**

Flat structure. Two events (`vladivostok-ai`, `kazan-engineering`) with `description`, `location`, `email_required: false`. Merge former nested flights/hotels into top-level lists with **globally unique** ids (prefix `vvo-` / `kzn-`). Drop former `out-sold` / unavailable flights. Hotels use `location` (not `city`). Preserve solvable date coverage from the old examples (night flights arrive day before event; return after event; Mayak nights cover stay).

Minimal event shape example:

```json
{
  "id": "vladivostok-ai",
  "name": "Конференция по ИИ",
  "description": "Двухдневная конференция во Владивостоке.",
  "location": "Владивосток",
  "start_date": "2027-03-12",
  "end_date": "2027-03-13",
  "email_required": false
}
```

- [ ] **Step 5: Fix `admin.py` check-config for list world**

```python
if a.command == 'check-config':
    world = load_catalog(a.config)
    for event in world['events']:
        print(f"{event['id']}: {event['name']} ({event['start_date']} — {event['end_date']})")
    sys.exit(0)
```

(Other admin commands may break until Task 3 — acceptable if Task 1 tests only hit `check-config` / `load_catalog`.)

- [ ] **Step 6: Run config tests**

Run: `python -m pytest -q tests/test_config.py`

Expected: PASS for the tests added in Step 1. If other tests in the file still reference old APIs, delete or comment them until Task 3 — do not leave failing obsolete tests.

- [ ] **Step 7: Commit**

```bash
git add service/catalog.py config/events.json tests/test_config.py service/admin.py
git commit -m "$(cat <<'EOF'
Reshape catalog into a flat events/flights/hotels world.

Remove nested per-event catalogs and request briefs so agents browse a shared config.
EOF
)"
```

---

### Task 2: Travel apply — list, RSVP, book (no grade)

**Files:**
- Modify: `service/travel.py`
- Create or extend: `tests/test_travel.py` (prefer new focused file)

**Interfaces:**
- Produces: `new_attempt(world: dict) -> dict` state with deep-copied `events`/`flights`/`hotels`, `bookings=[]`, `registrations=[]`
- Produces: `apply(state, tool, args) -> dict` for tools:
  - `list_events` → `{ok, events}`
  - `list_flights` → `{ok, flights}`
  - `list_hotels` → `{ok, hotels}`
  - `register_for_event` args: `event_id`, `name`, optional `email` → `{ok, registration}` or errors `unknown_event` / `email_required`
  - `list_registrations` → `{ok, registrations}`
  - `cancel_registration` args: `registration_id` → idempotent cancel
  - `book_flight` args: `flight_id` (no `available` check)
  - `book_hotel` / `cancel_booking` / `list_bookings` (same local rules as today, hotel field `location`)
- Removes: `inspect_task`, `finish`, `grade`, `new_trip`, `inspected`

- [ ] **Step 1: Write failing travel unit tests**

```python
# tests/test_travel.py
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'service'))
from catalog import load_catalog
from travel import apply, new_attempt


@pytest.fixture
def state():
    return new_attempt(load_catalog())


def test_list_tools_return_catalog(state):
    assert len(apply(state, 'list_events', {})['events']) >= 2
    assert apply(state, 'list_flights', {})['flights']
    assert apply(state, 'list_hotels', {})['hotels']


def test_register_requires_email_when_configured(state):
    event = next(e for e in state['events'] if e['id'] == 'vladivostok-ai')
    event['email_required'] = True
    assert apply(state, 'register_for_event',
                 {'event_id': 'vladivostok-ai', 'name': 'Ann'})['error'] == 'email_required'
    ok = apply(state, 'register_for_event',
               {'event_id': 'vladivostok-ai', 'name': 'Ann', 'email': 'a@example.com'})
    assert ok['ok'] and ok['registration']['email'] == 'a@example.com'


def test_multiple_registrations_allowed(state):
    apply(state, 'register_for_event', {'event_id': 'vladivostok-ai', 'name': 'A'})
    apply(state, 'register_for_event', {'event_id': 'vladivostok-ai', 'name': 'B'})
    apply(state, 'register_for_event', {'event_id': 'kazan-engineering', 'name': 'C'})
    assert len(apply(state, 'list_registrations', {})['registrations']) == 3


def test_book_flight_and_hotel_without_available_flag(state):
    flight = next(f for f in state['flights'] if f['id'].endswith('out-night') or 'out-night' in f['id'])
    # Use concrete ids from events.json after Task 1, e.g. vvo-out-night
    flight = next(f for f in state['flights'] if f['destination'] == 'Владивосток')
    hotel = next(h for h in state['hotels'] if h['name'] == 'Маяк' and h['location'] == 'Владивосток')
    assert apply(state, 'book_flight', {'flight_id': flight['id']})['ok']
    assert apply(state, 'book_hotel', {
        'hotel_id': hotel['id'], 'check_in': flight['arrive_date'],
        'check_out': (  # pick a return depart from catalog
            next(f for f in state['flights']
                 if f['origin'] == 'Владивосток' and f['depart_date'] > flight['arrive_date'])
        )['depart_date'],
    })['ok']


def test_unknown_tools_removed(state):
    assert apply(state, 'inspect_task', {})['error'] == 'unknown_tool'
    assert apply(state, 'finish', {})['error'] == 'unknown_tool'
```

Adjust flight id selectors to match the IDs you wrote in Task 1.

- [ ] **Step 2: Run — expect fail**

Run: `python -m pytest -q tests/test_travel.py`

Expected: FAIL (`new_attempt` missing / old `apply`)

- [ ] **Step 3: Implement `travel.py`**

```python
def new_attempt(world):
    from copy import deepcopy
    state = deepcopy(world)
    state.update(bookings=[], registrations=[])
    return state


def apply(state, tool, args):
    if tool == 'list_events':
        return dict(ok=True, events=state['events'])
    if tool == 'list_flights':
        return dict(ok=True, flights=state['flights'])
    if tool == 'list_hotels':
        return dict(ok=True, hotels=state['hotels'])
    if tool == 'list_registrations':
        return dict(ok=True, registrations=state['registrations'])
    if tool == 'register_for_event':
        event = next((e for e in state['events'] if e['id'] == args['event_id']), None)
        if event is None:
            return dict(ok=False, error='unknown_event')
        name = (args.get('name') or '').strip()
        if not name:
            return dict(ok=False, error='name_required')
        email = args.get('email')
        if event.get('email_required') and not email:
            return dict(ok=False, error='email_required')
        registration = dict(
            id=secrets.token_hex(12), status='active',
            event_id=event['id'], name=name, email=email)
        state['registrations'].append(registration)
        return dict(ok=True, registration=registration)
    if tool == 'cancel_registration':
        reg = next((r for r in state['registrations'] if r['id'] == args['registration_id']), None)
        if reg is None:
            return dict(ok=False, error='registration_not_found')
        reg['status'] = 'cancelled'
        return dict(ok=True, registration=reg)
    if tool == 'list_bookings':
        return dict(ok=True, bookings=state['bookings'],
                    total_rub=sum(b['total_rub'] for b in active(state)))
    if tool == 'book_flight':
        flight = next((f for f in state['flights'] if f['id'] == args['flight_id']), None)
        if flight is None:
            return dict(ok=False, error='unknown_flight')
        return add_booking(state, dict(kind='flight', item_id=flight['id'],
                                       total_rub=flight['price_rub']))
    if tool == 'book_hotel':
        # same as today, using hotel['available_nights']
        ...
    if tool == 'cancel_booking':
        ...
    return dict(ok=False, error='unknown_tool')
```

Keep `nights`, `active`, `add_booking`. Delete `grade` / `new_trip` / inspect/finish branches.

- [ ] **Step 4: Run travel tests — PASS**

Run: `python -m pytest -q tests/test_travel.py`

- [ ] **Step 5: Commit**

```bash
git add service/travel.py tests/test_travel.py
git commit -m "$(cat <<'EOF'
Replace trip inspect/finish with browse, RSVP, and book tools.

Student-facing logic no longer returns task briefs or grader checks.
EOF
)"
```

---

### Task 3: Store — token attempt, no event pin, no task_id

**Files:**
- Modify: `service/store.py`
- Modify: `service/admin.py` (drop `--event` from issue)
- Modify: `tests/test_config.py` (snapshot reload test)
- Modify: `tests/test_store.py` (full rewrite of helpers and cases)

**Interfaces:**
- `Store.__init__`: `self.world = load_catalog(config_path)` (rename from `self.catalog`)
- `issue(student, hours=168)` — no `event_id`; stop writing meaningful `event_id` (store `NULL` or drop use)
- `call(student, tool, args)`:
  - Reject unknown tools only via `apply`
  - Ensure open `travel` task for student (create with `new_attempt(self.world)` if missing)
  - Do **not** read `args['task_id']`
  - No `finish` / receipt issuance; remove `task_closed` via receipt (column may remain unused)
  - Keep 100-call budget, expiry, journal
- `report()`: `event_id` may be null / omit assigned event; status without `passed` from receipt (use `in_progress` / `expired` / `not_started` only, or `receipt` always null)

- [ ] **Step 1: Rewrite store test helpers and core cases**

```python
@pytest.fixture
def db(tmp_path):
    db = Store(tmp_path / 'state.sqlite3')
    db.issue('alice')
    db.issue('bob')
    return db


def invoke(db, tool, student='alice', **args):
    return db.call(student, tool, args)


def test_issue_has_no_event_assignment(db):
    # issue should not require --event; list_events works immediately
    events = invoke(db, 'list_events')['events']
    assert len(events) >= 2


def test_snapshot_ignores_config_edits(tmp_path):
    data = json.loads((Path(__file__).resolve().parents[1] / 'config/events.json').read_text())
    path = tmp_path / 'events.json'
    path.write_text(json.dumps(data))
    store = Store(tmp_path / 'db.sqlite3', path)
    store.issue('alice')
    before = store.call('alice', 'list_hotels', {})
    data['hotels'][0]['available_nights'] = []
    path.write_text(json.dumps(data))
    restarted = Store(store.path, path)
    assert restarted.call('alice', 'list_hotels', {}) == before
    restarted.reset('alice', 'travel')
    changed = restarted.call('alice', 'list_hotels', {})
    assert changed['hotels'][0]['available_nights'] == []


def test_book_cancel_repeat_and_isolation(db):
    flights = invoke(db, 'list_flights')['flights']
    flight = flights[0]
    r1 = invoke(db, 'book_flight', flight_id=flight['id'])
    r2 = invoke(db, 'book_flight', flight_id=flight['id'])
    assert r1['booking']['id'] == r2['booking']['id']
    assert invoke(db, 'book_flight', student='bob', flight_id=flight['id'])['booking']['id'] != r1['booking']['id']
    assert invoke(db, 'cancel_booking', booking_id=r1['booking']['id'])['ok']
```

Add tests for: call budget, reset keeps journal, revoke/expiry, hotel unavailable nights, concurrent replay (adapted from old file). **Delete** all `finish` / `inspect_task` / `start_task` / receipt assertions.

- [ ] **Step 2: Run — expect fail**

Run: `python -m pytest -q tests/test_store.py::test_issue_has_no_event_assignment`

- [ ] **Step 3: Implement store changes**

Core `call` sketch:

```python
def call(self, student, tool, args):
    with self.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        enrolled = db.execute(
            'SELECT student FROM students WHERE student=? AND active=1 AND expires>?',
            (student, time.time())).fetchone()
        if not enrolled:
            return dict(ok=False, error='access_expired')
        row = db.execute('SELECT * FROM tasks WHERE student=? AND kind=?',
                         (student, 'travel')).fetchone()
        if row is None:
            task_id = secrets.token_hex(16)
            db.execute(
                'INSERT INTO tasks(id,student,kind,expires,state,receipt) VALUES(?,?,?,?,?,NULL)',
                (task_id, student, 'travel', time.time() + 7200,
                 json.dumps(new_attempt(self.world))))
            row = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
        task_id = row['id']
        if row['expires'] <= time.time():
            result = dict(ok=False, error='task_expired')
        else:
            count = db.execute(
                'SELECT COUNT(*) FROM events WHERE task=? AND student=?',
                (task_id, student)).fetchone()[0]
            if count >= 100:
                result = dict(ok=False, error='call_budget_exhausted')
            else:
                state = json.loads(row['state'])
                result = apply(state, tool, args) if row['kind'] == 'travel' else dict(
                    ok=False, error='retired_task')
                db.execute('UPDATE tasks SET state=? WHERE id=?',
                           (json.dumps(state), task_id))
        db.execute(
            'INSERT INTO events(at,student,task,tool,args,result) VALUES(?,?,?,?,?,?)',
            (time.time(), student, task_id, tool, json.dumps(args), json.dumps(result)))
        return result
```

Simplify `issue` to not take/select `event_id` (keep DB column nullable for old rows).

Update `admin.py`: remove `--event` argument; `db.issue(a.student, a.hours)`.

- [ ] **Step 4: Run store + config tests**

Run: `python -m pytest -q tests/test_store.py tests/test_config.py tests/test_travel.py`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add service/store.py service/admin.py tests/test_store.py tests/test_config.py
git commit -m "$(cat <<'EOF'
Bind attempts to issued tokens without task_id or event pins.

Auto-create travel state on first tool call and snapshot the flat world.
EOF
)"
```

---

### Task 4: MCP server tool surface

**Files:**
- Modify: `service/server.py`
- Modify: `tests/test_http.py`

**Interfaces:**
- MCP tools (no `task_id` parameters):
  - `list_events()`, `list_flights()`, `list_hotels()`
  - `register_for_event(event_id, name, email=None)`
  - `list_registrations()`, `cancel_registration(registration_id)`
  - `book_flight(flight_id)`, `book_hotel(hotel_id, check_in, check_out)`
  - `cancel_booking(booking_id)`, `list_bookings()`
- Remove: `start_task`, `inspect_task`, `finish`

- [ ] **Step 1: Update HTTP test expectations first**

In `tests/test_http.py`:
- Stop mutating `request.budget_rub`; use flat config as-is (or tweak a hotel night if needed)
- `issue('bob')` without `event_id`
- Assert schemas == the new tool name set
- Flow: list → register → book flights/hotel → list_bookings (no finish)
- Keep access boundary and rate-limit tests

```python
names = {x['name'] for x in await schemas(a)}
assert names == {
    'list_events', 'list_flights', 'list_hotels',
    'register_for_event', 'list_registrations', 'cancel_registration',
    'book_flight', 'book_hotel', 'cancel_booking', 'list_bookings',
}
events = await call(a, 'list_events', {})
assert any(e['id'] == 'vladivostok-ai' for e in events['events'])
await call(a, 'register_for_event', {'event_id': 'vladivostok-ai', 'name': 'Alice'})
# book using listed flights/hotels for Владивосток...
```

- [ ] **Step 2: Run — expect fail**

Run: `python -m pytest -q tests/test_http.py::test_real_mcp_two_principals`

- [ ] **Step 3: Rewrite `server.py` tools**

Replace tool functions; each calls `invoke(tool_name, **args)` with no `task_id`. Update docstrings to Russian, matching prior style, without mentioning `finish` grading.

Example:

```python
@mcp.tool()
def list_events() -> dict:
    """Список мероприятий: id, название, описание, локация, даты, нужен ли email при регистрации."""
    return invoke('list_events')


@mcp.tool()
def register_for_event(event_id: str, name: str, email: str | None = None) -> dict:
    """Одна регистрация = один участник. Повторный вызов добавляет ещё одного человека."""
    args = dict(event_id=event_id, name=name)
    if email is not None:
        args['email'] = email
    return invoke('register_for_event', **args)
```

- [ ] **Step 4: Run all tests**

Run: `python service/admin.py check-config && python -m pytest -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add service/server.py tests/test_http.py
git commit -m "$(cat <<'EOF'
Expose browse, RSVP, and booking tools on MCP only.

Remove start/inspect/finish so agents cannot poll a success oracle.
EOF
)"
```

---

### Task 5: Requirements and README

**Files:**
- Modify: `docs/requirements.md`
- Modify: `README.md`

**Interfaces:** None (docs only)

- [ ] **Step 1: Update requirements**

Rewrite sections that describe nested catalogs, `request`, `inspect_task`, `finish`, success-via-receipt, and assigned event on token. New contract:

- Config = flat world
- Student tools = list + register + book
- Operator issues token; attempt snapshots world
- Success criteria documented as **future grader** intent (physical constraints + RSVP), not current server behavior
- Note hotel check-in 14:00 / check-out 12:00 as narrative defaults; grading still date-based when added

- [ ] **Step 2: Update README**

- Quick start: `admin.py issue local-user` without `--event`
- Table of event/flight/hotel fields
- Agent contract steps: authenticate → list → register → book
- Remove finish/receipt student flow; mention journal/report for operators
- Mention design spec path optionally under docs

- [ ] **Step 3: Docs-only verification**

Manually confirm commands in README match `admin.py` and tool names in `server.py`. No new tests required.

- [ ] **Step 4: Commit**

```bash
git add docs/requirements.md README.md
git commit -m "$(cat <<'EOF'
Document flat world and browse/RSVP/book agent contract.

Align requirements and README with the MCP surface that no longer grades.
EOF
)"
```

---

### Task 6: Final verification gate

**Files:** none (verify only)

- [ ] **Step 1: Full local check**

```bash
pip install -r requirements-dev.txt
python service/admin.py check-config
python -m pytest -q
```

Expected: config prints both events; pytest all green.

- [ ] **Step 2: Spec coverage self-check**

Confirm against the design doc: flat schema, no request, no flight available, RSVP with name/email rules, no inspect/finish, no task_id, snapshot on attempt, grader deferred, docs updated.

- [ ] **Step 3: No commit unless fixes were needed**

If fixes: commit them with a clear message, then re-run Step 1.

---

## Spec coverage checklist

| Spec item | Task |
|---|---|
| Flat `{events,flights,hotels}` | 1 |
| Slim event + `email_required` | 1 |
| Flight without `available`/`direction` | 1 |
| Hotel `location` | 1 |
| Example migration solvable | 1 + 2/4 tests |
| Remove inspect/finish/start_task | 2, 3, 4 |
| list_* tools | 2, 4 |
| RSVP register/list/cancel | 2, 4 |
| Multiple registrations allowed | 2 |
| Book tools without task_id | 3, 4 |
| Issue without event pin | 3 |
| Catalog snapshot on attempt | 3 |
| Grader deferred | 2–5 (absent) |
| requirements + README | 5 |

## Placeholder / consistency notes

- Concrete flight/hotel IDs in tests must match `config/events.json` from Task 1 (`vvo-` / `kzn-` prefixes recommended).
- `Store.world` naming must be used consistently after renaming from `catalog`.
- Do not reintroduce `finish` for HTTP “proof of success”; assert bookings/registrations instead.
