"""Load a strict catalog using the same data contracts published through MCP."""

import json
import os
from pathlib import Path

from pydantic import TypeAdapter

from contracts import Catalog

CATALOG_ADAPTER = TypeAdapter(Catalog)


def load_catalog(path: str | Path | None = None) -> Catalog:
    default = Path(__file__).resolve().parents[1] / 'config/events.json'
    source = Path(path or os.environ.get('VERIFY_CONFIG') or default)
    catalog = CATALOG_ADAPTER.validate_python(
        json.loads(source.read_text(encoding='utf-8'))
    )

    for items in (catalog['events'], catalog['flights'], catalog['hotels']):
        if len({item['id'] for item in items}) != len(items):
            raise ValueError('ID должны быть уникальны внутри списка')

    for event in catalog['events']:
        if event['end_date'] < event['start_date']:
            raise ValueError('Конец мероприятия раньше начала')

    for flight in catalog['flights']:
        if flight['arrive_date'] < flight['depart_date']:
            raise ValueError('Прибытие не может быть раньше вылета')

    return catalog
