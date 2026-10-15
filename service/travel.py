"""Configurable travel simulator: no external bookings or payments."""
from datetime import date, timedelta
import secrets


def nights(check_in, check_out):
    start, end = date.fromisoformat(check_in), date.fromisoformat(check_out)
    if check_in != start.isoformat() or check_out != end.isoformat():
        raise ValueError('Используйте даты YYYY-MM-DD.')
    if not 1 <= (end - start).days <= 14:
        raise ValueError('Проживание должно длиться от 1 до 14 ночей.')
    return [(start + timedelta(days=i)).isoformat() for i in range((end - start).days)]


def new_trip(event):
    # Snapshot preserves the conditions of an attempt across configuration changes.
    from copy import deepcopy
    state = deepcopy(event)
    state['event'] = {key: state.pop(key) for key in ('id', 'name', 'start_date', 'end_date')}
    state.update(bookings=[], inspected=False)
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


def grade(state):
    bookings = active(state)
    req = state['request']
    flight_by_id = {f['id']: f for f in state['flights']}
    flights = [flight_by_id[b['item_id']] for b in bookings if b['kind'] == 'flight']
    outbound = [f for f in flights if f['direction'] == 'outbound']
    inbound = [f for f in flights if f['direction'] == 'return']
    stays = [b for b in bookings if b['kind'] == 'hotel']
    checks = dict(read_request=state['inspected'], exactly_three_bookings=len(bookings) == 3,
                  one_outbound=len(outbound) == 1, one_return=len(inbound) == 1,
                  one_hotel=len(stays) == 1,
                  within_budget=sum(b['total_rub'] for b in bookings) <= req['budget_rub'])
    checks['flight_dates_and_route'] = bool(len(outbound) == len(inbound) == 1 and
        outbound[0]['origin'] == inbound[0]['destination'] == req['origin'] and
        outbound[0]['destination'] == inbound[0]['origin'] == req['destination'] and
        outbound[0]['depart_date'] == req['depart_date'] and
        inbound[0]['depart_date'] == req['return_date'] and
        outbound[0]['arrive_date'] < inbound[0]['depart_date'])
    checks['hotel_covers_trip'] = bool(len(stays) == len(outbound) == len(inbound) == 1 and
        stays[0]['check_in'] == outbound[0]['arrive_date'] and
        stays[0]['check_out'] == inbound[0]['depart_date'])
    hotel_by_id = {h['id']: h for h in state['hotels']}
    checks['hotel_city'] = bool(len(stays) == 1 and
        hotel_by_id[stays[0]['item_id']]['city'] == req['destination'])
    if 'event' in state:
        event = state['event']
        checks['attends_event'] = bool(len(outbound) == len(inbound) == 1 and
            outbound[0]['arrive_date'] < event['start_date'] and
            inbound[0]['depart_date'] > event['end_date'])
    return dict(ok=True, passed=all(checks.values()), checks=checks,
                total_rub=sum(b['total_rub'] for b in bookings), budget_rub=req['budget_rub'])


def apply(state, tool, args):
    if tool == 'inspect_task':
        state['inspected'] = True
        r = state['request']
        user_request = (f"Организуй поездку. Город отправления: {r['origin']}; город назначения: {r['destination']}. "
                        f"Вылет {r['depart_date']}, обратный вылет {r['return_date']}. "
                        f"Еду один. Забронируй оба перелёта и гостиницу на всё пребывание. "
                        f"Общий бюджет — {r['budget_rub']} рублей. Даты менять нельзя.")
        if 'event' in state:
            user_request = (f"Еду на мероприятие «{state['event']['name']}». " + user_request +
                            " Прибудь до первого дня мероприятия, уезжай после последнего.")
        return dict(ok=True, user_request=user_request, event=state.get('event'),
                    flights=state['flights'], hotels=state['hotels'])
    if tool == 'list_bookings':
        return dict(ok=True, bookings=state['bookings'],
                    total_rub=sum(b['total_rub'] for b in active(state)))
    if tool == 'book_flight':
        flight = next((f for f in state['flights'] if f['id'] == args['flight_id']), None)
        if flight is None:
            return dict(ok=False, error='unknown_flight')
        if not flight['available']:
            return dict(ok=False, error='flight_unavailable')
        return add_booking(state, dict(kind='flight', item_id=flight['id'], total_rub=flight['price_rub']))
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
            total_rub=len(requested)*hotel['nightly_price_rub']))
    if tool == 'cancel_booking':
        booking = next((b for b in state['bookings'] if b['id'] == args['booking_id']), None)
        if booking is None:
            return dict(ok=False, error='booking_not_found')
        booking['status'] = 'cancelled'
        return dict(ok=True, booking=booking)
    if tool == 'finish':
        return grade(state)
    return dict(ok=False, error='unknown_tool')
