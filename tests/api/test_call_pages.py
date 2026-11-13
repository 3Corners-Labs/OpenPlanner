"""The call page (ADR 0123 step 2): its route, its switch, its policy and who must not get it."""

from typing import Any

import httpx
import pytest

from app.adapters.db.uow import SqlUnitOfWork
from app.api.security_headers import SECURITY_HEADERS
from app.config import Settings
from app.web.templates import (
    CALL_CSP,
    CALL_PERMISSIONS,
    HTML_CSP,
    SCRIPT_CSP,
    permissions_policy,
)
from tests.conftest import app_client
from tests.fakes.clock import FixedClock
from tests.support.domain import NOW
from tests.support.participant import booked, open_link

CALL_POLICY = permissions_policy(CALL_PERMISSIONS)
DEFAULT_POLICY = SECURITY_HEADERS[b"permissions-policy"].decode()
SUPPLIER = ("webex", "cisco")


@pytest.fixture
def call_settings(demo_settings: Settings) -> Settings:
    """The demo installation with the call page switched on."""
    return demo_settings.model_copy(update={"embedded_call_enabled": True})


async def test_the_call_page_carries_its_own_policy_and_nothing_else_changes(
    call_settings: Settings,
) -> None:
    async with app_client(call_settings, FixedClock(NOW)) as client:
        base = await open_link(client, await booked(client))
        page = await client.get(f"{base}/call")

    assert page.status_code == 200
    assert page.headers["content-security-policy"] == CALL_CSP
    assert page.headers["permissions-policy"] == CALL_POLICY
    assert "camera=(self)" in CALL_POLICY
    assert "geolocation=()" in CALL_POLICY
    # The headers the middleware adds are the ones every response has.
    for name, value in SECURITY_HEADERS.items():
        if name not in (b"content-security-policy", b"permissions-policy"):
            assert page.headers[name.decode()] == value.decode()
    assert page.headers["cache-control"] == "no-store"


async def test_the_shell_has_one_heading_no_script_and_no_supplier(
    call_settings: Settings,
) -> None:
    async with app_client(call_settings, FixedClock(NOW)) as client:
        base = await open_link(client, await booked(client))
        english = await client.get(f"{base}/call?lang=en")
        dutch = await client.get(f"{base}/call?lang=nl")

    for page in (english, dutch):
        assert page.text.count("<h1") == 1
        assert "<script" not in page.text
        assert "<button" not in page.text
        assert f'href="{base}/join"' in page.text
        assert not any(name in page.text.lower() for name in SUPPLIER)
    assert "not available yet" in english.text
    assert "nog niet beschikbaar" in dutch.text


async def test_the_page_needs_the_participants_session(call_settings: Settings) -> None:
    async with app_client(call_settings, FixedClock(NOW)) as client:
        base = await open_link(client, await booked(client))
        async with httpx.AsyncClient(
            transport=client._transport,  # type: ignore[attr-defined]  # noqa: SLF001
            base_url="http://testserver",
        ) as stranger:
            refused = await stranger.get(f"{base}/call")
            unknown = await stranger.get("/b/m/no-such-ref/call")

    assert refused.status_code == unknown.status_code != 200
    assert "content-security-policy" in refused.headers
    assert refused.headers["content-security-policy"] != CALL_CSP
    assert (
        "permissions-policy" not in refused.headers
        or refused.headers["permissions-policy"] == DEFAULT_POLICY
    )


async def test_the_page_does_not_exist_while_the_feature_is_off(demo_settings: Settings) -> None:
    assert demo_settings.embedded_call_enabled is False
    async with app_client(demo_settings, FixedClock(NOW)) as client:
        base = await open_link(client, await booked(client))
        off = await client.get(f"{base}/call")
        nothing = await client.get(f"{base}/no-such-page")

    assert off.status_code == nothing.status_code == 404
    assert off.text == nothing.text
    assert off.headers["content-security-policy"] == nothing.headers["content-security-policy"]


async def test_no_other_page_carries_the_call_policy(call_settings: Settings) -> None:
    async with app_client(call_settings, FixedClock(NOW)) as client:
        base = await open_link(client, await booked(client))
        others = [
            await client.get("/"),
            await client.get("/g/demo"),
            await client.get(base),
            await client.get(f"{base}/join"),
            await client.get("/healthz"),
            await client.get("/no-such-page"),
        ]

    for response in others:
        assert response.headers["content-security-policy"] != CALL_CSP, response.url
        assert response.headers["permissions-policy"] == DEFAULT_POLICY, response.url
    # The booking page keeps the scripted policy, the home page the plain one.
    assert others[2].headers["content-security-policy"] == SCRIPT_CSP
    assert others[0].headers["content-security-policy"] in (HTML_CSP, SCRIPT_CSP)


async def _switch(client: httpx.AsyncClient, on: bool) -> None:
    """Store ``embedded_call_enabled`` as the superuser's portal does and let the web process
    see it, without a restart (ADR 0042 §2)."""
    app: Any = client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with SqlUnitOfWork(app.state.engine).locked() as tx:
        await tx.save_platform_setting("embedded_call_enabled", on, NOW, None)
        await tx.bump_settings_version()
    await app.state.live_settings.refresh()


async def test_the_page_follows_the_switch_at_runtime(demo_settings: Settings) -> None:
    async with app_client(demo_settings, FixedClock(NOW)) as client:
        base = await open_link(client, await booked(client))
        assert (await client.get(f"{base}/call")).status_code == 404

        await _switch(client, on=True)
        assert (await client.get(f"{base}/call")).status_code == 200

        await _switch(client, on=False)
        assert (await client.get(f"{base}/call")).status_code == 404
