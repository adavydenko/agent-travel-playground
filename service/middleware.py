"""HTTP policy built on the MCP SDK, Starlette and limits."""

import asyncio
import logging

from limits import RateLimitItemPerMinute
from limits.storage import MemoryStorage
from limits.strategies import MovingWindowRateLimiter
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser, BearerAuthBackend
from mcp.server.auth.provider import AccessToken
from starlette.authentication import AuthCredentials, AuthenticationError
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from store import Store

logger = logging.getLogger(__name__)


class StoreTokenVerifier:
    """The SDK handles Bearer authentication; the application verifies issued access."""

    def __init__(self, store: Store) -> None:
        self.store = store

    async def verify_token(self, token: str) -> AccessToken | None:
        student = await asyncio.to_thread(self.store.authenticate, token.strip())
        if student is None:
            return None
        return AccessToken(token=token, client_id=student, scopes=[])


class PrivateBearerBackend(BearerAuthBackend):
    """Require authentication on this private service, including discovery requests."""

    async def authenticate(
        self, connection: HTTPConnection
    ) -> tuple[AuthCredentials, AuthenticatedUser]:
        result = await super().authenticate(connection)
        if result is None:
            raise AuthenticationError('unauthorized')
        return result


def authentication_error(
    connection: HTTPConnection, error: AuthenticationError
) -> JSONResponse:
    logger.debug('authentication_rejected')
    return JSONResponse(
        {'error': 'unauthorized'},
        status_code=401,
        headers={'WWW-Authenticate': 'Bearer'},
    )


class RequestRateLimitMiddleware:
    """Keep the existing per-owner moving window: 120 requests per minute."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.limit = RateLimitItemPerMinute(120)
        self.limiter = MovingWindowRateLimiter(MemoryStorage())

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] == 'http':
            owner = scope['user'].identity
            if not self.limiter.hit(self.limit, owner):
                await JSONResponse(
                    {'error': 'rate_limited'},
                    status_code=429,
                    headers={'Retry-After': '60'},
                )(scope, receive, send)
                return
        await self.app(scope, receive, send)
