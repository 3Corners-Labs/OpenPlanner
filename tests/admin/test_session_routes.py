"""Viewing and ending admin sessions (ADR 0098 point 9, step 3)."""

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

END_OTHERS = "/admin/rights/sessions/end-others"


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


async def _audit(site: Site) -> list[tuple[str, str, str, str | None, str]]:
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with SqlUnitOfWork(app.state.engine).read() as tx:
        events = await tx.list_audit_events(Scope(everything=True), limit=200)
    return [
        (e.event.actor, e.event.action, e.event.outcome.value, e.event.organisation, e.event.detail)
        for e in events
    ]


async def _id_of(site: Site, name: str) -> str:
    text = (await site.client.get("/admin/staff")).text
    return text.split(f'">{name}</a>')[0].rsplit('href="/admin/staff/', 1)[1]


async def test_my_sessions_lists_the_own_sessions_and_marks_the_current_one(site: Site) -> None:
    first = await _device(site, ORG_ADMIN)
    second = await _device(site, ORG_ADMIN)

    text = (await site.client.get("/admin/rights/sessions")).text

    assert '<h1 id="page-title">Mijn sessies</h1>' in text
    assert text.count("Deze sessie") == 1
    assert text.count("<td>Andere sessie</td>") == 1
    assert "Laatst actief" in text
    for secret in (first, second, "sid"):
        assert secret not in text.replace('id="', "")
    assert 'aria-current="page"' in text  # the sub-tab row


async def test_ending_the_other_sessions_keeps_the_current_one(site: Site) -> None:
    other = await _device(site, ORG_ADMIN)
    current = await _device(site, ORG_ADMIN)

    response = await site.client.post(END_OTHERS, headers=SAME_ORIGIN)

    assert response.status_code == 303
    assert "done=sessions_ended_others&count=1" in response.headers["location"]
    text = (await site.client.get(response.headers["location"])).text
    assert "Aantal: 1." in text
    assert "U heeft geen andere sessies." in text
    assert await _opens(site, current) == 200
    assert await _opens(site, other) == 303
    assert (ORG_ADMIN.subject, "admin.sessions_ended_by_self", "success", None, "sessions:1") in (
        await _audit(site)
    )


async def test_a_person_only_ends_their_own_sessions(site: Site) -> None:
    gina = await _device(site, GROUP_ADMIN)
    await _device(site, ORG_ADMIN)

    await site.client.post(END_OTHERS, headers=SAME_ORIGIN)

    assert await _opens(site, gina) == 200


async def test_an_administrator_ends_all_sessions_of_a_person(site: Site) -> None:
    phone = await _device(site, GROUP_ADMIN)
    laptop = await _device(site, GROUP_ADMIN)
    await _device(site, ORG_ADMIN)
    gina = await _id_of(site, "Gina Group")

    page = (await site.client.get(f"/admin/staff/{gina}")).text
    assert "Alle sessies beëindigen" in page

    response = await site.client.post(f"/admin/staff/{gina}/sessions/end", headers=SAME_ORIGIN)

    assert response.status_code == 303
    assert response.headers["location"].startswith(
        f"/admin/staff/{gina}?done=sessions_ended_person"
    )
    shown = (await site.client.get(response.headers["location"])).text
    assert "Aantal: 2." in shown
    assert await _opens(site, phone) == 303
    assert await _opens(site, laptop) == 303
    events = await _audit(site)
    assert (
        ORG_ADMIN.subject,
        "admin.sessions_ended_by_admin",
        "success",
        "demo",
        f"sessions:2,subject:{GROUP_ADMIN.subject}",
    ) in events


async def test_ending_the_sessions_of_someone_outside_the_scope_is_refused(site: Site) -> None:
    """Abuse case: an organisation admin ends a session of a person of another organisation."""
    otto = await _device(site, OUTSIDER)
    await _device(site, SUPER)
    otto_id = await _id_of(site, "Otto Other")
    await _device(site, ORG_ADMIN)

    response = await site.client.post(f"/admin/staff/{otto_id}/sessions/end", headers=SAME_ORIGIN)

    assert response.status_code == 403
    assert await _opens(site, otto) == 200
    assert (
        ORG_ADMIN.subject,
        "staff.sessions.end",
        "denied",
        None,
        "",
    ) in await _audit(site)


async def test_a_superuser_cannot_be_ended_by_an_organisation_admin(site: Site) -> None:
    boss = await _device(site, SUPER)
    boss_id = await _id_of(site, "Sanne Super")
    await _device(site, ORG_ADMIN)

    response = await site.client.post(f"/admin/staff/{boss_id}/sessions/end", headers=SAME_ORIGIN)

    assert response.status_code == 403
    assert await _opens(site, boss) == 200


async def test_the_button_is_not_offered_without_the_permission(site: Site) -> None:
    await _device(site, GROUP_ADMIN)
    await _device(site, AUDITOR)
    gina = await _id_of(site, "Gina Group")

    page = (await site.client.get(f"/admin/staff/{gina}")).text

    assert "Alle sessies beëindigen" not in page
    denied = await site.client.post(f"/admin/staff/{gina}/sessions/end", headers=SAME_ORIGIN)
    assert denied.status_code == 403


async def test_ending_ones_own_sessions_as_an_administrator_logs_out(site: Site) -> None:
    other = await _device(site, ORG_ADMIN)
    current = await _device(site, ORG_ADMIN)
    olaf = await _id_of(site, "Olaf Org")

    response = await site.client.post(f"/admin/staff/{olaf}/sessions/end", headers=SAME_ORIGIN)

    assert response.headers["location"] == "/admin/logged-out"
    assert await _opens(site, current) == 303
    assert await _opens(site, other) == 303


async def test_an_unknown_person_is_not_found_for_the_superuser(site: Site) -> None:
    await _device(site, SUPER)

    response = await site.client.post("/admin/staff/999999/sessions/end", headers=SAME_ORIGIN)

    assert response.status_code == 404


async def test_ending_a_person_needs_a_recent_login(site: Site) -> None:
    await _device(site, GROUP_ADMIN)
    await _device(site, ORG_ADMIN)
    gina = await _id_of(site, "Gina Group")
    site.clock.advance(timedelta(minutes=30))
    await site.client.get("/admin/rights")
    site.clock.advance(timedelta(minutes=45))

    response = await site.client.post(f"/admin/staff/{gina}/sessions/end", headers=SAME_ORIGIN)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/login?next=")


async def test_an_expired_session_is_not_listed(site: Site) -> None:
    await _device(site, ORG_ADMIN)
    site.clock.advance(timedelta(minutes=50))
    await _device(site, ORG_ADMIN)
    site.clock.advance(timedelta(minutes=50))

    text = (await site.client.get("/admin/rights/sessions")).text

    assert text.count("Deze sessie") == 1
    assert "<td>Andere sessie</td>" not in text
