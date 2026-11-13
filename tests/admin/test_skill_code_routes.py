"""The contact-center code on a skill's page, against SQLite (ADR 0121 step 4b).

The organisation admin maps a skill to the code its contact-center flow tests, on the page of the
skill under *Skills and capacity*; the catalogue shows the code; two skills cannot share one; a
code that is no code is refused at the input; a blank code takes the mapping away.
"""

import httpx

from app.domain.authorization import Scope
from tests.admin.conftest import ORG_ADMIN, SAME_ORIGIN, Site
from tests.support.admin_site import audit_log, site_uow
from tests.support.skill_seed import add_skill

PAGE = "/admin/organisations/demo/skills"
DEMO = Scope(organisations=frozenset({"demo"}))


async def _save(site: Site, skill_id: int, code: str) -> httpx.Response:
    data = {"label_nl": "Intake", "label_en": "Intake (en)", "contact_center_code": code}
    return await site.client.post(f"{PAGE}/{skill_id}", data=data, headers=SAME_ORIGIN)


async def _code(site: Site, skill_id: int) -> str | None:
    async with site_uow(site).read() as tx:
        skill = await tx.get_skill(DEMO, skill_id)
    assert skill is not None
    return skill.contact_center_code


async def test_an_admin_maps_a_skill_and_the_catalogue_shows_the_code(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    skill_id = await add_skill(site)

    form = (await site.client.get(f"{PAGE}/{skill_id}")).text
    saved = await _save(site, skill_id, "intake_1")
    catalogue = (await site.client.get(PAGE)).text

    assert 'name="contact_center_code"' in form
    assert saved.status_code == 303
    assert await _code(site, skill_id) == "intake_1"
    assert "Contactcenter: intake_1" in catalogue
    details = [e.detail for e in await audit_log(site) if e.action == "skills.manage"]
    assert details[-1] == "skill.changed,skill:intake"

    await _save(site, skill_id, "  ")
    assert await _code(site, skill_id) is None


async def test_two_skills_cannot_share_a_code_and_a_bad_code_is_refused_at_the_input(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)
    first = await add_skill(site)
    second = await add_skill(site, "german", "Duits", "German")
    await _save(site, first, "shared")

    twin = await _save(site, second, "shared")
    bad = await _save(site, second, "met spatie")

    for refused in (twin, bad):
        assert refused.status_code == 422
        assert 'href="#contact_center_code"' in refused.text
    assert "heeft deze code al" in twin.text
    assert await _code(site, second) is None
