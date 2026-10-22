"""Browse, RSVP, and book tools operate on a flat world snapshot."""
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


def test_cancel_registration(state):
    reg = apply(state, 'register_for_event',
                {'event_id': 'vladivostok-ai', 'name': 'Ann'})['registration']
    assert apply(state, 'cancel_registration', {'registration_id': reg['id']})['ok']
    assert apply(state, 'cancel_registration', {'registration_id': reg['id']})['ok']
    assert apply(state, 'cancel_registration',
                 {'registration_id': 'missing'})['error'] == 'registration_not_found'


def test_book_flight_and_hotel(state):
    flight = next(f for f in state['flights'] if f['id'] == 'vvo-out-night')
    back = next(f for f in state['flights'] if f['id'] == 'vvo-back')
    hotel = next(h for h in state['hotels'] if h['id'] == 'vvo-mayak')
    assert apply(state, 'book_flight', {'flight_id': flight['id']})['ok']
    assert apply(state, 'book_flight', {'flight_id': back['id']})['ok']
    assert apply(state, 'book_hotel', {
        'hotel_id': hotel['id'],
        'check_in': flight['arrive_date'],
        'check_out': back['depart_date'],
    })['ok']
    assert len(apply(state, 'list_bookings', {})['bookings']) == 3


def test_hotel_unavailable_nights(state):
    flight = next(f for f in state['flights'] if f['id'] == 'vvo-out-night')
    back = next(f for f in state['flights'] if f['id'] == 'vvo-back')
    hotel = next(h for h in state['hotels'] if h['id'] == 'vvo-central')
    result = apply(state, 'book_hotel', {
        'hotel_id': hotel['id'],
        'check_in': flight['arrive_date'],
        'check_out': back['depart_date'],
    })
    assert result['error'] == 'hotel_unavailable'
    assert result['unavailable_nights'] == [flight['arrive_date']]


def test_unknown_tools_removed(state):
    assert apply(state, 'inspect_task', {})['error'] == 'unknown_tool'
    assert apply(state, 'finish', {})['error'] == 'unknown_tool'
