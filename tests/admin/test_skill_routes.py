"""The skill catalogue of an organisation (ADR 0121 steps 2c, screen 4): routes and audit."""

import re

import httpx

from tests.admin.conftest import GROUP_ADMIN, ORG_ADMIN, OUTSIDER, SAME_ORIGIN, SUPER, Site
from tests.support.admin_site import audit_log

PAGE = "/admin/organisations/demo/skills"
OTHER = "/admin/organisations/andere/skills"


async def create(
    site: Site,
    code: str = "intake",
    nl: str = "Intake",
    en: str = "Intake (en)",
    page: str = PAGE,
) -> httpx.Response:
    """Post the form that adds a skill."""
    data = {"skill_code": code, "label_nl": nl, "label_en": en}
    return await site.client.post(page, data=data, headers=SAME_ORIGIN)


async def id_of(site: Site, code: str, page: str = PAGE) -> int:
    """Return the id of the skill with ``code``, read from the edit link of the list."""
    text = (await site.client.get(page)).text
    row = text.split(f"<td><code>{code}</code></td>")[1]
    return int(re.search(rf"{page}/(\d+)", row)[1])  # type: ignore[index]


async def test_the_organisation_page_links_to_the_skills_sub_tab(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    organisation = (await site.client.get("/admin/organisations/demo")).text

    assert f'href="{PAGE}"' in organisation
    assert 'href="/admin/organisations/demo/capacity"' in organisation
    assert "/admin/organisations/andere/skills" not in organisation


async def test_a_skill_is_added_listed_and_audited_with_its_code_only(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    response = await create(site)

    assert response.status_code == 303
    assert response.headers["location"] == f"{PAGE}?done=skill_created"
    page = (await site.client.get(response.headers["location"])).text
    assert "De vaardigheid is toegevoegd." in page
    assert "Een vaardigheid geeft geen rechten en geen inzage." in page
    assert "<td><code>intake</code></td>" in page
    assert "Intake (en)" in page  # the other language underneath
    (event,) = [e for e in await audit_log(site) if e.action == "skills.manage"]
    assert (event.actor, event.detail, event.organisation) == (
        "sub-org",
        "skill.created,skill:intake",
        "demo",
    )
    assert "Olaf" not in repr(event)


async def test_the_page_speaks_english_and_shows_the_dutch_name_underneath(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await create(site)

    page = (await site.client.get(PAGE, params={"lang": "en"})).text

    assert "A skill grants no rights and no access to data." in page
    assert "Add a skill" in page
    row = page.split("<td><code>intake</code></td>")[0]
    assert "Intake (en)" in row.rsplit("<tr>", 1)[1]
    assert "Intake</span>" in row.rsplit("<tr>", 1)[1]


async def test_an_invalid_code_is_refused_with_the_error_linked_to_its_input(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    response = await create(site, code="Not A Code")

    assert response.status_code == 422
    assert 'href="#skill_code"' in response.text
    assert 'aria-describedby="skill_code-hint skill_code-error"' in response.text
    assert "Gebruik alleen kleine letters, cijfers en streepjes" in response.text
    assert 'value="Not A Code"' in response.text  # kept for correction
    assert "<td><code>not" not in response.text


async def test_a_missing_label_is_refused_and_a_duplicate_code_too(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    missing = await create(site, en="")
    await create(site)
    again = await create(site, nl="Anders", en="Other")

    assert missing.status_code == 422
    assert 'href="#label_nl"' in missing.text
    assert "Vul de naam in het Nederlands en in het Engels in" in missing.text
    assert again.status_code == 422
    assert "Deze code is al in gebruik in uw organisatie." in again.text
    assert 'href="#skill_code"' in again.text


async def test_the_labels_change_and_the_code_stays_fixed(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await create(site)
    skill_id = await id_of(site, "intake")

    form = (await site.client.get(f"{PAGE}/{skill_id}")).text
    response = await site.client.post(
        f"{PAGE}/{skill_id}",
        data={"skill_code": "other", "label_nl": "Aanmelding", "label_en": "Registration"},
        headers=SAME_ORIGIN,
    )

    assert 'value="intake"' in form
    assert "readonly" in form.split('id="skill_code"')[1].split(">")[0]
    assert response.headers["location"] == f"{PAGE}?done=skill_saved"
    page = (await site.client.get(PAGE)).text
    assert "Aanmelding" in page
    assert "<td><code>intake</code></td>" in page  # the posted code is ignored
    assert "<td><code>other</code></td>" not in page
    details = [e.detail for e in await audit_log(site) if e.action == "skills.manage"]
    assert details == ["skill.created,skill:intake", "skill.changed,skill:intake"]


async def test_an_invalid_label_re_renders_the_form_with_the_error_linked(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await create(site)
    skill_id = await id_of(site, "intake")

    response = await site.client.post(
        f"{PAGE}/{skill_id}", data={"label_nl": "", "label_en": "x"}, headers=SAME_ORIGIN
    )

    assert response.status_code == 422
    assert 'href="#label_nl"' in response.text
    assert 'value="x"' in response.text


async def test_archiving_asks_first_and_keeps_the_skill_under_the_archived_filter(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)
    await create(site)
    skill_id = await id_of(site, "intake")

    confirmation = (await site.client.get(f"{PAGE}/{skill_id}/archive")).text
    response = await site.client.post(f"{PAGE}/{skill_id}/archive", headers=SAME_ORIGIN)
    again = await site.client.post(f"{PAGE}/{skill_id}/archive", headers=SAME_ORIGIN)

    assert "Geen nieuwe eis en geen nieuwe toekenning" in confirmation
    assert "Bestaande verzoeken, eisen en toekenningen houden de vaardigheid" in confirmation
    assert "Verwijderen kan niet" in confirmation
    assert response.headers["location"] == f"{PAGE}?done=skill_archived"
    assert again.status_code == 303
    assert "<td><code>intake</code></td>" not in (await site.client.get(PAGE)).text
    archived = (await site.client.get(PAGE, params={"archived": "1"})).text
    assert "<td><code>intake</code></td>" in archived
    assert "Gearchiveerd" in archived
    details = [e.detail for e in await audit_log(site) if e.action == "skills.manage"]
    assert details == ["skill.created,skill:intake", "skill.archived,skill:intake"]  # once
    # The code stays taken: an archived skill is not deleted (ADR 0121 point 5).
    assert (await create(site)).status_code == 422


async def test_another_organisations_skill_is_not_found_by_its_id(site: Site) -> None:
    await site.login_as(SUPER)
    await create(site, code="theirs", page=OTHER)
    theirs = await id_of(site, "theirs", OTHER)
    await site.login_as(ORG_ADMIN)

    for method, suffix in (("GET", ""), ("POST", ""), ("GET", "/archive"), ("POST", "/archive")):
        response = await site.client.request(
            method, f"{PAGE}/{theirs}{suffix}", headers=SAME_ORIGIN, data={"label_nl": "x"}
        )
        assert response.status_code == 404, (method, suffix)
    # Through its own organisation's address the administrator has no access at all.
    assert (await site.client.get(f"{OTHER}/{theirs}")).status_code == 403
    await site.login_as(SUPER)
    assert (await site.client.get(f"{PAGE}/{theirs}")).status_code == 404
    assert "theirs" not in (await site.client.get(PAGE)).text
    assert "theirs" in (await site.client.get(OTHER)).text


async def test_only_the_organisation_admin_and_the_superuser_may_use_the_screen(
    site: Site,
) -> None:
    await site.login_as(GROUP_ADMIN)
    assert (await site.client.get(PAGE)).status_code == 403
    assert (await create(site)).status_code == 403
    await site.login_as(OUTSIDER)
    assert (await site.client.get(PAGE)).status_code == 403
    assert (await create(site)).status_code == 403
    await site.login_as(SUPER)
    assert (await site.client.get(PAGE)).status_code == 200
    assert not [e for e in await audit_log(site) if e.action == "skills.manage" and e.detail]


async def test_cross_site_posts_are_refused(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    response = await site.client.post(
        PAGE,
        data={"skill_code": "x", "label_nl": "x", "label_en": "x"},
        headers={"sec-fetch-site": "cross-site"},
    )

    assert response.status_code == 403


async def test_an_unknown_organisation_is_not_found(site: Site) -> None:
    await site.login_as(SUPER)

    assert (await site.client.get("/admin/organisations/nope/skills")).status_code == 404
