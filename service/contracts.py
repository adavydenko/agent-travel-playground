"""JSON contracts shared by catalog validation, operations and MCP output schemas."""

from datetime import date
from typing import Annotated, Literal, NotRequired

from pydantic import AfterValidator, ConfigDict, Field, RootModel, with_config
from typing_extensions import TypedDict


def iso_date(value: str) -> str:
    if date.fromisoformat(value).isoformat() != value:
        raise ValueError('Используйте дату YYYY-MM-DD')
    return value


Date = Annotated[
    str,
    Field(pattern=r'^\d{4}-\d{2}-\d{2}$', description='Дата YYYY-MM-DD.'),
    AfterValidator(iso_date),
]
Text = Annotated[str, Field(min_length=1)]
Money = Annotated[int, Field(ge=0, description='Сумма в учебных рублях.')]
Status = Literal['active', 'cancelled']


@with_config(ConfigDict(extra='forbid', strict=True))
class Record(TypedDict):
    pass


class Event(Record):
    id: Text
    name: Text
    description: Text
    location: Text
    start_date: Date
    end_date: Date
    email_required: NotRequired[Annotated[bool, Field(default=False)]]


class Flight(Record):
    id: Text
    origin: Text
    destination: Text
    depart_date: Date
    arrive_date: Date
    price_rub: Money


class Hotel(Record):
    id: Text
    name: Text
    location: Text
    nightly_price_rub: Money
    available_nights: Annotated[
        list[Date], Field(description='Даты начала доступных ночей проживания.')
    ]


class Catalog(Record):
    events: Annotated[list[Event], Field(min_length=1)]
    flights: list[Flight]
    hotels: list[Hotel]


@with_config(ConfigDict(extra='allow', strict=True))
class FlightBooking(TypedDict):
    """Flight booking with a snapshot of the chosen itinerary."""

    id: str
    status: Status
    total_rub: Money
    flight_id: Text
    origin: Text
    destination: Text
    depart_date: Date
    arrive_date: Date


@with_config(ConfigDict(extra='allow', strict=True))
class HotelBooking(TypedDict):
    """Hotel booking with stay dates and a snapshot of the property."""

    id: str
    status: Status
    total_rub: Money
    hotel_id: Text
    hotel_name: Text
    location: Text
    check_in: Date
    check_out: Date
    nights: Annotated[int, Field(ge=1, description='Число ночей [check_in, check_out).')]


@with_config(ConfigDict(extra='allow', strict=True))
class Registration(TypedDict):
    """New registrations include event_id/name/email; legacy fields are preserved."""

    id: str
    status: Status
    event_id: NotRequired[str]
    name: NotRequired[str]
    email: NotRequired[str | None]


class AttemptState(TypedDict):
    flight_bookings: list[FlightBooking]
    hotel_bookings: list[HotelBooking]
    registrations: list[Registration]


class State(AttemptState, Catalog):
    """Operation input: saved attempt plus the current validated catalog."""


class Error(Record):
    ok: Literal[False]
    error: Literal[
        'access_expired',
        'task_expired',
        'call_budget_exhausted',
        'invalid_config',
        'unknown_event',
        'name_required',
        'email_required',
        'registration_not_found',
        'unknown_flight',
        'unknown_hotel',
        'invalid_dates',
        'hotel_unavailable',
        'flight_booking_not_found',
        'hotel_booking_not_found',
    ]
    message: NotRequired[str]
    unavailable_nights: NotRequired[list[Date]]


class Success(Record):
    ok: Literal[True]


class EventsResult(Success):
    events: list[Event]


class FlightsResult(Success):
    flights: list[Flight]


class HotelsResult(Success):
    hotels: list[Hotel]


class RegistrationsResult(Success):
    registrations: list[Registration]


class FlightBookingsResult(Success):
    flight_bookings: list[FlightBooking]
    total_rub: Money


class HotelBookingsResult(Success):
    hotel_bookings: list[HotelBooking]
    total_rub: Money


class RegistrationResult(Success):
    registration: Registration


class FlightBookingResult(Success):
    flight_booking: FlightBooking
    repeated: NotRequired[
        Annotated[bool, Field(description='При бронировании: найдена прежняя бронь.')]
    ]


class HotelBookingResult(Success):
    hotel_booking: HotelBooking
    repeated: NotRequired[
        Annotated[bool, Field(description='При бронировании: найдена прежняя бронь.')]
    ]


ToolResult = (
    EventsResult
    | FlightsResult
    | HotelsResult
    | RegistrationsResult
    | FlightBookingsResult
    | HotelBookingsResult
    | RegistrationResult
    | FlightBookingResult
    | HotelBookingResult
    | Error
)


class ToolResponse(RootModel[ToolResult]):
    # MCP requires an object at the root; success/error keep their existing JSON shape.
    model_config = ConfigDict(
        json_schema_extra={'type': 'object'}, hide_input_in_errors=True
    )
