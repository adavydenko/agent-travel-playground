"""Готовый транспорт для проверочных работ; решения задач здесь нет."""
from contextlib import asynccontextmanager
from urllib.parse import urlparse
import json
import httpx

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


@asynccontextmanager
async def connect(url, token):
    parsed = urlparse(url)
    if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost')):
        raise ValueError('Используйте HTTPS; HTTP допустим только на localhost.')
    async with httpx.AsyncClient(headers={'Authorization': f'Bearer {token}'}, timeout=30) as http:
        async with streamable_http_client(url, http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


async def call(session, name, args):
    result = await session.call_tool(name, args)
    if result.isError:
        return {'ok': False, 'error': 'mcp_tool_error',
                'message': '\n'.join(x.text for x in result.content if x.type == 'text')}
    if result.structuredContent is not None:
        return result.structuredContent
    return json.loads(next(x.text for x in result.content if x.type == 'text'))


async def schemas(session):
    result = await session.list_tools()
    return [{'name': t.name, 'description': t.description, 'inputSchema': t.inputSchema}
            for t in result.tools]
