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
from jsonschema import Draft202012Validator, ValidationError
from sqlalchemy import select

from mcp_client import call, connect, schemas
from db.models import Attempt
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
    'list_flight_bookings',
    'list_hotel_bookings',
    'cancel_flight_booking',
    'cancel_hotel_booking',
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
                'server:create_app',
                '--factory',
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
                    # Stateless HTTP exposes POST (and DELETE); unauthenticated → 401.
                    if httpx.post(url, json={}).status_code == 401:
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
    assert (
        httpx.post(
            url,
            headers={'Authorization': f'Bearer {alice}'},
            content=iter([b'x' * 8192, b'x' * 8192, b'x']),
        ).status_code
        == 413
    )


@pytest.mark.asyncio
async def test_real_mcp_two_principals(live):
    url, store, alice, bob = live
    async with connect(url, alice) as a, connect(url, bob) as b:
        names = {x['name'] for x in await schemas(a)}
        assert names == EXPECTED_TOOLS
        tools = {tool.name: tool for tool in (await a.list_tools()).tools}
        for name, tool in tools.items():
            assert tool.description
            assert tool.annotations is not None
            assert tool.annotations.read_only_hint == name.startswith('list_')
            assert tool.annotations.open_world_hint is False
        assert tools['register_for_event'].annotations.idempotent_hint is False
        assert tools['book_flight'].annotations.idempotent_hint is True
        assert tools['cancel_flight_booking'].annotations.destructive_hint is True
        assert tools['cancel_hotel_booking'].annotations.destructive_hint is True
        assert set(tools['book_hotel'].input_schema['properties']) == {
            'hotel_id',
            'check_in',
            'check_out',
        }
        assert set(tools['book_hotel'].input_schema['required']) == {
            'hotel_id',
            'check_in',
            'check_out',
        }
        assert tools['list_events'].input_schema.get('properties', {}) == {}
        assert 'email' not in tools['register_for_event'].input_schema['required']
        for tool in tools.values():
            for field in tool.input_schema.get('properties', {}).values():
                assert field.get('description')
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
        flights_booked = await call(a, 'list_flight_bookings', {})
        hotels_booked = await call(a, 'list_hotel_bookings', {})
        assert (
            len(
                [
                    b
                    for b in flights_booked['flight_bookings']
                    if b['status'] == 'active'
                ]
            )
            == 2
        )
        assert (
            len(
                [b for b in hotels_booked['hotel_bookings'] if b['status'] == 'active']
            )
            == 1
        )
        assert (await call(b, 'list_flight_bookings', {}))['flight_bookings'] == []
        assert (await call(b, 'list_hotel_bookings', {}))['hotel_bookings'] == []
        flight_booking_id = flights_booked['flight_bookings'][0]['id']
        assert (
            await call(
                b, 'cancel_flight_booking', {'flight_booking_id': flight_booking_id}
            )
        )['error'] == 'flight_booking_not_found'
        for _ in range(2):
            assert (
                await call(
                    a,
                    'cancel_flight_booking',
                    {'flight_booking_id': flight_booking_id},
                )
            )['flight_booking']['status'] == 'cancelled'
        registration_id = reg['registration']['id']
        assert (
            await call(b, 'cancel_registration', {'registration_id': registration_id})
        )['error'] == 'registration_not_found'
        for _ in range(2):
            assert (
                await call(
                    a, 'cancel_registration', {'registration_id': registration_id}
                )
            )['registration']['status'] == 'cancelled'
        assert (await call(a, 'list_registrations', {}))['registrations'][0][
            'status'
        ] == 'cancelled'
        calls_before = next(
            r['calls'] for r in store.report() if r['student'] == 'alice'
        )
        missing = await call(a, 'book_flight', {})
        assert missing['error'] == 'mcp_tool_error'
        for name in ('inspect_task', 'finish'):
            assert (await call(a, name, {}))['error'] == 'mcp_tool_error'
        assert next(r['calls'] for r in store.report() if r['student'] == 'alice') == (
            calls_before
        )
    store.revoke('bob')
    assert (
        httpx.post(url, headers={'Authorization': f'Bearer {bob}'}, json={}).status_code
        == 401
    )


@pytest.mark.asyncio
async def test_http_reports_business_errors(live):
    url, store, _, _ = live
    token = store.issue('error-test')
    async with connect(url, token) as session:
        refused = await session.call_tool('book_flight', {'flight_id': 'missing'})
        assert refused.is_error
        assert refused.structured_content == {'ok': False, 'error': 'unknown_flight'}
        assert json.loads(refused.content[0].text) == refused.structured_content
        assert await call(session, 'book_flight', {'flight_id': 'missing'}) == (
            refused.structured_content
        )


@pytest.mark.asyncio
async def test_operator_clears_bookings_without_restarting_mcp(live):
    url, store, _, _ = live
    alice, bob = store.issue('clear-alice'), store.issue('clear-bob')
    async with connect(url, alice) as a, connect(url, bob) as b:
        for session in (a, b):
            assert (await call(session, 'book_flight', {'flight_id': 'vvo-out-night'}))[
                'ok'
            ]
        registration = await call(
            a, 'register_for_event', {'event_id': 'vladivostok-ai', 'name': 'A'}
        )
        before = store.report()
        events_before = store.events('clear-alice')
        cleared = subprocess.run(
            [
                sys.executable,
                str(ROOT / 'service/admin.py'),
                '--db',
                store.path,
                '--config',
                str(Path(store.path).parent / 'missing-config.json'),
                'clear-bookings',
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert cleared.returncode == 0, cleared.stderr
        assert json.loads(cleared.stdout)['bookings_cleared'] >= 2
        assert store.report() == before
        assert store.events('clear-alice') == events_before
        for session in (a, b):
            assert await call(session, 'list_flight_bookings', {}) == {
                'ok': True,
                'flight_bookings': [],
                'total_rub': 0,
            }
            assert await call(session, 'list_hotel_bookings', {}) == {
                'ok': True,
                'hotel_bookings': [],
                'total_rub': 0,
            }
        assert (await call(a, 'list_registrations', {}))['registrations'] == [
            registration['registration']
        ]
        rebooked = await call(a, 'book_flight', {'flight_id': 'vvo-out-night'})
        assert rebooked['ok'] and not rebooked['repeated']


@pytest.mark.asyncio
async def test_http_publishes_and_honors_data_contracts(live):
    url, store, _, _ = live
    token = store.issue('contracts-test')
    async with connect(url, token) as session:
        advertised = {tool['name']: tool for tool in await schemas(session)}
        validators = {}
        for name, tool in advertised.items():
            schema = tool['outputSchema']
            assert schema['type'] == 'object'
            Draft202012Validator.check_schema(schema)
            validators[name] = Draft202012Validator(schema)

        async def checked(name, args):
            response = await session.call_tool(name, args)
            validators[name].validate(response.structured_content)
            assert json.loads(response.content[0].text) == response.structured_content
            assert response.is_error == (not response.structured_content['ok'])
            return response.structured_content

        events = await checked('list_events', {})
        flights = await checked('list_flights', {})
        hotels = await checked('list_hotels', {})
        reg = await checked(
            'register_for_event', {'event_id': events['events'][0]['id'], 'name': 'A'}
        )
        await checked('list_registrations', {})
        booked = await checked(
            'book_flight', {'flight_id': flights['flights'][0]['id']}
        )
        hotel = next(h for h in hotels['hotels'] if h['id'] == 'vvo-mayak')
        await checked(
            'book_hotel',
            {
                'hotel_id': hotel['id'],
                'check_in': '2027-03-11',
                'check_out': '2027-03-13',
            },
        )
        await checked('list_flight_bookings', {})
        await checked('list_hotel_bookings', {})
        await checked(
            'cancel_flight_booking',
            {'flight_booking_id': booked['flight_booking']['id']},
        )
        await checked(
            'cancel_registration', {'registration_id': reg['registration']['id']}
        )
        await checked('book_flight', {'flight_id': 'missing'})
        await checked(
            'book_hotel',
            {'hotel_id': hotel['id'], 'check_in': 'bad', 'check_out': 'bad'},
        )

        # Schemas must describe nested records and reject broken success payloads.
        invalid = json.loads(json.dumps(flights))
        del invalid['flights'][0]['destination']
        with pytest.raises(ValidationError):
            validators['list_flights'].validate(invalid)
        invalid = json.loads(json.dumps(booked))
        invalid['flight_booking']['total_rub'] = 'free'
        with pytest.raises(ValidationError):
            validators['book_flight'].validate(invalid)
        with pytest.raises(ValidationError):
            validators['book_flight'].validate({'ok': True})


@pytest.mark.asyncio
async def test_http_reloads_config_and_recovers_from_invalid_json(live):
    url, store, _, _ = live
    token = store.issue('config-test')
    path = Path(store.config_path)
    original = path.read_text()
    try:
        async with connect(url, token) as session:
            assert (await call(session, 'list_flights', {}))['flights']
            data = json.loads(original)
            data['flights'] = []
            path.write_text(json.dumps(data))
            assert (await call(session, 'list_flights', {}))['flights'] == []
            path.write_text('{')
            refused = await session.call_tool('list_flights', {})
            assert refused.is_error
            assert refused.structured_content['error'] == 'invalid_config'
            path.write_text(original)
            assert (await call(session, 'list_flights', {}))['flights']
    finally:
        path.write_text(original)


@pytest.mark.asyncio
async def test_http_rate_limit(live):
    url, store, _, _ = live
    token = store.issue('rate-test')
    # FastMCP sliding-window limit is per authenticated MCP request (not raw HTTP).
    # tools/list does not consume the attempt call budget.
    async with connect(url, token) as session:
        limited = False
        for _ in range(150):
            try:
                await session.list_tools()
            except Exception as error:
                assert 'Rate limit' in str(error)
                limited = True
                break
        assert limited


@pytest.mark.asyncio
async def test_http_masks_internal_errors_and_keeps_private_data_out_of_logs(live):
    url, store, _, _ = live
    token = store.issue('diagnostic-test')
    async with connect(url, token) as session:
        registered = await call(
            session,
            'register_for_event',
            {
                'event_id': 'vladivostok-ai',
                'name': 'Private Participant',
                'email': 'do-not-log@example.com',
            },
        )
        assert registered['ok']
        # Simulate damaged persisted state through an independent operator session.
        with store.database.write_sessions.begin() as transaction:
            attempt = transaction.scalar(
                select(Attempt).where(Attempt.student == 'diagnostic-test')
            )
            attempt.state = {}
        failed = await session.call_tool('list_flight_bookings', {})
        assert failed.is_error
        message = '\n'.join(item.text for item in failed.content if item.type == 'text')
        assert 'Внутренняя ошибка сервиса' in message
        assert 'KeyError' not in message and 'Traceback' not in message
        # Output validation errors must also keep participant data out of diagnostics.
        with store.database.write_sessions.begin() as transaction:
            attempt = transaction.scalar(
                select(Attempt).where(Attempt.student == 'diagnostic-test')
            )
            invalid = dict(registered['registration'])
            invalid['name'] = {'private': 'Private Participant'}
            attempt.state = {
                'flight_bookings': [],
                'hotel_bookings': [],
                'registrations': [invalid],
            }
        failed = await session.call_tool('list_registrations', {})
        assert failed.is_error
        message = '\n'.join(item.text for item in failed.content if item.type == 'text')
        assert 'Внутренняя ошибка сервиса' in message
        assert 'ValidationError' not in message
    log = (Path(store.path).parent / 'server.log').read_text()
    assert 'operation_failed' in log
    assert token not in log
    assert 'Private Participant' not in log
    assert 'do-not-log@example.com' not in log
