"""Organisation settings: class D values per organisation (ADR 0042 step 5)."""

import re

from tests.admin.conftest import AUDITOR, ORG_ADMIN, OUTSIDER, SAME_ORIGIN, SUPER, Site

URL = "/admin/organisations/demo/settings"


async def test_an_org_admin_lists_and_opens_their_own_organisation(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    listed = (await site.client.get("/admin/organisation-settings")).text
    page = (await site.client.get(URL)).text

    assert 'href="/admin/organisations/demo/settings"' in listed
    assert "Andere" not in listed.split('id="page-title"')[1]
    assert "Tijdnotatie" in page
    assert "Standaardwaarde" in page  # nothing set yet: the platform default


async def test_a_value_for_the_organisation_applies_to_its_admin_and_participants(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)

    response = await site.client.post(
        f"{URL}/time_format", data={"value": "12h"}, headers=SAME_ORIGIN
    )

    assert response.status_code == 303
    page = (await site.client.get(URL)).text
    assert "Organisatie" in page
    assert f'action="{URL}/time_format/reset"' in page
    booking = (await site.client.get("/g/demo")).text
    assert re.search(r"\b\d{1,2}:\d{2}\s?[ap]\.m\.", booking)  # the group's times in 12 hours


async def test_an_invalid_time_zone_is_refused_at_its_row(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    response = await site.client.post(
        f"{URL}/admin_timezone", data={"value": "Mars/Olympus"}, headers=SAME_ORIGIN
    )

    assert response.status_code == 422
    assert 'id="error-admin_timezone"' in response.text


async def test_another_organisations_admin_and_the_auditor_may_not_change_it(site: Site) -> None:
    for principal in (OUTSIDER, AUDITOR):
        await site.login_as(principal)
        response = await site.client.post(
            f"{URL}/time_format", data={"value": "12h"}, headers=SAME_ORIGIN
        )
        assert response.status_code == 403


async def test_the_superuser_sets_the_platform_default_on_advanced_settings(site: Site) -> None:
    await site.login_as(SUPER)
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001

    response = await site.client.post(
        "/admin/settings/shortfall_horizon_days", data={"value": "21"}, headers=SAME_ORIGIN
    )

    assert response.status_code == 303
    await site.client.get("/admin/settings")
    assert app.state.settings.shortfall_horizon_days == 21
