"""What a walkthrough of the flow screens found (ADR 0121 step 2d): wording, notices and rights."""

from app.admin.skill_inputs import skill_name
from app.domain.authorization import CustomRole, Permission, Role
from app.domain.entitlements import LoginClaims
from app.domain.skills import Skill
from tests.admin.conftest import ORG_ADMIN, SAME_ORIGIN, SUPER, Site
from tests.support.admin_site import site_uow
from tests.support.domain import make_group
from tests.support.flow_pages import BASE, act, create, create_ready, form_data
from tests.support.flow_seed import add_flow, add_subjects, publish_flow
from tests.support.skill_seed import add_pool, add_skill


async def test_an_empty_code_asks_for_a_code_and_does_not_say_new_is_reserved(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    response = await site.client.post(BASE, data=form_data(flow_code=""), headers=SAME_ORIGIN)

    assert response.status_code == 422
    assert "Vul een code in." in response.text
    assert 'href="#flow_code"' in response.text
    assert "gereserveerd" not in response.text.split("<main")[1].split('id="flow_code"')[0]
    assert 'value="new"' not in response.text
    english = await site.client.post(
        f"{BASE}?lang=en", data=form_data(flow_code=""), headers=SAME_ORIGIN
    )
    assert "Enter a code." in english.text


async def test_the_confirmation_of_a_save_is_shown_once(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await create(site)

    page = (await site.client.get(f"{BASE}/advies", params={"done": "flow_created"})).text

    assert page.count("De flow is aangemaakt als concept.") == 1


async def test_a_draft_that_is_not_in_order_has_no_publish_button_but_the_reason(
    site: Site,
) -> None:
    await site.login_as(ORG_ADMIN)
    await create(site)

    page = (await site.client.get(f"{BASE}/advies")).text
    refused = await act(site, "publish")

    assert f'action="{BASE}/advies/publish"' not in page
    assert (
        "Publiceren kan pas als alle punten in orde zijn, behalve de punten die nog volgen." in page
    )
    assert '<p class="hint" id="publish-hint">Publiceren kan pas' in page
    assert refused.status_code == 422
    assert "Publiceren kan pas als alle punten in orde zijn" in refused.text
    assert 'id="readiness-heading"' in refused.text
    assert "Gepubliceerde revisie" not in (await site.client.get(f"{BASE}/advies")).text
    english = (await site.client.get(f"{BASE}/advies", params={"lang": "en"})).text
    assert '<p class="hint" id="publish-hint">Publishing is possible once' in english


async def test_a_draft_with_only_the_point_that_follows_open_offers_publish(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await create_ready(site)

    page = (await site.client.get(f"{BASE}/advies")).text

    assert f'action="{BASE}/advies/publish"' in page
    assert (await act(site, "publish")).status_code == 303


async def test_the_checklist_names_the_skills_the_pool_does_not_guarantee(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    first = await add_skill(site, "eerste", "Eerste", "First")
    second = await add_skill(site, "tweede", "Tweede", "Second")
    pool = await add_pool(site, "Leeg")
    await create(
        site,
        capacity_pool_id=str(pool),
        **{f"skill-{first}": "on", f"skill-{second}": "on"},
    )

    page = (await site.client.get(f"{BASE}/advies")).text
    english = (await site.client.get(f"{BASE}/advies", params={"lang": "en"})).text

    assert "Ontbreekt in de pool: Eerste (eerste), Tweede (tweede)" in page
    assert "Missing in the pool: First (eerste), Second (tweede)" in english


def test_every_list_names_a_skill_with_its_code() -> None:
    skill = Skill(1, 1, "intake", {"nl": "Intake", "en": "Intake (en)"})

    assert skill_name(skill, "nl") == "Intake (intake)"
    assert skill_name(skill, "en") == "Intake (en) (intake)"
    assert skill_name(Skill(2, 1, "x", {"nl": "Alleen", "en": "Only"}), "en") == "Only (x)"


async def test_the_page_of_a_subject_has_one_heading_with_its_name(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await add_subjects(site, {"contract": "Contract"})
    await create_ready(site, offer_contract="yes")
    await act(site, "publish")
    await act(site, "prepare")

    page = (await site.client.get(f"{BASE}/advies/subjects/contract")).text

    assert page.count(">Extra vaardigheden voor Contract<") == 1


async def test_an_unknown_skill_or_pool_answers_with_the_house_not_found_page(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    for path in (
        "/admin/organisations/demo/skills/99999",
        "/admin/organisations/demo/capacity/99999",
    ):
        response = await site.client.get(path)
        assert response.status_code == 404, path
        assert "Pagina niet gevonden" in response.text
        assert "Er ging iets mis" not in response.text


async def test_the_service_admin_of_the_service_manages_its_flows_and_no_other(site: Site) -> None:
    await site.login_as(SUPER)
    await add_subjects(site, {"contract": "Contract"})
    pool = await add_pool(site, "Loket")
    await add_flow(site, "een", pool, subjects=frozenset({"contract"}))
    await add_flow(site, "twee", pool)
    async with site_uow(site).locked() as tx:
        organisation = await tx.get_organisation("demo")
        assert organisation is not None
        await tx.add_group(
            make_group(id=0, organisation_id=organisation.id, slug="tweede", name="Tweede")
        )
    await add_flow(site, "geheim", pool, group="tweede")
    await publish_flow(site, "geheim", group="tweede")
    async with site_uow(site).locked() as tx:
        await tx.add_custom_role(
            CustomRole(
                None,
                Role.GROUP_ADMIN,
                frozenset({Permission.GROUP_EDIT}),
                "dienstbeheer",
                "Rol",
                "Role",
            )
        )
    await site.login_with(
        LoginClaims(
            "sub-service",
            "Sam Service",
            groups=frozenset({"demo"}),
            role_claims=frozenset({"dienstbeheer"}),
        )
    )
    other = "/admin/groups/tweede/flows"

    assert (await site.client.get(BASE)).status_code == 200
    assert (await site.client.get(f"{BASE}/een")).status_code == 200
    assert (await site.client.get(f"{BASE}/een/subjects/contract")).status_code == 200
    assert (await site.client.get(f"{BASE}/twee/delete")).status_code == 200
    assert (await act(site, "publish", "een")).status_code == 303
    assert (await act(site, "prepare", "een")).status_code == 303
    assert (await site.client.get(f"{BASE}/een/discard")).status_code == 200
    assert (await act(site, "delete", "twee")).status_code == 303
    for path in (other, f"{other}/geheim", f"{other}/geheim/delete", f"{other}/geheim/discard"):
        assert (await site.client.get(path)).status_code == 403, path
    assert (await site.client.get(f"{BASE}/geheim")).status_code == 404
    assert (await site.client.post(f"{BASE}/geheim/delete", headers=SAME_ORIGIN)).status_code == 404
    assert (
        await site.client.post(f"{BASE}/geheim/publish", headers=SAME_ORIGIN)
    ).status_code == 404
