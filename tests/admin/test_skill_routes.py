"""The skill catalogue of an organisation (ADR 0121 step 2c): routes, audit and the shared page."""

import httpx
import pytest

from tests.admin.conftest import (
    AGENT,
    AUDITOR,
    GROUP_ADMIN,
    NO_ROLE,
    ORG_ADMIN,
    OUTSIDER,
    SAME_ORIGIN,
    SUPER,
    Site,
)
from tests.support.admin_site import audit_log
from tests.support.skill_seed import add_pool, add_skill, archive_skill, limit

PAGE = "/admin/organisations/demo/skills"
OTHER = "/admin/organisations/andere/skills"
ADD_SKILL_FORM = 'aria-labelledby="add-skill-heading"'
ADD_POOL_FORM = 'aria-labelledby="add-pool-heading"'


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


async def test_the_organisation_page_links_to_the_one_sub_tab(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    organisation = (await site.client.get("/admin/organisations/demo")).text

    assert f'href="{PAGE}"' in organisation
    assert "Vaardigheden en capaciteit" in organisation
    assert 'href="/admin/organisations/demo/capacity"' not in organisation  # no second home
    assert "/admin/organisations/andere/skills" not in organisation


async def test_the_old_capacity_address_moves_to_the_section_of_the_page(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    response = await site.client.get("/admin/organisations/demo/capacity")

    assert response.status_code == 308
    assert response.headers["location"] == f"{PAGE}#capacity"


async def test_a_skill_is_added_listed_and_audited_with_its_code_only(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    response = await create(site)

    assert response.status_code == 303
    assert response.headers["location"] == f"{PAGE}?done=skill_created#skills"
    page = (await site.client.get(PAGE, params={"done": "skill_created"})).text
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
    await add_skill(site)

    page = (await site.client.get(PAGE, params={"lang": "en"})).text

    assert "Skills and capacity" in page
    assert "A skill grants no rights and no access to data." in page
    assert "Add a skill" in page
    row = page.split("<td><code>intake</code></td>")[0].rsplit("<tr>", 1)[1]
    assert "Intake (en)" in row
    assert "Intake</span>" in row


async def test_both_sections_have_ids_of_their_own_and_every_input_a_distinct_one(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)
    await add_skill(site)

    page = (await site.client.get(PAGE)).text

    assert 'id="skills"' in page
    assert 'id="capacity"' in page
    assert page.count('id="skills-archived"') == page.count('id="pools-archived"') == 1
    assert ADD_SKILL_FORM in page
    assert ADD_POOL_FORM in page
    ids = [part.split('"')[0] for part in page.split(' id="')[1:]]
    assert len(ids) == len(set(ids))


async def test_an_invalid_code_is_refused_with_the_error_linked_to_its_input(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    response = await create(site, code="Not A Code")

    assert response.status_code == 422
    assert 'href="#skill_code"' in response.text
    assert 'aria-describedby="skill_code-hint skill_code-error"' in response.text
    assert "Gebruik alleen kleine letters, cijfers en streepjes" in response.text
    assert 'value="Not A Code"' in response.text  # kept for correction
    assert "<td><code>not" not in response.text
    # The whole page comes back and the summary names the section the error belongs to.
    assert "Vaardigheden: Gebruik alleen kleine letters" in response.text
    assert ADD_POOL_FORM in response.text
    assert 'name="pool_name"' in response.text


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
    skill_id = await add_skill(site)

    form = (await site.client.get(f"{PAGE}/{skill_id}")).text
    response = await site.client.post(
        f"{PAGE}/{skill_id}",
        data={"skill_code": "other", "label_nl": "Aanmelding", "label_en": "Registration"},
        headers=SAME_ORIGIN,
    )

    assert 'value="intake"' in form
    assert "readonly" in form.split('id="skill_code"')[1].split(">")[0]
    assert response.headers["location"] == f"{PAGE}?done=skill_saved#skills"
    page = (await site.client.get(PAGE)).text
    assert "Aanmelding" in page
    assert "<td><code>intake</code></td>" in page  # the posted code is ignored
    assert "<td><code>other</code></td>" not in page
    details = [e.detail for e in await audit_log(site) if e.action == "skills.manage"]
    assert details == ["skill.created,skill:intake", "skill.changed,skill:intake"]


async def test_saving_the_same_labels_says_unchanged_and_audits_nothing(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    skill_id = await add_skill(site)

    response = await site.client.post(
        f"{PAGE}/{skill_id}",
        data={"label_nl": " Intake ", "label_en": "Intake (en)"},
        headers=SAME_ORIGIN,
    )

    assert response.headers["location"] == f"{PAGE}?done=unchanged#skills"
    details = [e.detail for e in await audit_log(site) if e.action == "skills.manage"]
    assert details == ["skill.created,skill:intake"]


async def test_an_invalid_label_re_renders_the_form_with_the_error_linked(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    skill_id = await add_skill(site)

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
    skill_id = await add_skill(site)

    confirmation = (await site.client.get(f"{PAGE}/{skill_id}/archive")).text
    response = await site.client.post(f"{PAGE}/{skill_id}/archive", headers=SAME_ORIGIN)
    again = await site.client.post(f"{PAGE}/{skill_id}/archive", headers=SAME_ORIGIN)

    assert "Geen nieuwe eis en geen nieuwe toekenning" in confirmation
    assert "Bestaande verzoeken, eisen en toekenningen houden de vaardigheid" in confirmation
    assert "Verwijderen kan niet" in confirmation
    assert response.headers["location"] == f"{PAGE}?done=skill_archived#skills"
    assert again.status_code == 303
    assert "<td><code>intake</code></td>" not in (await site.client.get(PAGE)).text
    archived = (await site.client.get(PAGE, params={"skills": "archived"})).text
    assert "<td><code>intake</code></td>" in archived
    assert "Gearchiveerd" in archived
    assert ADD_SKILL_FORM not in archived  # nothing to add to the archive
    details = [e.detail for e in await audit_log(site) if e.action == "skills.manage"]
    assert details == ["skill.created,skill:intake", "skill.archived,skill:intake"]  # once
    # The code stays taken: an archived skill is not deleted (ADR 0121 point 5).
    assert (await create(site)).status_code == 422


async def test_an_archived_skill_has_no_edit_or_archive_page(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    skill_id = await add_skill(site)
    await archive_skill(site, skill_id)

    for method, suffix in (("GET", ""), ("POST", ""), ("GET", "/archive")):
        response = await site.client.request(
            method, f"{PAGE}/{skill_id}{suffix}", headers=SAME_ORIGIN, data={"label_nl": "x"}
        )
        assert response.status_code == 404, (method, suffix)
    details = [e.detail for e in await audit_log(site) if e.action == "skills.manage"]
    assert details == ["skill.created,skill:intake", "skill.archived,skill:intake"]


async def test_the_two_archived_filters_are_independent_and_keep_each_other(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    skill = await add_skill(site)
    await archive_skill(site, skill)
    await add_skill(site, "live", "Levend", "Live")

    only_skills = (await site.client.get(PAGE, params={"skills": "archived"})).text
    both = (await site.client.get(PAGE, params={"skills": "archived", "pools": "archived"})).text

    assert "<td><code>intake</code></td>" in only_skills
    assert "<td><code>live</code></td>" not in only_skills
    assert 'id="skills-archived" name="skills" value="archived" checked' in only_skills
    assert 'id="pools-archived" name="pools" value="archived">' in only_skills
    # Each filter form carries the state of the other, so applying one does not reset it.
    assert '<input type="hidden" name="pools" value="archived">' in both
    assert '<input type="hidden" name="skills" value="archived">' in both
    assert '<input type="hidden" name="pools"' not in only_skills


async def test_another_organisations_skill_is_not_found_by_its_id(site: Site) -> None:
    await site.login_as(SUPER)
    theirs = await add_skill(site, "theirs", organisation="andere")
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


async def test_whoever_may_open_the_organisation_reads_the_page_but_only_managers_get_the_forms(
    site: Site,
) -> None:
    skill = await add_skill(site)
    pool = await add_pool(site)
    readers = {"superuser": SUPER, "org admin": ORG_ADMIN, "auditor": AUDITOR}
    refused = {"group admin": GROUP_ADMIN, "other org": OUTSIDER, "agent": AGENT, "none": NO_ROLE}

    for name, principal in readers.items():
        await site.login_as(principal)
        page = await site.client.get(PAGE)
        manages = principal is not AUDITOR
        assert page.status_code == 200, name
        assert "<td><code>intake</code></td>" in page.text, name
        assert '<th scope="row">Loket' in page.text, name
        assert (ADD_SKILL_FORM in page.text) is manages, name
        assert (ADD_POOL_FORM in page.text) is manages, name
        assert (f"/skills/{skill}/archive" in page.text) is manages, name
        assert (f"/capacity/{pool}/archive" in page.text) is manages, name
    for name, principal in refused.items():
        await site.login_as(principal)
        assert (await site.client.get(PAGE)).status_code == 403, name


async def test_an_auditor_cannot_write_or_open_the_pages_of_one_skill(site: Site) -> None:
    skill = await add_skill(site)
    await site.login_as(AUDITOR)

    assert (await create(site, code="x")).status_code == 403
    for method, suffix in (("GET", ""), ("POST", ""), ("GET", "/archive"), ("POST", "/archive")):
        response = await site.client.request(
            method, f"{PAGE}/{skill}{suffix}", headers=SAME_ORIGIN, data={"label_nl": "x"}
        )
        assert response.status_code == 403, (method, suffix)


async def test_only_the_organisation_admin_and_the_superuser_may_change_the_catalogue(
    site: Site,
) -> None:
    await site.login_as(GROUP_ADMIN)
    assert (await create(site)).status_code == 403
    await site.login_as(OUTSIDER)
    assert (await create(site)).status_code == 403
    await site.login_as(SUPER)
    assert (await site.client.get(PAGE)).status_code == 200
    assert not [e for e in await audit_log(site) if e.action == "skills.manage" and e.detail]


async def test_the_limit_refuses_a_new_skill_and_the_page_says_so(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    limit(site, skills=2)
    first = await add_skill(site, "one", "Een", "One")
    await add_skill(site, "two", "Twee", "Two")

    refused = await create(site, code="three")
    page = (await site.client.get(PAGE)).text
    await archive_skill(site, first)  # an archived skill does not count
    accepted = await create(site, code="three")

    assert refused.status_code == 422
    assert "Uw organisatie heeft het maximale aantal vaardigheden bereikt." in refused.text
    assert 'href="#skill_code"' in refused.text
    assert "Het maximum van 2 vaardigheden is bereikt." in page
    assert ADD_SKILL_FORM not in page
    assert accepted.status_code == 303


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


@pytest.mark.parametrize("filter_name", ["skills", "pools"])
async def test_an_unknown_filter_value_shows_the_active_rows(site: Site, filter_name: str) -> None:
    await site.login_as(ORG_ADMIN)
    await add_skill(site)

    page = (await site.client.get(PAGE, params={filter_name: "yes"})).text

    assert "<td><code>intake</code></td>" in page
