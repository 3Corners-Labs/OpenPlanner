"""Skills of a person on the staff page (ADR 0121 step 2c, screen 5): grants, audit, scope."""

import httpx

from tests.admin.conftest import (
    AUDITOR,
    GROUP_ADMIN,
    ORG_ADMIN,
    OUTSIDER,
    SAME_ORIGIN,
    SUPER,
    Site,
)
from tests.admin.test_skill_routes import create as create_skill
from tests.admin.test_skill_routes import id_of as skill_id_of
from tests.support.admin_site import audit_log

SKILLS = "/admin/organisations/demo/skills"


async def person_id(site: Site, name: str) -> int:
    """Return the number of the person called ``name`` in the staff list (as the superuser)."""
    await site.login_as(SUPER)
    text = (await site.client.get("/admin/staff")).text
    return int(text.split(f'">{name}</a>')[0].rsplit('href="/admin/staff/', 1)[1])


async def grant(
    site: Site, member: int, org: str = "demo", skills: tuple[int, ...] = ()
) -> httpx.Response:
    """Post the skills form of ``member`` in ``org`` with ``skills`` ticked."""
    return await site.client.post(
        f"/admin/staff/{member}/skills/{org}",
        data={f"skill-{i}": "on" for i in skills},
        headers=SAME_ORIGIN,
    )


async def with_gina_and_a_skill(site: Site) -> tuple[int, int]:
    """Log everyone in, add the skill ``passport`` and return Gina's number and its id."""
    for principal in (GROUP_ADMIN, OUTSIDER, AUDITOR):
        await site.login_as(principal)
    gina = await person_id(site, "Gina Group")
    await site.login_as(ORG_ADMIN)
    await create_skill(site, "passport", "Paspoort", "Passport")
    return gina, await skill_id_of(site, "passport")


async def test_an_organisation_admin_grants_and_takes_away_a_skill_with_an_audit_trail(
    site: Site,
) -> None:
    gina, passport = await with_gina_and_a_skill(site)

    page = (await site.client.get(f"/admin/staff/{gina}")).text
    granted = await grant(site, gina, skills=(passport,))
    unchanged = await grant(site, gina, skills=(passport,))
    shown = (await site.client.get(granted.headers["location"])).text
    removed = await grant(site, gina)

    assert "Rechten bepalen wat Gina Group mag zien. Vaardigheden bepalen welke verzoeken" in page
    assert "Een wijziging geldt vanaf het volgende verzoek dat wordt opgepakt of gestart." in page
    assert f'action="/admin/staff/{gina}/skills/demo"' in page
    assert f'id="skill-{passport}" name="skill-{passport}" value="on">' in page  # not held yet
    assert granted.status_code == 303
    assert granted.headers["location"] == f"/admin/staff/{gina}?done=staff_skills_saved#skills"
    assert "De vaardigheden zijn opgeslagen." in shown
    assert f'id="skill-{passport}" name="skill-{passport}" value="on" checked' in shown
    assert unchanged.headers["location"] == f"/admin/staff/{gina}?done=unchanged#skills"
    assert removed.headers["location"].endswith("done=staff_skills_saved#skills")
    events = [e for e in await audit_log(site) if e.action == "skills.manage"]
    assert [e.detail for e in events][1:] == [
        f"staff_skills.changed,staff:{gina},skill.add:passport",
        f"staff_skills.changed,staff:{gina},skill.remove:passport",
    ]
    assert all("Gina" not in repr(e) for e in events)


async def test_the_catalogue_counts_the_people_who_hold_a_skill(site: Site) -> None:
    gina, passport = await with_gina_and_a_skill(site)

    await grant(site, gina, skills=(passport,))
    row = (await site.client.get(SKILLS)).text.split("<td><code>passport</code></td>")[1]

    assert "<td>1</td>" in row.split("</tr>")[0]


async def test_a_viewer_without_the_right_sees_the_skills_as_a_list_and_cannot_change_them(
    site: Site,
) -> None:
    gina, passport = await with_gina_and_a_skill(site)
    await grant(site, gina, skills=(passport,))

    await site.login_as(AUDITOR)
    page = (await site.client.get(f"/admin/staff/{gina}")).text
    refused = await grant(site, gina)
    await site.login_as(GROUP_ADMIN)
    own = (await site.client.get(f"/admin/staff/{gina}")).text

    for text in (page, own):
        assert "<li>Paspoort</li>" in text
        assert f'action="/admin/staff/{gina}/skills/demo"' not in text
    assert refused.status_code == 403
    assert (await grant(site, gina)).status_code == 403


async def test_an_archived_skill_a_person_holds_stays_shown_and_can_be_taken_away(
    site: Site,
) -> None:
    gina, passport = await with_gina_and_a_skill(site)
    await grant(site, gina, skills=(passport,))
    await site.client.post(f"{SKILLS}/{passport}/archive", headers=SAME_ORIGIN)

    page = (await site.client.get(f"/admin/staff/{gina}")).text
    kept = await grant(site, gina, skills=(passport,))
    removed = await grant(site, gina)
    again = await grant(site, gina, skills=(passport,))

    assert f'id="skill-{passport}" name="skill-{passport}" value="on" checked' in page
    assert "Gearchiveerd</span>" in page
    assert kept.headers["location"].endswith("done=unchanged#skills")
    assert removed.headers["location"].endswith("done=staff_skills_saved#skills")
    assert again.status_code == 422  # an archived skill is never newly granted
    assert 'href="#skills-demo"' in again.text
    assert "Een gekozen vaardigheid bestaat niet meer" in again.text


async def test_a_person_outside_the_scope_does_not_exist_for_the_grant(site: Site) -> None:
    await site.login_as(OUTSIDER)
    await site.login_as(ORG_ADMIN)
    otto = await person_id(site, "Otto Other")
    await site.login_as(ORG_ADMIN)

    assert (await site.client.get(f"/admin/staff/{otto}")).status_code == 404
    assert (await grant(site, otto)).status_code == 404
    assert (await grant(site, otto, org="andere")).status_code == 403  # not their organisation


async def test_a_skill_needs_a_membership_in_the_organisation_it_belongs_to(site: Site) -> None:
    gina, passport = await with_gina_and_a_skill(site)
    otto = await person_id(site, "Otto Other")
    await site.login_as(SUPER)

    page = (await site.client.get(f"/admin/staff/{otto}")).text
    response = await grant(site, otto, skills=(passport,))

    assert f'action="/admin/staff/{otto}/skills/andere"' in page
    assert f'action="/admin/staff/{otto}/skills/demo"' not in page  # not a member of demo
    assert response.status_code == 422
    assert "Deze persoon is geen lid van deze organisatie" in response.text
    assert 'href="#skills-demo"' in response.text
    assert gina  # Gina keeps her own membership and is not affected


async def test_a_skill_of_another_organisation_cannot_be_granted(site: Site) -> None:
    gina, _ = await with_gina_and_a_skill(site)
    await site.login_as(SUPER)
    await create_skill(site, "theirs", "Theirs", "Theirs", "/admin/organisations/andere/skills")
    theirs = await skill_id_of(site, "theirs", "/admin/organisations/andere/skills")

    response = await grant(site, gina, skills=(theirs,))

    assert response.status_code == 422
    assert "Een gekozen vaardigheid bestaat niet meer" in response.text
    assert not [e for e in await audit_log(site) if e.detail.startswith("staff_skills")]


async def test_the_staff_page_speaks_english(site: Site) -> None:
    gina, _ = await with_gina_and_a_skill(site)

    page = (await site.client.get(f"/admin/staff/{gina}", params={"lang": "en"})).text

    assert "Rights determine what Gina Group may see. Skills determine which requests" in page
    assert "Save skills" in page


async def test_cross_site_grants_are_refused(site: Site) -> None:
    gina, passport = await with_gina_and_a_skill(site)

    response = await site.client.post(
        f"/admin/staff/{gina}/skills/demo",
        data={f"skill-{passport}": "on"},
        headers={"sec-fetch-site": "cross-site"},
    )

    assert response.status_code == 403
