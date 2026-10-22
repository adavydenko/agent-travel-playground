"""Flat world config: validation, uniqueness, snapshot independence."""
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / 'service'
sys.path.insert(0, str(SERVICE))
from catalog import load_catalog


@pytest.fixture
def config(tmp_path):
    data = json.loads((ROOT / 'config/events.json').read_text())
    path = tmp_path / 'events.json'
    path.write_text(json.dumps(data))
    return path, data


def test_load_catalog_returns_flat_world():
    world = load_catalog()
    assert set(world) == {'events', 'flights', 'hotels'}
    assert {e['id'] for e in world['events']} >= {'vladivostok-ai', 'kazan-engineering'}
    assert all('request' not in e for e in world['events'])
    assert all('available' not in f and 'direction' not in f for f in world['flights'])
    assert all('location' in h for h in world['hotels'])


@pytest.mark.parametrize('mistake', [
    'duplicate_event', 'duplicate_flight', 'bad_date', 'date_order',
    'negative_price', 'typo', 'empty_events',
])
def test_invalid_config_rejected(config, mistake):
    path, data = config
    if mistake == 'duplicate_event':
        data['events'].append(copy.deepcopy(data['events'][0]))
    if mistake == 'duplicate_flight':
        data['flights'].append(copy.deepcopy(data['flights'][0]))
    if mistake == 'bad_date':
        data['hotels'][0]['available_nights'][0] = '2027-02-30'
    if mistake == 'date_order':
        data['events'][0]['end_date'] = '2026-01-01'
        data['events'][0]['start_date'] = '2027-01-01'
    if mistake == 'negative_price':
        data['flights'][0]['price_rub'] = -1
    if mistake == 'typo':
        data['events'][0]['lokation'] = 'X'
    if mistake == 'empty_events':
        data['events'] = []
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_catalog(path)


def test_cli_check_config(config, tmp_path):
    path, _ = config
    db = tmp_path / 'should-not-exist.sqlite3'
    result = subprocess.run(
        [sys.executable, str(SERVICE / 'admin.py'), '--config', str(path),
         '--db', str(db), 'check-config'],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'kazan-engineering' in result.stdout and not db.exists()
