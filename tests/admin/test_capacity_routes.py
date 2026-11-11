"""The capacity pools of an organisation (ADR 0121 step 2c, screen 4): routes and audit."""

import re
from dataclasses import replace

import httpx

from app.admin.capacity_routes import flow_name, hidden_flows
from app.domain.authorization import Scope
from app.domain.capacity import CapacityPool, PoolMode
from app.domain.capacity_service import FlowRef
from app.domain.service_flows import FlowState
from app.i18n import Translator, load_catalogues
from app.web.locale import RequestLocale
from tests.admin.conftest import GROUP_ADMIN, ORG_ADMIN, OUTSIDER, SAME_ORIGIN, SUPER, Site
from tests.admin.test_skill_routes import create as create_skill
from tests.admin.test_skill_routes import id_of as skill_id_of
from tests.support.admin_site import audit_log, site_uow
from tests.support.service_flows import flow as make_flow
from tests.support.service_flows import revision

PAGE = "/admin/organisations/demo/capacity"
OTHER = "/admin/organisations/andere/capacity"
EVERYTHING = Scope(everything=True)


async def create(
    site: Site, name: str = "Loket", skills: tuple[int, ...] = (), page: str = PAGE
) -> httpx.Response:
    """Post the form that adds a counted pool, ticking ``skills``."""
    data = {"pool_name": name, **{f"skill-{i}": "on" for i in skills}}
    return await site.client.post(page, data=data, headers=SAME_ORIGIN)


async def pool_id(site: Site, name: str, page: str = PAGE) -> int:
    """Return the id of the pool called ``name``, read from its edit link in the list."""
    text = (await site.client.get(page)).text
    row = text.split(f'<th scope="row">{name}')[1]
    return int(re.search(rf"{page}/(\d+)", row)[1])  # type: ignore[index]


async def add_skill(site: Site, code: str) -> int:
    """Add a skill to the demo organisation and return its id."""
    await create_skill(site, code, code.title(), code.title())
    return await skill_id_of(site, code)


async def draw_on(site: Site, pool: int, code: str = "draft-one") -> None:
    """Store a draft flow of the demo group that draws on ``pool``."""
    async with site_uow(site).locked() as tx:
        group = await tx.get_group("demo")
        assert group is not None
        draft = revision(
            flow_id=None, capacity_pool_id=pool, labels={"nl": "Concept", "en": "Draft"}
        )
        flow = replace(
            make_flow(code, FlowState.DRAFT, None, frozenset(), None, draft), group_id=group.id
        )
        await tx.save_draft(EVERYTHING, flow)


async def legacy_pool(site: Site) -> int:
    """Store a legacy pool of the demo group, as the migration's backfill does."""
    async with site_uow(site).locked() as tx:
        group = await tx.get_group("demo")
        organisation = await tx.get_organisation("demo")
        assert group is not None
        assert organisation is not None
        stored = await tx.save_capacity_pool(
            EVERYTHING,
            CapacityPool(None, organisation.id, "Demo", PoolMode.LEGACY_GROUP, group_id=group.id),
        )
    assert stored.id is not None
    return stored.id


async def test_a_counted_pool_is_added_with_its_skills_listed_and_audited(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    passport = await add_skill(site, "passport")

    response = await create(site, "Loket", (passport,))

    assert response.status_code == 303
    assert response.headers["location"] == f"{PAGE}?done=pool_created"
    page = (await site.client.get(response.headers["location"])).text
    assert "De capaciteitspool is toegevoegd." in page
    assert '<th scope="row">Loket' in page
    assert "Passport" in page.split('<th scope="row">Loket')[1].split("</tr>")[0]
    assert "Eigen</span>" in page
    # The warning and the missing staffing numbers are on the page (ADR 0121 screen 4).
    assert "Let op bij tellen. Een pool belooft dat iedere getelde medewerker alle" in page
    assert "Hoeveel medewerkers per tijdblok in een pool werken" in page
    assert 'name="staffing' not in page
    details = [e.detail for e in await audit_log(site) if e.action == "capacity.manage"]
    assert re.fullmatch(r"capacity_pool\.created,pool:\d+", details[0])


async def test_the_page_speaks_english(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    page = (await site.client.get(PAGE, params={"lang": "en"})).text

    assert "Mind the counting. A pool promises that every counted member of staff" in page
    assert "How many members of staff work in a pool per time block is set once" in page
    assert "Add a pool" in page


async def test_a_blank_name_and_an_archived_or_unknown_skill_are_refused(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    old = await add_skill(site, "old")
    await site.client.post(f"/admin/organisations/demo/skills/{old}/archive", headers=SAME_ORIGIN)

    blank = await create(site, " ")
    archived = await create(site, "Loket", (old,))
    unknown = await create(site, "Loket", (999999,))

    assert blank.status_code == 422
    assert 'href="#pool_name"' in blank.text
    assert 'aria-describedby="pool_name-error"' in blank.text
    for refused in (archived, unknown):
        assert refused.status_code == 422
        assert 'href="#guaranteed_skill_ids"' in refused.text
        assert (
            'aria-describedby="guaranteed_skill_ids-hint guaranteed_skill_ids-error"'
            in refused.text
        )
        assert "Een gekozen vaardigheid bestaat niet meer" in refused.text
    assert "<td>Loket" not in (await site.client.get(PAGE)).text


async def test_a_pool_is_changed_and_an_archived_skill_it_has_stays_shown(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    old, fresh = await add_skill(site, "old"), await add_skill(site, "fresh")
    await create(site, "Loket", (old,))
    pool = await pool_id(site, "Loket")
    await site.client.post(f"/admin/organisations/demo/skills/{old}/archive", headers=SAME_ORIGIN)

    form = (await site.client.get(f"{PAGE}/{pool}")).text
    response = await site.client.post(
        f"{PAGE}/{pool}",
        data={"pool_name": "Spreekuur", f"skill-{fresh}": "on"},
        headers=SAME_ORIGIN,
    )
    unchanged = await site.client.post(
        f"{PAGE}/{pool}",
        data={"pool_name": "Spreekuur", f"skill-{fresh}": "on"},
        headers=SAME_ORIGIN,
    )

    assert f'id="skill-{old}" name="skill-{old}" value="on" checked' in form  # still ticked
    assert "Gearchiveerd</span>" in form  # marked in words
    assert f'id="skill-{fresh}" name="skill-{fresh}" value="on">' in form
    assert response.headers["location"] == f"{PAGE}?done=pool_saved"
    assert unchanged.headers["location"] == f"{PAGE}?done=unchanged"
    page = (await site.client.get(PAGE)).text
    assert '<th scope="row">Spreekuur' in page
    assert "Fresh" in page
    details = [e.detail for e in await audit_log(site) if e.action == "capacity.manage"]
    assert [d.split(",")[0] for d in details] == ["capacity_pool.created", "capacity_pool.changed"]


async def test_a_pool_with_a_flow_on_it_cannot_be_archived_and_the_flow_is_named(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)
    await create(site, "Loket")
    pool = await pool_id(site, "Loket")
    await draw_on(site, pool)

    confirmation = (await site.client.get(f"{PAGE}/{pool}/archive")).text
    refused = await site.client.post(f"{PAGE}/{pool}/archive", headers=SAME_ORIGIN)

    assert "Concept" in confirmation  # the flows that draw on it
    assert refused.status_code == 422
    assert "De pool wordt nog gebruikt door: Concept" in refused.text
    assert 'class="error-summary"' in refused.text
    assert '<th scope="row">Loket' in (await site.client.get(PAGE)).text  # still in use
    assert "Gedeeld</span>" not in (await site.client.get(PAGE)).text  # one flow: own


async def test_an_unused_pool_is_archived_after_the_confirmation_page(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await create(site, "Loket")
    pool = await pool_id(site, "Loket")

    confirmation = (await site.client.get(f"{PAGE}/{pool}/archive")).text
    response = await site.client.post(f"{PAGE}/{pool}/archive", headers=SAME_ORIGIN)

    assert "Geen flow kan de pool nog nieuw kiezen." in confirmation
    assert response.headers["location"] == f"{PAGE}?done=pool_archived"
    assert '<th scope="row">Loket' not in (await site.client.get(PAGE)).text
    archived = (await site.client.get(PAGE, params={"archived": "1"})).text
    assert '<th scope="row">Loket' in archived
    details = [e.detail for e in await audit_log(site) if e.action == "capacity.manage"]
    assert details[-1] == f"capacity_pool.archived,pool:{pool}"


async def test_a_legacy_pool_is_shown_read_only_and_cannot_be_changed(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    legacy = await legacy_pool(site)

    page = (await site.client.get(PAGE)).text
    row = page.split('<th scope="row">Demo')[1].split("</tr>")[0]

    assert "Bestaande pools zijn de bezetting" in page
    assert "Bezetting van de dienst zelf, ongewijzigd" in page
    assert "Bestaand</span>" in row
    assert "Alleen ter inzage" in row
    assert f"{PAGE}/{legacy}" not in row  # no edit or archive link
    for method, suffix in (("GET", ""), ("POST", ""), ("GET", "/archive"), ("POST", "/archive")):
        response = await site.client.request(
            method, f"{PAGE}/{legacy}{suffix}", data={"pool_name": "x"}, headers=SAME_ORIGIN
        )
        assert response.status_code in (404, 409), (method, suffix)
    assert not [e for e in await audit_log(site) if e.action == "capacity.manage"]


async def test_two_flows_make_a_pool_shared(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await create(site, "Loket")
    pool = await pool_id(site, "Loket")
    await draw_on(site, pool, "one")
    await draw_on(site, pool, "two")

    page = (await site.client.get(PAGE)).text

    assert "Gedeeld</span>" in page


def test_the_flows_of_other_services_are_counted_not_named() -> None:
    translator = Translator(load_catalogues())
    nl, en = (
        RequestLocale(translator.bind(code), remember=False, translator=translator)
        for code in ("nl", "en")
    )
    ref = FlowRef({"nl": "Intake", "en": "Intake (en)"}, "Balie")

    assert flow_name(ref, "nl") == "Intake · Balie"
    assert flow_name(ref, "en") == "Intake (en) · Balie"
    assert flow_name(FlowRef({"nl": "Intake"}, ""), "en") == "Intake"
    assert hidden_flows(0, nl) == ""
    assert hidden_flows(1, nl) == "en 1 flow van een andere dienst"
    assert hidden_flows(2, nl) == "en 2 flows van andere diensten"
    assert hidden_flows(2, en) == "and 2 flows of other services"


async def test_another_organisations_pool_is_not_found_by_its_id(site: Site) -> None:
    await site.login_as(SUPER)
    await create(site, "Theirs", page=OTHER)
    theirs = await pool_id(site, "Theirs", OTHER)
    await site.login_as(ORG_ADMIN)

    for method, suffix in (("GET", ""), ("POST", ""), ("GET", "/archive"), ("POST", "/archive")):
        response = await site.client.request(
            method, f"{PAGE}/{theirs}{suffix}", data={"pool_name": "x"}, headers=SAME_ORIGIN
        )
        assert response.status_code == 404, (method, suffix)
    assert (await site.client.get(f"{OTHER}/{theirs}")).status_code == 403
    assert "Theirs" not in (await site.client.get(PAGE)).text


async def test_a_pool_cannot_guarantee_another_organisations_skill(site: Site) -> None:
    await site.login_as(SUPER)
    await create_skill(site, "theirs", "Theirs", "Theirs", "/admin/organisations/andere/skills")
    theirs = await skill_id_of(site, "theirs", "/admin/organisations/andere/skills")
    await site.login_as(ORG_ADMIN)

    response = await create(site, "Loket", (theirs,))

    assert response.status_code == 422
    assert 'href="#guaranteed_skill_ids"' in response.text


async def test_only_the_organisation_admin_and_the_superuser_may_use_the_screen(
    site: Site,
) -> None:
    await site.login_as(GROUP_ADMIN)
    assert (await site.client.get(PAGE)).status_code == 403
    assert (await create(site)).status_code == 403
    await site.login_as(OUTSIDER)
    assert (await site.client.get(PAGE)).status_code == 403
    await site.login_as(SUPER)
    assert (await site.client.get(PAGE)).status_code == 200
    assert (await site.client.get("/admin/organisations/nope/capacity")).status_code == 404
