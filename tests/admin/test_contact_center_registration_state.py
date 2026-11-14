"""What a rotation left at the platform is said on the card until a registration succeeds.

ADR 0124 point 8: a gap or no subscription is a system event with a code and a line on the card;
a registration that failed and is not retried by anything shows as failed while it lasts.
"""

from datetime import timedelta

import pytest

from app.domain.contact_center import Registration
from tests.admin.conftest import SAME_ORIGIN, SUPER, Site
from tests.support.admin_site import site_app
from tests.support.contact_center_site import PAGE, _connect, _registrar

ROTATE = f"{PAGE}/contact-center/webhook-secret"


async def _rotate(site: Site, outcome: Registration) -> str:
    """Rotate with the platform answering ``outcome``; return the confirmation's address."""
    _registrar(site, outcome)
    response = await site.client.post(ROTATE, headers=SAME_ORIGIN)
    assert response.status_code == 303
    return response.headers["location"]


async def _gap_events(site: Site) -> list[tuple[str, str | None, str]]:
    audit = await site_app(site).state.admin_service.audit_log(SUPER)
    return [
        (e.event.reason, e.event.organisation, e.event.detail)
        for e in audit
        if e.event.action == "webhook.subscription_gap"
    ]


@pytest.mark.parametrize(
    ("outcome", "done", "reason", "words"),
    [
        (Registration.REPLACED, "contact_center_replaced", "replaced", "is vervangen"),
        (Registration.GONE, "contact_center_gone", "gone", "Er is nu geen abonnement"),
    ],
)
async def test_a_gap_or_no_subscription_is_an_event_a_confirmation_and_a_line_on_the_card(
    site: Site, outcome: Registration, done: str, reason: str, words: str
) -> None:
    await _connect(site)

    location = await _rotate(site, outcome)
    card = (await site.client.get(PAGE)).text

    assert f"done={done}" in location
    assert await _gap_events(site) == [(reason, "demo", "")]  # a code, no platform text
    assert words in card
    assert 'role="alert"' in card


async def test_a_successful_registration_clears_the_line_and_writes_no_event(site: Site) -> None:
    await _connect(site)
    await _rotate(site, Registration.GONE)

    await _rotate(site, Registration.REGISTERED)
    card = (await site.client.get(PAGE)).text

    assert "Er is nu geen abonnement" not in card
    assert len(await _gap_events(site)) == 1  # the one from the first rotation only


async def test_a_failed_registration_shows_while_the_previous_secret_still_works(
    site: Site,
) -> None:
    """Nothing retries it: the card says so, and until when the old secret is accepted."""
    await _connect(site)

    await _rotate(site, Registration.FAILED)
    inside = (await site.client.get(PAGE)).text
    site.clock.advance(timedelta(minutes=61))
    await site.login_as(SUPER)  # the idle session ended
    after = (await site.client.get(PAGE)).text

    assert "heeft het nieuwe geheim niet gekregen" in inside
    assert "dat wordt geaccepteerd tot" in inside
    assert "Er probeert niets vanzelf opnieuw" in inside
    assert "niet meer geaccepteerd" in after
    assert "dat wordt geaccepteerd tot" not in after
    assert await _gap_events(site) == []  # a failure at the platform is no gap of ours


async def test_saving_the_card_registers_again_and_clears_a_failed_state(site: Site) -> None:
    from tests.support.contact_center_site import FORM  # noqa: PLC0415

    await _connect(site)
    await _rotate(site, Registration.REFUSED)
    _registrar(site, Registration.REGISTERED)

    saved = await site.client.post(
        f"{PAGE}/contact-center",
        data=FORM | {"cc_client_secret": "", "cc_refresh_token": ""},
        headers=SAME_ORIGIN,
    )
    card = (await site.client.get(PAGE)).text

    assert "done=contact_center_registered" in saved.headers["location"]
    assert "heeft het nieuwe geheim niet gekregen" not in card


async def test_the_sentences_about_rotating_are_tied_to_the_button_and_nothing_is_a_status_role(
    site: Site,
) -> None:
    await _connect(site)
    before = (await site.client.get(PAGE)).text
    await _rotate(site, Registration.REGISTERED)
    after = (await site.client.get(PAGE)).text

    assert 'id="cc-rotate-lead"' in before
    assert 'aria-describedby="cc-rotate-lead"' in before
    assert 'id="cc-rotate-again"' not in before
    assert 'aria-describedby="cc-rotate-lead cc-rotate-again"' in after
    assert 'id="cc-rotate-again"' in after
    assert 'role="status"' not in after.split('id="contact-center"')[1].split("</section>")[0]


async def test_the_card_tells_an_administrator_how_to_end_a_leaked_secret_at_once(
    site: Site,
) -> None:
    """Before the action: once keeps the old one until the time shown, twice ends it at once."""
    await _connect(site)

    card = (await site.client.get(PAGE)).text

    assert "Maakt u één keer een nieuw geheim, dan blijft het oude nog 60 minuten geldig" in card
    assert "Maakt u daarna meteen nog een nieuw geheim, dan werkt het oude direct niet meer" in card
    assert "De platformbeheerder kan die tijd ook op 0 zetten" in card
