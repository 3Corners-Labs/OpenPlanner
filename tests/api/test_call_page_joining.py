"""The call page's markup for the click that joins (ADR 0123 step 4c).

The server decides whether the page may load the SDK at all: the markup names the file, with its
checksum as integrity, only when the file is placed and whole. It also carries the two connection
values and the address of the token. The behaviour of the click is walked in
``e2e/tests/call-page-joining.spec.ts``.
"""

import base64
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest

from app.adapters.vendored_files import VendoredFile
from app.config import Settings
from app.web.static_assets import StaticAssets
from tests.conftest import app_client
from tests.fakes.clock import FixedClock
from tests.support.call_page import Page
from tests.support.domain import NOW
from tests.support.participant import booked, open_link

CONTENT = b"window.Webex = {};"


@pytest.fixture
def call_settings(demo_settings: Settings) -> Settings:
    """The demo installation with the call page switched on."""
    return demo_settings.model_copy(update={"embedded_call_enabled": True})


def _place(root: Path, *, sha256: str | None = None, checksum_version: str = "1") -> VendoredFile:
    """Put a vendored folder with a manifest under ``root``, laid out as the static folder is."""
    folder = root / "vendor" / "webex"
    folder.mkdir(parents=True)
    (folder / "meetings.min.js").write_bytes(CONTENT)
    manifest = {
        "name": "webex",
        "version": "1",
        "source": "test",
        "file": "meetings.min.js",
        "checksum": {
            "version": checksum_version,
            "sha256": sha256 or hashlib.sha256(CONTENT).hexdigest(),
        },
    }
    (folder / "manifest.json").write_text(json.dumps(manifest))
    return VendoredFile(folder)


async def test_the_markup_carries_the_connection_values_and_no_sdk_while_none_is_placed(
    call_settings: Settings, tmp_path: Path
) -> None:
    settings = call_settings.model_copy(
        update={"embedded_call_connect_seconds": 12, "embedded_call_connect_attempts": 4}
    )
    async with app_client(settings, FixedClock(NOW)) as client:
        app: Any = client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
        app.state.call_sdk = VendoredFile(tmp_path)  # an installation without the file
        base = await open_link(client, await booked(client))
        page = Page((await client.get(f"{base}/call?lang=en")).text)

    root = page.element("data-call").attrs
    assert root["data-call-token-url"] == f"{base}/call/token"
    assert (root["data-call-seconds"], root["data-call-attempts"]) == ("12", "4")
    assert "data-call-sdk" not in root
    assert "data-call-sdk-integrity" not in root
    # The attempt line keeps a placeholder for the script; the failure sentence has its count.
    assert "Attempt {n} of 4" in page.raw
    assert "We tried 4 times" in page.raw


async def test_a_whole_sdk_file_is_named_in_an_attribute_and_never_as_a_tag(
    call_settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.api.call_pages.ASSETS", StaticAssets(tmp_path))
    async with app_client(call_settings, FixedClock(NOW)) as client:
        app: Any = client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
        app.state.call_sdk = _place(tmp_path)
        base = await open_link(client, await booked(client))
        page = Page((await client.get(f"{base}/call")).text)

    root = page.element("data-call").attrs
    digest = base64.b64encode(hashlib.sha256(CONTENT).digest()).decode()
    assert str(root["data-call-sdk"]).startswith("/static/vendor/webex/meetings.min.js?v=")
    assert root["data-call-sdk-integrity"] == f"sha256-{digest}"
    # Nothing the browser would fetch or run before the click: our ten scripts, no SDK.
    assert len(page.script_sources) == 11  # our own, never the SDK
    assert not any("vendor" in url for url in page.references)


@pytest.mark.parametrize("what", ["differs", "other_version", "not_placed"])
async def test_an_sdk_file_that_cannot_be_trusted_is_not_named(
    call_settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, what: str
) -> None:
    monkeypatch.setattr("app.api.call_pages.ASSETS", StaticAssets(tmp_path))
    sdk = _place(
        tmp_path,
        sha256="0" * 64 if what == "differs" else None,
        checksum_version="older" if what == "other_version" else "1",
    )
    if what == "not_placed":
        (tmp_path / "vendor" / "webex" / "meetings.min.js").unlink()
    async with app_client(call_settings, FixedClock(NOW)) as client:
        app: Any = client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
        app.state.call_sdk = sdk
        base = await open_link(client, await booked(client))
        response = await client.get(f"{base}/call")

    assert response.status_code == 200
    assert "data-call-sdk" not in response.text


async def test_every_failure_has_its_own_sentence_and_the_failed_state_its_ways_on(
    call_settings: Settings,
) -> None:
    settings = call_settings.model_copy(update={"embedded_call_webex_option": "on_failure"})
    async with app_client(settings, FixedClock(NOW)) as client:
        base = await open_link(client, await booked(client))
        page = Page((await client.get(f"{base}/call?lang=en")).text)

    failed = page.state("failed")
    reasons = re.findall(r'data-call-failure="(\w+)"', failed.raw)
    sentences = re.findall(r'data-call-failure="\w+" data-text="\w+" hidden>([^<]+)<', failed.raw)
    assert reasons == [
        "attempts",
        "generic",
        "media",
        "lost",
        "not_open",
        "rate_limited",
        "unavailable",
    ]
    assert len(set(sentences)) == 7
    assert [b.text for b in failed.buttons()] == ["Try again"]
    # The link out is offered by `on_failure` in the failed state, with the contact details kept
    # for the script to show instead of it when the call is simply not open (a link out would
    # be refused as well).
    assert f'href="{base}/join/go"' in failed.raw
    assert "data-call-failed-other-way" in failed.raw
    assert "data-call-failed-contacts" in failed.raw


async def test_the_failed_state_offers_contact_details_alone_while_the_other_way_is_hidden(
    call_settings: Settings,
) -> None:
    async with app_client(call_settings, FixedClock(NOW)) as client:
        base = await open_link(client, await booked(client))
        page = Page((await client.get(f"{base}/call")).text)

    failed = page.state("failed")
    assert "/join/go" not in failed.raw
    assert "data-call-failed-other-way" not in failed.raw
    assert "data-call-failed-contacts" in failed.raw


async def test_the_states_after_the_click_have_their_headings_and_name_no_supplier(
    call_settings: Settings,
) -> None:
    async with app_client(call_settings, FixedClock(NOW)) as client:
        base = await open_link(client, await booked(client))
        english = Page((await client.get(f"{base}/call?lang=en")).text)
        dutch = Page((await client.get(f"{base}/call?lang=nl")).text)

    expected = {
        "connecting": ("We are connecting you", "Wij verbinden u"),
        "lobby": ("You are here. The agent will let you in shortly", None),
        "call": ("You are in the call", "U bent in gesprek"),
        "failed": ("We cannot connect", "Het lukt niet om te verbinden"),
        "left": ("You left the call", "U hebt het gesprek verlaten"),
        "ended": ("The call has ended", "Het gesprek is beëindigd"),
    }
    for name, (en, nl) in expected.items():
        assert f'id="call-{name}-heading"' in english.state(name).raw
        assert en in english.state(name).text()
        if nl:
            assert nl in dutch.state(name).text()
    for page in (english, dutch):
        # Each state has one heading; the one the script moves focus to.
        for name in expected:
            assert len(re.findall(r"<h1\b", page.state(name).raw)) == 1


async def test_an_organisation_without_contacts_gets_one_plain_sentence_in_the_failure_states(
    call_settings: Settings,
) -> None:
    async with app_client(call_settings, FixedClock(NOW)) as client:
        base = await open_link(client, await booked(client))
        english = Page((await client.get(f"{base}/call?lang=en")).text)
        dutch = Page((await client.get(f"{base}/call?lang=nl")).text)

    for page, sentence in (
        (english, "Get in touch with the organisation."),
        (dutch, "Neem contact op met de organisatie."),
    ):
        for state in ("failed", "unavailable"):
            assert sentence in page.state(state).text()


async def test_each_own_picture_has_a_name_of_its_own_and_each_sentence_is_in_the_markup_once(
    call_settings: Settings,
) -> None:
    async with app_client(call_settings, FixedClock(NOW)) as client:
        base = await open_link(client, await booked(client))
        page = Page((await client.get(f"{base}/call?lang=en")).text)

    labels = re.findall(r'<video[^>]*aria-label="([^"]+)"[^>]*data-call-(?:preview|self)', page.raw)
    assert labels == ["Preview of your camera", "Your picture", "Your picture"]
    assert len(set(labels)) == 2
    # The check's, the lobby's and the call's picture are told apart by the state they are in.
    assert page.state("check").raw.count("Preview of your camera") == 1
    for sentence in (
        "We tried 3 times",
        "Thank you for the call.",
        "The last attempt did not work.",
        "This call is being recorded. The picture",
        "The connection was interrupted. It is being",
    ):
        assert page.raw.count(sentence) == 1, sentence


async def test_the_way_to_hear_the_other_side_is_in_the_call_state_and_hidden(
    call_settings: Settings,
) -> None:
    async with app_client(call_settings, FixedClock(NOW)) as client:
        base = await open_link(client, await booked(client))
        page = Page((await client.get(f"{base}/call?lang=en")).text)

    call = page.state("call")
    assert [b.text for b in call.buttons() if "data-call-hear" in b.attrs] == [
        "Tap to hear the other person"
    ]
    assert "hidden" in [b for b in call.buttons() if "data-call-hear" in b.attrs][0].attrs


async def test_a_file_that_goes_missing_between_the_check_and_the_address_is_no_offer(
    call_settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The verdict was "whole", the static folder no longer has it: the page, not a 500.
    monkeypatch.setattr("app.api.call_pages.ASSETS", StaticAssets(tmp_path / "elsewhere"))
    async with app_client(call_settings, FixedClock(NOW)) as client:
        app: Any = client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
        app.state.call_sdk = _place(tmp_path)
        base = await open_link(client, await booked(client))
        response = await client.get(f"{base}/call")

    assert response.status_code == 200
    assert "data-call-sdk" not in response.text
