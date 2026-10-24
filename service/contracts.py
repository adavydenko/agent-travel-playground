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
class Booking(TypedDict):
    """New bookings include kind/item_id; old saved records may omit them.

    Hotel bookings also include check_in/check_out. Extra historical fields survive.
    """

    id: str
    status: Status
    total_rub: Money
    kind: NotRequired[Literal['flight', 'hotel']]
    item_id: NotRequired[Annotated[str, Field(description='ID рейса или гостиницы.')]]
    check_in: NotRequired[Date]
    check_out: NotRequired[Date]


@with_config(ConfigDict(extra='allow', strict=True))
class Registration(TypedDict):
    """New registrations include event_id/name/email; legacy fields are preserved."""

    id: str
    status: Status
    event_id: NotRequired[str]
    name: NotRequired[str]
    email: NotRequired[str | None]


class AttemptState(TypedDict):
    bookings: list[Booking]
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
        'booking_not_found',
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


class BookingsResult(Success):
    bookings: list[Booking]
    total_rub: Money


class RegistrationResult(Success):
    registration: Registration


class BookingResult(Success):
    booking: Booking
    repeated: NotRequired[
        Annotated[bool, Field(description='При бронировании: найдена прежняя бронь.')]
    ]


ToolResult = (
    EventsResult
    | FlightsResult
    | HotelsResult
    | RegistrationsResult
    | BookingsResult
    | RegistrationResult
    | BookingResult
    | Error
)


class ToolResponse(RootModel[ToolResult]):
    # MCP requires an object at the root; success/error keep their existing JSON shape.
    model_config = ConfigDict(
        json_schema_extra={'type': 'object'}, hide_input_in_errors=True
    )
