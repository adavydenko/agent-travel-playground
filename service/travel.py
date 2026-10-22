"""Configurable travel simulator: no external bookings or payments."""

import secrets
from copy import deepcopy
from datetime import date, timedelta

MIN_NIGHTS = 1
MAX_NIGHTS = 14


def nights(check_in, check_out):
    start, end = date.fromisoformat(check_in), date.fromisoformat(check_out)
    if check_in != start.isoformat() or check_out != end.isoformat():
        raise ValueError('Используйте даты YYYY-MM-DD.')
    span = (end - start).days
    if not MIN_NIGHTS <= span <= MAX_NIGHTS:
        raise ValueError(
            f'Проживание должно длиться от {MIN_NIGHTS} до {MAX_NIGHTS} ночей.'
        )
    return [(start + timedelta(days=i)).isoformat() for i in range(span)]


def new_attempt(world):
    # Snapshot preserves the conditions of an attempt across configuration changes.
    state = deepcopy(world)
    state['bookings'] = []
    state['registrations'] = []
    return state


def active(state):
    return [booking for booking in state['bookings'] if booking['status'] == 'active']


def find_by_id(items, item_id):
    return next((item for item in items if item['id'] == item_id), None)


def add_booking(state, details):
    for booking in active(state):
        if all(booking.get(key) == value for key, value in details.items()):
            return dict(ok=True, booking=booking, repeated=True)
    booking = dict(id=secrets.token_hex(12), status='active', **details)
    state['bookings'].append(booking)
    return dict(ok=True, booking=booking, repeated=False)


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
        return _register(state, args)
    if tool == 'cancel_registration':
        return _cancel_registration(state, args)
    if tool == 'list_bookings':
        return dict(
            ok=True,
            bookings=state['bookings'],
            total_rub=sum(booking['total_rub'] for booking in active(state)),
        )
    if tool == 'book_flight':
        return _book_flight(state, args)
    if tool == 'book_hotel':
        return _book_hotel(state, args)
    if tool == 'cancel_booking':
        return _cancel_booking(state, args)
    return dict(ok=False, error='unknown_tool')


def _register(state, args):
    event = find_by_id(state['events'], args['event_id'])
    if event is None:
        return dict(ok=False, error='unknown_event')
    name = (args.get('name') or '').strip()
    if not name:
        return dict(ok=False, error='name_required')
    email = (args.get('email') or '').strip() or None
    if event.get('email_required') and email is None:
        return dict(ok=False, error='email_required')
    registration = dict(
        id=secrets.token_hex(12),
        status='active',
        event_id=event['id'],
        name=name,
        email=email,
    )
    state['registrations'].append(registration)
    return dict(ok=True, registration=registration)


def _cancel_registration(state, args):
    registration = find_by_id(state['registrations'], args['registration_id'])
    if registration is None:
        return dict(ok=False, error='registration_not_found')
    registration['status'] = 'cancelled'
    return dict(ok=True, registration=registration)


def _book_flight(state, args):
    flight = find_by_id(state['flights'], args['flight_id'])
    if flight is None:
        return dict(ok=False, error='unknown_flight')
    return add_booking(
        state,
        dict(kind='flight', item_id=flight['id'], total_rub=flight['price_rub']),
    )


def _book_hotel(state, args):
    hotel = find_by_id(state['hotels'], args['hotel_id'])
    if hotel is None:
        return dict(ok=False, error='unknown_hotel')
    try:
        requested = nights(args['check_in'], args['check_out'])
    except ValueError as exc:
        return dict(ok=False, error='invalid_dates', message=str(exc))
    available = set(hotel['available_nights'])
    unavailable = [night for night in requested if night not in available]
    if unavailable:
        return dict(ok=False, error='hotel_unavailable', unavailable_nights=unavailable)
    return add_booking(
        state,
        dict(
            kind='hotel',
            item_id=hotel['id'],
            check_in=args['check_in'],
            check_out=args['check_out'],
            total_rub=len(requested) * hotel['nightly_price_rub'],
        ),
    )


def _cancel_booking(state, args):
    booking = find_by_id(state['bookings'], args['booking_id'])
    if booking is None:
        return dict(ok=False, error='booking_not_found')
    booking['status'] = 'cancelled'
    return dict(ok=True, booking=booking)
