"""The organisation text editor (ADR 0074): who may open it, what is stored, what is shown."""

import pytest

from tests.admin.conftest import (
    AUDITOR,
    GROUP_ADMIN,
    ORG_ADMIN,
    OUTSIDER,
    SAME_ORIGIN,
    SUPER,
    Site,
)
from tests.admin.test_admin_flow import audit_events

TEXTS = "/admin/organisations/demo/texts"
INTRO = "Kies een moment; een gesprek duurt {minutes} minuten."


async def test_the_editor_opens_in_the_default_locale_and_shows_the_shipped_texts(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)

    opened = await site.client.get(TEXTS)
    assert opened.headers["location"] == f"{TEXTS}/nl"
    page = (await site.client.get(f"{TEXTS}/nl")).text

    assert "Kies een dag en tijd voor een videogesprek." in page  # a page text, as shipped
    assert "Herinnering: videogesprek op {date} om {time}" in page  # a message text, as shipped
    assert "U kunt gebruiken: {minutes}." in page
    assert 'aria-current="page"' in page


async def test_a_saved_text_shows_on_the_participant_page_of_every_group_at_once(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)
    before = (await site.client.get("/g/demo")).text
    assert "Kies een dag en tijd voor een videogesprek." in before

    saved = await site.client.post(f"{TEXTS}/nl", data={"group.intro": INTRO}, headers=SAME_ORIGIN)

    assert saved.headers["location"] == f"{TEXTS}/nl?done=texts_saved"
    after = (await site.client.get("/g/demo")).text
    assert "Kies een moment; een gesprek duurt 30 minuten." in after
    assert "Kies een dag en tijd" not in after
    english = (await site.client.get("/g/demo?lang=en")).text
    assert "Choose a day and time" in english  # the other locale is untouched
    editor = (await site.client.get(f"{TEXTS}/nl?lang=nl")).text
    assert "Eigen tekst van deze organisatie." in editor


async def test_the_organisation_text_is_escaped_on_the_participant_page(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    await site.client.post(
        f"{TEXTS}/nl",
        data={"group.intro": "<script>alert(1)</script> {{bold}}"},
        headers=SAME_ORIGIN,
    )

    page = (await site.client.get("/g/demo")).text
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt; {bold}" in page


async def test_the_default_or_an_empty_field_brings_the_default_back(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await site.client.post(f"{TEXTS}/nl", data={"booked.text": "Eigen"}, headers=SAME_ORIGIN)
    assert "Eigen" in (await site.client.get(f"{TEXTS}/nl")).text

    await site.client.post(f"{TEXTS}/nl", data={"booked.text": ""}, headers=SAME_ORIGIN)

    page = (await site.client.get(f"{TEXTS}/nl")).text
    assert "Eigen tekst van deze organisatie." not in page
    assert "We hebben u een e-mail gestuurd." in page


async def test_an_unknown_placeholder_is_refused_and_kept_in_the_form(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    response = await site.client.post(
        f"{TEXTS}/nl", data={"confirm.text": "Hoi {first_name}, {bsn}"}, headers=SAME_ORIGIN
    )

    assert response.status_code == 422
    assert 'href="#confirm.text"' in response.text
    assert "{deadline}" in response.text  # the allowed placeholders are listed
    assert "Hoi {first_name}, {bsn}" in response.text


async def test_a_key_off_the_allow_list_is_ignored(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    saved = await site.client.post(
        f"{TEXTS}/nl", data={"home.title": "gekaapt", "admin.title": "x"}, headers=SAME_ORIGIN
    )

    assert saved.status_code == 303
    assert "gekaapt" not in (await site.client.get("/")).text


async def test_an_unknown_locale_is_not_found(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    assert (await site.client.get(f"{TEXTS}/xx")).status_code == 404
    assert (await site.client.post(f"{TEXTS}/xx", data={}, headers=SAME_ORIGIN)).status_code == 404


@pytest.mark.parametrize("principal", [GROUP_ADMIN, AUDITOR, OUTSIDER])
async def test_others_cannot_open_or_change_the_texts(site: Site, principal) -> None:  # type: ignore[no-untyped-def]
    await site.login_as(principal)

    assert (await site.client.get(f"{TEXTS}/nl")).status_code == 403
    denied = await site.client.post(f"{TEXTS}/nl", data={"booked.text": "x"}, headers=SAME_ORIGIN)
    assert denied.status_code == 403


async def test_a_change_needs_a_cross_site_safe_request(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    response = await site.client.post(
        f"{TEXTS}/nl", data={"booked.text": "x"}, headers={"sec-fetch-site": "cross-site"}
    )

    assert response.status_code == 403


async def test_the_superuser_edits_any_organisation_and_the_change_is_audited(site: Site) -> None:
    await site.login_as(SUPER)

    await site.client.post(f"{TEXTS}/en", data={"booked.title": "Confirm it"}, headers=SAME_ORIGIN)

    events = [(a, b, str(c)) for a, b, c, _ in await audit_events(site)]
    assert ("sub-super", "texts.organisation.manage", "success") in events
    assert all("Confirm it" not in str(event) for event in events)  # never the text itself


async def test_the_organisation_settings_page_links_to_the_texts(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    page = (await site.client.get("/admin/organisations/demo/settings")).text

    assert f'href="{TEXTS}"' in page
