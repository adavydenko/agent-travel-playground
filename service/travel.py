"""Travel domain: catalog reads and attempt-state mutations (no I/O, no MCP)."""

import secrets
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import TypeVar

from contracts import (
    Error,
    EventsResult,
    Flight,
    FlightBooking,
    FlightBookingResult,
    FlightBookingsResult,
    FlightsResult,
    Hotel,
    HotelBooking,
    HotelBookingResult,
    HotelBookingsResult,
    HotelsResult,
    Registration,
    RegistrationResult,
    RegistrationsResult,
    State,
)

Item = TypeVar('Item', bound=Mapping[str, object])

MIN_NIGHTS = 1
MAX_NIGHTS = 14


def nights(check_in: str, check_out: str) -> list[str]:
    start, end = date.fromisoformat(check_in), date.fromisoformat(check_out)
    if check_in != start.isoformat() or check_out != end.isoformat():
        raise ValueError('Используйте даты YYYY-MM-DD.')
    span = (end - start).days
    if not MIN_NIGHTS <= span <= MAX_NIGHTS:
        raise ValueError(
            f'Проживание должно длиться от {MIN_NIGHTS} до {MAX_NIGHTS} ночей.'
        )
    return [(start + timedelta(days=i)).isoformat() for i in range(span)]


def active_flights(state: State) -> list[FlightBooking]:
    return [
        booking
        for booking in state['flight_bookings']
        if booking['status'] == 'active'
    ]


def active_hotels(state: State) -> list[HotelBooking]:
    return [
        booking for booking in state['hotel_bookings'] if booking['status'] == 'active'
    ]


def find_by_id(items: Sequence[Item], item_id: str) -> Item | None:
    return next((item for item in items if item['id'] == item_id), None)


def repeated_flight(
    state: State, *, flight_id: str
) -> FlightBookingResult | None:
    for booking in active_flights(state):
        if booking['flight_id'] == flight_id:
            return {'ok': True, 'flight_booking': booking, 'repeated': True}
    return None


def repeated_hotel(
    state: State, *, hotel_id: str, check_in: str, check_out: str
) -> HotelBookingResult | None:
    for booking in active_hotels(state):
        if (
            booking['hotel_id'] == hotel_id
            and booking['check_in'] == check_in
            and booking['check_out'] == check_out
        ):
            return {'ok': True, 'hotel_booking': booking, 'repeated': True}
    return None


def add_flight_booking(state: State, flight: Flight) -> FlightBookingResult:
    booking = FlightBooking(
        id=secrets.token_hex(12),
        status='active',
        total_rub=flight['price_rub'],
        flight_id=flight['id'],
        origin=flight['origin'],
        destination=flight['destination'],
        depart_date=flight['depart_date'],
        arrive_date=flight['arrive_date'],
    )
    state['flight_bookings'].append(booking)
    return {'ok': True, 'flight_booking': booking, 'repeated': False}


def add_hotel_booking(
    state: State,
    hotel: Hotel,
    *,
    check_in: str,
    check_out: str,
    night_count: int,
) -> HotelBookingResult:
    booking = HotelBooking(
        id=secrets.token_hex(12),
        status='active',
        total_rub=night_count * hotel['nightly_price_rub'],
        hotel_id=hotel['id'],
        hotel_name=hotel['name'],
        location=hotel['location'],
        check_in=check_in,
        check_out=check_out,
        nights=night_count,
    )
    state['hotel_bookings'].append(booking)
    return {'ok': True, 'hotel_booking': booking, 'repeated': False}


def list_events(state: State) -> EventsResult | Error:
    """Список мероприятий: id, название, описание, локация, даты,
    нужен ли email при регистрации.
    """
    return {'ok': True, 'events': state['events']}


def list_flights(state: State) -> FlightsResult | Error:
    """Список рейсов: id, города, даты вылета и прибытия, цена в учебных рублях."""
    return {'ok': True, 'flights': state['flights']}


def list_hotels(state: State) -> HotelsResult | Error:
    """Список гостиниц: id, название, локация, цена за ночь.
    available_nights — даты начала доступных ночей. Ночь выезда не оплачивается.
    """
    return {'ok': True, 'hotels': state['hotels']}


def list_registrations(state: State) -> RegistrationsResult | Error:
    """Показать регистрации текущего доступа на мероприятия."""
    return {'ok': True, 'registrations': state['registrations']}


def list_flight_bookings(state: State) -> FlightBookingsResult | Error:
    """Прочитать брони рейсов, статусы и сумму активных авиаброней."""
    return {
        'ok': True,
        'flight_bookings': state['flight_bookings'],
        'total_rub': sum(booking['total_rub'] for booking in active_flights(state)),
    }


def list_hotel_bookings(state: State) -> HotelBookingsResult | Error:
    """Прочитать брони гостиниц, статусы и сумму активных броней проживания."""
    return {
        'ok': True,
        'hotel_bookings': state['hotel_bookings'],
        'total_rub': sum(booking['total_rub'] for booking in active_hotels(state)),
    }


def register_for_event(
    state: State,
    event_id: str,
    name: str,
    email: str | None = None,
) -> RegistrationResult | Error:
    """Одна регистрация = один участник. Повторный вызов добавляет ещё одного человека.
    Email обязателен, только если у мероприятия email_required=true.
    """
    event = find_by_id(state['events'], event_id)
    if event is None:
        return {'ok': False, 'error': 'unknown_event'}
    name = name.strip()
    if not name:
        return {'ok': False, 'error': 'name_required'}
    email = (email or '').strip() or None
    if event.get('email_required') and email is None:
        return {'ok': False, 'error': 'email_required'}
    registration = Registration(
        id=secrets.token_hex(12),
        status='active',
        event_id=event['id'],
        name=name,
        email=email,
    )
    state['registrations'].append(registration)
    return {'ok': True, 'registration': registration}


def cancel_registration(
    state: State, registration_id: str
) -> RegistrationResult | Error:
    """Отменить свою регистрацию. Повтор отмены безопасен."""
    registration = find_by_id(state['registrations'], registration_id)
    if registration is None:
        return {'ok': False, 'error': 'registration_not_found'}
    registration['status'] = 'cancelled'
    return {'ok': True, 'registration': registration}


def book_flight(state: State, flight_id: str) -> FlightBookingResult | Error:
    """Забронировать один рейс на одного человека.
    Повтор того же рейса не создаёт дубликат и сохраняет исходную стоимость.
    """
    repeated = repeated_flight(state, flight_id=flight_id)
    if repeated:
        return repeated
    flight = find_by_id(state['flights'], flight_id)
    if flight is None:
        return {'ok': False, 'error': 'unknown_flight'}
    return add_flight_booking(state, flight)


def book_hotel(
    state: State, hotel_id: str, check_in: str, check_out: str
) -> HotelBookingResult | Error:
    """Забронировать один номер: от 1 до 14 ночей [check_in, check_out).
    Все ночи должны быть доступны. Повтор тех же гостиницы и дат возвращает
    прежнюю бронь с исходной стоимостью, даже после изменения каталога.
    """
    repeated = repeated_hotel(
        state, hotel_id=hotel_id, check_in=check_in, check_out=check_out
    )
    if repeated:
        return repeated
    hotel = find_by_id(state['hotels'], hotel_id)
    if hotel is None:
        return {'ok': False, 'error': 'unknown_hotel'}
    try:
        requested = nights(check_in, check_out)
    except ValueError as exc:
        return {'ok': False, 'error': 'invalid_dates', 'message': str(exc)}
    available = set(hotel['available_nights'])
    unavailable = [night for night in requested if night not in available]
    if unavailable:
        return {
            'ok': False,
            'error': 'hotel_unavailable',
            'unavailable_nights': unavailable,
        }
    return add_hotel_booking(
        state,
        hotel,
        check_in=check_in,
        check_out=check_out,
        night_count=len(requested),
    )


def cancel_flight_booking(
    state: State, flight_booking_id: str
) -> FlightBookingResult | Error:
    """Отменить свою бронь рейса бесплатно. Повтор отмены безопасен."""
    booking = find_by_id(state['flight_bookings'], flight_booking_id)
    if booking is None:
        return {'ok': False, 'error': 'flight_booking_not_found'}
    booking['status'] = 'cancelled'
    return {'ok': True, 'flight_booking': booking}


def cancel_hotel_booking(
    state: State, hotel_booking_id: str
) -> HotelBookingResult | Error:
    """Отменить свою бронь гостиницы бесплатно. Повтор отмены безопасен."""
    booking = find_by_id(state['hotel_bookings'], hotel_booking_id)
    if booking is None:
        return {'ok': False, 'error': 'hotel_booking_not_found'}
    booking['status'] = 'cancelled'
    return {'ok': True, 'hotel_booking': booking}
