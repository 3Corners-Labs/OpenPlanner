from datetime import timedelta

import pytest

from app.adapters.db.uow import SqlUnitOfWork
from app.domain.audit import Outcome
from app.domain.authorization import Scope
from tests.admin.conftest import (
    GROUP_ADMIN,
    NO_ROLE,
    ORG_ADMIN,
    OUTSIDER,
    SAME_ORIGIN,
    SUPER,
    Site,
)


async def audit_events(site: Site) -> list[tuple[str, str, Outcome, str]]:
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    uow = SqlUnitOfWork(app.state.engine)
    async with uow.read() as tx:
        events = await tx.list_audit_events(Scope(everything=True), limit=50)
    return [
        (e.event.actor, e.event.action, e.event.outcome, e.event.reason) for e in reversed(events)
    ]


async def test_login_redirects_to_the_identity_provider(site: Site) -> None:
    response = await site.client.get("/admin/login", params={"next": "/admin/rights"})

    assert response.status_code == 303
    assert response.headers["location"] == "https://idp.test/authorize?x=1"
    assert site.idp.redirect_uris == ["http://127.0.0.1:8000/admin/callback"]
    cookie = response.headers["set-cookie"].lower()
    assert "op_admin=" in cookie
    assert "path=/admin" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie


async def test_successful_login_starts_a_fresh_session_and_is_audited(site: Site) -> None:
    await site.client.get("/admin/login", params={"next": "/admin/rights"})
    before = site.client.cookies.get("op_admin")
    site.idp.next_principal = GROUP_ADMIN

    response = await site.client.get("/admin/callback", params={"code": "c", "state": "state-1"})

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/rights"
    assert site.client.cookies.get("op_admin") != before  # no session fixation
    assert ("sub-group", "admin.login", Outcome.SUCCESS, "group_admin") in await audit_events(site)


@pytest.mark.parametrize(
    "params", [{"code": "c", "state": "forged"}, {"code": "c"}, {"state": "state-1"}]
)
async def test_bad_state_or_missing_code_fails(site: Site, params: dict[str, str]) -> None:
    await site.client.get("/admin/login")

    response = await site.client.get("/admin/callback", params=params)

    assert response.status_code == 400
    assert "Inloggen mislukt" in response.text
    assert ("anonymous", "admin.login", Outcome.FAILURE, "invalid_state") in await audit_events(
        site
    )


async def test_callback_without_login_in_progress_fails(site: Site) -> None:
    response = await site.client.get("/admin/callback", params={"code": "c", "state": "state-1"})

    assert response.status_code == 400


async def test_a_login_can_be_completed_only_once(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    replay = await site.client.get("/admin/callback", params={"code": "c", "state": "state-1"})

    assert replay.status_code == 400


async def test_identity_errors_are_audited(site: Site) -> None:
    await site.client.get("/admin/login")
    site.idp.fail = "invalid_token"

    response = await site.client.get("/admin/callback", params={"code": "c", "state": "state-1"})

    assert response.status_code == 401
    assert ("anonymous", "admin.login", Outcome.FAILURE, "invalid_token") in await audit_events(
        site
    )


@pytest.mark.parametrize(
    "next_url",
    ["https://evil.example/", "//evil.example/admin", "/g/demo", "/admin\\@evil", "javascript:x"],
)
async def test_next_cannot_leave_the_admin_portal(site: Site, next_url: str) -> None:
    await site.client.get("/admin/login", params={"next": next_url})
    site.idp.next_principal = GROUP_ADMIN

    response = await site.client.get("/admin/callback", params={"code": "c", "state": "state-1"})

    assert response.headers["location"] == "/admin/"


async def test_session_has_an_absolute_lifetime(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    assert (await site.client.get("/admin/")).status_code == 200

    site.clock.advance(timedelta(hours=8, minutes=1))

    assert (await site.client.get("/admin/")).status_code == 303


async def test_tampered_cookie_means_logged_out(site: Site) -> None:
    await site.login_as(SUPER)
    site.client.cookies.set(
        "op_admin", "tampered" + (site.client.cookies.get("op_admin") or ""), path="/admin"
    )

    assert (await site.client.get("/admin/")).status_code == 303


async def test_logout(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    refused = await site.client.post("/admin/logout", headers={"sec-fetch-site": "cross-site"})
    response = await site.client.post("/admin/logout", headers=SAME_ORIGIN)

    assert refused.status_code == 403
    # Also ends the session at the provider, so single sign-on cannot log the admin back in.
    # A page navigates on, because CSP form-action 'self' blocks a cross-origin POST redirect.
    provider_logout = (
        "https://idp.test/logout?post_logout_redirect_uri=http://127.0.0.1:8000/admin/logged-out"
    )
    assert response.status_code == 200
    assert f'<meta http-equiv="refresh" content="0;url={provider_logout}">' in response.text
    assert f'href="{provider_logout}"' in response.text
    assert "form-action 'self'" in response.headers["content-security-policy"]
    assert (await site.client.get("/admin/logged-out")).status_code == 200
    assert (await site.client.get("/admin/")).status_code == 303
    assert ("sub-org", "admin.logout", Outcome.SUCCESS, "org_admin") in await audit_events(site)


async def test_audit_log_shows_names_and_logins_within_the_organisation(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    await site.login_as(OUTSIDER)
    await site.login_as(ORG_ADMIN)

    text = (await site.client.get("/admin/audit")).text

    assert "<td>Gina Group</td>" in text  # name, not the subject identifier
    assert "sub-group" not in text
    assert "Otto Other" not in text  # logged in to another organisation


async def test_audit_log_names_failed_logins_as_not_signed_in(site: Site) -> None:
    await site.client.get("/admin/callback", params={"code": "c", "state": "forged"})
    await site.login_as(SUPER)

    assert "<td>Niet ingelogd</td>" in (await site.client.get("/admin/audit")).text


async def test_denials_are_audited(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    await site.client.get("/admin/groups/andere-team")
    await site.client.get("/admin/organisations")

    events = await audit_events(site)
    assert ("sub-group", "group.view", Outcome.DENIED, "out_of_scope") in events
    assert ("sub-group", "organisation.manage", Outcome.DENIED, "not_permitted") in events


async def test_dashboard_lists_only_groups_in_scope(site: Site) -> None:
    expected = {
        SUPER: {"demo", "andere-team"},
        ORG_ADMIN: {"demo"},
        GROUP_ADMIN: {"demo"},
        OUTSIDER: {"andere-team"},
    }
    for principal, groups in expected.items():
        await site.login_as(principal)
        text = (await site.client.get("/admin/")).text
        assert {g for g in ("demo", "andere-team") if f'href="/admin/groups/{g}"' in text} == groups


async def test_navigation_follows_the_matrix(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    group_admin_page = (await site.client.get("/admin/")).text
    await site.login_as(ORG_ADMIN)
    org_admin_page = (await site.client.get("/admin/")).text

    assert 'href="/admin/audit"' in group_admin_page  # ADR 0029: the own groups' log
    assert 'href="/admin/audit"' in org_admin_page
    assert 'href="/admin/integrations"' not in group_admin_page
    assert 'href="/admin/integrations"' not in org_admin_page
    assert 'href="/admin/rights"' in group_admin_page
    assert 'aria-current="page"' in group_admin_page
    assert "Ingelogd als Gina Group" in group_admin_page


async def test_rights_page_shows_scope_per_permission(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    text = (await site.client.get("/admin/rights")).text

    assert "Groepsbeheerder" in text
    assert "Openingstijden en uitzonderingen beheren" in text
    assert "Groepen: demo" in text
    assert "Groepsinstellingen wijzigen" not in text


async def test_group_page_lists_the_admins_rights_for_that_group(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    text = (await site.client.get("/admin/groups/demo")).text

    assert "Demo: videogesprek" in text
    assert "5 minuten voor de start" in text
    assert "Openingstijden en uitzonderingen beheren" in text
    assert "Groepsinstellingen wijzigen" not in text


async def test_audit_log_is_scoped_and_translated(site: Site) -> None:
    await site.login_as(OUTSIDER)
    await site.client.get("/admin/groups/demo")  # denied: about organisation demo
    await site.login_as(ORG_ADMIN)

    text = (await site.client.get("/admin/audit")).text

    assert "Geweigerd" in text
    assert "demo / demo" in text
    assert "andere" not in text.split("<tbody>")[1] if "<tbody>" in text else True


async def test_matrix_page(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)
    text = (await site.client.get("/admin/matrix")).text

    assert "Koppelingen en geheimen beheren" in text
    assert "Deelnemers (zonder inloggen)" in text
    assert "Met de beheerlink, tot de taak naar de wachtrij gaat" in text


async def test_no_role_sees_a_forbidden_page(site: Site) -> None:
    await site.login_as(NO_ROLE)
    response = await site.client.get("/admin/")

    assert response.status_code == 403
    assert "Geen toegang" in response.text
    assert "geen rol in het beheerportaal" in response.text
    assert 'href="/admin/">' not in response.text  # the overview would only loop back here


async def test_forbidden_page_keeps_the_admin_menu_and_logout(site: Site) -> None:
    await site.login_as(GROUP_ADMIN)

    text = (await site.client.get("/admin/groups/andere-team")).text

    assert "<title>Geen toegang – Beheer</title>" in text
    assert 'href="/admin/rights"' in text
    assert 'action="/admin/logout"' in text
    assert 'href="/admin/">Terug naar het overzicht' in text


async def test_public_admin_pages_say_beheer(site: Site) -> None:
    text = (await site.client.get("/admin/logged-out")).text

    assert '<span class="admin-brand">' in text
    assert "<span>Beheer</span>" in text
    assert 'action="/admin/logout"' not in text


async def test_unknown_group_is_a_404(site: Site) -> None:
    await site.login_as(SUPER)

    assert (await site.client.get("/admin/groups/nope")).status_code == 404


async def test_admin_login_disabled_without_identity_provider(settings) -> None:  # type: ignore[no-untyped-def]
    from tests.conftest import app_client

    async with app_client(settings) as client:
        response = await client.get("/admin/login")

    assert response.status_code == 503
    assert "Beheer niet beschikbaar" in response.text
