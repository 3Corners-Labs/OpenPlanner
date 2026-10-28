"""Closure content screens (ADR 0037 §2, CL-1): a team's own contact block, heading and copy.

Calendar as in ``test_closure_routes``: "now" is Thursday 1 January 2026 08:00 UTC.
"""

import httpx

from tests.admin.conftest import GROUP_ADMIN, ORG_ADMIN, SAME_ORIGIN, Site

CLOSURE = "/admin/groups/demo/closure"
TEAM_CONTACTS = f"{CLOSURE}/contacts"
ORGANISATION_CONTACTS = "/admin/organisations/demo/contacts"
CLOSE = {
    "notice_nl": "Door een stroomstoring zijn wij gesloten.",
    "notice_en": "We are closed because of a power cut.",
    "heading_nl": "Storing bij het loket",
}
DESK = {"contact_1_kind": "email", "contact_1_value": "loket@gemeente.example"}
CALL = {"contact_1_kind": "phone", "contact_1_value": "14 020"}


async def _post(site: Site, path: str, data: dict[str, str]) -> httpx.Response:
    return await site.client.post(path, data=data, headers=SAME_ORIGIN)


async def test_the_team_page_shows_the_organisation_default_until_it_has_its_own(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)
    await _post(site, ORGANISATION_CONTACTS, CALL)

    before = (await site.client.get(TEAM_CONTACTS)).text
    saved = await _post(site, TEAM_CONTACTS, DESK)
    after = (await site.client.get(CLOSURE)).text

    assert "De standaard van de organisatie geldt (1 contactmogelijkheden)." in before
    assert '<a class="contact-block__link" href="tel:14020">14 020</a>' in before  # preview
    assert 'name="contact_1_value"' in before
    assert saved.headers["location"] == f"{TEAM_CONTACTS}?done=saved"
    assert "Eigen blok met 1 contactmogelijkheden." in after
    assert f'href="{TEAM_CONTACTS}"' in after


async def test_a_closure_shows_its_heading_and_the_copy_taken_at_the_start(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    await _post(site, TEAM_CONTACTS, DESK)

    started = await _post(site, CLOSURE, CLOSE)
    await _post(site, TEAM_CONTACTS, CALL)  # a later edit leaves the running closure alone
    page = (await site.client.get(CLOSURE)).text

    assert started.status_code == 303
    assert "<strong>Storing bij het loket</strong>" in page
    assert 'href="mailto:loket@gemeente.example"' in page
    assert 'aria-labelledby="closure-contacts-heading"' in page
    assert 'name="heading_nl" type="text" value="Storing bij het loket"' in page  # pre-filled
    assert "Neem de contactalternatieven over die nu voor dit team gelden (1)" in page


async def test_an_update_can_take_over_the_block_that_applies_now(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    await _post(site, TEAM_CONTACTS, DESK)
    await _post(site, CLOSURE, CLOSE)
    await _post(site, TEAM_CONTACTS, CALL)

    updated = await _post(site, f"{CLOSURE}/update", {**CLOSE, "refresh_contacts": "yes"})
    page = (await site.client.get(CLOSURE)).text

    assert updated.status_code == 303
    assert 'href="tel:14020"' in page
    assert "loket@gemeente.example" not in page


async def test_a_closure_without_any_block_says_so(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    await _post(site, CLOSURE, CLOSE)

    assert (
        "Bij deze sluiting staan geen contactalternatieven."
        in (await site.client.get(CLOSURE)).text
    )


async def test_a_heading_that_is_too_long_is_refused_and_linked_to_its_input(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    response = await _post(site, CLOSURE, {**CLOSE, "heading_en": "x" * 121})

    assert response.status_code == 422
    assert 'href="#heading_en"' in response.text
    assert "De kop mag hooguit 120 tekens op één regel zijn." in response.text


async def test_a_javascript_link_in_the_teams_block_is_refused(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    response = await _post(
        site, TEAM_CONTACTS, {"contact_1_kind": "website", "contact_1_value": "javascript:x()"}
    )

    assert response.status_code == 422
    assert 'href="#contact_1_value"' in response.text
    assert "Dit team heeft geen eigen blok" in response.text  # nothing stored
