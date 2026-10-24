"""Browse, RSVP, and book operations on the current catalog and attempt records."""

import pytest

import travel
from catalog import load_catalog


@pytest.fixture
def state():
    return dict(load_catalog(), bookings=[], registrations=[])


def test_list_tools_return_catalog(state):
    assert len(travel.list_events(state)['events']) >= 2
    assert travel.list_flights(state)['flights']
    assert travel.list_hotels(state)['hotels']


def test_register_requires_email_when_configured(state):
    event = next(e for e in state['events'] if e['id'] == 'vladivostok-ai')
    event['email_required'] = True
    assert (
        travel.register_for_event(state, event_id='vladivostok-ai', name='Ann')['error']
        == 'email_required'
    )
    ok = travel.register_for_event(
        state, event_id='vladivostok-ai', name='Ann', email='a@example.com'
    )
    assert ok['ok'] and ok['registration']['email'] == 'a@example.com'


def test_blank_email_does_not_satisfy_requirement(state):
    event = next(e for e in state['events'] if e['id'] == 'vladivostok-ai')
    event['email_required'] = True
    refused = travel.register_for_event(
        state, event_id='vladivostok-ai', name='Ann', email='   '
    )
    assert refused['error'] == 'email_required'
    assert state['registrations'] == []


def test_multiple_registrations_allowed(state):
    travel.register_for_event(state, event_id='vladivostok-ai', name='A')
    travel.register_for_event(state, event_id='vladivostok-ai', name='B')
    travel.register_for_event(state, event_id='kazan-engineering', name='C')
    assert len(travel.list_registrations(state)['registrations']) == 3


def test_cancel_registration(state):
    reg = travel.register_for_event(state, event_id='vladivostok-ai', name='Ann')[
        'registration'
    ]
    assert travel.cancel_registration(state, registration_id=reg['id'])['ok']
    assert travel.cancel_registration(state, registration_id=reg['id'])['ok']
    assert (
        travel.cancel_registration(state, registration_id='missing')['error']
        == 'registration_not_found'
    )


def test_book_flight_and_hotel(state):
    flight = next(f for f in state['flights'] if f['id'] == 'vvo-out-night')
    back = next(f for f in state['flights'] if f['id'] == 'vvo-back')
    hotel = next(h for h in state['hotels'] if h['id'] == 'vvo-mayak')
    assert travel.book_flight(state, flight_id=flight['id'])['ok']
    assert travel.book_flight(state, flight_id=back['id'])['ok']
    assert travel.book_hotel(
        state,
        hotel_id=hotel['id'],
        check_in=flight['arrive_date'],
        check_out=back['depart_date'],
    )['ok']
    assert len(travel.list_bookings(state)['bookings']) == 3


def test_hotel_unavailable_nights(state):
    flight = next(f for f in state['flights'] if f['id'] == 'vvo-out-night')
    back = next(f for f in state['flights'] if f['id'] == 'vvo-back')
    hotel = next(h for h in state['hotels'] if h['id'] == 'vvo-central')
    result = travel.book_hotel(
        state,
        hotel_id=hotel['id'],
        check_in=flight['arrive_date'],
        check_out=back['depart_date'],
    )
    assert result['error'] == 'hotel_unavailable'
    assert result['unavailable_nights'] == [flight['arrive_date']]


def test_hotel_replay_preserves_price_when_offer_removed(state):
    booked = travel.book_hotel(
        state, hotel_id='vvo-mayak', check_in='2027-03-11', check_out='2027-03-13'
    )
    assert booked['ok']
    state['hotels'] = []
    repeated = travel.book_hotel(
        state, hotel_id='vvo-mayak', check_in='2027-03-11', check_out='2027-03-13'
    )
    assert repeated['repeated'] and repeated['booking'] == booked['booking']
    travel.cancel_booking(state, booking_id=booked['booking']['id'])
    assert (
        travel.book_hotel(
            state, hotel_id='vvo-mayak', check_in='2027-03-11', check_out='2027-03-13'
        )['error']
        == 'unknown_hotel'
    )
