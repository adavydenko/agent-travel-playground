"""Bearer token verification against operator-issued Store credentials."""

import asyncio

from fastmcp.server.auth import AccessToken, TokenVerifier

from store import Store


class StoreTokenVerifier(TokenVerifier):
    """Map an opaque Bearer token to the student id stored by admin.py."""

    def __init__(self, store: Store) -> None:
        super().__init__()
        self.store = store

    async def verify_token(self, token: str) -> AccessToken | None:
        student = await asyncio.to_thread(self.store.authenticate, token.strip())
        if student is None:
            return None
        return AccessToken(token=token, client_id=student, scopes=[])
