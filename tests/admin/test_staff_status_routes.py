"""Suspend, reinstate and erase a person from the staff overview (ADR 0046 step 2)."""

from datetime import timedelta

from app.adapters.db.uow import SqlUnitOfWork
from app.domain.authorization import Scope
from tests.admin.conftest import (
    AUDITOR,
    GROUP_ADMIN,
    ORG_ADMIN,
    OUTSIDER,
    SAME_ORIGIN,
    SUPER,
    Site,
)


def _cookie(site: Site) -> str:
    return next(c.value for c in site.client.cookies.jar if c.name == "op_admin" and c.value)


async def _opens(site: Site, value: str) -> int:
    """Open ``/admin/rights`` sending exactly ``value`` as the admin cookie."""
    response = await site.client.get(
        "/admin/rights", headers={"cookie": f"op_admin={value}"}, follow_redirects=False
    )
    return response.status_code


async def _device(site: Site, principal: object) -> str:
    """Log in on a fresh 'device' (an empty cookie jar) and return its cookie."""
    site.client.cookies.clear()
    await site.login_as(principal)  # type: ignore[arg-type]
    site.clock.advance(timedelta(minutes=1))
    return _cookie(site)


async def _audit(site: Site) -> list[tuple[str, str, str, str, str | None, str]]:
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with SqlUnitOfWork(app.state.engine).read() as tx:
        events = await tx.list_audit_events(Scope(everything=True), limit=300)
    return [
        (
            e.event.actor,
            e.event.action,
            e.event.outcome.value,
            e.event.reason,
            e.event.organisation,
            e.event.detail,
        )
        for e in events
    ]


async def _id_of(site: Site, name: str) -> str:
    text = (await site.client.get("/admin/staff")).text
    return text.split(f'">{name}</a>')[0].rsplit('href="/admin/staff/', 1)[1]


async def test_suspending_ends_the_sessions_and_refuses_the_next_login(site: Site) -> None:
    phone = await _device(site, GROUP_ADMIN)
    laptop = await _device(site, GROUP_ADMIN)
    await _device(site, ORG_ADMIN)
    gina = await _id_of(site, "Gina Group")

    page = (await site.client.get(f"/admin/staff/{gina}")).text
    assert "Medewerker schorsen" in page

    response = await site.client.post(f"/admin/staff/{gina}/suspend", headers=SAME_ORIGIN)

    assert response.status_code == 303
    assert response.headers["location"] == f"/admin/staff/{gina}?done=staff_suspended"
    shown = (await site.client.get(response.headers["location"])).text
    assert "Geschorst" in shown
    assert "Schorsing opheffen" in shown
    # An old cookie is dead: abuse case "a suspended subject keeps working with an old cookie".
    assert await _opens(site, phone) == 303
    assert await _opens(site, laptop) == 303
    site.client.cookies.clear()
    refused = await site.login_as(GROUP_ADMIN)
    assert refused.status_code == 403
    assert "geschorst" in refused.text
    events = await _audit(site)
    assert (
        ORG_ADMIN.subject,
        "staff.suspended",
        "success",
        "",
        "demo",
        f"sessions:2,subject:{GROUP_ADMIN.subject}",
    ) in events
    assert any(
        e[:2] == (GROUP_ADMIN.subject, "admin.login") and e[3] == "suspended" for e in events
    )


async def test_reinstating_lets_the_person_log_in_again(site: Site) -> None:
    await _device(site, GROUP_ADMIN)
    await _device(site, ORG_ADMIN)
    gina = await _id_of(site, "Gina Group")
    await site.client.post(f"/admin/staff/{gina}/suspend", headers=SAME_ORIGIN)

    response = await site.client.post(f"/admin/staff/{gina}/reinstate", headers=SAME_ORIGIN)

    assert response.headers["location"] == f"/admin/staff/{gina}?done=staff_reinstated"
    site.client.cookies.clear()
    assert (await site.login_as(GROUP_ADMIN)).status_code == 303
    assert any(e[1] == "staff.reinstated" for e in await _audit(site))


async def test_nobody_suspends_themselves(site: Site) -> None:
    await _device(site, ORG_ADMIN)
    olaf = await _id_of(site, "Olaf Org")

    response = await site.client.post(f"/admin/staff/{olaf}/suspend", headers=SAME_ORIGIN)

    assert response.status_code == 403
    assert (await site.client.get("/admin/rights")).status_code == 200


async def test_an_organisation_admin_cannot_suspend_a_superuser_or_another_organisation(
    site: Site,
) -> None:
    """Abuse case: suspend a superuser, or someone of another organisation, to lock them out."""
    boss = await _device(site, SUPER)
    otto = await _device(site, OUTSIDER)
    await _device(site, SUPER)
    boss_id = await _id_of(site, "Sanne Super")
    otto_id = await _id_of(site, "Otto Other")
    await _device(site, ORG_ADMIN)

    for target in (boss_id, otto_id):
        response = await site.client.post(f"/admin/staff/{target}/suspend", headers=SAME_ORIGIN)
        assert response.status_code == 403

    assert await _opens(site, boss) == 200
    assert await _opens(site, otto) == 200
    assert not any(e[1] == "staff.suspended" for e in await _audit(site))


async def test_buttons_are_only_offered_to_those_who_may_use_them(site: Site) -> None:
    await _device(site, GROUP_ADMIN)
    await _device(site, AUDITOR)
    gina = await _id_of(site, "Gina Group")

    page = (await site.client.get(f"/admin/staff/{gina}")).text

    assert "Medewerker schorsen" not in page
    for action in ("suspend", "reinstate", "erase"):
        denied = await site.client.post(f"/admin/staff/{gina}/{action}", headers=SAME_ORIGIN)
        assert denied.status_code == 403


async def test_the_organisation_admin_cannot_erase_only_the_superuser_can(site: Site) -> None:
    await _device(site, GROUP_ADMIN)
    await _device(site, ORG_ADMIN)
    gina = await _id_of(site, "Gina Group")
    await site.client.post(f"/admin/staff/{gina}/suspend", headers=SAME_ORIGIN)

    page = (await site.client.get(f"/admin/staff/{gina}")).text
    assert "Gegevens wissen" not in page
    denied = await site.client.post(f"/admin/staff/{gina}/erase", headers=SAME_ORIGIN)
    assert denied.status_code == 403


async def test_the_superuser_erases_a_suspended_person(site: Site) -> None:
    await _device(site, GROUP_ADMIN)
    await _device(site, SUPER)
    gina = await _id_of(site, "Gina Group")

    early = await site.client.post(f"/admin/staff/{gina}/erase", headers=SAME_ORIGIN)
    assert early.status_code == 409  # a person is suspended first

    await site.client.post(f"/admin/staff/{gina}/suspend", headers=SAME_ORIGIN)
    page = (await site.client.get(f"/admin/staff/{gina}")).text
    assert "Gegevens wissen" in page

    response = await site.client.post(f"/admin/staff/{gina}/erase", headers=SAME_ORIGIN)

    assert response.headers["location"] == f"/admin/staff/{gina}?done=staff_erased"
    shown = (await site.client.get(response.headers["location"])).text
    assert "Gewiste medewerker" in shown
    assert "Gina Group" not in shown
    listing = (await site.client.get("/admin/staff")).text
    assert "Gina Group" not in listing
    assert "Gewiste medewerker" in listing
    events = await _audit(site)
    assert (
        SUPER.subject,
        "staff.erased",
        "success",
        "manual",
        "demo",
        f"sessions:0,subject:{GROUP_ADMIN.subject}",
    ) in events
    # The audit log now names an erased person instead of the display name.
    log = (await site.client.get("/admin/audit")).text
    assert "Gina Group" not in log


async def test_an_unknown_person_is_not_found_for_the_superuser(site: Site) -> None:
    await _device(site, SUPER)

    for action in ("suspend", "reinstate", "erase"):
        response = await site.client.post(f"/admin/staff/999999/{action}", headers=SAME_ORIGIN)
        assert response.status_code == 404


async def test_changes_need_a_recent_login(site: Site) -> None:
    await _device(site, GROUP_ADMIN)
    await _device(site, ORG_ADMIN)
    gina = await _id_of(site, "Gina Group")
    site.clock.advance(timedelta(minutes=30))
    await site.client.get("/admin/rights")
    site.clock.advance(timedelta(minutes=45))

    response = await site.client.post(f"/admin/staff/{gina}/suspend", headers=SAME_ORIGIN)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/login?next=")
