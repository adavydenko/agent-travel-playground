"""MCP over real local HTTP: auth boundaries, tools, and rate limit."""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from mcp_client import call, connect, schemas
from store import Store

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_TOOLS = {
    'list_events',
    'list_flights',
    'list_hotels',
    'register_for_event',
    'list_registrations',
    'cancel_registration',
    'book_flight',
    'book_hotel',
    'cancel_booking',
    'list_bookings',
}


@pytest.fixture(scope='module')
def live(tmp_path_factory):
    folder = tmp_path_factory.mktemp('verification-http')
    config = json.loads((ROOT / 'config/events.json').read_text())
    config_path = folder / 'custom-events.json'
    config_path.write_text(json.dumps(config))
    store = Store(folder / 'db.sqlite3', config_path)
    alice = store.issue('alice')
    bob = store.issue('bob')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}/mcp'
    with open(folder / 'server.log', 'w') as log:
        proc = subprocess.Popen(
            [
                sys.executable,
                '-m',
                'uvicorn',
                'server:app',
                '--host',
                '127.0.0.1',
                '--port',
                str(port),
                '--no-access-log',
            ],
            cwd=ROOT / 'service',
            env={
                **os.environ,
                'VERIFY_DB': store.path,
                'VERIFY_CONFIG': str(config_path),
            },
            stdout=log,
            stderr=log,
        )
        try:
            for _ in range(100):
                if proc.poll() is not None:
                    raise RuntimeError((folder / 'server.log').read_text())
                try:
                    if httpx.get(url).status_code == 401:
                        break
                except httpx.ConnectError:
                    pass
                time.sleep(0.05)
            else:
                raise RuntimeError('server startup timeout')
            yield url, store, alice, bob
        finally:
            proc.terminate()
            proc.wait(timeout=10)


def test_http_access_boundaries(live):
    url, _, alice, _ = live
    assert httpx.post(url, json={}).status_code == 401
    assert (
        httpx.post(
            url, headers={'Authorization': 'Bearer incorrect'}, json={}
        ).status_code
        == 401
    )
    assert (
        httpx.post(
            url, headers={'Authorization': f'bearer {alice}'}, json={}
        ).status_code
        != 401
    )
    headers = {
        'Authorization': f'Bearer {alice}',
        'Origin': 'https://untrusted.example',
    }
    assert httpx.post(url, headers=headers, json={}).status_code == 403
    headers = {'Authorization': f'Bearer {alice}', 'Host': 'untrusted.example'}
    assert httpx.post(url, headers=headers, json={}).status_code == 421
    assert (
        httpx.post(
            url, headers={'Authorization': f'Bearer {alice}'}, content=b'x' * 17000
        ).status_code
        == 413
    )


@pytest.mark.asyncio
async def test_real_mcp_two_principals(live):
    url, store, alice, bob = live
    async with connect(url, alice) as a, connect(url, bob) as b:
        names = {x['name'] for x in await schemas(a)}
        assert names == EXPECTED_TOOLS
        events_a, events_b = await asyncio.gather(
            call(a, 'list_events', {}), call(b, 'list_events', {})
        )
        assert {e['id'] for e in events_a['events']} >= {
            'vladivostok-ai',
            'kazan-engineering',
        }
        assert events_a['events'] == events_b['events']

        reg = await call(
            a, 'register_for_event', {'event_id': 'vladivostok-ai', 'name': 'Alice'}
        )
        assert reg['ok']
        bob_regs = await call(b, 'list_registrations', {})
        assert bob_regs['registrations'] == []

        flights = await call(a, 'list_flights', {})
        hotels = await call(a, 'list_hotels', {})
        out = next(f for f in flights['flights'] if f['id'] == 'vvo-out-night')
        back = next(f for f in flights['flights'] if f['id'] == 'vvo-back')
        hotel = next(h for h in hotels['hotels'] if h['id'] == 'vvo-mayak')
        assert (await call(a, 'book_flight', {'flight_id': out['id']}))['ok']
        assert (await call(a, 'book_flight', {'flight_id': back['id']}))['ok']
        assert (
            await call(
                a,
                'book_hotel',
                {
                    'hotel_id': hotel['id'],
                    'check_in': out['arrive_date'],
                    'check_out': back['depart_date'],
                },
            )
        )['ok']
        bookings = await call(a, 'list_bookings', {})
        assert len([b for b in bookings['bookings'] if b['status'] == 'active']) == 3
        assert (await call(b, 'list_bookings', {}))['bookings'] == []
        missing = await call(a, 'book_flight', {})
        assert missing['error'] == 'mcp_tool_error'
    store.revoke('bob')
    assert (
        httpx.post(url, headers={'Authorization': f'Bearer {bob}'}, json={}).status_code
        == 401
    )


def test_http_rate_limit(live):
    url, store, _, _ = live
    token = store.issue('rate-test')
    with httpx.Client(headers={'Authorization': f'Bearer {token}'}) as client:
        statuses = [client.post(url, json={}).status_code for _ in range(121)]
    assert statuses[-1] == 429
