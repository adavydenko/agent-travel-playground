"""Token-scoped attempts: browse, book, RSVP without task_id or grader."""

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import select

import travel
from catalog import load_catalog
from models import Access, Attempt
from store import Store
from travel import nights

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def db(tmp_path):
    with closing(Store(tmp_path / 'state.sqlite3')) as store:
        store.issue('alice')
        store.issue('bob')
        yield store


def invoke(db, operation, student='alice', **args):
    return db.call(student, operation, args)


def test_issue_has_no_event_assignment(db):
    events = invoke(db, travel.list_events)['events']
    assert {e['id'] for e in events} >= {'vladivostok-ai', 'kazan-engineering'}


def test_config_edits_apply_without_restart_or_reset(tmp_path):
    data = json.loads((ROOT / 'config/events.json').read_text())
    path = tmp_path / 'events.json'
    path.write_text(json.dumps(data))
    store = Store(tmp_path / 'db.sqlite3', path)
    store.issue('alice')
    booked = store.call(
        'alice', travel.book_flight, {'flight_id': data['flights'][0]['id']}
    )
    attempt_id = store.report()[0]['task_id']
    data['hotels'][0]['available_nights'] = []
    data['flights'][0]['price_rub'] += 100
    path.write_text(json.dumps(data))
    changed = store.call('alice', travel.list_hotels, {})
    assert changed['hotels'][0]['available_nights'] == []
    assert store.report()[0]['task_id'] == attempt_id
    assert store.call('alice', travel.list_bookings, {})['bookings'] == [
        booked['booking']
    ]
    repeated = store.call(
        'alice', travel.book_flight, {'flight_id': data['flights'][0]['id']}
    )
    assert repeated['repeated'] and repeated['booking'] == booked['booking']


def test_legacy_attempt_keeps_bookings_receipt_and_journal(tmp_path):
    world = load_catalog()
    path = tmp_path / 'events.json'
    path.write_text(json.dumps(world))
    store = Store(tmp_path / 'db.sqlite3', path)
    token = store.issue('alice')
    booked = store.call(
        'alice', travel.book_flight, {'flight_id': world['flights'][0]['id']}
    )
    registration = store.call(
        'alice',
        travel.register_for_event,
        {'event_id': world['events'][0]['id'], 'name': 'A'},
    )['registration']
    legacy_state = dict(
        world, bookings=[booked['booking']], registrations=[registration]
    )
    legacy_state['legacy_extra'] = {'preserved': True}
    with store.database.write_sessions.begin() as session:
        attempt = session.scalar(select(Attempt).where(Attempt.student == 'alice'))
        attempt.state = legacy_state
        attempt.receipt = '{"legacy":true}'
    before = store.events('alice')
    world['flights'] = []
    path.write_text(json.dumps(world))
    restarted = Store(store.path, path)
    assert restarted.authenticate(token) == 'alice'
    assert restarted.call('alice', travel.list_flights, {})['flights'] == []
    assert restarted.call('alice', travel.list_bookings, {})['bookings'] == [
        booked['booking']
    ]
    assert restarted.call('alice', travel.list_registrations, {})['registrations'] == [
        registration
    ]
    assert restarted.report()[0]['receipt'] == '{"legacy":true}'
    assert restarted.events('alice')[: len(before)] == before
    with restarted.database.sessions() as session:
        saved = session.scalar(select(Attempt)).state
    assert saved['legacy_extra'] == {'preserved': True}
    assert not {'events', 'flights', 'hotels'} & saved.keys()


def test_invalid_live_config_is_audited_and_recoverable(tmp_path):
    path = tmp_path / 'events.json'
    original = (ROOT / 'config/events.json').read_text()
    path.write_text(original)
    store = Store(tmp_path / 'db.sqlite3', path)
    store.issue('alice')
    path.write_text('{')
    refused = store.call('alice', travel.list_events, {})
    assert refused['ok'] is False and refused['error'] == 'invalid_config'
    assert json.loads(store.events('alice')[-1]['result']) == refused
    path.write_text(original)
    assert store.call('alice', travel.list_events, {})['ok']


def test_book_cancel_repeat_and_isolation(db):
    flight = next(
        f
        for f in invoke(db, travel.list_flights)['flights']
        if f['id'] == 'vvo-out-night'
    )
    r1 = invoke(db, travel.book_flight, flight_id=flight['id'])
    r2 = invoke(db, travel.book_flight, flight_id=flight['id'])
    assert r1['booking']['id'] == r2['booking']['id']
    bob = invoke(db, travel.book_flight, student='bob', flight_id=flight['id'])
    assert bob['booking']['id'] != r1['booking']['id']
    assert invoke(db, travel.cancel_booking, booking_id=r1['booking']['id'])['ok']
    assert invoke(db, travel.cancel_booking, booking_id=r1['booking']['id'])['ok']


def test_register_and_list(db):
    assert invoke(
        db, travel.register_for_event, event_id='vladivostok-ai', name='Alice'
    )['ok']
    regs = invoke(db, travel.list_registrations)['registrations']
    assert len(regs) == 1 and regs[0]['name'] == 'Alice'


def test_unavailable_hotel_has_no_side_effects(db):
    out = next(
        f
        for f in invoke(db, travel.list_flights)['flights']
        if f['id'] == 'vvo-out-night'
    )
    back = next(
        f for f in invoke(db, travel.list_flights)['flights'] if f['id'] == 'vvo-back'
    )
    hotel = next(
        h for h in invoke(db, travel.list_hotels)['hotels'] if h['id'] == 'vvo-central'
    )
    result = invoke(
        db,
        travel.book_hotel,
        hotel_id=hotel['id'],
        check_in=out['arrive_date'],
        check_out=back['depart_date'],
    )
    assert result['error'] == 'hotel_unavailable'
    assert result['unavailable_nights'] == [out['arrive_date']]
    assert invoke(db, travel.list_bookings)['bookings'] == []


def test_concurrent_replays_do_not_duplicate(db):
    out = next(
        f
        for f in invoke(db, travel.list_flights)['flights']
        if f['id'] == 'vvo-out-night'
    )
    with ThreadPoolExecutor(4) as pool:
        results = list(
            pool.map(
                lambda _: invoke(db, travel.book_flight, flight_id=out['id']), range(8)
            )
        )
    assert len({r['booking']['id'] for r in results}) == 1
    assert len(invoke(db, travel.list_bookings)['bookings']) == 1


@pytest.mark.parametrize(
    'start_date,end_date',
    [
        ('bad', '2027-04-01'),
        ('2027-04-01', '2027-04-01'),
        ('2027-04-02', '2027-04-01'),
        ('20270401', '2027-04-03'),
    ],
)
def test_invalid_dates(db, start_date, end_date):
    hotel = next(
        h for h in invoke(db, travel.list_hotels)['hotels'] if h['id'] == 'vvo-mayak'
    )
    assert (
        invoke(
            db,
            travel.book_hotel,
            hotel_id=hotel['id'],
            check_in=start_date,
            check_out=end_date,
        )['error']
        == 'invalid_dates'
    )


def test_isolation_foreign_calls_do_not_affect_owner_budget(db):
    out = next(
        f
        for f in invoke(db, travel.list_flights)['flights']
        if f['id'] == 'vvo-out-night'
    )
    booked = invoke(db, travel.book_flight, flight_id=out['id'])
    assert (
        db.call('bob', travel.cancel_booking, {'booking_id': booked['booking']['id']})[
            'error'
        ]
        == 'booking_not_found'
    )
    alice_before = next(r for r in db.report() if r['student'] == 'alice')['calls']
    for _ in range(5):
        assert (
            db.call(
                'bob', travel.cancel_booking, {'booking_id': booked['booking']['id']}
            )['error']
            == 'booking_not_found'
        )
    alice_after = next(r for r in db.report() if r['student'] == 'alice')['calls']
    assert alice_after == alice_before == 2
    assert invoke(db, travel.list_bookings)['bookings'][0]['status'] == 'active'


def test_auth_rotation_revocation_expiry(db):
    old = db.issue('alice')
    new = db.issue('alice')
    assert db.authenticate(old) is None and db.authenticate(new) == 'alice'
    db.revoke('alice')
    assert db.authenticate(new) is None
    assert db.call('alice', travel.list_events, {})['error'] == 'access_expired'
    new = db.issue('alice')
    with db.database.write_sessions.begin() as session:
        session.get(Access, 'alice').expires = 0
    assert db.authenticate(new) is None


def test_budget_expiry_reset_and_old_work_retired(db):
    assert {r['status'] for r in db.report()} == {'not_started'}
    invoke(db, travel.list_bookings)
    task_id = next(r['task_id'] for r in db.report() if r['student'] == 'alice')
    for _ in range(99):
        invoke(db, travel.list_bookings)
    assert invoke(db, travel.list_bookings)['error'] == 'call_budget_exhausted'
    db.reset('alice', 'travel')
    assert invoke(db, travel.list_bookings)['ok']
    new_id = next(r['task_id'] for r in db.report() if r['student'] == 'alice')
    assert new_id != task_id
    assert any(e['task'] == task_id for e in db.events('alice'))
    with db.database.write_sessions.begin() as session:
        session.get(Attempt, new_id).expires = 0
    assert invoke(db, travel.list_bookings)['error'] == 'task_expired'


def test_clear_all_bookings_preserves_other_data(db, tmp_path):
    tokens = {student: db.issue(student) for student in ('alice', 'bob', 'unused')}
    flight = invoke(db, travel.book_flight, flight_id='vvo-out-night')['booking']
    invoke(db, travel.cancel_booking, booking_id=flight['id'])
    invoke(
        db,
        travel.book_hotel,
        hotel_id='vvo-mayak',
        check_in='2027-03-11',
        check_out='2027-03-13',
    )
    invoke(db, travel.book_flight, student='bob', flight_id='vvo-back')
    for student in ('alice', 'bob'):
        invoke(
            db,
            travel.register_for_event,
            student=student,
            event_id='vladivostok-ai',
            name=student,
        )
    db.revoke('bob')
    with db.database.write_sessions.begin() as session:
        alice = session.scalar(select(Attempt).where(Attempt.student == 'alice'))
        alice.expires = 0
        alice.receipt = '{"legacy":true}'
        alice.state = {**alice.state, 'legacy_extra': {'preserved': True}}
        session.add(
            Attempt(
                id='other-kind',
                student='alice',
                kind='other',
                expires=0,
                state={'bookings': [{'id': 'keep'}]},
            )
        )
    with db.database.sessions() as session:
        attempts_before = {
            attempt.id: (attempt.expires, attempt.receipt, deepcopy(attempt.state))
            for attempt in session.scalars(select(Attempt))
        }
    report_before = db.report()
    events_before = {student: db.events(student) for student in tokens}
    auth_before = {student: db.authenticate(token) for student, token in tokens.items()}
    db.config_path = tmp_path / 'missing.json'

    # Include cancelled bookings and bookings owned by expired/revoked attempts.
    assert db.clear_bookings() == 3
    assert db.clear_bookings() == 0

    assert db.report() == report_before
    assert {student: db.events(student) for student in tokens} == events_before
    assert {
        student: db.authenticate(token) for student, token in tokens.items()
    } == auth_before
    with db.database.sessions() as session:
        for attempt in session.scalars(select(Attempt)):
            expires, receipt, state = attempts_before[attempt.id]
            if attempt.kind == 'travel':
                state['bookings'] = []
            assert (attempt.expires, attempt.receipt, attempt.state) == (
                expires,
                receipt,
                state,
            )


def test_clear_bookings_on_empty_database(tmp_path):
    with closing(Store(tmp_path / 'empty.sqlite3')) as store:
        assert store.clear_bookings() == 0
        assert store.report() == []


def test_nights_helper_still_used():
    assert nights('2027-03-11', '2027-03-14') == [
        '2027-03-11',
        '2027-03-12',
        '2027-03-13',
    ]


def test_operation_logs_metadata_without_credentials_or_participant_data(db, caplog):
    token = db.issue('log-test')
    with caplog.at_level(logging.INFO, logger='store'):
        assert db.authenticate(token) == 'log-test'
        result = db.call(
            'log-test',
            travel.register_for_event,
            {
                'event_id': 'vladivostok-ai',
                'name': 'Private Name',
                'email': 'private@example.com',
            },
        )
    assert result['ok']
    assert 'register_for_event' in caplog.text
    assert token not in caplog.text
    assert 'Private Name' not in caplog.text
    assert 'private@example.com' not in caplog.text


def test_failed_operation_rolls_back_state_and_audit(db):
    invoke(db, travel.list_bookings)
    before = db.events('alice')

    def failing_operation(state):
        state['bookings'].append({'id': 'must-not-persist'})
        raise RuntimeError('failure before audit')

    with pytest.raises(RuntimeError, match='failure before audit'):
        db.call('alice', failing_operation, {})
    assert db.events('alice') == before
    assert invoke(db, travel.list_bookings)['bookings'] == []


def test_invalid_result_rolls_back_state_and_audit(db):
    invoke(db, travel.list_bookings)
    before = db.events('alice')

    def broken_operation(state):
        booking = {'id': 'broken', 'status': 'active', 'total_rub': 'free'}
        state['bookings'].append(booking)
        return {'ok': True, 'booking': booking}

    with pytest.raises(ValidationError):
        db.call('alice', broken_operation, {})
    assert db.events('alice') == before
    assert invoke(db, travel.list_bookings)['bookings'] == []
