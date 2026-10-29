"""Private MCP service over Streamable HTTP via FastMCP 4."""

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, get_type_hints

import uvicorn
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware.middleware import MiddlewareContext
from fastmcp.server.middleware.rate_limiting import SlidingWindowRateLimitingMiddleware
from mcp.server.transport_security import RequestBodyLimitMiddleware
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field, create_model
from starlette.applications import Starlette
from starlette.middleware import Middleware

from auth import StoreTokenVerifier
from catalog import load_catalog
from contracts import State, ToolResponse, ToolResult
from store import DEFAULT_DB_PATH, Store
from travel import (
    book_flight as book_flight_op,
    book_hotel as book_hotel_op,
    cancel_flight_booking as cancel_flight_booking_op,
    cancel_hotel_booking as cancel_hotel_booking_op,
    cancel_registration as cancel_registration_op,
    list_events as list_events_op,
    list_flight_bookings as list_flight_bookings_op,
    list_flights as list_flights_op,
    list_hotel_bookings as list_hotel_bookings_op,
    list_hotels as list_hotels_op,
    list_registrations as list_registrations_op,
    register_for_event as register_for_event_op,
)

MAX_BODY_BYTES = 16 * 1024
RATE_LIMIT_PER_MINUTE = 120
logger = logging.getLogger(__name__)


def _output_model(operation: Callable[..., Any]) -> type:
    return create_model(
        f'{operation.__name__}Result',
        __base__=ToolResponse,
        root=(get_type_hints(operation)['return'], ...),
    )


ListEventsOut = _output_model(list_events_op)
ListFlightsOut = _output_model(list_flights_op)
ListHotelsOut = _output_model(list_hotels_op)
ListRegistrationsOut = _output_model(list_registrations_op)
ListFlightBookingsOut = _output_model(list_flight_bookings_op)
ListHotelBookingsOut = _output_model(list_hotel_bookings_op)
RegisterOut = _output_model(register_for_event_op)
CancelRegistrationOut = _output_model(cancel_registration_op)
BookFlightOut = _output_model(book_flight_op)
BookHotelOut = _output_model(book_hotel_op)
CancelFlightBookingOut = _output_model(cancel_flight_booking_op)
CancelHotelBookingOut = _output_model(cancel_hotel_booking_op)

_READ = ToolAnnotations(
    readOnlyHint=True, openWorldHint=False, idempotentHint=True
)
_WRITE = ToolAnnotations(
    readOnlyHint=False, openWorldHint=False, idempotentHint=True
)
_REGISTER = ToolAnnotations(
    readOnlyHint=False,
    openWorldHint=False,
    idempotentHint=False,
    destructiveHint=False,
)
_DESTRUCTIVE = ToolAnnotations(
    readOnlyHint=False,
    openWorldHint=False,
    idempotentHint=True,
    destructiveHint=True,
)


def _rate_limit_client_id(_context: MiddlewareContext) -> str:
    access = get_access_token()
    return access.client_id if access is not None else 'anonymous'


def _present(result: ToolResult) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type='text', text=json.dumps(result, ensure_ascii=False))],
        structured_content=dict(result),
        is_error=not result['ok'],
    )


class TravelMCP(FastMCP):
    """MCP adapter: auth, rate limits, and tools over Store.execute + travel ops."""

    store: Store

    def __init__(self, store: Store, **kwargs: Any) -> None:
        self.store = store
        kwargs.setdefault('name', 'agent-travel-playground')
        kwargs.setdefault('auth', StoreTokenVerifier(store))
        kwargs.setdefault('mask_error_details', True)
        middleware = list(kwargs.pop('middleware', []) or [])
        middleware.insert(
            0,
            SlidingWindowRateLimitingMiddleware(
                max_requests=RATE_LIMIT_PER_MINUTE,
                window_minutes=1,
                get_client_id=_rate_limit_client_id,
            ),
        )
        super().__init__(middleware=middleware, **kwargs)
        self._register_tools()

    def _student(self) -> str:
        access = get_access_token()
        if access is None:
            raise ToolError('Требуется аутентификация.')
        return access.client_id

    async def _run(
        self,
        name: str,
        body: Callable[[State], ToolResult],
        args: dict[str, Any],
    ) -> CallToolResult:
        student = self._student()
        try:
            result = await asyncio.to_thread(
                self.store.execute, student, name, body, args
            )
        except Exception:
            logger.exception('operation_failed tool=%s', name)
            raise ToolError('Внутренняя ошибка сервиса. Сообщите оператору.') from None
        return _present(result)

    def _register_tools(self) -> None:
        # @self.tool registers immediately; class-method @tool leaves `self` in the schema.
        @self.tool(annotations=_READ, description=list_events_op.__doc__)
        async def list_events() -> Annotated[CallToolResult, ListEventsOut]:
            return await self._run('list_events', list_events_op, {})

        @self.tool(annotations=_READ, description=list_flights_op.__doc__)
        async def list_flights() -> Annotated[CallToolResult, ListFlightsOut]:
            return await self._run('list_flights', list_flights_op, {})

        @self.tool(annotations=_READ, description=list_hotels_op.__doc__)
        async def list_hotels() -> Annotated[CallToolResult, ListHotelsOut]:
            return await self._run('list_hotels', list_hotels_op, {})

        @self.tool(annotations=_READ, description=list_registrations_op.__doc__)
        async def list_registrations() -> Annotated[
            CallToolResult, ListRegistrationsOut
        ]:
            return await self._run('list_registrations', list_registrations_op, {})

        @self.tool(annotations=_READ, description=list_flight_bookings_op.__doc__)
        async def list_flight_bookings() -> Annotated[
            CallToolResult, ListFlightBookingsOut
        ]:
            return await self._run('list_flight_bookings', list_flight_bookings_op, {})

        @self.tool(annotations=_READ, description=list_hotel_bookings_op.__doc__)
        async def list_hotel_bookings() -> Annotated[
            CallToolResult, ListHotelBookingsOut
        ]:
            return await self._run('list_hotel_bookings', list_hotel_bookings_op, {})

        @self.tool(annotations=_REGISTER, description=register_for_event_op.__doc__)
        async def register_for_event(
            event_id: Annotated[str, Field(description='ID из list_events.')],
            name: Annotated[str, Field(description='Имя участника.')],
            email: Annotated[
                str | None, Field(description='Email участника, если требуется.')
            ] = None,
        ) -> Annotated[CallToolResult, RegisterOut]:
            args = {'event_id': event_id, 'name': name, 'email': email}
            return await self._run(
                'register_for_event',
                lambda state: register_for_event_op(state, **args),
                args,
            )

        @self.tool(annotations=_DESTRUCTIVE, description=cancel_registration_op.__doc__)
        async def cancel_registration(
            registration_id: Annotated[
                str, Field(description='ID из list_registrations.')
            ],
        ) -> Annotated[CallToolResult, CancelRegistrationOut]:
            args = {'registration_id': registration_id}
            return await self._run(
                'cancel_registration',
                lambda state: cancel_registration_op(state, **args),
                args,
            )

        @self.tool(annotations=_WRITE, description=book_flight_op.__doc__)
        async def book_flight(
            flight_id: Annotated[str, Field(description='ID из list_flights.')],
        ) -> Annotated[CallToolResult, BookFlightOut]:
            args = {'flight_id': flight_id}
            return await self._run(
                'book_flight',
                lambda state: book_flight_op(state, **args),
                args,
            )

        @self.tool(annotations=_WRITE, description=book_hotel_op.__doc__)
        async def book_hotel(
            hotel_id: Annotated[str, Field(description='ID из list_hotels.')],
            check_in: Annotated[str, Field(description='Дата заезда YYYY-MM-DD.')],
            check_out: Annotated[
                str, Field(description='Дата выезда YYYY-MM-DD, не оплачивается.')
            ],
        ) -> Annotated[CallToolResult, BookHotelOut]:
            args = {
                'hotel_id': hotel_id,
                'check_in': check_in,
                'check_out': check_out,
            }
            return await self._run(
                'book_hotel',
                lambda state: book_hotel_op(state, **args),
                args,
            )

        @self.tool(
            annotations=_DESTRUCTIVE, description=cancel_flight_booking_op.__doc__
        )
        async def cancel_flight_booking(
            flight_booking_id: Annotated[
                str, Field(description='ID из list_flight_bookings.')
            ],
        ) -> Annotated[CallToolResult, CancelFlightBookingOut]:
            args = {'flight_booking_id': flight_booking_id}
            return await self._run(
                'cancel_flight_booking',
                lambda state: cancel_flight_booking_op(state, **args),
                args,
            )

        @self.tool(
            annotations=_DESTRUCTIVE, description=cancel_hotel_booking_op.__doc__
        )
        async def cancel_hotel_booking(
            hotel_booking_id: Annotated[
                str, Field(description='ID из list_hotel_bookings.')
            ],
        ) -> Annotated[CallToolResult, CancelHotelBookingOut]:
            args = {'hotel_booking_id': hotel_booking_id}
            return await self._run(
                'cancel_hotel_booking',
                lambda state: cancel_hotel_booking_op(state, **args),
                args,
            )


def create_app(
    db_path: str | Path | None = None, config_path: str | Path | None = None
) -> Starlette:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s',
    )

    store = Store(db_path or os.environ.get('VERIFY_DB', DEFAULT_DB_PATH), config_path)
    logger.info('Store initialized: %s', store.path)

    try:
        load_catalog(store.config_path)
    except Exception:
        store.close()
        raise

    @asynccontextmanager
    async def lifespan(mcp: TravelMCP) -> AsyncIterator[dict[str, Any]]:
        try:
            yield {}
        finally:
            mcp.store.close()

    mcp = TravelMCP(store, lifespan=lifespan)
    return mcp.http_app(
        json_response=True,
        stateless_http=True,
        host_origin_protection=True,
        allowed_hosts=os.environ.get(
            'VERIFY_HOSTS', '127.0.0.1:*,localhost:*'
        ).split(','),
        allowed_origins=os.environ.get(
            'VERIFY_ORIGINS', 'http://127.0.0.1:*,http://localhost:*'
        ).split(','),
        middleware=[
            Middleware(RequestBodyLimitMiddleware, max_body_size=MAX_BODY_BYTES),
        ],
    )


if __name__ == '__main__':
    uvicorn.run(create_app(), host='127.0.0.1', port=8000, access_log=False)
