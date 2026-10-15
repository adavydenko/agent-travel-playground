"""Read the same event configuration for local practice and instructor hosting."""
import json
import os
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator


def iso_date(value):
    if date.fromisoformat(value).isoformat() != value:
        raise ValueError('Используйте дату YYYY-MM-DD')
    return value


Date = Annotated[str, AfterValidator(iso_date)]
Text = Annotated[str, Field(min_length=1)]
Money = Annotated[int, Field(ge=0)]


class Record(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Request(Record):
    origin: Text
    destination: Text
    depart_date: Date
    return_date: Date
    budget_rub: Money
    passengers: Literal[1]


class Flight(Record):
    id: Text
    direction: Literal['outbound', 'return']
    origin: Text
    destination: Text
    depart_date: Date
    arrive_date: Date
    price_rub: Money
    available: bool

    @model_validator(mode='after')
    def chronological(self):
        if self.arrive_date < self.depart_date:
            raise ValueError('Прибытие не может быть раньше вылета')
        return self


class Hotel(Record):
    id: Text
    name: Text
    city: Text
    nightly_price_rub: Money
    available_nights: list[Date]


def unique_ids(items):
    if len({item.id for item in items}) != len(items):
        raise ValueError('ID должны быть уникальны внутри списка')


class Event(Record):
    id: Text
    name: Text
    start_date: Date
    end_date: Date
    request: Request
    flights: list[Flight]
    hotels: list[Hotel]

    @model_validator(mode='after')
    def consistent(self):
        r = self.request
        if not r.depart_date < self.start_date <= self.end_date < r.return_date:
            raise ValueError('Даты поездки должны охватывать все дни мероприятия')
        if not 1 <= (date.fromisoformat(r.return_date) - date.fromisoformat(r.depart_date)).days <= 14:
            raise ValueError('Поездка должна длиться от 1 до 14 дней')
        if r.origin == r.destination:
            raise ValueError('Города отправления и назначения должны различаться')
        unique_ids(self.flights)
        unique_ids(self.hotels)
        return self


class Catalog(Record):
    events: Annotated[list[Event], Field(min_length=1)]

    @model_validator(mode='after')
    def unique_events(self):
        unique_ids(self.events)
        return self


def load_catalog(path=None):
    default = Path(__file__).resolve().parents[1] / 'config/events.json'
    path = Path(path or os.environ.get('VERIFY_CONFIG', default))
    catalog = Catalog.model_validate(json.loads(path.read_text(encoding='utf-8')))
    return {event.id: event.model_dump() for event in catalog.events}
