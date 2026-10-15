"""Config changes affect new attempts; assignment and grading stay server-owned."""
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
from travel import apply, new_trip


@pytest.fixture
def config(tmp_path):
    data = json.loads((SERVICE.parent / 'config/events.json').read_text())
    path = tmp_path / 'events.json'
    path.write_text(json.dumps(data))
    return path, data


def test_assignment_snapshot_reload_and_reset(config, tmp_path):
    path, data = config
    store = Store(tmp_path / 'db.sqlite3', path)
    store.issue('alice', event_id='kazan-engineering')
    started = store.call('alice', 'start_task', {'kind': 'travel', 'event_id': 'vladivostok-ai'})
    assert started['event_id'] == 'kazan-engineering'  # Tool args cannot change assignment.
    args = {'task_id': started['task_id']}
    before = store.call('alice', 'inspect_task', args)
    assert before['event']['name'] == 'Инженерный форум'
    assert '32000 рублей' in before['user_request']
    data['events'][1]['request']['budget_rub'] = 25000
    data['events'][1]['hotels'][1]['available_nights'] = []
    path.write_text(json.dumps(data))
    restarted = Store(store.path, path)
    assert restarted.call('alice', 'inspect_task', args) == before
    rotated = restarted.issue('alice')
    assert restarted.authenticate(rotated) == 'alice'
    with pytest.raises(ValueError, match='reset'):
        restarted.issue('alice', event_id='vladivostok-ai')
    restarted.reset('alice', 'travel')
    new_id = restarted.call('alice', 'start_task', {'kind': 'travel'})['task_id']
    changed = restarted.call('alice', 'inspect_task', {'task_id': new_id})
    assert '25000 рублей' in changed['user_request']
    assert changed['hotels'][1]['available_nights'] == []
    assert any(event['task'] == started['task_id'] for event in restarted.events('alice'))
    assert restarted.report()[0]['event_id'] == 'kazan-engineering'


@pytest.mark.parametrize('mistake', ['duplicate_event', 'duplicate_flight', 'bad_date', 'date_order',
                                    'negative_price', 'string_available', 'typo', 'empty_catalog'])
def test_invalid_config_rejected(config, mistake):
    path, data = config
    event = data['events'][0]
    if mistake == 'duplicate_event': data['events'].append(copy.deepcopy(event))
    if mistake == 'duplicate_flight': event['flights'].append(copy.deepcopy(event['flights'][0]))
    if mistake == 'bad_date': event['hotels'][0]['available_nights'][0] = '2027-02-30'
    if mistake == 'date_order': event['end_date'] = '2028-01-01'
    if mistake == 'negative_price': event['flights'][0]['price_rub'] = -1
    if mistake == 'string_available': event['flights'][0]['available'] = 'false'
    if mistake == 'typo': event['request']['budjet_rub'] = 1
    if mistake == 'empty_catalog': data['events'] = []
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError): load_catalog(path)


def test_cli_validates_without_creating_database(config, tmp_path):
    path, _ = config
    db = tmp_path / 'should-not-exist.sqlite3'
    result = subprocess.run([sys.executable, str(SERVICE/'admin.py'), '--config', str(path),
                             '--db', str(db), 'check-config'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'kazan-engineering' in result.stdout and not db.exists()
    store = Store(tmp_path/'db.sqlite3', path)
    with pytest.raises(ValueError, match='Неизвестное мероприятие'):
        store.issue('alice', event_id='typo')
    assert store.report() == []


def test_event_attendance_and_hotel_city_are_checked():
    event = next(iter(load_catalog().values()))
    state = new_trip(event)
    apply(state, 'inspect_task', {})
    for flight_id in ['out-night', 'back']:
        apply(state, 'book_flight', {'flight_id': flight_id})
    apply(state, 'book_hotel', {'hotel_id': 'mayak', 'check_in': '2027-03-11', 'check_out': '2027-03-14'})
    assert apply(state, 'finish', {})['passed']
    state['hotels'][1]['city'] = 'Другой город'
    assert not apply(state, 'finish', {})['checks']['hotel_city']
    state['hotels'][1]['city'] = event['request']['destination']
    state['flights'][0]['arrive_date'] = '2027-03-13'
    assert not apply(state, 'finish', {})['checks']['attends_event']


def test_both_example_events_can_be_completed():
    for event in load_catalog().values():
        state = new_trip(event)
        apply(state, 'inspect_task', {})
        out = next(f for f in state['flights'] if f['id'] == 'out-night')
        back = next(f for f in state['flights'] if f['id'] == 'back')
        for flight in [out, back]:
            assert apply(state, 'book_flight', {'flight_id': flight['id']})['ok']
        assert apply(state, 'book_hotel', {'hotel_id': 'mayak', 'check_in': out['arrive_date'],
                                         'check_out': back['depart_date']})['ok']
        assert apply(state, 'finish', {})['passed']
