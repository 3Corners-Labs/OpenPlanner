"""The names an organisation gives its organisation admin and group admin (ADR 0077)."""

from tests.admin.conftest import GROUP_ADMIN, ORG_ADMIN, OUTSIDER, SAME_ORIGIN, SUPER, Site

TEXTS = "/admin/organisations/demo/texts/nl"
ORG_KEY = "authz.role.org_admin"
GROUP_KEY = "authz.role.group_admin"
NAMES = {ORG_KEY: "Hoofdbeheer", GROUP_KEY: "Teamleider"}


async def test_the_editor_has_a_card_for_the_two_roles(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    page = (await site.client.get(TEXTS)).text

    assert "Hoe u de beheerders noemt" in page
    assert f'name="{ORG_KEY}"' in page
    assert f'name="{GROUP_KEY}"' in page
    assert (
        'value="Organisatiebeheerder"' in page
    )  # the shipped name until the organisation sets one
    assert 'name="authz.role.superuser"' not in page
    assert 'name="authz.role.auditor"' not in page


async def test_the_chosen_name_shows_next_to_the_rights_with_the_standard_name(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    saved = await site.client.post(TEXTS, data=NAMES, headers=SAME_ORIGIN)

    assert saved.headers["location"] == f"{TEXTS}?done=texts_saved"
    rights = (await site.client.get("/admin/rights")).text
    assert "Hoofdbeheer (Organisatiebeheerder)" in rights
    matrix = (await site.client.get("/admin/matrix")).text
    assert "Hoofdbeheer (Organisatiebeheerder)" in matrix
    assert "Teamleider (Groepsbeheerder)" in matrix


async def test_the_top_bar_shows_the_chosen_name_on_the_page_of_a_group(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await site.client.post(TEXTS, data=NAMES, headers=SAME_ORIGIN)

    await site.login_as(GROUP_ADMIN)
    page = (await site.client.get("/admin/groups/demo/staffing")).text

    assert ">Teamleider<" in page
    assert ">Groepsbeheerder<" not in page


async def test_a_name_of_another_role_is_refused_whatever_its_case(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    refused = await site.client.post(
        TEXTS, data={ORG_KEY: "GROEPSBEHEERDER", GROUP_KEY: ""}, headers=SAME_ORIGIN
    )

    assert refused.status_code == 422
    assert "die niet de naam van een andere rol is" in refused.text


async def test_the_two_names_of_one_form_may_not_be_equal(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    refused = await site.client.post(
        TEXTS, data={ORG_KEY: "Beheer", GROUP_KEY: "beheer"}, headers=SAME_ORIGIN
    )

    assert refused.status_code == 422


async def test_the_superuser_and_the_auditor_cannot_be_renamed(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    await site.client.post(
        TEXTS,
        data={"authz.role.superuser": "Baas", "authz.role.auditor": "Spion"},
        headers=SAME_ORIGIN,
    )

    await site.login_as(SUPER)
    matrix = (await site.client.get("/admin/matrix")).text
    assert "Baas" not in matrix
    assert "Spion" not in matrix


async def test_another_organisation_keeps_the_shipped_names(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    await site.client.post(TEXTS, data=NAMES, headers=SAME_ORIGIN)

    await site.login_as(OUTSIDER)
    rights = (await site.client.get("/admin/rights")).text

    assert "Hoofdbeheer" not in rights
    assert "Organisatiebeheerder" in rights


async def test_a_name_changes_no_right(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    before = await site.client.get("/admin/organisations/demo/texts/nl")
    await site.login_as(ORG_ADMIN)
    await site.client.post(TEXTS, data=NAMES, headers=SAME_ORIGIN)

    await site.login_as(GROUP_ADMIN)
    after = await site.client.get("/admin/organisations/demo/texts/nl")

    assert before.status_code == after.status_code in (403, 404)  # a group admin still may not edit
