"""The call page (ADR 0123 steps 2 and 4b): route, switch, policy, start state, references."""

import re
from typing import Any

import httpx
import pytest

from app.adapters.db.uow import SqlUnitOfWork
from app.api.security_headers import SECURITY_HEADERS
from app.config import Settings
from app.domain.contacts import ContactBlock, ContactKind, ContactOption
from app.web.security_policy import (
    CALL_CSP,
    CALL_POLICY,
    HTML_CSP,
    SCRIPT_CSP,
)
from tests.conftest import app_client
from tests.fakes.clock import FixedClock
from tests.support.call_page import Page
from tests.support.domain import NOW
from tests.support.participant import booked, open_link

DEFAULT_POLICY = SECURITY_HEADERS[b"permissions-policy"].decode()
SUPPLIER = ("webex", "cisco")
_ALWAYS = {"embedded_call_webex_option": "always"}
STATES = [
    "start",
    "check",
    "connecting",
    "lobby",
    "call",
    "failed",
    "left",
    "ended",
    "unavailable",
]
"""Every state of the page, in the order of the markup; all but the first are hidden at first."""


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
    assert page.headers["permissions-policy"] == CALL_POLICY.permissions
    assert "camera=(self)" in CALL_POLICY.permissions
    assert "geolocation=()" in CALL_POLICY.permissions
    # The headers the middleware adds are the ones every response has.
    for name, value in SECURITY_HEADERS.items():
        if name not in (b"content-security-policy", b"permissions-policy"):
            assert page.headers[name.decode()] == value.decode()
    assert page.headers["cache-control"] == "no-store"


async def test_the_start_state_shows_the_appointment_and_one_button_to_begin(
    call_settings: Settings,
) -> None:
    async with app_client(call_settings, FixedClock(NOW)) as client:
        base = await open_link(client, await booked(client))
        english = Page((await client.get(f"{base}/call?lang=en")).text)
        dutch = Page((await client.get(f"{base}/call?lang=nl")).text)

    for page, heading, when, button in (
        (english, "Your video call", "Friday", "Test camera and microphone"),
        (dutch, "Uw videogesprek", "vrijdag", "Camera en microfoon testen"),
    ):
        start = page.state("start")
        assert page.visible_headings() == [heading]
        assert when in start.text()
        assert "Demo" in start.text()
        # The one button is there for a browser that can run the call; the script shows it.
        assert [b.text for b in start.buttons()] == [button]
        assert "hidden" in start.buttons()[0].attrs
        assert f'href="{base}/join"' in page.raw
    # Every other state is rendered, hidden, so the script only has to show it.
    assert english.state_names() == STATES
    assert [n for n in english.state_names() if english.state(n).hidden] == STATES[1:]


async def test_without_javascript_the_page_says_what_the_call_needs(
    call_settings: Settings,
) -> None:
    async with app_client(call_settings, FixedClock(NOW)) as client:
        base = await open_link(client, await booked(client))
        english = Page((await client.get(f"{base}/call?lang=en")).text)
        dutch = Page((await client.get(f"{base}/call?lang=nl")).text)

    # The sentence stands in the start state, visible: nothing is broken or empty.
    for page, sentence in (
        (english, "a browser with JavaScript, a camera and a microphone"),
        (dutch, "een browser nodig met JavaScript, een camera en een microfoon"),
    ):
        needs = page.element("data-call-needs")
        assert sentence in needs.text()
        assert "hidden" not in needs.attrs
        assert not page.state("start").hidden


async def test_the_start_state_offers_the_contact_details_of_the_organisation(
    call_settings: Settings,
) -> None:
    async with app_client(call_settings, FixedClock(NOW)) as client:
        app: Any = client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
        async with SqlUnitOfWork(app.state.engine).locked() as tx:
            demo = await tx.get_organisation("demo")
            assert demo is not None
            await tx.save_contact_block(
                demo.id, ContactBlock((ContactOption.create(ContactKind.PHONE, "012 345 6789"),))
            )
        base = await open_link(client, await booked(client))
        page = Page((await client.get(f"{base}/call?lang=en")).text)

    assert 'href="tel:0123456789"' in page.element("data-call-needs").raw
    assert 'href="tel:0123456789"' in page.state("unavailable").raw


async def test_the_page_has_no_supplier_name_in_any_locale_and_any_state(
    call_settings: Settings,
) -> None:
    settings = call_settings.model_copy(update=_ALWAYS)
    async with app_client(settings, FixedClock(NOW)) as client:
        app: Any = client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
        async with SqlUnitOfWork(app.state.engine).locked() as tx:
            demo = await tx.get_organisation("demo")
            assert demo is not None
            await tx.save_contact_block(
                demo.id, ContactBlock((ContactOption.create(ContactKind.PHONE, "012 345 6789"),))
            )
        base = await open_link(client, await booked(client))
        pages = [Page((await client.get(f"{base}/call?lang={code}")).text) for code in ("nl", "en")]

    for page in pages:
        # The hidden states are in the markup, the shared texts and the contact block with them.
        assert page.state_names() == STATES
        for name in page.state_names():
            assert not any(word in page.state(name).raw.lower() for word in SUPPLIER)
        assert not any(word in page.raw.lower() for word in SUPPLIER)


async def test_the_page_refers_only_to_files_of_its_own_and_has_no_inline_code(
    call_settings: Settings,
) -> None:
    async with app_client(call_settings.model_copy(update=_ALWAYS), FixedClock(NOW)) as client:
        base = await open_link(client, await booked(client))
        page = Page((await client.get(f"{base}/call")).text)

    assert page.references
    for url in page.references:
        assert url.startswith(("/", "?")), url
        assert not url.startswith("//"), url
    for name in (
        "call-status.js",
        "call-sound.js",
        "call-devices.js",
        "call-connection.js",
        "call-media.js",
        "call-captions.js",
        "call-remote.js",
        "call-render.js",
        "call-events.js",
        "call-poll.js",
        "call-page.js",
    ):
        assert any(url.startswith(f"/static/{name}?v=") for url in page.references)
    assert any(url.startswith("/static/call-page.css?v=") for url in page.references)
    assert page.inline_code == []
    assert "<style" not in page.raw
    # Nothing is asked of the video platform: no address of another origin is in the page at all.
    assert "http://" not in page.raw
    assert "https://" not in page.raw


async def test_the_script_is_served_with_the_page_and_nothing_else_is_loaded(
    call_settings: Settings,
) -> None:
    async with app_client(call_settings, FixedClock(NOW)) as client:
        base = await open_link(client, await booked(client))
        page = Page((await client.get(f"{base}/call")).text)
        scripts = [await client.get(source) for source in page.script_sources]

    assert [s.split("?")[0] for s in page.script_sources] == [
        "/static/call-status.js",
        "/static/call-sound.js",
        "/static/call-devices.js",
        "/static/call-connection.js",
        "/static/call-media.js",
        "/static/call-captions.js",
        "/static/call-remote.js",
        "/static/call-render.js",
        "/static/call-events.js",
        "/static/call-poll.js",
        "/static/call-page.js",
    ]
    for script in scripts:
        assert script.status_code == 200
        assert script.headers["content-type"].startswith(
            ("text/javascript", "application/javascript")
        )


async def test_no_link_to_another_way_in_while_the_setting_is_hidden(
    call_settings: Settings,
) -> None:
    assert call_settings.embedded_call_webex_option == "hidden"
    async with app_client(call_settings, FixedClock(NOW)) as client:
        base = await open_link(client, await booked(client))
        page = (await client.get(f"{base}/call?lang=en")).text

    assert "/join/go" not in page
    assert "Join this call another way" not in page


async def test_on_failure_shows_the_link_only_where_the_page_cannot_give_the_call(
    call_settings: Settings,
) -> None:
    settings = call_settings.model_copy(update={"embedded_call_webex_option": "on_failure"})
    async with app_client(settings, FixedClock(NOW)) as client:
        base = await open_link(client, await booked(client))
        page = Page((await client.get(f"{base}/call?lang=en")).text)

    link = f'href="{base}/join/go"'
    assert link in page.element("data-call-needs").raw
    assert link in page.state("unavailable").raw
    assert link not in page.state("check").raw
    assert page.state("start").raw.count(link) == 1  # the notice's, which the script hides
    assert "Join this call another way" in page.state("unavailable").text()


async def test_always_shows_the_link_before_joining_too(call_settings: Settings) -> None:
    async with app_client(call_settings.model_copy(update=_ALWAYS), FixedClock(NOW)) as client:
        base = await open_link(client, await booked(client))
        page = Page((await client.get(f"{base}/call?lang=nl")).text)

    link = f'href="{base}/join/go"'
    assert page.state("start").raw.count(link) == 1  # once, outside the notice the script hides
    assert link not in page.element("data-call-needs").raw
    assert link in page.state("check").raw
    assert link in page.state("unavailable").raw
    assert "Op een andere manier deelnemen aan dit gesprek" in page.state("check").text()


async def test_the_other_way_follows_the_organisations_own_choice(
    call_settings: Settings,
) -> None:
    async with app_client(call_settings, FixedClock(NOW)) as client:
        app: Any = client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
        base = await open_link(client, await booked(client))
        assert "/join/go" not in (await client.get(f"{base}/call")).text
        async with SqlUnitOfWork(app.state.engine).locked() as tx:
            demo = await tx.get_organisation("demo")
            assert demo is not None
            await tx.save_organisation_setting(demo.id, "embedded_call_webex_option", "always", NOW)
            await tx.bump_settings_version()
        await app.state.live_settings.refresh()
        page = (await client.get(f"{base}/call")).text

    assert f'href="{base}/join/go"' in page


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


@pytest.mark.parametrize(
    ("lang", "deleted", "thanks"),
    [
        ("en", "deleted 24 hours after the end of your appointment", "Thank you for the call"),
        ("nl", "24 uur na het einde van uw afspraak verwijderd", "Bedankt voor het gesprek"),
    ],
)
async def test_the_ended_state_says_what_is_deleted_and_when_in_both_languages(
    call_settings: Settings, lang: str, deleted: str, thanks: str
) -> None:
    async with app_client(call_settings, FixedClock(NOW)) as client:
        base = await open_link(client, await booked(client))
        page = Page((await client.get(f"{base}/call?lang={lang}")).text)

    ended = page.state("ended").text()
    assert deleted in ended
    assert thanks in ended
    # Deleted, never "anonymised" (ADR 0102); no chat exists on this page yet.
    assert "anonym" not in ended.lower()
    assert "chat" not in ended.lower()


async def test_the_failure_state_has_a_sentence_for_a_call_that_was_lost_and_for_the_media(
    call_settings: Settings,
) -> None:
    async with app_client(call_settings, FixedClock(NOW)) as client:
        base = await open_link(client, await booked(client))
        failed = Page((await client.get(f"{base}/call?lang=en")).text).state("failed")

    sentences = {
        "media": "The picture or sound of the call could not be set up. You can try again.",
        "lost": "The connection was lost and could not be restored. You can try again.",
    }
    for kind, sentence in sentences.items():
        assert re.search(rf'data-call-failure="{kind}"[^>]*>{re.escape(sentence)}<', failed.raw)
    assert [b.text for b in failed.buttons()] == ["Try again"]
