"""The admin session lives on the server (ADR 0098): replay, rotation, idle, no claims."""

import base64
import json
from datetime import timedelta

import structlog

from app.adapters.db.uow import SqlUnitOfWork
from app.domain.authorization import Scope
from tests.admin.conftest import ORG_ADMIN, SAME_ORIGIN, Site


def _cookie_value(site: Site) -> str:
    jar = site.client.cookies.jar
    return next(c.value for c in jar if c.name == "op_admin" and c.value)


async def _rights(site: Site, value: str) -> int:
    """Open ``/admin/rights`` sending exactly ``value`` as the admin cookie (a copy)."""
    response = await site.client.get(
        "/admin/rights", headers={"cookie": f"op_admin={value}"}, follow_redirects=False
    )
    return response.status_code


def _decode(value: str) -> dict[str, object]:
    """Return the payload of a signed cookie without checking the signature."""
    payload = value.rsplit(".", 2)[0]
    data = json.loads(base64.b64decode(payload + "=" * (-len(payload) % 4)))
    assert isinstance(data, dict)
    return data


async def _audit(site: Site) -> list[tuple[str, str, str]]:
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    async with SqlUnitOfWork(app.state.engine).read() as tx:
        events = await tx.list_audit_events(Scope(everything=True), limit=100)
    return [(e.event.actor, e.event.action, e.event.reason) for e in events]


async def test_a_copied_cookie_is_dead_after_logout(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    copy = _cookie_value(site)
    assert await _rights(site, copy) == 200

    out = await site.client.post("/admin/logout", headers=SAME_ORIGIN)
    assert out.status_code in (200, 303)
    assert await _rights(site, copy) == 303


async def test_a_new_login_ends_the_previous_cookie(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    old = _cookie_value(site)
    await site.login_as(ORG_ADMIN)
    new = _cookie_value(site)

    assert new != old
    assert await _rights(site, old) == 303
    assert await _rights(site, new) == 200


async def test_idle_ends_the_session_and_is_audited(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    site.clock.advance(timedelta(minutes=61))

    response = await site.client.get("/admin/rights")

    assert response.status_code == 303
    assert (ORG_ADMIN.subject, "admin.session_ended", "idle") in await _audit(site)


async def test_activity_inside_the_idle_limit_keeps_the_session(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    for _ in range(4):
        site.clock.advance(timedelta(minutes=30))
        assert (await site.client.get("/admin/rights")).status_code == 200


async def test_the_absolute_lifetime_still_ends_the_session(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    for _ in range(9):
        site.clock.advance(timedelta(minutes=55))
        await site.client.get("/admin/rights")

    assert (await site.client.get("/admin/rights")).status_code == 303
    assert (ORG_ADMIN.subject, "admin.session_ended", "expired") in await _audit(site)


async def test_the_cookie_holds_a_reference_and_no_claims(site: Site) -> None:
    await site.login_as(ORG_ADMIN)

    data = _decode(_cookie_value(site))

    assert set(data) == {"sid"}
    text = json.dumps(data)
    for leaked in ("Olaf Org", ORG_ADMIN.subject, "org_admin", "demo"):
        assert leaked not in text


async def test_the_reference_is_never_logged_or_audited(site: Site) -> None:
    with structlog.testing.capture_logs() as events:
        await site.login_as(ORG_ADMIN)
        sid = str(_decode(_cookie_value(site))["sid"])
        await site.client.get("/admin/rights")
        await site.client.post("/admin/logout", headers=SAME_ORIGIN)

    assert sid not in json.dumps(events, default=str)
    assert sid not in json.dumps(await _audit(site))


async def test_more_sessions_than_the_limit_end_the_one_idle_longest(site: Site) -> None:
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    app.state.settings = app.state.settings.model_copy(update={"admin_max_sessions_per_person": 2})
    cookies = []
    for _ in range(3):
        site.client.cookies.clear()
        await site.login_as(ORG_ADMIN)
        cookies.append(_cookie_value(site))
        site.clock.advance(timedelta(minutes=1))

    results = []
    for value in cookies:
        results.append(await _rights(site, value))

    assert results == [303, 200, 200]
    assert (ORG_ADMIN.subject, "admin.session_ended", "limit") in await _audit(site)


async def test_an_unreadable_register_gives_no_access(site: Site) -> None:
    await site.login_as(ORG_ADMIN)
    app = site.client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001

    async def broken(*_: object) -> None:
        raise RuntimeError("database unavailable")

    app.state.admin_session_service.resolve = broken
    try:
        response = await site.client.get("/admin/rights")
    except RuntimeError:
        return
    assert response.status_code != 200
