"""Configurable travel simulator: no external bookings or payments."""
from datetime import date, timedelta
from copy import deepcopy
import secrets


def nights(check_in, check_out):
    start, end = date.fromisoformat(check_in), date.fromisoformat(check_out)
    if check_in != start.isoformat() or check_out != end.isoformat():
        raise ValueError('Используйте даты YYYY-MM-DD.')
    if not 1 <= (end - start).days <= 14:
        raise ValueError('Проживание должно длиться от 1 до 14 ночей.')
    return [(start + timedelta(days=i)).isoformat() for i in range((end - start).days)]


def new_attempt(world):
    # Snapshot preserves the conditions of an attempt across configuration changes.
    state = deepcopy(world)
    state.update(bookings=[], registrations=[])
    return state


def active(state):
    return [b for b in state['bookings'] if b['status'] == 'active']


def add_booking(state, details):
    for booking in active(state):
        if all(booking.get(k) == v for k, v in details.items()):
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
        hotel = next((h for h in state['hotels'] if h['id'] == args['hotel_id']), None)
        if hotel is None:
            return dict(ok=False, error='unknown_hotel')
        try:
            requested = nights(args['check_in'], args['check_out'])
        except ValueError as exc:
            return dict(ok=False, error='invalid_dates', message=str(exc))
        unavailable = [n for n in requested if n not in hotel['available_nights']]
        if unavailable:
            return dict(ok=False, error='hotel_unavailable', unavailable_nights=unavailable)
        return add_booking(state, dict(kind='hotel', item_id=hotel['id'],
            check_in=args['check_in'], check_out=args['check_out'],
            total_rub=len(requested) * hotel['nightly_price_rub']))
    if tool == 'cancel_booking':
        booking = next((b for b in state['bookings'] if b['id'] == args['booking_id']), None)
        if booking is None:
            return dict(ok=False, error='booking_not_found')
        booking['status'] = 'cancelled'
        return dict(ok=True, booking=booking)
    return dict(ok=False, error='unknown_tool')
