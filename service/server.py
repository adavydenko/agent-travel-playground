"""MCP Streamable HTTP. Private course bearer credentials, not OAuth discovery."""

import os
import time
from collections import defaultdict, deque
from contextvars import ContextVar

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse

from store import DEFAULT_DB_PATH, Store

RATE_LIMIT = 120
RATE_WINDOW_SECONDS = 60
MAX_BODY_BYTES = 16 * 1024

# Store loads and validates the catalog; bad config fails the process at startup.
store = Store(os.environ.get('VERIFY_DB', DEFAULT_DB_PATH))
identity = ContextVar('student')
allowed_hosts = os.environ.get('VERIFY_HOSTS', '127.0.0.1:*,localhost:*').split(',')
allowed_origins = os.environ.get(
    'VERIFY_ORIGINS', 'http://127.0.0.1:*,http://localhost:*'
).split(',')
mcp = FastMCP(
    'agent-travel-playground',
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    ),
)


def invoke(tool, **args):
    return store.call(identity.get(), tool, args)


@mcp.tool()
def list_events() -> dict:
    """Список мероприятий: id, название, описание, локация, даты,
    нужен ли email при регистрации.
    """
    return invoke('list_events')


@mcp.tool()
def list_flights() -> dict:
    """Список рейсов: id, города, даты вылета и прибытия, цена в учебных рублях."""
    return invoke('list_flights')


@mcp.tool()
def list_hotels() -> dict:
    """Список гостиниц: id, название, локация, цена за ночь.
    available_nights — даты начала доступных ночей. Ночь выезда не оплачивается.
    """
    return invoke('list_hotels')


@mcp.tool()
def register_for_event(event_id: str, name: str, email: str | None = None) -> dict:
    """Одна регистрация = один участник. Повторный вызов добавляет ещё одного человека.
    Email обязателен, только если у мероприятия email_required=true.
    """
    args = dict(event_id=event_id, name=name)
    if email is not None:
        args['email'] = email
    return invoke('register_for_event', **args)


@mcp.tool()
def list_registrations() -> dict:
    """Показать регистрации текущего доступа на мероприятия."""
    return invoke('list_registrations')


@mcp.tool()
def cancel_registration(registration_id: str) -> dict:
    """Отменить свою регистрацию. Повтор отмены безопасен."""
    return invoke('cancel_registration', registration_id=registration_id)


@mcp.tool()
def book_flight(flight_id: str) -> dict:
    """Забронировать один рейс на одного человека.
    Повтор того же рейса не создаёт дубликат.
    """
    return invoke('book_flight', flight_id=flight_id)


@mcp.tool()
def book_hotel(hotel_id: str, check_in: str, check_out: str) -> dict:
    """Забронировать один номер: даты YYYY-MM-DD,
    от 1 до 14 ночей [check_in, check_out).
    Все ночи должны быть доступны.
    Повтор тех же гостиницы и дат возвращает прежнюю бронь.
    """
    return invoke(
        'book_hotel', hotel_id=hotel_id, check_in=check_in, check_out=check_out
    )


@mcp.tool()
def cancel_booking(booking_id: str) -> dict:
    """Отменить свою бронь бесплатно. Повтор отмены безопасен."""
    return invoke('cancel_booking', booking_id=booking_id)


@mcp.tool()
def list_bookings() -> dict:
    """Прочитать брони, их статусы и сумму активных броней."""
    return invoke('list_bookings')


def bearer_token(value: str) -> str | None:
    scheme, _, token = value.strip().partition(' ')
    if scheme.lower() != 'bearer':
        return None
    return token.strip() or None


class Gate:
    """Bearer auth, per-student rate limit, and a hard request-body cap."""

    def __init__(self, app):
        self.app = app
        self.requests = defaultdict(deque)

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = dict(scope['headers'])
        token = bearer_token(headers.get(b'authorization', b'').decode('latin1'))
        student = store.authenticate(token) if token else None
        if student is None:
            return await JSONResponse(
                {'error': 'unauthorized'},
                status_code=401,
                headers={'WWW-Authenticate': 'Bearer'},
            )(scope, receive, send)
        now = time.monotonic()
        recent = self.requests[student]
        while recent and recent[0] < now - RATE_WINDOW_SECONDS:
            recent.popleft()
        if len(recent) >= RATE_LIMIT:
            return await JSONResponse(
                {'error': 'rate_limited'},
                status_code=429,
                headers={'Retry-After': str(RATE_WINDOW_SECONDS)},
            )(scope, receive, send)
        recent.append(now)
        # Bound actual bytes, including chunked bodies;
        # reject before JSON parsing/auditing.
        body = bytearray()
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            body.extend(message.get('body', b''))
            if len(body) > MAX_BODY_BYTES:
                return await JSONResponse({'error': 'body_too_large'}, status_code=413)(
                    scope, receive, send
                )
            if not message.get('more_body', False):
                break
        consumed = False

        async def replay():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
            return await receive()

        marker = identity.set(student)
        try:
            await self.app(scope, replay, send)
        finally:
            identity.reset(marker)


app = Gate(mcp.streamable_http_app())


if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=8000, access_log=False)
