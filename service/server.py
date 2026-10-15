"""MCP Streamable HTTP. Private course bearer credentials, not OAuth discovery."""
import os
import time
from collections import defaultdict, deque
from contextvars import ContextVar
from typing import Literal

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse

from store import Store

store = Store(os.environ.get('VERIFY_DB', 'data/verification.sqlite3'))
identity = ContextVar('student')
allowed_hosts = os.environ.get('VERIFY_HOSTS', '127.0.0.1:*,localhost:*').split(',')
allowed_origins = os.environ.get('VERIFY_ORIGINS', 'http://127.0.0.1:*,http://localhost:*').split(',')
mcp = FastMCP('agent-travel-playground', stateless_http=True, json_response=True,
              transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True,
                  allowed_hosts=allowed_hosts, allowed_origins=allowed_origins))


def invoke(tool, **args):
    return store.call(identity.get(), tool, args)


@mcp.tool()
def start_task(kind: Literal['travel'] = 'travel') -> dict:
    """Начать персональную поездку: 2 часа, до 100 вызовов. Повтор возвращает ту же попытку."""
    return invoke('start_task', kind=kind)


@mcp.tool()
def inspect_task(task_id: str) -> dict:
    """Прочитать запрос пользователя, рейсы и гостиницы. Даты местные, цены в учебных рублях.
    available_nights — даты начала доступных ночей. Ночь выезда не оплачивается.
    """
    return invoke('inspect_task', task_id=task_id)


@mcp.tool()
def book_flight(task_id: str, flight_id: str) -> dict:
    """Забронировать один рейс на одного человека. Недоступный рейс возвращает ошибку.
    Повтор того же рейса не создаёт дубликат. Совместимость всей поездки проверяет finish.
    """
    return invoke('book_flight', task_id=task_id, flight_id=flight_id)


@mcp.tool()
def book_hotel(task_id: str, hotel_id: str, check_in: str, check_out: str) -> dict:
    """Забронировать один номер: даты YYYY-MM-DD, от 1 до 14 ночей [check_in, check_out).
    Все ночи должны быть доступны. Заезд — дата прибытия, выезд — дата обратного вылета.
    Повтор тех же гостиницы и дат возвращает прежнюю бронь.
    """
    return invoke('book_hotel', task_id=task_id, hotel_id=hotel_id,
                  check_in=check_in, check_out=check_out)


@mcp.tool()
def cancel_booking(task_id: str, booking_id: str) -> dict:
    """Отменить свою бронь бесплатно. Повтор отмены безопасен. После успешного finish изменений нет."""
    return invoke('cancel_booking', task_id=task_id, booking_id=booking_id)


@mcp.tool()
def list_bookings(task_id: str) -> dict:
    """Прочитать брони, их статусы и сумму активных броней."""
    return invoke('list_bookings', task_id=task_id)


@mcp.tool()
def finish(task_id: str) -> dict:
    """Проверить поездку: туда + гостиница + обратно, даты, бюджет, отсутствие лишних броней.
    passed=true выдаёт квитанцию и закрывает попытку; passed=false позволяет исправления.
    """
    return invoke('finish', task_id=task_id)


class Gate:
    def __init__(self, app):
        self.app = app
        self.requests = defaultdict(deque)

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = dict(scope['headers'])
        bearer = headers.get(b'authorization', b'').decode('latin1')
        student = store.authenticate(bearer[7:]) if bearer.startswith('Bearer ') else None
        if student is None:
            return await JSONResponse({'error': 'unauthorized'}, status_code=401,
                                      headers={'WWW-Authenticate': 'Bearer'})(scope, receive, send)
        now = time.monotonic()
        recent = self.requests[student]
        while recent and recent[0] < now - 60:
            recent.popleft()
        if len(recent) >= 120:
            return await JSONResponse({'error': 'rate_limited'}, status_code=429,
                                      headers={'Retry-After': '60'})(scope, receive, send)
        recent.append(now)
        # Bound actual bytes, including chunked bodies; reject before JSON parsing/auditing.
        body = bytearray()
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            body.extend(message.get('body', b''))
            if len(body) > 16384:
                return await JSONResponse({'error': 'body_too_large'}, status_code=413)(scope, receive, send)
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
