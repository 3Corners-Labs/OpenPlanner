"""The authentication page of an organisation: steps, warnings and the route check (ADR 0083).

The page says what is left to do before an own identity provider is chosen, warns about a
set-up that never routes, and answers where an address would log in. The address is personal
data: it is posted, never kept, and the answer shows its domain only (B11, B12).
"""

from collections.abc import AsyncIterator

import pytest

from app.config import Settings
from tests.admin.conftest import (
    AUDITOR,
    ORG_ADMIN,
    OUTSIDER,
    SAME_ORIGIN,
    SUPER,
    Site,
    _running_site,
)
from tests.support.organisation_login import LOGIN, register, route_domain

CHECK = f"{LOGIN}/_check"


@pytest.fixture
async def routed(settings: Settings, admin_db: str) -> AsyncIterator[Site]:
    """A site that allows organisation providers, with acme-a.nl routing to org-a."""
    switched_on = settings.model_copy(update={"organisation_idps_enabled": True})
    async with _running_site(switched_on, admin_db) as running:
        running.page_url = await route_domain(running)  # type: ignore[attr-defined]
        yield running


@pytest.fixture
async def superuser(site: Site) -> Site:
    await site.login_as(SUPER)
    return site


async def _ask_at(url: str, site: Site, address: str) -> str:
    response = await site.client.post(url, data={"address": address}, headers=SAME_ORIGIN)
    assert response.status_code == 200
    return response.text


async def _ask(site: Site, address: str) -> str:
    return await _ask_at(CHECK, site, address)


async def test_the_tab_and_page_are_called_authentication(superuser: Site) -> None:
    page = await superuser.client.get(LOGIN)

    assert "<h2" in page.text
    assert ">Authenticatie<" in page.text
    assert ">Inloggen<" not in page.text


async def test_an_empty_organisation_starts_with_the_steps_to_do(superuser: Site) -> None:
    page = (await superuser.client.get(LOGIN)).text

    assert "Stappenplan" in page
    assert "ORGANISATION_IDPS_ENABLED" in page
    assert "Wacht op een eerdere stap" in page
    assert "platformprovider" in page  # the platform account stays usable


async def test_a_provider_that_is_on_without_a_domain_is_warned_about(superuser: Site) -> None:
    page_url = await register(superuser)
    await superuser.client.post(f"{page_url}/enable", data={"enabled": "on"}, headers=SAME_ORIGIN)

    page = (await superuser.client.get(LOGIN)).text

    assert "dit wordt nog niet gekozen" in page
    assert "heeft geen geverifieerd domein" in page


async def test_an_address_without_a_matching_domain_goes_to_the_platform(superuser: Site) -> None:
    text = await _ask(superuser, "ada@nergens.nl")

    assert "Geen enkele provider claimt domein nergens.nl" in text
    assert "ada" not in text.split("Probeer een adres", 1)[1].split("nergens.nl", 1)[0]


async def test_a_user_name_without_a_domain_goes_to_the_platform(superuser: Site) -> None:
    assert "inlognaam zonder domein" in await _ask(superuser, "ada")


async def test_the_reserved_suffix_goes_to_the_platform(superuser: Site) -> None:
    assert "gereserveerd" in await _ask(superuser, "ada@demo.openplanner.internal")


async def test_a_blank_address_shows_the_page_without_an_answer(superuser: Site) -> None:
    text = await _ask(superuser, "   ")

    assert "Vul een e-mailadres" in text


async def test_a_verified_domain_on_a_live_provider_is_chosen(routed: Site) -> None:
    await routed.login_as(SUPER)

    text = await _ask(routed, "jan@acme-a.nl")
    sub = await _ask(routed, "jan@mail.acme-a.nl")
    page = (await routed.client.get(LOGIN)).text

    assert "logt in bij provider Acme A" in text
    assert "logt in bij provider Acme A" in sub
    assert "dit wordt nog niet gekozen" not in page
    assert "jan@" not in text  # B12: the domain, never the address


async def test_the_check_says_when_the_provider_is_off(routed: Site) -> None:
    await routed.login_as(SUPER)
    await routed.client.post(f"{routed.page_url}/enable", data={}, headers=SAME_ORIGIN)  # type: ignore[attr-defined]

    text = await _ask(routed, "jan@acme-a.nl")
    page = (await routed.client.get(LOGIN)).text

    assert "staat uit" in text
    assert "routeert pas als de provider aan staat" in page


@pytest.mark.parametrize(("admin", "status"), [(ORG_ADMIN, 200), (AUDITOR, 200), (OUTSIDER, 403)])
async def test_those_who_may_look_may_check(site: Site, admin: str, status: int) -> None:
    await site.login_as(admin)

    response = await site.client.post(CHECK, data={"address": "x@y.nl"}, headers=SAME_ORIGIN)

    assert response.status_code == status


async def test_a_too_long_address_is_refused_not_cut(superuser: Site) -> None:
    text = await _ask(superuser, "a" * 330 + "@y.nl")

    assert "Vul een e-mailadres" in text


def test_every_step_reason_and_warning_has_its_text() -> None:
    from app.admin.idp_routes import CHECK_TEXTS, STEP_TEXTS, WARNING_TEXTS
    from app.domain.idp_setup import STEP_KEYS, RouteReason

    assert set(STEP_TEXTS) == set(STEP_KEYS)
    assert set(CHECK_TEXTS) == set(RouteReason)
    assert len(WARNING_TEXTS) == 5


async def test_b16_a_pending_claim_of_another_organisation_is_not_told_apart(
    superuser: Site,
) -> None:
    await register(superuser)
    await superuser.client.post(
        f"{LOGIN}/org-a/domains", data={"domain": "acme-a.nl"}, headers=SAME_ORIGIN
    )

    text = await _ask_at("/admin/organisations/andere/login/_check", superuser, "x@acme-a.nl")

    assert "Geen enkele provider claimt domein acme-a.nl" in text
    assert "Acme A" not in text


async def test_b12_the_address_is_in_neither_the_page_nor_the_log(
    superuser: Site, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()

    text = await _ask(superuser, "ada.lovelace@nergens.nl")
    seen = capsys.readouterr()

    assert "ada.lovelace" not in text
    assert "ada.lovelace" not in seen.out + seen.err
    assert "nergens.nl" in text  # the domain is the answer
