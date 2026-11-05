"""In-memory token store and Webex Service App tokens on a mock transport, for adapter tests.

The fake token endpoint at ``/v1/access_token`` always answers; every other request goes to the
handler the test passes.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from app.adapters.webex_auth import AuthorizedClient, ServiceAppConfig, WebexTokens
from tests.fakes.clock import FixedClock

ACCESS_TOKEN = "access-token-value-never-printed"  # noqa: S105 - test value
"""The access token ``webex_tokens`` hands out: a report or log line that shows it leaks it."""


class MemoryTokenStore:
    def __init__(self) -> None:
        self.values: dict[str, dict[str, Any]] = {}

    async def get(self, name: str) -> dict[str, Any] | None:
        return self.values.get(name)

    async def put(self, name: str, value: dict[str, Any], now: datetime) -> None:
        self.values[name] = value


def webex_client(
    route: Callable[[httpx.Request], httpx.Response], clock: FixedClock
) -> tuple[WebexTokens, httpx.AsyncClient]:
    """Return Service App tokens in an empty store and the client they share with the API.

    Every request, the token refresh too, is answered by ``route``.
    """
    http = httpx.AsyncClient(transport=httpx.MockTransport(route))
    tokens = WebexTokens(ServiceAppConfig("c", "s", "r"), http, MemoryTokenStore(), clock)
    return tokens, http


def webex_tokens(
    handler: Callable[[httpx.Request], httpx.Response], clock: FixedClock
) -> tuple[WebexTokens, httpx.AsyncClient]:
    """Return tokens whose refresh always hands out ``ACCESS_TOKEN``, and the shared client.

    Every other request must carry that token as its bearer, and is answered by ``handler``.
    """

    def route(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/access_token":
            return httpx.Response(200, json={"access_token": ACCESS_TOKEN, "expires_in": 3600})
        assert request.headers["authorization"] == f"Bearer {ACCESS_TOKEN}"
        return handler(request)

    return webex_client(route, clock)


def authorized_client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> AuthorizedClient:
    """Return a client whose token endpoint always answers and whose API calls go to ``handler``."""

    def route(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/access_token":
            return httpx.Response(200, json={"access_token": "a", "expires_in": 3600})
        return handler(request)

    tokens, http = webex_client(route, FixedClock(datetime(2026, 1, 1, 8, 0, tzinfo=UTC)))
    return AuthorizedClient(http, tokens)
