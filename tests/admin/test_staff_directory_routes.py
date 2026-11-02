"""Staff management in the bundled identity provider from the portal (ADR 0095)."""

from collections.abc import AsyncIterator

import pytest

from app.config import Settings
from app.domain.staff_directory import DirectoryUser
from tests.admin.conftest import (
    AUDITOR,
    GROUP_ADMIN,
    ORG_ADMIN,
    SAME_ORIGIN,
    SUPER,
    Site,
    _running_site,
)
from tests.fakes.staff_directory import FakeStaffDirectory

P = "openplanner"
INVITE = {
    "email": "nina@example.test",
    "first_name": "Nina",
    "last_name": "Nieuw",
    "role": "org_admin",
    "place": "demo",
}


@pytest.fixture
async def managed(settings: Settings, admin_db: str) -> AsyncIterator[Site]:
    directory = FakeStaffDirectory(
        [
            DirectoryUser(
                "kc-mine",
                "a",
                "mine@example.test",
                "A",
                "B",
                True,
                frozenset({f"{P}:org_admin:demo"}),
            ),
        ]
    )
    async with _running_site(settings, admin_db, directory=directory) as running:
        yield running


async def test_without_a_directory_the_screen_offers_no_actions(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    text = (await site.client.get("/admin/staff")).text
    redirect = await site.client.get("/admin/staff/invite")
    refused = await site.client.post("/admin/staff/invite", data=INVITE, headers=SAME_ORIGIN)

    assert "Medewerker uitnodigen" not in text
    assert redirect.status_code == 303, redirect.text[:400]
    assert refused.status_code == 422
    assert "niet beschikbaar" in refused.text


async def test_an_organisation_admin_sees_the_invite_button_and_the_pending_people(
    managed: Site,
) -> None:
    await managed.login_as(ORG_ADMIN)

    text = (await managed.client.get("/admin/staff")).text

    assert 'href="/admin/staff/invite"' in text
    assert "mine@example.test" in text
    assert "Uitgenodigd, nog niet ingelogd" in text


async def test_the_invite_form_renders(managed: Site) -> None:
    await managed.login_as(ORG_ADMIN)

    response = await managed.client.get("/admin/staff/invite")

    assert response.status_code == 200
    assert "Uitnodiging versturen" in response.text


async def test_an_invitation_creates_the_person_with_the_role_and_mails_them(
    managed: Site,
) -> None:
    await managed.login_as(ORG_ADMIN)

    response = await managed.client.post("/admin/staff/invite", data=INVITE, headers=SAME_ORIGIN)

    assert response.status_code == 303
    assert managed.directory is not None
    created = next(u for u in managed.directory.users.values() if u.email == INVITE["email"])
    assert created.entitlements == {f"{P}:org_admin:demo"}
    assert managed.directory.mails[0][0] == created.id


async def test_an_organisation_admin_cannot_invite_into_another_organisation(
    managed: Site,
) -> None:
    await managed.login_as(ORG_ADMIN)

    response = await managed.client.post(
        "/admin/staff/invite", data={**INVITE, "place": "andere"}, headers=SAME_ORIGIN
    )

    assert response.status_code == 422
    assert managed.directory is not None
    assert all(u.email != INVITE["email"] for u in managed.directory.users.values())


async def test_an_organisation_admin_cannot_invite_a_superuser(managed: Site) -> None:
    await managed.login_as(ORG_ADMIN)

    response = await managed.client.post(
        "/admin/staff/invite",
        data={**INVITE, "role": "superuser", "place": ""},
        headers=SAME_ORIGIN,
    )

    assert response.status_code == 422


async def test_the_superuser_invites_a_superuser(managed: Site) -> None:
    await managed.login_as(SUPER)

    response = await managed.client.post(
        "/admin/staff/invite",
        data={**INVITE, "role": "superuser", "place": ""},
        headers=SAME_ORIGIN,
    )

    assert response.status_code == 303
    assert managed.directory is not None
    assert any(f"{P}:superuser" in u.entitlements for u in managed.directory.users.values())


@pytest.mark.parametrize("who", [GROUP_ADMIN, AUDITOR])
async def test_roles_without_the_right_are_refused_everywhere(managed: Site, who: object) -> None:
    await managed.login_as(who)  # type: ignore[arg-type]

    for path in (
        "/admin/staff/invite",
        "/admin/staff/directory/enabled",
        "/admin/staff/directory/reset",
        "/admin/staff/directory/grants/add",
        "/admin/staff/directory/grants/remove",
    ):
        response = await managed.client.post(
            path, data={"subject": "kc-mine", **INVITE}, headers=SAME_ORIGIN
        )
        assert response.status_code == 403, path
    assert (await managed.client.get("/admin/staff/invite")).status_code == 403


async def test_disable_and_reset_work_on_a_person_in_scope(managed: Site) -> None:
    await managed.login_as(ORG_ADMIN)

    off = await managed.client.post(
        "/admin/staff/directory/enabled",
        data={"subject": "kc-mine", "enabled": "no", "back": "/admin/staff"},
        headers=SAME_ORIGIN,
    )
    reset = await managed.client.post(
        "/admin/staff/directory/reset", data={"subject": "kc-mine"}, headers=SAME_ORIGIN
    )

    assert (off.status_code, reset.status_code) == (303, 303)
    assert managed.directory is not None
    assert not managed.directory.users["kc-mine"].enabled
    assert managed.directory.mails[0][0] == "kc-mine"


async def test_nobody_is_addressed_outside_their_scope(managed: Site) -> None:
    assert managed.directory is not None
    managed.directory.users["kc-far"] = DirectoryUser(
        "kc-far", "f", "f@example.test", "F", "G", True, frozenset({f"{P}:org_admin:andere"})
    )
    await managed.login_as(ORG_ADMIN)

    response = await managed.client.post(
        "/admin/staff/directory/enabled",
        data={"subject": "kc-far", "enabled": "no"},
        headers=SAME_ORIGIN,
    )

    assert response.status_code == 200
    assert managed.directory.users["kc-far"].enabled
    assert "f@example.test" not in response.text


async def test_a_cross_site_post_is_refused(managed: Site) -> None:
    await managed.login_as(ORG_ADMIN)

    response = await managed.client.post(
        "/admin/staff/directory/enabled",
        data={"subject": "kc-mine", "enabled": "no"},
        headers={"sec-fetch-site": "cross-site"},
    )

    assert response.status_code == 403
    assert managed.directory is not None
    assert managed.directory.users["kc-mine"].enabled
