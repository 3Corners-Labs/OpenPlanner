"""In-memory token store and an authorised client on a mock transport, for adapter tests."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from app.adapters.webex_auth import AuthorizedClient, ServiceAppConfig, WebexTokens
from tests.fakes.clock import FixedClock


class MemoryTokenStore:
    def __init__(self) -> None:
        self.values: dict[str, dict[str, Any]] = {}

    async def get(self, name: str) -> dict[str, Any] | None:
        return self.values.get(name)

    async def put(self, name: str, value: dict[str, Any], now: datetime) -> None:
        self.values[name] = value


def authorized_client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> AuthorizedClient:
    """Return a client whose token endpoint always answers and whose API calls go to ``handler``."""

    def route(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/access_token":
            return httpx.Response(200, json={"access_token": "a", "expires_in": 3600})
        return handler(request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(route))
    clock = FixedClock(datetime(2026, 1, 1, 8, 0, tzinfo=UTC))
    tokens = WebexTokens(ServiceAppConfig("c", "s", "r"), http, MemoryTokenStore(), clock)
    return AuthorizedClient(http, tokens)
