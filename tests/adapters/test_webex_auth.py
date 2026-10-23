import json
from datetime import timedelta
from urllib.parse import parse_qs

import httpx
import pytest

from app.adapters.webex_auth import (
    CREDENTIAL_NAME,
    AuthorizedClient,
    ServiceAppConfig,
    WebexTokens,
)
from app.domain.ports import IntegrationError
from tests.fakes.clock import FixedClock
from tests.fakes.tokens import MemoryTokenStore
from tests.support.domain import NOW

CONFIG = ServiceAppConfig("client-1", "client-secret", "bootstrap-refresh")


class FakeWebex:
    """Answers the token endpoint and one API path; records what it received."""

    def __init__(self) -> None:
        self.refreshes: list[dict[str, list[str]]] = []
        self.api_tokens: list[str] = []
        self.token_status = 200
        self.api_statuses: list[int] = [200]
        self.counter = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/access_token":
            self.refreshes.append(parse_qs(request.content.decode()))
            self.counter += 1
            if self.token_status != 200:
                return httpx.Response(self.token_status)
            return httpx.Response(
                200,
                json={
                    "access_token": f"access-{self.counter}",
                    "expires_in": 1209600,
                    "refresh_token": f"refresh-{self.counter}",
                    "refresh_token_expires_in": 7776000,
                },
            )
        self.api_tokens.append(request.headers["authorization"])
        status = self.api_statuses.pop(0) if self.api_statuses else 200
        return httpx.Response(status, json={"ok": True})


@pytest.fixture
def webex() -> FakeWebex:
    return FakeWebex()


def _tokens(webex: FakeWebex, store: MemoryTokenStore, clock: FixedClock) -> WebexTokens:
    http = httpx.AsyncClient(transport=httpx.MockTransport(webex.handler))
    return WebexTokens(CONFIG, http, store, clock)


async def test_first_use_refreshes_with_the_bootstrap_token(webex: FakeWebex) -> None:
    store, clock = MemoryTokenStore(), FixedClock(NOW)
    tokens = _tokens(webex, store, clock)

    assert await tokens.access_token() == "access-1"
    assert await tokens.access_token() == "access-1"  # cached while valid

    assert webex.refreshes == [
        {
            "grant_type": ["refresh_token"],
            "client_id": ["client-1"],
            "client_secret": ["client-secret"],
            "refresh_token": ["bootstrap-refresh"],
        }
    ]
    stored = store.values[CREDENTIAL_NAME]
    assert stored["refresh_token"] == "refresh-1"
    assert "client-secret" not in json.dumps(stored)


async def test_tokens_are_refreshed_before_they_expire(webex: FakeWebex) -> None:
    store, clock = MemoryTokenStore(), FixedClock(NOW)
    tokens = _tokens(webex, store, clock)
    await tokens.access_token()
    clock.advance(timedelta(days=14) - timedelta(minutes=4))

    assert await tokens.access_token() == "access-2"
    assert webex.refreshes[1]["refresh_token"] == ["refresh-1"]  # the rotated one


async def test_refused_refresh_is_not_retryable(webex: FakeWebex) -> None:
    webex.token_status = 400
    tokens = _tokens(webex, MemoryTokenStore(), FixedClock(NOW))

    with pytest.raises(IntegrationError) as error:
        await tokens.access_token()
    assert (error.value.code, error.value.retryable) == ("unauthorized", False)


async def test_health_warns_two_weeks_before_the_refresh_token_expires(webex: FakeWebex) -> None:
    store, clock = MemoryTokenStore(), FixedClock(NOW)
    tokens = _tokens(webex, store, clock)
    await tokens.access_token()

    assert not (await tokens.health()).refresh_expiring_soon
    clock.advance(timedelta(days=80))
    assert (await tokens.health()).refresh_expiring_soon


async def test_a_401_refreshes_once_and_repeats_the_call(webex: FakeWebex) -> None:
    webex.api_statuses = [401, 200]
    store, clock = MemoryTokenStore(), FixedClock(NOW)
    http = httpx.AsyncClient(transport=httpx.MockTransport(webex.handler))
    client = AuthorizedClient(http, WebexTokens(CONFIG, http, store, clock))

    response = await client.request("GET", "https://webexapis.com/v1/meetings")

    assert response.status_code == 200
    assert webex.api_tokens == ["Bearer access-1", "Bearer access-2"]


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [(429, "rate_limited", True), (503, "server_error", True), (400, "rejected_400", False)],
)
async def test_failures_map_to_codes(
    webex: FakeWebex, status: int, code: str, retryable: bool
) -> None:
    webex.api_statuses = [status]
    http = httpx.AsyncClient(transport=httpx.MockTransport(webex.handler))
    client = AuthorizedClient(http, WebexTokens(CONFIG, http, MemoryTokenStore(), FixedClock(NOW)))

    with pytest.raises(IntegrationError) as error:
        await client.request("POST", "https://webexapis.com/v1/meetings", json={})
    assert (error.value.code, error.value.retryable) == (code, retryable)
