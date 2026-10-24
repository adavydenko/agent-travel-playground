"""Private MCP service over Streamable HTTP; application resources live in a factory."""

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import partial, wraps
from inspect import signature
from pathlib import Path
from typing import Annotated, Any

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import create_model
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware

import travel
from catalog import load_catalog
from contracts import ToolResponse
from middleware import (
    PrivateBearerBackend,
    RequestRateLimitMiddleware,
    StoreTokenVerifier,
    authentication_error,
)
from store import DEFAULT_DB_PATH, Operation, Store

MAX_BODY_BYTES = 16 * 1024
logger = logging.getLogger(__name__)


def register_tool(
    mcp: FastMCP,
    store: Store,
    operation: Operation,
    *,
    read_only: bool = False,
    destructive: bool = False,
    idempotent: bool = True,
) -> None:
    @wraps(operation)
    async def invoke(**args: Any) -> CallToolResult:
        request = mcp.get_context().request_context.request
        if request is None or not request.user.is_authenticated:
            raise ToolError('Требуется аутентификация.')
        try:
            result = await asyncio.to_thread(
                store.call, request.user.identity, operation, args
            )
        except Exception:
            logger.exception('operation_failed tool=%s', operation.__name__)
            raise ToolError('Внутренняя ошибка сервиса. Сообщите оператору.') from None
        return CallToolResult(
            content=[
                TextContent(type='text', text=json.dumps(result, ensure_ascii=False))
            ],
            structuredContent=dict(result),
            isError=not result['ok'],
        )

    # Hide internal state and publish this operation's result, without a result wrapper.
    public_signature = signature(partial(operation, None))
    response_model = create_model(
        f'{operation.__name__}Result',
        __base__=ToolResponse,
        root=(public_signature.return_annotation, ...),
    )
    invoke.__signature__ = public_signature.replace(  # type: ignore[attr-defined]
        return_annotation=Annotated[CallToolResult, response_model]
    )
    mcp.add_tool(
        invoke,
        name=operation.__name__,
        description=operation.__doc__,
        annotations=ToolAnnotations(
            readOnlyHint=read_only,
            destructiveHint=destructive,
            idempotentHint=idempotent,
            openWorldHint=False,
        ),
    )


def create_app(
    db_path: str | Path | None = None, config_path: str | Path | None = None
) -> Starlette:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s',
    )
    store = Store(db_path or os.environ.get('VERIFY_DB', DEFAULT_DB_PATH), config_path)
    try:
        load_catalog(store.config_path)
    except Exception:
        store.close()
        raise
    mcp = FastMCP(
        'agent-travel-playground',
        stateless_http=True,
        json_response=True,
        max_request_body_size=MAX_BODY_BYTES,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=os.environ.get(
                'VERIFY_HOSTS', '127.0.0.1:*,localhost:*'
            ).split(','),
            allowed_origins=os.environ.get(
                'VERIFY_ORIGINS', 'http://127.0.0.1:*,http://localhost:*'
            ).split(','),
        ),
    )
    register_tool(mcp, store, travel.list_events, read_only=True)
    register_tool(mcp, store, travel.list_flights, read_only=True)
    register_tool(mcp, store, travel.list_hotels, read_only=True)
    register_tool(mcp, store, travel.register_for_event, idempotent=False)
    register_tool(mcp, store, travel.list_registrations, read_only=True)
    register_tool(mcp, store, travel.cancel_registration, destructive=True)
    register_tool(mcp, store, travel.book_flight)
    register_tool(mcp, store, travel.book_hotel)
    register_tool(mcp, store, travel.cancel_booking, destructive=True)
    register_tool(mcp, store, travel.list_bookings, read_only=True)

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            store.close()

    return Starlette(
        routes=mcp.streamable_http_app().routes,
        lifespan=lifespan,
        middleware=[
            Middleware(
                AuthenticationMiddleware,
                backend=PrivateBearerBackend(StoreTokenVerifier(store)),
                on_error=authentication_error,
            ),
            Middleware(RequestRateLimitMiddleware),
        ],
    )


if __name__ == '__main__':
    uvicorn.run(create_app(), host='127.0.0.1', port=8000, access_log=False)
