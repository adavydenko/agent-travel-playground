"""Token-scoped attempts: browse, book, RSVP without task_id or grader."""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'service'))
from store import Store
from travel import nights


@pytest.fixture
def db(tmp_path):
    store = Store(tmp_path / 'state.sqlite3')
    store.issue('alice')
    store.issue('bob')
    return store


def invoke(db, tool, student='alice', **args):
    return db.call(student, tool, args)


def test_issue_has_no_event_assignment(db):
    events = invoke(db, 'list_events')['events']
    assert {e['id'] for e in events} >= {'vladivostok-ai', 'kazan-engineering'}


def test_snapshot_ignores_config_edits(tmp_path):
    data = json.loads((ROOT / 'config/events.json').read_text())
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
    flight = next(f for f in invoke(db, 'list_flights')['flights'] if f['id'] == 'vvo-out-night')
    r1 = invoke(db, 'book_flight', flight_id=flight['id'])
    r2 = invoke(db, 'book_flight', flight_id=flight['id'])
    assert r1['booking']['id'] == r2['booking']['id']
    bob = invoke(db, 'book_flight', student='bob', flight_id=flight['id'])
    assert bob['booking']['id'] != r1['booking']['id']
    assert invoke(db, 'cancel_booking', booking_id=r1['booking']['id'])['ok']
    assert invoke(db, 'cancel_booking', booking_id=r1['booking']['id'])['ok']


def test_register_and_list(db):
    assert invoke(db, 'register_for_event', event_id='vladivostok-ai', name='Alice')['ok']
    regs = invoke(db, 'list_registrations')['registrations']
    assert len(regs) == 1 and regs[0]['name'] == 'Alice'


def test_unavailable_hotel_has_no_side_effects(db):
    out = next(f for f in invoke(db, 'list_flights')['flights'] if f['id'] == 'vvo-out-night')
    back = next(f for f in invoke(db, 'list_flights')['flights'] if f['id'] == 'vvo-back')
    hotel = next(h for h in invoke(db, 'list_hotels')['hotels'] if h['id'] == 'vvo-central')
    result = invoke(db, 'book_hotel', hotel_id=hotel['id'],
                    check_in=out['arrive_date'], check_out=back['depart_date'])
    assert result['error'] == 'hotel_unavailable'
    assert result['unavailable_nights'] == [out['arrive_date']]
    assert invoke(db, 'list_bookings')['bookings'] == []


def test_concurrent_replays_do_not_duplicate(db):
    out = next(f for f in invoke(db, 'list_flights')['flights'] if f['id'] == 'vvo-out-night')
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(
            lambda _: invoke(db, 'book_flight', flight_id=out['id']), range(8)))
    assert len({r['booking']['id'] for r in results}) == 1
    assert len(invoke(db, 'list_bookings')['bookings']) == 1


@pytest.mark.parametrize('start_date,end_date', [
    ('bad', '2027-04-01'), ('2027-04-01', '2027-04-01'),
    ('2027-04-02', '2027-04-01'), ('20270401', '2027-04-03'),
])
def test_invalid_dates(db, start_date, end_date):
    hotel = next(h for h in invoke(db, 'list_hotels')['hotels'] if h['id'] == 'vvo-mayak')
    assert invoke(db, 'book_hotel', hotel_id=hotel['id'],
                  check_in=start_date, check_out=end_date)['error'] == 'invalid_dates'


def test_isolation_foreign_calls_do_not_affect_owner_budget(db):
    out = next(f for f in invoke(db, 'list_flights')['flights'] if f['id'] == 'vvo-out-night')
    booked = invoke(db, 'book_flight', flight_id=out['id'])
    assert db.call('bob', 'cancel_booking',
                   {'booking_id': booked['booking']['id']})['error'] == 'booking_not_found'
    alice_before = next(r for r in db.report() if r['student'] == 'alice')['calls']
    for _ in range(5):
        assert db.call('bob', 'cancel_booking',
                       {'booking_id': booked['booking']['id']})['error'] == 'booking_not_found'
    alice_after = next(r for r in db.report() if r['student'] == 'alice')['calls']
    assert alice_after == alice_before == 2
    assert invoke(db, 'list_bookings')['bookings'][0]['status'] == 'active'


def test_auth_rotation_revocation_expiry(db):
    old = db.issue('alice')
    new = db.issue('alice')
    assert db.authenticate(old) is None and db.authenticate(new) == 'alice'
    db.revoke('alice')
    assert db.authenticate(new) is None
    assert db.call('alice', 'list_events', {})['error'] == 'access_expired'
    new = db.issue('alice')
    with db.connect() as con:
        con.execute('UPDATE students SET expires=0 WHERE student=?', ('alice',))
    assert db.authenticate(new) is None


def test_budget_expiry_reset_and_old_work_retired(db):
    assert {r['status'] for r in db.report()} == {'not_started'}
    invoke(db, 'list_bookings')
    task_id = next(r['task_id'] for r in db.report() if r['student'] == 'alice')
    for _ in range(99):
        invoke(db, 'list_bookings')
    assert invoke(db, 'list_bookings')['error'] == 'call_budget_exhausted'
    db.reset('alice', 'travel')
    assert invoke(db, 'list_bookings')['ok']
    new_id = next(r['task_id'] for r in db.report() if r['student'] == 'alice')
    assert new_id != task_id
    assert any(e['task'] == task_id for e in db.events('alice'))
    with db.connect() as con:
        con.execute('UPDATE tasks SET expires=0 WHERE id=?', (new_id,))
    assert invoke(db, 'list_bookings')['error'] == 'task_expired'


def test_nights_helper_still_used():
    assert nights('2027-03-11', '2027-03-14') == ['2027-03-11', '2027-03-12', '2027-03-13']
