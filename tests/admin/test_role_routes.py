"""The role editor and custom roles end to end (ADR 0039 step 3).

The custom-role part of the authorisation matrix test: a copy of each template, made in the
editor and held through its claim value, answers every admin route exactly like the template
in the hand-written table of ``test_authorization_matrix.py``; a narrowed copy answers less.
Abuse cases: a non-superuser posts to the editor, a form smuggles in a locked or
out-of-reach permission, a claim value collides with the superuser's, an auditor copy reaches
a personal-data route.
"""

import re

import pytest

from app.domain.authorization import MATRIX, Permission, Principal, Role
from app.domain.entitlements import LoginClaims
from tests.admin.conftest import AUDITOR, GROUP_ADMIN, ORG_ADMIN, SAME_ORIGIN, SUPER, Site
from tests.admin.test_authorization_matrix import EXPECTED
from tests.support.principals import legacy_claims

CLAIM = "openplanner-custom"
TEMPLATE_ROWS = {
    Role.ORG_ADMIN: ("org_admin (demo)", ORG_ADMIN),
    Role.GROUP_ADMIN: ("group_admin (demo)", GROUP_ADMIN),
    Role.AUDITOR: ("auditor (demo)", AUDITOR),
}


def role_form(
    template: Role, permissions: frozenset[Permission], claim: str = CLAIM, **extra: str
) -> dict[str, str]:
    form = {
        "template": template.value,
        "name_nl": "Teamleider",
        "name_en": "Team lead",
        "claim_value": claim,
        **{f"perm.{p}": "on" for p in permissions},
    }
    form.update(extra)
    return form


def holder_like(admin: Principal, claim: str = CLAIM) -> LoginClaims:
    """A login with no built-in role, the scope claims of ``admin`` and the custom claim."""
    scope = legacy_claims(admin)
    return LoginClaims(
        "sub-custom",
        "Carla Custom",
        organisations=scope.organisations,
        groups=scope.groups,
        role_claims=frozenset({claim}),
    )


async def create_role(site: Site, form: dict[str, str]) -> int:
    await site.login_as(SUPER)
    response = await site.client.post("/admin/roles", data=form, headers=SAME_ORIGIN)
    assert response.status_code == 303, response.text
    match = re.fullmatch(r"/admin/roles/(\d+)\?done=created", response.headers["location"])
    assert match
    return int(match.group(1))


async def status(site: Site, method: str, path: str) -> int:
    if method == "GET":
        return (await site.client.get(path)).status_code
    return (await site.client.post(path, headers=SAME_ORIGIN)).status_code


@pytest.mark.parametrize("template", list(TEMPLATE_ROWS))
async def test_a_copy_of_a_template_answers_every_route_like_the_template(
    site: Site, template: Role
) -> None:
    row, principal = TEMPLATE_ROWS[template]
    await create_role(site, role_form(template, MATRIX[template]))
    await site.login_with(holder_like(principal))

    wrong = []
    for key, paths in EXPECTED.items():
        method = key.split(" ", 1)[0]
        for path, expected in paths.items():
            got = await status(site, method, path)
            if got != expected[row]:
                wrong.append((key, path, expected[row], got))
    assert wrong == []


async def test_a_narrowed_copy_loses_exactly_what_was_switched_off(site: Site) -> None:
    narrowed = MATRIX[Role.GROUP_ADMIN] - {Permission.BOOKINGS_VIEW, Permission.TEXTS_MANAGE}
    narrowed -= {Permission.BOOKINGS_MANAGE}  # it requires bookings.view (ADR 0044)
    await create_role(site, role_form(Role.GROUP_ADMIN, narrowed))
    await site.login_with(holder_like(GROUP_ADMIN))

    assert (await site.client.get("/admin/groups/demo/settings")).status_code == 403
    assert (await site.client.get("/admin/groups/demo/agenda")).status_code == 403
    assert (await site.client.get("/admin/groups/demo/messages")).status_code == 403
    assert (await site.client.get("/admin/groups/demo/staffing")).status_code == 200
    assert (await site.client.get("/admin/groups/andere-team/staffing")).status_code == 403


async def test_a_role_change_works_on_the_next_request_without_logging_in(site: Site) -> None:
    role_id = await create_role(site, role_form(Role.GROUP_ADMIN, MATRIX[Role.GROUP_ADMIN]))
    client = site.client
    superuser = dict(client.cookies)
    client.cookies.clear()
    await site.login_with(holder_like(GROUP_ADMIN))
    holder = dict(client.cookies)

    def act_as(cookies: dict[str, str]) -> None:
        client.cookies.clear()
        client.cookies.update(cookies)

    assert (await client.get("/admin/groups/demo/agenda")).status_code == 200

    act_as(superuser)
    narrowed = MATRIX[Role.GROUP_ADMIN] - {Permission.BOOKINGS_VIEW, Permission.BOOKINGS_MANAGE}
    form = role_form(Role.GROUP_ADMIN, narrowed, version="1")
    saved = await client.post(f"/admin/roles/{role_id}", data=form, headers=SAME_ORIGIN)
    assert saved.status_code == 303

    act_as(holder)
    assert (await client.get("/admin/groups/demo/agenda")).status_code == 403
    assert (await client.get("/admin/groups/demo/staffing")).status_code == 200

    act_as(superuser)
    deleted = await client.post(
        f"/admin/roles/{role_id}/delete", data={"version": "2"}, headers=SAME_ORIGIN
    )
    assert deleted.status_code == 303

    act_as(holder)
    assert (await client.get("/admin/")).status_code == 403  # no role left


async def test_the_editor_refuses_smuggled_permissions_with_linked_messages(site: Site) -> None:
    await site.login_as(SUPER)
    form = role_form(
        Role.GROUP_ADMIN,
        MATRIX[Role.GROUP_ADMIN] | {Permission.INTEGRATIONS_MANAGE, Permission.GROUP_CREATE},
    )
    response = await site.client.post("/admin/roles", data=form, headers=SAME_ORIGIN)

    assert response.status_code == 422
    assert 'href="#permissions"' in response.text
    assert "alleen voor de superuser" in response.text
    assert "buiten het bereik" in response.text
    assert (await site.client.get("/admin/roles")).text.count("/admin/roles/") == 1  # only "new"


async def test_a_missing_requirement_links_to_its_checkbox(site: Site) -> None:
    await site.login_as(SUPER)
    permissions = MATRIX[Role.GROUP_ADMIN] - {Permission.GROUP_VIEW}
    response = await site.client.post(
        "/admin/roles", data=role_form(Role.GROUP_ADMIN, permissions), headers=SAME_ORIGIN
    )

    assert response.status_code == 422
    assert 'href="#perm.schedule.manage"' in response.text
    assert 'id="perm.schedule.manage-error"' in response.text


async def test_an_auditor_copy_cannot_get_personal_data(site: Site) -> None:
    await site.login_as(SUPER)
    form = role_form(Role.AUDITOR, MATRIX[Role.AUDITOR] | {Permission.BOOKINGS_VIEW})
    response = await site.client.post("/admin/roles", data=form, headers=SAME_ORIGIN)
    assert response.status_code == 422
    assert "persoonsgegevens" in response.text

    await create_role(site, role_form(Role.AUDITOR, MATRIX[Role.AUDITOR]))
    await site.login_with(holder_like(AUDITOR))
    assert (await site.client.get("/admin/groups/demo/agenda")).status_code == 403


@pytest.mark.parametrize(
    "claim", ["openplanner-superuser", "OpenPlanner-Superuser", "openplanner-agent"]
)
async def test_a_built_in_claim_value_is_refused(site: Site, claim: str) -> None:
    await site.login_as(SUPER)
    form = role_form(Role.ORG_ADMIN, MATRIX[Role.ORG_ADMIN], claim=claim)
    response = await site.client.post("/admin/roles", data=form, headers=SAME_ORIGIN)

    assert response.status_code == 422
    assert 'href="#claim_value"' in response.text


async def test_a_stale_form_is_refused(site: Site) -> None:
    role_id = await create_role(site, role_form(Role.GROUP_ADMIN, MATRIX[Role.GROUP_ADMIN]))
    first = role_form(Role.GROUP_ADMIN, MATRIX[Role.GROUP_ADMIN], name_nl="Leider", version="1")
    assert (
        await site.client.post(f"/admin/roles/{role_id}", data=first, headers=SAME_ORIGIN)
    ).status_code == 303
    second = role_form(Role.GROUP_ADMIN, MATRIX[Role.GROUP_ADMIN], name_nl="Anders", version="1")
    response = await site.client.post(f"/admin/roles/{role_id}", data=second, headers=SAME_ORIGIN)

    assert response.status_code == 422
    assert "Iemand anders heeft deze rol" in response.text


async def test_non_superusers_cannot_post_to_the_editor(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    form = role_form(Role.ORG_ADMIN, MATRIX[Role.ORG_ADMIN])
    response = await site.client.post("/admin/roles", data=form, headers=SAME_ORIGIN)
    assert response.status_code == 403


async def test_pages_show_the_role_names_and_the_history(site: Site) -> None:
    role_id = await create_role(site, role_form(Role.GROUP_ADMIN, MATRIX[Role.GROUP_ADMIN]))

    editor = await site.client.get(f"/admin/roles/{role_id}")
    assert editor.status_code == 200
    assert 'name="version" value="1"' in editor.text
    assert "Kopie van Groepsbeheerder" in editor.text
    assert 'id="perm.integrations.manage"' not in editor.text  # locked: no checkbox
    overview = await site.client.get("/admin/roles")
    assert "openplanner-custom" in overview.text

    await site.login_with(holder_like(GROUP_ADMIN))
    matrix = await site.client.get("/admin/matrix")
    assert matrix.status_code == 200
    assert "Teamleider" in matrix.text  # a column, and the history row
    assert "Gemaakt" in matrix.text
    assert 'href="/admin/roles"' not in matrix.text  # no editor link for non-superusers
    rights = await site.client.get("/admin/rights")
    assert "Teamleider" in rights.text


async def test_the_new_form_copies_the_template(site: Site) -> None:
    await site.login_as(SUPER)
    page = await site.client.get("/admin/roles/new", params={"template": "auditor"})
    assert page.status_code == 200
    assert 'id="perm.audit.view" name="perm.audit.view" checked' in page.text
    assert 'id="perm.bookings.view"' not in page.text
    unknown = await site.client.get("/admin/roles/new", params={"template": "superuser"})
    assert "Kopie van Organisatiebeheerder" in unknown.text
