"""Read the same event configuration for local practice and instructor hosting."""
import json
import os
from datetime import date
from pathlib import Path
from typing import Annotated

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


class Flight(Record):
    id: Text
    origin: Text
    destination: Text
    depart_date: Date
    arrive_date: Date
    price_rub: Money

    @model_validator(mode='after')
    def chronological(self):
        if self.arrive_date < self.depart_date:
            raise ValueError('Прибытие не может быть раньше вылета')
        return self


class Hotel(Record):
    id: Text
    name: Text
    location: Text
    nightly_price_rub: Money
    available_nights: list[Date]


class Event(Record):
    id: Text
    name: Text
    description: Text
    location: Text
    start_date: Date
    end_date: Date
    email_required: bool = False

    @model_validator(mode='after')
    def chronological(self):
        if self.end_date < self.start_date:
            raise ValueError('Конец мероприятия раньше начала')
        return self


def unique_ids(items):
    if len({item.id for item in items}) != len(items):
        raise ValueError('ID должны быть уникальны внутри списка')


class Catalog(Record):
    events: Annotated[list[Event], Field(min_length=1)]
    flights: list[Flight]
    hotels: list[Hotel]

    @model_validator(mode='after')
    def unique_lists(self):
        unique_ids(self.events)
        unique_ids(self.flights)
        unique_ids(self.hotels)
        return self


def load_catalog(path=None):
    default = Path(__file__).resolve().parents[1] / 'config/events.json'
    path = Path(path or os.environ.get('VERIFY_CONFIG', default))
    catalog = Catalog.model_validate(json.loads(path.read_text(encoding='utf-8')))
    return catalog.model_dump()
