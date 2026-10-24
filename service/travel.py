"""Configurable travel simulator: no external bookings or payments."""

import secrets
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Annotated, Literal, TypeVar

from pydantic import Field

from contracts import (
    Booking,
    BookingResult,
    BookingsResult,
    Error,
    EventsResult,
    FlightsResult,
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


def active(state: State) -> list[Booking]:
    return [booking for booking in state['bookings'] if booking['status'] == 'active']


def find_by_id(items: Sequence[Item], item_id: str) -> Item | None:
    return next((item for item in items if item['id'] == item_id), None)


def repeated_booking(
    state: State,
    *,
    kind: Literal['flight', 'hotel'],
    item_id: str,
    check_in: str | None = None,
    check_out: str | None = None,
) -> BookingResult | None:
    for booking in active(state):
        if (
            booking.get('kind') == kind
            and booking.get('item_id') == item_id
            and (
                kind == 'flight'
                or (
                    booking.get('check_in') == check_in
                    and booking.get('check_out') == check_out
                )
            )
        ):
            return {'ok': True, 'booking': booking, 'repeated': True}
    return None


def add_booking(
    state: State,
    *,
    kind: Literal['flight', 'hotel'],
    item_id: str,
    total_rub: int,
    check_in: str | None = None,
    check_out: str | None = None,
) -> BookingResult:
    booking = Booking(
        id=secrets.token_hex(12),
        status='active',
        kind=kind,
        item_id=item_id,
        total_rub=total_rub,
    )
    if check_in is not None:
        booking['check_in'] = check_in
    if check_out is not None:
        booking['check_out'] = check_out
    state['bookings'].append(booking)
    return {'ok': True, 'booking': booking, 'repeated': False}


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


def list_bookings(state: State) -> BookingsResult | Error:
    """Прочитать брони, их статусы и сумму активных броней."""
    return {
        'ok': True,
        'bookings': state['bookings'],
        'total_rub': sum(booking['total_rub'] for booking in active(state)),
    }


def register_for_event(
    state: State,
    event_id: Annotated[str, Field(description='ID из list_events.')],
    name: Annotated[str, Field(description='Имя участника.')],
    email: Annotated[
        str | None, Field(description='Email участника, если требуется.')
    ] = None,
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
    state: State,
    registration_id: Annotated[str, Field(description='ID из list_registrations.')],
) -> RegistrationResult | Error:
    """Отменить свою регистрацию. Повтор отмены безопасен."""
    registration = find_by_id(state['registrations'], registration_id)
    if registration is None:
        return {'ok': False, 'error': 'registration_not_found'}
    registration['status'] = 'cancelled'
    return {'ok': True, 'registration': registration}


def book_flight(
    state: State, flight_id: Annotated[str, Field(description='ID из list_flights.')]
) -> BookingResult | Error:
    """Забронировать один рейс на одного человека.
    Повтор того же рейса не создаёт дубликат и сохраняет исходную стоимость.
    """
    repeated = repeated_booking(state, kind='flight', item_id=flight_id)
    if repeated:
        return repeated
    flight = find_by_id(state['flights'], flight_id)
    if flight is None:
        return {'ok': False, 'error': 'unknown_flight'}
    return add_booking(
        state,
        kind='flight',
        item_id=flight['id'],
        total_rub=flight['price_rub'],
    )


def book_hotel(
    state: State,
    hotel_id: Annotated[str, Field(description='ID из list_hotels.')],
    check_in: Annotated[str, Field(description='Дата заезда YYYY-MM-DD.')],
    check_out: Annotated[
        str, Field(description='Дата выезда YYYY-MM-DD, не оплачивается.')
    ],
) -> BookingResult | Error:
    """Забронировать один номер: от 1 до 14 ночей [check_in, check_out).
    Все ночи должны быть доступны. Повтор тех же гостиницы и дат возвращает
    прежнюю бронь с исходной стоимостью, даже после изменения каталога.
    """
    repeated = repeated_booking(
        state, kind='hotel', item_id=hotel_id, check_in=check_in, check_out=check_out
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
    return add_booking(
        state,
        kind='hotel',
        item_id=hotel_id,
        check_in=check_in,
        check_out=check_out,
        total_rub=len(requested) * hotel['nightly_price_rub'],
    )


def cancel_booking(
    state: State, booking_id: Annotated[str, Field(description='ID из list_bookings.')]
) -> BookingResult | Error:
    """Отменить свою бронь бесплатно. Повтор отмены безопасен."""
    booking = find_by_id(state['bookings'], booking_id)
    if booking is None:
        return {'ok': False, 'error': 'booking_not_found'}
    booking['status'] = 'cancelled'
    return {'ok': True, 'booking': booking}
