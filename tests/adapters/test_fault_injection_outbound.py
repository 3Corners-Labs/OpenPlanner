"""Faults on outbound calls: a TLS failure or a timeout fails closed, with a code and no PII.

ASVS V16.5.2 (a failing dependency does not break the application's handling) and V16.5.3
(fail closed: no access, no skipped check). The TLS failure is real: a local server presents a
self-signed certificate, and ``guarded_request`` (``app/adapters/outbound.py``) connects to it
with the default trust store.
"""

import asyncio
import ssl
from collections.abc import AsyncIterator, Callable
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from structlog.testing import capture_logs

from app.adapters.discovery import GuardedDiscoveryFetcher
from app.adapters.oidc import Guard, OidcConfig, OidcIdentityProvider
from app.adapters.outbound import guarded_request
from app.adapters.webex_auth import ServiceAppConfig, WebexTokens
from app.adapters.webex_meetings import WebexMeetings
from app.domain.audit import Category, Outcome
from app.domain.ports import IdentityError, IntegrationError
from app.domain.security_log import SecurityLog
from tests.adapters.test_oidc import CLIENT, MAPPING, REDIRECT
from tests.adapters.test_webex_meetings import REQUEST
from tests.fakes.clock import FixedClock
from tests.fakes.persistence import InMemoryUnitOfWork, State
from tests.fakes.tokens import MemoryTokenStore, authorized_client
from tests.support.certificates import make_pair
from tests.support.domain import NOW

HOST = "idp.example.nl"
LOOPBACK = "127.0.0.1"
PII = ("ada@example.org", "Lovelace", "+31612345678")


def lab_resolver(_host: str, _port: int) -> list[str]:
    """Resolve every name to the local TLS server."""
    return [LOOPBACK]


@pytest.fixture
async def untrusted_server(tmp_path: Path) -> AsyncIterator[int]:
    """Serve TLS with a self-signed certificate nobody trusts; yield the port."""
    pair = make_pair(HOST)
    chain, key = tmp_path / "chain.pem", tmp_path / "key.pem"
    chain.write_bytes(pair.chain)
    key.write_bytes(pair.key)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(chain, key)

    async def hold(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.close()

    server = await asyncio.start_server(hold, LOOPBACK, 0, ssl=context)
    yield server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()


def _timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("timed out", request=request)


async def test_a_tls_failure_through_the_guard_is_a_connection_error_and_sends_nothing(
    untrusted_server: int,
) -> None:
    async with httpx.AsyncClient() as http:
        with pytest.raises(httpx.ConnectError) as failure:
            await guarded_request(
                http,
                "GET",
                f"https://{HOST}:{untrusted_server}/.well-known/openid-configuration",
                resolve=lab_resolver,
                allow_private=True,
            )

    assert "CERTIFICATE_VERIFY_FAILED" in str(failure.value)


async def test_the_discovery_fetch_fails_closed_on_a_tls_failure(untrusted_server: int) -> None:
    async with httpx.AsyncClient() as http:
        fetcher = GuardedDiscoveryFetcher(http, allow_private=True, resolve=lab_resolver)
        with capture_logs() as logs, pytest.raises(IdentityError) as refused:
            await fetcher.fetch(f"https://{HOST}:{untrusted_server}/.well-known/x")

    assert refused.value.code == "discovery_failed"
    assert logs == [
        {"event": "idp.discovery_failed", "error": "ConnectError", "log_level": "warning"}
    ]


async def test_a_login_at_a_provider_with_a_bad_certificate_grants_nothing(
    untrusted_server: int,
) -> None:
    config = OidcConfig(
        discovery_url=f"https://{HOST}:{untrusted_server}/.well-known/openid-configuration",
        client_id=CLIENT,
        client_secret="s3cret",  # noqa: S106 - test value
        scopes="openid",
        mapping=MAPPING,
        guard=Guard(allow_private=True, resolve=lab_resolver),
    )
    async with httpx.AsyncClient() as http:
        provider = OidcIdentityProvider(config, http)
        with capture_logs() as logs:
            with pytest.raises(IdentityError) as at_start:
                await provider.start_login(REDIRECT)
            with pytest.raises(IdentityError) as at_finish:
                await provider.finish_login(
                    code="c", redirect_uri=REDIRECT, code_verifier="v" * 43, nonce="n"
                )

    assert (at_start.value.code, at_finish.value.code) == ("discovery_failed", "discovery_failed")
    assert {entry["error"] for entry in logs} == {"ConnectError"}
    assert HOST not in repr(logs)  # the log names the kind of failure, never the address


@pytest.mark.parametrize(
    "fault",
    [
        httpx.ConnectTimeout("connect timed out"),
        httpx.ReadTimeout("read timed out"),
        httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED]"),
    ],
    ids=["connect-timeout", "read-timeout", "tls"],
)
async def test_an_identity_provider_that_cannot_be_reached_refuses_the_login(
    fault: httpx.HTTPError,
) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise fault

    config = OidcConfig(
        discovery_url=f"https://{HOST}/.well-known/openid-configuration",
        client_id=CLIENT,
        client_secret="s3cret",  # noqa: S106 - test value
        scopes="openid",
        mapping=MAPPING,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(down)) as http:
        with pytest.raises(IdentityError, match="discovery_failed"):
            await OidcIdentityProvider(config, http).start_login(REDIRECT)


def _tokens(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[WebexTokens, InMemoryUnitOfWork]:
    uow, clock = InMemoryUnitOfWork(State()), FixedClock(NOW)
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    security = SecurityLog(uow, clock, timedelta(minutes=15))
    config = ServiceAppConfig("client-1", "client-secret", "bootstrap-refresh")
    return WebexTokens(config, http, MemoryTokenStore(), clock, security), uow


@pytest.mark.parametrize("fault", [httpx.ReadTimeout, httpx.ConnectError])
async def test_a_provider_timeout_on_the_token_refresh_is_a_security_event_with_a_code(
    fault: type[httpx.TransportError],
) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise fault("fault", request=request)

    tokens, uow = _tokens(down)

    with pytest.raises(IntegrationError) as failure:
        await tokens.access_token()

    assert (failure.value.code, failure.value.retryable) == ("unreachable", True)
    (event,) = uow.state.audit
    assert (event.action, event.outcome, event.reason) == (
        "webex.token_refresh",
        Outcome.FAILURE,
        "unreachable",
    )
    assert event.category is Category.SECURITY
    assert event.detail == fault.__name__  # the kind of failure, no address, no PII


async def test_a_timeout_on_an_api_call_is_a_retryable_code_and_leaves_no_meeting() -> None:
    meetings = WebexMeetings(
        authorized_client(_timeout), "https://webexapis.com/v1", FixedClock(NOW)
    )

    with capture_logs() as logs, pytest.raises(IntegrationError) as failure:
        await meetings.create_meeting(REQUEST)

    assert (failure.value.code, failure.value.retryable) == ("unreachable", True)
    assert [e["error"] for e in logs if e["event"] == "integration.unreachable"] == ["ReadTimeout"]
    assert not any(value in repr(failure.value) + repr(logs) for value in PII)
