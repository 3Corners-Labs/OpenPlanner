"""The organisation's privacy statement on the call page (ADR 0123 point 5, step 6b).

The page says that an external video service carries the call and links to the organisation's
privacy statement, which names that service; the page never does. The address is the class D
setting ``privacy_statement_url``. It is checked at save (``test_privacy_statement.py``)
and again where the page renders it, so a value that is not valid never becomes a link.
"""

import html

import pytest
from pydantic import ValidationError

from app.config import Settings
from tests.fakes.clock import FixedClock
from tests.support.call_page import Page, embedded_app_client
from tests.support.domain import NOW
from tests.support.participant import booked, open_link

PRIVACY = "https://voorbeeld.nl/privacy"
SUPPLIER = ("webex", "cisco")


@pytest.fixture
def call_settings(demo_settings: Settings) -> Settings:
    """The demo installation with the call page switched on."""
    return demo_settings.model_copy(update={"embedded_call_enabled": True})


async def test_the_page_links_to_the_privacy_statement_in_a_new_tab_next_to_the_line(
    call_settings: Settings,
) -> None:
    settings = call_settings.model_copy(update={"privacy_statement_url": PRIVACY})
    async with embedded_app_client(settings, FixedClock(NOW)) as client:
        base = await open_link(client, await booked(client))
        english = (await client.get(f"{base}/call?lang=en")).text
        dutch = (await client.get(f"{base}/call?lang=nl")).text

    for text, line, words in (
        (
            english,
            "An external video service carries this video call.",
            "The organisation's privacy statement (opens in a new tab)",
        ),
        (
            dutch,
            "Een externe videodienst verzorgt dit videogesprek.",
            "Privacyverklaring van de organisatie (opent in een nieuw tabblad)",
        ),
    ):
        page = Page(text)
        check = html.unescape(page.state("check").raw)
        link = f'<a href="{PRIVACY}" target="_blank" rel="noopener noreferrer">{words}</a>'
        assert line in check
        assert link in check  # in a new tab, said in the link's text, with no opener or referrer
        assert page.references.count(PRIVACY) == 1
        assert not any(name in text.lower() for name in SUPPLIER)  # the statement names it


async def test_without_an_address_the_line_stands_without_a_link(call_settings: Settings) -> None:
    async with embedded_app_client(call_settings, FixedClock(NOW)) as client:
        base = await open_link(client, await booked(client))
        text = (await client.get(f"{base}/call?lang=en")).text

    assert "An external video service carries this video call." in text
    assert "privacy statement" not in text.lower()
    assert "_blank" not in text


@pytest.mark.parametrize(
    "stored",
    [
        "http://voorbeeld.nl/privacy",
        "javascript:alert(1)",
        "https://gebruiker:wachtwoord@voorbeeld.nl/",
        "https://voorbeeld.nl/" + "a" * 2048,
        'https://voorbeeld.nl/"><script>x</script>',
    ],
)
async def test_a_stored_address_that_is_not_valid_is_never_rendered_as_a_link(
    call_settings: Settings, stored: str
) -> None:
    """The check at save is not the only one: the page asks again where it renders the value."""
    settings = call_settings.model_copy(update={"privacy_statement_url": stored})
    async with embedded_app_client(settings, FixedClock(NOW)) as client:
        base = await open_link(client, await booked(client))
        text = (await client.get(f"{base}/call?lang=en")).text

    assert "privacy statement" not in text.lower()
    assert "javascript:" not in text
    assert "<script>x" not in text


def test_the_environment_refuses_an_address_the_portal_would_refuse() -> None:
    """The rule applies at the environment too: ``Settings`` validates it (empty: no link)."""
    assert Settings(environment="dev", privacy_statement_url=PRIVACY).privacy_statement_url
    assert Settings(environment="dev", privacy_statement_url="").privacy_statement_url is None
    for bad in ("http://voorbeeld.nl/p", "https://127.0.0.1/p", "javascript:alert(1)"):
        with pytest.raises(ValidationError):
            Settings(environment="dev", privacy_statement_url=bad)
