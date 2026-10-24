"""Готовый транспорт для проверочных работ; решения задач здесь нет."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

LOOPBACK_HOSTS = frozenset({'localhost', '127.0.0.1', '::1'})


def require_secure_url(url: str) -> None:
    parsed = urlparse(url)
    loopback = parsed.scheme == 'http' and parsed.hostname in LOOPBACK_HOSTS
    if parsed.scheme != 'https' and not loopback:
        raise ValueError('Используйте HTTPS; HTTP допустим только на localhost.')


@asynccontextmanager
async def connect(url: str, token: str) -> AsyncIterator[ClientSession]:
    require_secure_url(url)
    async with (
        httpx.AsyncClient(
            headers={'Authorization': f'Bearer {token}'}, timeout=30
        ) as http,
        streamable_http_client(url, http_client=http) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield session


async def call(
    session: ClientSession, name: str, args: dict[str, Any]
) -> dict[str, Any]:
    result = await session.call_tool(name, args)
    if result.structuredContent is not None:
        return result.structuredContent
    if result.isError:
        return {
            'ok': False,
            'error': 'mcp_tool_error',
            'message': '\n'.join(x.text for x in result.content if x.type == 'text'),
        }
    return json.loads(next(x.text for x in result.content if x.type == 'text'))


async def schemas(session: ClientSession) -> list[dict[str, Any]]:
    result = await session.list_tools()
    return [
        {
            'name': t.name,
            'description': t.description,
            'inputSchema': t.inputSchema,
            'outputSchema': t.outputSchema,
        }
        for t in result.tools
    ]
